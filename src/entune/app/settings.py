"""Settings kept in the store: API keys, fast mode, Jev, the suggestion model, shortcuts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass

from entune.app import shortcuts
from entune.app.shortcuts import Shortcuts
from entune.learning import suggestion_model
from entune.processing.jev_client import Policy
from entune.providers.contracts import Provider
from entune.storage.store import Store

DEFAULT_MODEL_KEY = "default_model"
DICTIONARY_MODEL_KEY = "dictionary_model"
FAST_MODE_KEY = "fast_mode"
JEV_PROVIDER = "typesafe"  # the key is stored like a speech provider's
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

    def __init__(self, store: Store, providers: list[Provider], changed: Callable[[], None]):
        self._store, self._providers, self._changed = store, providers, changed

    def key(self, provider_id: str) -> str | None:
        return self._store.get_setting(key_setting(provider_id))

    def set_key(self, provider_id: str, key: str) -> None:
        known = (
            any(p.id == provider_id for p in self._providers)
            or provider_id in suggestion_model.LLM_PROVIDERS
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

    # Jev: a TypeSafe key and independently controlled, opt-in processing stages.

    def jev_status(self) -> JevStatus:
        key = self.key(JEV_PROVIDER)
        return JevStatus(
            None if key is None else mask_key(key),
            self._store.get_setting(JEV_DICTIONARY_KEY) == "1",
            self._store.get_setting(JEV_FORMATTING_KEY) == "1",
            self._store.get_setting(JEV_CLEANUP_KEY) == "1",
        )

    def set_jev(
        self,
        dictionary: bool | None = None,
        formatting: bool | None = None,
        cleanup: bool | None = None,
    ) -> None:
        """Turn a Jev use on or off; turning one on needs the key, so the setting never
        promises what a dictation cannot do."""
        if (dictionary or formatting or cleanup) and self.key(JEV_PROVIDER) is None:
            raise ValueError("Save a TypeSafe API key first.")
        if dictionary is not None:
            self._store.set_setting(JEV_DICTIONARY_KEY, "1" if dictionary else None)
        if formatting is not None:
            self._store.set_setting(JEV_FORMATTING_KEY, "1" if formatting else None)
        if cleanup is not None:
            self._store.set_setting(JEV_CLEANUP_KEY, "1" if cleanup else None)
        self._changed()

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
                None if (key := self.key(provider_id)) is None else mask_key(key),
                default_model,
                tuple(suggestion_model.catalog(provider_id)),
            )
            for provider_id, (name, default_model) in suggestion_model.LLM_PROVIDERS.items()
        ]

    def dictionary_model(self) -> str | None:
        """The saved `provider:model`, else the suggested model of the first language-model
        provider that has a key; None only when there is no key at all."""
        saved = self._store.get_setting(DICTIONARY_MODEL_KEY)
        if saved is not None:
            return saved
        for provider_id, (_, default_model) in suggestion_model.LLM_PROVIDERS.items():
            if self.key(provider_id) is not None:
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
        """The configured shortcuts; empty until the user sets one, cancel Fn+Control."""
        cancel = self._store.get_setting(SHORTCUT_CANCEL_KEY)
        return shortcuts.parse(
            self._store.get_setting(SHORTCUT_HOLD_KEY),
            self._store.get_setting(SHORTCUT_TOGGLE_KEY),
            "fn+ctrl" if cancel is None else cancel,
        )

    def set_shortcuts(
        self, hold: str | None, toggle: str | None, cancel: str | None = "fn+ctrl"
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
