import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import decode_access_token
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def resolve_token_user(token: str | None, db: AsyncSession) -> User | None:
    """The single source of truth for "does this access token still identify a
    real, non-revoked user": signature/expiry, required claims, the user still
    existing, and the password-change revocation check (a token issued before
    the user's last password change is dead, so a reset really does log every
    session out).

    Returns None instead of raising so both transports can shape their own
    rejection — get_current_user turns it into a 401, and the live-stats
    WebSocket (app/api/rooms.py) into a close frame. Skipping this and only
    checking payload["sub"] is how a WS connection could previously outlive
    the password reset that should have revoked its token."""
    if not token:
        return None

    payload = decode_access_token(token)
    if payload is None:
        return None

    subject = payload.get("sub")
    issued_at = payload.get("iat")
    if subject is None or issued_at is None:
        return None

    try:
        user_id = uuid.UUID(subject)
    except ValueError:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return None

    if user.password_changed_at is not None:
        token_issued_at = datetime.fromtimestamp(issued_at, tz=timezone.utc)
        if token_issued_at < user.password_changed_at:
            return None

    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await resolve_token_user(token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
