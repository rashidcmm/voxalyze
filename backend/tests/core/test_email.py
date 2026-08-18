from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.email import EmailSendError, build_reset_email, build_verify_email, send_email


def test_build_verify_email_includes_the_link():
    subject, html = build_verify_email("Alex", "https://app.example.com/verify-email?token=abc")
    assert "abc" in html
    assert subject


def test_build_reset_email_includes_the_link():
    subject, html = build_reset_email("Alex", "https://app.example.com/reset-password?token=xyz")
    assert "xyz" in html
    assert subject


async def test_send_email_raises_if_brevo_not_configured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BREVO_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(EmailSendError):
        await send_email("to@example.com", "To", "subject", "<p>hi</p>")
    get_settings.cache_clear()


async def test_send_email_raises_on_non_2xx_response(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@example.com")
    get_settings.cache_clear()

    mock_response = httpx.Response(401, request=httpx.Request("POST", "https://api.brevo.com/v3/smtp/email"))
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(EmailSendError):
            await send_email("to@example.com", "To", "subject", "<p>hi</p>")
    get_settings.cache_clear()


async def test_send_email_succeeds_on_2xx_response(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "noreply@example.com")
    get_settings.cache_clear()

    mock_response = httpx.Response(201, request=httpx.Request("POST", "https://api.brevo.com/v3/smtp/email"))
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        await send_email("to@example.com", "To", "subject", "<p>hi</p>")  # should not raise
    get_settings.cache_clear()
