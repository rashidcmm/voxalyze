# Multi-Party GD Room — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend for a live, multi-party GD room — room creation/join, LiveKit-based audio/video, live per-speaker transcription and talk-time/interruption analytics, and a post-session report — per `docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md`.

**Architecture:** New `app/rooms/` package (parallel to the existing `app/scoring/`/`app/metrics/` packages) holds room-specific logic: a pure deterministic analytics engine, an energy-based VAD, an Azure streaming-STT bridge, an in-process "room bot" that joins each LiveKit room server-side to subscribe to every participant's audio track, and an in-memory live-stats registry. New `rooms`/`room_participants`/`room_transcript_segments`/`room_reports` tables follow the existing `sessions`/`transcripts`/`model_scores` pattern. A new ARQ job (`analyze_room_session`) finalizes the post-session report, mirroring `app/jobs/transcription.py`/`scoring.py`'s idempotency + isolated-failure conventions.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + Alembic + ARQ (existing) · `livekit`/`livekit-api` (new — room media + server-side track subscription) · `azure-cognitiveservices-speech` (new — real-time streaming STT; the rest of this project's Azure usage is a plain `requests` REST call, but Azure's streaming protocol is a proprietary WebSocket framing only the SDK owns) · `pytest`/`pytest-asyncio`/`httpx` (new — this repo has no automated test suite yet; introduced here since a multi-speaker interruption/dominance heuristic is exactly the kind of thing that needs unit tests to trust, per the design spec's testing plan)

## Global Constraints

- This is **Plan 1 of 2** for spec sub-project A (`docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md`). It covers the backend only — room data model, room/join API, the LiveKit room bot, live Azure streaming transcription, the live-stats engine, the post-session LLM pass, and the report API. A second plan covers the Next.js room UI (join/call screens, host live dashboard, report page) and is written after this one is implemented and manually verified against real LiveKit Cloud + Azure accounts.
- Max participants per room: 6 (`docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md` Goals).
- Anonymous-mode rooms never publish video and stay alias-labeled end-to-end, including in the post-session report (spec: Data model, Anonymous-mode privacy).
- Video is never recorded; only audio is persisted (spec: Non-goals).
- The room bot runs in-process (spawned via `asyncio.create_task` from the FastAPI app, tracked in an in-memory registry) rather than as a separate daemon — appropriate at ≤6 participants/room and consistent with this project's single-process, local-first dev setup elsewhere.
- Every external-service scorer/dependency (LiveKit, Azure) must degrade gracefully when not configured — same `ScorerNotConfigured`/`status: "not_configured"` isolation pattern already used by `app/scoring/pipeline.py`, never a hard crash.
- Follow existing conventions exactly: SQLAlchemy 2.0 `Mapped`/`mapped_column` style, `native_enum=False` string enums (see `app/models/session.py`), Pydantic schemas with `from_attributes = True`, ARQ jobs with an idempotency guard + exponential-backoff `Retry` (see `app/jobs/transcription.py`).
- **Known limitation carried into Plan 2:** anonymous-mode "no video" is enforced by the frontend simply never publishing a camera track, not by a LiveKit server-side grant restriction — this backend plan didn't find high-confidence documentation for the exact Python SDK call to lock that down at the token level, and guessing at an unverified API surface here would risk a worse bug than the current honest limitation. Fine for a trusted small-group practice tool; worth hardening later if this ever needs to resist an adversarial participant.
- **One-time local setup this plan assumes:** a `gdtrainer_test` Postgres database exists (`docker exec -it gdtrainer_postgres psql -U gdtrainer -c "CREATE DATABASE gdtrainer_test;"`) — the test suite runs against real Postgres (this project uses Postgres-specific types like `UUID`), not SQLite.

---

### Task 1: Live-stats engine (pure functions) + pytest scaffold

This repo has no test suite yet. Since this task's code is pure functions (no DB, no network), it's the natural place to introduce `pytest` with zero extra fixture complexity.

**Files:**
- Create: `backend/pytest.ini`
- Create: `backend/app/rooms/__init__.py`
- Create: `backend/app/rooms/live_stats.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/rooms/__init__.py`
- Create: `backend/tests/rooms/test_live_stats.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `SpeechSegment(participant_id: str, start_s: float, end_s: float)` (frozen dataclass, `.duration_s` property); `ParticipantStats(participant_id, talk_time_s, talk_time_pct, turn_count, interruptions_made, interruptions_received, longest_monologue_s, silence_pct)` (frozen dataclass); `compute_participant_stats(segments: list[SpeechSegment], participant_ids: list[str], session_duration_s: float) -> dict[str, ParticipantStats]`; `compute_session_dominance(stats_by_participant: dict[str, ParticipantStats]) -> float`. Later tasks (7, 10) import all four names from `app.rooms.live_stats`.

- [ ] **Step 1: Add pytest to requirements and create the pytest config**

Append to `backend/requirements.txt`:

```
# Testing (Rooms MVP — first automated test suite in this repo)
pytest==8.3.4
```

Create `backend/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pip install -r requirements.txt`

- [ ] **Step 2: Write failing tests for talk-time and turn counting**

Create `backend/app/rooms/__init__.py` (empty file).

Create `backend/tests/__init__.py` and `backend/tests/rooms/__init__.py` (both empty).

Create `backend/tests/rooms/test_live_stats.py`:

```python
import pytest

from app.rooms.live_stats import SpeechSegment, compute_participant_stats


def test_talk_time_split_evenly_between_two_non_overlapping_speakers():
    segments = [
        SpeechSegment("a", 0.0, 10.0),
        SpeechSegment("b", 10.0, 20.0),
    ]
    stats = compute_participant_stats(segments, ["a", "b"], session_duration_s=20.0)
    assert stats["a"].talk_time_s == 10.0
    assert stats["a"].talk_time_pct == 50.0
    assert stats["b"].talk_time_pct == 50.0


def test_participant_with_no_segments_gets_zeroed_stats():
    segments = [SpeechSegment("a", 0.0, 20.0)]
    stats = compute_participant_stats(segments, ["a", "b"], session_duration_s=20.0)
    assert stats["b"].talk_time_s == 0.0
    assert stats["b"].talk_time_pct == 0.0
    assert stats["b"].silence_pct == 100.0


def test_short_gap_between_same_speaker_segments_merges_into_one_turn():
    # 0.3s gap, below TURN_MERGE_GAP_S (0.5s) — counts as one continuous turn.
    # The raw VAD layer (Task 5) already absorbs true micro-pauses via its own
    # ~300ms hangover before segments ever reach this layer, so a merge here
    # is a deliberate "still their turn" call — the merged span (including the
    # brief gap) counts as talk time, not just the sum of the two sub-spans.
    segments = [SpeechSegment("a", 0.0, 5.0), SpeechSegment("a", 5.3, 8.0)]
    stats = compute_participant_stats(segments, ["a"], session_duration_s=8.0)
    assert stats["a"].turn_count == 1
    assert stats["a"].talk_time_s == 8.0  # full merged span, gap included


def test_long_gap_between_same_speaker_segments_counts_as_two_turns():
    segments = [SpeechSegment("a", 0.0, 5.0), SpeechSegment("a", 7.0, 8.0)]
    stats = compute_participant_stats(segments, ["a"], session_duration_s=8.0)
    assert stats["a"].turn_count == 2
    assert stats["a"].talk_time_s == 6.0  # gap itself isn't counted as talk time


def test_longest_monologue_is_the_longest_single_turn():
    segments = [SpeechSegment("a", 0.0, 2.0), SpeechSegment("a", 10.0, 16.0)]
    stats = compute_participant_stats(segments, ["a"], session_duration_s=20.0)
    assert stats["a"].longest_monologue_s == 6.0
```

- [ ] **Step 2b: Run tests to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_live_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.live_stats'`

- [ ] **Step 3: Implement talk-time/turn-merging logic**

Create `backend/app/rooms/live_stats.py`:

```python
"""Deterministic multi-speaker analytics for a GD room — the group analogue
of app/metrics/fluency.py. Operates purely on speech segments (no DB, no
network), so every heuristic here is unit-testable against synthetic,
engineered overlap patterns before it ever touches real audio — see the
testing plan in docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md.
"""
from dataclasses import dataclass

INTERRUPTION_OVERLAP_THRESHOLD_S = 0.3
INTERRUPTION_YIELD_WINDOW_S = 2.0
TURN_MERGE_GAP_S = 0.5


@dataclass(frozen=True)
class SpeechSegment:
    """One continuous stretch where a participant's VAD was active."""

    participant_id: str
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class ParticipantStats:
    participant_id: str
    talk_time_s: float
    talk_time_pct: float
    turn_count: int
    interruptions_made: int
    interruptions_received: int
    longest_monologue_s: float
    silence_pct: float


def merge_same_speaker_segments(segments: list[SpeechSegment]) -> list[SpeechSegment]:
    """Merge consecutive same-speaker segments separated by a short gap
    (< TURN_MERGE_GAP_S) into one turn, so a person pausing mid-sentence isn't
    counted as two turns."""
    by_participant: dict[str, list[SpeechSegment]] = {}
    for seg in segments:
        by_participant.setdefault(seg.participant_id, []).append(seg)

    merged: list[SpeechSegment] = []
    for participant_id, segs in by_participant.items():
        segs = sorted(segs, key=lambda s: s.start_s)
        current = segs[0]
        for nxt in segs[1:]:
            if nxt.start_s - current.end_s <= TURN_MERGE_GAP_S:
                current = SpeechSegment(participant_id, current.start_s, max(current.end_s, nxt.end_s))
            else:
                merged.append(current)
                current = nxt
        merged.append(current)
    return sorted(merged, key=lambda s: s.start_s)


def compute_participant_stats(
    segments: list[SpeechSegment],
    participant_ids: list[str],
    session_duration_s: float,
) -> dict[str, ParticipantStats]:
    merged = merge_same_speaker_segments(segments)
    interruption_pairs = detect_interruptions(merged)

    talk_time_s = {pid: 0.0 for pid in participant_ids}
    turn_count = {pid: 0 for pid in participant_ids}
    longest_monologue = {pid: 0.0 for pid in participant_ids}
    for seg in merged:
        talk_time_s[seg.participant_id] = talk_time_s.get(seg.participant_id, 0.0) + seg.duration_s
        turn_count[seg.participant_id] = turn_count.get(seg.participant_id, 0) + 1
        longest_monologue[seg.participant_id] = max(
            longest_monologue.get(seg.participant_id, 0.0), seg.duration_s
        )

    interruptions_made = {pid: 0 for pid in participant_ids}
    interruptions_received = {pid: 0 for pid in participant_ids}
    for interrupter, interrupted in interruption_pairs:
        interruptions_made[interrupter] = interruptions_made.get(interrupter, 0) + 1
        interruptions_received[interrupted] = interruptions_received.get(interrupted, 0) + 1

    talk_time_pct = {
        pid: (talk_time_s[pid] / session_duration_s * 100.0) if session_duration_s > 0 else 0.0
        for pid in participant_ids
    }

    return {
        pid: ParticipantStats(
            participant_id=pid,
            talk_time_s=round(talk_time_s[pid], 2),
            talk_time_pct=round(talk_time_pct[pid], 2),
            turn_count=turn_count[pid],
            interruptions_made=interruptions_made[pid],
            interruptions_received=interruptions_received[pid],
            longest_monologue_s=round(longest_monologue[pid], 2),
            silence_pct=round(100.0 - talk_time_pct[pid], 2),
        )
        for pid in participant_ids
    }


def detect_interruptions(segments: list[SpeechSegment]) -> list[tuple[str, str]]:
    return []  # implemented in Step 5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_live_stats.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Write failing tests for interruption detection**

Append to `backend/tests/rooms/test_live_stats.py`:

```python
def test_b_interrupting_a_is_detected_both_ways():
    # a talks 0-5s; b starts at 4s (1s overlap, well over the 0.3s threshold)
    # and a stops at 4.5s (0.5s after b started, within the 2s yield window)
    segments = [SpeechSegment("a", 0.0, 4.5), SpeechSegment("b", 4.0, 8.0)]
    stats = compute_participant_stats(segments, ["a", "b"], session_duration_s=8.0)
    assert stats["b"].interruptions_made == 1
    assert stats["a"].interruptions_received == 1
    assert stats["a"].interruptions_made == 0
    assert stats["b"].interruptions_received == 0


def test_brief_overlap_below_threshold_is_not_an_interruption():
    # only 0.1s overlap — below INTERRUPTION_OVERLAP_THRESHOLD_S (0.3s), a
    # backchannel/agreement noise, not a real interruption
    segments = [SpeechSegment("a", 0.0, 4.1), SpeechSegment("b", 4.0, 8.0)]
    stats = compute_participant_stats(segments, ["a", "b"], session_duration_s=8.0)
    assert stats["b"].interruptions_made == 0


def test_a_continuing_long_after_b_starts_is_not_counted_as_interrupted():
    # b starts at 4s but a keeps going past the 2s yield window (until 8s) —
    # a wasn't actually cut off, so this isn't counted as an interruption
    segments = [SpeechSegment("a", 0.0, 8.0), SpeechSegment("b", 4.0, 10.0)]
    stats = compute_participant_stats(segments, ["a", "b"], session_duration_s=10.0)
    assert stats["a"].interruptions_received == 0
```

- [ ] **Step 6: Run to verify the new tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_live_stats.py -v`
Expected: FAIL on the three new tests (interruption counts come back 0 — `detect_interruptions` is still a stub)

- [ ] **Step 7: Implement interruption detection**

In `backend/app/rooms/live_stats.py`, replace the stub:

```python
def detect_interruptions(segments: list[SpeechSegment]) -> list[tuple[str, str]]:
    """Returns (interrupter_id, interrupted_id) pairs. B interrupts A when B
    starts while A is still speaking (overlap >= threshold) and A stops
    speaking within a short window after B started."""
    segments = sorted(segments, key=lambda s: s.start_s)
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(segments):
        for b in segments[i + 1 :]:
            if b.start_s >= a.end_s:
                break  # sorted by start_s — no more overlaps with a beyond this point
            if b.participant_id == a.participant_id:
                continue
            overlap = min(a.end_s, b.end_s) - b.start_s
            if overlap >= INTERRUPTION_OVERLAP_THRESHOLD_S and a.end_s - b.start_s <= INTERRUPTION_YIELD_WINDOW_S:
                pairs.append((b.participant_id, a.participant_id))
    return pairs
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_live_stats.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 9: Write failing tests for the dominance index**

Append to `backend/tests/rooms/test_live_stats.py`:

```python
from app.rooms.live_stats import ParticipantStats, compute_session_dominance


def test_dominance_index_is_zero_for_an_even_split():
    stats = {
        "a": ParticipantStats("a", 10.0, 50.0, 1, 0, 0, 10.0, 50.0),
        "b": ParticipantStats("b", 10.0, 50.0, 1, 0, 0, 10.0, 50.0),
    }
    assert compute_session_dominance(stats) == 0.0


def test_dominance_index_approaches_one_when_one_participant_dominates():
    stats = {
        "a": ParticipantStats("a", 20.0, 100.0, 1, 0, 0, 20.0, 0.0),
        "b": ParticipantStats("b", 0.0, 0.0, 0, 0, 0, 0.0, 100.0),
    }
    # Gini coefficient for one participant at 100%, n=2: (n-1)/n = 0.5
    assert compute_session_dominance(stats) == pytest.approx(0.5)


def test_dominance_index_handles_a_single_participant():
    stats = {"a": ParticipantStats("a", 10.0, 100.0, 1, 0, 0, 10.0, 0.0)}
    assert compute_session_dominance(stats) == 0.0
```

- [ ] **Step 10: Run to verify the new tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_live_stats.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_session_dominance'`

- [ ] **Step 11: Implement the dominance index**

Append to `backend/app/rooms/live_stats.py`:

```python
def compute_session_dominance(stats_by_participant: dict[str, "ParticipantStats"]) -> float:
    """Gini coefficient of the talk-time-percentage distribution: 0.0 = a
    perfectly even split, approaching (n-1)/n = one participant did all the
    talking. Same family of measure as published speaking-time/dominance
    research (see the design spec's viability note)."""
    values = [s.talk_time_pct for s in stats_by_participant.values()]
    n = len(values)
    if n <= 1 or sum(values) == 0:
        return 0.0
    total_abs_diff = sum(abs(x - y) for x in values for y in values)
    return round(total_abs_diff / (2 * n * sum(values)), 3)
```

- [ ] **Step 12: Run the full test file to verify everything passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_live_stats.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 13: Commit**

```bash
git add backend/pytest.ini backend/requirements.txt backend/app/rooms/__init__.py \
        backend/app/rooms/live_stats.py backend/tests/__init__.py \
        backend/tests/rooms/__init__.py backend/tests/rooms/test_live_stats.py
git commit -m "Rooms: deterministic live-stats engine (talk-time, turns, interruptions, dominance)

First automated test suite in this repo (pytest) — introduced here because
these heuristics need engineered-overlap unit tests to trust, per the
design spec's testing plan.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: LiveKit config + access-token issuance

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/requirements.txt`
- Create: `backend/app/rooms/livekit_tokens.py`
- Create: `backend/tests/rooms/test_livekit_tokens.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `RoomsNotConfigured` (exception); `issue_participant_token(*, room_name: str, identity: str, display_name: str) -> str`; `issue_bot_token(*, room_name: str) -> str`. Used by Tasks 4 and 7.

- [ ] **Step 1: Add LiveKit dependency and settings**

Append to `backend/requirements.txt`:

```
livekit-api==1.2.0
livekit==1.1.14
```

In `backend/app/core/config.py`, add to the `Settings` class (after the `embedding_model` line):

```python
    # Rooms (Sub-project A: multi-party GD room MVP)
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    room_max_participants: int = 6
```

Append to `backend/.env.example`:

```
# --- Rooms (multi-party GD room MVP) ---
# LiveKit Cloud: https://cloud.livekit.io -> create a project (free tier) ->
# Settings -> Keys. LIVEKIT_URL is the project's wss:// URL.
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pip install -r requirements.txt`

- [ ] **Step 2: Write failing tests**

Create `backend/tests/rooms/test_livekit_tokens.py`:

```python
import pytest
from jose import jwt as jose_jwt

from app.core.config import get_settings
from app.rooms.livekit_tokens import RoomsNotConfigured, issue_bot_token, issue_participant_token


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


def test_issue_participant_token_raises_when_not_configured(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "")
    get_settings.cache_clear()
    with pytest.raises(RoomsNotConfigured):
        issue_participant_token(room_name="abc123", identity="user-1", display_name="Alex")


def test_issue_participant_token_returns_a_jwt_with_the_right_claims(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-at-least-32-bytes-long")
    get_settings.cache_clear()
    token = issue_participant_token(room_name="abc123", identity="user-1", display_name="Alex")
    claims = jose_jwt.get_unverified_claims(token)
    assert claims["sub"] == "user-1"
    assert claims["video"]["room"] == "abc123"
    assert claims["video"]["roomJoin"] is True


def test_issue_bot_token_uses_a_bot_prefixed_identity(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-at-least-32-bytes-long")
    get_settings.cache_clear()
    token = issue_bot_token(room_name="abc123")
    claims = jose_jwt.get_unverified_claims(token)
    assert claims["sub"] == "bot:abc123"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_livekit_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.livekit_tokens'`

- [ ] **Step 4: Implement**

Create `backend/app/rooms/livekit_tokens.py`:

```python
"""LiveKit access-token issuance for room participants.

Same "fail loud with a clear, actionable message when not configured yet"
pattern as app/scoring/pronunciation.py's ScorerNotConfigured, so a missing
LIVEKIT_API_KEY/SECRET degrades to a clear 503 at the API layer (see
app/api/rooms.py) rather than an unhandled crash.
"""
from livekit import api

from app.core.config import get_settings


class RoomsNotConfigured(RuntimeError):
    """Raised when LIVEKIT_API_KEY/LIVEKIT_API_SECRET aren't set."""


def issue_participant_token(*, room_name: str, identity: str, display_name: str) -> str:
    settings = get_settings()
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise RoomsNotConfigured(
            "LIVEKIT_API_KEY / LIVEKIT_API_SECRET not set — see README for how to get a free LiveKit Cloud project"
        )
    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
    )
    return token.to_jwt()


def issue_bot_token(*, room_name: str) -> str:
    """Token for the backend's own server-side participant (see
    app/rooms/bot.py) — identity is prefixed so it's never confused with a
    real participant when the frontend lists who's in the room."""
    return issue_participant_token(room_name=room_name, identity=f"bot:{room_name}", display_name="analysis-bot")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_livekit_tokens.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/config.py backend/.env.example backend/requirements.txt \
        backend/app/rooms/livekit_tokens.py backend/tests/rooms/test_livekit_tokens.py
git commit -m "Rooms: LiveKit config and access-token issuance

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Room data models + migration

No automated test for this task (matches how `session.py`/`model_scores.py` and their migrations have no dedicated tests in this repo either) — verified by running the migration and importing the models cleanly. Task 4 exercises these models through the API.

**Files:**
- Create: `backend/app/models/room.py`
- Create: `backend/app/models/room_participant.py`
- Create: `backend/app/models/room_transcript_segment.py`
- Create: `backend/app/models/room_report.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/alembic/env.py`
- Create: `backend/alembic/versions/f1a9c3d7e2b4_create_rooms_tables.py`

**Interfaces:**
- Produces: `Room`, `RoomMode`, `RoomStatus` (from `app.models.room`); `RoomParticipant` (from `app.models.room_participant`); `RoomTranscriptSegment` (from `app.models.room_transcript_segment`); `RoomReport` (from `app.models.room_report`). Used by Tasks 4, 7, 8, 10, 11.

- [ ] **Step 1: Create the `Room` model**

Create `backend/app/models/room.py`:

```python
import enum
import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomMode(str, enum.Enum):
    IDENTIFIED = "identified"
    ANONYMOUS = "anonymous"


class RoomStatus(str, enum.Enum):
    WAITING = "waiting"
    LIVE = "live"
    ENDED = "ended"
    ANALYZED = "analyzed"


def _generate_join_code() -> str:
    # 8 chars, unambiguous alphabet (no 0/O/1/I) — meant to be read aloud or
    # typed over a call.
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(8))


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # native_enum=False: plain VARCHAR + CHECK constraint, same reasoning as
    # SessionStatus in app/models/session.py.
    mode: Mapped[RoomMode] = mapped_column(
        Enum(RoomMode, name="room_mode", native_enum=False, length=20), nullable=False
    )
    status: Mapped[RoomStatus] = mapped_column(
        Enum(RoomStatus, name="room_status", native_enum=False, length=20),
        default=RoomStatus.WAITING,
        nullable=False,
        index=True,
    )
    join_code: Mapped[str] = mapped_column(String(8), unique=True, index=True, default=_generate_join_code)
    max_participants: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 2: Create the `RoomParticipant` model**

Create `backend/app/models/room_participant.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomParticipant(Base):
    """The real user_id is always recorded server-side — even in anonymous
    rooms — for the participant's own progress history and abuse prevention.

    alias_name is always populated at join time and is what's shown as the
    participant's display name for the rest of the room's lifecycle: in
    identified rooms it's a snapshot of the user's account name at join time
    (so a later name change doesn't retroactively alter a past session's
    report); in anonymous rooms it's a generated "Speaker N" label. Either
    way, callers never need to fall back to a live User lookup to label a
    participant.
    """

    __tablename__ = "room_participants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    alias_name: Mapped[str] = mapped_column(String(50), nullable=False)
    livekit_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    audio_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: Create the `RoomTranscriptSegment` model**

Create `backend/app/models/room_transcript_segment.py`:

```python
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomTranscriptSegment(Base):
    __tablename__ = "room_transcript_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("room_participants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

- [ ] **Step 4: Create the `RoomReport` model**

Create `backend/app/models/room_report.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RoomReport(Base):
    """One row per room. participant_stats is a JSON object keyed by
    participant_id, values shaped like app.rooms.live_stats.ParticipantStats.
    qualitative_* follows the same status/result/error isolation pattern as
    model_scores.py: a failed or not-yet-configured LLM pass never blocks the
    deterministic stats from being available.
    """

    __tablename__ = "room_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    participant_stats: Mapped[dict] = mapped_column(JSON, nullable=False)
    dominance_index: Mapped[float] = mapped_column(Float, nullable=False)

    qualitative_status: Mapped[str] = mapped_column(String(20), nullable=False)
    qualitative_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    qualitative_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 5: Register the new models**

Replace `backend/app/models/__init__.py`:

```python
from app.models.user import User
from app.models.topic import Topic
from app.models.session import Session, SessionStatus
from app.models.transcript import Transcript, Word
from app.models.session_metrics import SessionMetrics
from app.models.model_scores import ModelScores
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.models.room_report import RoomReport

__all__ = [
    "User",
    "Topic",
    "Session",
    "SessionStatus",
    "Transcript",
    "Word",
    "SessionMetrics",
    "ModelScores",
    "Room",
    "RoomMode",
    "RoomStatus",
    "RoomParticipant",
    "RoomTranscriptSegment",
    "RoomReport",
]
```

In `backend/alembic/env.py`, update the model import line so autogenerate/`Base.metadata` sees the new tables too:

```python
from app.models import (  # noqa: E402,F401  (import so they register on Base.metadata)
    User, Topic, Session, Transcript, Word, SessionMetrics, ModelScores,
    Room, RoomParticipant, RoomTranscriptSegment, RoomReport,
)
```

(This replaces the existing single-line `from app.models import User, Topic, Session, Transcript, Word, SessionMetrics, ModelScores  # noqa: E402,F401  (...)` line in `env.py`.)

- [ ] **Step 6: Write the migration**

Create `backend/alembic/versions/f1a9c3d7e2b4_create_rooms_tables.py`:

```python
"""create rooms tables

Revision ID: f1a9c3d7e2b4
Revises: 85cb62ba58af
Create Date: 2026-08-11 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a9c3d7e2b4'
down_revision: Union[str, None] = '85cb62ba58af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rooms',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('host_user_id', sa.UUID(), nullable=False),
        sa.Column('mode', sa.Enum('IDENTIFIED', 'ANONYMOUS', name='room_mode', native_enum=False, length=20), nullable=False),
        sa.Column('status', sa.Enum('WAITING', 'LIVE', 'ENDED', 'ANALYZED', name='room_status', native_enum=False, length=20), nullable=False),
        sa.Column('join_code', sa.String(length=8), nullable=False),
        sa.Column('max_participants', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['host_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rooms_host_user_id'), 'rooms', ['host_user_id'], unique=False)
    op.create_index(op.f('ix_rooms_status'), 'rooms', ['status'], unique=False)
    op.create_index(op.f('ix_rooms_join_code'), 'rooms', ['join_code'], unique=True)

    op.create_table(
        'room_participants',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('room_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('alias_name', sa.String(length=50), nullable=False),
        sa.Column('livekit_identity', sa.String(length=200), nullable=False),
        sa.Column('audio_path', sa.String(length=1024), nullable=True),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_room_participants_room_id'), 'room_participants', ['room_id'], unique=False)
    op.create_index(op.f('ix_room_participants_user_id'), 'room_participants', ['user_id'], unique=False)

    op.create_table(
        'room_transcript_segments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('room_id', sa.UUID(), nullable=False),
        sa.Column('participant_id', sa.UUID(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('start_s', sa.Float(), nullable=False),
        sa.Column('end_s', sa.Float(), nullable=False),
        sa.Column('is_final', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['participant_id'], ['room_participants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_room_transcript_segments_room_id'), 'room_transcript_segments', ['room_id'], unique=False)
    op.create_index(op.f('ix_room_transcript_segments_participant_id'), 'room_transcript_segments', ['participant_id'], unique=False)

    op.create_table(
        'room_reports',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('room_id', sa.UUID(), nullable=False),
        sa.Column('participant_stats', sa.JSON(), nullable=False),
        sa.Column('dominance_index', sa.Float(), nullable=False),
        sa.Column('qualitative_status', sa.String(length=20), nullable=False),
        sa.Column('qualitative_result', sa.JSON(), nullable=True),
        sa.Column('qualitative_error', sa.Text(), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_room_reports_room_id'), 'room_reports', ['room_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_room_reports_room_id'), table_name='room_reports')
    op.drop_table('room_reports')
    op.drop_index(op.f('ix_room_transcript_segments_participant_id'), table_name='room_transcript_segments')
    op.drop_index(op.f('ix_room_transcript_segments_room_id'), table_name='room_transcript_segments')
    op.drop_table('room_transcript_segments')
    op.drop_index(op.f('ix_room_participants_user_id'), table_name='room_participants')
    op.drop_index(op.f('ix_room_participants_room_id'), table_name='room_participants')
    op.drop_table('room_participants')
    op.drop_index(op.f('ix_rooms_join_code'), table_name='rooms')
    op.drop_index(op.f('ix_rooms_status'), table_name='rooms')
    op.drop_index(op.f('ix_rooms_host_user_id'), table_name='rooms')
    op.drop_table('rooms')
```

- [ ] **Step 7: Run the migration against the dev database and verify**

Run:
```bash
cd backend
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -c "import app.models; print('models import cleanly')"
```
Expected: migration applies with no errors; the import line prints `models import cleanly`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/room.py backend/app/models/room_participant.py \
        backend/app/models/room_transcript_segment.py backend/app/models/room_report.py \
        backend/app/models/__init__.py backend/alembic/env.py \
        backend/alembic/versions/f1a9c3d7e2b4_create_rooms_tables.py
git commit -m "Rooms: data models and migration (rooms, room_participants, room_transcript_segments, room_reports)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Room creation & join API

Introduces the async DB test fixtures (`pytest-asyncio` + `httpx`) this and every later API/job test in this plan reuses. Requires the `gdtrainer_test` database from Global Constraints to exist.

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/app/schemas/room.py`
- Create: `backend/app/api/rooms.py`
- Create: `backend/tests/rooms/test_rooms_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: `Room`, `RoomMode`, `RoomStatus`, `RoomParticipant` (Task 3); `issue_participant_token`, `RoomsNotConfigured` (Task 2).
- Produces: `db_session_maker`, `db_session`, `client` pytest fixtures (in `conftest.py`) — reused by Tasks 8, 10, 11. `router` (FastAPI `APIRouter`, prefix `/rooms`) from `app.api.rooms`, mounted in `main.py`.

- [ ] **Step 1: Add test dependencies**

Append to `backend/requirements.txt`:

```
pytest-asyncio==0.25.2
httpx==0.28.1
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pip install -r requirements.txt`

- [ ] **Step 2: Write the shared DB/client test fixtures**

Create `backend/tests/conftest.py`:

```python
"""Shared pytest fixtures for API-level tests.

Runs against a real Postgres test database (same asyncpg/SQLAlchemy stack as
production — this project uses Postgres-specific types like UUID, not
sqlite). Each test is wrapped in an outer transaction that's rolled back
afterward via a SAVEPOINT (join_transaction_mode="create_savepoint"), so
tests never see each other's data even though the application code under
test calls session.commit() normally — see Task 10 for how ARQ job tests
reuse db_session_maker to share that same per-test transaction.
"""
import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers every model on Base.metadata
from app.core.db import Base, get_db
from app.main import app as fastapi_app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://gdtrainer:gdtrainer@localhost:5432/gdtrainer_test"
)
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session_maker(_schema):
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session_maker = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        yield session_maker
        await trans.rollback()


@pytest_asyncio.fixture
async def db_session(db_session_maker):
    async with db_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
```

- [ ] **Step 3: Write failing tests for room creation and joining**

Create `backend/tests/rooms/test_rooms_api.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_rooms_api.py -v`
Expected: FAIL (404 on `/rooms` — router doesn't exist yet)

- [ ] **Step 5: Write the schemas**

Create `backend/app/schemas/room.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.room import RoomMode, RoomStatus


class RoomCreate(BaseModel):
    mode: RoomMode
    max_participants: int = 6


class RoomResponse(BaseModel):
    id: uuid.UUID
    join_code: str
    mode: RoomMode
    status: RoomStatus
    max_participants: int
    host_user_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class RoomJoinResponse(BaseModel):
    room_id: uuid.UUID
    participant_id: uuid.UUID
    livekit_url: str
    livekit_token: str
    display_name: str
    mode: RoomMode
    max_participants: int
```

- [ ] **Step 6: Implement the router**

Create `backend/app/api/rooms.py`:

```python
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
        livekit_identity = f"{current_user.id}:{room.id}"
        participant = RoomParticipant(
            room_id=room.id, user_id=current_user.id, alias_name=alias, livekit_identity=livekit_identity
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
```

Modify `backend/app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, progress, rooms, sessions, topics

app = FastAPI(title="GD/Debate Speech Trainer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(topics.router)
app.include_router(sessions.router)
app.include_router(progress.router)
app.include_router(rooms.router)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_rooms_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 8: Run the full test suite so far**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (all tests from Tasks 1, 2, and 4)

- [ ] **Step 9: Commit**

```bash
git add backend/tests/conftest.py backend/app/schemas/room.py backend/app/api/rooms.py \
        backend/tests/rooms/test_rooms_api.py backend/app/main.py backend/requirements.txt
git commit -m "Rooms: room creation and join API

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Energy-based voice-activity detection (VAD)

**Files:**
- Create: `backend/app/rooms/vad.py`
- Create: `backend/tests/rooms/test_vad.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SAMPLE_RATE_HZ`, `FRAME_MS`, `FRAME_SAMPLES` (constants); `StreamingVAD(participant_id: str)` with `.push_frame(pcm_frame: np.ndarray) -> None`, `.flush() -> None`, `.closed_segments: list[tuple[float, float]]`. Used by Task 7.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/rooms/test_vad.py`:

```python
import numpy as np
import pytest

from app.rooms.vad import FRAME_SAMPLES, StreamingVAD

LOUD_FRAME = np.full(FRAME_SAMPLES, 20_000, dtype=np.int16)
SILENT_FRAME = np.zeros(FRAME_SAMPLES, dtype=np.int16)


def test_pure_silence_produces_no_segments():
    vad = StreamingVAD(participant_id="a")
    for _ in range(50):
        vad.push_frame(SILENT_FRAME)
    assert vad.closed_segments == []


def test_a_loud_stretch_followed_by_hangover_silence_closes_one_segment():
    vad = StreamingVAD(participant_id="a")
    for _ in range(10):  # 10 * 20ms = 200ms of speech
        vad.push_frame(LOUD_FRAME)
    for _ in range(20):  # past HANGOVER_FRAMES, closes the segment
        vad.push_frame(SILENT_FRAME)
    assert len(vad.closed_segments) == 1
    start_s, end_s = vad.closed_segments[0]
    assert start_s == pytest.approx(0.0)
    assert end_s == pytest.approx(0.2, abs=0.001)


def test_a_brief_dip_below_threshold_does_not_split_the_segment():
    vad = StreamingVAD(participant_id="a")
    for _ in range(5):
        vad.push_frame(LOUD_FRAME)
    for _ in range(3):  # brief dip, well under HANGOVER_FRAMES (15)
        vad.push_frame(SILENT_FRAME)
    for _ in range(5):
        vad.push_frame(LOUD_FRAME)
    for _ in range(20):
        vad.push_frame(SILENT_FRAME)
    assert len(vad.closed_segments) == 1  # one continuous segment, not two


def test_push_frame_rejects_the_wrong_frame_size():
    vad = StreamingVAD(participant_id="a")
    with pytest.raises(ValueError):
        vad.push_frame(np.zeros(100, dtype=np.int16))


def test_flush_closes_a_still_open_segment():
    vad = StreamingVAD(participant_id="a")
    for _ in range(5):
        vad.push_frame(LOUD_FRAME)
    assert vad.closed_segments == []  # nothing closed yet — still "speaking"
    vad.flush()
    assert len(vad.closed_segments) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_vad.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.vad'`

- [ ] **Step 3: Implement**

Create `backend/app/rooms/vad.py`:

```python
"""Lightweight energy-based voice-activity detection over a stream of PCM
audio frames — deliberately not a native VAD library (e.g. webrtcvad), to
avoid another native dependency on top of what LiveKit/Azure already need.
Good enough to turn "is this participant's mic producing speech-level audio
right now" into segments; not meant to be broadcast-grade.
"""
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE_HZ = 16_000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE_HZ * FRAME_MS // 1000  # 320 samples/frame at 16kHz/20ms
RMS_SPEECH_THRESHOLD = 300.0  # int16 RMS floor; silence/room noise sits well below this
HANGOVER_FRAMES = 15  # ~300ms of below-threshold audio before a segment is considered ended


@dataclass
class StreamingVAD:
    """Feed 20ms int16 PCM frames in order via push_frame(); closed_segments
    fills in with (start_s, end_s) tuples as speech ends. Stateful per
    participant — one instance per participant's audio track (see
    app/rooms/bot.py).

    A segment only closes once HANGOVER_FRAMES of silence confirm speech has
    really ended (so a brief pause mid-sentence doesn't split it), but the
    recorded end_s is the moment speech actually stopped — the *first* silent
    frame of that run — not the later moment the hangover confirms it;
    otherwise every segment's end would be inflated by ~300ms."""

    participant_id: str
    closed_segments: list[tuple[float, float]] = field(default_factory=list)
    _elapsed_s: float = 0.0
    _speech_start_s: float | None = None
    _speech_end_candidate_s: float | None = None
    _silence_run: int = 0

    def push_frame(self, pcm_frame: np.ndarray) -> None:
        if pcm_frame.size != FRAME_SAMPLES:
            raise ValueError(f"expected {FRAME_SAMPLES}-sample frames, got {pcm_frame.size}")

        rms = float(np.sqrt(np.mean(pcm_frame.astype(np.float64) ** 2)))
        is_speech = rms >= RMS_SPEECH_THRESHOLD
        frame_start_s = self._elapsed_s
        self._elapsed_s += FRAME_MS / 1000.0

        if is_speech:
            self._silence_run = 0
            if self._speech_start_s is None:
                self._speech_start_s = frame_start_s
        elif self._speech_start_s is not None:
            if self._silence_run == 0:
                self._speech_end_candidate_s = frame_start_s  # true moment speech stopped
            self._silence_run += 1
            if self._silence_run >= HANGOVER_FRAMES:
                self.closed_segments.append((self._speech_start_s, self._speech_end_candidate_s))
                self._speech_start_s = None
                self._silence_run = 0

    def flush(self) -> None:
        """Call when the track ends — closes any still-open speech segment."""
        if self._speech_start_s is not None:
            end_s = self._speech_end_candidate_s if self._silence_run > 0 else self._elapsed_s
            self.closed_segments.append((self._speech_start_s, end_s))
            self._speech_start_s = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_vad.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rooms/vad.py backend/tests/rooms/test_vad.py
git commit -m "Rooms: energy-based voice-activity detection

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Azure streaming speech-to-text bridge

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/rooms/azure_stream.py`
- Create: `backend/tests/rooms/test_azure_stream.py`

**Interfaces:**
- Consumes: `ScorerNotConfigured` (from `app.scoring.types`, already exists).
- Produces: `TranscriptSegment(text: str, start_s: float, end_s: float)`; `parse_recognition_result(result_json: dict) -> TranscriptSegment | None`; `AzureStreamingTranscriber(loop, recognizer_factory=...)` with `.segments: asyncio.Queue[TranscriptSegment]`, `.push_pcm(data: bytes) -> None`, `.close() -> None`. Used by Task 7.

- [ ] **Step 1: Add the Azure Speech SDK dependency**

Append to `backend/requirements.txt`:

```
# Real-time streaming STT (unlike app/scoring/pronunciation.py's plain REST
# call, Azure's streaming protocol is a proprietary WebSocket framing only
# the SDK owns — worth the one native dependency).
azure-cognitiveservices-speech==1.50.0
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pip install -r requirements.txt`

- [ ] **Step 2: Write failing tests for JSON-result parsing**

Create `backend/tests/rooms/test_azure_stream.py`:

```python
import asyncio
import json

import pytest

from app.core.config import get_settings
from app.rooms.azure_stream import AzureStreamingTranscriber, parse_recognition_result
from app.scoring.types import ScorerNotConfigured


def test_parse_recognition_result_uses_word_level_timestamps():
    result_json = {
        "NBest": [{
            "Display": "hello world",
            "Words": [
                {"Word": "hello", "Offset": 10_000_000, "Duration": 5_000_000},
                {"Word": "world", "Offset": 16_000_000, "Duration": 5_000_000},
            ],
        }]
    }
    segment = parse_recognition_result(result_json)
    assert segment.text == "hello world"
    assert segment.start_s == pytest.approx(1.0)
    assert segment.end_s == pytest.approx(2.1)


def test_parse_recognition_result_returns_none_for_empty_text():
    assert parse_recognition_result({"NBest": [{"Display": ""}]}) is None


def test_parse_recognition_result_falls_back_to_utterance_offset_without_words():
    result_json = {"NBest": [{"Display": "hi"}], "Offset": 20_000_000, "Duration": 5_000_000}
    segment = parse_recognition_result(result_json)
    assert segment.start_s == pytest.approx(2.0)
    assert segment.end_s == pytest.approx(2.5)
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_azure_stream.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.azure_stream'`

- [ ] **Step 4: Implement result parsing and the transcriber class**

Create `backend/app/rooms/azure_stream.py`:

```python
"""Bridges Azure Speech's continuous streaming recognition (push-stream) into
an asyncio-friendly per-participant segment queue.

Unlike app/scoring/pronunciation.py's plain `requests` REST call, this uses
the native azure-cognitiveservices-speech SDK — Azure's real-time streaming
protocol is a proprietary WebSocket framing only the SDK owns; hand-rolling
it would be far more fragile than the one extra native dependency.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable

import azure.cognitiveservices.speech as speechsdk

from app.core.config import get_settings
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("rooms.azure_stream")

SAMPLE_RATE_HZ = 16_000


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_s: float
    end_s: float


def parse_recognition_result(result_json: dict) -> TranscriptSegment | None:
    """Azure's Detailed-format JSON -> (text, start_s, end_s), using the
    first/last word's offset+duration (100ns ticks) when word-level
    timestamps are enabled, falling back to the utterance-level Offset/Duration."""
    best = (result_json.get("NBest") or [{}])[0]
    text = best.get("Display") or result_json.get("DisplayText") or ""
    if not text:
        return None
    words = best.get("Words")
    if words:
        start_s = words[0]["Offset"] / 10_000_000
        last = words[-1]
        end_s = (last["Offset"] + last["Duration"]) / 10_000_000
    else:
        start_s = result_json.get("Offset", 0) / 10_000_000
        end_s = start_s + result_json.get("Duration", 0) / 10_000_000
    return TranscriptSegment(text=text, start_s=start_s, end_s=end_s)


def _default_recognizer_factory(key: str, region: str, push_stream):
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.output_format = speechsdk.OutputFormat.Detailed
    speech_config.request_word_level_timestamps()
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    return speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)


class AzureStreamingTranscriber:
    """One instance per room participant (see app/rooms/bot.py). `segments`
    is an asyncio.Queue the caller drains to persist RoomTranscriptSegment
    rows. The SDK's recognized callback fires on its own background thread,
    so it's bridged into the event loop via call_soon_threadsafe rather than
    touched directly from asyncio code.

    `recognizer_factory` is injectable so tests can supply a fake recognizer
    without a real Azure connection — the real PushAudioInputStream/
    AudioStreamFormat objects constructed below don't touch the network
    themselves, only starting recognition does."""

    def __init__(self, loop: asyncio.AbstractEventLoop, recognizer_factory: Callable = _default_recognizer_factory):
        settings = get_settings()
        if not settings.azure_speech_key or not settings.azure_speech_region:
            raise ScorerNotConfigured("AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set — see README")

        self.segments: asyncio.Queue[TranscriptSegment] = asyncio.Queue()
        self._loop = loop
        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=SAMPLE_RATE_HZ, bits_per_sample=16, channels=1
        )
        self._push_stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
        self._recognizer = recognizer_factory(settings.azure_speech_key, settings.azure_speech_region, self._push_stream)
        self._recognizer.recognized.connect(self._on_recognized)
        self._recognizer.start_continuous_recognition()

    def _on_recognized(self, evt) -> None:
        if evt.result.reason != speechsdk.ResultReason.RecognizedSpeech:
            return
        try:
            result_json = json.loads(evt.result.json)
        except (json.JSONDecodeError, AttributeError):
            logger.warning("azure streaming result had no parseable json")
            return
        segment = parse_recognition_result(result_json)
        if segment is not None:
            self._loop.call_soon_threadsafe(self.segments.put_nowait, segment)

    def push_pcm(self, data: bytes) -> None:
        self._push_stream.write(data)

    def close(self) -> None:
        self._push_stream.close()
        self._recognizer.stop_continuous_recognition()
```

- [ ] **Step 5: Run parsing tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_azure_stream.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Write failing tests for the transcriber class using a fake recognizer**

Append to `backend/tests/rooms/test_azure_stream.py`:

```python
class _FakeResult:
    def __init__(self, reason, result_json):
        self.reason = reason
        self.json = json.dumps(result_json)


class _FakeEvent:
    def __init__(self, result):
        self.result = result


class _FakeRecognizer:
    """Stands in for speechsdk.SpeechRecognizer: captures the connected
    handler so the test can fire a fake recognition event directly, instead
    of needing a real Azure connection."""

    def __init__(self):
        self._handler = None
        self.started = False
        self.stopped = False

    @property
    def recognized(self):
        return self

    def connect(self, handler):
        self._handler = handler

    def start_continuous_recognition(self):
        self.started = True

    def stop_continuous_recognition(self):
        self.stopped = True

    def fire(self, reason, result_json):
        self._handler(_FakeEvent(_FakeResult(reason, result_json)))


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


def test_transcriber_raises_when_not_configured(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "")
    get_settings.cache_clear()
    with pytest.raises(ScorerNotConfigured):
        AzureStreamingTranscriber(asyncio.get_event_loop())


async def test_transcriber_queues_a_recognized_segment(monkeypatch):
    monkeypatch.setenv("AZURE_SPEECH_KEY", "test-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "test-region")
    get_settings.cache_clear()

    fake_recognizer = _FakeRecognizer()
    loop = asyncio.get_event_loop()
    transcriber = AzureStreamingTranscriber(
        loop, recognizer_factory=lambda key, region, push_stream: fake_recognizer
    )
    assert fake_recognizer.started is True

    fake_recognizer.fire(
        speechsdk.ResultReason.RecognizedSpeech,
        {"NBest": [{"Display": "testing one two", "Words": [
            {"Word": "testing", "Offset": 0, "Duration": 5_000_000},
            {"Word": "two", "Offset": 10_000_000, "Duration": 3_000_000},
        ]}]},
    )
    segment = await asyncio.wait_for(transcriber.segments.get(), timeout=1.0)
    assert segment.text == "testing one two"

    transcriber.close()
    assert fake_recognizer.stopped is True
```

Add the missing import at the top of `backend/tests/rooms/test_azure_stream.py`:

```python
import azure.cognitiveservices.speech as speechsdk
```

- [ ] **Step 7: Run to verify the new tests fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_azure_stream.py -v`
Expected: The two new tests FAIL — the first because `AzureStreamingTranscriber` doesn't yet raise before this step ran (it already does after Step 4, so this should actually PASS — verify by reading the failure/pass output; if `test_transcriber_raises_when_not_configured` and `test_transcriber_queues_a_recognized_segment` both already pass because Step 4's implementation was already correct, that's expected — this step exists to confirm no regression, not to find a red bar)

- [ ] **Step 8: Run tests to verify the full file passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_azure_stream.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/requirements.txt backend/app/rooms/azure_stream.py backend/tests/rooms/test_azure_stream.py
git commit -m "Rooms: Azure streaming speech-to-text bridge

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Live-stats registry + room bot

The LiveKit connection itself (`RoomBot.run()`) is a thin, deliberately-not-unit-tested shell around the SDK's async room/track events — same spirit as this project's existing external-service integrations (Day 4's Azure/Anthropic scorers were verified by running them for real, not mocked end-to-end). Everything worth unit-testing (`handle_audio_frame`, `close_participant`, the registry) is decoupled from that connection so it's fully testable without one. Manual verification of `run()` against a real LiveKit Cloud room is listed at the end of this plan.

**Files:**
- Modify: `backend/app/core/storage.py`
- Create: `backend/app/rooms/registry.py`
- Create: `backend/app/rooms/bot.py`
- Create: `backend/tests/rooms/test_registry.py`
- Create: `backend/tests/rooms/test_bot.py`

**Interfaces:**
- Consumes: `SpeechSegment`, `ParticipantStats`, `compute_participant_stats` (Task 1); `StreamingVAD`, `FRAME_SAMPLES`, `SAMPLE_RATE_HZ` (Task 5); `AzureStreamingTranscriber` (Task 6); `issue_bot_token` (Task 2).
- Produces: `room_participant_audio_path(room_id, participant_id) -> Path` (in `app.core.storage`); `RoomRegistry` with `.record_segment`, `.segments_for`, `.live_stats`, `.register_bot_task`, `.stop_bot`, `.subscribe`, `.unsubscribe`; `get_registry() -> RoomRegistry` (module-level singleton); `RoomBot(room_id: str, registry: RoomRegistry)` with `.handle_audio_frame`, `.close_participant`, `.run()`. Used by Task 8.

- [ ] **Step 1: Extend storage for per-participant room audio**

Modify `backend/app/core/storage.py` — add after `save_upload`:

```python
def room_participant_audio_path(room_id: str, participant_id: str) -> Path:
    base = Path(settings.storage_dir) / "rooms" / room_id
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{participant_id}.wav"
```

- [ ] **Step 2: Write failing tests for the registry**

Create `backend/tests/rooms/test_registry.py`:

```python
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
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.registry'`

- [ ] **Step 4: Implement the registry**

Create `backend/app/rooms/registry.py`:

```python
"""In-process registry of live rooms: which RoomBot task is running for a
room, the speech segments recorded so far, and a fan-out for the host's live
WebSocket dashboard. Lives in the FastAPI app's own process/event loop — fine
at this project's scale (<=6 participants per room); a multi-process deploy
would need this moved to Redis instead (already used for the ARQ queue).
"""
import asyncio

from app.rooms.live_stats import ParticipantStats, SpeechSegment, compute_participant_stats


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
        self._subscribers.pop(room_id, None)

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_registry.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Write failing tests for the room bot's frame handling**

Create `backend/tests/rooms/test_bot.py`:

```python
import numpy as np
import pytest

from app.core.config import get_settings
from app.core.storage import room_participant_audio_path
from app.rooms.bot import RoomBot
from app.rooms.registry import RoomRegistry
from app.rooms.vad import FRAME_SAMPLES

LOUD_FRAME = np.full(FRAME_SAMPLES, 20_000, dtype=np.int16)
SILENT_FRAME = np.zeros(FRAME_SAMPLES, dtype=np.int16)


@pytest.fixture(autouse=True)
def _storage_dir(tmp_path):
    original = get_settings().storage_dir
    get_settings().storage_dir = str(tmp_path)
    yield
    get_settings().storage_dir = original


def test_a_closed_speech_segment_is_recorded_to_the_registry():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-1", registry=registry)

    for _ in range(5):
        bot.handle_audio_frame("participant-a", LOUD_FRAME)
    for _ in range(20):  # past HANGOVER_FRAMES, closes the segment
        bot.handle_audio_frame("participant-a", SILENT_FRAME)

    segments = registry.segments_for("room-1")
    assert len(segments) == 1
    assert segments[0].participant_id == "participant-a"
    assert segments[0].duration_s == pytest.approx(0.1, abs=0.01)  # 5 frames * 20ms


def test_audio_frames_are_written_to_a_wav_file_on_disk():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-2", registry=registry)
    bot.handle_audio_frame("participant-b", LOUD_FRAME)
    bot.close_participant("participant-b")

    path = room_participant_audio_path("room-2", "participant-b")
    assert path.exists()
    assert path.stat().st_size > 0


def test_close_participant_flushes_a_still_open_segment():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-3", registry=registry)
    for _ in range(3):
        bot.handle_audio_frame("participant-c", LOUD_FRAME)
    bot.close_participant("participant-c")

    segments = registry.segments_for("room-3")
    assert len(segments) == 1
    assert segments[0].duration_s == pytest.approx(0.06, abs=0.01)
```

- [ ] **Step 7: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_bot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.bot'`

- [ ] **Step 8: Implement the room bot**

Create `backend/app/rooms/bot.py`:

```python
"""The backend's own LiveKit participant: joins each live room, subscribes to
every other participant's audio track, and for each one:
  1. runs energy-based VAD (app/rooms/vad.py) to build speech segments,
  2. streams the same PCM to Azure for live transcription (app/rooms/azure_stream.py),
  3. records the raw audio to local disk (audio only — no video, per the
     design spec's Non-goals).

Runs in-process (spawned via asyncio.create_task, tracked in
app/rooms/registry.py) rather than as a separate daemon — see this plan's
Global Constraints.

`handle_audio_frame`/`close_participant` are the two methods covered by unit
tests. `run()` is the thin LiveKit-connection shell around them — not unit
tested by design (see this task's header note); verified manually per the
"Manual verification" section at the end of this plan.
"""
import asyncio
import logging
import wave

import numpy as np
from livekit import rtc

from app.core.config import get_settings
from app.core.storage import room_participant_audio_path
from app.rooms.azure_stream import AzureStreamingTranscriber
from app.rooms.live_stats import SpeechSegment
from app.rooms.livekit_tokens import issue_bot_token
from app.rooms.registry import RoomRegistry
from app.rooms.vad import FRAME_SAMPLES, SAMPLE_RATE_HZ, StreamingVAD

logger = logging.getLogger("rooms.bot")


class RoomBot:
    def __init__(self, room_id: str, registry: RoomRegistry):
        self.room_id = room_id
        self.registry = registry
        self._vads: dict[str, StreamingVAD] = {}
        self._wave_writers: dict[str, wave.Wave_write] = {}
        self._transcribers: dict[str, AzureStreamingTranscriber] = {}

    def _vad_for(self, participant_id: str) -> StreamingVAD:
        if participant_id not in self._vads:
            self._vads[participant_id] = StreamingVAD(participant_id=participant_id)
        return self._vads[participant_id]

    def _wave_writer_for(self, participant_id: str) -> wave.Wave_write:
        if participant_id not in self._wave_writers:
            path = room_participant_audio_path(self.room_id, participant_id)
            writer = wave.open(str(path), "wb")
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE_HZ)
            self._wave_writers[participant_id] = writer
        return self._wave_writers[participant_id]

    def handle_audio_frame(self, participant_id: str, pcm: np.ndarray) -> None:
        """pcm: one 20ms int16 mono frame at 16kHz (FRAME_SAMPLES samples)."""
        self._wave_writer_for(participant_id).writeframes(pcm.tobytes())

        vad = self._vad_for(participant_id)
        segments_before = len(vad.closed_segments)
        vad.push_frame(pcm)
        for start_s, end_s in vad.closed_segments[segments_before:]:
            self.registry.record_segment(
                self.room_id, SpeechSegment(participant_id=participant_id, start_s=start_s, end_s=end_s)
            )

        transcriber = self._transcribers.get(participant_id)
        if transcriber is not None:
            transcriber.push_pcm(pcm.tobytes())

    def close_participant(self, participant_id: str) -> None:
        vad = self._vads.get(participant_id)
        if vad is not None:
            segments_before = len(vad.closed_segments)
            vad.flush()
            for start_s, end_s in vad.closed_segments[segments_before:]:
                self.registry.record_segment(
                    self.room_id, SpeechSegment(participant_id=participant_id, start_s=start_s, end_s=end_s)
                )
        writer = self._wave_writers.pop(participant_id, None)
        if writer is not None:
            writer.close()
        transcriber = self._transcribers.pop(participant_id, None)
        if transcriber is not None:
            transcriber.close()

    async def run(self) -> None:
        """Connects to the LiveKit room as the analysis bot, subscribes to
        every participant's audio track, and forwards received frames to
        handle_audio_frame. Runs until cancelled (see registry.stop_bot)."""
        settings = get_settings()
        room = rtc.Room()
        loop = asyncio.get_event_loop()

        async def _forward_track(participant_id: str, track: rtc.Track) -> None:
            try:
                self._transcribers[participant_id] = AzureStreamingTranscriber(loop)
            except Exception:
                logger.warning("live transcription unavailable for %s (room %s)", participant_id, self.room_id)
            audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE_HZ, num_channels=1)
            async for event in audio_stream:
                samples = np.frombuffer(event.frame.data, dtype=np.int16)
                for i in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
                    self.handle_audio_frame(participant_id, samples[i : i + FRAME_SAMPLES])

        @room.on("track_subscribed")
        def on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(_forward_track(participant.identity, track))

        @room.on("participant_disconnected")
        def on_participant_disconnected(participant):
            self.close_participant(participant.identity)

        token = issue_bot_token(room_name=self.room_id)
        await room.connect(settings.livekit_url, token)
        logger.info("room bot connected to room %s", self.room_id)

        try:
            await asyncio.Future()  # runs until this task is cancelled
        finally:
            for participant_id in list(self._wave_writers.keys()):
                self.close_participant(participant_id)
            await room.disconnect()
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_bot.py -v`
Expected: PASS (3 tests)

- [ ] **Step 10: Run the full test suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (all tests from Tasks 1, 2, 4, 5, 6, 7)

- [ ] **Step 11: Commit**

```bash
git add backend/app/core/storage.py backend/app/rooms/registry.py backend/app/rooms/bot.py \
        backend/tests/rooms/test_registry.py backend/tests/rooms/test_bot.py
git commit -m "Rooms: live-stats registry and the server-side room bot

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Live-stats WebSocket + ending a room

Spawns the room bot on first join (modifying Task 4's `join_room`), adds the host-only live-stats WebSocket, and adds `POST /rooms/{id}/end` which stops the bot and enqueues the (not-yet-existing until Task 10) `analyze_room_session` job — ARQ jobs can be enqueued by name before the worker registers them; they simply sit queued until a worker that knows the function processes them, same as how `sessions.py`'s upload endpoint enqueued `transcribe_session` before scoring existed.

**Files:**
- Modify: `backend/app/api/rooms.py`
- Create: `backend/tests/rooms/test_rooms_live_and_end.py`

**Interfaces:**
- Consumes: `RoomBot` (Task 7), `get_registry` (Task 7), `get_arq_pool` (existing, `app.jobs.pool`), `decode_access_token` (existing, `app.core.security`).
- Produces: `WS /rooms/{room_id}/live`, `POST /rooms/{room_id}/end` (both added to the existing `rooms` router). Used by Plan 2 (frontend).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/rooms/test_rooms_live_and_end.py`:

```python
import pytest
from starlette.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.jobs import pool as jobs_pool
from app.main import app as fastapi_app
from app.models.user import User
from app.rooms.live_stats import SpeechSegment
from app.rooms.registry import get_registry


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
        import asyncio
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


async def test_live_stats_ws_streams_stats_to_the_host(client, db_session):
    host = await _create_user(db_session, "host3@example.com", "Host3")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(host))
    room_id = create_resp.json()["id"]
    join_resp = await client.post(f"/rooms/{create_resp.json()['join_code']}/join", headers=_auth_headers(host))
    participant_id = join_resp.json()["participant_id"]

    token = create_access_token(str(host.id))
    with TestClient(fastapi_app) as sync_client:
        with sync_client.websocket_connect(f"/rooms/{room_id}/live?token={token}") as ws:
            get_registry().record_segment(room_id, SpeechSegment(participant_id, 0.0, 1.0))
            message = ws.receive_json()
            assert participant_id in message
            assert message[participant_id]["talk_time_s"] == 1.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_rooms_live_and_end.py -v`
Expected: FAIL (404s — `/end` and `/live` don't exist yet)

- [ ] **Step 3: Spawn the room bot on first join**

Modify `backend/app/api/rooms.py` — in `join_room`, inside the `if existing is None:` branch, right after `room.status = RoomStatus.LIVE`:

```python
        room.status = RoomStatus.LIVE
        is_first_participant = active_count == 0
        await db.commit()
        await db.refresh(participant)

        if is_first_participant:
            import asyncio

            from app.rooms.bot import RoomBot
            from app.rooms.registry import get_registry

            registry = get_registry()
            bot = RoomBot(room_id=str(room.id), registry=registry)
            task = asyncio.create_task(bot.run())
            registry.register_bot_task(str(room.id), task)
```

(This replaces the existing `room.status = RoomStatus.LIVE` / `await db.commit()` / `await db.refresh(participant)` block from Task 4.)

- [ ] **Step 4: Add the end-room and live-stats-WebSocket endpoints**

Modify `backend/app/api/rooms.py` — update the imports at the top:

```python
import asyncio
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DBSession

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import decode_access_token
from app.jobs.pool import get_arq_pool
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.user import User
from app.rooms.livekit_tokens import RoomsNotConfigured, issue_participant_token
from app.rooms.registry import get_registry
from app.schemas.room import RoomCreate, RoomJoinResponse, RoomResponse
```

Append to `backend/app/api/rooms.py`:

```python
@router.post("/{room_id}/end", response_model=RoomResponse)
async def end_room(
    room_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    if room.host_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the host can end the room")
    if room.status not in (RoomStatus.WAITING, RoomStatus.LIVE):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Room already ended")

    get_registry().stop_bot(str(room.id))
    room.status = RoomStatus.ENDED
    room.ended_at = func.now()
    await db.commit()
    await db.refresh(room)

    pool = await get_arq_pool()
    await pool.enqueue_job("analyze_room_session", str(room.id), _job_id=f"analyze_room:{room.id}")

    return room


@router.websocket("/{room_id}/live")
async def live_stats_ws(websocket: WebSocket, room_id: uuid.UUID, db: DBSession = Depends(get_db)):
    await websocket.accept()
    token = websocket.query_params.get("token")
    subject = decode_access_token(token) if token else None
    if subject is None:
        await websocket.close(code=4401)
        return

    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if room is None or str(room.host_user_id) != subject:
        await websocket.close(code=4403)
        return

    participants = (
        await db.execute(
            select(RoomParticipant).where(RoomParticipant.room_id == room_id, RoomParticipant.left_at.is_(None))
        )
    ).scalars().all()
    participant_ids = [str(p.id) for p in participants]
    started_at = room.created_at

    registry = get_registry()
    queue = registry.subscribe(str(room_id))
    try:
        while True:
            await queue.get()
            elapsed_s = (datetime.now(timezone.utc) - started_at).total_seconds()
            stats = registry.live_stats(str(room_id), participant_ids, elapsed_s)
            await websocket.send_json({pid: asdict(s) for pid, s in stats.items()})
    except WebSocketDisconnect:
        pass
    finally:
        registry.unsubscribe(str(room_id), queue)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_rooms_live_and_end.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full test suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (all tests so far)

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/rooms.py backend/tests/rooms/test_rooms_live_and_end.py
git commit -m "Rooms: spawn the room bot on first join; live-stats WebSocket and end-room endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Group-dynamics LLM qualitative pass

**Files:**
- Create: `backend/app/scoring/group_dynamics.py`
- Create: `backend/tests/rooms/test_group_dynamics.py`

**Interfaces:**
- Consumes: `ScorerNotConfigured` (existing, `app.scoring.types`).
- Produces: `score_group_dynamics(transcript: str, deterministic_stats: dict) -> dict` (keys: `status`, and on success `participants`, `overall_summary`; on failure `error_detail`). Used by Task 10.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/rooms/test_group_dynamics.py`:

```python
import pytest

from app.core.config import get_settings
from app.scoring.types import ScorerNotConfigured


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    yield
    get_settings.cache_clear()


def test_raises_when_not_configured(monkeypatch):
    from app.scoring.group_dynamics import score_group_dynamics

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(ScorerNotConfigured):
        score_group_dynamics("transcript", {})


def test_returns_parsed_result_on_success(monkeypatch):
    import app.scoring.group_dynamics as gd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()

    parsed = gd._GroupDynamicsSchema(
        participants=[
            gd._ParticipantRead(
                participant_id="p1",
                constructiveness=4,
                clarity_of_points=3,
                observations=["built on another participant's point about market sizing"],
            )
        ],
        overall_summary="A focused, evenly-paced discussion.",
    )

    class _FakeMessages:
        def parse(self, **kwargs):
            class _Resp:
                parsed_output = parsed

            return _Resp()

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(gd, "_get_client", lambda: _FakeClient())

    result = gd.score_group_dynamics("p1: hello\np2: hi", {"p1": {"talk_time_pct": 50}})
    assert result["status"] == "ok"
    assert result["participants"][0]["participant_id"] == "p1"
    assert result["overall_summary"].startswith("A focused")


def test_returns_error_status_on_repeated_failure(monkeypatch):
    import app.scoring.group_dynamics as gd

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()

    class _FakeMessages:
        def parse(self, **kwargs):
            raise RuntimeError("api down")

    class _FakeClient:
        messages = _FakeMessages()

    monkeypatch.setattr(gd, "_get_client", lambda: _FakeClient())

    result = gd.score_group_dynamics("transcript", {})
    assert result["status"] == "error"
    assert "api down" in result["error_detail"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_group_dynamics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scoring.group_dynamics'`

- [ ] **Step 3: Implement**

Create `backend/app/scoring/group_dynamics.py`:

```python
"""LLM qualitative pass over a full multi-speaker room transcript — the group-
discussion analogue of app/scoring/argument_quality.py. Explicitly avoids
personality-trait labels (no "arrogant", "rude", etc.): those are subjective,
potentially biased claims about a real person, especially in front of an
interviewer. Every observation must be evidence-tied to something that
actually happened in the transcript — see the design spec's Post-session
report section.
"""
import logging

from anthropic import Anthropic
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("scoring.group_dynamics")

MAX_TRANSCRIPT_CHARS = 30_000

SYSTEM_PROMPT = (
    "You are analyzing a transcript of a multi-person group discussion or debate "
    "practice session. Speaker turns are labeled by participant id. Give a neutral, "
    "evidence-tied behavioral read on each participant's contribution — for example "
    "'frequently spoke over others without yielding' or 'built on other participants' "
    "points with specific references'. Every observation must cite what actually "
    "happened in the transcript. NEVER use personality-trait labels or character "
    "judgments (e.g. 'arrogant', 'rude', 'shy') — describe behavior, not character. "
    "You are also given deterministic talk-time/interruption stats; use them as "
    "context, don't just restate the numbers."
)

_client: Anthropic | None = None


class _ParticipantRead(BaseModel):
    participant_id: str
    constructiveness: int = Field(ge=1, le=5)
    clarity_of_points: int = Field(ge=1, le=5)
    observations: list[str] = Field(min_length=1, max_length=4)


class _GroupDynamicsSchema(BaseModel):
    participants: list[_ParticipantRead]
    overall_summary: str


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()  # resolves ANTHROPIC_API_KEY from env, per SDK convention
    return _client


def score_group_dynamics(transcript: str, deterministic_stats: dict) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ScorerNotConfigured("ANTHROPIC_API_KEY not set — see README for how to get one")

    user_message = (
        f"Deterministic stats (talk-time %, turns, interruptions per participant):\n"
        f"{deterministic_stats}\n\n"
        f"Transcript:\n{transcript[:MAX_TRANSCRIPT_CHARS]}\n\n"
        "Give a behavioral read per participant plus a short overall summary of how "
        "the discussion went."
    )

    client = _get_client()
    last_error: Exception | None = None
    for attempt in range(2):  # one retry on schema failure, same convention as argument_quality.py
        try:
            response = client.messages.parse(
                model=settings.anthropic_model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": user_message}],
                output_format=_GroupDynamicsSchema,
            )
            parsed = response.parsed_output
            return {
                "status": "ok",
                "participants": [p.model_dump() for p in parsed.participants],
                "overall_summary": parsed.overall_summary,
            }
        except ValidationError as exc:
            last_error = exc
            logger.warning("group dynamics schema validation failed (attempt %d): %s", attempt + 1, exc)
        except Exception as exc:  # API errors, refusals, etc.
            last_error = exc
            logger.warning("group dynamics scoring failed (attempt %d): %s", attempt + 1, exc)
            break  # don't retry non-schema failures

    return {"status": "error", "error_detail": str(last_error)[:500] if last_error else "unknown"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_group_dynamics.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/scoring/group_dynamics.py backend/tests/rooms/test_group_dynamics.py
git commit -m "Rooms: group-dynamics LLM qualitative pass (neutral, evidence-tied observations only)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: `analyze_room_session` ARQ job

Reuses `db_session_maker` from Task 4's `conftest.py`, monkeypatched into this job's module so its internally-opened sessions share the test's single per-test transaction/SAVEPOINT instead of hitting the real dev database.

**Files:**
- Create: `backend/app/jobs/room_analysis.py`
- Modify: `backend/app/jobs/worker.py`
- Create: `backend/tests/rooms/test_room_analysis_job.py`

**Interfaces:**
- Consumes: `Room`, `RoomStatus` (Task 3); `RoomParticipant` (Task 3); `RoomReport` (Task 3); `RoomTranscriptSegment` (Task 3); `SpeechSegment`, `compute_participant_stats`, `compute_session_dominance` (Task 1); `score_group_dynamics` (Task 9); `ScorerNotConfigured` (existing).
- Produces: `analyze_room_session(ctx: dict, room_id: str) -> str`, `MAX_TRIES` (in `app.jobs.room_analysis`). Registered in the ARQ worker; used by Task 8's `end_room` (already enqueues it by name).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/rooms/test_room_analysis_job.py`:

```python
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

    refreshed_room = (await db_session.execute(select(Room).where(Room.id == room.id))).scalar_one()
    assert refreshed_room.status == RoomStatus.ANALYZED


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_room_analysis_job.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs.room_analysis'`

- [ ] **Step 3: Implement the job**

Create `backend/app/jobs/room_analysis.py`:

```python
"""ARQ job: finalize a room's post-session report once the host ends it.
Mirrors app/jobs/scoring.py's shape (idempotency guard, isolated LLM-pass
failure, exponential-backoff retry) but works from already-captured live
data (room_transcript_segments) rather than re-running transcription — the
transcript was already produced live by Azure streaming STT during the
session (see app/rooms/bot.py).
"""
import logging

from sqlalchemy import select

from app.core.db import async_session_maker
from app.models.room import Room, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_report import RoomReport
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.rooms.live_stats import SpeechSegment, compute_participant_stats, compute_session_dominance
from app.scoring.group_dynamics import score_group_dynamics
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("jobs.room_analysis")

MAX_TRIES = 3


def _build_transcript_text(segments: list[RoomTranscriptSegment], alias_by_participant: dict) -> str:
    ordered = sorted(segments, key=lambda s: s.start_s)
    return "\n".join(f"{alias_by_participant[str(s.participant_id)]}: {s.text}" for s in ordered)


async def analyze_room_session(ctx: dict, room_id: str) -> str:
    from arq.worker import Retry  # local import, same reasoning as app/jobs/transcription.py

    job_try = ctx.get("job_try", 1)

    async with async_session_maker() as db:
        existing = await db.execute(select(RoomReport).where(RoomReport.room_id == room_id))
        if existing.scalar_one_or_none() is not None:
            logger.info("room %s already has a report, skipping", room_id)
            return "already_analyzed"

        room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
        if room is None:
            logger.error("room %s not found, cannot analyze", room_id)
            return "room_not_found"

        participants = (
            await db.execute(select(RoomParticipant).where(RoomParticipant.room_id == room_id))
        ).scalars().all()
        transcript_rows = (
            await db.execute(select(RoomTranscriptSegment).where(RoomTranscriptSegment.room_id == room_id))
        ).scalars().all()

        alias_by_participant = {str(p.id): p.alias_name for p in participants}
        participant_ids = [str(p.id) for p in participants]
        segments = [
            SpeechSegment(participant_id=str(row.participant_id), start_s=row.start_s, end_s=row.end_s)
            for row in transcript_rows
        ]
        session_duration_s = max((row.end_s for row in transcript_rows), default=0.0)
        transcript_text = _build_transcript_text(transcript_rows, alias_by_participant)

    try:
        stats = compute_participant_stats(segments, participant_ids, session_duration_s)
        dominance = compute_session_dominance(stats)
        stats_payload = {pid: {k: v for k, v in s.__dict__.items() if k != "participant_id"} for pid, s in stats.items()}

        try:
            qualitative = score_group_dynamics(transcript_text, stats_payload)
        except ScorerNotConfigured as exc:
            qualitative = {"status": "not_configured", "error_detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 — deliberate isolation boundary, same as app/scoring/pipeline.py
            logger.exception("group dynamics scorer crashed for room %s", room_id)
            qualitative = {"status": "error", "error_detail": str(exc)[:500]}

        async with async_session_maker() as db:
            db.add(
                RoomReport(
                    room_id=room_id,
                    participant_stats=stats_payload,
                    dominance_index=dominance,
                    qualitative_status=qualitative["status"],
                    # status/error_detail already have their own columns above —
                    # same convention as app/scoring/pipeline.py's _payload() helper.
                    qualitative_result=(
                        {k: v for k, v in qualitative.items() if k not in ("status", "error_detail")}
                        if qualitative["status"] == "ok"
                        else None
                    ),
                    qualitative_error=qualitative.get("error_detail"),
                )
            )
            room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one()
            room.status = RoomStatus.ANALYZED
            await db.commit()

        logger.info("analyzed room %s: %d participants, dominance=%.3f", room_id, len(participant_ids), dominance)
        return "ok"

    except Exception as exc:
        logger.exception("room analysis failed for room %s (try %d)", room_id, job_try)
        if job_try < MAX_TRIES:
            raise Retry(defer=5 * (5 ** (job_try - 1)))
        raise
```

Modify `backend/app/jobs/worker.py`:

```python
import logging

from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.jobs.room_analysis import MAX_TRIES as ROOM_ANALYSIS_MAX_TRIES, analyze_room_session
from app.jobs.scoring import MAX_TRIES as SCORING_MAX_TRIES, score_session
from app.jobs.transcription import MAX_TRIES, transcribe_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

settings = get_settings()


async def dummy_job(ctx, message: str) -> str:
    """Placeholder job used to prove the queue works end to end (Phase 0 DoD).
    Replaced by real jobs (transcription, scoring, ...) from Day 2 onward.
    """
    logger.info("dummy_job received message: %s", message)
    return f"processed: {message}"


async def startup(ctx):
    logger.info("ARQ worker starting up")


async def shutdown(ctx):
    logger.info("ARQ worker shutting down")


class WorkerSettings:
    functions = [
        dummy_job,
        func(transcribe_session, max_tries=MAX_TRIES),
        func(score_session, max_tries=SCORING_MAX_TRIES),
        func(analyze_room_session, max_tries=ROOM_ANALYSIS_MAX_TRIES),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_room_analysis_job.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (all tests so far)

- [ ] **Step 6: Commit**

```bash
git add backend/app/jobs/room_analysis.py backend/app/jobs/worker.py backend/tests/rooms/test_room_analysis_job.py
git commit -m "Rooms: analyze_room_session ARQ job — finalizes the post-session report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Post-session report API

**Files:**
- Create: `backend/app/schemas/room_report.py`
- Modify: `backend/app/api/rooms.py`
- Create: `backend/tests/rooms/test_room_report_api.py`

**Interfaces:**
- Consumes: `RoomReport`, `Room`, `RoomMode`, `RoomParticipant` (Task 3).
- Produces: `GET /rooms/{room_id}/report` (added to the existing `rooms` router).

- [ ] **Step 1: Write failing tests**

Create `backend/tests/rooms/test_room_report_api.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_room_report_api.py -v`
Expected: FAIL with 404 (endpoint doesn't exist yet)

- [ ] **Step 3: Write the schemas**

Create `backend/app/schemas/room_report.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.room import RoomMode


class RoomParticipantReport(BaseModel):
    participant_id: str
    label: str
    is_you: bool
    talk_time_s: float
    talk_time_pct: float
    turn_count: int
    interruptions_made: int
    interruptions_received: int
    longest_monologue_s: float
    silence_pct: float


class RoomReportResponse(BaseModel):
    room_id: uuid.UUID
    mode: RoomMode
    generated_at: datetime
    dominance_index: float
    participants: list[RoomParticipantReport]
    qualitative_status: str
    qualitative: dict | None
```

- [ ] **Step 4: Implement the endpoint**

Modify `backend/app/api/rooms.py` — add to the imports:

```python
from app.models.room_report import RoomReport
from app.schemas.room_report import RoomParticipantReport, RoomReportResponse
```

Append to `backend/app/api/rooms.py`:

```python
@router.get("/{room_id}/report", response_model=RoomReportResponse)
async def get_room_report(
    room_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    requesting_participant = (
        await db.execute(
            select(RoomParticipant).where(
                RoomParticipant.room_id == room_id, RoomParticipant.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if requesting_participant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant in this room")

    report = (await db.execute(select(RoomReport).where(RoomReport.room_id == room_id))).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not ready yet")

    room = (await db.execute(select(Room).where(Room.id == room_id))).scalar_one()
    all_participants = {
        str(p.id): p
        for p in (
            await db.execute(select(RoomParticipant).where(RoomParticipant.room_id == room_id))
        ).scalars()
    }

    participants = [
        RoomParticipantReport(
            participant_id=pid,
            label=all_participants[pid].alias_name if pid in all_participants else pid,
            is_you=(pid == str(requesting_participant.id)),
            **{k: v for k, v in stats.items() if k != "participant_id"},
        )
        for pid, stats in report.participant_stats.items()
    ]

    return RoomReportResponse(
        room_id=room.id,
        mode=room.mode,
        generated_at=report.generated_at,
        dominance_index=report.dominance_index,
        participants=participants,
        qualitative_status=report.qualitative_status,
        qualitative=report.qualitative_result,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_room_report_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full test suite one last time**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (every test from all 11 tasks)

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/room_report.py backend/app/api/rooms.py backend/tests/rooms/test_room_report_api.py
git commit -m "Rooms: post-session report API (alias-labeled in anonymous mode end-to-end)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Manual verification (needs real LiveKit Cloud + Azure accounts — not covered by the automated suite above)

Per this plan's Task 7 note, `RoomBot.run()`'s actual LiveKit connection and the live Azure streaming path are deliberately not unit-tested (mirrors this project's existing Day 4 precedent for Azure/Anthropic integrations). Once `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` and `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION` are set in `.env` and both the API server and ARQ worker are restarted to pick them up:

1. Two browser tabs (or two devices) each `POST /rooms` → `POST /rooms/{code}/join`, and connect to the returned `livekit_url`/`livekit_token` with a minimal LiveKit JS client (Plan 2 builds the real UI for this) — confirm both can publish/hear each other's audio.
2. Speak in each tab; confirm `room_transcript_segments` rows appear for the right `participant_id` with plausible `start_s`/`end_s`.
3. Deliberately talk over each other; confirm an interruption is recorded (check `GET /rooms/{id}/report` after ending, or watch the `/rooms/{id}/live` WebSocket as the host).
4. `POST /rooms/{id}/end`; confirm the ARQ worker log shows `analyzed room ... dominance=...` and `GET /rooms/{id}/report` returns a populated report, including a qualitative section if `ANTHROPIC_API_KEY` is set.
5. Repeat with `mode: "anonymous"`; confirm no video track is offered by the frontend stub, participants see "Speaker N" labels, and the report stays alias-labeled for a non-host participant.
