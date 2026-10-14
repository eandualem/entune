"""Runs inside the parakeet-mlx tool environment, not inside Entune: one JSON request
per line on stdin, one JSON answer per line on stdout. The model is loaded once.

Kept free of Entune imports on purpose; Entune starts it with the engine's own
Python (`uv tool install parakeet-mlx`), which has MLX and the model code.

The audio is decoded here from the WAV Entune writes, not by the engine's own
loader, which shells out to ffmpeg: a Dock-launched app has no PATH to find one,
and a user should not need one. The engine's resampler (soxr, via librosa) and
its mel front end are used directly.

The audio check. A transcription asked with "keep" is kept, its encoder output and
tokens, for the newest KEEP_SECONDS of audio. {"op": "score"} then says, for each span
of it, how much likelier the audio is with the span's text replaced by each candidate:
log P(audio | candidate) - log P(audio | the text heard), each a TDT forward sum over
the span's audio and CONTEXT_SECONDS on each side. The text heard is scored with the
model's own tokens, a candidate with the best of its tokenisations. The encoder output
is reused; the decoder's state before the span is the one the greedy decode had, so a
candidate differs from the text heard only from the span on, and only the lattice cells
near where each token was heard are computed (BAND, MARGIN).
"""

from __future__ import annotations

import json
import math
import sys
import wave
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

CHUNK_SECONDS = 120  # parakeet-mlx's file-transcription defaults
OVERLAP_SECONDS = 15
CACHE_BYTES = 64 * 1024 * 1024

# The audio check, as measured on held-out dictations in October 2026.
KEEP_SECONDS = 600.0  # audio of the newest transcriptions kept for it; the newest always
CONTEXT_SECONDS = 1.5  # audio scored on each side of a span
BAND = 3  # frames a token around the span may move from where it was heard
MARGIN = 4  # frames the span's own tokens may reach beyond it
OWN = 2  # tokens after the span decoded from the candidate's own state; later, the heard's
SEGMENTATIONS = 2  # tokenisations of a candidate: the model's own, then the next fewest pieces
CELLS = 8192  # joint-network cells computed at once
FLOOR = -1e4  # the log-probability of a lattice cell no path may use
TIME_TOLERANCE = 5e-4  # word times are rounded to milliseconds

Floats = npt.NDArray[np.float64]


def decode_wav(path: str, target_rate: int):  # type: ignore[no-untyped-def]
    with wave.open(path, "rb") as clip:
        channels, width, rate = clip.getnchannels(), clip.getsampwidth(), clip.getframerate()
        frames = clip.readframes(clip.getnframes())
    if width != 2:
        raise ValueError(f"{width * 8}-bit WAV; only 16-bit is supported")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        import librosa

        samples = librosa.resample(samples, orig_sr=rate, target_sr=target_rate)
    return samples.astype(np.float32)


def groups(tokens: list[Any]) -> list[list[int]]:
    """The tokens' indices grouped into words: a word starts at a token beginning with a
    space; a group writing nothing is left out."""
    grouped: list[list[int]] = []
    for i, token in enumerate(tokens):
        if not grouped or token.text.startswith(" "):
            grouped.append([i])
        else:
            grouped[-1].append(i)
    return [g for g in grouped if "".join(tokens[i].text for i in g).strip()]


def words(tokens: list[Any]) -> list[list[object]]:
    """The tokens grouped into words, `[text, start, end, confidence]` each."""
    return [list(word(tokens, group)) for group in groups(tokens)]


def word(tokens: list[Any], group: list[int]) -> tuple[str, float, float, float]:
    """A word's text, start, end and confidence. Its end and confidence come from its
    tokens that hold a letter or digit, so a stop or comma after it, emitted late, does
    not count."""
    held = [tokens[i] for i in group]
    text = "".join(token.text for token in held).strip()
    spoken = [token for token in held if any(c.isalnum() for c in token.text)] or held
    confidence = min(float(token.confidence) for token in spoken)
    return text, round(held[0].start, 3), round(spoken[-1].end, 3), round(confidence, 4)


