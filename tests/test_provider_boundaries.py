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
