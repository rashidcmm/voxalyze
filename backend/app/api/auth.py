import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.email import EmailSendError, build_reset_email, build_verify_email, send_email
from app.core.email_tokens import consume_token, issue_token
from app.core.rate_limit import client_ip, enforce_rate_limit
from app.core.security import create_access_token, hash_password, verify_password
from app.models.email_token import EmailTokenPurpose
from app.models.user import User
from app.schemas.auth import (
    EmailOnlyRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)

logger = logging.getLogger("api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit(f"ratelimit:signup:ip:{client_ip(request)}", limit=5, window_seconds=3600)

    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
    )
    db.add(user)
    await db.flush()

    raw_token = await issue_token(db, user.id, EmailTokenPurpose.VERIFY)
    # Commit before attempting to send: the account and its token must persist even
    # if Brevo is down, so a later resend-verification call works without re-signup.
    await db.commit()

    link = f"{get_settings().frontend_base_url}/verify-email?token={raw_token}"
    subject, html = build_verify_email(user.name, link)
    try:
        await send_email(user.email, user.name, subject, html)
    except EmailSendError:
        logger.error("Failed to send verification email to %s", user.email)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Account created, but we couldn't send the verification email. "
                "Use 'resend verification' from the login page to try again."
            ),
        )

    return MessageResponse(message="Account created. Check your email to verify before logging in.")


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    await enforce_rate_limit(f"ratelimit:login:ip:{client_ip(request)}", limit=10, window_seconds=300)
    await enforce_rate_limit(f"ratelimit:login:email:{payload.email}", limit=5, window_seconds=300)

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    token = create_access_token(subject=str(user.id))
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)):
    token = await consume_token(db, payload.token, EmailTokenPurpose.VERIFY)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired."
        )

    user = await db.get(User, token.user_id)
    user.is_verified = True
    await db.commit()
    return MessageResponse(message="Email verified. You can now log in.")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    payload: EmailOnlyRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    await enforce_rate_limit(
        f"ratelimit:resend-verification:email:{payload.email}", limit=1, window_seconds=60
    )

    generic_response = MessageResponse(
        message="If that email is registered and not yet verified, we've sent a new link."
    )

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or user.is_verified:
        return generic_response

    raw_token = await issue_token(db, user.id, EmailTokenPurpose.VERIFY)
    await db.commit()

    link = f"{get_settings().frontend_base_url}/verify-email?token={raw_token}"
    subject, html = build_verify_email(user.name, link)
    try:
        await send_email(user.email, user.name, subject, html)
    except EmailSendError:
        # Deliberately still generic — a differing response here would leak whether
        # payload.email belongs to a real, unverified account.
        logger.error("Failed to resend verification email to %s", user.email)

    return generic_response


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    payload: EmailOnlyRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    await enforce_rate_limit(
        f"ratelimit:forgot-password:email:{payload.email}", limit=1, window_seconds=60
    )

    generic_response = MessageResponse(
        message="If that email is registered, we've sent a password reset link."
    )

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        return generic_response

    raw_token = await issue_token(db, user.id, EmailTokenPurpose.RESET)
    await db.commit()

    link = f"{get_settings().frontend_base_url}/reset-password?token={raw_token}"
    subject, html = build_reset_email(user.name, link)
    try:
        await send_email(user.email, user.name, subject, html)
    except EmailSendError:
        logger.error("Failed to send password reset email to %s", user.email)

    return generic_response


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    token = await consume_token(db, payload.token, EmailTokenPurpose.RESET)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired."
        )

    user = await db.get(User, token.user_id)
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return MessageResponse(message="Password updated. You can now log in with your new password.")
