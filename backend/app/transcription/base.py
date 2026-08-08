"""The Transcriber abstraction (Phase 3).

Word-level timestamps are non-negotiable — every deterministic metric in Phase 4
(WPM, pauses, filler rate, ...) is computed from `WordTiming.start_s` / `end_s`.
Any implementation added later (a hosted API, a different local model) must satisfy
this same protocol so the rest of the pipeline never has to know which one ran.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class WordTiming:
    word: str
    start_s: float
    end_s: float
    confidence: float | None = None


@dataclass
class TranscriptResult:
    full_text: str
    words: list[WordTiming]
    duration_s: float
    provider: str
    model: str


class Transcriber(Protocol):
    provider_name: str
    model_name: str

    def transcribe(self, audio_path: Path) -> TranscriptResult: ...
