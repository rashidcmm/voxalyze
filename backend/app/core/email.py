"""Sending transactional email via Brevo's HTTP API (verify-email, reset-password).

Uses httpx.AsyncClient directly rather than Brevo's Python SDK, matching this
project's existing pattern of calling external HTTP APIs directly (see
app/scoring/pronunciation.py for the Azure equivalent) rather than pulling in
a provider SDK for a single endpoint.
"""
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("core.email")

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


class EmailSendError(Exception):
    """Raised when Brevo isn't configured, unreachable, or rejects the send."""


async def send_email(to_email: str, to_name: str, subject: str, html_content: str) -> None:
    settings = get_settings()
    if not settings.brevo_api_key:
        raise EmailSendError("Brevo is not configured (BREVO_API_KEY is empty)")

    payload = {
        "sender": {"email": settings.brevo_sender_email, "name": settings.brevo_sender_name},
        "to": [{"email": to_email, "name": to_name}],
        "subject": subject,
        "htmlContent": html_content,
    }
    headers = {"api-key": settings.brevo_api_key, "content-type": "application/json"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(BREVO_SEND_URL, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error("Brevo request failed: %s", exc)
            raise EmailSendError("Could not reach the email provider") from exc

    if response.status_code >= 400:
        logger.error(
            "Brevo rejected email to %s: %s %s", to_email, response.status_code, response.text
        )
        raise EmailSendError(f"Brevo returned {response.status_code}")


def build_verify_email(name: str, link: str) -> tuple[str, str]:
    subject = "Verify your email — GD Trainer"
    html = (
        f"<p>Hi {name},</p>"
        f"<p>Confirm your email to activate your GD Trainer account:</p>"
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>This link expires in 24 hours.</p>"
    )
    return subject, html


def build_reset_email(name: str, link: str) -> tuple[str, str]:
    subject = "Reset your password — GD Trainer"
    html = (
        f"<p>Hi {name},</p>"
        f"<p>Someone requested a password reset for this account. If that was you, "
        f'click below (expires in 1 hour):</p>'
        f'<p><a href="{link}">{link}</a></p>'
        f"<p>If you didn't request this, you can ignore this email.</p>"
    )
    return subject, html
