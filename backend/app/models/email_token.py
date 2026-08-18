"""Verification/reset tokens (docs/superpowers/specs/2026-08-18-auth-hardening-design.md).

Tokens are opaque random strings; only their SHA-256 hash is ever persisted
here (see app/core/email_tokens.py), so a database compromise doesn't yield
usable tokens.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EmailTokenPurpose(str, enum.Enum):
    VERIFY = "verify"
    RESET = "reset"


class EmailToken(Base):
    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # native_enum=False: plain VARCHAR + CHECK constraint, matching app/models/session.py's
    # SessionStatus convention, so adding a purpose later is a one-line change.
    purpose: Mapped[EmailTokenPurpose] = mapped_column(
        Enum(EmailTokenPurpose, name="email_token_purpose", native_enum=False, length=10),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
