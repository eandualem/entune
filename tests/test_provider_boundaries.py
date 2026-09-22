from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dictum.providers.local import audio


def test_contracts_and_parakeet_do_not_load_unrelated_engines() -> None:
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import sys
import dictum.providers.contracts
import dictum.providers.cloud.contracts
import dictum.providers.local.contracts
assert 'httpx' not in sys.modules
import dictum.providers.local.parakeet
assert 'dictum.providers.local.whisper' not in sys.modules
import dictum.providers.registry
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
    monkeypatch.setattr("dictum.providers.local.audio.shutil.which", lambda _: "/test/ffmpeg")

    def convert(
        command: list[str], *, capture_output: bool, check: bool, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        source = Path(command[command.index("-i") + 1])
        paths.append(source)
        assert source.read_bytes() == b"encoded audio"
        assert command[command.index("-ar") + 1] == "16000"
        assert capture_output and check
        assert timeout == 300
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("dictum.providers.local.audio.subprocess.run", convert)
    with pytest.raises(subprocess.CalledProcessError):
        audio.to_wav_with_ffmpeg(b"encoded audio", sample_rate=16_000)
    assert len(paths) == 1 and not paths[0].exists()
