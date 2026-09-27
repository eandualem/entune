"""The decision model the processing steps ask: Jev at TypeSafe, or Laya on this Mac."""

from __future__ import annotations

from entune.app.settings import Settings
from entune.processing.jev_client import JEV, Endpoint
from entune.processing.laya import Laya


class DecisionModels:
    def __init__(self, settings: Settings, laya: Laya) -> None:
        self._settings, self.laya = settings, laya

    def endpoint(self) -> Endpoint:
        """Where the steps ask now; an endpoint that cannot answer says why."""
        model = self._settings.decision_model()
        if model == "laya":
            return self.laya.endpoint()
        if model is None:
            return Endpoint("none", "", "", None, "No decision model is chosen in Settings.")
        return JEV

    def sync(self, retry: bool = False) -> None:
        """Laya runs while it is the chosen decision model and a step that asks it is on,
        and only then, so its memory is used only while it can be asked."""
        status = self._settings.jev_status()
        if self._settings.decision_model() == "laya" and (
            status.dictionary or status.formatting or status.cleanup
        ):
            self.laya.start(retry)
        else:
            self.laya.stop()

    def close(self) -> None:
        self.laya.stop()
