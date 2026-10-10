"""MCP tools for the rest of what a person does in Entune, so their agent does it for them:
see how Entune is set up, choose and download speech models, choose the decision model
and its steps, import audio, build the dictionary from it and apply the result, and read
how dictations came out. API keys, sign-ins and system permissions stay with the person:
no tool reads, sets or asks for a key.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from entune.app import audio_import
from entune.app.dictionary_file import DictionaryChanged
from entune.app.entune import Entune
from entune.app.models import UnknownModel
from entune.app.operations import Busy
from entune.app.settings import DECISION_MODELS
from entune.app.suggestion_runs import DEFAULT_EFFORT, EFFORTS, JobConflict
from entune.dictionary.changes import Proposal
from entune.learning import suggestion_model

READ = ToolAnnotations(read_only_hint=True)
EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False)
REMOVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}


def register(mcp: MCPServer, app: Entune) -> None:
    downloads: dict[str, threading.Thread] = {}
    failures: dict[str, str] = {}  # a download that failed before the model kept its state

    def failed(exc: Exception) -> ToolError:
        return ToolError(f"Unknown model: {exc}" if isinstance(exc, UnknownModel) else str(exc))

    def decision_options() -> list[dict[str, Any]]:
        options = []
        for model in DECISION_MODELS:
            try:
                app.settings.check_processing(model, dictionary=True)
                needs = None
            except ValueError as exc:
                needs = str(exc)
            option: dict[str, Any] = {"id": model, "ready": needs is None, "needs": needs}
            if model == "laya":
                # Installed is not running: a failed start shows here, not in the check.
                state, error = app.decisions.laya.status()
                option["state"] = state
                if state == "failed":
                    option.update(ready=False, needs=f"Laya did not start: {error}")
            options.append(option)
        return options

    @mcp.tool(annotations=READ)
    def entune_setup() -> dict[str, Any]:
        """Start here. How Entune is set up now: the speech models it can use (cloud ones
        whose key is saved, and local ones with their download state), the default speech
        model, the decision model and its steps, the suggestion model that builds the
        dictionary, the dictionary's size and any dictionary build. `needs_person` lists
        the few things only the person can do, such as saving an API key in Settings;
        everything else, do yourself with these tools."""
        dictionary = app.dictionary.dictionary()
        status = app.settings.jev_status()
        suggestion = app.settings.dictionary_model()
        speech = app.models.available_models()
        needs = []
        if not speech:
            needs.append(
                "A speech model: download a local one with download_speech_model, or the"
                " person saves a cloud provider's API key in Entune's Models page."
            )
        provider = suggestion.partition(":")[0] if suggestion else None
        credentials = {p.id: p.key_hint for p in app.settings.suggestion_providers()}
        if suggestion is None:
            needs.append(
                "A suggestion model to build the dictionary: the person saves an Anthropic,"
                " OpenAI, Google Gemini, Groq or Mistral API key in Settings, or signs in"
                " with ChatGPT there."
            )
        elif credentials.get(provider or "") is None:
            needs.append(
                f"The suggestion model {suggestion} has no key or sign-in: choose one whose"
                " provider is ready with set_preferences, or the person saves its key or"
                " signs in again in Settings."
            )
        if not any(o["ready"] for o in decision_options()):
            needs.append(
                "A decision model for the dictionary and formatting steps: the person saves"
                " a TypeSafe (Jev), OpenAI or Perplexity key in Settings; Laya runs on this"
                " Mac once its engine is installed (see decision_models)."
            )
        return {
            "default_speech_model": app.models.default_model(),
            "speech_models": [asdict(m) for m in speech],
            "local_models": [_local(asdict(m), failures) for m in app.models.local_models()],
            "speech_providers": [
                {"id": p.id, "name": p.name, "local": p.local, "key_saved": p.key_hint is not None}
                for p in app.models.provider_statuses()
            ],
            "decision_model": app.settings.decision_model(),
            "decision_models": decision_options(),
            "steps": {
                "dictionary": status.dictionary,
                "formatting": status.formatting,
                "cleanup": status.cleanup,
            },
            "suggestion_model": suggestion,
            "suggestion_providers": [
                {
                    "id": p.id,
                    "name": p.name,
                    "ready": p.key_hint is not None,
                    "suggested_model": p.default_model,
                }
                for p in app.settings.suggestion_providers()
            ],
            "fast_mode": app.settings.fast_mode(),
            "remove_silence": app.settings.remove_silence(),
            "dictionary": {
                "words": len(dictionary.words),
                "pinned": len(dictionary.pinned),
                "learned": {m: len(es) for m, es in dictionary.learned.items()},
            },
            "dictionary_build": _summary(app.learning.dictionary_build_status()),
            "needs_person": needs,
        }

    @mcp.tool(annotations=EDIT)
    def download_speech_model(name: str) -> dict[str, Any]:
        """Download a local speech model (a `name` from `local_models` in entune_setup). It
        runs in the background: check entune_setup until its state is "ready", then make
        it the default with set_speech_model. Local models are free, private and need no
        key; Parakeet, the most accurate here, runs on Apple Silicon and its first download
        also installs its engine."""
        status = next((m for m in app.models.local_models() if m.name == name), None)
        if status is None:
            raise ToolError(f"Unknown local model: {name}; see local_models in entune_setup")
        if status.state == "ready":
            return {"name": name, "state": "ready"}
        running = downloads.get(name)
        if running is None or not running.is_alive():

            def download() -> None:
                failures.pop(name, None)
                try:
                    app.models.download_local_model(name)
                except Exception as exc:  # also when it fails before the model keeps a state
                    failures[name] = f"{type(exc).__name__}: {exc}"
                    print(f"MCP download of {name} failed: {exc}", flush=True)

            running = threading.Thread(
                target=download, daemon=True, name=f"entune-mcp-download-{name}"
            )
            downloads[name] = running
            running.start()
        return {"name": name, "state": "downloading"}

    @mcp.tool(annotations=EDIT)
    def set_speech_model(model: str) -> dict[str, Any]:
        """Make `model` (an `id` from `speech_models` in entune_setup) the default speech
        model: the one every dictation uses. A local model must be downloaded first."""
        if model not in {m.id for m in app.models.available_models()}:
            raise ToolError(
                f"{model} is not ready: download it, or it needs a key the person saves;"
                " see entune_setup"
            )
        try:
            app.models.set_default_model(model)
        except (UnknownModel, ValueError) as exc:
            raise failed(exc) from exc
        return {"default_speech_model": app.models.default_model()}

    @mcp.tool(annotations=EDIT)
    def set_processing(
        decision_model: Literal["jev", "laya", "openai", "perplexity"] | None = None,
        dictionary: bool | None = None,
        formatting: bool | None = None,
        cleanup: bool | None = None,
    ) -> dict[str, Any]:
        """Choose the decision model and turn its steps on or off; leave out what should
        stay. `dictionary` applies the dictionary in context, `formatting` adds paragraph
        breaks and bullets, `cleanup` removes hesitation sounds and repeats. A step can be
        on only with a decision model that is ready (see decision_models in entune_setup).
        Laya runs on this Mac: if it needs its engine, run the install command it names
        yourself, then choose it."""
        try:
            app.settings.set_processing(decision_model, dictionary, formatting, cleanup)
        except ValueError as exc:
            raise failed(exc) from exc
        if decision_model is not None:
            app.decisions.sync(retry=True)  # choosing Laya again starts it after a failure
        status = app.settings.jev_status()
        return {
            "decision_model": app.settings.decision_model(),
            "steps": {
                "dictionary": status.dictionary,
                "formatting": status.formatting,
                "cleanup": status.cleanup,
            },
        }

    @mcp.tool(annotations=EDIT)
    def set_preferences(
        fast_mode: bool | None = None,
        remove_silence: bool | None = None,
        suggestion_model: str | None = None,
    ) -> dict[str, Any]:
        """Change other settings; leave out what should stay. `fast_mode` transcribes while
        the person speaks, so a long dictation is ready sooner (cloud models that stream,
        and local ones). `remove_silence` leaves long silences out of what the speech
        model is sent. `suggestion_model` is the model that builds the dictionary, as
        "provider:model" from suggestion_providers in entune_setup."""
        if suggestion_model is not None:
            provider, separator, name = suggestion_model.partition(":")
            if not separator or provider not in LLM_PROVIDERS or not name.strip():
                raise ToolError(
                    "suggestion_model is provider:model, with a provider from"
                    " suggestion_providers in entune_setup"
                )
            app.settings.set_dictionary_model(suggestion_model)
        if fast_mode is not None:
            app.settings.set_fast_mode(fast_mode)
        if remove_silence is not None:
            app.settings.set_remove_silence(remove_silence)
        return {
            "fast_mode": app.settings.fast_mode(),
            "remove_silence": app.settings.remove_silence(),
            "suggestion_model": app.settings.dictionary_model(),
        }

    @mcp.tool(annotations=READ)
    def find_audio() -> dict[str, Any]:
        """Audio Entune can learn the dictionary from: other dictation apps that keep
        recordings on this Mac (import one with import_audio), the audio already imported,
        and the person's own Entune recordings. More audio teaches the dictionary more;
        import every app that has recordings."""
        with app.data.using_data("audio listing"):
            stored = app.store.learning_audio()
        imported = [a for a, _ in stored if not a.id.startswith("recording:")]
        # Imports made before the source was kept fall back to Wispr Flow's file names.
        sources = [
            a.source or ("wispr" if a.name.startswith("wispr-") else "folder") for a in imported
        ]
        apps = []
        for item in audio_import.APPS:
            try:
                found, note = audio_import.present(item), None
            except ValueError as exc:
                found, note = False, str(exc)
            apps.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "has_audio": found,
                    "imported": sources.count(item.id),
                    "note": note,
                }
            )
        return {
            "apps": apps,
            "imported": {
                "count": len(imported),
                "seconds": round(sum(a.seconds or 0 for a in imported)),
            },
            "entune_recordings": len(stored) - len(imported),
        }

    @mcp.tool(annotations=EDIT)
    def import_audio(app_id: str | None = None, paths: list[str] | None = None) -> dict[str, Any]:
        """Import audio to learn from: every recording another dictation app keeps
        (`app_id` from find_audio), or audio files and folders on this Mac (`paths`;
        folders are searched for WAV, MP3, M4A, FLAC, OGG and WebM files). Only the audio
        is copied, never another app's text. Importing again skips what is already
        there."""
        if (app_id is None) == (paths is None):
            raise ToolError("Give app_id or paths")
        try:
            with app.data.using_data("audio import"):
                if app_id is not None:
                    return audio_import.import_app(app.store, app_id)
                return _import_paths(app, paths or [])
        except (ValueError, OSError, Busy) as exc:
            raise failed(exc) from exc

    @mcp.tool(annotations=EDIT)
    def start_dictionary_build(
        source: Literal["audio", "history"] = "audio",
        include_recordings: bool = False,
        reread: bool = False,
        effort: str = DEFAULT_EFFORT,
    ) -> dict[str, Any]:
        """Build the dictionary for the default speech model. With source "audio", the
        imported audio this model has not learned from yet (all of it with `reread`) is
        transcribed again with that model (with `include_recordings`, the person's own
        Entune recordings too) and the suggestion model proposes entries while it runs;
        with "history", it reads the person's transcripts not learned from yet (all of them
        with `reread`). The reply says how many recordings or transcripts it reads. It runs in the
        background: check dictionary_build_status, then read_suggestions and
        apply_suggestions. A local speech model costs nothing; a cloud one is billed per
        minute of audio to the person's key. `effort` is the suggestion model's reasoning:
        minimal, low, medium, high (default) or xhigh."""
        if effort not in EFFORTS:
            raise ToolError(f"Choose an effort: {', '.join(EFFORTS)}")
        audio_ids = None
        seconds = 0.0
        unmeasured = 0
        if source == "audio":
            model = app.models.default_model() or ""
            learned = set() if reread else app.store.learning_covered(model, "audio")
            with app.data.using_data("audio listing"):
                chosen = [
                    a
                    for a, _ in app.store.learning_audio()
                    if (include_recordings or not a.id.startswith("recording:"))
                    and a.id not in learned
                ]
            if not chosen:
                raise ToolError(
                    "No audio this speech model has not learned from: import more, or pass"
                    " reread to read it all again"
                )
            audio_ids = [a.id for a in chosen]
            seconds = sum(a.seconds or 0 for a in chosen)
            unmeasured = sum(a.seconds is None for a in chosen)
        try:
            state = app.learning.start_dictionary_build(
                source,
                scope="all" if reread else "new",
                audio_ids=audio_ids,
                effort=effort,
            )
        except (JobConflict, Busy, ValueError) as exc:
            raise failed(exc) from exc
        summary = _summary(state)
        if source == "audio":
            summary["audio_minutes"] = round(seconds / 60)
            if unmeasured:
                # MP3, M4A, FLAC and OGG imports have no measured length: the minutes are
                # a floor, not the total.
                summary["recordings_without_length"] = unmeasured
        return summary

    @mcp.tool(annotations=READ)
    def dictionary_build_status() -> dict[str, Any]:
        """Where the dictionary build stands: transcribing (recordings done of all), building
        (parts done of all), ready (suggestions waiting: read_suggestions, then
        apply_suggestions), cancelled or failed (with the reason; continue it with
        control_dictionary_build), accepted or discarded."""
        return _summary(app.learning.dictionary_build_status())

    @mcp.tool(annotations=EDIT)
    def control_dictionary_build(
        action: Literal["stop", "continue", "discard"], effort: str | None = None
    ) -> dict[str, Any]:
        """Stop a running build (finished parts are kept), continue a stopped or failed one
        from where it stopped (optionally with another `effort`), or discard its
        suggestions without changing the dictionary."""
        state = app.learning.dictionary_build_status()
        if "id" not in state:
            raise ToolError("There is no dictionary build; start one with start_dictionary_build")
        job = str(state["id"])
        try:
            if action == "stop":
                app.learning.cancel_dictionary_build(job)
            elif action == "continue":
                app.learning.retry_dictionary_build(job, effort)
            else:
                app.learning.discard_dictionary_build(job)
        except (JobConflict, Busy, ValueError) as exc:
            raise failed(exc) from exc
        return _summary(app.learning.dictionary_build_status())

    @mcp.tool(annotations=READ)
    def read_suggestions() -> dict[str, Any]:
        """The suggestions of a finished build, with the `build` to name when applying
        them, each with its `id`: new and changed heard entries with the words they stand
        for (spelling and meaning, `new` when the build defines it), entries to remove, and
        clearer meanings for existing words."""
        proposal = _proposal(app)
        words = {w["id"]: w for w in proposal["words"]}
        stored = {w.id: w for w in app.dictionary.dictionary().words}
        new = set(proposal.get("newWords", []))

        def word(identity: str) -> dict[str, Any]:
            found = words.get(identity) or (asdict(stored[identity]) if identity in stored else {})
            return {
                "spelling": found.get("spelling"),
                "meaning": found.get("meaning"),
                "new": identity in new,
            }

        changes = []
        for change in proposal["changes"]:
            item: dict[str, Any] = {"id": change["id"], "kind": change["kind"]}
            if change["kind"] == "word":
                item.update(
                    spelling=change["after"]["spelling"],
                    meaning_before=change["before"]["meaning"],
                    meaning_after=change["after"]["meaning"],
                )
            else:
                heard = change["after"] or change["before"]
                item["text"] = heard["text"]
                for side in ("before", "after"):
                    if change[side] is not None:
                        item[side] = [word(c["word"]) for c in change[side]["candidates"]]
                if change["kind"] == "update" and item["before"] == item["after"]:
                    item["note"] = "same words; only the evidence behind them changes"
            changes.append(item)
        return {
            "build": _token(app, proposal),
            "speech_model": proposal["model"],
            "changes": changes,
        }

    @mcp.tool(annotations=EDIT)
    def apply_suggestions(build: str, leave_out: list[str] | None = None) -> dict[str, Any]:
        """Apply the suggestions of the `build` you read with read_suggestions, all of them
        or all but the `id`s in `leave_out`. Refine the result afterwards with the
        dictionary tools, as you would any entry."""
        state = app.learning.dictionary_build_status()
        proposal = _proposal(app)
        if _token(app, proposal) != build:
            raise ToolError("Those suggestions changed since you read them; read_suggestions again")
        skipped = set(leave_out or [])
        unknown = skipped - {c["id"] for c in proposal["changes"]}
        if unknown:
            raise ToolError(f"No suggestion has the id {sorted(unknown)[0]}; see read_suggestions")
        chosen = [c for c in proposal["changes"] if c["id"] not in skipped]
        words = {w["id"]: w for w in proposal["words"]}
        named = dict.fromkeys(
            c["word"]
            for change in chosen
            if change["kind"] != "word" and change["after"]
            for c in change["after"]["candidates"]
        )
        selected = [{"id": c["id"], "after": c["after"]} for c in chosen]
        selected += [
            {"id": f"word:{w}", "after": words[w]}
            for w in named
            if w in set(proposal.get("newWords", [])) and w in words
        ]

        def unchanged(current: Proposal) -> bool:
            return _fingerprint(current.as_json()) == build.partition(":")[2]

        try:
            app.learning.accept_dictionary_build(str(state["id"]), selected, unchanged)
        except (JobConflict, DictionaryChanged, Busy, ValueError) as exc:
            raise failed(exc) from exc
        dictionary = app.dictionary.dictionary()
        return {
            "applied": len(chosen),
            "words": len(dictionary.words),
            "learned": {m: len(es) for m, es in dictionary.learned.items()},
            "version": app.dictionary.dictionary_version(),
        }

    @mcp.tool(annotations=READ)
    def recent_dictations(limit: int = 20) -> list[dict[str, Any]]:
        """How the person's latest dictations came out, newest first: the speech model,
        what it wrote, the text delivered, and each step's outcome. For the dictionary:
        what it replaced, and how many matches it kept as heard or could not decide. Use
        it to see whether the dictionary helps and what to fix; nothing here needs the
        person."""
        with app.data.using_data("history listing"):
            recordings = app.store.list_recordings(limit=max(1, min(limit, 100)))
        found = []
        for recording in recordings:
            if not recording.transcriptions:
                continue
            attempt = recording.transcriptions[0]  # attempts come newest first
            item: dict[str, Any] = {
                "recording": recording.id,
                "at": attempt.created_at,
                "speech_model": f"{attempt.provider}/{attempt.model}",
                "status": attempt.status,
                "error": attempt.error,
                "raw_text": attempt.raw_text,
                "text": attempt.text,
            }
            for name, stage in (
                ("dictionary", attempt.correction),
                ("formatting", attempt.formatting),
                ("cleanup", attempt.cleanup),
            ):
                if stage is None:
                    continue
                outcome: dict[str, Any] = {"status": stage.status, "error": stage.error}
                if name == "dictionary":
                    outcome.update(
                        replaced=[
                            {"heard": c.before, "wrote": c.after} for c in stage.changes or ()
                        ],
                        kept_as_heard=stage.preserved,
                        undecided=stage.abstained,
                    )
                item[name] = outcome
            found.append(item)
        return found


LLM_PROVIDERS = suggestion_model.LLM_PROVIDERS


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    """A build's state as the tools name things: where it stands, how far it is, and what
    is next, without its proposal or the page's internal fields."""
    names = {
        "phase": "phase",
        "id": "build",
        "outcome": "outcome",
        "source": "source",
        "model": "speech_model",
        "dictionaryModel": "suggestion_model",
        "effort": "effort",
        "skipped": "skipped_recordings",
        "skippedReason": "skipped_reason",
        "error": "error",
        "errorDetail": "error_detail",
        "applied": "applied",
    }
    summary = {new: state[old] for old, new in names.items() if state.get(old) is not None}
    if state.get("source") == "audio":
        summary["recordings"] = {
            "transcribed": state.get("completed", 0),
            "of": state.get("total", 0),
        }
    elif state.get("source") == "history":
        summary["transcripts"] = state.get("total", 0)
    if "completedBatches" in state:
        parts: dict[str, Any] = {
            "done": state["completedBatches"],
            "of": state.get("steps") or None,
        }
        if state.get("stepStartedAt"):
            # A part takes minutes at high effort: this says it is still going.
            parts["running"] = state.get("step")
            parts["running_for_seconds"] = round(time.time() - float(state["stepStartedAt"]))
        if (state.get("attempt") or 1) > 1 and state.get("brokenRule"):
            parts["fixing"] = state["brokenRule"]
        summary["parts"] = parts
    if state.get("phase") == "ready":
        summary["next"] = "read_suggestions, then apply_suggestions"
    return summary


