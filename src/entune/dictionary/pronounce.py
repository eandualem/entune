"""How a spelling is said, as phonemes, and how close two pronunciations are.

A word is looked up in the CMU Pronouncing Dictionary; a word it lacks, such as a name, is
predicted by a small grapheme-to-phoneme model (g2p_en's weights, run with numpy). Both are
bundled in `pronunciation/` with their licences, so nothing is downloaded. A written token is
read the way it would be said: a possessive dropped, camelCase, dots and digits split ("v2.dev"
is "v two dot dev"), short capitals read as letter names and, for two to five capitals, also as
a word ("SaaS", "NASA"). Phonemes carry no stress.

Two pronunciations are compared by a weighted edit distance: a vowel for another vowel or a
consonant for a near one (F and V, S and Z, M and N, ...) costs 0.4, dropping a vowel 0.6, any
other change 1. Similarity is 1 minus that distance over the longer length.
"""

from __future__ import annotations

import gzip
import itertools
import re
from collections.abc import Iterable, Sequence
from functools import cache, lru_cache
from importlib.resources import files

import numpy as np
import numpy.typing as npt

Phones = tuple[int, ...]

PHONES = [
    "AA",
    "AE",
    "AH",
    "AO",
    "AW",
    "AY",
    "EH",
    "ER",
    "EY",
    "IH",
    "IY",
    "OW",
    "OY",
    "UH",
    "UW",
    "B",
    "CH",
    "D",
    "DH",
    "F",
    "G",
    "HH",
    "JH",
    "K",
    "L",
    "M",
    "N",
    "NG",
    "P",
    "R",
    "S",
    "SH",
    "T",
    "TH",
    "V",
    "W",
    "Y",
    "Z",
    "ZH",
]
_ID = {p: i for i, p in enumerate(PHONES)}
_VOWELS = frozenset(_ID[p] for p in PHONES[:15])
_NEAR = (
    ("F", "V"), ("S", "Z"), ("T", "D"), ("K", "G"), ("P", "B"), ("SH", "ZH"), ("CH", "JH"),
    ("TH", "DH"), ("M", "N"), ("N", "NG"), ("L", "R"), ("W", "V"), ("S", "TH"), ("Z", "DH"),
    ("JH", "G"), ("CH", "T"), ("SH", "S"), ("Y", "IY"),
)  # fmt: skip
_SUB = np.ones((len(PHONES), len(PHONES)))
np.fill_diagonal(_SUB, 0.0)
for _a in _VOWELS:
    for _b in _VOWELS:
        if _a != _b:
            _SUB[_a, _b] = 0.4
for _p, _q in _NEAR:
    _SUB[_ID[_p], _ID[_q]] = _SUB[_ID[_q], _ID[_p]] = 0.4
_INDEL = np.array([0.6 if i in _VOWELS else 1.0 for i in range(len(PHONES))])
_INDEL[_ID["HH"]] = 0.5
LENGTH_RATIO = (0.4, 2.5)  # pronunciations further apart in length are not compared

_LETTERS = dict(
    zip(
        "abcdefghijklmnopqrstuvwxyz",
        [
            "ay",
            "bee",
            "see",
            "dee",
            "ee",
            "ef",
            "gee",
            "aitch",
            "eye",
            "jay",
            "kay",
            "el",
            "em",
            "en",
            "oh",
            "pee",
            "cue",
            "ar",
            "ess",
            "tee",
            "you",
            "vee",
            "double-you",
            "ex",
            "why",
            "zee",
        ],
        strict=True,
    )
)
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
_ONES += "fifteen sixteen seventeen eighteen nineteen"
_SMALL = _ONES.split()
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_TOKEN = re.compile(r"[\w'\u2019.+#\-]+")
_CHUNK = re.compile(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]|\d+(?:\.\d+)?|[^\W\d_]+")


@cache
def _cmudict() -> dict[str, tuple[Phones, ...]]:
    raw = files(__package__).joinpath("pronunciation/cmudict.dict.gz").read_bytes()
    out: dict[str, list[Phones]] = {}
    for line in gzip.decompress(raw).decode("utf-8").splitlines():
        head, _, rest = line.partition(" ")
        word = re.sub(r"\(\d+\)$", "", head)
        phones = tuple(_ID[re.sub(r"\d", "", p)] for p in rest.split("#")[0].split())
        if phones:
            out.setdefault(word, []).append(phones)
    return {w: tuple(v) for w, v in out.items()}


