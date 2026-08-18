from datetime import datetime, timedelta, timezone

from app.core.security import hash_password
from app.models.email_token import EmailToken, EmailTokenPurpose
from app.models.user import User


async def test_new_user_defaults_to_unverified_with_no_password_change(db_session):
    user = User(email="new@example.com", password_hash=hash_password("pw12345678"), name="New")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.is_verified is False
    assert user.password_changed_at is None


async def test_email_token_round_trips_through_the_database(db_session):
    user = User(email="tok@example.com", password_hash=hash_password("pw12345678"), name="Tok")
    db_session.add(user)
    await db_session.flush()

    token = EmailToken(
        user_id=user.id,
        token_hash="a" * 64,
        purpose=EmailTokenPurpose.VERIFY,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(token)
    await db_session.commit()
    await db_session.refresh(token)

    assert token.used_at is None
    assert token.purpose == EmailTokenPurpose.VERIFY