@dataclass
class Heard:
    """One transcription: its text and words as answered and, for the audio check, its
    tokens with each word's (first token, start, end, end token), and per chunk of audio
    (start in seconds, frames) the encoder output as the joint network takes it. The
    decoder's states are made when it is first scored."""

    text: str
    words: list[list[object]]
    tokens: list[Any]  # the engine's AlignedToken: id, text, start, duration
    spans: list[tuple[int, float, float, int]]
    chunks: list[tuple[float, Any, int]]
    seconds: float
    states: Any = None


class Kept:
    """The newest transcriptions asked to be kept, up to KEEP_SECONDS of audio."""

    def __init__(self, seconds: float = KEEP_SECONDS) -> None:
        self.seconds = seconds
        self.items: OrderedDict[object, Heard] = OrderedDict()

    def put(self, key: object, heard: Heard) -> None:
        self.items[key] = heard
        while len(self.items) > 1 and sum(h.seconds for h in self.items.values()) > self.seconds:
            self.items.popitem(last=False)

    def get(self, key: object) -> Heard | None:
        return self.items.get(key)


def recognise(model: Any, mel: Any) -> tuple[Any, Any, int]:
    """What `model.generate` does for one chunk, keeping the encoder output as the joint
    network takes it: the result, that output and its frames."""
    import mlx.core as mx
    from parakeet_mlx.alignment import sentences_to_result, tokens_to_sentences

    if mel.ndim == 2:
        mel = mx.expand_dims(mel, 0)
    features, lengths = model.encoder(mel)
    mx.eval(features, lengths)
    hypotheses, _ = model.decode(features, lengths)
    frames = int(lengths[0])
    encoded = model.joint.enc(features[0, :frames])
    mx.eval(encoded)
    return sentences_to_result(tokens_to_sentences(hypotheses[0])), encoded, frames


def transcribe(model: Any, path: str) -> Heard:
    import mlx.core as mx
    from parakeet_mlx.alignment import (
        merge_longest_common_subsequence,
        merge_longest_contiguous,
        sentences_to_result,
        tokens_to_sentences,
    )
    from parakeet_mlx.audio import get_logmel

    config = model.preprocessor_config
    audio = decode_wav(path, config.sample_rate)
    seconds = len(audio) / config.sample_rate
    chunk_samples = CHUNK_SECONDS * config.sample_rate
    step = (CHUNK_SECONDS - OVERLAP_SECONDS) * config.sample_rate
    tokens: list[Any] = []
    chunks: list[tuple[float, Any, int]] = []
    # Keep our WAV decoder (no ffmpeg dependency), but use the engine's overlapping
    # file-chunk strategy and token merger so attention cannot grow with file length.
    for start in range(0, len(audio), step):
        end = min(start + chunk_samples, len(audio))
        if end - start < config.hop_length:
            break
        mel = get_logmel(mx.array(audio[start:end]), config)
        result, encoded, frames = recognise(model, mel)
        chunks.append((start / config.sample_rate, encoded, frames))
        del mel
        mx.clear_cache()
        if start == 0 and end == len(audio):
            return _heard(str(result.text), result.tokens, chunks, seconds)
        for token in result.tokens:
            token.start += start / config.sample_rate
            token.end = token.start + token.duration
        if tokens:
            try:
                tokens = merge_longest_contiguous(
                    tokens, result.tokens, overlap_duration=OVERLAP_SECONDS
                )
            except RuntimeError:
                tokens = merge_longest_common_subsequence(
                    tokens, result.tokens, overlap_duration=OVERLAP_SECONDS
                )
        else:
            tokens = result.tokens
        if end == len(audio):
            break
    whole = sentences_to_result(tokens_to_sentences(tokens))
    return _heard(str(whole.text), whole.tokens, chunks, seconds)


def _heard(
    text: str, tokens: list[Any], chunks: list[tuple[float, Any, int]], seconds: float
) -> Heard:
    found = [(group, word(tokens, group)) for group in groups(tokens)]
    return Heard(
        text,
        [list(w) for _, w in found],
        list(tokens),
        [(group[0], w[1], w[2], group[-1] + 1) for group, w in found],
        chunks,
        seconds,
    )


