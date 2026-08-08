import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.session import SessionStatus
from app.schemas.topic import TopicResponse


class SessionCreate(BaseModel):
    difficulty: int | None = None
    category: str | None = None


class SessionResponse(BaseModel):
    id: uuid.UUID
    status: SessionStatus
    topic: TopicResponse
    duration_s: float | None
    failure_reason: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AudioUploadResponse(BaseModel):
    id: uuid.UUID
    status: SessionStatus
