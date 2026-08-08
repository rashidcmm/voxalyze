import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DBSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.storage import save_upload
from app.jobs.pool import get_arq_pool
from app.models.session import Session, SessionStatus
from app.models.topic import Topic
from app.models.transcript import Transcript
from app.models.user import User
from app.schemas.session import AudioUploadResponse, SessionCreate, SessionResponse
from app.schemas.transcript import TranscriptResponse

CONTENT_TYPE_BY_EXTENSION = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
}

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Accept common browser MediaRecorder output types + a few common upload formats.
ALLOWED_AUDIO_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB — generous cap for a 2-5 min recording


async def _get_owned_session(session_id: uuid.UUID, user: User, db: DBSession) -> Session:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    stmt = select(Topic)
    if payload.difficulty is not None:
        stmt = stmt.where(Topic.difficulty == payload.difficulty)
    if payload.category is not None:
        stmt = stmt.where(Topic.category == payload.category)
    stmt = stmt.order_by(func.random()).limit(1)

    topic = (await db.execute(stmt)).scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No matching topic found")

    session = Session(user_id=current_user.id, topic_id=topic.id, status=SessionStatus.RECORDING)
    db.add(session)
    await db.commit()

    # Re-fetch with the topic relationship loaded for the response.
    result = await db.execute(select(Session).where(Session.id == session.id))
    session = result.scalar_one()
    return session


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    return await _get_owned_session(session_id, current_user, db)


@router.post("/{session_id}/audio", response_model=AudioUploadResponse)
async def upload_audio(
    session_id: uuid.UUID,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    session = await _get_owned_session(session_id, current_user, db)

    extension = ALLOWED_AUDIO_TYPES.get(file.content_type)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio type: {file.content_type}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file too large",
        )

    # Re-uploads (retry after a failed attempt) simply overwrite — upload is not itself
    # a state machine step worth guarding against duplicates.
    path = await save_upload(str(session.id), extension, data)

    session.audio_path = str(path)
    session.status = SessionStatus.UPLOADED
    await db.commit()

    pool = await get_arq_pool()
    # job_id pins one in-flight transcription job per session, so a re-upload
    # (retry) while a job is still queued/running doesn't fan out duplicate jobs.
    await pool.enqueue_job(
        "transcribe_session", str(session.id), _job_id=f"transcribe:{session.id}"
    )

    return AudioUploadResponse(id=session.id, status=session.status)


@router.get("/{session_id}/audio")
async def get_audio(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    session = await _get_owned_session(session_id, current_user, db)
    if not session.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No audio uploaded yet")

    path = Path(session.audio_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Audio file missing on disk")

    extension = path.suffix.lstrip(".")
    media_type = CONTENT_TYPE_BY_EXTENSION.get(extension, "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@router.get("/{session_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    await _get_owned_session(session_id, current_user, db)

    result = await db.execute(
        select(Transcript)
        .options(selectinload(Transcript.words))
        .where(Transcript.session_id == session_id)
    )
    transcript = result.scalar_one_or_none()
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No transcript yet")

    return transcript
