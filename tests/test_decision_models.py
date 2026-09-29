from __future__ import annotations

import json
import sys
import textwrap
import time
from contextlib import closing
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from entune.app.entune import Entune
from entune.processing import laya as laya_module
from entune.processing.laya import Laya
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER
from tests.dictionary_samples import group
from tests.test_server import StubProvider

# Stands in for the `laya` package's server: the same environment, health check and
# System One answers (the first option of each question), without PyTorch.
FAKE_SERVE = """
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

def main():
    if os.environ.get("FAKE_LAYA_BROKEN"):
        sys.exit("ModuleNotFoundError: No module named 'fastapi'")
    seen = os.path.join(os.path.dirname(os.environ["HF_HOME"]), "seen.json")
    with open(seen, "w") as out:
        json.dump({k: os.environ.get(k) for k in ("LAYA_HOST", "LAYA_MODELS", "LAYA_API_KEY")}, out)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"status": "ok"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            key = os.environ.get("LAYA_API_KEY")
            if len(body["questions"]) > 64:  # the real server's limit
                self.send_response(413)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            if key and self.headers.get("authorization") != "Bearer " + key:
                self.send_response(401)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            answers = {}
            for name, question in body["questions"].items():
                options = list(question["criteria"])
                p = {o: (1.0 if i == 0 else 0.0) for i, o in enumerate(options)}
                answers[name] = {"type": "choice", "choice": options[0], "probabilities": p}
            with open(seen + ".requests", "a") as out:
                auth = self.headers.get("authorization")
                questions = sorted(body["questions"])
                asked = {"auth": auth, "questions": questions, "state": body["state"]}
                out.write(json.dumps(asked) + "\\n")
            self.reply({"model": body["model"], "answers": answers})

        def log_message(self, *args):
            pass

    HTTPServer((os.environ["LAYA_HOST"], int(os.environ["LAYA_PORT"])), Handler).serve_forever()
"""


@pytest.fixture
def fake_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    package = tmp_path / "engine" / "laya"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "serve.py").write_text(textwrap.dedent(FAKE_SERVE))
    monkeypatch.setenv("PYTHONPATH", str(package.parent))
    monkeypatch.setenv("LAYA_API_KEY", "must-not-reach-the-server")
    return Path(sys.executable)


