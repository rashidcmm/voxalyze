import logging

from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.jobs.room_analysis import MAX_TRIES as ROOM_ANALYSIS_MAX_TRIES, analyze_room_session
from app.jobs.scoring import MAX_TRIES as SCORING_MAX_TRIES, score_session
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
        func(score_session, max_tries=SCORING_MAX_TRIES),
        func(analyze_room_session, max_tries=ROOM_ANALYSIS_MAX_TRIES),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
