"""Deleting all local data: every Entune item goes, nothing else does, and the app works."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from entune.llm import LearningText
from entune.providers.contracts import Clip, TranscribeResult, Transcript
from entune.providers.local.contracts import LocalModelStatus
from entune.recorder import wav_bytes
from entune.server import RESET_PHRASE, create_app
from entune.service import Entune
from entune.store import Store
from tests.conftest import WEBM_HEADER


class Stub:
    id, name, models = "stub", "Stub", ("good",)

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        return Transcript("hello there")


class LocalStub:
    """A downloadable model kept as one file in the models folder."""

    id, name, models = "local", "Local", ("tiny",)

    def __init__(self, models_dir: Path) -> None:
        self.file = models_dir / "tiny.bin"
        self.downloading = False
        self.unloaded = 0

    def catalogue(self) -> list[LocalModelStatus]:
        state = "downloading" if self.downloading else "ready" if self.file.exists() else "absent"
        return [LocalModelStatus("tiny", "Tiny", 4, "", state, 0.0, None, self.id)]

    def download(self, name: str) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_bytes(b"tiny")

    def remove(self, name: str) -> None:
        self.file.unlink(missing_ok=True)

    def warm(self, name: str) -> None:
        pass

    def unload(self, keep: str | None = None) -> None:
        self.unloaded += 1

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        return Transcript("local words")


def fill(client: TestClient, data: Path, local: LocalStub) -> None:
    client.put("/api/settings", json={"keys": {"stub": "secret-key"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    assert client.get(f"/api/recordings/{rec['id']}/audio").status_code == 200
    client.put("/api/dictionary", json={"pinned": []})
    client.post(
        "/api/dictionary/corrections",
        json={"entries": [{"spelling": "Entune", "heard": ["in tune"]}], "source": "test"},
    )
    audio = wav_bytes(b"\0\1" * 1600, 1600)
    client.post("/api/dictionary/audio", files={"audio": ("a.wav", audio, "audio/wav")})
    local.download("tiny")
    (data / "backups" / "old").mkdir(parents=True)
    (data / "backups" / "old" / "entune.db").write_bytes(b"copy")
    (data / "dictionary.pre-v2-0123456789ab.json").write_text("{}")
    (data / "entune.log").write_text("dictated words in a log line\n")


@pytest.fixture
def setup(tmp_path: Path) -> tuple[TestClient, Entune, Path, LocalStub]:
    data = tmp_path / "entune"
    local = LocalStub(data / "models")
    app = Entune(Store(data), [Stub(), local])
    return TestClient(create_app(app), base_url="http://localhost"), app, data, local


def test_reset_deletes_every_entune_item_and_nothing_else(
    tmp_path: Path, setup: tuple[TestClient, Entune, Path, LocalStub]
) -> None:
    client, _, data, local = setup
    fill(client, data, local)
    outside = tmp_path / "original.wav"
    outside.write_bytes(b"external original")
    (data / "notes.txt").write_text("not Entune's")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "keep.txt").write_text("linked, not owned")
    (data / "dictionary-audio" / "link").symlink_to(shared)

    inventory = client.get("/api/data").json()
    assert inventory["folder"] == str(data)
    names = {item["name"] for item in inventory["items"]}
    assert {"entune.db", "audio", "dictionary-audio", "dictionary.json", "models", "backups",
            "entune.log", "dictionary.pre-v2-0123456789ab.json"} <= names  # fmt: skip
    assert inventory["other"] == ["notes.txt"]

    assert client.post("/api/data/reset", json={"confirm": "yes"}).status_code == 400
    assert client.post("/api/data/reset").status_code == 400
    assert len(client.get("/api/recordings").json()) == 1  # nothing deleted

    res = client.post("/api/data/reset", json={"confirm": RESET_PHRASE})
    assert res.status_code == 200, res.text
    assert res.json()["kept"] == ["notes.txt"]
    assert "models" in res.json()["deleted"]

    # Gone: rows, audio, imported audio, dictionary and copies, models, backups, log text.
    assert client.get("/api/recordings").json() == []
    assert client.get("/api/dictionary").json()["pinned"] == []
    assert client.get("/api/dictionary/corrections").json() == []
    assert client.get("/api/dictionary/audio").json()["items"] == []
    settings = client.get("/api/settings").json()
    assert settings["providers"][0]["keyHint"] is None and settings["defaultModel"] is None
    assert sorted(p.name for p in data.iterdir()) == [
        "audio", "entune.db", "entune.db-shm", "entune.db-wal", "entune.log", "notes.txt",
    ]  # fmt: skip
    assert list((data / "audio").iterdir()) == []
    assert (data / "entune.log").read_bytes() == b""
    assert local.catalogue()[0].state == "absent" and local.unloaded
    # Kept: the folder's other file, originals outside it, and what a link pointed to.
    assert (data / "notes.txt").read_text() == "not Entune's"
    assert outside.read_bytes() == b"external original"
    assert (shared / "keep.txt").read_text() == "linked, not owned"

    # Usable at once: keys, recording, transcription, dictionary edits.
    client.put("/api/settings", json={"keys": {"stub": "k2"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    assert rec["transcriptions"][0]["text"] == "hello there"
    assert client.get(f"/api/recordings/{rec['id']}/audio").status_code == 200
    assert client.put("/api/dictionary", json={"pinned": []}).status_code == 200


def test_reset_waits_for_nothing_it_would_break(
    setup: tuple[TestClient, Entune, Path, LocalStub],
) -> None:
    client, app, data, local = setup
    fill(client, data, local)
    confirm = {"confirm": RESET_PHRASE}

    operation = app.operations.begin("dictation", "recording")
    res = client.post("/api/data/reset", json=confirm)
    assert res.status_code == 409 and "dictation" in res.text
    app.operations.finish(operation)

    local.downloading = True
    res = client.post("/api/data/reset", json=confirm)
    assert res.status_code == 409 and "downloading" in res.text
    local.downloading = False

    # Refusals changed nothing.
    assert len(client.get("/api/recordings").json()) == 1
    assert local.file.exists() and (data / "backups").exists()
    assert client.post("/api/data/reset", json=confirm).status_code == 200


def test_a_linked_models_folder_is_unlinked_not_emptied(
    tmp_path: Path, setup: tuple[TestClient, Entune, Path, LocalStub]
) -> None:
    client, _, data, local = setup
    shared = tmp_path / "shared-models"
    shared.mkdir()
    (shared / "tiny.bin").write_bytes(b"someone else's model")
    (data / "models").symlink_to(shared)
    assert local.catalogue()[0].state == "ready"

    assert client.post("/api/data/reset", json={"confirm": RESET_PHRASE}).status_code == 200
    assert not (data / "models").exists() and local.unloaded
    assert (shared / "tiny.bin").read_bytes() == b"someone else's model"


def test_a_refused_reset_keeps_a_stopped_run_for_retry(
    setup: tuple[TestClient, Entune, Path, LocalStub],
) -> None:
    client, app, _, local = setup
    builds = app._builds
    builds._state = {"id": "job", "phase": "failed"}
    builds._texts["1"] = LearningText("1", "kept for retry", "temporary_audio")
    local.downloading = True
    assert client.post("/api/data/reset", json={"confirm": RESET_PHRASE}).status_code == 409
    assert builds._texts and builds._state["phase"] == "failed"

    local.downloading = False
    assert client.post("/api/data/reset", json={"confirm": RESET_PHRASE}).status_code == 200
    assert not builds._texts and builds._state == {"phase": "idle"}


def test_a_failed_deletion_leaves_the_app_usable(
    setup: tuple[TestClient, Entune, Path, LocalStub],
) -> None:
    client, _, data, local = setup
    fill(client, data, local)
    locked = data / "backups" / "old"
    locked.chmod(0o500)  # its file cannot be removed
    try:
        res = client.post("/api/data/reset", json={"confirm": RESET_PHRASE})
        assert res.status_code == 500 and "Could not delete everything" in res.text
        assert client.get("/api/settings").status_code == 200
        rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")})
        assert rec.status_code == 200
    finally:
        locked.chmod(0o700)
    assert client.post("/api/data/reset", json={"confirm": RESET_PHRASE}).status_code == 200
    assert not (data / "backups").exists()


def test_imports_exports_and_a_reset_never_overlap(
    setup: tuple[TestClient, Entune, Path, LocalStub],
) -> None:
    client, app, _, _ = setup
    confirm = {"confirm": RESET_PHRASE}
    for what in ("audio import", "export"):
        with app.using_data(what):
            res = client.post("/api/data/reset", json=confirm)
            assert res.status_code == 409 and what in res.text
    app._resetting = True  # as while a reset runs
    audio = wav_bytes(b"\0\1" * 1600, 1600)
    res = client.post("/api/dictionary/audio", files={"audio": ("a.wav", audio, "audio/wav")})
    assert res.status_code == 400 and "deleting all data" in res.text
    assert client.get("/api/exports/audio").status_code == 409
    app._resetting = False
    assert client.get("/api/dictionary/audio").json()["items"] == []
    assert client.post("/api/data/reset", json=confirm).status_code == 200


def test_an_unreadable_folder_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, setup: tuple[TestClient, Entune, Path, LocalStub]
) -> None:
    client, app, data, local = setup
    fill(client, data, local)

    def unreadable() -> tuple[list[Path], list[Path]]:
        raise PermissionError("folder not readable")

    monkeypatch.setattr(app.store, "managed", unreadable)
    res = client.post("/api/data/reset", json={"confirm": RESET_PHRASE})
    assert res.status_code == 500 and "folder not readable" in res.text
    assert len(client.get("/api/recordings").json()) == 1  # still open, nothing deleted
