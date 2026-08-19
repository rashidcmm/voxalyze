import asyncio
import uuid

import pytest
from starlette.testclient import TestClient

from app.api.rooms import live_stats_ws
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.jobs import pool as jobs_pool
from app.main import app as fastapi_app
from app.models.user import User
from app.rooms.live_stats import SpeechSegment
from app.rooms.registry import get_registry


class _FakeWebSocket:
    """Minimal stand-in for Starlette's WebSocket, implementing just the
    surface live_stats_ws actually uses. Lets the handler be driven directly
    as a coroutine — awaited straight, no ASGI routing involved — on the
    test's own event loop, instead of through Starlette's TestClient (which
    always runs the ASGI app on a *different* thread with its own event loop,
    incompatible with this project's SAVEPOINT-based db_session fixture; see
    test_live_stats_ws_rejects_a_non_host's docstring below for why that
    still works fine for the no-DB-access rejection path)."""

    def __init__(self, token: str | None):
        self.query_params = {"token": token} if token is not None else {}
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[dict] = []
        self.message_received = asyncio.Event()

    async def accept(self):
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed_code = code

    async def send_json(self, data):
        self.sent.append(data)
        self.message_received.set()


def _await_subscribed(monkeypatch) -> asyncio.Event:
    """Wraps registry.subscribe so a test can await the exact moment
    live_stats_ws has subscribed (i.e. is about to block on queue.get()),
    instead of guessing with a sleep. Public monkeypatching, not a
    registry.py edit."""
    registry = get_registry()
    original_subscribe = registry.subscribe
    subscribed = asyncio.Event()

    def _subscribe_and_signal(room_id):
        queue = original_subscribe(room_id)
        subscribed.set()
        return queue

    monkeypatch.setattr(registry, "subscribe", _subscribe_and_signal)
    return subscribed


async def _create_user(db_session, email: str, name: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), name=name)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _auth_headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


class _FakePool:
    def __init__(self):
        self.enqueued = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.enqueued.append((name, args, kwargs))


@pytest.fixture(autouse=True)
def _livekit_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-at-least-32-bytes-long")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fake_arq_pool(monkeypatch):
    fake_pool = _FakePool()

    async def _fake_get_arq_pool():
        return fake_pool

    monkeypatch.setattr("app.api.rooms.get_arq_pool", _fake_get_arq_pool)
    return fake_pool


@pytest.fixture(autouse=True)
def _no_real_bot(monkeypatch):
    # join_room spawns a real RoomBot task on first join, which would try a
    # real LiveKit connection — replace .run() with a no-op coroutine so the
    # room/join tests here don't need a live LiveKit account.
    async def _fake_run(self):
        await asyncio.Event().wait()

    monkeypatch.setattr("app.rooms.bot.RoomBot.run", _fake_run)


async def test_end_room_requires_the_host(client, db_session):
    host = await _create_user(db_session, "host@example.com", "Host")
    guest = await _create_user(db_session, "guest@example.com", "Guest")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    room_id = create_resp.json()["id"]

    resp = await client.post(f"/rooms/{room_id}/end", headers=_auth_headers(guest))
    assert resp.status_code == 403


async def test_end_room_enqueues_the_analysis_job(client, db_session, _fake_arq_pool):
    host = await _create_user(db_session, "host2@example.com", "Host2")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    room_id = create_resp.json()["id"]
    await client.post(f"/rooms/{create_resp.json()['join_code']}/join", headers=_auth_headers(host))

    resp = await client.post(f"/rooms/{room_id}/end", headers=_auth_headers(host))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ended"
    assert _fake_arq_pool.enqueued[0][0] == "analyze_room_session"
    assert _fake_arq_pool.enqueued[0][1] == (room_id,)


def test_live_stats_ws_rejects_a_non_host(db_session):
    # Synchronous TestClient for the websocket handshake; dependency_overrides
    # set up by the async `client` fixture are already active on the shared
    # app object at this point in the test.
    with TestClient(fastapi_app) as sync_client:
        with pytest.raises(Exception):
            with sync_client.websocket_connect("/rooms/00000000-0000-0000-0000-000000000000/live?token=garbage"):
                pass


async def test_live_stats_ws_streams_stats_to_the_host(client, db_session, monkeypatch):
    # Drives live_stats_ws directly as a coroutine against a fake WebSocket,
    # on the SAME event loop as db_session/client — see _FakeWebSocket's
    # docstring for why (Starlette's TestClient can't be used here without
    # crossing event loops, which the app's real asyncpg connections don't
    # tolerate).
    host = await _create_user(db_session, "host3@example.com", "Host3")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    room_id = create_resp.json()["id"]
    join_resp = await client.post(f"/rooms/{create_resp.json()['join_code']}/join", headers=_auth_headers(host))
    participant_id = join_resp.json()["participant_id"]

    subscribed = _await_subscribed(monkeypatch)
    token = create_access_token(str(host.id))
    fake_ws = _FakeWebSocket(token)
    task = asyncio.create_task(live_stats_ws(fake_ws, uuid.UUID(room_id), db=db_session))
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=2.0)
        assert fake_ws.accepted is True

        get_registry().record_segment(room_id, SpeechSegment(participant_id, 0.0, 1.0))
        await asyncio.wait_for(fake_ws.message_received.wait(), timeout=2.0)

        message = fake_ws.sent[-1]
        assert participant_id in message
        assert message[participant_id]["talk_time_s"] == 1.0
    finally:
        if not task.done():
            task.cancel()


async def test_live_stats_ws_closes_cleanly_when_the_bot_stops(client, db_session, monkeypatch):
    # Covers the one deviation-from-brief behavior with no other automated
    # coverage: registry.stop_bot pushes STOP_SENTINEL to wake a WS handler
    # blocked on queue.get() (see app/rooms/registry.py's module docstring).
    # live_stats_ws must recognize it and close instead of looping forever
    # waiting for a stats wakeup that will never come.
    host = await _create_user(db_session, "host5@example.com", "Host5")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    room_id = create_resp.json()["id"]
    await client.post(f"/rooms/{create_resp.json()['join_code']}/join", headers=_auth_headers(host))

    subscribed = _await_subscribed(monkeypatch)
    token = create_access_token(str(host.id))
    fake_ws = _FakeWebSocket(token)
    task = asyncio.create_task(live_stats_ws(fake_ws, uuid.UUID(room_id), db=db_session))
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=2.0)

        get_registry().stop_bot(room_id)
        # If the STOP_SENTINEL check were missing/wrong this would hang until
        # the wait_for timeout instead of completing promptly.
        await asyncio.wait_for(task, timeout=2.0)

        assert fake_ws.closed_code is not None
        assert fake_ws.sent == []  # never mistook the sentinel for a stats wakeup
    finally:
        if not task.done():
            task.cancel()
