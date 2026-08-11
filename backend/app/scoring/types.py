"""Shared result types for the three Phase 5 / Day 4 scorers.

Each scorer is independently testable and independently optional: a session
with no Azure key configured still gets relevance + argument scores (and vice
versa). `status` distinguishes "ran and produced a score" from "skipped
because not configured" from "ran and failed" so the API/UI can tell those
apart instead of quietly showing a zero.
"""
from dataclasses import dataclass, field
from typing import Literal

ScorerStatus = Literal["ok", "not_configured", "error"]


class ScorerNotConfigured(Exception):
    """Raised by a scorer when the API key/credentials it needs aren't set.
    Caught by the pipeline orchestrator and recorded as status=not_configured
    rather than failing the whole scoring job — the rest of the pipeline
    (deterministic metrics, the other scorers) still works standalone.
    """


@dataclass
class WordIssue:
    word: str
    offset_s: float
    error_type: str  # "Mispronunciation" | "Omission" | "Insertion"
    accuracy_score: float | None = None


@dataclass
class PronunciationResult:
    status: ScorerStatus
    accuracy_score: float | None = None  # 0-100
    fluency_score: float | None = None  # 0-100
    prosody_score: float | None = None  # 0-100, not returned by all Azure API versions
    completeness_score: float | None = None  # 0-100
    # Only mispronunciation/omission words, worst-first, capped — this is the
    # "clarity" surface: which specific words were hardest to make out, not a
    # verdict on accent. See app/scoring/pronunciation.py module docstring.
    words_needing_attention: list[WordIssue] = field(default_factory=list)
    chunks_scored: int = 0
    error_detail: str | None = None


@dataclass
class RelevanceUtterance:
    text: str
    start_s: float
    end_s: float
    similarity: float  # cosine similarity to topic statement, 0-1 (clamped)


@dataclass
class RelevanceResult:
    status: ScorerStatus
    mean_relevance: float | None = None  # 0-1
    drift_curve: list[RelevanceUtterance] = field(default_factory=list)
    error_detail: str | None = None


@dataclass
class ArgumentQualityResult:
    status: ScorerStatus
    argument_structure: int | None = None  # 1-5
    evidence_use: int | None = None  # 1-5
    persuasiveness: int | None = None  # 1-5
    coherence: int | None = None  # 1-5
    counter_argument_handling: int | None = None  # 1-5
    improvement_points: list[str] = field(default_factory=list)
    rationale: str | None = None
    error_detail: str | None = None