def wait_until(condition: object, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():  # type: ignore[operator]
            return
        time.sleep(0.05)
    pytest.fail("condition not reached")


def test_laya_is_reported_missing_without_its_engine(tmp_path: Path) -> None:
    laya = Laya(tmp_path / "models", find_engine=lambda: None)
    laya.start()
    assert laya.status() == ("unavailable", None)
    endpoint = laya.endpoint()
    assert endpoint.unavailable == (
        f"Laya's engine is not installed. Run: {laya_module.INSTALL_COMMAND}"
    )


def test_laya_runs_on_this_mac_while_chosen_and_stops_when_not(
    tmp_path: Path, fake_engine: Path
) -> None:
    store = Store(tmp_path / "data")
    laya = Laya(store.data_dir / "models", find_engine=lambda: fake_engine)
    service = Entune(store, [StubProvider()], laya=laya)
    try:
        with TestClient(create_app(service), base_url="http://localhost") as client:
            settings = client.get("/api/settings").json()["decisionModel"]
            assert settings == {
                "selected": None,
                "laya": {
                    "state": "stopped",
                    "error": None,
                    "install": "uv tool install 'laya[serve]'",
                },
            }
            refused = client.put("/api/settings", json={"jev": {"dictionary": True}})
            assert refused.text == "Choose a decision model first."
            assert client.put("/api/settings", json={"decisionModel": "laya"}).status_code == 200
            assert laya.status()[0] == "stopped"  # chosen, but no step asks it yet
            ok = client.put(
                "/api/settings",
                json={"keys": {"stub": "k"}, "defaultModel": "stub/good", "jev": {"cleanup": True}},
            )
            assert ok.status_code == 200
            wait_until(lambda: laya.status()[0] == "ready")
            seen = (store.data_dir / "models" / "laya" / "seen.json").read_text()
            assert '"LAYA_HOST": "127.0.0.1"' in seen and '"LAYA_MODELS": "english"' in seen
            key = json.loads(seen)["LAYA_API_KEY"]  # a token of its own, not the environment's
            assert key and key != "must-not-reach-the-server"

            # A dictation asks Laya, without a key, and records that it did.
            attempt = client.post(
                "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}
            ).json()["transcriptions"][0]
            assert attempt["cleanup"]["model"] == "laya"
            assert attempt["cleanup"]["status"] in ("succeeded", "skipped")
            assert attempt["correction"]["model"] is None  # dictionary step is off

            summary = client.get("/api/settings").json()["jev"]["summary"]
            assert summary["transcriptions"] == 1

            client.put("/api/settings", json={"jev": {"cleanup": False}})
            assert laya.status()[0] == "stopped"
    finally:
        service.close()
    assert laya.status()[0] == "stopped"


def test_a_failed_laya_start_is_reported_and_restarts_only_when_chosen_again(
    tmp_path: Path, fake_engine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LAYA_BROKEN", "1")
    store = Store(tmp_path / "data")
    laya = Laya(store.data_dir / "models", find_engine=lambda: fake_engine)
    service = Entune(store, [StubProvider()], laya=laya)
    try:
        service.settings.set_processing("laya")
        service.settings.set_processing(formatting=True)
        wait_until(lambda: laya.status()[0] == "failed")
        error = laya.status()[1]
        assert error == "Laya stopped: ModuleNotFoundError: No module named 'fastapi'"
        assert laya.endpoint().unavailable == error
        service.settings.set_fast_mode(True)  # an unrelated change does not restart it
        assert laya.status() == ("failed", error)
        monkeypatch.delenv("FAKE_LAYA_BROKEN")
        service.decisions.sync(retry=True)
        wait_until(lambda: laya.status()[0] == "ready")
    finally:
        service.close()


def test_the_chosen_decision_model_must_be_able_to_run_the_steps(tmp_path: Path) -> None:
    service = Entune(
        Store(tmp_path), [StubProvider()], laya=Laya(tmp_path / "models", find_engine=lambda: None)
    )
    with TestClient(create_app(service), base_url="http://localhost") as client:
        assert client.put("/api/settings", json={"decisionModel": "other"}).status_code == 400
        # Saving a TypeSafe key once chose Jev, the only decision model; it still does.
        client.put("/api/settings", json={"keys": {"typesafe": "ts-key"}})
        assert client.get("/api/settings").json()["decisionModel"]["selected"] == "jev"
        assert client.put("/api/settings", json={"jev": {"dictionary": True}}).status_code == 200
        refused = client.put("/api/settings", json={"decisionModel": "laya"})
        assert refused.text == (f"Install Laya's engine first: {laya_module.INSTALL_COMMAND}")
        assert client.get("/api/settings").json()["decisionModel"]["selected"] == "jev"
        client.put("/api/settings", json={"jev": {"dictionary": False}})
        assert client.put("/api/settings", json={"decisionModel": "laya"}).status_code == 200
        refused = client.put("/api/settings", json={"jev": {"formatting": True}})
        assert refused.status_code == 400


def test_the_summary_counts_only_the_chosen_decision_models_work(tmp_path: Path) -> None:
    from entune.app.metrics import processing_summary
    from entune.processing.results import Processed, Stage

    store = Store(tmp_path)
    recording = store.create_recording(WEBM_HEADER, None)
    for model in (None, "jev", "laya"):  # None: recorded before the choice existed
        stage = Stage("succeeded", "cleanup", seconds=1.0, removed_words=1, model=model)
        disabled = Stage("disabled", "deterministic"), Stage("disabled", "formatting")
        processed = Processed("text", *disabled, stage)
        store.add_transcription(
            recording.id, "stub", "good", "ok", "text", None, processing=processed
        )
    assert processing_summary(store, "jev").stages["cleanup"].removed_words == 2
    assert processing_summary(store, "laya").stages["cleanup"].removed_words == 1
    assert processing_summary(store).stages["cleanup"].removed_words == 3


def test_meaning_questions_go_to_the_chosen_endpoint(
    tmp_path: Path, fake_engine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entune.dictionary import entries
    from entune.processing import jev_client
    from entune.processing.pipeline import process_text

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):  # never used for this Mac's server
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    laya = Laya(tmp_path / "models", find_engine=lambda: fake_engine)
    laya.start()
    try:
        wait_until(lambda: laya.status()[0] == "ready")
        found = (group("Entune", "victim"), group("Jev", "Jeff"))
        groups = entries.Dictionary(learned={"s/m": found}).effective("s/m")
        with closing(jev_client.Client()) as client:
            result = process_text(
                "Open victim now. Then ask Jeff.",
                groups,
                contextual=True,
                formatting=False,
                key="ts-key",  # a saved TypeSafe key never goes to Laya
                client=client,
                policy=jev_client.Policy(),
                endpoint=laya.endpoint(),
            )
        correction = result.correction
        assert correction.status == "succeeded" and correction.model == "laya"
        assert correction.decisions == 2 and correction.attempts == 1  # two requests, no retry
        # Laya reads a short input, so each occurrence is asked alone, with only its passage.
        lines = (tmp_path / "models" / "laya" / "seen.json.requests").read_text().splitlines()
        asked = [json.loads(line) for line in lines][1:]  # after the warm-up
        assert [a["questions"] for a in asked] == [["o0"], ["o1"]]
        assert [list(a["state"]["occurrences"]) for a in asked] == [["o0"], ["o1"]]
        seen = json.loads((tmp_path / "models" / "laya" / "seen.json").read_text())
        assert all(a["auth"] == "Bearer " + seen["LAYA_API_KEY"] for a in asked)
    finally:
        laya.stop()


def test_a_laya_that_cannot_launch_is_reported_not_raised(tmp_path: Path) -> None:
    laya = Laya(tmp_path / "models", find_engine=lambda: tmp_path / "gone" / "python")
    service = Entune(Store(tmp_path / "data"), [StubProvider()], laya=laya)
    try:
        service.settings.set_processing("laya", cleanup=True)  # no exception reaches here
        state, error = laya.status()
        assert state == "failed" and error is not None
        assert error.startswith("Laya could not start: [Errno 2] No such file or directory")
    finally:
        service.close()


def test_deleting_all_data_stops_laya_first(tmp_path: Path, fake_engine: Path) -> None:
    store = Store(tmp_path / "data")
    laya = Laya(store.data_dir / "models", find_engine=lambda: fake_engine)
    service = Entune(store, [StubProvider()], laya=laya)
    try:
        service.settings.set_processing("laya", formatting=True)
        wait_until(lambda: laya.status()[0] == "ready")
        reset = store.reset
        seen: list[str] = []

        def reset_after_stop() -> tuple[list[str], list[str]]:
            seen.append(laya.status()[0])  # nothing writes into the folders being deleted
            return reset()

        store.reset = reset_after_stop  # type: ignore[method-assign]
        service.data.reset_data()
        assert seen == ["stopped"] and not (store.data_dir / "models").exists()
        assert laya.status()[0] == "stopped" and service.settings.decision_model() is None
    finally:
        service.close()


def test_model_and_step_changes_are_checked_and_applied_together(
    tmp_path: Path, fake_engine: Path
) -> None:
    laya = Laya(tmp_path / "models", find_engine=lambda: fake_engine)
    service = Entune(Store(tmp_path / "data"), [StubProvider()], laya=laya)
    try:
        with TestClient(create_app(service), base_url="http://localhost") as client:
            client.put("/api/settings", json={"decisionModel": "laya", "jev": {"cleanup": True}})
            # Switching to Jev while turning its only step off needs no key.
            both = {"decisionModel": "jev", "jev": {"cleanup": False}, "fastMode": True}
            assert client.put("/api/settings", json=both).status_code == 200
            assert service.settings.decision_model() == "jev"
            # A refused change stores none of its fields.
            refused = client.put(
                "/api/settings", json={"fastMode": False, "jev": {"cleanup": True}}
            )
            assert refused.text == "Save a TypeSafe API key first."
            assert service.settings.fast_mode() is True
            assert client.put(
                "/api/settings", json={"keys": {"typesafe": "ts-key"}, "jev": {"cleanup": True}}
            ).json() == {"ok": True}
    finally:
        service.close()


def test_more_questions_than_laya_takes_are_asked_in_batches(
    tmp_path: Path, fake_engine: Path
) -> None:
    from entune.dictionary import entries
    from entune.processing import jev_client
    from entune.processing.pipeline import process_text

    laya = Laya(tmp_path / "models", find_engine=lambda: fake_engine)
    laya.start()
    try:
        wait_until(lambda: laya.status()[0] == "ready")
        with closing(jev_client.Client()) as client:
            result = process_text(
                " ".join(["Do it."] * 70),
                entries.Dictionary().effective("s/m"),
                contextual=False,
                formatting=True,
                key=None,
                client=client,
                policy=jev_client.Policy(),
                endpoint=laya.endpoint(),
            )
        stage = result.formatting
        assert stage.status == "succeeded" and stage.decisions == 70 and stage.attempts == 1
        lines = (tmp_path / "models" / "laya" / "seen.json.requests").read_text().splitlines()
        assert [len(json.loads(line)["questions"]) for line in lines[1:]] == [64, 6]
    finally:
        laya.stop()
