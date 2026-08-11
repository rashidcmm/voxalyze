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
    host = await _create_user(db_session, "host4@example.com", "Host4")
    guest = await _create_user(db_session, "guest4@example.com", "Guest4")
    create_resp = await client.post(
        "/rooms", json={"mode": "identified", "max_participants": 1}, headers=_auth_headers(host)
    )
    join_code = create_resp.json()["join_code"]
    await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(host))

    guest_join = await client.post(f"/rooms/{join_code}/join", headers=_auth_headers(guest))
    assert guest_join.status_code == 409


async def test_join_room_404s_on_an_unknown_code(client, db_session):
    user = await _create_user(db_session, "solo@example.com", "Solo")
    resp = await client.post("/rooms/ZZZZZZZZ/join", headers=_auth_headers(user))
    assert resp.status_code == 404
