"""The MCP endpoint for agents, end to end through the app as an agent calls it."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from entune.app.entune import Entune
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER
from tests.dictionary_samples import CLOUD, document
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
        assert found == [
            {
                "transcript": found[0]["transcript"],
                "excerpt": said.replace("cloud code", "⟦cloud code⟧"),
            }
        ]

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
        unsaid = call(client, "set_word", version=added["version"], spelling="x", meaning=" ")
        assert "Give the word's meaning" in unsaid["error"]
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
        assert call(client, "read_dictionary")["unused_words"] == ["a_claude", "b_cloud", "c_cloud"]
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
        assert call(client, "find_in_transcripts", text="cloud", speech_model="gone/model") == []
        view = call(client, "read_dictionary", speech_model="gone/model")
        assert [e["text"] for e in view["entries_in_use"]] == ["cloud"]
        assert removed["version"] == call(client, "read_dictionary")["version"]
