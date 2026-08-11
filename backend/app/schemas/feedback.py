import uuid
from datetime import datetime

from pydantic import BaseModel


class HeadlineScoresSchema(BaseModel):
    fluency: float
    vocabulary: float
    clarity: float | None
    relevance: float | None
    argumentation: float | None
    overall: float


class FillerWordCount(BaseModel):
    word: str
    count: int


class PauseSpan(BaseModel):
    start_s: float
    end_s: float
    duration_s: float
    is_hesitation: bool


class SlowSegment(BaseModel):
    start_s: float
    end_s: float
    wpm: float


class TranscriptWord(BaseModel):
    word: str
    start_s: float
    end_s: float


class FeedbackResponse(BaseModel):
    session_id: uuid.UUID
    created_at: datetime
    topic_text: str
    topic_category: str
    topic_difficulty: int
    duration_s: float | None

    headline: HeadlineScoresSchema

    full_text: str
    transcript_words: list[TranscriptWord]
    pauses: list[PauseSpan]
    slow_segments: list[SlowSegment]
    top_filler_words: list[FillerWordCount]

    # None until Day 4 scorers (Azure/Anthropic) are configured & have run.
    relevance_drift_curve: list[dict] | None
    improvement_points: list[str] | None
    argument_rationale: str | None
    pronunciation_words_needing_attention: list[dict] | None
