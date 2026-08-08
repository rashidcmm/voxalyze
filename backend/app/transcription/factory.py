from functools import lru_cache

from app.core.config import get_settings
from app.transcription.base import Transcriber
from app.transcription.local_whisper import LocalWhisperTranscriber

settings = get_settings()


@lru_cache
def get_transcriber() -> Transcriber:
    """Provider selection lives in one place so swapping local <-> hosted is a
    config change, not a code change. Only `local` is implemented in the 6-day
    build (see plan: dual-provider was cut to a stretch goal) — `api` is wired
    as a clear placeholder for whoever picks that up next.
    """
    provider = settings.transcriber_provider
    if provider == "local":
        return LocalWhisperTranscriber(model_name=settings.whisper_model)
    if provider == "api":
        raise NotImplementedError(
            "TRANSCRIBER_PROVIDER=api has no implementation yet — it's a stretch "
            "goal cut from the 6-day plan. Implement an APITranscriber satisfying "
            "the Transcriber protocol and register it here."
        )
    raise ValueError(f"Unknown TRANSCRIBER_PROVIDER: {provider!r}")
