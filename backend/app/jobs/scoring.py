"""ARQ job: run the three Day 4 model-layer scorers for a transcribed session.

Mirrors app/jobs/transcription.py's shape (idempotency guard, status
transitions, exponential-backoff retry) but is a separate job/queue hop rather
than folded into the transcription job — unlike the Phase 4 deterministic
metrics (cheap, no external calls), these scorers make real network calls
(Azure, Anthropic) that can be slow or rate-limited, so they get their own
retry budget instead of re-running the whole transcription on failure.
"""
import logging
from pathlib import Path

from sqlalchemy import select

from app.core.db import async_session_maker
from app.models.model_scores import ModelScores
from app.models.session import Session, SessionStatus
from app.models.topic import Topic
from app.models.transcript import Transcript
from app.scoring.pipeline import compute_all_scores

logger = logging.getLogger("jobs.scoring")

MAX_TRIES = 3


async def score_session(ctx: dict, session_id: str) -> str:
    from arq.worker import Retry  # local import, same reasoning as jobs/transcription.py

    job_try = ctx.get("job_try", 1)

    async with async_session_maker() as db:
        existing = await db.execute(select(ModelScores).where(ModelScores.session_id == session_id))
        if existing.scalar_one_or_none() is not None:
            logger.info("session %s already has model scores, skipping", session_id)
            return "already_scored"

        session = (
            await db.execute(select(Session).where(Session.id == session_id))
        ).scalar_one_or_none()
        if session is None:
            logger.error("session %s not found, cannot score", session_id)
            return "session_not_found"
        if not session.audio_path:
            logger.error("session %s has no audio_path", session_id)
            return "no_audio"

        from sqlalchemy.orm import selectinload  # local import keeps top-level imports minimal

        transcript = (
            await db.execute(
                select(Transcript)
                .options(selectinload(Transcript.words))
                .where(Transcript.session_id == session_id)
            )
        ).scalar_one_or_none()
        if transcript is None:
            logger.error("session %s has no transcript, cannot score", session_id)
            return "no_transcript"

        topic = (await db.execute(select(Topic).where(Topic.id == session.topic_id))).scalar_one()

        full_text = transcript.full_text
        words = list(transcript.words)
        topic_text = topic.text
        audio_path = session.audio_path

        session.status = SessionStatus.SCORING
        await db.commit()

    try:
        scores_dict = compute_all_scores(Path(audio_path), full_text, words, topic_text)

        async with async_session_maker() as db:
            db.add(ModelScores(session_id=session_id, **scores_dict))

            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one()
            session.status = SessionStatus.SCORED
            await db.commit()

        logger.info(
            "scored session %s: pronunciation=%s relevance=%s argument=%s",
            session_id,
            scores_dict["pronunciation_status"],
            scores_dict["relevance_status"],
            scores_dict["argument_status"],
        )
        return "ok"

    except Exception as exc:
        # Each individual scorer already isolates its own failures (see
        # app/scoring/pipeline.py) — reaching here means something broke the
        # orchestration itself (DB down, out of memory, etc.), which is worth
        # retrying rather than silently storing a partial/missing row.
        logger.exception("scoring failed for session %s (try %d)", session_id, job_try)

        async with async_session_maker() as db:
            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one_or_none()
            if session is not None:
                session.status = SessionStatus.FAILED
                session.failure_reason = f"scoring failed: {exc}"[:500]
                await db.commit()

        if job_try < MAX_TRIES:
            raise Retry(defer=5 * (5 ** (job_try - 1)))
        raise