# ---- the audio check


class Tokenizer:
    """Text into the model's pieces: as its SentencePiece BPE does it, and in the fewest."""

    def __init__(self, vocabulary: list[str]) -> None:
        self.piece = {p: i for i, p in enumerate(vocabulary)}
        self.longest = max(len(p) for p in vocabulary)
        self.unknown = self.piece.get("<unk>", 0)
        self._memo: dict[str, list[list[int]]] = {}

    def bpe(self, word: str) -> list[int]:
        """Merge the adjacent pair whose merged piece ranks first, until none merges."""
        s = list("▁" + word)
        while True:
            best: tuple[int, int] | None = None
            for i in range(len(s) - 1):
                rank = self.piece.get(s[i] + s[i + 1])
                if rank is not None and (best is None or rank < best[0]):
                    best = (rank, i)
            if best is None:
                break
            i = best[1]
            s[i : i + 2] = [s[i] + s[i + 1]]
        return [self.piece.get(p, self.unknown) for p in s]

    def segmentations(self, text: str) -> list[list[int]]:
        """Up to SEGMENTATIONS tokenisations of the text: each word as the model's BPE
        writes it, then one word at a time in its fewest pieces instead."""
        found = self._memo.get(text)
        if found is None:
            per_word = [self._ways(w) for w in text.split()]
            base = [ways[0] for ways in per_word]
            found = [[t for pieces in base for t in pieces]]
            for n, ways in enumerate(per_word):
                for other in ways[1:]:
                    seq = [
                        t for k, pieces in enumerate(base) for t in (other if k == n else pieces)
                    ]
                    if seq not in found:
                        found.append(seq)
            found = found[:SEGMENTATIONS]
            if len(self._memo) > 20_000:
                self._memo.clear()
            self._memo[text] = found
        return found

    def _ways(self, word: str) -> list[list[int]]:
        """The word's BPE, then its segmentations into the fewest pieces (one more allowed)."""
        s = "▁" + word
        n = len(s)
        fewest = [math.inf] * (n + 1)
        fewest[0] = 0
        for i in range(n):
            if fewest[i] == math.inf:
                continue
            fits = [k for k in range(1, min(self.longest, n - i) + 1) if s[i : i + k] in self.piece]
            for k in fits:
                fewest[i + k] = min(fewest[i + k], fewest[i] + 1)
            if not fits and fewest[i + 1] == math.inf:
                fewest[i + 1] = fewest[i] + 2  # a character the vocabulary lacks
        cap = fewest[n] + 1
        found: list[list[int]] = []

        def walk(i: int, pieces: list[int]) -> None:
            if len(found) >= 12:
                return
            if i == n:
                found.append(list(pieces))
                return
            if len(pieces) + 1 > cap:
                return
            any_piece = False
            for k in range(min(self.longest, n - i), 0, -1):
                if s[i : i + k] in self.piece:
                    any_piece = True
                    walk(i + k, [*pieces, self.piece[s[i : i + k]]])
            if not any_piece:
                walk(i + 1, [*pieces, self.unknown])

        walk(0, [])
        found.sort(key=len)
        own = self.bpe(word)
        return [own, *[f for f in found if f != own][: SEGMENTATIONS - 1]]


def span_tokens(heard: Heard, start: float, end: float, text: str) -> tuple[int, int] | None:
    """The tokens a..b-1 that write `text`, in the words from the one starting at `start`
    to the one ending at `end`; None when there are none."""
    texts = [t.text for t in heard.tokens]
    wanted = text.strip()
    for i, (first, began, _, _) in enumerate(heard.spans):
        if abs(began - start) > TIME_TOLERANCE:
            continue
        for _, _, ended, last in heard.spans[i:]:
            if abs(ended - end) > TIME_TOLERANCE:
                continue
            for a in range(first, last):
                for b in range(last, a, -1):
                    if "".join(texts[a:b]).strip() == wanted:
                        return a, b
    return None


