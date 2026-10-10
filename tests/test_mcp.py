"""The MCP endpoint for agents, end to end through the app as an agent calls it."""

from __future__ import annotations

import json
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from entune.api import mcp_setup
from entune.app import audio_import
from entune.app.entune import Entune
from entune.app.suggestion_runs import JobConflict
from entune.audio.formats import wav_bytes
from entune.learning.suggestion_model import Request
from entune.processing.results import Processed, Stage
from entune.providers.local.contracts import LocalModelStatus
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER, wait_for_build
from tests.dictionary_samples import CLOUD, document, proposed
from tests.test_server import StubProvider

HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def call(client: TestClient, tool: str, **arguments: Any) -> Any:
    """One tool call as an agent sends it; the tool's result, or its error as {"error"}."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    body["params"] = {"name": tool, "arguments": arguments}
    reply = client.post("/mcp", headers=HEADERS, content=json.dumps(body))
    assert reply.status_code == 200, reply.text
    result = reply.json()["result"]
    if result["isError"]:
        return {"error": result["content"][0]["text"]}
    structured: dict[str, Any] = result["structuredContent"]
    return structured.get("result", structured) if set(structured) == {"result"} else structured


def test_an_agent_reads_looks_up_and_improves_the_dictionary(tmp_path: Path) -> None:
    store = Store(tmp_path)
    app = Entune(store, [StubProvider()])
    app.settings.set_key("stub", "k")
    app.models.set_default_model("stub/good")
    recording = store.create_recording(WEBM_HEADER)
    said = "Ask cloud code to review it. The files are in the cloud."
    store.add_transcription(recording.id, "stub", "good", "ok", said, None, raw_text=said)
    with closing(store), TestClient(create_app(app), base_url="http://localhost") as client:
        assert client.put("/api/dictionary", json=document(learned={"stub/good": (CLOUD,)}))
        listed = client.post(
            "/mcp", headers=HEADERS, content='{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
        ).json()["result"]["tools"]
        assert {t["name"] for t in listed} == {
            "dictionary_guide",
            "read_dictionary",
            "find_in_transcripts",
            "set_word",
            "set_heard_entry",
            "remove_heard_entry",
            "pin_heard_entry",
            "delete_word",
            # Running the rest of Entune for the person (api/mcp_setup.py).
            "entune_setup",
            "download_speech_model",
            "set_speech_model",
            "set_processing",
            "set_preferences",
            "find_audio",
            "import_audio",
            "start_dictionary_build",
            "dictionary_build_status",
            "control_dictionary_build",
            "read_suggestions",
            "apply_suggestions",
            "recent_dictations",
        }
        assert "Candidates come from real usage" in call(client, "dictionary_guide")
        read = call(client, "read_dictionary")
        assert read["default_speech_model"] == "stub/good"
        assert "stub/good" in read["speech_models"]
        assert read["dictionary"]["version"] == 3 and len(read["dictionary"]["words"]) == 3
        # What the default speech model applies, each entry with its section.
        assert read["speech_model"] == "stub/good" and read["unused_words"] == []
        assert [(e["text"], e["scope"]) for e in read["entries_in_use"]] == [
            ("cloud", "stub/good"),
            ("Claude", "stub/good"),
        ]
        found = call(client, "find_in_transcripts", text="cloud code")
        assert (found["total"], found["transcripts"], found["searched"]) == (1, 1, 1)
        assert found["excerpts"] == [
            {
                "transcript": found["excerpts"][0]["transcript"],
                "excerpt": said.replace("cloud code", "⟦cloud code⟧"),
            }
        ]
        # Every occurrence counts toward the total, however few excerpts are asked for.
        counted = call(client, "find_in_transcripts", text="the", limit=1)
        assert counted["total"] == 2 and len(counted["excerpts"]) == 1

        added = call(
            client,
            "set_word",
            version=read["version"],
            spelling="Claude Code",
            meaning="Anthropic's coding agent",
        )
        entry = call(
            client,
            "set_heard_entry",
            version=added["version"],
            scope="stub/good",
            text="cloud code",
            words=[added["word_id"]],
        )
        stale = call(client, "set_word", version=read["version"], spelling="x", meaning="y")
        assert "The dictionary changed since that version was read" in stale["error"]
        assert (
            "Unknown speech model"
            in call(client, "remove_heard_entry", version=entry["version"], scope="nope", text="x")[
                "error"
            ]
        )
        pinned = call(
            client,
            "pin_heard_entry",
            version=entry["version"],
            speech_model="stub/good",
            text="cloud code",
        )
        deleted = call(client, "delete_word", version=pinned["version"], word_id=added["word_id"])
        assert deleted["removed_from"] == ["cloud code"]
        # Removing entries keeps their words; they show as unused for the person to decide.
        version = deleted["version"]
        for text in ("Claude", "cloud"):
            version = call(
                client, "remove_heard_entry", version=version, scope="stub/good", text=text
            )["version"]
        unused = call(client, "read_dictionary")["unused_words"]
        assert [(w["id"], w["spelling"]) for w in unused] == [
            ("a_claude", "Claude"),
            ("b_cloud", "cloud"),
            ("c_cloud", "cloud"),
        ]
        final = client.get("/api/dictionary").json()
        assert final["pinned"] == [] and added["word_id"] not in {w["id"] for w in final["words"]}

        # A page from another site cannot reach it, nor a DNS-rebinding host.
        refused = client.post(
            "/mcp", headers={**HEADERS, "origin": "https://example.com"}, content="{}"
        )
        assert refused.status_code == 403
        assert client.post("/mcp", headers={**HEADERS, "host": "evil.example"}).status_code == 403


def test_edits_keep_the_file_readable_and_reach_a_removed_speech_models_entries(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    app = Entune(store, [StubProvider()])
    with closing(store), TestClient(create_app(app), base_url="http://localhost") as client:
        assert client.put("/api/dictionary", json=document(learned={"gone/model": (CLOUD,)}))
        version = call(client, "read_dictionary")["version"]
        unreadable = call(
            client,
            "set_heard_entry",
            version=version,
            scope="gone/model",
            text='"cloud"',
            words=["a_claude"],
        )
        assert "starts with a letter or digit" in unreadable["error"]
        removed = call(
            client, "remove_heard_entry", version=version, scope="gone/model", text="Claude"
        )
        assert [
            h["text"] for h in client.get("/api/dictionary").json()["learned"]["gone/model"]
        ] == ["cloud"]
        gone = call(client, "find_in_transcripts", text="cloud", speech_model="gone/model")
        assert gone == {"total": 0, "transcripts": 0, "searched": 0, "excerpts": []}
        view = call(client, "read_dictionary", speech_model="gone/model")
        assert [e["text"] for e in view["entries_in_use"]] == ["cloud"]
        assert removed["version"] == call(client, "read_dictionary")["version"]


def test_an_agent_sets_entune_up_and_builds_the_dictionary_without_the_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flow the guide gives: setup, models, steps, audio, build, apply, recent dictations."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")  # no real dictation apps
    heard = "hello there, I use cloud code"  # what StubProvider writes

    async def suggest(_: Request) -> str:
        return json.dumps(proposed(heard))

    store = Store(tmp_path / "data")
    app = Entune(store, [StubProvider()], llm_call=suggest)
    with closing(store), TestClient(create_app(app), base_url="http://localhost") as client:
        setup = call(client, "entune_setup")
        assert setup["default_speech_model"] is None and setup["speech_models"] == []
        assert len(setup["needs_person"]) == 3  # a speech, a suggestion and a decision model
        # Keys stay with the person: they save them in Settings.
        app.settings.set_key("stub", "speech-key")
        app.settings.set_key("openai", "suggestion-key")
        app.settings.set_key("typesafe", "decision-key")
        assert "not ready" in call(client, "set_speech_model", model="stub/nope")["error"]
        assert call(client, "set_speech_model", model="stub/good") == {
            "default_speech_model": "stub/good"
        }
        processing = call(
            client, "set_processing", decision_model="jev", dictionary=True, formatting=True
        )
        assert processing["steps"] == {"dictionary": True, "formatting": True, "cleanup": False}
        assert call(client, "set_preferences", fast_mode=True)["fast_mode"] is True
        assert "provider:model" in call(client, "set_preferences", suggestion_model="x")["error"]
        assert call(client, "entune_setup")["needs_person"] == []
        # A chosen suggestion model whose provider has no key is named as such.
        call(client, "set_preferences", suggestion_model="anthropic:claude-test")
        (blocked,) = call(client, "entune_setup")["needs_person"]
        assert "anthropic:claude-test has no key" in blocked
        call(client, "set_preferences", suggestion_model="openai:gpt-test")
        # Laya installed but failed to start is not ready.
        monkeypatch.setattr(app.decisions.laya, "status", lambda: ("failed", "port in use"))
        (laya,) = [d for d in call(client, "entune_setup")["decision_models"] if d["id"] == "laya"]
        assert laya == {
            "id": "laya",
            "ready": False,
            "needs": "Laya did not start: port in use",
            "state": "failed",
        }
        assert "Unknown local model" in call(client, "download_speech_model", name="x")["error"]
        # A download that fails before the model keeps any state is still shown.
        local = LocalModelStatus("tiny", "Tiny", 1, "", "absent", 0.0, None, "local")
        monkeypatch.setattr(app.models, "local_models", lambda: [local])

        def cannot_download(name: str) -> None:
            raise PermissionError("cannot create the models folder")

        monkeypatch.setattr(app.models, "download_local_model", cannot_download)
        assert call(client, "download_speech_model", name="tiny")["state"] == "downloading"
        deadline = time.monotonic() + 2
        while (m := call(client, "entune_setup")["local_models"][0])["state"] != "error":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert "cannot create the models folder" in m["error"]
        assert (
            "no dictionary build"
            in call(client, "control_dictionary_build", action="stop")["error"]
        )

        found = call(client, "find_audio")
        assert {a["id"] for a in found["apps"]} >= {"wispr", "superwhisper"}
        assert not any(a["has_audio"] for a in found["apps"])
        folder = tmp_path / "recordings"
        folder.mkdir()
        for i in range(2):
            (folder / f"{i}.wav").write_bytes(wav_bytes(bytes([i, 7]) * 16))
        (folder / "notes.txt").write_text("not audio")
        assert call(client, "import_audio", paths=[str(folder)]) == {
            "added": 2,
            "duplicates": 0,
            "skipped": 0,
            "first_skipped": None,
        }
        assert "app_id or paths" in call(client, "import_audio")["error"]
        assert call(client, "find_audio")["imported"]["count"] == 2

        started = call(client, "start_dictionary_build")
        assert started["phase"] in ("queued", "transcribing")
        assert started["recordings"] == {"transcribed": 0, "of": 2} and "audio_minutes" in started
        assert started["recordings_by_source"] == {"folder": 2}
        assert started["speech_model"] == "stub/good"  # the tools' own names, not the page's
        assert wait_for_build(client)["phase"] == "ready"
        status = call(client, "dictionary_build_status")
        assert status["phase"] == "ready" and status["next"]
        suggestions = call(client, "read_suggestions")
        (change,) = suggestions["changes"]
        assert change["kind"] == "add" and change["text"] == "cloud code"
        assert change["after"] == [
            {"spelling": "Claude Code", "meaning": "The named tool Claude Code.", "new": True}
        ]
        build = suggestions["build"]
        assert "changed since" in call(client, "apply_suggestions", build="other")["error"]
        refused = call(client, "apply_suggestions", build=build, leave_out=["x"])
        assert "no suggestion" in refused["error"].lower()
        # The check runs inside the apply step's lock: changed suggestions are refused there.
        with pytest.raises(JobConflict, match="changed since"):
            app.learning.accept_dictionary_build(build.partition(":")[0], None, lambda _: False)
        assert call(client, "dictionary_build_status")["phase"] == "ready"
        applied = call(client, "apply_suggestions", build=build)
        assert applied["applied"] == 1 and applied["learned"] == {"stub/good": 1}
        assert call(client, "dictionary_build_status")["phase"] == "accepted"
        assert "No suggestions are waiting" in call(client, "read_suggestions")["error"]
        # Audio this model learned from is not read again unless asked.
        assert "has not learned from" in call(client, "start_dictionary_build")["error"]
        again = call(client, "start_dictionary_build", reread=True)
        assert again["recordings"]["of"] == 2
        assert wait_for_build(client)["phase"] == "ready"
        assert call(client, "control_dictionary_build", action="discard")["phase"] == "discarded"

        # A retry after a failure: the newest attempt is the one reported.
        recording = store.create_recording(WEBM_HEADER)
        store.add_transcription(recording.id, "stub", "bad", "error", None, "HTTP 401")
        steps = Processed(heard, Stage("skipped", "contextual"), Stage("disabled", "formatting"))
        store.add_transcription(
            recording.id, "stub", "good", "ok", heard, None, raw_text=heard, processing=steps
        )
        (latest,) = call(client, "recent_dictations", limit=5)
        assert latest["speech_model"] == "stub/good" and latest["raw_text"] == heard
        assert latest["dictionary"]["why"] == "nothing to do: no dictionary entry matched"
        assert latest["formatting"]["why"] == "the step is off"
        assert latest["processing_state"] == "processing" and latest["notice"] is None


def test_a_dictation_app_folder_macos_will_not_let_entune_read_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    app = audio_import.DictationApp("demo", "Demo", "", ("recordings",), "*.wav")
    assert audio_import.present(app) is False  # no folder: nothing to import
    folder = tmp_path / "recordings"
    folder.mkdir()
    (folder / "a.wav").write_bytes(wav_bytes(bytes([1, 2]) * 16))
    assert audio_import.present(app) is True
    if sys.platform == "win32":
        return  # a folder's mode does not refuse reading on Windows
    folder.chmod(0)
    try:
        with pytest.raises(ValueError, match="may not read"):
            audio_import.present(app)
        # Importing a folder that holds one Entune may not read says so too.
        store = Store(tmp_path / "data")
        with closing(store):
            result = mcp_setup._import_paths(Entune(store, [StubProvider()]), [str(tmp_path)])
        assert result["skipped"] == 1 and "Permission" in result["first_skipped"]
    finally:
        folder.chmod(0o755)


def test_the_build_token_covers_the_new_words_definitions_too() -> None:
    class Builds:
        def dictionary_build_status(self) -> dict[str, str]:
            return {"id": "b1"}

    app: Any = type("App", (), {"learning": Builds()})()
    proposal = {"changes": [{"id": "heard:x"}], "words": [{"id": "n1", "meaning": "first"}]}
    token = mcp_setup._token(app, proposal)
    assert token.startswith("b1:") and token == mcp_setup._token(app, proposal)
    revised = {**proposal, "words": [{"id": "n1", "meaning": "revised"}]}
    assert mcp_setup._token(app, revised) != token


def test_a_build_for_audio_chosen_for_another_speech_model_is_refused(tmp_path: Path) -> None:
    async def suggest(_: Request) -> str:
        return json.dumps(proposed("hello there, I use cloud code"))

    store = Store(tmp_path / "data")
    app = Entune(store, [StubProvider()], llm_call=suggest)
    app.settings.set_key("stub", "speech-key")
    app.settings.set_key("openai", "suggestion-key")
    app.models.set_default_model("stub/good")
    with closing(store), TestClient(create_app(app), base_url="http://localhost") as client:
        folder = tmp_path / "audio"
        folder.mkdir()
        (folder / "a.wav").write_bytes(wav_bytes(bytes([5, 9]) * 16))
        call(client, "import_audio", paths=[str(folder)])
        covered = app.store.learning_covered

        def switched(model: str, source: str) -> set[str]:
            app.models.set_default_model("stub/bad")  # changed while the audio is chosen
            return covered(model, source)

        app.store.learning_covered = switched  # type: ignore[method-assign]
        assert "changed as the build started" in call(client, "start_dictionary_build")["error"]
        assert wait_for_build(client)["phase"] == "cancelled"
