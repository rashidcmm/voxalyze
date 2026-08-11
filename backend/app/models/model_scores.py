import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ModelScores(Base):
    """One row per session, one column-group per Phase 5/Day 4 scorer.

    Unlike session_metrics (flat scalar columns, because every deterministic
    metric is exactly one number), each scorer here produces a differently
    shaped, sometimes-nested result (word-level error lists, a per-utterance
    drift curve, an LLM rationale string) — so each is stored as a single JSON
    blob rather than enumerated into a dozen columns apiece. `*_status` is
    always one of "ok" / "not_configured" / "error" (see app.scoring.types)
    so the API/UI can distinguish "no score yet because you haven't added an
    API key" from "this scorer actually failed" without inspecting the blob.
    """

    __tablename__ = "model_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )

    pronunciation_status: Mapped[str] = mapped_column(String(20), nullable=False)
    pronunciation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pronunciation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    relevance_status: Mapped[str] = mapped_column(String(20), nullable=False)
    relevance_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    relevance_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    argument_status: Mapped[str] = mapped_column(String(20), nullable=False)
    argument_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    argument_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
