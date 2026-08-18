from unittest.mock import AsyncMock, patch

from app.core.email_tokens import issue_token
from app.core.security import create_access_token, hash_password
from app.models.email_token import EmailTokenPurpose
from app.models.user import User

SEND_EMAIL_PATCH_TARGET = "app.api.auth.send_email"


async def _create_verified_user(db_session, email="verified@example.com", password="pw12345678") -> User:
    user = User(
        email=email, password_hash=hash_password(password), name="Verified", is_verified=True
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_signup_creates_an_unverified_user_and_sends_a_verify_email(client, db_session):
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()) as mock_send:
        resp = await client.post(
            "/auth/signup",
            json={"email": "new@example.com", "password": "pw12345678", "name": "New"},
        )
    assert resp.status_code == 201
    assert "access_token" not in resp.json()
    mock_send.assert_awaited_once()

    from sqlalchemy import select

    result = await db_session.execute(select(User).where(User.email == "new@example.com"))
    user = result.scalar_one()
    assert user.is_verified is False


async def test_signup_rejects_duplicate_email(client, db_session):
    await _create_verified_user(db_session, email="dupe@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()):
        resp = await client.post(
            "/auth/signup",
            json={"email": "dupe@example.com", "password": "pw12345678", "name": "Dupe"},
        )
    assert resp.status_code == 409


async def test_signup_returns_502_but_keeps_the_user_row_if_email_send_fails(client, db_session):
    from app.core.email import EmailSendError

    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock(side_effect=EmailSendError("boom"))):
        resp = await client.post(
            "/auth/signup",
            json={"email": "flaky@example.com", "password": "pw12345678", "name": "Flaky"},
        )
    assert resp.status_code == 502

    from sqlalchemy import select

    result = await db_session.execute(select(User).where(User.email == "flaky@example.com"))
    assert result.scalar_one_or_none() is not None


async def test_login_rejects_unverified_user(client, db_session):
    user = User(email="unverified@example.com", password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.commit()

    resp = await client.post(
        "/auth/login", json={"email": "unverified@example.com", "password": "pw12345678"}
    )
    assert resp.status_code == 403


async def test_login_succeeds_for_verified_user(client, db_session):
    await _create_verified_user(db_session, email="ok@example.com")
    resp = await client.post("/auth/login", json={"email": "ok@example.com", "password": "pw12345678"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


async def test_verify_email_marks_user_verified(client, db_session):
    user = User(email="tobev@example.com", password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.flush()
    raw_token = await issue_token(db_session, user.id, EmailTokenPurpose.VERIFY)
    await db_session.commit()

    resp = await client.post("/auth/verify-email", json={"token": raw_token})
    assert resp.status_code == 200

    await db_session.refresh(user)
    assert user.is_verified is True


async def test_verify_email_rejects_invalid_token(client, db_session):
    resp = await client.post("/auth/verify-email", json={"token": "garbage"})
    assert resp.status_code == 400


async def test_forgot_password_returns_the_same_message_whether_or_not_the_email_exists(
    client, db_session
):
    await _create_verified_user(db_session, email="exists@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()):
        exists_resp = await client.post("/auth/forgot-password", json={"email": "exists@example.com"})
    missing_resp = await client.post("/auth/forgot-password", json={"email": "nobody@example.com"})

    assert exists_resp.status_code == 200
    assert missing_resp.status_code == 200
    assert exists_resp.json() == missing_resp.json()


async def test_forgot_password_still_returns_generic_message_if_brevo_fails(client, db_session):
    from app.core.email import EmailSendError

    await _create_verified_user(db_session, email="flaky2@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock(side_effect=EmailSendError("boom"))):
        resp = await client.post("/auth/forgot-password", json={"email": "flaky2@example.com"})
    assert resp.status_code == 200


async def test_reset_password_updates_password_and_invalidates_prior_jwts(client, db_session):
    user = await _create_verified_user(db_session, email="reset@example.com", password="oldpassword1")
    old_token = create_access_token(subject=str(user.id))

    raw_reset_token = await issue_token(db_session, user.id, EmailTokenPurpose.RESET)
    await db_session.commit()

    reset_resp = await client.post(
        "/auth/reset-password", json={"token": raw_reset_token, "new_password": "newpassword1"}
    )
    assert reset_resp.status_code == 200

    old_me_resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"})
    assert old_me_resp.status_code == 401

    login_resp = await client.post(
        "/auth/login", json={"email": "reset@example.com", "password": "newpassword1"}
    )
    assert login_resp.status_code == 200


async def test_reset_password_rejects_reused_token(client, db_session):
    user = await _create_verified_user(db_session, email="reuse@example.com")
    raw_reset_token = await issue_token(db_session, user.id, EmailTokenPurpose.RESET)
    await db_session.commit()

    first = await client.post(
        "/auth/reset-password", json={"token": raw_reset_token, "new_password": "firstnewpw1"}
    )
    assert first.status_code == 200

    second = await client.post(
        "/auth/reset-password", json={"token": raw_reset_token, "new_password": "secondnewpw1"}
    )
    assert second.status_code == 400


async def test_resend_verification_reissues_for_an_unverified_user(client, db_session):
    user = User(email="resend@example.com", password_hash=hash_password("pw12345678"), name="U")
    db_session.add(user)
    await db_session.commit()

    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()) as mock_send:
        resp = await client.post("/auth/resend-verification", json={"email": "resend@example.com"})
    assert resp.status_code == 200
    mock_send.assert_awaited_once()


async def test_resend_verification_is_a_no_op_but_still_generic_for_already_verified_user(
    client, db_session
):
    await _create_verified_user(db_session, email="alreadyok@example.com")
    with patch(SEND_EMAIL_PATCH_TARGET, new=AsyncMock()) as mock_send:
        resp = await client.post("/auth/resend-verification", json={"email": "alreadyok@example.com"})
    assert resp.status_code == 200
    mock_send.assert_not_awaited()
