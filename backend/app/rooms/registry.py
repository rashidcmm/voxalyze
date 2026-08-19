"""In-process registry of live rooms: which RoomBot task is running for a
room, the speech segments recorded so far, and a fan-out for the host's live
WebSocket dashboard. Lives in the FastAPI app's own process/event loop — fine
at this project's scale (<=6 participants per room); a multi-process deploy
would need this moved to Redis instead (already used for the ARQ queue).

Subscriber queues carry two kinds of wakeup: `None` is a plain "a segment was
recorded, go recompute stats" ping (see `_broadcast`); `STOP_SENTINEL` is a
distinct close signal pushed by `stop_bot` so a WS handler blocked on
`queue.get()` wakes up and can shut the connection down instead of hanging
forever once the room's bot has stopped. Consumers (Task 8's WS handler)
must check `is STOP_SENTINEL` and stop reading from the queue when they see it.
"""
import asyncio

from app.rooms.live_stats import ParticipantStats, SpeechSegment, compute_participant_stats

STOP_SENTINEL = object()  # pushed to subscriber queues when a room's bot stops; see module docstring


class RoomRegistry:
    def __init__(self):
        self._segments: dict[str, list[SpeechSegment]] = {}
        self._bot_tasks: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def record_segment(self, room_id: str, segment: SpeechSegment) -> None:
        self._segments.setdefault(room_id, []).append(segment)
        self._broadcast(room_id)

    def segments_for(self, room_id: str) -> list[SpeechSegment]:
        return list(self._segments.get(room_id, []))

    def live_stats(self, room_id: str, participant_ids: list[str], elapsed_s: float) -> dict[str, ParticipantStats]:
        return compute_participant_stats(self.segments_for(room_id), participant_ids, elapsed_s)

    def register_bot_task(self, room_id: str, task: asyncio.Task) -> None:
        self._bot_tasks[room_id] = task

    def stop_bot(self, room_id: str) -> None:
        task = self._bot_tasks.pop(room_id, None)
        if task is not None:
            task.cancel()
        # Drop the room's segments with the rest of its state, or this dict
        # grows forever in a long-lived API process. Safe to clear here:
        # end_room's only work after stop_bot() is DB writes plus enqueuing
        # analyze_room_session, which reads room_transcript_segments from
        # Postgres, not this registry; and the WS handler woken by the
        # sentinel below breaks out of its loop without recomputing stats.
        self._segments.pop(room_id, None)
        # Wake any WS handler blocked on queue.get() with the close sentinel
        # before dropping the subscribers, so it can shut down cleanly instead
        # of hanging forever waiting for a segment that will never come.
        for queue in self._subscribers.pop(room_id, set()):
            queue.put_nowait(STOP_SENTINEL)

    def subscribe(self, room_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(room_id, set()).add(queue)
        return queue

    def unsubscribe(self, room_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(room_id, set()).discard(queue)

    def _broadcast(self, room_id: str) -> None:
        for queue in self._subscribers.get(room_id, set()):
            queue.put_nowait(None)  # a wakeup ping; the WS handler (Task 8) recomputes and sends stats


_registry = RoomRegistry()


def get_registry() -> RoomRegistry:
    return _registry
