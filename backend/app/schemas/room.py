import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.room import RoomMode, RoomStatus


class RoomCreate(BaseModel):
    mode: RoomMode
    max_participants: int = 6


class RoomResponse(BaseModel):
    id: uuid.UUID
    join_code: str
    mode: RoomMode
    status: RoomStatus
    max_participants: int
    host_user_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class RoomJoinResponse(BaseModel):
    room_id: uuid.UUID
    participant_id: uuid.UUID
    livekit_url: str
    livekit_token: str
    display_name: str
    mode: RoomMode
    max_participants: int
