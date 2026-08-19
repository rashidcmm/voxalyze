import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession as DBSession

from app.api.deps import get_current_user, resolve_token_user
from app.core.config import get_settings
from app.core.db import get_db
from app.jobs.pool import get_arq_pool
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_report import RoomReport
from app.models.user import User
from app.rooms.livekit_tokens import RoomsNotConfigured, issue_participant_token
from app.rooms.registry import STOP_SENTINEL, get_registry
from app.schemas.room import RoomCreate, RoomJoinResponse, RoomResponse
from app.schemas.room_report import RoomParticipantReport, RoomReportResponse

router = APIRouter(prefix="/rooms", tags=["rooms"])
logger = logging.getLogger("api.rooms")


def _room_response(room: Room, current_user: User) -> RoomResponse:
    """RoomResponse deliberately exposes `is_host` rather than the raw
    host_user_id: list_rooms returns rooms the caller merely *joined*, so
    echoing the host's real, cross-room-stable user id there would hand every
    guest of an anonymous room a durable identity for the host — the same leak
    already closed for livekit_identity."""
    return RoomResponse(
        id=room.id,
        join_code=room.join_code,
        mode=room.mode,
        status=room.status,
        max_participants=room.max_participants,
        is_host=(room.host_user_id == current_user.id),
        created_at=room.created_at,
    )