class _G2p:
    """g2p_en's GRU encoder-decoder, run greedily on one lowercase word."""

    GRAPHEMES = ("<pad>", "<unk>", "</s>", *"abcdefghijklmnopqrstuvwxyz")

    def __init__(self) -> None:
        with files(__package__).joinpath("pronunciation/g2p.npz").open("rb") as f:
            loaded = np.load(f)
            self.w = {k: loaded[k] for k in loaded.files}
        # g2p_en's outputs: four markers, then the phonemes in alphabetical order, vowels
        # with stress 0-2 (UW also without).
        outputs = ["<pad>", "<unk>", "<s>", "</s>"]
        for p in sorted(PHONES):
            stressed = [f"{p}{s}" for s in "012"] if _ID[p] in _VOWELS else []
            outputs += ([p] if p == "UW" else []) + stressed if stressed else [p]
        assert len(outputs) == len(self.w["fc_b"])
        self.phones = [_ID.get(re.sub(r"\d", "", o)) for o in outputs]

    def _cell(
        self, x: npt.NDArray[np.float32], h: npt.NDArray[np.float32], side: str
    ) -> npt.NDArray[np.float32]:
        w = self.w
        ih = x @ w[f"{side}_w_ih"].T + w[f"{side}_b_ih"]
        hh = h @ w[f"{side}_w_hh"].T + w[f"{side}_b_hh"]
        n = ih.shape[-1] // 3
        r = 1 / (1 + np.exp(-(ih[:, :n] + hh[:, :n])))
        z = 1 / (1 + np.exp(-(ih[:, n : 2 * n] + hh[:, n : 2 * n])))
        new = np.tanh(ih[:, 2 * n :] + r * hh[:, 2 * n :])
        return np.asarray((1 - z) * new + z * h, dtype=np.float32)

    def __call__(self, word: str) -> Phones:
        w = self.w
        h = np.zeros((1, w["enc_w_hh"].shape[1]), np.float32)
        for c in word:
            h = self._cell(w["enc_emb"][[self.GRAPHEMES.index(c)]], h, "enc")
        h = self._cell(w["enc_emb"][[2]], h, "enc")  # </s>
        previous, out = 2, []  # <s>
        for _ in range(20):
            h = self._cell(w["dec_emb"][[previous]], h, "dec")
            previous = int((h @ w["fc_w"].T + w["fc_b"]).argmax())
            if previous == 3:  # </s>
                break
            phone = self.phones[previous]
            if phone is not None:
                out.append(phone)
        return tuple(out)


@cache
def _g2p() -> _G2p:
    return _G2p()


@lru_cache(maxsize=65536)
def word(text: str) -> tuple[Phones, ...]:
    """The readings of one plain lowercase word: CMUdict's (at most two), else predicted."""
    known = _cmudict().get(text)
    if known:
        return known[:2]
    letters = re.sub(r"[^a-z]", "", text)
    return (_g2p()(letters),) if letters else ()


