"""LiveKit access-token issuance for room participants.

Same "fail loud with a clear, actionable message when not configured yet"
pattern as app/scoring/pronunciation.py's ScorerNotConfigured, so a missing
LIVEKIT_API_KEY/SECRET degrades to a clear 503 at the API layer (see
app/api/rooms.py) rather than an unhandled crash.
"""
from livekit import api

from app.core.config import get_settings


class RoomsNotConfigured(RuntimeError):
    """Raised when LIVEKIT_API_KEY/LIVEKIT_API_SECRET aren't set."""


def issue_participant_token(*, room_name: str, identity: str, display_name: str) -> str:
    settings = get_settings()
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RoomsNotConfigured(
            "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set — see README for how to get a free LiveKit Cloud project"
        )
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
    )
    return token.to_jwt()


def issue_bot_token(*, room_name: str) -> str:
    """Token for the backend's own server-side participant (see
    app/rooms/bot.py) — identity is prefixed so it's never confused with a
    real participant when the frontend lists who's in the room."""
    return issue_participant_token(room_name=room_name, identity=f"bot:{room_name}", display_name="analysis-bot")
