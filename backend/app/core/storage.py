"""Local-disk storage for uploaded audio (dev). Swap for Cloudflare R2 /
Supabase Storage in Phase 8 — this module is the seam to swap at.
"""
from pathlib import Path

from app.core.config import get_settings

settings = get_settings()


def session_audio_path(session_id: str, extension: str = "webm") -> Path:
    base = Path(settings.storage_dir) / "sessions" / session_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"audio.{extension}"


async def save_upload(session_id: str, extension: str, data: bytes) -> Path:
    path = session_audio_path(session_id, extension)
    path.write_bytes(data)
    return path


def room_participant_audio_path(room_id: str, participant_id: str) -> Path:
    base = Path(settings.storage_dir) / "rooms" / room_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{participant_id}.wav"