def _log_bot_task_failure(task: asyncio.Task) -> None:
    """Done-callback for a room's RoomBot task. A crash in run() (e.g. LiveKit
    misconfigured or unreachable) is otherwise invisible: nobody awaits the
    task, so it surfaces only as an "exception was never retrieved" warning at
    GC time with no room context. Cancellation is the normal shutdown path
    (registry.stop_bot) and is not an error."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("room bot task failed: %r", exc, exc_info=exc)


async def _get_room_by_code(join_code: str, db: DBSession) -> Room:
    result = await db.execute(select(Room).where(Room.join_code == join_code.upper()))
    room = result.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return room


@router.post("", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    payload: RoomCreate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    room = Room(host_user_id=current_user.id, mode=payload.mode, max_participants=payload.max_participants)
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return _room_response(room, current_user)


@router.get("", response_model=list[RoomResponse])
async def list_rooms(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    hosted = select(Room.id).where(Room.host_user_id == current_user.id)
    joined = select(RoomParticipant.room_id).where(RoomParticipant.user_id == current_user.id)
    result = await db.execute(
        select(Room).where(Room.id.in_(hosted) | Room.id.in_(joined)).order_by(Room.created_at.desc())
    )
    return [_room_response(room, current_user) for room in result.scalars().all()]


@router.post("/{join_code}/join", response_model=RoomJoinResponse)
async def join_room(
    join_code: str,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    room = await _get_room_by_code(join_code, db)
    if room.status not in (RoomStatus.WAITING, RoomStatus.LIVE):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room is no longer accepting participants")

    existing = (
        await db.execute(
            select(RoomParticipant).where(
                RoomParticipant.room_id == room.id,
                RoomParticipant.user_id == current_user.id,
                RoomParticipant.left_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        active_count = (
            await db.execute(
                select(func.count())
                .select_from(RoomParticipant)
                .where(RoomParticipant.room_id == room.id, RoomParticipant.left_at.is_(None))
            )
        ).scalar_one()
        if active_count >= room.max_participants:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room is full")

        seat_number = active_count + 1
        alias = f"Speaker {seat_number}" if room.mode == RoomMode.ANONYMOUS else current_user.name
        # The room bot keys every speech segment by LiveKit identity, while the
        # live-stats WS and the analysis job key by RoomParticipant.id — so the
        # identity IS the participant id, not a composite. Generated up front
        # because the identity has to be known before the row is flushed.
        #
        # It also has to stay opaque: LiveKit broadcasts participant.identity to
        # every peer, so a user-derived identity would leak the real user's UUID
        # into anonymous rooms and, being stable across rooms, allow correlating
        # an alias back to a real person.
        participant_id = uuid.uuid4()
        participant = RoomParticipant(
            id=participant_id,
            room_id=room.id,
            user_id=current_user.id,
            alias_name=alias,
            livekit_identity=str(participant_id),
        )
        db.add(participant)
        room.status = RoomStatus.LIVE
        is_first_participant = active_count == 0
        await db.commit()
        await db.refresh(participant)

        if is_first_participant:
            from app.rooms.bot import RoomBot

            registry = get_registry()
            bot = RoomBot(room_id=str(room.id), registry=registry)
            task = asyncio.create_task(bot.run())
            task.add_done_callback(_log_bot_task_failure)
            registry.register_bot_task(str(room.id), task)
    else:
        participant = existing

    try:
        token = issue_participant_token(
            room_name=str(room.id), identity=participant.livekit_identity, display_name=participant.alias_name
        )
    except RoomsNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    return RoomJoinResponse(
        room_id=room.id,
        participant_id=participant.id,
        livekit_url=get_settings().livekit_url,
        livekit_token=token,
        display_name=participant.alias_name,
        mode=room.mode,
        max_participants=room.max_participants,
    )


@router.post("/{room_id}/end", response_model=RoomResponse)
async def end_room(
    room_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    if room.host_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the host can end the room")
    if room.status not in (RoomStatus.WAITING, RoomStatus.LIVE):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room already ended")

    get_registry().stop_bot(str(room.id))
    room.status = RoomStatus.ENDED
    room.ended_at = func.now()
    # Nothing else ever sets left_at, so without this every participant of
    # every past room reads as still active forever. Safe to do here: the two
    # queries that filter on left_at IS NULL (join_room's existing-row and
    # active-count checks) only run for WAITING/LIVE rooms, and a join against
    # an ENDED room is rejected by the status check before either one.
    await db.execute(
        update(RoomParticipant)
        .where(RoomParticipant.room_id == room.id, RoomParticipant.left_at.is_(None))
        .values(left_at=func.now())
    )
    await db.commit()
    await db.refresh(room)

    pool = await get_arq_pool()
    await pool.enqueue_job("analyze_room_session", str(room.id), _job_id=f"analyze_room:{room.id}")

    return _room_response(room, current_user)


@router.websocket("/{room_id}/live")
async def live_stats_ws(websocket: WebSocket, room_id: uuid.UUID, db: DBSession = Depends(get_db)):
    # Same validation get_current_user applies over HTTP — including the
    # password_changed_at revocation check, which this handler used to skip by
    # reading payload["sub"] directly, letting a WS connection outlive the
    # password reset that revoked its token.
    user = await resolve_token_user(websocket.query_params.get("token"), db)
    if user is None:
        # Reject before accepting: sending "websocket.close" as the first ASGI
        # message (instead of accept-then-close) is what makes the client see
        # this as a rejected handshake rather than a connection that opened
        # and was immediately dropped.
        await websocket.close(code=4401)
        return

    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None or room.host_user_id != user.id:
        await websocket.close(code=4403)
        return

    await websocket.accept()

    participants = (
        await db.execute(
            select(RoomParticipant).where(RoomParticipant.room_id == room_id, RoomParticipant.left_at.is_(None))
        )
    ).scalars().all()
    participant_ids = [str(p.id) for p in participants]
    started_at = room.created_at

    registry = get_registry()
    queue = registry.subscribe(str(room_id))
    try:
        while True:
            item = await queue.get()
            if item is STOP_SENTINEL:
                # The room's bot has stopped (see registry.stop_bot); no more
                # stats will ever arrive, so close instead of blocking on
                # queue.get() forever.
                await websocket.close()
                break
            elapsed_s = (datetime.now(timezone.utc) - started_at).total_seconds()
            stats = registry.live_stats(str(room_id), participant_ids, elapsed_s)
            await websocket.send_json({pid: asdict(s) for pid, s in stats.items()})
    except WebSocketDisconnect:
        pass
    finally:
        registry.unsubscribe(str(room_id), queue)


@router.get("/{room_id}/report", response_model=RoomReportResponse)
async def get_room_report(
    room_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    requesting_participant = (
        await db.execute(
            select(RoomParticipant).where(
                RoomParticipant.room_id == room_id, RoomParticipant.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if requesting_participant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant in this room")

    report = (await db.execute(select(RoomReport).where(RoomReport.room_id == room_id))).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not ready yet")

    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one()
    all_participants = {
        str(p.id): p
        for p in (
            await db.execute(select(RoomParticipant).where(RoomParticipant.room_id == room_id))
        ).scalars()
    }

    participants = [
        RoomParticipantReport(
            participant_id=pid,
            label=all_participants[pid].alias_name if pid in all_participants else pid,
            is_you=(pid == str(requesting_participant.id)),
            **{k: v for k, v in stats.items() if k != "participant_id"},
        )
        for pid, stats in report.participant_stats.items()
    ]

    return RoomReportResponse(
        room_id=room.id,
        mode=room.mode,
        generated_at=report.generated_at,
        dominance_index=report.dominance_index,
        participants=participants,
        qualitative_status=report.qualitative_status,
        qualitative=report.qualitative_result,
    )
