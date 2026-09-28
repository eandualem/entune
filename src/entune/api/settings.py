"""Settings: read everything the pages show, and save changes with validation."""

from __future__ import annotations

import threading
from dataclasses import asdict

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from entune.api.common import bad, optional_text, shortcuts_json
from entune.app import shortcuts
from entune.app.entune import Entune
from entune.app.metrics import processing_summary
from entune.app.models import UnknownModel
from entune.app.settings import DECISION_MODELS, JEV_PROVIDER
from entune.learning import suggestion_model
from entune.learning.suggestion_model import chatgpt
from entune.processing import jev_client, laya


def routes(app: Entune) -> list[Route]:
    def get_settings(_: Request) -> Response:
        return JSONResponse(
            {
                "providers": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "keyHint": s.key_hint,
                        "streams": s.streams,
                        "local": s.local,
                    }
                    for s in app.models.provider_statuses()
                ],
                "defaultModel": app.models.default_model(),
                "shortcuts": shortcuts_json(app),
                "llmProviders": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "keyHint": s.key_hint,
                        "defaultModel": s.default_model,
                        "models": [{"id": m.id, "name": m.name} for m in s.models],
                    }
                    for s in app.settings.suggestion_providers()
                ],
                "dictionaryModel": app.settings.dictionary_model(),
                "fastMode": app.settings.fast_mode(),
                "jev": {
                    **asdict(app.settings.jev_status()),
                    "policy": asdict(app.settings.jev_policy()),
                    "summary": asdict(processing_summary(app.store, app.settings.decision_model())),
                },
                "decisionModel": {
                    "selected": app.settings.decision_model(),
                    "laya": dict(
                        zip(("state", "error"), app.decisions.laya.status(), strict=True),
                        install=laya.INSTALL_COMMAND,
                    ),
                },
            }
        )

    async def put_settings(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError as exc:
            return bad(str(exc))
        return await run_in_threadpool(update_settings, body)

    def update_settings(body: object) -> Response:
        try:
            if not isinstance(body, dict):
                raise ValueError("Body must be a JSON object")
            keys = body.get("keys", {})
            if not isinstance(keys, dict):
                raise ValueError("keys must be an object")
            known = (
                {p.id for p in app.providers}
                | (suggestion_model.LLM_PROVIDERS.keys() - {suggestion_model.CHATGPT})
                | {JEV_PROVIDER}
            )
            for provider_id, key in keys.items():
                if provider_id not in known:
                    raise ValueError(f"Unknown provider: {provider_id}")
                if not isinstance(key, str) or not key.strip():
                    raise ValueError(f"Empty or invalid key for {provider_id}")
            default_model = optional_text(body.get("defaultModel"), "defaultModel")
            if default_model is not None and app.models.resolve(default_model) is None:
                raise UnknownModel(default_model)
            dictionary_model = optional_text(body.get("dictionaryModel"), "dictionaryModel")
            if dictionary_model:
                provider, separator, model = dictionary_model.partition(":")
                if (
                    not separator
                    or provider not in suggestion_model.LLM_PROVIDERS
                    or not model.strip()
                ):
                    raise ValueError(
                        "dictionaryModel must be provider:model for Anthropic, OpenAI,"
                        " ChatGPT subscription, Google Gemini, Groq or Mistral"
                    )
            if "fastMode" in body and not isinstance(body["fastMode"], bool):
                raise ValueError("fastMode must be a boolean")
            if "decisionModel" in body and body["decisionModel"] not in DECISION_MODELS:
                raise ValueError("decisionModel must be jev or laya")
            jev_settings = body.get("jev", {})
            if not isinstance(jev_settings, dict) or set(jev_settings) - {
                "dictionary",
                "formatting",
                "cleanup",
                "policy",
            }:
                raise ValueError(
                    "jev must contain dictionary, formatting, cleanup or policy settings"
                )
            for name in ("dictionary", "formatting", "cleanup"):
                if name in jev_settings and not isinstance(jev_settings[name], bool):
                    raise ValueError(f"jev.{name} must be a boolean")
            policy = None
            if "policy" in jev_settings:
                value = jev_settings["policy"]
                if not isinstance(value, dict) or set(value) != {
                    "total_seconds",
                    "attempt_seconds",
                    "max_attempts",
                }:
                    raise ValueError(
                        "jev.policy needs total_seconds, attempt_seconds and max_attempts"
                    )
                policy = jev_client.Policy(**value)
            # The same check the change itself makes, counting a key saved by this request,
            # so a refusal comes before any field is stored.
            processing = (
                body.get("decisionModel"),
                jev_settings.get("dictionary"),
                jev_settings.get("formatting"),
                jev_settings.get("cleanup"),
            )
            app.settings.check_processing(
                *processing,
                key_saved=JEV_PROVIDER in keys or app.settings.key(JEV_PROVIDER) is not None,
            )
            shortcut_settings = body.get("shortcuts", {})
            if not isinstance(shortcut_settings, dict):
                raise ValueError("shortcuts must be an object")
            hold = optional_text(shortcut_settings.get("hold"), "shortcuts.hold")
            toggle = optional_text(shortcut_settings.get("toggle"), "shortcuts.toggle")
            cancel = optional_text(
                shortcut_settings.get("cancel", shortcuts_json(app)["cancel"]), "shortcuts.cancel"
            )
            shortcuts.parse(hold, toggle, cancel)

            # Validate the complete request before storing any field: a bad shortcut
            # or model must not leave a seemingly failed Save with some keys changed.
            for provider_id, key in keys.items():
                app.settings.set_key(provider_id, key)
            if "defaultModel" in body:
                app.models.set_default_model(default_model)
            if "dictionaryModel" in body:
                app.settings.set_dictionary_model(dictionary_model or None)
            if "fastMode" in body:
                app.settings.set_fast_mode(body["fastMode"])

            if any(value is not None for value in processing):
                app.settings.set_processing(*processing)
            if policy is not None:
                app.settings.set_jev_policy(policy)
            if "shortcuts" in body:
                app.settings.set_shortcuts(hold, toggle, cancel)
            if "decisionModel" in body:
                app.decisions.sync(retry=True)  # choosing Laya again starts it after a failure
        except UnknownModel as exc:
            return bad(f"Unknown model: {exc}")
        except ValueError as exc:
            return bad(str(exc))
        return JSONResponse({"ok": True})

    # Signing in with ChatGPT: start gives the code to enter at OpenAI, the page then
    # checks until the person has approved it. One sign-in is pending at a time.
    pending: list[chatgpt.DeviceCode] = []
    lock = threading.Lock()

    def start_sign_in() -> Response:
        try:
            code = chatgpt.start()
        except ValueError as exc:
            return bad(str(exc))
        with lock:
            pending[:] = [code]
        return JSONResponse(
            {
                "userCode": code.user_code,
                "verificationUrl": chatgpt.VERIFICATION_URL,
                "interval": code.interval,
            }
        )

    def check_sign_in(user_code: object) -> Response:
        # A code is exchanged once, and a sign-out waits for a check in progress, so
        # neither undoes the other. The page names the code it shows: a newer start
        # replaced any other.
        with lock:
            if not pending or pending[0].user_code != user_code:
                return bad("This ChatGPT sign-in is no longer waiting; sign in again", 409)
            try:
                login = chatgpt.check(pending[0])
            except ValueError as exc:
                pending.clear()
                return bad(str(exc))
            if login is None:
                return JSONResponse({"state": "waiting"})
            pending.clear()
            app.settings.set_chatgpt_login(login)
        return JSONResponse({"state": "signed-in", "account": login.email})

    def sign_out() -> Response:
        with lock:
            pending.clear()
            app.settings.set_chatgpt_login(None)
        return JSONResponse({"state": "signed-out"})

    async def chatgpt_sign_in(request: Request) -> Response:
        action = {"POST": start_sign_in, "DELETE": sign_out}[request.method]
        return await run_in_threadpool(action)

    async def chatgpt_sign_in_check(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError as exc:
            return bad(str(exc))
        user_code = body.get("userCode") if isinstance(body, dict) else None
        return await run_in_threadpool(check_sign_in, user_code)

    return [
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings", put_settings, methods=["PUT"]),
        Route("/api/chatgpt/sign-in", chatgpt_sign_in, methods=["POST", "DELETE"]),
        Route("/api/chatgpt/sign-in/check", chatgpt_sign_in_check, methods=["POST"]),
    ]
