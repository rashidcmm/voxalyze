import pytest
from jose import jwt as jose_jwt

from app.core.config import get_settings
from app.rooms.livekit_tokens import RoomsNotConfigured, issue_bot_token, issue_participant_token


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


def test_issue_participant_token_raises_when_not_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "")
    get_settings.cache_clear()
    with pytest.raises(RoomsNotConfigured):
        issue_participant_token(room_name="abc123", identity="user-1", display_name="Alex")


def test_issue_participant_token_returns_a_jwt_with_the_right_claims(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-at-least-32-bytes-long")
    get_settings.cache_clear()
    token = issue_participant_token(room_name="abc123", identity="user-1", display_name="Alex")
    claims = jose_jwt.get_unverified_claims(token)
    assert claims["sub"] == "user-1"
    assert claims["video"]["room"] == "abc123"
    assert claims["video"]["roomJoin"] is True


def test_issue_bot_token_uses_a_bot_prefixed_identity(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-at-least-32-bytes-long")
    get_settings.cache_clear()
    token = issue_bot_token(room_name="abc123")
    claims = jose_jwt.get_unverified_claims(token)
    assert claims["sub"] == "bot:abc123"
