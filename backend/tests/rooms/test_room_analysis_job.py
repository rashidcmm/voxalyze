import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_report import RoomReport
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.models.user import User


@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch):
    # Leave ANTHROPIC_API_KEY unset so the job takes the not_configured path —
    # deterministic and doesn't need a fake LLM client for most of these tests.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_room(db_session):
    host = User(email="host@example.com", password_hash=hash_password("pw"), name="Host")
    guest = User(email="guest@example.com", password_hash=hash_password("pw"), name="Guest")
    db_session.add_all([host, guest])
    await db_session.flush()

    room = Room(host_user_id=host.id, mode=RoomMode.IDENTIFIED, status=RoomStatus.ENDED)
    db_session.add(room)
    await db_session.flush()

    p1 = RoomParticipant(room_id=room.id, user_id=host.id, alias_name="Host", livekit_identity="x1")
    p2 = RoomParticipant(room_id=room.id, user_id=guest.id, alias_name="Guest", livekit_identity="x2")
    db_session.add_all([p1, p2])
    await db_session.flush()

    db_session.add_all([
        RoomTranscriptSegment(room_id=room.id, participant_id=p1.id, text="hello everyone", start_s=0.0, end_s=5.0, is_final=True),
        RoomTranscriptSegment(room_id=room.id, participant_id=p2.id, text="hi there", start_s=5.0, end_s=8.0, is_final=True),
    ])
    await db_session.commit()
    return room, p1, p2


async def test_analyze_room_session_persists_a_report(db_session, db_session_maker, monkeypatch):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, p1, p2 = await _seed_room(db_session)

    result = await analyze_room_session({}, str(room.id))
    assert result == "ok"

    from sqlalchemy import select

    report = (await db_session.execute(select(RoomReport).where(RoomReport.room_id == room.id))).scalar_one()
    assert report.participant_stats[str(p1.id)]["talk_time_s"] == 5.0
    assert report.qualitative_status == "not_configured"

    # The job commits the status change from a different AsyncSession sharing
    # this test's connection/transaction (per db_session_maker's design), so
    # db_session's identity map still holds the pre-update Room instance from
    # _seed_room; a plain re-select doesn't overwrite already-loaded attributes
    # (SQLAlchemy only does that with populate_existing()/refresh()) — refresh
    # explicitly, same convention as tests/rooms/test_rooms_live_and_end.py.
    await db_session.refresh(room)
    assert room.status == RoomStatus.ANALYZED


async def test_analyze_room_session_is_idempotent(db_session, db_session_maker, monkeypatch):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, _, _ = await _seed_room(db_session)

    first = await analyze_room_session({}, str(room.id))
    second = await analyze_room_session({}, str(room.id))
    assert first == "ok"
    assert second == "already_analyzed"


async def test_analyze_room_session_returns_room_not_found_for_unknown_room(db_session, db_session_maker, monkeypatch):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    result = await analyze_room_session({}, "00000000-0000-0000-0000-000000000000")
    assert result == "room_not_found"


# --- session duration (final-review item 10) ---------------------------------


async def test_session_duration_uses_the_rooms_real_length_not_the_last_utterance(
    db_session, db_session_maker, monkeypatch
):
    """The duration used to be max(segment.end_s), so a discussion that trailed
    off into silence measured short — inflating everyone's talk_time_pct and
    deflating silence_pct."""
    from datetime import timedelta

    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, p1, p2 = await _seed_room(db_session)

    # Segments end at 8.0s, but the host let the room run for 100s.
    room.ended_at = room.created_at + timedelta(seconds=100)
    await db_session.commit()

    assert await analyze_room_session({}, str(room.id)) == "ok"

    from sqlalchemy import select

    report = (await db_session.execute(select(RoomReport).where(RoomReport.room_id == room.id))).scalar_one()
    stats = report.participant_stats[str(p1.id)]
    assert stats["talk_time_s"] == 5.0
    # 5s of 100s, not 5s of 8s (which would have read as 62.5%).
    assert stats["talk_time_pct"] == pytest.approx(5.0)
    assert stats["silence_pct"] == pytest.approx(95.0)


async def test_session_duration_falls_back_to_the_last_segment_when_ended_at_is_unset(
    db_session, db_session_maker, monkeypatch
):
    """Defensive fallback — end_room always stamps ended_at, but a row missing
    it must not crash or produce a zero-length session."""
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, p1, _ = await _seed_room(db_session)
    assert room.ended_at is None

    assert await analyze_room_session({}, str(room.id)) == "ok"

    from sqlalchemy import select

    report = (await db_session.execute(select(RoomReport).where(RoomReport.room_id == room.id))).scalar_one()
    stats = report.participant_stats[str(p1.id)]
    assert stats["talk_time_pct"] == pytest.approx(62.5)  # 5s of the 8s transcript span
