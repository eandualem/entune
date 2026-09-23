"""The dictionary file: read it, save a new version of it, pin, add confirmed corrections.

It is read from disk each time, so a hand edit of the file counts too. A writer names
the version it edited; a save on a stale copy is refused rather than silently dropping
what was added meanwhile.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable

from entune.app.operations import Operations
from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary.corrections import Correction, add_corrections, read_entries
from entune.dictionary.entries import Dictionary
from entune.storage.store import Store


class DictionaryChanged(Exception):
    """A dictionary write named a version that is no longer the one on disk."""


class DictionaryFile:
    def __init__(self, store: Store, operations: Operations, changed: Callable[[], None]):
        self._store, self._operations, self._changed = store, operations, changed
        # Held around every read-modify-write; learning and the data reset take it too.
        self.lock = threading.RLock()

    def dictionary(self) -> Dictionary:
        with self.lock:
            return dictionary_document.load(self._store.data_dir)

    def dictionary_text(self) -> str:
        return dictionary_document.dumps(self.dictionary())

    def dictionary_snapshot(self) -> tuple[str, str]:
        """The editor's document and revision from the same locked read."""
        with self.lock:
            return self.dictionary_text(), self.dictionary_version()

    def dictionary_version(self) -> str:
        """A hash of the file as it is on disk; a writer names the version it edited."""
        path = self._store.data_dir / dictionary_document.FILENAME
        raw = path.read_bytes() if path.exists() else b""
        return str(hashlib.sha256(raw).hexdigest()[:16])

    def set_dictionary(self, text: str, expected_version: str | None = None) -> Dictionary:
        """Validate and save the JSON form. Raises ValueError with the reason, and
        DictionaryChanged when `expected_version` is given and the file moved on since."""
        parsed = dictionary_document.parse(text)
        with self._operations.dictionary_edit(), self.lock:
            if expected_version is not None and expected_version != self.dictionary_version():
                raise DictionaryChanged(
                    "The dictionary changed since it was loaded (an agent or a hand edit);"
                    " reload it and redo the change."
                )
            dictionary_document.save(self._store.data_dir, parsed)
        self._changed()
        return parsed

    def pin_meaning(self, model: str, group: str | None, meaning: str | None, version: str) -> None:
        with self._operations.dictionary_edit(), self.lock:
            current = self.dictionary()
            if group is None or meaning is None:
                updated = dictionary_changes.share(
                    current, {m.id for g in current.learned_for(model) for m in g.meanings}
                )
            else:
                updated = dictionary_changes.pin(current, model, group, meaning)
            self.set_dictionary(dictionary_document.dumps(updated), version)

    def add_agent_corrections(self, data: object) -> tuple[Correction, ...]:
        """Pin corrections an agent sent after confirming them with the user.

        `data` is the request body: `entries` (spelling, description, heard), or
        `terms` and `replacements`, plus an optional `source`. Returns what was actually
        new. Raises ValueError with the reason on bad input.
        """
        if not isinstance(data, dict):
            raise ValueError("Send a JSON object with entries")
        if "entries" in data:
            corrections = read_entries(data["entries"], "entries")
        else:
            body = {k: v for k, v in data.items() if k in ("terms", "replacements")}
            corrections = read_entries(body, "corrections")
        if not corrections:
            raise ValueError("Nothing to add: give entries with a spelling and heard phrases")
        with self._operations.dictionary_edit(), self.lock:
            current = self.dictionary()
            updated, added = add_corrections(current, corrections)
            if added:
                dictionary_document.save(self._store.data_dir, updated)
        if added:
            source = data.get("source")
            self._store.add_corrections(added, source if isinstance(source, str) else None)
            self._changed()
        return added
