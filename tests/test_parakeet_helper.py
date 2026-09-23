"""Exercise helper memory lifetimes without loading a GPU model in the test suite."""

import io
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from entune.providers.local import parakeet_helper as helper


def test_helper_caps_and_clears_gpu_cache_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mx = Mock()
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(sys.modules, "parakeet_mlx", SimpleNamespace(from_pretrained=Mock()))
    monkeypatch.setattr(helper, "transcribe", Mock(side_effect=["hello", RuntimeError("failed")]))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"op":"load","model":"test"}\n'
            '{"op":"transcribe","path":"test.wav"}\n'
            '{"op":"transcribe","path":"test.wav"}\n'
        ),
    )
    helper.main()
    mx.set_cache_limit.assert_called_once_with(helper.CACHE_BYTES)
    assert mx.clear_cache.call_count == 3
    assert capsys.readouterr().out.splitlines() == [
        '{"ok": true}',
        '{"text": "hello"}',
        '{"error": "RuntimeError: failed"}',
    ]


def test_long_audio_uses_bounded_overlapping_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    mx = SimpleNamespace(array=lambda audio: audio, clear_cache=Mock())
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(
        sys.modules, "parakeet_mlx.audio", SimpleNamespace(get_logmel=lambda audio, config: audio)
    )
    merge = Mock(side_effect=lambda previous, current, **kw: previous + current)
    monkeypatch.setitem(
        sys.modules,
        "parakeet_mlx.alignment",
        SimpleNamespace(
            merge_longest_contiguous=merge,
            merge_longest_common_subsequence=Mock(),
            tokens_to_sentences=lambda tokens: tokens,
            sentences_to_result=lambda tokens: SimpleNamespace(
                text=" ".join(str(t.start) for t in tokens)
            ),
        ),
    )
    monkeypatch.setattr(helper, "decode_wav", lambda path, rate: list(range(226)))
    chunks: list[list[int]] = []

    def generate(audio: list[int]) -> list[SimpleNamespace]:
        chunks.append(audio)
        return [SimpleNamespace(tokens=[SimpleNamespace(start=0, end=1, duration=1)])]

    model = SimpleNamespace(
        preprocessor_config=SimpleNamespace(sample_rate=1, hop_length=1), generate=generate
    )
    assert helper.transcribe(model, "test.wav") == "0.0 105.0 210.0"
    assert chunks == [list(range(120)), list(range(105, 225)), list(range(210, 226))]
    assert merge.call_count == 2 and mx.clear_cache.call_count == 3
