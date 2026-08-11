import enum
import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomMode(str, enum.Enum):
    IDENTIFIED = "identified"
    ANONYMOUS = "anonymous"


class RoomStatus(str, enum.Enum):
    WAITING = "waiting"
    LIVE = "live"
    ENDED = "ended"
    ANALYZED = "analyzed"


def _generate_join_code() -> str:
    # 8 chars, unambiguous alphabet (no 0/O/1/I) — meant to be read aloud or
    # typed over a call.
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(8))


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # native_enum=False: plain VARCHAR + CHECK constraint, same reasoning as
    # SessionStatus in app/models/session.py.
    mode: Mapped[RoomMode] = mapped_column(
        Enum(RoomMode, name="room_mode", native_enum=False, length=20), nullable=False
    )
    status: Mapped[RoomStatus] = mapped_column(
        Enum(RoomStatus, name="room_status", native_enum=False, length=20),
        default=RoomStatus.WAITING,
        nullable=False,
        index=True,
    )
    join_code: Mapped[str] = mapped_column(String(8), unique=True, index=True, default=_generate_join_code)
    max_participants: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
