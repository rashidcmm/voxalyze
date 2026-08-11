import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomReport(Base):
    """One row per room. participant_stats is a JSON object keyed by
    participant_id, values shaped like app.rooms.live_stats.ParticipantStats.
    qualitative_* follows the same status/result/error isolation pattern as
    model_scores.py: a failed or not-yet-configured LLM pass never blocks the
    deterministic stats from being available.
    """

    __tablename__ = "room_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    participant_stats: Mapped[dict] = mapped_column(JSON, nullable=False)
    dominance_index: Mapped[float] = mapped_column(Float, nullable=False)

    qualitative_status: Mapped[str] = mapped_column(String(20), nullable=False)
    qualitative_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    qualitative_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