def score(model: Any, tokenizer: Tokenizer, heard: Heard, spans: list[Any]) -> list[Any]:
    """For each span, each candidate's change in the audio's log-probability (the best of
    its tokenisations); None for a span whose words or audio cannot be found."""
    out: list[Any] = [None] * len(spans)
    jobs: list[tuple[int, int, list[list[int]]]] = []
    owners: list[tuple[int, list[int], int]] = []
    for n, span in enumerate(spans):
        found = span_tokens(heard, float(span["start"]), float(span["end"]), str(span["heard"]))
        if found is None:
            continue
        candidates = [str(c) for c in span["candidates"]]
        if not candidates:
            out[n] = []
            continue
        seqs: list[list[int]] = []
        owner: list[int] = []
        for c, text in enumerate(candidates):
            for seq in [s for s in tokenizer.segmentations(text) if s] or [[tokenizer.unknown]]:
                seqs.append(seq)
                owner.append(c)
        jobs.append((found[0], found[1], seqs))
        owners.append((n, owner, len(candidates)))
    for (n, owner, count), result in zip(owners, lattice_scores(model, heard, jobs), strict=True):
        if result is None or result[0] < FLOOR / 2:
            continue  # no audio, or no path, for its window: it cannot be scored
        heard_score, scores = result
        best = [FLOOR] * count
        for c, value in zip(owner, scores, strict=True):
            best[c] = max(best[c], value - heard_score)
        out[n] = [round(max(d, FLOOR), 3) for d in best]
    return out


def decoder_states(model: Any, heard: Heard) -> Any:
    """The decoder's output before each token (and at the end), and each LSTM layer's
    state after each, as the greedy decode had them."""
    import mlx.core as mx

    if heard.states is None:
        embed = model.decoder.prediction["embed"]
        x = mx.zeros((1, 1, model.decoder.pred_hidden), dtype=embed.weight.dtype)
        ids = [int(t.id) for t in heard.tokens]
        if ids:
            x = mx.concatenate([x, embed(mx.array([ids]))], axis=1)
        out, layers = run_lstm(model, x)
        mx.eval(out, layers)
        heard.states = (out[0], [(h[0], c[0]) for h, c in layers])
    return heard.states


def run_lstm(model: Any, x: Any, hidden: Any = None, cell: Any = None) -> tuple[Any, Any]:
    """The decoder's LSTM over embedded inputs [batch, steps, size]: its outputs and each
    layer's states after every step."""
    layers = []
    out = x
    for i, layer in enumerate(model.decoder.prediction["dec_rnn"].lstm):
        h, c = layer(
            out,
            hidden=None if hidden is None else hidden[i],
            cell=None if cell is None else cell[i],
        )
        layers.append((h, c))
        out = h
    return out, layers


def window(heard: Heard, a: int, b: int, ratio: float) -> tuple[int, int, int | None]:
    """The tokens p..q-1 around the span a..b-1, CONTEXT_SECONDS on each side, and the
    chunk of audio holding them; narrowed, one token on each side at a time, until one
    chunk does. The window ends where token q starts, or where its chunk ends when q is
    past it (after a pause). None when no chunk holds even the span."""
    tokens = heard.tokens
    count = len(tokens)
    span_start = tokens[a].start
    span_end = tokens[b - 1].start + tokens[b - 1].duration
    p = a
    while p > 0 and tokens[p - 1].start >= span_start - CONTEXT_SECONDS:
        p -= 1
    q = b
    while q < count and tokens[q].start <= span_end + CONTEXT_SECONDS:
        q += 1

    def holding(p: int, q: int) -> int | None:
        last = tokens[q - 1].start + tokens[q - 1].duration
        for n, (offset, _, frames) in enumerate(heard.chunks):
            if tokens[p].start >= offset and last <= offset + frames * ratio:
                return n
        return None

    chunk = holding(p, q)
    while chunk is None and (p < a or q > b):
        p, q = min(a, p + 1), max(b, q - 1)
        chunk = holding(p, q)
    return p, q, chunk