def _number(digits: str) -> list[str]:
    """Words for a number as said: whole up to 99, else digit by digit; "5.5" with "point"."""
    whole, _, fraction = digits.partition(".")
    n = int(whole) if whole.isdigit() else -1
    if 0 <= n < 20:
        said = [_SMALL[n]]
    elif 20 <= n < 100:
        said = [_TENS[n // 10]] + ([_SMALL[n % 10]] if n % 10 else [])
    else:
        said = [_SMALL[int(d)] for d in whole]
    if fraction:
        said += ["point", *(_SMALL[int(d)] for d in fraction)]
    return said


def _join(options: Iterable[Sequence[Phones]], limit: int) -> tuple[Phones, ...]:
    out: list[Phones] = []
    for combo in itertools.islice(itertools.product(*options), limit):
        phones = tuple(p for part in combo for p in part)
        if phones and phones not in out:
            out.append(phones)
    return tuple(out)


def _lettered(chunk: str) -> bool:
    """Short capitals, said letter by letter ("CLI"); "I" is a word."""
    short = chunk.isascii() and chunk.isalpha() and len(chunk) <= 5
    return short and chunk.isupper() and chunk != "I"


def _chunk(chunk: str) -> tuple[Phones, ...]:
    if chunk == "dot":
        return word("dot")
    if chunk[0].isdigit():
        return _join([word(w) for w in _number(chunk)], 2)
    if _lettered(chunk):
        spelled = _join([word(_LETTERS[c]) for c in chunk.lower()], 1)
        if len(chunk) == 1:
            return spelled
        return tuple(dict.fromkeys(spelled + word(chunk.lower())))
    return word(chunk.lower())


@lru_cache(maxsize=65536)
def token(text: str, limit: int = 4) -> tuple[Phones, ...]:
    """The readings of one written token ("OpenAI's", "GPT-5.5", "entune.dev")."""
    plain = text.lower().replace("\u2019", "'").strip(".-")
    if "'" in plain.rstrip("'") and plain in _cmudict():  # "I'll", "don't": a word whole
        return word(plain)
    return _join([r for r in map(_chunk, _chunks(text)) if r], limit)


def _chunks(text: str) -> list[str]:
    """A token's parts as said: camelCase, capitals and numbers apart, a dot as "dot"."""
    text = re.sub(r"['\u2019]s$", "", text).strip(".-'\u2019")
    chunks: list[str] = []
    for i, part in enumerate(re.split(r"(?<=\D)\.|\.(?=\D)", text)):
        if i:
            chunks.append("dot")
        for piece in re.split(r"([-_/+#])", part):
            if piece in ("+", "#"):
                chunks.append("plus" if piece == "+" else "sharp")
            elif piece not in ("-", "_", "/"):
                chunks += _CHUNK.findall(piece)
    return chunks


def said_words(text: str) -> int:
    """How many words a spelling is said in: "GPT-5.5" is six, "OpenAI" three."""
    count = 0
    for t in _TOKEN.findall(text):
        for chunk in _chunks(t):
            if chunk[0].isdigit():
                count += len(_number(chunk))
            elif _lettered(chunk):
                count += sum(_LETTERS[c].count("-") + 1 for c in chunk.lower())  # W: two
            else:
                count += 1
    return count


@lru_cache(maxsize=65536)
def readings(text: str, limit: int = 6) -> tuple[Phones, ...]:
    """The readings of written text: each token's, joined, at most `limit`."""
    return _join([r for r in (token(t) for t in _TOKEN.findall(text)) if r], limit)


class Index:
    """Pronunciations of a set of spellings, for similarity against any text at once."""

    def __init__(self, forms: Sequence[Sequence[Phones]]) -> None:
        """`forms[i]` holds the readings of the i-th spelling."""
        self.size = len(forms)
        flat = [(i, f) for i, fs in enumerate(forms) for f in fs if f]
        self.owner = np.array([i for i, _ in flat], dtype=np.int64)
        self.lengths = np.array([len(f) for _, f in flat], dtype=np.int64)
        longest = int(self.lengths.max()) if flat else 0
        self.phones = np.zeros((len(flat), longest), dtype=np.int64)
        for row, (_, f) in enumerate(flat):
            self.phones[row, : len(f)] = f
        self.exact: dict[Phones, set[int]] = {}
        for i, f in flat:
            self.exact.setdefault(f, set()).add(i)

    def similarity(self, text: Sequence[Phones]) -> npt.NDArray[np.float64]:
        """Each spelling's best similarity to any of `text`'s readings."""
        best = np.zeros(self.size)
        if not len(self.owner):
            return best
        for reading in text:
            n = len(reading)
            rows = np.flatnonzero(
                (self.lengths * LENGTH_RATIO[0] <= n) & (n <= self.lengths * LENGTH_RATIO[1])
            )
            if not len(rows) or not n:
                continue
            lengths = self.lengths[rows]
            forms = self.phones[rows, : int(lengths.max())]
            sim = _similarity(np.array(reading, dtype=np.int64), forms, lengths)
            np.maximum.at(best, self.owner[rows], sim)
        return best

    def same(self, text: Sequence[Phones]) -> set[int]:
        """The spellings said exactly as one of `text`'s readings (homophones)."""
        return set().union(*(self.exact.get(r, set()) for r in text)) if text else set()


def _similarity(
    a: npt.NDArray[np.int64], forms: npt.NDArray[np.int64], lengths: npt.NDArray[np.int64]
) -> npt.NDArray[np.float64]:
    """1 - weighted edit distance / longer length, between `a` and every form, at once.
    `forms` is padded to its longest; `lengths` holds each form's own length."""
    n, (m, width) = len(a), forms.shape
    if n == 0:
        return np.zeros(m)
    indel_b = _INDEL[forms]  # (m, width)
    prev = np.concatenate([np.zeros((m, 1)), np.cumsum(indel_b, axis=1)], axis=1)
    for i in range(n):
        cur = np.empty_like(prev)
        cur[:, 0] = prev[:, 0] + _INDEL[a[i]]
        sub = _SUB[a[i], forms]
        for j in range(1, width + 1):
            cur[:, j] = np.minimum(
                np.minimum(prev[:, j] + _INDEL[a[i]], cur[:, j - 1] + indel_b[:, j - 1]),
                prev[:, j - 1] + sub[:, j - 1],
            )
        prev = cur
    distance = prev[np.arange(m), lengths]
    return np.asarray(1 - distance / np.maximum(lengths, n), dtype=np.float64)
