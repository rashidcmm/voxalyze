import logging

from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.jobs.transcription import MAX_TRIES, transcribe_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

settings = get_settings()


async def dummy_job(ctx, message: str) -> str:
    """Placeholder job used to prove the queue works end to end (Phase 0 DoD).
    Replaced by real jobs (transcription, scoring, ...) from Day 2 onward.
    """
    logger.info("dummy_job received message: %s", message)
    return f"processed: {message}"


async def startup(ctx):
    logger.info("ARQ worker starting up")


async def shutdown(ctx):
    logger.info("ARQ worker shutting down")


class WorkerSettings:
    functions = [
        dummy_job,
        func(transcribe_session, max_tries=MAX_TRIES),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
