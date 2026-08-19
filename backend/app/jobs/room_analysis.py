"""ARQ job: finalize a room's post-session report once the host ends it.
Mirrors app/jobs/scoring.py's shape (idempotency guard, isolated LLM-pass
failure, exponential-backoff retry) but works from already-captured live
data (room_transcript_segments) rather than re-running transcription — the
transcript was already produced live by Azure streaming STT during the
session (see app/rooms/bot.py).
"""
import logging

from sqlalchemy import select

from app.core.db import async_session_maker
from app.models.room import Room, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_report import RoomReport
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.rooms.live_stats import SpeechSegment, compute_participant_stats, compute_session_dominance
from app.scoring.group_dynamics import score_group_dynamics
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("jobs.room_analysis")

MAX_TRIES = 3


def _build_transcript_text(segments: list[RoomTranscriptSegment], alias_by_participant: dict) -> str:
    ordered = sorted(segments, key=lambda s: s.start_s)
    return "\n".join(f"{alias_by_participant[str(s.participant_id)]}: {s.text}" for s in ordered)


async def analyze_room_session(ctx: dict, room_id: str) -> str:
    from arq.worker import Retry  # local import, same reasoning as app/jobs/transcription.py

    job_try = ctx.get("job_try", 1)

    async with async_session_maker() as db:
        existing = await db.execute(select(RoomReport).where(RoomReport.room_id == room_id))
        if existing.scalar_one_or_none() is not None:
            logger.info("room %s already has a report, skipping", room_id)
            return "already_analyzed"

        room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
        if room is None:
            logger.error("room %s not found, cannot analyze", room_id)
            return "room_not_found"

        participants = (
            await db.execute(select(RoomParticipant).where(RoomParticipant.room_id == room_id))
        ).scalars().all()
        transcript_rows = (
            await db.execute(select(RoomTranscriptSegment).where(RoomTranscriptSegment.room_id == room_id))
        ).scalars().all()

        alias_by_participant = {str(p.id): p.alias_name for p in participants}
        participant_ids = [str(p.id) for p in participants]
        segments = [
            SpeechSegment(participant_id=str(row.participant_id), start_s=row.start_s, end_s=row.end_s)
            for row in transcript_rows
        ]
        session_duration_s = max((row.end_s for row in transcript_rows), default=0.0)
        transcript_text = _build_transcript_text(transcript_rows, alias_by_participant)

    try:
        stats = compute_participant_stats(segments, participant_ids, session_duration_s)
        dominance = compute_session_dominance(stats)
        stats_payload = {pid: {k: v for k, v in s.__dict__.items() if k != "participant_id"} for pid, s in stats.items()}

        try:
            qualitative = score_group_dynamics(transcript_text, stats_payload)
        except ScorerNotConfigured as exc:
            qualitative = {"status": "not_configured", "error_detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 — deliberate isolation boundary, same as app/scoring/pipeline.py
            logger.exception("group dynamics scorer crashed for room %s", room_id)
            qualitative = {"status": "error", "error_detail": str(exc)[:500]}

        async with async_session_maker() as db:
            db.add(
                RoomReport(
                    room_id=room_id,
                    participant_stats=stats_payload,
                    dominance_index=dominance,
                    qualitative_status=qualitative["status"],
                    # status/error_detail already have their own columns above —
                    # same convention as app/scoring/pipeline.py's _payload() helper.
                    qualitative_result=(
                        {k: v for k, v in qualitative.items() if k not in ("status", "error_detail")}
                        if qualitative["status"] == "ok"
                        else None
                    ),
                    qualitative_error=qualitative.get("error_detail"),
                )
            )
            room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one()
            room.status = RoomStatus.ANALYZED
            await db.commit()

        logger.info("analyzed room %s: %d participants, dominance=%.3f", room_id, len(participant_ids), dominance)
        return "ok"

    except Exception as exc:
        logger.exception("room analysis failed for room %s (try %d)", room_id, job_try)
        if job_try < MAX_TRIES:
            raise Retry(defer=5 * (5 ** (job_try - 1)))
        raise
