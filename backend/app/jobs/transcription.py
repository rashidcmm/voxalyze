"""ARQ job: transcribe a session's uploaded audio.

Idempotent: a `transcripts` row is unique per session_id, so re-running this job
after it already succeeded is a no-op (checked first, before touching Whisper).
Retries with exponential backoff on failure via arq's `Retry(defer=...)` — a
plain exception in an arq job does NOT auto-retry, it fails the job immediately,
so backoff has to be raised explicitly (see MAX_TRIES below).
"""
import logging
from pathlib import Path

from sqlalchemy import select

from app.core.db import async_session_maker
from app.metrics.pipeline import compute_all_metrics
from app.models.session import Session, SessionStatus
from app.models.session_metrics import SessionMetrics
from app.models.transcript import Transcript, Word
from app.transcription import get_transcriber

logger = logging.getLogger("jobs.transcription")

MAX_TRIES = 3


async def transcribe_session(ctx: dict, session_id: str) -> str:
    from arq.worker import Retry  # local import: keeps arq out of the transcription package's deps

    job_try = ctx.get("job_try", 1)

    async with async_session_maker() as db:
        # Idempotency guard: if a transcript already exists, this job already
        # succeeded (possibly in a previous attempt that crashed after the
        # commit but before returning) — don't re-transcribe or duplicate rows.
        existing = await db.execute(select(Transcript).where(Transcript.session_id == session_id))
        if existing.scalar_one_or_none() is not None:
            logger.info("session %s already has a transcript, skipping", session_id)
            return "already_transcribed"

        session = (
            await db.execute(select(Session).where(Session.id == session_id))
        ).scalar_one_or_none()
        if session is None:
            logger.error("session %s not found, cannot transcribe", session_id)
            return "session_not_found"

        if not session.audio_path:
            logger.error("session %s has no audio_path", session_id)
            return "no_audio"

        session.status = SessionStatus.TRANSCRIBING
        await db.commit()

    try:
        transcriber = get_transcriber()
        result = transcriber.transcribe(Path(session.audio_path))

        async with async_session_maker() as db:
            transcript = Transcript(
                session_id=session_id,
                full_text=result.full_text,
                provider=result.provider,
                model=result.model,
                duration_s=result.duration_s,
            )
            db.add(transcript)
            await db.flush()  # assign transcript.id before building Word rows

            for i, w in enumerate(result.words):
                db.add(
                    Word(
                        transcript_id=transcript.id,
                        position=i,
                        word=w.word,
                        start_s=w.start_s,
                        end_s=w.end_s,
                        confidence=w.confidence,
                    )
                )

            # Deterministic metrics (Phase 4) computed in the same job/transaction
            # as the transcript — they're cheap (no ML inference beyond the parse
            # spaCy already needs) and this keeps transcript+metrics atomic:
            # either both land in this commit or neither does.
            metrics_dict = compute_all_metrics(result.full_text, result.words, result.duration_s)
            db.add(SessionMetrics(session_id=session_id, **metrics_dict))

            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one()
            session.status = SessionStatus.TRANSCRIBED
            session.duration_s = result.duration_s
            await db.commit()

        # Chain straight into Day 4 scoring — same job_id-pinning trick as the
        # upload->transcribe enqueue, so a retried transcription doesn't fan
        # out duplicate scoring jobs either.
        from app.jobs.pool import get_arq_pool

        pool = await get_arq_pool()
        await pool.enqueue_job("score_session", session_id, _job_id=f"score:{session_id}")

        logger.info(
            "transcribed session %s: %d words, %.1fs audio, wpm=%.1f",
            session_id,
            len(result.words),
            result.duration_s,
            metrics_dict["wpm_overall"],
        )
        return "ok"

    except Exception as exc:
        logger.exception("transcription failed for session %s (try %d)", session_id, job_try)

        async with async_session_maker() as db:
            session = (
                await db.execute(select(Session).where(Session.id == session_id))
            ).scalar_one_or_none()
            if session is not None:
                session.status = SessionStatus.FAILED
                session.failure_reason = f"transcription failed: {exc}"[:500]
                await db.commit()

        if job_try < MAX_TRIES:
            # Exponential backoff: 5s, 25s, ...
            raise Retry(defer=5 * (5 ** (job_try - 1)))
        raise
