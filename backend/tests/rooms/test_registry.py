import asyncio

import pytest

from app.rooms.live_stats import SpeechSegment
from app.rooms.registry import RoomRegistry


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