Row = tuple[object, int, int, int]  # decoder state, target token, first and end frame


class _Plan:
    """One span's lattice: its rows (a decoder state and the token it should emit, on the
    frames it may), the heard text's rows in order and each candidate's."""

    def __init__(self, frames: int, first: int) -> None:
        self.rows: list[Row] = []
        self._index: dict[tuple[object, int], int] = {}
        self.heard: list[int] = []
        self.candidates: list[list[int]] = []
        self.frames = frames  # in the window
        self.first = first  # the window's first frame in all the audio's

    def row(self, state: object, target: int, lo: int, hi: int) -> int:
        """A row, shared by every hypothesis that has it."""
        key = (state, target)
        if key not in self._index:
            self._index[key] = len(self.rows)
            self.rows.append((state, target, lo, hi))
        return self._index[key]


def lattice_scores(
    model: Any, heard: Heard, jobs: list[tuple[int, int, list[list[int]]]]
) -> list[tuple[float, list[float]] | None]:
    """For each job (span tokens a..b-1, candidate token lists): log P(window | the heard
    tokens) and log P(window | the span's tokens replaced by each candidate's), or None
    when no chunk of audio holds its window. All of a transcription's jobs are computed in
    one pass of the LSTM and of the joint network."""
    import mlx.core as mx

    if not jobs:
        return []
    tokens = heard.tokens
    count = len(tokens)
    ids = [int(t.id) for t in tokens]
    blank = len(model.vocabulary)
    ratio = float(model.time_ratio)
    durations = np.array(model.durations, dtype=np.int64)
    output, states = decoder_states(model, heard)
    starts = np.cumsum([0] + [frames for _, _, frames in heard.chunks])
    seqs: list[list[int]] = []  # each candidate's tokens and the OWN after the span
    seq_at: list[int] = []  # and the decoder state it starts from
    plans: list[_Plan | None] = []
    for a, b, candidates in jobs:
        p, q, chunk = window(heard, a, b, ratio)
        if chunk is None:
            plans.append(None)
            continue
        offset, _, chunk_frames = heard.chunks[chunk]
        at = [round((tokens[k].start - offset) / ratio) for k in range(p, q)]
        f0 = max(0, min(at[0], chunk_frames - 1))
        f1 = round((tokens[q].start - offset) / ratio) if q < count else chunk_frames
        f1 = max(f0 + 1, min(f1, chunk_frames))
        frames = f1 - f0
        heard_at = [min(max(f - f0, 0), frames - 1) for f in at]  # each token's frame
        span_lo = max(0, (heard_at[a - 1 - p] if a > p else 0) - MARGIN)
        span_hi = min(frames, (heard_at[b - p] if b < q else frames - 1) + MARGIN + 1)
        # The row emitting token k (k == q: the end) may use the frames from the token
        # before it to its own, BAND more on each side.
        bands = [
            (
                max(0, (heard_at[k - 1 - p] if k > p else 0) - BAND),
                min(frames, (heard_at[k - p] if k < q else frames - 1) + BAND + 1),
            )
            for k in range(p, q + 1)
        ]
        plan = _Plan(frames, int(starts[chunk]) + f0)
        for k in range(p, q + 1):
            target = ids[k] if k < q else blank
            if a <= k < b:
                lo, hi = span_lo, span_hi
            elif k == b:
                lo, hi = span_lo, bands[k - p][1]
            else:
                lo, hi = bands[k - p]
            plan.heard.append(plan.row(k, target, lo, hi))
        suffix = ids[b:q]
        for c in candidates:
            m = len(c)
            n = len(seqs)
            seqs.append(c + suffix[:OWN])
            seq_at.append(a)
            hyp = plan.heard[: a - p]
            hyp.append(plan.row(a, c[0], span_lo, span_hi))
            for j in range(1, m + len(suffix) + 1):
                if j - m > OWN:  # the heard text's state from here on
                    hyp.append(plan.heard[b + j - m - p])
                    continue
                target = c[j] if j < m else (suffix[j - m] if j - m < len(suffix) else blank)
                if j < m:
                    lo, hi = span_lo, span_hi
                else:
                    lo, hi = bands[b + j - m - p]
                    lo = span_lo if j == m else lo
                hyp.append(plan.row(("own", n, j - 1), target, lo, hi))
            plan.candidates.append(hyp)
        plans.append(plan)
    if not seqs:
        return [None] * len(jobs)
    # One LSTM pass over every candidate, each from the decoder's state before its span.
    width = max(len(s) for s in seqs)
    padded = np.zeros((len(seqs), width), dtype=np.int32)
    for i, s in enumerate(seqs):
        padded[i, : len(s)] = s
    where = mx.array(np.array(seq_at, dtype=np.int32))
    hidden = [mx.take(h, where, axis=0) for h, _ in states]
    cell = [mx.take(c, where, axis=0) for _, c in states]
    own, _ = run_lstm(model, model.decoder.prediction["embed"](mx.array(padded)), hidden, cell)
    table = mx.concatenate([output, own.reshape(-1, own.shape[-1]).astype(output.dtype)], axis=0)
    predicted = model.joint.pred(table)
    encoded = mx.concatenate([e for _, e, _ in heard.chunks], axis=0)
    # The cells of every row.
    sources, cells, targets = [], [], []
    for job in plans:
        if job is None:
            continue
        for state, target, lo, hi in job.rows:
            if isinstance(state, int):
                index = state
            else:
                assert isinstance(state, tuple)  # ("own", candidate, step)
                index = count + 1 + state[1] * width + state[2]
            sources.append(np.full(hi - lo, index, dtype=np.int32))
            cells.append(np.arange(job.first + lo, job.first + hi, dtype=np.int32))
            targets.append(np.full(hi - lo, target, dtype=np.int32))
    source = np.concatenate(sources)
    frame = np.concatenate(cells)
    goal = np.concatenate(targets)
    classes = blank + 1
    token_parts, blank_parts, duration_parts = [], [], []
    for i in range(0, len(source), CELLS):
        part = slice(i, i + CELLS)
        z = encoded[mx.array(frame[part])] + predicted[mx.array(source[part])]
        for layer in model.joint.joint_net[:-1]:
            z = layer(z)
        logits = model.joint.joint_net[-1](z).astype(mx.float32)
        norm = mx.logsumexp(logits[:, :classes], axis=-1)
        emit = mx.take_along_axis(logits, mx.array(goal[part])[:, None], axis=1)[:, 0] - norm
        still = logits[:, blank] - norm
        moves = logits[:, classes:]
        moves = moves - mx.logsumexp(moves, axis=-1, keepdims=True)
        mx.eval(emit, still, moves)
        token_parts.append(np.array(emit, dtype=np.float64))
        blank_parts.append(np.array(still, dtype=np.float64))
        duration_parts.append(np.array(moves, dtype=np.float64))
    emits = np.concatenate(token_parts)
    stills = np.concatenate(blank_parts)
    moving = np.concatenate(duration_parts)
    results: list[tuple[float, list[float]] | None] = []
    done = 0
    for job in plans:
        if job is None:
            results.append(None)
            continue
        rows = len(job.rows)
        token = np.full((rows, job.frames), FLOOR)
        blanks = np.full((rows, job.frames), FLOOR)
        moved = np.full((rows, job.frames, len(durations)), FLOOR)
        for r, (_, _, lo, hi) in enumerate(job.rows):
            token[r, lo:hi] = emits[done : done + hi - lo]
            blanks[r, lo:hi] = stills[done : done + hi - lo]
            moved[r, lo:hi] = moving[done : done + hi - lo]
            done += hi - lo
        sums = forward([job.heard, *job.candidates], token, blanks, moved, durations)
        results.append((float(sums[0]), [float(s) for s in sums[1:]]))
    return results


