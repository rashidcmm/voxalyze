import uuid
from datetime import datetime

from pydantic import BaseModel


class ProgressPoint(BaseModel):
    session_id: uuid.UUID
    created_at: datetime
    topic_difficulty: int
    topic_category: str

    fluency: float
    vocabulary: float
    clarity: float | None
    relevance: float | None
    argumentation: float | None
    overall: float

    # EWMA-smoothed versions of the same 6 fields, for the trend line — see
    # app/api/progress.py for the smoothing constant and why raw per-session
    # points are too noisy to chart directly.
    fluency_ewma: float
    vocabulary_ewma: float
    clarity_ewma: float | None
    relevance_ewma: float | None
    argumentation_ewma: float | None
    overall_ewma: float


class ProgressResponse(BaseModel):
    points: list[ProgressPoint]
    latest: ProgressPoint | None
