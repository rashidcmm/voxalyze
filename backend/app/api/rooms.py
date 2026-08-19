import asyncio
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DBSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import decode_access_token
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
    return room


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
    return result.scalars().all()


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
    await db.commit()
    await db.refresh(room)

    pool = await get_arq_pool()
    await pool.enqueue_job("analyze_room_session", str(room.id), _job_id=f"analyze_room:{room.id}")

    return room


@router.websocket("/{room_id}/live")
async def live_stats_ws(websocket: WebSocket, room_id: uuid.UUID, db: DBSession = Depends(get_db)):
    token = websocket.query_params.get("token")
    payload = decode_access_token(token) if token else None
    subject = payload.get("sub") if payload else None
    if subject is None:
        # Reject before accepting: sending "websocket.close" as the first ASGI
        # message (instead of accept-then-close) is what makes the client see
        # this as a rejected handshake rather than a connection that opened
        # and was immediately dropped.
        await websocket.close(code=4401)
        return

    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None or str(room.host_user_id) != subject:
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