def _proposal(app: Entune) -> dict[str, Any]:
    state = app.learning.dictionary_build_status()
    if state.get("phase") != "ready":
        raise ToolError(f"No suggestions are waiting (the build is {state.get('phase')})")
    detail = app.learning.dictionary_build_status(str(state["id"]))
    proposal = detail.get("proposal")
    if not isinstance(proposal, dict):
        raise ToolError("The build has no suggestions to read")
    return proposal


def _import_paths(app: Entune, paths: list[str]) -> dict[str, Any]:
    files: list[Path] = []
    skipped: list[str] = []  # an unreadable file or folder does not stop the others
    for value in paths:
        path = Path(value).expanduser()
        if path.is_dir():
            # os.walk reports a folder it may not read; Path.rglob would hide it.
            for folder, _, names in os.walk(path, onerror=lambda e: skipped.append(str(e))):
                files += sorted(
                    Path(folder) / n for n in names if Path(n).suffix.lower() in AUDIO_SUFFIXES
                )
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError(f"No such file or folder: {value}")
    added = duplicates = 0
    for path in files:
        try:
            created = audio_import.recorded_at(path.stat().st_mtime)
            if audio_import.import_audio(app.store, path.read_bytes(), path.name, created):
                added += 1
            else:
                duplicates += 1
        except (ValueError, OSError) as exc:
            skipped.append(str(exc))
    return {
        "added": added,
        "duplicates": duplicates,
        "skipped": len(skipped),
        "first_skipped": skipped[0] if skipped else None,
    }


def _token(app: Entune, proposal: dict[str, Any]) -> str:
    """The build and exactly these suggestions: a build continued after they were read
    keeps its ID but not its suggestions."""
    return f"{app.learning.dictionary_build_status()['id']}:{_fingerprint(proposal)}"


def _fingerprint(proposal: dict[str, Any]) -> str:
    """Everything applying writes: the entries and the new words' definitions."""
    content = json.dumps([proposal["changes"], proposal["words"]], sort_keys=True).encode()
    return hashlib.sha256(content).hexdigest()[:16]


def _local(status: dict[str, Any], failures: dict[str, str]) -> dict[str, Any]:
    """A local model's state, with a download that failed before the model kept one."""
    failure = failures.get(status["name"])
    if failure and status["state"] in ("absent", "unavailable"):
        return {**status, "state": "error", "error": failure}
    return status
