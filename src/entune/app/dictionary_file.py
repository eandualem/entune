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
from entune.dictionary.entries import EMPTY, Dictionary
from entune.storage.store import Store


class DictionaryChanged(Exception):
    """A dictionary write named a version that is no longer the one on disk."""


def _revision(raw: bytes) -> str:
    return str(hashlib.sha256(raw).hexdigest()[:16])


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
        """The editor's document and revision from one read of the file, so an edit made
        outside Entune between two reads can never pair old contents with a new revision."""
        with self.lock:
            raw = self._read()
            document = EMPTY if raw is None else dictionary_document.parse(raw.decode("utf-8"))
            return dictionary_document.dumps(document), _revision(raw or b"")

    def dictionary_version(self) -> str:
        """A hash of the file as it is on disk; a writer names the version it edited."""
        return _revision(self._read() or b"")

    def _read(self) -> bytes | None:
        try:
            return (self._store.data_dir / dictionary_document.FILENAME).read_bytes()
        except FileNotFoundError:
            return None

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

    def change(self, update: Callable[[Dictionary], Dictionary], version: str) -> str:
        """Apply one edit to the dictionary as it is on disk, if it is still `version`, and
        return the new version. Raises DictionaryChanged on a stale version, ValueError
        with the reason on an edit the dictionary refuses."""
        with self._operations.dictionary_edit(), self.lock:
            if version.strip('"') != self.dictionary_version():
                raise DictionaryChanged(
                    "The dictionary changed since that version was read; read it again and"
                    " redo the change on the current one."
                )
            dictionary_document.save(self._store.data_dir, update(self.dictionary()))
            changed = self.dictionary_version()
        self._changed()
        return changed

    def pin(self, model: str, text: str | None, version: str) -> None:
        """Pin one learned heard entry of `model`, or all of them when `text` is None."""
        with self._operations.dictionary_edit(), self.lock:
            updated = dictionary_changes.pin(self.dictionary(), model, text)
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
                # Logged under the same lock, so a data reset cannot land between the two.
                source = data.get("source")
                self._store.add_corrections(added, source if isinstance(source, str) else None)
        if added:
            self._changed()
        return added
