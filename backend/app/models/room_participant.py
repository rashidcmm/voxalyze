import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomParticipant(Base):
    """The real user_id is always recorded server-side — even in anonymous
    rooms — for the participant's own progress history and abuse prevention.

    alias_name is always populated at join time and is what's shown as the
    participant's display name for the rest of the room's lifecycle: in
    identified rooms it's a snapshot of the user's account name at join time
    (so a later name change doesn't retroactively alter a past session's
    report); in anonymous rooms it's a generated "Speaker N" label. Either
    way, callers never need to fall back to a live User lookup to label a
    participant.
    """

    __tablename__ = "room_participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    alias_name: Mapped[str] = mapped_column(String(50), nullable=False)
    livekit_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    audio_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
