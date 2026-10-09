"""Settings: read everything the pages show, and save changes with validation."""

from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import asdict
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from entune.api.common import bad, optional_text, shortcuts_json
from entune.app import shortcuts
from entune.app.entune import Entune
from entune.app.metrics import processing_summary
from entune.app.models import UnknownModel
from entune.app.operations import Busy
from entune.app.settings import DECISION_MODELS, JEV_PROVIDER, mask_key
from entune.learning import suggestion_model
from entune.learning.suggestion_model import chatgpt
from entune.processing import jev_client, laya


def routes(app: Entune) -> list[Route]:
    # The processing summary reads every transcription; it changes only with the store
    # (any write, or a data reset) or the chosen decision model, so it is kept until then.
    summaries: dict[tuple[str, str | None], dict[str, Any]] = {}

    def summary() -> dict[str, Any]:
        key = (app.store.history_version(), app.settings.decision_model())
        found = summaries.get(key)
        if found is None:  # requests run side by side: each keeps the summary it made
            found = asdict(processing_summary(app.store, key[1]))
            summaries.clear()
            summaries[key] = found
        return found

    def get_settings(_: Request) -> Response:
        return JSONResponse(
            {
                "providers": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "keyHint": s.key_hint,
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
                    "summary": summary(),
                },
                "decisionModel": {
                    "selected": app.settings.decision_model(),
                    "perplexityKey": None
                    if (key := app.settings.key("perplexity")) is None
                    else mask_key(key),
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
                | {JEV_PROVIDER, "perplexity"}
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
                raise ValueError("decisionModel must be jev, laya, openai or perplexity")
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
                keys_saved=keys.keys(),
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

    # Signing in with ChatGPT: start opens OpenAI's sign-in in the browser, which comes
    # back to /auth/callback here once the person has approved. The waiting sign-in is
    # kept with the settings, so a newer start replaces it and deleting all data forgets
    # it; each step counts as work on the data, so that deletion waits for one in progress.
    lock = threading.Lock()  # a sign-out waits for a callback, so neither undoes the other
    outcome: dict[str, str | None] = {"error": None}  # why the latest sign-in failed

    def start_sign_in(port: int) -> Response:
        try:
            with app.data.using_data("ChatGPT sign-in"):
                attempt, url = chatgpt.start(app.settings.chatgpt_client_id(), port)
                with lock:
                    app.settings.set_chatgpt_sign_in(attempt)
                    outcome["error"] = None
        except Busy as exc:
            return bad(str(exc), 409)
        # OpenAI's page opens where the person is signed in to ChatGPT: their own browser.
        return JSONResponse({"url": url, "opened": webbrowser.open(url)})

    def sign_in_status() -> Response:
        # Read under the callback's lock, so a sign-in being saved never reads as signed out.
        with lock:
            login = app.settings.chatgpt_login()
            attempt = app.settings.chatgpt_sign_in()
            if login is None and attempt is not None and attempt.expires_at < time.time():
                # The browser never came back: say so, so it can be started again.
                app.settings.set_chatgpt_sign_in(None)
                attempt = None
                outcome["error"] = "The sign-in was not finished in time. Start it again."
            error = outcome["error"]
        if login is not None:
            return JSONResponse({"state": "signed-in", "account": login.email or "signed in"})
        if error:
            return JSONResponse({"state": "failed", "error": error})
        return JSONResponse({"state": "waiting" if attempt is not None else "signed-out"})

    def finish_sign_in(query: dict[str, str]) -> Response:
        try:
            with app.data.using_data("ChatGPT sign-in"), lock:
                attempt = app.settings.chatgpt_sign_in()
                if attempt is None:
                    return _page("No sign-in is waiting", "Start it again from Entune's settings.")
                if query.get("state") != attempt.state:
                    # An older tab or a stray request: the sign-in waiting goes on.
                    return _page(
                        "Sign-in did not finish",
                        "This page is not from the sign-in Entune is waiting for. Finish that"
                        " one, or start again from Entune's settings.",
                    )
                try:
                    login = chatgpt.finish(attempt, query)
                except ValueError as exc:
                    app.settings.set_chatgpt_sign_in(None)
                    outcome["error"] = str(exc)
                    return _page("Sign-in did not finish", str(exc))
                try:  # the plan's own models; the suggested list stands in when this fails
                    catalog = chatgpt.models(login.access_token) or None
                except ValueError:
                    catalog = None
                # Saved together, so the page shows the account's models once signed in.
                app.settings.set_chatgpt_sign_in(None)
                app.settings.set_chatgpt_client_id(login.client_id)
                app.settings.set_chatgpt_models(catalog)
                app.settings.set_chatgpt_login(login)
        except Busy as exc:
            return _page("Sign-in did not finish", str(exc))
        return _page("Signed in to ChatGPT", "You can close this tab and go back to Entune.")

    def sign_out() -> Response:
        with lock:
            login = app.settings.chatgpt_login()
            app.settings.set_chatgpt_sign_in(None)
            app.settings.set_chatgpt_login(None)
            app.settings.set_chatgpt_models(None)
            outcome["error"] = None
        # Ended at OpenAI too when it answers; otherwise the person can disconnect Entune
        # in ChatGPT Settings, which the page says.
        revoked = login is None or chatgpt.revoke(login)
        return JSONResponse({"state": "signed-out", "revoked": revoked})

    async def chatgpt_sign_in(request: Request) -> Response:
        if request.method == "POST":
            return await run_in_threadpool(start_sign_in, request.url.port or 80)
        if request.method == "DELETE":
            return await run_in_threadpool(sign_out)
        return await run_in_threadpool(sign_in_status)

    async def chatgpt_callback(request: Request) -> Response:
        return await run_in_threadpool(finish_sign_in, dict(request.query_params))

    async def tracing(request: Request) -> Response:
        """Langfuse tracing: its keys (masked), host and state. PUT saves keys (blank keeps
        the saved one) and a host; DELETE removes them and turns tracing off."""
        if request.method == "PUT":
            try:
                body = await request.json()
                if not isinstance(body, dict) or set(body) - {"publicKey", "secretKey", "host"}:
                    raise ValueError("Send publicKey, secretKey and host")
                values = [optional_text(body.get(k), k) for k in ("publicKey", "secretKey", "host")]
                await run_in_threadpool(app.tracing.save, *values)
            except ValueError as exc:
                return bad(str(exc))
        elif request.method == "DELETE":
            await run_in_threadpool(app.tracing.clear)
        return JSONResponse(app.tracing.status())

    return [
        Route("/api/tracing", tracing, methods=["GET", "PUT", "DELETE"]),
        Route("/api/settings", get_settings, methods=["GET"]),
        Route("/api/settings", put_settings, methods=["PUT"]),
        Route("/api/chatgpt/sign-in", chatgpt_sign_in, methods=["GET", "POST", "DELETE"]),
        Route(chatgpt.CALLBACK_PATH, chatgpt_callback, methods=["GET"]),
    ]


def _page(title: str, body: str) -> Response:
    """What the browser shows when OpenAI's sign-in returns to Entune."""
    from html import escape

    html = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{escape(title)} · Entune</title>"
        "<style>body{font:15px -apple-system,system-ui,sans-serif;margin:15vh auto;max-width:28rem;"
        "padding:0 1rem;color:#262626;background:#fafafa}@media(prefers-color-scheme:dark){"
        "body{color:#ddd;background:#1e1e1e}}h1{font-size:20px}</style>"
        f"<h1>{escape(title)}</h1><p>{escape(body)}</p>"
    )
    return HTMLResponse(html)
