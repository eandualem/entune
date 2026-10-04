"""The application, independent of how it is reached: its parts, built once and wired.

The HTTP API and the menu-bar app call the part they need (`settings`, `models`,
`dictionary`, `dictation`, `learning`, `capture`, `desktop`, `data`); this class only
creates them, shares one set of change listeners, and shuts everything down.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from entune.app.capture import ShortcutCapture
from entune.app.decision_models import DecisionModels
from entune.app.desktop_bridge import DesktopBridge
from entune.app.dictation import Dictation
from entune.app.dictionary_file import DictionaryFile
from entune.app.learning import Learning
from entune.app.local_data import LocalData
from entune.app.models import SpeechModels
from entune.app.operations import Operations
from entune.app.settings import Settings
from entune.app.suggestion_runs import DictionaryBuilds
from entune.app.tracing import Tracing
from entune.learning import suggestion_model
from entune.processing.jev_client import Client as JevClient
from entune.processing.laya import Laya
from entune.providers.contracts import Provider
from entune.providers.resources import SpeechResources
from entune.storage.store import Store


class Entune:
    def __init__(
        self,
        store: Store,
        providers: list[Provider],
        llm_call: suggestion_model.Caller = suggestion_model.call_model,
        *,
        jev_client: JevClient | None = None,
        laya: Laya | None = None,
    ) -> None:
        self.store = store
        self.providers = providers
        self._listeners: list[Callable[[], None]] = []
        self.operations = Operations()
        self.desktop = DesktopBridge(self.operations)
        self.jev = jev_client or JevClient()
        self.speech = SpeechResources(
            providers, lambda message: self.desktop.report_status(lastError=message)
        )
        self.tracing = Tracing(store)
        self._listeners.append(self.tracing.sync)  # Delete everything removes its keys
        self.builds = DictionaryBuilds(self.speech, llm_call, self.operations, self.tracing.run)
        laya = laya or Laya(store.data_dir / "models")
        self.settings = Settings(
            store, providers, self._changed, laya_installed=lambda: laya.engine() is not None
        )
        self.decisions = DecisionModels(self.settings, laya)
        self._listeners.append(self.decisions.sync)
        self.models = SpeechModels(store, providers, self.speech, self.settings, self._changed)
        self.dictionary = DictionaryFile(store, self.operations, self._changed)
        self.learning = Learning(
            store, self.builds, self.dictionary, self.settings, self.models, self._changed
        )
        self.dictation = Dictation(
            store,
            self.speech,
            self.operations,
            self.settings,
            self.models,
            self.dictionary,
            self.jev,
            self.decisions,
            lambda message: self.desktop.report_status(lastError=message),
        )
        self.capture = ShortcutCapture()
        self.data = LocalData(
            store,
            providers,
            self.speech,
            self.operations,
            self.builds,
            self.dictionary,
            self.models,
            self._changed,
            stop_laya=laya.stop,
        )

    def on_change(self, listener: Callable[[], None]) -> None:
        """Called after any setting changes; the menu-bar app uses it to reload its shortcut."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in self._listeners:
            listener()

    def close(self) -> bool:
        # Signal both owners before waiting; no new work can race shutdown. The decision client
        # owns a separate two-second close; builds/resources share two more seconds.
        self.builds.close(0)
        self.speech.close(0)
        self.tracing.close()  # pending traces get two seconds at most
        try:
            self.decisions.close()
        except Exception as exc:
            self._warn(f"Laya shutdown: {type(exc).__name__}: {exc}")
        jev_done = True
        try:
            self.jev.close()
        except Exception as exc:
            jev_done = False
            self._warn(f"Decision model cleanup: {type(exc).__name__}: {exc}")
        deadline = time.monotonic() + 2.0
        builds_done = self.builds.close(max(0.0, deadline - time.monotonic()))
        resources_done = self.speech.close(max(0.0, deadline - time.monotonic()))
        if not (builds_done and resources_done):
            self._warn(
                "Shutdown cleanup is incomplete; an operation is still draining "
                "or resource cleanup failed."
            )
        return jev_done and builds_done and resources_done

    def _warn(self, message: str) -> None:
        self.desktop.report_status(lastError=message)
        logging.getLogger(__name__).warning(message)