def forward(
    hypotheses: list[list[int]],
    token: Floats,
    blank: Floats,
    duration: Floats,
    durations: npt.NDArray[np.int64],
) -> Floats:
    """log P(tokens | frames) of each hypothesis under TDT, summed over its alignments.

    A hypothesis is its rows, one per state u = 0..U (U tokens emitted); row r emits its
    token at frame t with log-probability token[r, t], a blank with blank[r, t], and moves
    on durations[k] frames with duration[r, t, k] (a blank never moves 0). Frames are taken
    in order, every hypothesis at once; within a frame, the tokens emitted without moving
    are a recurrence over u, solved as a cumulative log-sum.
    """
    count = len(hypotheses)
    width = max(len(h) for h in hypotheses)
    frames = token.shape[1]
    dead = len(token)  # a row no path may use, filling shorter hypotheses
    rows = np.full((count, width), dead)
    for h, hyp in enumerate(hypotheses):
        rows[h, : len(hyp)] = hyp
    last = np.array([len(h) - 1 for h in hypotheses])
    every = np.arange(count)
    emit = np.concatenate([token, np.full((1, frames), FLOOR)])[rows]
    emit[every, last] = FLOOR  # the end state emits no token
    emit = emit.transpose(2, 0, 1).copy()  # [frame, hypothesis, state]
    still = np.concatenate([blank, np.full((1, frames), FLOOR)])[rows].transpose(2, 0, 1).copy()
    moves = np.concatenate([duration, np.full((1, frames, len(durations)), FLOOR)])[rows]
    moves = moves.transpose(2, 3, 0, 1).copy()  # [frame, duration, hypothesis, state]
    slots = int(durations.max()) + 1
    stay = [k for k, d in enumerate(durations) if d == 0]
    on = np.array([k for k, d in enumerate(durations) if d > 0])
    ahead = durations[on]
    alpha = np.full((slots, count, width), -np.inf)
    alpha[0, :, 0] = 0.0
    total = np.zeros((count, width))
    for t in range(frames):
        here = alpha[t % slots]
        if stay:
            np.cumsum(emit[t, :, :-1] + moves[t, stay[0], :, :-1], axis=1, out=total[:, 1:])
            here = total + np.logaddexp.accumulate(here - total, axis=1)
        to = (t + ahead) % slots
        move = moves[t, on]
        alpha[to] = np.logaddexp(alpha[to], here + still[t] + move)
        alpha[to, :, 1:] = np.logaddexp(
            alpha[to, :, 1:], here[:, :-1] + emit[t, :, :-1] + move[:, :, :-1]
        )
        alpha[t % slots] = -np.inf
    ends = alpha[np.arange(frames, frames + slots) % slots][:, every, last]
    result: Floats = np.logaddexp.reduce(ends, axis=0)
    return result


