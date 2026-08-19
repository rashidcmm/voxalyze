import asyncio

import pytest

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models.user import User


async def _create_user(db_session, email: str, name: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), name=name)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _auth_headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture(autouse=True)
def _livekit_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-at-least-32-bytes-long")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_bot(monkeypatch):
    # join_room spawns a real RoomBot task on first join, which would attempt a
    # real LiveKit connection (and, since the failure-logging done-callback was
    # added, log an ERROR for it). Replace run() with a no-op coroutine — same
    # fixture as tests/rooms/test_rooms_live_and_end.py.
    async def _fake_run(self):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.rooms.bot.RoomBot.run", _fake_run)


async def test_create_room_returns_an_8_char_join_code(client, db_session):
    user = await _create_user(db_session, "alex@example.com", "Alex")
    resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(user))
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["join_code"]) == 8
    assert body["status"] == "waiting"


async def test_join_room_identified_mode_uses_the_users_real_name(client, db_session):
    host = await _create_user(db_session, "host@example.com", "Host")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    join_code = create_resp.json()["join_code"]

    join_resp = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))
    assert join_resp.status_code == 200
    assert join_resp.json()["display_name"] == "Host"


async def test_join_room_anonymous_mode_assigns_sequential_aliases(client, db_session):
    host = await _create_user(db_session, "host2@example.com", "Host2")
    guest = await _create_user(db_session, "guest2@example.com", "Guest2")
    create_resp = await client.post("/rooms", json={"mode": "anonymous"}, headers=_auth_headers(host))
    join_code = create_resp.json()["join_code"]

    host_join = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))
    guest_join = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(guest))
    assert host_join.json()["display_name"] == "Speaker 1"
    assert guest_join.json()["display_name"] == "Speaker 2"


async def test_rejoining_the_same_room_reuses_the_same_participant(client, db_session):
    host = await _create_user(db_session, "host3@example.com", "Host3")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    join_code = create_resp.json()["join_code"]

    first = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))
    second = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))
    assert first.json()["participant_id"] == second.json()["participant_id"]


async def test_join_room_rejects_a_new_participant_once_full(client, db_session):
    # max_participants=2 (the schema's floor — a "group" needs at least two)
    # rather than the 1 this test used before max_participants gained bounds.
    host = await _create_user(db_session, "host4@example.com", "Host4")
    guest = await _create_user(db_session, "guest4@example.com", "Guest4")
    latecomer = await _create_user(db_session, "late4@example.com", "Late4")
    create_resp = await client.post(
        "/rooms", json={"mode": "identified", "max_participants": 2}, headers=_auth_headers(host)
    )
    join_code = create_resp.json()["join_code"]
    assert (await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))).status_code == 200
    assert (await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(guest))).status_code == 200

    third_join = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(latecomer))
    assert third_join.status_code == 409


async def test_join_room_404s_on_an_unknown_code(client, db_session):
    user = await _create_user(db_session, "solo@example.com", "Solo")
    resp = await client.post("/rooms/ZZZZZZZZ/join", headers=_auth_headers(user))
    assert resp.status_code == 404


async def test_livekit_identity_is_an_opaque_per_join_id(client, db_session):
    """LiveKit broadcasts participant.identity to every peer, and the stats
    layer keys on RoomParticipant.id — so the identity must BE the participant
    id: opaque, not derived from the user, and matching what the stats layer
    looks up."""
    from sqlalchemy import select

    from app.models.room_participant import RoomParticipant

    user = await _create_user(db_session, "ident@example.com", "Ident")
    create_resp = await client.post("/rooms", json={"mode": "anonymous"}, headers=_auth_headers(user))
    join_resp = await client.post(
        f"/rooms/{create_resp.json()['join_code']}/join", headers=_auth_headers(user)
    )
    participant_id = join_resp.json()["participant_id"]

    participant = (
        await db_session.execute(select(RoomParticipant).where(RoomParticipant.id == participant_id))
    ).scalar_one()

    assert participant.livekit_identity == str(participant.id)
    assert str(user.id) not in participant.livekit_identity
    assert str(create_resp.json()["id"]) not in participant.livekit_identity


# --- max_participants bounds (final-review item 5) ---------------------------


async def test_create_room_rejects_max_participants_above_the_configured_cap(client, db_session):
    """The plan's binding Global Constraint caps a room at
    settings.room_max_participants; the schema used to accept any int."""
    user = await _create_user(db_session, "bigroom@example.com", "BigRoom")
    resp = await client.post(
        "/rooms", json={"mode": "identified", "max_participants": 500}, headers=_auth_headers(user)
    )
    assert resp.status_code == 422
    assert "max_participants" in resp.text


async def test_create_room_rejects_max_participants_below_two(client, db_session):
    user = await _create_user(db_session, "soloroom@example.com", "SoloRoom")
    resp = await client.post(
        "/rooms", json={"mode": "identified", "max_participants": 1}, headers=_auth_headers(user)
    )
    assert resp.status_code == 422


async def test_create_room_accepts_a_value_within_range(client, db_session):
    user = await _create_user(db_session, "okroom@example.com", "OkRoom")
    configured_max = get_settings().room_max_participants
    resp = await client.post(
        "/rooms",
        json={"mode": "identified", "max_participants": configured_max},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201
    assert resp.json()["max_participants"] == configured_max


async def test_the_cap_tracks_the_configured_setting(client, db_session, monkeypatch):
    """room_max_participants was previously dead config, referenced nowhere."""
    monkeypatch.setenv("ROOM_MAX_PARTICIPANTS", "3")
    get_settings.cache_clear()
    user = await _create_user(db_session, "capped@example.com", "Capped")
    assert (
        await client.post(
            "/rooms", json={"mode": "identified", "max_participants": 4}, headers=_auth_headers(user)
        )
    ).status_code == 422
    assert (
        await client.post(
            "/rooms", json={"mode": "identified", "max_participants": 3}, headers=_auth_headers(user)
        )
    ).status_code == 201


# --- Host identity is never exposed to guests (final-review item 6) ----------


async def test_list_rooms_never_leaks_the_hosts_user_id_to_a_guest(client, db_session):
    """A guest of an anonymous room gets every room they joined back from
    GET /rooms. Echoing host_user_id there would hand them the host's real,
    cross-room-stable identity — exactly what anonymous mode (and the earlier
    livekit_identity fix) exists to prevent."""
    host = await _create_user(db_session, "anonhost@example.com", "AnonHost")
    guest = await _create_user(db_session, "anonguest@example.com", "AnonGuest")
    create_resp = await client.post("/rooms", json={"mode": "anonymous"}, headers=_auth_headers(host))
    join_code = create_resp.json()["join_code"]
    await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))
    await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(guest))

    resp = await client.get("/rooms", headers=_auth_headers(guest))
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]

    assert row["is_host"] is False
    assert "host_user_id" not in row
    assert str(host.id) not in resp.text  # not hiding anywhere else in the body


async def test_list_rooms_marks_the_callers_own_rooms_as_hosted(client, db_session):
    host = await _create_user(db_session, "ownhost@example.com", "OwnHost")
    await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))

    rows = (await client.get("/rooms", headers=_auth_headers(host))).json()
    assert len(rows) == 1
    assert rows[0]["is_host"] is True


async def test_create_room_reports_the_caller_as_host(client, db_session):
    host = await _create_user(db_session, "createhost@example.com", "CreateHost")
    resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    assert resp.json()["is_host"] is True
    assert "host_user_id" not in resp.json()
