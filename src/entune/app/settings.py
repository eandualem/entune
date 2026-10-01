"""Settings kept in the store: API keys, fast mode, the decision model and its steps, the
suggestion model, shortcuts."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from entune.app import shortcuts
from entune.app.shortcuts import DEFAULT_CANCEL, Shortcuts
from entune.learning import suggestion_model
from entune.learning.suggestion_model import CHATGPT, chatgpt
from entune.processing.jev_client import Policy
from entune.processing.laya import INSTALL_COMMAND as LAYA_INSTALL
from entune.providers.contracts import Provider
from entune.storage.store import Store

DEFAULT_MODEL_KEY = "default_model"
DICTIONARY_MODEL_KEY = "dictionary_model"
CHATGPT_LOGIN_KEY = "chatgpt_login"
CHATGPT_SIGN_IN_KEY = "chatgpt_sign_in"  # the code waiting for approval, if any
FAST_MODE_KEY = "fast_mode"
JEV_PROVIDER = "typesafe"  # the key is stored like a speech provider's
DECISION_MODEL_KEY = "decision_model"
DECISION_MODELS = ("jev", "laya")
JEV_DICTIONARY_KEY = "jev_dictionary"
JEV_FORMATTING_KEY = "jev_formatting"
JEV_CLEANUP_KEY = "jev_cleanup"
JEV_POLICY_KEY = "jev_policy"
SHORTCUT_HOLD_KEY = "shortcut_hold"
SHORTCUT_TOGGLE_KEY = "shortcut_toggle"
SHORTCUT_CANCEL_KEY = "shortcut_cancel"


def key_setting(provider_id: str) -> str:
    return f"key:{provider_id}"


def mask_key(key: str) -> str:
    """A hint that a key is set, without revealing it."""
    return "••••" if len(key) <= 4 else f"••••{key[-4:]}"


@dataclass(frozen=True)
class JevStatus:
    key_hint: str | None
    dictionary: bool  # decide each dictionary match in context
    formatting: bool  # paragraph breaks and bullets
    cleanup: bool  # bounded repeated-filler reduction


@dataclass(frozen=True)
class SuggestionProvider:
    id: str
    name: str
    key_hint: str | None
    default_model: str
    models: tuple[suggestion_model.ModelChoice, ...]


class Settings:
    """Each setting is read from the store when asked for; `changed` runs after a write."""

    def __init__(
        self,
        store: Store,
        providers: list[Provider],
        changed: Callable[[], None],
        laya_installed: Callable[[], bool] = lambda: False,
    ):
        self._store, self._providers, self._changed = store, providers, changed
        self._laya_installed = laya_installed
        self._login_lock = threading.Lock()  # a renewal never undoes a sign-out

    def key(self, provider_id: str) -> str | None:
        return self._store.get_setting(key_setting(provider_id))

    def set_key(self, provider_id: str, key: str) -> None:
        known = (
            any(p.id == provider_id for p in self._providers)
            or (provider_id in suggestion_model.LLM_PROVIDERS and provider_id != CHATGPT)
            or provider_id == JEV_PROVIDER
        )
        if not known:
            raise ValueError(f"Unknown provider: {provider_id}")
        if not key.strip():
            raise ValueError(f"Empty key for {provider_id}")
        self._store.set_setting(key_setting(provider_id), key.strip())
        self._changed()

    def fast_mode(self) -> bool:
        """Stream the audio to the default model's provider while recording (opt-in)."""
        return self._store.get_setting(FAST_MODE_KEY) == "1"

    def set_fast_mode(self, on: bool) -> None:
        self._store.set_setting(FAST_MODE_KEY, "1" if on else None)
        self._changed()

    # The decision model and the independently controlled, opt-in steps that ask it.

    def decision_model(self) -> str | None:
        """The chosen decision model. Before the choice existed, saving a TypeSafe key chose
        Jev, the only one, so a saved key without a choice still means Jev."""
        saved = self._store.get_setting(DECISION_MODEL_KEY)
        if saved in DECISION_MODELS:
            return saved
        return "jev" if self.key(JEV_PROVIDER) is not None else None

    def check_processing(
        self,
        model: str | None = None,
        dictionary: bool | None = None,
        formatting: bool | None = None,
        cleanup: bool | None = None,
        key_saved: bool | None = None,
    ) -> None:
        """Refuse a change whose final state has a step on that the decision model cannot run,
        so the setting never promises what a dictation cannot do. Turning steps off is always
        allowed. `key_saved` counts a TypeSafe key saved by the same request."""
        if model is not None and model not in DECISION_MODELS:
            raise ValueError(f"Unknown decision model: {model}")
        status = self.jev_status()
        on = [
            status.dictionary if dictionary is None else dictionary,
            status.formatting if formatting is None else formatting,
            status.cleanup if cleanup is None else cleanup,
        ]
        if not any(on) or not (model is not None or dictionary or formatting or cleanup):
            return
        key = self.key(JEV_PROVIDER) is not None if key_saved is None else key_saved
        chosen = model or self.decision_model() or ("jev" if key else None)
        if chosen is None:
            raise ValueError("Choose a decision model first.")
        if chosen == "jev" and not key:
            raise ValueError("Save a TypeSafe API key first.")
        if chosen == "laya" and not self._laya_installed():
            raise ValueError(f"Install Laya's engine first: {LAYA_INSTALL}")

    def set_processing(
        self,
        model: str | None = None,
        dictionary: bool | None = None,
        formatting: bool | None = None,
        cleanup: bool | None = None,
    ) -> None:
        """Choose the decision model and turn steps on or off together, checked as one."""
        self.check_processing(model, dictionary, formatting, cleanup)
        if model is not None:
            self._store.set_setting(DECISION_MODEL_KEY, model)
        for name, value in (
            (JEV_DICTIONARY_KEY, dictionary),
            (JEV_FORMATTING_KEY, formatting),
            (JEV_CLEANUP_KEY, cleanup),
        ):
            if value is not None:
                self._store.set_setting(name, "1" if value else None)
        self._changed()

    def jev_status(self) -> JevStatus:
        key = self.key(JEV_PROVIDER)
        return JevStatus(
            None if key is None else mask_key(key),
            self._store.get_setting(JEV_DICTIONARY_KEY) == "1",
            self._store.get_setting(JEV_FORMATTING_KEY) == "1",
            self._store.get_setting(JEV_CLEANUP_KEY) == "1",
        )

    def jev_policy(self) -> Policy:
        saved = self._store.get_setting(JEV_POLICY_KEY)
        return Policy(**json.loads(saved)) if saved else Policy()

    def set_jev_policy(self, policy: Policy) -> None:
        self._store.set_setting(JEV_POLICY_KEY, json.dumps(asdict(policy)))
        self._changed()

    # The suggestion model: the language model that writes dictionary suggestions.

    def suggestion_providers(self) -> list[SuggestionProvider]:
        return [
            SuggestionProvider(
                provider_id,
                name,
                self._credential_hint(provider_id),
                default_model,
                tuple(suggestion_model.catalog(provider_id)),
            )
            for provider_id, (name, default_model) in suggestion_model.LLM_PROVIDERS.items()
        ]

    def _credential_hint(self, provider_id: str) -> str | None:
        """A masked key, or for a ChatGPT plan the signed-in account; None when neither."""
        if provider_id == CHATGPT:
            login = self.chatgpt_login()
            return None if login is None else login.email or "signed in"
        key = self.key(provider_id)
        return None if key is None else mask_key(key)

    def chatgpt_login(self) -> chatgpt.Login | None:
        saved = self._store.get_setting(CHATGPT_LOGIN_KEY)
        return None if saved is None else chatgpt.Login.from_json(saved)

    def set_chatgpt_login(self, login: chatgpt.Login | None) -> None:
        with self._login_lock:
            self._store.set_setting(CHATGPT_LOGIN_KEY, None if login is None else login.to_json())
        self._changed()

    def chatgpt_sign_in(self) -> chatgpt.DeviceCode | None:
        saved = self._store.get_setting(CHATGPT_SIGN_IN_KEY)
        return None if saved is None else chatgpt.DeviceCode(**json.loads(saved))

    def set_chatgpt_sign_in(self, code: chatgpt.DeviceCode | None) -> None:
        saved = None if code is None else json.dumps(asdict(code))
        self._store.set_setting(CHATGPT_SIGN_IN_KEY, saved)

    def chatgpt_access_token(self) -> str | None:
        """The signed-in plan's access token, renewed first when it runs out soon."""
        login = self.chatgpt_login()
        if login is None:
            return None
        fresh = chatgpt.renewed(login)
        if fresh is None:
            return login.access_token
        with self._login_lock:
            current = self.chatgpt_login()
            if current != login:  # signed out, or in again, while renewing: that stands
                return None if current is None else current.access_token
            # Kept at once: the refresh token just used no longer works.
            self._store.set_setting(CHATGPT_LOGIN_KEY, fresh.to_json())
        self._changed()
        return fresh.access_token

    def dictionary_model(self) -> str | None:
        """The saved `provider:model`, else the suggested model of the first language-model
        provider that has a key; None only when there is no key at all."""
        saved = self._store.get_setting(DICTIONARY_MODEL_KEY)
        if saved is not None:
            return saved
        for provider_id, (_, default_model) in suggestion_model.LLM_PROVIDERS.items():
            if self._credential_hint(provider_id) is not None:
                return default_model
        return None

    def set_dictionary_model(self, ref: str | None) -> None:
        """`provider:model` for a language-model provider we can route to, or None."""
        if ref is not None:
            provider, sep, model = ref.partition(":")
            if not sep or provider not in suggestion_model.LLM_PROVIDERS or not model.strip():
                known = ", ".join(suggestion_model.LLM_PROVIDERS)
                raise ValueError(f"The dictionary model must be provider:model with one of {known}")
        self._store.set_setting(DICTIONARY_MODEL_KEY, ref)
        self._changed()

    # Shortcuts

    def shortcuts(self) -> Shortcuts:
        """The configured shortcuts; empty until the user sets one, cancel the default."""
        cancel = self._store.get_setting(SHORTCUT_CANCEL_KEY)
        return shortcuts.parse(
            self._store.get_setting(SHORTCUT_HOLD_KEY),
            self._store.get_setting(SHORTCUT_TOGGLE_KEY),
            DEFAULT_CANCEL if cancel is None else cancel,
        )

    def set_shortcuts(
        self, hold: str | None, toggle: str | None, cancel: str | None = DEFAULT_CANCEL
    ) -> Shortcuts:
        """Validate and store shortcuts; blank clears one. ValueError says what is wrong."""
        parsed = shortcuts.parse(hold, toggle, cancel)
        self._store.set_setting(
            SHORTCUT_HOLD_KEY, shortcuts.format_keys(parsed.hold) if parsed.hold else None
        )
        self._store.set_setting(
            SHORTCUT_TOGGLE_KEY, shortcuts.format_keys(parsed.toggle) if parsed.toggle else None
        )
        # Missing means the original default; an empty value explicitly disables cancel.
        self._store.set_setting(
            SHORTCUT_CANCEL_KEY, shortcuts.format_keys(parsed.cancel) if parsed.cancel else ""
        )
        self._changed()
        return parsed
