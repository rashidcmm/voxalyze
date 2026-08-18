import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DBSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.user import User
from app.rooms.livekit_tokens import RoomsNotConfigured, issue_participant_token
from app.schemas.room import RoomCreate, RoomJoinResponse, RoomResponse

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
        await db.commit()
        await db.refresh(participant)
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
