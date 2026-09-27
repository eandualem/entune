"""Entune's local data as a whole: what is there, imports and exports, deleting it all."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from entune.app.dictionary_file import DictionaryFile
from entune.app.models import SpeechModels
from entune.app.operations import Busy, Operations
from entune.app.suggestion_runs import DictionaryBuilds
from entune.providers.contracts import Provider
from entune.providers.local.contracts import Downloadable
from entune.providers.resources import SpeechResources
from entune.storage.store import Store


class LocalData:
    def __init__(
        self,
        store: Store,
        providers: list[Provider],
        speech: SpeechResources,
        operations: Operations,
        builds: DictionaryBuilds,
        dictionary: DictionaryFile,
        models: SpeechModels,
        changed: Callable[[], None],
        stop_laya: Callable[[], None] = lambda: None,
    ) -> None:
        self._store, self._providers, self._speech = store, providers, speech
        self._operations, self._builds, self._dictionary = operations, builds, dictionary
        self._models, self._changed, self._stop_laya = models, changed, stop_laya
        self._lock = threading.Lock()
        self._using: list[str] = []  # imports and exports in progress; a reset waits for none
        self._resetting = False

    def data_inventory(self) -> dict[str, object]:
        """What a reset would delete, with sizes, and what it would leave in the folder."""

        def size(path: Path) -> int:
            if path.is_symlink() or not path.is_dir():
                return path.lstat().st_size
            return sum(
                (Path(root) / name).lstat().st_size
                for root, _, files in os.walk(path)  # symlinked folders are not followed
                for name in files
            )

        ours, others = self._store.managed()
        return {
            "folder": str(self._store.data_dir),
            "items": [{"name": p.name, "bytes": size(p)} for p in ours],
            "other": [p.name for p in others],
        }

    @contextmanager
    def using_data(self, what: str) -> Iterator[None]:
        """Mark an import or export in progress; none may start while data is deleted."""
        with self._lock:
            if self._resetting:
                raise Busy(f"Entune is deleting all data; try the {what} again afterwards.")
            self._using.append(what)
        try:
            yield
        finally:
            with self._lock:
                self._using.remove(what)

    def reset_data(self) -> dict[str, list[str]]:
        """Delete everything Entune keeps locally and continue with an empty folder.

        Refused while a dictation, suggestion run or model download is under way; a
        refusal changes nothing. New operations wait until it is done. Locks are taken
        in the order suggestion runs take them: builds, operations, then the dictionary.
        Local models are unloaded, not removed through their providers, so a models
        folder that is a link is unlinked by the store rather than followed."""
        with self._builds.idle(), self._operations.idle(), self._speech.use(None):
            if any(status.state == "downloading" for status in self._models.local_models()):
                raise ValueError("A model is still downloading; wait for it to finish first.")
            with self._lock:
                if self._using:
                    raise Busy(f"Wait for the {self._using[0]} to finish first.")
                self._resetting = True
            try:
                return self._reset_now()
            finally:
                with self._lock:
                    self._resetting = False

    def _reset_now(self) -> dict[str, list[str]]:
        """The deletion itself; the caller holds every lock and has checked for work."""
        self._builds.forget()
        self._speech.select(None)
        for provider in self._providers:
            if isinstance(provider, Downloadable):
                provider.unload()
        # Laya's server may still be writing its model into the models folder; stopping it
        # first means nothing writes there while it is deleted. Its setting goes too.
        self._stop_laya()
        try:
            with self._dictionary.lock:
                deleted, kept = self._store.reset()
        finally:
            self._changed()
        return {"deleted": deleted, "kept": kept}
