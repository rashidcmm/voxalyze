import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.room import RoomMode


class RoomParticipantReport(BaseModel):
    participant_id: str
    label: str
    is_you: bool
    talk_time_s: float
    talk_time_pct: float
    turn_count: int
    interruptions_made: int
    interruptions_received: int
    longest_monologue_s: float
    silence_pct: float


class RoomReportResponse(BaseModel):
    room_id: uuid.UUID
    mode: RoomMode
    generated_at: datetime
    dominance_index: float
    participants: list[RoomParticipantReport]
    qualitative_status: str
    qualitative: dict | None
