"""Exercise helper memory lifetimes without loading a GPU model in the test suite."""

import io
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from entune.providers.local import parakeet_helper as helper


def heard(text: str, seconds: float = 1.0) -> helper.Heard:
    return helper.Heard(text, [[text, 0.1, 0.4, 0.97]], [], [], [], seconds)


def test_helper_caps_and_clears_gpu_cache_on_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mx = Mock()
    model = SimpleNamespace(vocabulary=["\u2581a"])
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=mx))
    monkeypatch.setitem(sys.modules, "mlx.core", mx)
    monkeypatch.setitem(
        sys.modules, "parakeet_mlx", SimpleNamespace(from_pretrained=Mock(return_value=model))
    )
    monkeypatch.setattr(
        helper, "transcribe", Mock(side_effect=[heard("hello"), RuntimeError("failed")])
    )
    score = Mock(return_value=[[2.5]])
    monkeypatch.setattr(helper, "score", score)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"op":"load","model":"test"}\n'
            '{"op":"transcribe","path":"test.wav","keep":1}\n'
            '{"op":"transcribe","path":"test.wav","keep":2}\n'
            '{"op":"score","keep":1,"spans":[]}\n'
            '{"op":"score","keep":2,"spans":[{"start":0,"end":1,"heard":"a","candidates":["b"]}]}\n'
        ),
    )
    helper.main()
    mx.set_cache_limit.assert_called_once_with(helper.CACHE_BYTES)
    assert mx.clear_cache.call_count == 5
    assert capsys.readouterr().out.splitlines() == [
        '{"ok": true}',
        '{"text": "hello", "words": [["hello", 0.1, 0.4, 0.97]]}',
        '{"error": "RuntimeError: failed"}',
        '{"scores": [[2.5]]}',
        '{"scores": [null]}',  # never kept: it holds no span
    ]
    assert score.call_args.args[2].text == "hello"  # the transcription kept as 1


def test_only_the_newest_audio_is_kept_and_the_newest_always() -> None:
    kept = helper.Kept(seconds=600)
    for key in (1, 2, 3):
        kept.put(key, heard("piece", seconds=250))
    assert list(kept.items) == [2, 3]
    kept.put(4, heard("long", seconds=900))
    assert list(kept.items) == [4]


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
                text=" ".join(str(t.start) for t in tokens), tokens=tokens
            ),
        ),
    )
    monkeypatch.setattr(helper, "decode_wav", lambda path, rate: list(range(226)))
    chunks: list[list[int]] = []

    def recognise(model: object, audio: list[int]) -> tuple[SimpleNamespace, str, int]:
        chunks.append(audio)
        token = SimpleNamespace(id=1, text=" w", start=0, end=1, duration=1, confidence=0.5)
        return SimpleNamespace(tokens=[token]), f"encoded {audio[0]}", len(audio)

    monkeypatch.setattr(helper, "recognise", recognise)
    model = SimpleNamespace(preprocessor_config=SimpleNamespace(sample_rate=1, hop_length=1))
    heard = helper.transcribe(model, "test.wav")
    assert heard.text == "0.0 105.0 210.0" and [w[1] for w in heard.words] == [0.0, 105.0, 210.0]
    assert chunks == [list(range(120)), list(range(105, 225)), list(range(210, 226))]
    assert merge.call_count == 2 and mx.clear_cache.call_count == 3
    # Each chunk's encoder output is kept with where it starts, for the audio check.
    assert heard.chunks == [
        (0.0, "encoded 0", 120),
        (105.0, "encoded 105", 120),
        (210.0, "encoded 210", 16),
    ]


def test_tokens_become_words_with_the_lowest_confidence_of_their_letters() -> None:
    def token(text: str, start: float, confidence: float) -> SimpleNamespace:
        return SimpleNamespace(text=text, start=start, end=start + 0.1, confidence=confidence)

    tokens = [
        token(" Open", 0.0, 0.99),
        token("A", 0.1, 0.6),
        token("I", 0.2, 0.9),
        token(".", 0.3, 0.2),
        token(" Yes", 0.5, 1.0),
    ]
    # The stop, emitted late, neither lowers the confidence nor moves the end.
    assert helper.words(tokens) == [["OpenAI.", 0.0, 0.3, 0.6], ["Yes", 0.5, 0.6, 1.0]]


