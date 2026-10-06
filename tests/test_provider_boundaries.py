from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from entune.audio import convert


def test_contracts_and_parakeet_do_not_load_unrelated_engines() -> None:
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
import entune.providers.contracts
import entune.providers.cloud.contracts
import entune.providers.local.contracts
assert 'httpx' not in sys.modules
import entune.providers.local.parakeet
assert 'entune.providers.local.whisper' not in sys.modules
import entune.providers.registry
assert not {'numpy', 'pywhispercpp', 'parakeet_mlx', 'mlx'} & sys.modules.keys()
""",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_shared_conversion_uses_explicit_rate_and_cleans_up_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[Path] = []
    monkeypatch.setattr("entune.audio.convert.shutil.which", lambda _: "/test/ffmpeg")

    def run(
        command: list[str], *, capture_output: bool, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        source = Path(command[command.index("-i") + 1])
        paths.append(source)
        assert source.read_bytes() == b"encoded audio"
        assert command[command.index("-ar") + 1] == "16000"
        assert capture_output
        assert timeout == 300
        return subprocess.CompletedProcess(command, 183, b"", b"Invalid data found\n")

    monkeypatch.setattr("entune.audio.convert.subprocess.run", run)
    with pytest.raises(RuntimeError, match=r"ffmpeg could not read the audio: Invalid data found$"):
        convert.to_wav_with_ffmpeg(b"encoded audio", sample_rate=16_000)
    assert len(paths) == 1 and not paths[0].exists()


def test_ffmpeg_is_found_in_homebrew_when_the_dock_gives_no_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from entune.audio import convert

    (tmp_path / "ffmpeg").write_text("")
    monkeypatch.setattr("entune.audio.convert.shutil.which", lambda _: None)
    monkeypatch.setattr("entune.audio.convert.FFMPEG_DIRS", (str(tmp_path),))
    used: list[str] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        used.append(command[0])
        return subprocess.CompletedProcess(command, 0, b"RIFF", b"")

    monkeypatch.setattr("entune.audio.convert.subprocess.run", run)
    assert convert.to_wav_with_ffmpeg(b"m4a", sample_rate=16_000) == b"RIFF"
    assert used == [f"{tmp_path}/ffmpeg"]


@pytest.mark.skipif(sys.platform != "darwin", reason="afconvert is part of macOS")
def test_a_mac_without_ffmpeg_decodes_m4a_with_afconvert(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from entune.audio import convert
    from entune.audio.formats import wav_bytes

    source, m4a = tmp_path / "clip.wav", tmp_path / "clip.m4a"
    source.write_bytes(wav_bytes(bytes(3200), 16_000))
    subprocess.run(
        [convert.AFCONVERT, "-f", "m4af", "-d", "aac", str(source), str(m4a)],
        check=True,
        timeout=300,
    )
    monkeypatch.setattr("entune.audio.convert.shutil.which", lambda _: None)
    monkeypatch.setattr("entune.audio.convert.FFMPEG_DIRS", ())
    decoded = convert.to_wav_with_ffmpeg(m4a.read_bytes(), sample_rate=16_000)
    assert decoded[:4] == b"RIFF" and decoded[8:12] == b"WAVE"
