from datetime import datetime, timedelta, timezone

import pytest

from app.core.email_tokens import consume_token, hash_token, issue_token
from app.core.security import hash_password
from app.models.email_token import EmailToken, EmailTokenPurpose
from app.models.user import User


async def _make_user(db_session, email="user@example.com") -> User:
    user = User(email=email, password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.flush()
    return user


async def test_issue_token_returns_a_raw_token_whose_hash_is_stored(db_session):
    user = await _make_user(db_session)
    raw = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    from sqlalchemy import select

    result = await db_session.execute(select(EmailToken).where(EmailToken.user_id == user.id))
    row = result.scalar_one()
    assert row.token_hash == hash_token(raw)
    assert row.token_hash != raw


async def test_issuing_a_second_token_invalidates_the_first(db_session):
    user = await _make_user(db_session)
    first = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    second = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    assert await consume_token(db_session, first, EmailTokenPurpose.VERIFY) is None
    assert await consume_token(db_session, second, EmailTokenPurpose.VERIFY) is not None


async def test_consume_token_rejects_unknown_token(db_session):
    assert await consume_token(db_session, "not-a-real-token", EmailTokenPurpose.VERIFY) is None


async def test_consume_token_rejects_wrong_purpose(db_session):
    user = await _make_user(db_session)
    raw = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    assert await consume_token(db_session, raw, EmailTokenPurpose.RESET) is None


async def test_consume_token_rejects_expired_token(db_session):
    user = await _make_user(db_session)
    raw = "expired-raw-token"
    token = EmailToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        purpose=EmailTokenPurpose.RESET,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db_session.add(token)
    await db_session.commit()

    assert await consume_token(db_session, raw, EmailTokenPurpose.RESET) is None


async def test_consume_token_is_single_use(db_session):
    user = await _make_user(db_session)
    raw = await issue_token(db_session, user.id, EmailTokenPurpose.RESET)
    await db_session.commit()

    first = await consume_token(db_session, raw, EmailTokenPurpose.RESET)
    await db_session.commit()
    assert first is not None

    second = await consume_token(db_session, raw, EmailTokenPurpose.RESET)
    assert second is None