def token(text: str, start: float, duration: float = 0.1) -> SimpleNamespace:
    return SimpleNamespace(
        id=0, text=text, start=start, duration=duration, end=start + duration, confidence=0.9
    )


def test_a_span_is_found_by_its_words_times_and_its_text() -> None:
    tokens = [token(" Ask", 0.0), token(' "', 0.2), token("Open", 0.3), token("AI", 0.4)]
    tokens += [token(".", 0.5), token(" now", 0.8)]
    kept = helper._heard('Ask "OpenAI." now', tokens, [], 1.0)
    assert kept.words[1] == ['"OpenAI.', 0.2, 0.5, 0.9]
    assert helper.span_tokens(kept, 0.2, 0.5, "OpenAI") == (2, 4)  # not the quote or stop
    assert helper.span_tokens(kept, 0.0, 0.5, 'Ask "OpenAI') == (0, 4)
    assert helper.span_tokens(kept, 0.3, 0.5, "OpenAI") is None  # no word starts there
    assert helper.span_tokens(kept, 0.2, 0.5, "Open AI") is None  # not what it wrote


def test_a_span_before_a_long_pause_is_scored_in_the_chunk_that_holds_it() -> None:
    # 260 s in chunks of 120 s every 105 s; a word at 200 s, the next after a pause at
    # 240 s: the second chunk (105-225 s) holds the word, and its window ends with it.
    tokens = [token(" Ask", 100.0), token(" Jeff", 200.0), token(" now", 240.0)]
    chunks = [(0.0, None, 1500), (105.0, None, 1500), (210.0, None, 625)]
    kept = helper._heard("Ask Jeff now", tokens, chunks, 260.0)
    assert [helper.window(kept, k, k + 1, 0.08)[2] for k in range(3)] == [0, 1, 2]
    # A span no chunk holds whole is not scored.
    assert helper.window(kept, 0, 2, 0.08)[2] is None


def test_the_forward_sum_is_the_sum_over_every_alignment() -> None:
    rng = np.random.default_rng(0)
    durations = np.array([0, 1, 2])
    rows, frames = 4, 3
    emit = np.log(rng.uniform(0.05, 1, (rows, frames)))
    still = np.log(rng.uniform(0.05, 1, (rows, frames)))
    moves = np.log(rng.dirichlet(np.ones(3), (rows, frames)))
    emit[1, 0] = helper.FLOOR  # a cell outside a row's band

    def paths(hyp: list[int], t: int, u: int) -> float:
        """Every path from state u at frame t, by brute force."""
        if t >= frames:
            return 1.0 if u == len(hyp) - 1 else 0.0
        r, total = hyp[u], 0.0
        for k, d in enumerate(durations):
            if u < len(hyp) - 1 and emit[r, t] > helper.FLOOR:
                total += np.exp(emit[r, t] + moves[r, t, k]) * paths(hyp, t + d, u + 1)
            if d > 0:
                total += np.exp(still[r, t] + moves[r, t, k]) * paths(hyp, t + d, u)
        return float(total)

    hypotheses = [[0, 1, 3], [0, 2], [2, 1, 0, 3]]
    sums = helper.forward(hypotheses, emit, still, moves, durations)
    assert sums == pytest.approx([np.log(paths(h, 0, 0)) for h in hypotheses], abs=1e-9)


def test_a_candidate_is_tried_as_the_model_writes_it_and_in_its_fewest_other_pieces() -> None:
    tokenizer = helper.Tokenizer(["<unk>", "\u2581J", "ev", "\u2581", "J", "e", "v", "\u2581Jev"])
    assert tokenizer.bpe("Jev") == [7]
    assert tokenizer.segmentations("Jev") == [[7], [1, 2]]
