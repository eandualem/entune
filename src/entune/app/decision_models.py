"""The decision model the processing steps ask: Jev at TypeSafe, Laya on this Mac, OpenAI or
Perplexity."""

from __future__ import annotations

from entune.app.settings import Settings
from entune.processing.jev_client import JEV, OPENAI, PERPLEXITY, Endpoint
from entune.processing.laya import Laya


class DecisionModels:
    def __init__(self, settings: Settings, laya: Laya) -> None:
        self._settings, self.laya = settings, laya

    def endpoint(self) -> Endpoint:
        """Where the steps ask now; an endpoint that cannot answer says why."""
        return self._endpoint(self._settings.decision_model())

    def chosen(self) -> tuple[Endpoint, str | None]:
        """Where the steps ask now and the key it takes, from one reading of the choice, so
        a switch meanwhile never sends one model's key to another."""
        model = self._settings.decision_model()
        return self._endpoint(model), self._settings.decision_key(model)

    def _endpoint(self, model: str | None) -> Endpoint:
        if model == "laya":
            return self.laya.endpoint()
        if model == "openai":
            return OPENAI
        if model == "perplexity":
            return PERPLEXITY
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
