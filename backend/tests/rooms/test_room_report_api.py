import pytest

from app.core.security import create_access_token, hash_password
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_report import RoomReport
from app.models.user import User


async def _seed(db_session, mode: RoomMode):
    host = User(email="rhost@example.com", password_hash=hash_password("pw"), name="Host")
    guest = User(email="rguest@example.com", password_hash=hash_password("pw"), name="Guest")
    outsider = User(email="routsider@example.com", password_hash=hash_password("pw"), name="Outsider")
    db_session.add_all([host, guest, outsider])
    await db_session.flush()

    room = Room(host_user_id=host.id, mode=mode, status=RoomStatus.ANALYZED)
    db_session.add(room)
    await db_session.flush()

    alias_host = "Host" if mode == RoomMode.IDENTIFIED else "Speaker 1"
    alias_guest = "Guest" if mode == RoomMode.IDENTIFIED else "Speaker 2"
    p1 = RoomParticipant(room_id=room.id, user_id=host.id, alias_name=alias_host, livekit_identity="x1")
    p2 = RoomParticipant(room_id=room.id, user_id=guest.id, alias_name=alias_guest, livekit_identity="x2")
    db_session.add_all([p1, p2])
    await db_session.flush()

    db_session.add(
        RoomReport(
            room_id=room.id,
            participant_stats={
                str(p1.id): {"talk_time_s": 30.0, "talk_time_pct": 60.0, "turn_count": 3, "interruptions_made": 1, "interruptions_received": 0, "longest_monologue_s": 15.0, "silence_pct": 40.0},
                str(p2.id): {"talk_time_s": 20.0, "talk_time_pct": 40.0, "turn_count": 2, "interruptions_made": 0, "interruptions_received": 1, "longest_monologue_s": 10.0, "silence_pct": 60.0},
            },
            dominance_index=0.2,
            qualitative_status="not_configured",
            qualitative_result=None,
            qualitative_error="ANTHROPIC_API_KEY not set",
        )
    )
    await db_session.commit()
    return room, host, guest, outsider


def _auth_headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


async def test_report_labels_participants_by_alias_in_anonymous_mode(client, db_session):
    room, host, guest, _ = await _seed(db_session, RoomMode.ANONYMOUS)

    resp = await client.get(f"/rooms/{room.id}/report", headers=_auth_headers(guest))
    assert resp.status_code == 200
    labels = {p["label"] for p in resp.json()["participants"]}
    assert labels == {"Speaker 1", "Speaker 2"}


async def test_report_marks_the_requesting_participants_own_row(client, db_session):
    room, host, guest, _ = await _seed(db_session, RoomMode.IDENTIFIED)

    resp = await client.get(f"/rooms/{room.id}/report", headers=_auth_headers(guest))
    body = resp.json()
    you_rows = [p for p in body["participants"] if p["is_you"]]
    assert len(you_rows) == 1
    assert you_rows[0]["label"] == "Guest"


async def test_report_403s_for_a_non_participant(client, db_session):
    room, host, guest, outsider = await _seed(db_session, RoomMode.IDENTIFIED)

    resp = await client.get(f"/rooms/{room.id}/report", headers=_auth_headers(outsider))
    assert resp.status_code == 403


async def test_report_404s_when_not_ready_yet(client, db_session):
    host = User(email="nrhost@example.com", password_hash=hash_password("pw"), name="Host")
    db_session.add(host)
    await db_session.flush()
    room = Room(host_user_id=host.id, mode=RoomMode.IDENTIFIED, status=RoomStatus.ENDED)
    db_session.add(room)
    await db_session.flush()
    participant = RoomParticipant(room_id=room.id, user_id=host.id, alias_name="Host", livekit_identity="x1")
    db_session.add(participant)
    await db_session.commit()

    resp = await client.get(f"/rooms/{room.id}/report", headers=_auth_headers(host))
    assert resp.status_code == 404
