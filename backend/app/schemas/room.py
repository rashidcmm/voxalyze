import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.models.room import RoomMode, RoomStatus

MIN_ROOM_PARTICIPANTS = 2  # a "group" discussion needs at least two people


class RoomCreate(BaseModel):
    mode: RoomMode
    max_participants: int = Field(default=6, ge=MIN_ROOM_PARTICIPANTS)

    @field_validator("max_participants")
    @classmethod
    def _not_above_configured_max(cls, value: int) -> int:
        """The plan's binding Global Constraint caps a room at
        settings.room_max_participants (6). Checked here rather than as a
        static Field(le=...) so the setting is read at request time — a
        class-level default would freeze whatever value was configured at
        import. Raising ValueError gives FastAPI's normal 422."""
        configured_max = get_settings().room_max_participants
        if value > configured_max:
            raise ValueError(f"max_participants must be at most {configured_max}")
        return value


class RoomResponse(BaseModel):
    id: uuid.UUID
    join_code: str
    mode: RoomMode
    status: RoomStatus
    max_participants: int
    # Deliberately NOT host_user_id: list_rooms returns rooms the caller only
    # joined, so the host's real user id would leak a durable cross-room
    # identity to every guest of an anonymous room. Built explicitly by
    # app/api/rooms.py's _room_response (hence no from_attributes here).
    is_host: bool
    created_at: datetime


class RoomJoinResponse(BaseModel):
    room_id: uuid.UUID
    participant_id: uuid.UUID
    livekit_url: str
    livekit_token: str
    display_name: str
    mode: RoomMode
    max_participants: int
