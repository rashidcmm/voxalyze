import asyncio

import pytest

from app.rooms.live_stats import SpeechSegment
from app.rooms.registry import STOP_SENTINEL, RoomRegistry


def test_record_segment_is_reflected_in_live_stats():
    registry = RoomRegistry()
    registry.record_segment("room-1", SpeechSegment("a", 0.0, 5.0))
    stats = registry.live_stats("room-1", ["a", "b"], elapsed_s=10.0)
    assert stats["a"].talk_time_s == 5.0
    assert stats["b"].talk_time_s == 0.0


def test_stop_bot_cancels_the_registered_task():
    registry = RoomRegistry()

    async def _noop():
        await asyncio.sleep(10)

    async def _run():
        task = asyncio.ensure_future(_noop())
        registry.register_bot_task("room-2", task)
        registry.stop_bot("room-2")
        await asyncio.sleep(0)  # let cancellation propagate
        assert task.cancelled()

    asyncio.run(_run())


async def test_subscribers_are_woken_up_when_a_segment_is_recorded():
    registry = RoomRegistry()
    queue = registry.subscribe("room-3")
    registry.record_segment("room-3", SpeechSegment("a", 0.0, 1.0))
    await asyncio.wait_for(queue.get(), timeout=1.0)  # doesn't raise/timeout -> broadcast worked


def test_unsubscribe_stops_further_wakeups():
    registry = RoomRegistry()
    queue = registry.subscribe("room-4")
    registry.unsubscribe("room-4", queue)
    registry.record_segment("room-4", SpeechSegment("a", 0.0, 1.0))
    assert queue.empty()


async def test_stop_bot_wakes_a_subscriber_with_the_close_sentinel():
    registry = RoomRegistry()
    queue = registry.subscribe("room-5")

    registry.stop_bot("room-5")

    item = await asyncio.wait_for(queue.get(), timeout=1.0)  # doesn't hang -> stop_bot woke it
    assert item is STOP_SENTINEL
    assert item is not None  # distinguishable from a normal broadcast wakeup ping


def test_stop_bot_frees_the_rooms_segments():
    """_segments used to be left behind by stop_bot, so a long-lived API
    process accumulated every segment of every room it had ever hosted."""
    registry = RoomRegistry()
    registry.record_segment("room-6", SpeechSegment("a", 0.0, 1.0))
    assert registry.segments_for("room-6") != []

    registry.stop_bot("room-6")

    assert registry.segments_for("room-6") == []
    assert "room-6" not in registry._segments
