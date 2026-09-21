"""Jev on a transcript, with TypeSafe's endpoint replaced by a handler."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from dictum import jev
from dictum.dictionary import Entry, matches
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store
from tests.conftest import WEBM_HEADER, mock_client
from tests.test_server import StubProvider

Handler = Callable[[httpx.Request], httpx.Response]
ENTRIES = (
    Entry("JEV", "TypeSafe's decision model; not a person named Jeff", ("Jeff", "jav")),
    Entry("Claude Code", heard=("cloud code",)),
)


def answering(
    decide: Callable[[str, dict[str, object]], dict[str, float]],
) -> tuple[list[dict[str, object]], Handler]:
    """A TypeSafe stand-in: `decide(name, question)` gives each answer's probabilities."""
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer ts-key"
        assert body["model"] == jev.MODEL
        answers = {}
        for name, question in body["questions"].items():
            p = decide(name, question)
            answers[name] = {
                "type": "choice",
                "choice": max(p, key=lambda k: p[k]),
                "probabilities": p,
                "confidence": 0.9,
            }
        return httpx.Response(200, json={"model": jev.MODEL, "answers": answers, "usage": {}})

    return requests, handler


def use(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    monkeypatch.setattr(jev, "_client", lambda: mock_client(handler))


def test_decide_sends_only_the_matched_entries_and_vetoes_on_probability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Jeff is my brother. jav is fast, and cloud code runs it."

    def decide(name: str, question: dict[str, object]) -> dict[str, float]:
        return (
            {"term": 0.1, "recognised": 0.9} if name == "o0" else {"term": 0.97, "recognised": 0.03}
        )

    requests, handler = answering(decide)
    use(monkeypatch, handler)
    found = matches(ENTRIES, text)
    decisions, elapsed = jev.decide(text, found, "ts-key")
    assert [(d.match.spelling, d.replace, d.recognised) for d in decisions] == [
        ("JEV", False, 0.9),
        ("JEV", True, 0.03),
        ("Claude Code", True, 0.03),
    ]
    assert elapsed >= 0
    state = requests[0]["state"]
    assert isinstance(state, dict)
    assert state["transcript"] == text
    assert state["terms"] == {
        "JEV": {"spelling": "JEV", "meaning": "TypeSafe's decision model; not a person named Jeff"},
        "Claude_Code": {
            "spelling": "Claude Code",
            "meaning": "the term 'Claude Code', as this person spells it",
        },
    }
    assert state["occurrences"]["o1"] == {
        "heard": "jav",
        "before": "Jeff is my brother. ",
        "after": " is fast, and cloud code runs it.",
    }
    question = requests[0]["questions"]["o2"]  # type: ignore[index]
    assert "`terms.Claude_Code`" in question["instructions"]["question"]
    assert set(question["criteria"]) == {"term", "recognised"}
    assert jev.decide(text, [], "ts-key") == ([], 0.0)


def test_unusable_answers_and_failures_are_jev_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Jeff"
    found = matches(ENTRIES, text)
    use(monkeypatch, lambda _: httpx.Response(429, text="slow down ts-key"))
    with pytest.raises(jev.JevError, match=r"HTTP 429: slow down $"):
        jev.decide(text, found, "ts-key")
    use(monkeypatch, lambda _: httpx.Response(200, json={"answers": {"other": {}}}))
    with pytest.raises(jev.JevError, match="do not match"):
        jev.decide(text, found, "ts-key")
    use(
        monkeypatch,
        lambda _: httpx.Response(200, json={"answers": {"o0": {"probabilities": {"term": 2}}}}),
    )
    with pytest.raises(jev.JevError, match="probabilities"):
        jev.decide(text, found, "ts-key")

    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    use(monkeypatch, down)
    with pytest.raises(jev.JevError, match="ConnectError: no route"):
        jev.decide(text, found, "ts-key")


def test_formatting_inserts_breaks_and_bullets_and_keeps_every_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Two things. First, the key. And the model.  Second, the port. Then unrelated news."
    plan = {
        "S01": {"continues": 0.1, "new_paragraph": 0.1, "list_item": 0.8},
        "S02": {"continues": 0.6, "new_paragraph": 0.05, "list_item": 0.35},  # bridged
        "S03": {"continues": 0.2, "new_paragraph": 0.1, "list_item": 0.7},
        "S04": {"continues": 0.3, "new_paragraph": 0.7, "list_item": 0.0},
    }
    requests, handler = answering(lambda name, _: plan[name])
    use(monkeypatch, handler)
    formatted, _ = jev.format_text(text, "ts-key")
    assert formatted == (
        "Two things.\n\n- First, the key.\n- And the model.\n- Second, the port.\n\n"
        "Then unrelated news."
    )
    assert formatted.replace("\n", " ").replace("- ", "").split() == text.split()
    assert list(requests[0]["state"]["sentences"]) == ["S00", "S01", "S02", "S03", "S04"]  # type: ignore[index]
    assert "S00" not in requests[0]["questions"]  # type: ignore[operator]
    # Below the bar nothing changes; one sentence needs no request at all.
    weak = {name: {"continues": 0.5, "new_paragraph": 0.5, "list_item": 0.0} for name in plan}
    use(monkeypatch, answering(lambda name, _: weak[name])[1])
    assert jev.format_text(text, "ts-key")[0] == text
    assert jev.format_text("One sentence only.", "ts-key") == ("One sentence only.", 0.0)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    stub = StubProvider()
    client = TestClient(create_app(Dictum(Store(tmp_path), [stub])), base_url="http://localhost")
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    client.put(
        "/api/dictionary",
        json={
            "pinned": [
                {"spelling": "Claude Code", "description": "the agent", "heard": ["cloud code"]}
            ]
        },
    )
    return client


def test_jev_settings_need_a_key_and_report_what_it_did(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = client.get("/api/settings").json()["jev"]
    assert settings == {
        "key_hint": None,
        "dictionary": False,
        "formatting": False,
        "summary": {
            "transcriptions": 0,
            "fixed": 0,
            "kept": 0,
            "failed": 0,
            "median_seconds": None,
        },
    }
    refused = client.put("/api/settings", json={"jev": {"dictionary": True}})
    assert refused.status_code == 400 and "TypeSafe API key first" in refused.text
    assert client.put("/api/settings", json={"keys": {"typesafe": "ts-key"}}).status_code == 200
    assert client.put("/api/settings", json={"jev": {"dictionary": True}}).status_code == 200
    settings = client.get("/api/settings").json()["jev"]
    assert settings["key_hint"] == "••••-key" and settings["dictionary"] is True

    requests, handler = answering(lambda *_: {"term": 0.05, "recognised": 0.95})
    use(monkeypatch, handler)
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["text"] == "hello there, I use cloud code"  # Jev kept the words as heard
    assert attempt["raw_text"] == "hello there, I use cloud code"
    assert (attempt["jev_fixed"], attempt["jev_kept"], attempt["jev_error"]) == (0, 1, None)
    assert attempt["jev_seconds"] >= 0 and len(requests) == 1

    use(monkeypatch, answering(lambda *_: {"term": 0.9, "recognised": 0.1})[1])
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["text"] == "hello there, I use Claude Code"
    assert (attempt["jev_fixed"], attempt["jev_kept"]) == (1, 0)

    # A failure falls back to replacing every match, and says so with the transcript.
    use(monkeypatch, lambda _: httpx.Response(529, text="overloaded"))
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["text"] == "hello there, I use Claude Code"
    assert attempt["jev_error"] == "contextual dictionary: HTTP 529: overloaded"
    assert attempt["jev_fixed"] == 1 and attempt["jev_seconds"] == 0.0

    summary = client.get("/api/settings").json()["jev"]["summary"]
    assert summary == {
        "transcriptions": 3,
        "fixed": 2,
        "kept": 1,
        "failed": 1,
        "median_seconds": summary["median_seconds"],
    }
    assert summary["median_seconds"] is not None

    # Formatting runs on the corrected text; with both off, nothing is recorded.
    plan = {"S01": {"continues": 0.1, "new_paragraph": 0.9, "list_item": 0.0}}
    client.put("/api/settings", json={"jev": {"dictionary": False, "formatting": True}})
    use(monkeypatch, answering(lambda name, _: plan[name])[1])
    client.put(
        "/api/dictionary",
        json={"pinned": [{"spelling": "Hello there.", "heard": ["hello there,"]}]},
    )
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["text"] == "Hello there.\n\nI use cloud code"
    assert attempt["jev_fixed"] is None and attempt["jev_seconds"] >= 0
    client.put("/api/settings", json={"jev": {"formatting": False}})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    assert rec["transcriptions"][0]["jev_seconds"] is None
