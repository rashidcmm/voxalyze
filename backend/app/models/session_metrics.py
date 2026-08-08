import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SessionMetrics(Base):
    """One row per session, one column per deterministic metric (Phase 4).
    Everything here is computed from the transcript + word timestamps —
    no LLM, no external API, fully reproducible from the same input.
    """

    __tablename__ = "session_metrics"

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

    # --- fluency & delivery ---
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    wpm_overall: Mapped[float] = mapped_column(Float, nullable=False)
    wpm_rolling_windows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    filler_count: Mapped[int] = mapped_column(Integer, nullable=False)
    filler_rate_per_100_words: Mapped[float] = mapped_column(Float, nullable=False)
    natural_pause_count: Mapped[int] = mapped_column(Integer, nullable=False)
    hesitation_pause_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pause_duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    longest_pause_s: Mapped[float] = mapped_column(Float, nullable=False)
    speech_to_silence_ratio: Mapped[float] = mapped_column(Float, nullable=False)

    # --- vocabulary ---
    mtld_score: Mapped[float] = mapped_column(Float, nullable=False)
    repeated_trigram_rate: Mapped[float] = mapped_column(Float, nullable=False)

    # --- syntax ---
    mean_length_utterance: Mapped[float] = mapped_column(Float, nullable=False)
    clauses_per_sentence: Mapped[float] = mapped_column(Float, nullable=False)
    subordination_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    passive_voice_pct: Mapped[float] = mapped_column(Float, nullable=False)
    discourse_marker_rate_per_100_words: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