def main() -> None:
    model = None
    mlx: Any = None
    kept = Kept()
    tokenizer: Tokenizer | None = None
    for line in sys.stdin:
        request = json.loads(line)
        answer: dict[str, object]
        try:
            if request["op"] == "load":
                import mlx.core as mx
                from parakeet_mlx import from_pretrained

                mlx = mx
                mx.set_cache_limit(CACHE_BYTES)
                model = from_pretrained(request["model"])
                kept, tokenizer = Kept(), None
                answer = {"ok": True}
            elif request["op"] == "transcribe":
                if model is None:
                    raise RuntimeError("model not loaded")
                heard = transcribe(model, request["path"])
                if "keep" in request:
                    kept.put(request["keep"], heard)
                answer = {"text": heard.text, "words": heard.words}
            elif request["op"] == "score":
                if model is None:
                    raise RuntimeError("model not loaded")
                found = kept.get(request["keep"])
                if found is None:
                    raise LookupError("this recording's audio is no longer kept")
                tokenizer = tokenizer or Tokenizer(list(model.vocabulary))
                answer = {"scores": score(model, tokenizer, found, request["spans"])}
            else:
                answer = {"error": f"unknown op {request['op']}"}
        except Exception as exc:  # the answer carries it; the process stays up
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        finally:
            # Model weights stay loaded; temporary GPU buffers do not stay resident
            # between dictations, including when loading or inference fails.
            if mlx is not None:
                mlx.clear_cache()
        print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    main()
