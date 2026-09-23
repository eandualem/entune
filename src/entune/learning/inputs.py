"""What a suggestion run reads: transcripts and what the dictionary did to them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from entune.processing.results import Selection
from entune.processing.text_edits import Change

Mode = Literal["generate", "refine"]
# raw_speech: the recognizer's recorded output. legacy_final: an older attempt whose raw
# output was not kept, so only its final text exists. temporary_audio: saved audio
# transcribed again with the selected recognizer for this learning run.
Kind = Literal["raw_speech", "legacy_final", "temporary_audio"]


@dataclass(frozen=True)
class DictionaryResult:
    """What the dictionary step recorded for one transcript, in its raw coordinates."""

    changes: tuple[Change, ...]
    # Every matched span and what was decided; None when older records kept edits only.
    selections: tuple[Selection, ...] | None = None


@dataclass(frozen=True)
class LearningText:
    id: str
    text: str
    kind: Kind = "raw_speech"
    # Only when the dictionary step ran on this text and recorded its edits.
    result: DictionaryResult | None = None
