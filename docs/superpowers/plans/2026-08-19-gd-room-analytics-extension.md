# GD Room Post-Session Analytics Extension — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the multi-party GD room with the per-participant analytics that make the product a *GD analyzer* rather than a video call — topic alignment, pronunciation, speech quality (fluency/vocabulary/syntax/fillers), agreement-vs-disagreement stance, and composite competency scores — and fix two blocking defects found in the room backend plan while reviewing it.

**Architecture:** Everything here runs in the existing `analyze_room_session` ARQ job, post-session, over data already captured live. Per-participant analytics reuse the solo trainer's existing, tested modules unchanged (`app/metrics/pipeline.py`, `app/scoring/relevance.py`, `app/scoring/pronunciation.py`) by assembling each participant's room transcript into the same `(full_text, words, duration_s)` shape those modules already take. New room-specific logic lives in `app/rooms/` next to `live_stats.py`. Every scorer is individually isolated: one participant's failed pronunciation call never blocks anyone else's report.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + Alembic + ARQ + pytest (all existing) · `sentence-transformers`, `spacy`, Azure Speech REST (all already installed and used by the solo trainer) · **no new dependencies and no new external services.**

**Spec:** `docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md` (sections: "Post-session analytics scope (extension)", "Consent & retention", "Session access")

## Execution order — READ FIRST

This plan is **Plan 3**. It assumes `docs/superpowers/plans/2026-08-11-multiparty-gd-room-backend.md` (Tasks 1-11) has already been implemented — `app/rooms/{live_stats,vad,azure_stream,registry,bot,livekit_tokens}.py`, the `rooms`/`room_participants`/`room_transcript_segments`/`room_reports` tables, `app/api/rooms.py`, and `app/jobs/room_analysis.py` must all exist before starting Task 12 here. Task numbering continues from that plan (12+) so the two read as one sequence.

The room **frontend** (join screen, QR code, consent modal, live host dashboard, report page) is still unwritten and is a separate plan — do not attempt it here.

## Global Constraints

- Max participants per room: 6 (spec: Goals).
- Anonymous-mode rooms stay alias-labeled end-to-end, including every new analytics field added here (spec: Data model, Anonymous-mode privacy).
- Video is never recorded; only audio is persisted (spec: Non-goals).
- Raw audio is deleted 30 days after `rooms.ended_at`; transcripts and reports are retained indefinitely (spec: Consent & retention).
- No new paid services. Topic alignment uses the local `all-MiniLM-L6-v2` already in `app/scoring/relevance.py`; pronunciation uses the `AZURE_SPEECH_KEY` already configured (spec: Cost note).
- Every scorer must degrade gracefully when unconfigured or failing — the `status: "ok" | "not_configured" | "error"` convention from `app/scoring/types.py`, isolated per participant *and* per scorer, never a hard crash (spec: Post-session analytics scope, final paragraph).
- Follow existing conventions exactly: SQLAlchemy 2.0 `Mapped`/`mapped_column`, `native_enum=False` string enums, Pydantic schemas with `from_attributes = True`, ARQ jobs with an idempotency guard + exponential-backoff `Retry`.
- Run all commands from `backend/` using `./.venv/Scripts/python.exe`.

## Defects this plan fixes (found while reviewing the room backend plan)

Both would have shipped a report full of zeros. Task 12 fixes them together because they share a test.

1. **Participant-identity mismatch.** Plan 2's Task 4 sets `livekit_identity = f"{user_id}:{room_id}"`, and `RoomBot` keys every `SpeechSegment` by that LiveKit identity — but Task 8's live-stats WebSocket and Task 10's analysis job both ask for stats keyed by `str(RoomParticipant.id)`. The two key spaces never meet, so `compute_participant_stats` returns all-zero rows for everyone.
2. **Transcript segments are never persisted.** `RoomBot._forward_track` constructs an `AzureStreamingTranscriber` and pushes PCM into it, but nothing ever drains `transcriber.segments`. Nothing writes `room_transcript_segments` rows, so `analyze_room_session` (which reads them) always sees an empty transcript — and the LLM qualitative pass gets an empty string.

---

### Task 12: Fix participant identity + persist live transcripts with word timings

Makes `livekit_identity` *be* the participant UUID (one key space everywhere), keeps Azure's word-level timings instead of discarding them, and drains the transcriber queue into `room_transcript_segments`. Word timings are required by every later task — `compute_all_metrics` and `score_relevance` both take a `Sequence[WordLike]`.

**Files:**
- Modify: `backend/app/rooms/azure_stream.py`
- Modify: `backend/app/rooms/bot.py`
- Modify: `backend/app/api/rooms.py`
- Modify: `backend/app/models/room_transcript_segment.py`
- Create: `backend/alembic/versions/c2d8e4f1a903_add_words_to_room_transcript_segments.py`
- Modify: `backend/tests/rooms/test_azure_stream.py`
- Create: `backend/tests/rooms/test_transcript_persistence.py`

**Interfaces:**
- Consumes: `TranscriptSegment`, `parse_recognition_result`, `AzureStreamingTranscriber` (Plan 2 Task 6); `RoomBot` (Plan 2 Task 7); `RoomTranscriptSegment` (Plan 2 Task 3).
- Produces: `TranscriptWord(word: str, start_s: float, end_s: float)` (frozen dataclass, in `app.rooms.azure_stream`); `TranscriptSegment.words: tuple[TranscriptWord, ...]`; `RoomTranscriptSegment.words` (JSON column, list of `{"word": str, "start_s": float, "end_s": float}`); `RoomBot.persist_segment(participant_id: str, segment: TranscriptSegment) -> None`. Used by Tasks 13, 14, 17.

- [ ] **Step 1: Write the failing test for word-level parsing**

Replace the body of `test_parse_recognition_result_uses_word_level_timestamps` in `backend/tests/rooms/test_azure_stream.py` and add a new test after it:

```python
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


def test_parse_recognition_result_keeps_each_words_timing():
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
    assert [w.word for w in segment.words] == ["hello", "world"]
    assert segment.words[0].start_s == pytest.approx(1.0)
    assert segment.words[0].end_s == pytest.approx(1.5)
    assert segment.words[1].start_s == pytest.approx(1.6)


def test_parse_recognition_result_without_words_has_an_empty_word_list():
    result_json = {"NBest": [{"Display": "hi"}], "Offset": 20_000_000, "Duration": 5_000_000}
    segment = parse_recognition_result(result_json)
    assert segment.words == ()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_azure_stream.py -v`
Expected: FAIL — `AttributeError: 'TranscriptSegment' object has no attribute 'words'`

- [ ] **Step 3: Keep word timings in the parsed segment**

In `backend/app/rooms/azure_stream.py`, replace the `TranscriptSegment` dataclass and `parse_recognition_result` function with:

```python
@dataclass(frozen=True)
class TranscriptWord:
    """Satisfies app.metrics.types.WordLike (word/start_s/end_s), so a room
    participant's words feed the existing solo-trainer metrics and relevance
    scorers unchanged."""

    word: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_s: float
    end_s: float
    words: tuple[TranscriptWord, ...] = ()


def parse_recognition_result(result_json: dict) -> TranscriptSegment | None:
    """Azure's Detailed-format JSON -> (text, start_s, end_s, words), using the
    first/last word's offset+duration (100ns ticks) when word-level
    timestamps are enabled, falling back to the utterance-level Offset/Duration.

    Word timings are kept (not just the span) because the post-session
    per-participant analytics reuse app/metrics/pipeline.py and
    app/scoring/relevance.py, both of which need a per-word sequence.
    """
    best = (result_json.get("NBest") or [{}])[0]
    text = best.get("Display") or result_json.get("DisplayText") or ""
    if not text:
        return None
    raw_words = best.get("Words")
    if raw_words:
        words = tuple(
            TranscriptWord(
                word=w["Word"],
                start_s=w["Offset"] / 10_000_000,
                end_s=(w["Offset"] + w["Duration"]) / 10_000_000,
            )
            for w in raw_words
        )
        start_s = words[0].start_s
        end_s = words[-1].end_s
    else:
        words = ()
        start_s = result_json.get("Offset", 0) / 10_000_000
        end_s = start_s + result_json.get("Duration", 0) / 10_000_000
    return TranscriptSegment(text=text, start_s=start_s, end_s=end_s, words=words)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_azure_stream.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Add the `words` column to the transcript segment model**

In `backend/app/models/room_transcript_segment.py`, add `JSON` to the SQLAlchemy import and add the column after `is_final`:

```python
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Text
```

```python
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [{"word": str, "start_s": float, "end_s": float}] — Azure word-level
    # timings, kept so post-session per-participant analytics can reuse
    # app/metrics/pipeline.py and app/scoring/relevance.py unchanged.
    words: Mapped[list | None] = mapped_column(JSON, nullable=True)
```

Create `backend/alembic/versions/c2d8e4f1a903_add_words_to_room_transcript_segments.py`:

```python
"""add words to room_transcript_segments

Revision ID: c2d8e4f1a903
Revises: f1a9c3d7e2b4
Create Date: 2026-08-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c2d8e4f1a903'
down_revision: Union[str, None] = 'f1a9c3d7e2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('room_transcript_segments', sa.Column('words', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('room_transcript_segments', 'words')
```

Run: `cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head`
Expected: migration applies with no errors.

- [ ] **Step 6: Write the failing test for identity + transcript persistence**

Create `backend/tests/rooms/test_transcript_persistence.py`:

```python
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models.room_participant import RoomParticipant
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.models.user import User
from app.rooms.azure_stream import TranscriptSegment, TranscriptWord
from app.rooms.bot import RoomBot
from app.rooms.registry import RoomRegistry


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
    async def _fake_run(self):
        import asyncio
        await asyncio.Event().wait()

    monkeypatch.setattr("app.rooms.bot.RoomBot.run", _fake_run)


async def test_livekit_identity_is_the_participant_uuid(client, db_session):
    """The bot keys speech segments by LiveKit identity, while the live-stats
    WS and the analysis job key by RoomParticipant.id — they must be the same
    string or every computed stat comes back zero."""
    user = await _create_user(db_session, "ident@example.com", "Ident")
    create_resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(user))
    join_resp = await client.post(
        f"/rooms/{create_resp.json()['join_code']}/join", headers=_auth_headers(user)
    )
    participant_id = join_resp.json()["participant_id"]

    participant = (
        await db_session.execute(select(RoomParticipant).where(RoomParticipant.id == participant_id))
    ).scalar_one()
    assert participant.livekit_identity == str(participant.id)


async def test_persist_segment_writes_a_row_with_word_timings(db_session, db_session_maker, monkeypatch):
    monkeypatch.setattr("app.rooms.bot.async_session_maker", db_session_maker)

    user = await _create_user(db_session, "persist@example.com", "Persist")
    from app.models.room import Room, RoomMode, RoomStatus

    room = Room(host_user_id=user.id, mode=RoomMode.IDENTIFIED, status=RoomStatus.LIVE)
    db_session.add(room)
    await db_session.flush()
    participant = RoomParticipant(
        room_id=room.id, user_id=user.id, alias_name="Persist", livekit_identity="x"
    )
    db_session.add(participant)
    await db_session.commit()
    participant.livekit_identity = str(participant.id)

    bot = RoomBot(room_id=str(room.id), registry=RoomRegistry())
    await bot.persist_segment(
        str(participant.id),
        TranscriptSegment(
            text="hello everyone",
            start_s=1.0,
            end_s=2.1,
            words=(TranscriptWord("hello", 1.0, 1.5), TranscriptWord("everyone", 1.6, 2.1)),
        ),
    )

    rows = (
        await db_session.execute(
            select(RoomTranscriptSegment).where(RoomTranscriptSegment.room_id == room.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].text == "hello everyone"
    assert rows[0].is_final is True
    assert rows[0].words == [
        {"word": "hello", "start_s": 1.0, "end_s": 1.5},
        {"word": "everyone", "start_s": 1.6, "end_s": 2.1},
    ]
```

- [ ] **Step 7: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_transcript_persistence.py -v`
Expected: FAIL — the first on the identity assertion, the second with `AttributeError: 'RoomBot' object has no attribute 'persist_segment'`

- [ ] **Step 8: Make `livekit_identity` the participant UUID**

In `backend/app/api/rooms.py`, inside `join_room`'s `if existing is None:` branch, replace the participant-construction lines:

```python
        seat_number = active_count + 1
        alias = f"Speaker {seat_number}" if room.mode == RoomMode.ANONYMOUS else current_user.name
        # The room bot keys every speech segment by LiveKit identity, while the
        # live-stats WS and the analysis job key by RoomParticipant.id — so the
        # identity IS the participant id, not a composite. Generated up front
        # because the identity has to be known before the row is flushed.
        participant_id = uuid.uuid4()
        participant = RoomParticipant(
            id=participant_id,
            room_id=room.id,
            user_id=current_user.id,
            alias_name=alias,
            livekit_identity=str(participant_id),
        )
        db.add(participant)
```

- [ ] **Step 9: Add transcript persistence to the room bot**

In `backend/app/rooms/bot.py`, add these imports:

```python
from dataclasses import asdict

from app.core.db import async_session_maker
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.rooms.azure_stream import AzureStreamingTranscriber, TranscriptSegment
```

(the `AzureStreamingTranscriber` import already exists — extend it to also import `TranscriptSegment`.)

Add this method to `RoomBot`, after `close_participant`:

```python
    async def persist_segment(self, participant_id: str, segment: TranscriptSegment) -> None:
        """Write one recognized utterance to room_transcript_segments. Opens
        its own session because the bot runs as a detached asyncio task with no
        request-scoped session — same reasoning as the ARQ jobs.

        Volume is low (a handful of utterances per speaker per minute), so a
        row-per-utterance write is fine and keeps the transcript durable
        against a mid-session restart.
        """
        async with async_session_maker() as db:
            db.add(
                RoomTranscriptSegment(
                    room_id=self.room_id,
                    participant_id=participant_id,
                    text=segment.text,
                    start_s=segment.start_s,
                    end_s=segment.end_s,
                    is_final=True,
                    words=[asdict(w) for w in segment.words],
                )
            )
            await db.commit()
```

Add this drain loop as a method on `RoomBot`, after `persist_segment`:

```python
    async def _drain_transcripts(self, participant_id: str, transcriber: AzureStreamingTranscriber) -> None:
        """Consumes the transcriber's segment queue until cancelled. Without
        this, Azure recognizes speech and nothing ever stores it."""
        while True:
            segment = await transcriber.segments.get()
            try:
                await self.persist_segment(participant_id, segment)
            except Exception:
                logger.exception("failed to persist transcript segment for %s", participant_id)
```

In `RoomBot.run`'s `_forward_track`, replace the transcriber-construction block so the drain task is started alongside it:

```python
        async def _forward_track(participant_id: str, track: rtc.Track) -> None:
            drain_task = None
            try:
                transcriber = AzureStreamingTranscriber(loop)
                self._transcribers[participant_id] = transcriber
                drain_task = asyncio.ensure_future(self._drain_transcripts(participant_id, transcriber))
            except Exception:
                logger.warning("live transcription unavailable for %s (room %s)", participant_id, self.room_id)
            try:
                audio_stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE_HZ, num_channels=1)
                async for event in audio_stream:
                    samples = np.frombuffer(event.frame.data, dtype=np.int16)
                    for i in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
                        self.handle_audio_frame(participant_id, samples[i : i + FRAME_SAMPLES])
            finally:
                if drain_task is not None:
                    drain_task.cancel()
```

- [ ] **Step 10: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_transcript_persistence.py -v`
Expected: PASS (2 tests)

- [ ] **Step 11: Run the full suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (everything from Plan 2 Tasks 1-11 plus these)

- [ ] **Step 12: Commit**

```bash
git add backend/app/rooms/azure_stream.py backend/app/rooms/bot.py backend/app/api/rooms.py \
        backend/app/models/room_transcript_segment.py \
        backend/alembic/versions/c2d8e4f1a903_add_words_to_room_transcript_segments.py \
        backend/tests/rooms/test_azure_stream.py backend/tests/rooms/test_transcript_persistence.py
git commit -m "Rooms: persist live transcripts with word timings; fix participant identity mismatch

Two defects that would have produced all-zero reports: the bot keyed speech
segments by a composite LiveKit identity while the stats layer keyed by
RoomParticipant.id, and nothing ever drained the Azure transcriber queue.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: Room topic + per-participant transcript assembly

Rooms need a discussion topic for topic-alignment scoring to mean anything (the `rooms` table has none). This task adds it and the pure function that turns stored segment rows into the `(full_text, words, duration_s)` shape every existing scorer already accepts.

**Files:**
- Modify: `backend/app/models/room.py`
- Modify: `backend/app/schemas/room.py`
- Modify: `backend/app/api/rooms.py`
- Create: `backend/alembic/versions/d3e9f5a2b104_add_topic_text_to_rooms.py`
- Create: `backend/app/rooms/participant_analysis.py`
- Create: `backend/tests/rooms/test_participant_analysis.py`
- Modify: `backend/tests/rooms/test_rooms_api.py`

**Interfaces:**
- Consumes: `RoomTranscriptSegment.words` (Task 12).
- Produces: `Room.topic_text` (nullable str column); `RoomWord(word, start_s, end_s)` and `ParticipantTranscript(participant_id, full_text, words, duration_s)` (frozen dataclasses); `assemble_participant_transcripts(rows: Sequence) -> dict[str, ParticipantTranscript]` — all in `app.rooms.participant_analysis`. Used by Tasks 14, 16, 17.

- [ ] **Step 1: Write the failing test for transcript assembly**

Create `backend/tests/rooms/test_participant_analysis.py`:

```python
import pytest

from app.rooms.participant_analysis import ParticipantTranscript, assemble_participant_transcripts


class _Row:
    """Stands in for a RoomTranscriptSegment ORM row — assembly is a pure
    function over duck-typed rows, so it needs no database."""

    def __init__(self, participant_id, text, start_s, end_s, words):
        self.participant_id = participant_id
        self.text = text
        self.start_s = start_s
        self.end_s = end_s
        self.words = words


def test_segments_are_grouped_per_participant_in_time_order():
    rows = [
        _Row("b", "second", 5.0, 6.0, [{"word": "second", "start_s": 5.0, "end_s": 6.0}]),
        _Row("a", "later", 8.0, 9.0, [{"word": "later", "start_s": 8.0, "end_s": 9.0}]),
        _Row("a", "first", 0.0, 1.0, [{"word": "first", "start_s": 0.0, "end_s": 1.0}]),
    ]
    result = assemble_participant_transcripts(rows)
    assert result["a"].full_text == "first later"
    assert result["b"].full_text == "second"


def test_word_timings_are_flattened_in_order():
    rows = [
        _Row("a", "one two", 0.0, 2.0, [
            {"word": "one", "start_s": 0.0, "end_s": 1.0},
            {"word": "two", "start_s": 1.0, "end_s": 2.0},
        ]),
        _Row("a", "three", 4.0, 5.0, [{"word": "three", "start_s": 4.0, "end_s": 5.0}]),
    ]
    result = assemble_participant_transcripts(rows)
    assert [w.word for w in result["a"].words] == ["one", "two", "three"]
    assert result["a"].words[2].start_s == pytest.approx(4.0)


def test_duration_spans_from_first_start_to_last_end():
    rows = [
        _Row("a", "one", 2.0, 3.0, [{"word": "one", "start_s": 2.0, "end_s": 3.0}]),
        _Row("a", "two", 9.0, 12.0, [{"word": "two", "start_s": 9.0, "end_s": 12.0}]),
    ]
    result = assemble_participant_transcripts(rows)
    assert result["a"].duration_s == pytest.approx(10.0)


def test_a_segment_with_no_word_timings_still_contributes_its_text():
    rows = [_Row("a", "no timings here", 0.0, 3.0, None)]
    result = assemble_participant_transcripts(rows)
    assert result["a"].full_text == "no timings here"
    assert result["a"].words == ()


def test_no_rows_produces_an_empty_mapping():
    assert assemble_participant_transcripts([]) == {}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_participant_analysis.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.participant_analysis'`

- [ ] **Step 3: Implement transcript assembly**

Create `backend/app/rooms/participant_analysis.py`:

```python
"""Turns a room's stored transcript segments into per-participant transcripts
shaped exactly like a solo session's — (full_text, words, duration_s) — so the
existing, already-tested solo-trainer scorers (app/metrics/pipeline.py,
app/scoring/relevance.py) run per room participant with no changes at all.

Pure functions over duck-typed rows: no DB, no network, fully unit-testable.
"""
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class RoomWord:
    """Satisfies app.metrics.types.WordLike — the duck-typed protocol the
    metrics and relevance packages accept."""

    word: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class ParticipantTranscript:
    participant_id: str
    full_text: str
    words: tuple[RoomWord, ...]
    duration_s: float


def assemble_participant_transcripts(rows: Sequence) -> dict[str, ParticipantTranscript]:
    """Group RoomTranscriptSegment-like rows by participant, ordered by time.

    duration_s spans that participant's first word to their last — their own
    active window, not the room's wall-clock length. Talk-time share against
    the whole session is already covered separately by
    app/rooms/live_stats.py; here the point is per-speaker pace (WPM), which
    would be meaningless if divided by a session length they were mostly
    silent for.
    """
    by_participant: dict[str, list] = {}
    for row in rows:
        by_participant.setdefault(str(row.participant_id), []).append(row)

    transcripts: dict[str, ParticipantTranscript] = {}
    for participant_id, participant_rows in by_participant.items():
        ordered = sorted(participant_rows, key=lambda r: r.start_s)
        full_text = " ".join(r.text for r in ordered if r.text).strip()

        words: list[RoomWord] = []
        for row in ordered:
            for raw in row.words or []:
                words.append(
                    RoomWord(word=raw["word"], start_s=raw["start_s"], end_s=raw["end_s"])
                )

        duration_s = max(r.end_s for r in ordered) - min(r.start_s for r in ordered)
        transcripts[participant_id] = ParticipantTranscript(
            participant_id=participant_id,
            full_text=full_text,
            words=tuple(words),
            duration_s=max(duration_s, 0.0),
        )
    return transcripts
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_participant_analysis.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing test for the room topic**

Append to `backend/tests/rooms/test_rooms_api.py`:

```python
async def test_create_room_stores_the_discussion_topic(client, db_session):
    user = await _create_user(db_session, "topic@example.com", "Topic")
    resp = await client.post(
        "/rooms",
        json={"mode": "identified", "topic_text": "Should AI be used in hiring decisions?"},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201
    assert resp.json()["topic_text"] == "Should AI be used in hiring decisions?"


async def test_create_room_without_a_topic_is_still_allowed(client, db_session):
    user = await _create_user(db_session, "notopic@example.com", "NoTopic")
    resp = await client.post("/rooms", json={"mode": "identified"}, headers=_auth_headers(user))
    assert resp.status_code == 201
    assert resp.json()["topic_text"] is None
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_rooms_api.py -v`
Expected: FAIL — `KeyError: 'topic_text'` (the field doesn't exist on the response yet)

- [ ] **Step 7: Add `topic_text` to the model, migration, and schemas**

In `backend/app/models/room.py`, add the column after `max_participants`:

```python
    # The discussion prompt, typed by the host at room creation. Free text
    # rather than a topics FK: a GD topic is usually improvised, and the
    # topic-alignment scorer only needs a string to embed.
    topic_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
```

Create `backend/alembic/versions/d3e9f5a2b104_add_topic_text_to_rooms.py`:

```python
"""add topic_text to rooms

Revision ID: d3e9f5a2b104
Revises: c2d8e4f1a903
Create Date: 2026-08-19 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3e9f5a2b104'
down_revision: Union[str, None] = 'c2d8e4f1a903'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rooms', sa.Column('topic_text', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('rooms', 'topic_text')
```

In `backend/app/schemas/room.py`, add `topic_text` to both `RoomCreate` and `RoomResponse`:

```python
class RoomCreate(BaseModel):
    mode: RoomMode
    max_participants: int = 6
    topic_text: str | None = None


class RoomResponse(BaseModel):
    id: uuid.UUID
    join_code: str
    mode: RoomMode
    status: RoomStatus
    max_participants: int
    topic_text: str | None
    host_user_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True
```

In `backend/app/api/rooms.py`, pass it through in `create_room`:

```python
    room = Room(
        host_user_id=current_user.id,
        mode=payload.mode,
        max_participants=payload.max_participants,
        topic_text=payload.topic_text,
    )
```

Run: `cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head`
Expected: migration applies with no errors.

- [ ] **Step 8: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_rooms_api.py -v`
Expected: PASS (8 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/room.py backend/app/schemas/room.py backend/app/api/rooms.py \
        backend/alembic/versions/d3e9f5a2b104_add_topic_text_to_rooms.py \
        backend/app/rooms/participant_analysis.py \
        backend/tests/rooms/test_participant_analysis.py backend/tests/rooms/test_rooms_api.py
git commit -m "Rooms: room discussion topic and per-participant transcript assembly

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 14: Per-participant speech quality, topic alignment, and pronunciation

The core reuse task: runs the solo trainer's existing scorers once per participant, each isolated so one failure never blocks the rest of the report.

Note on fillers: `app/metrics/fluency.py` already handles multi-word fillers correctly via `MULTI_WORD_FILLERS` n-gram matching over normalized tokens (the spec flagged this as worth verifying — it checks out, and Step 1's test pins the behavior so it stays that way).

**Files:**
- Create: `backend/app/rooms/participant_scoring.py`
- Create: `backend/tests/rooms/test_participant_scoring.py`

**Interfaces:**
- Consumes: `ParticipantTranscript` (Task 13); `compute_all_metrics` (existing, `app.metrics.pipeline`); `score_relevance` (existing, `app.scoring.relevance`); `score_pronunciation` (existing, `app.scoring.pronunciation`); `ScorerNotConfigured` (existing, `app.scoring.types`).
- Produces: `score_participant(transcript: ParticipantTranscript, topic_text: str | None, audio_path: Path | None) -> dict` in `app.rooms.participant_scoring`. Returns keys `speech_metrics`, `relevance_status`, `relevance_result`, `relevance_error`, `pronunciation_status`, `pronunciation_result`, `pronunciation_error`. Used by Tasks 16, 17.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/rooms/test_participant_scoring.py`:

```python
from pathlib import Path

import pytest

from app.rooms.participant_analysis import ParticipantTranscript, RoomWord
from app.rooms.participant_scoring import score_participant
from app.scoring.types import PronunciationResult, RelevanceResult, ScorerNotConfigured


def _transcript(text: str, words: tuple, duration_s: float = 10.0) -> ParticipantTranscript:
    return ParticipantTranscript(
        participant_id="p1", full_text=text, words=words, duration_s=duration_s
    )


def _words(*pairs) -> tuple:
    return tuple(RoomWord(word=w, start_s=s, end_s=e) for w, s, e in pairs)


def test_speech_metrics_count_multi_word_fillers(monkeypatch):
    # "you know" is a two-token filler — it must be counted, and the existing
    # fluency module's n-gram matching is what makes that work.
    monkeypatch.setattr("app.rooms.participant_scoring.score_relevance", lambda *a, **k: RelevanceResult(status="ok", mean_relevance=0.5))
    transcript = _transcript(
        "um you know the point",
        _words(("um", 0.0, 0.4), ("you", 0.5, 0.8), ("know", 0.8, 1.1), ("the", 1.2, 1.4), ("point", 1.5, 2.0)),
    )
    result = score_participant(transcript, topic_text="a topic", audio_path=None)
    assert result["speech_metrics"]["filler_count"] == 2  # "um" + "you know"


def test_relevance_runs_when_a_topic_is_set(monkeypatch):
    monkeypatch.setattr(
        "app.rooms.participant_scoring.score_relevance",
        lambda full_text, words, topic_text: RelevanceResult(status="ok", mean_relevance=0.82),
    )
    transcript = _transcript("on topic talk", _words(("on", 0.0, 0.3), ("topic", 0.4, 0.9)))
    result = score_participant(transcript, topic_text="the topic", audio_path=None)
    assert result["relevance_status"] == "ok"
    assert result["relevance_result"]["mean_relevance"] == 0.82


def test_relevance_is_not_configured_when_the_room_has_no_topic():
    transcript = _transcript("some talk", _words(("some", 0.0, 0.3)))
    result = score_participant(transcript, topic_text=None, audio_path=None)
    assert result["relevance_status"] == "not_configured"
    assert result["relevance_result"] is None


def test_a_crashing_relevance_scorer_is_isolated(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("app.rooms.participant_scoring.score_relevance", _boom)
    transcript = _transcript("some talk", _words(("some", 0.0, 0.3)))
    result = score_participant(transcript, topic_text="a topic", audio_path=None)
    assert result["relevance_status"] == "error"
    assert "model exploded" in result["relevance_error"]
    # the deterministic metrics still came through
    assert result["speech_metrics"]["word_count"] == 1


def test_pronunciation_is_not_configured_without_audio():
    transcript = _transcript("some talk", _words(("some", 0.0, 0.3)))
    result = score_participant(transcript, topic_text=None, audio_path=None)
    assert result["pronunciation_status"] == "not_configured"


def test_pronunciation_runs_when_audio_is_present(monkeypatch, tmp_path):
    audio = tmp_path / "p1.wav"
    audio.write_bytes(b"fake")
    monkeypatch.setattr(
        "app.rooms.participant_scoring.score_pronunciation",
        lambda path: PronunciationResult(status="ok", accuracy_score=88.0),
    )
    transcript = _transcript("some talk", _words(("some", 0.0, 0.3)))
    result = score_participant(transcript, topic_text=None, audio_path=audio)
    assert result["pronunciation_status"] == "ok"
    assert result["pronunciation_result"]["accuracy_score"] == 88.0


def test_an_unconfigured_pronunciation_scorer_is_recorded_not_raised(monkeypatch, tmp_path):
    audio = tmp_path / "p1.wav"
    audio.write_bytes(b"fake")

    def _not_configured(path):
        raise ScorerNotConfigured("AZURE_SPEECH_KEY not set")

    monkeypatch.setattr("app.rooms.participant_scoring.score_pronunciation", _not_configured)
    transcript = _transcript("some talk", _words(("some", 0.0, 0.3)))
    result = score_participant(transcript, topic_text=None, audio_path=audio)
    assert result["pronunciation_status"] == "not_configured"
    assert "AZURE_SPEECH_KEY" in result["pronunciation_error"]


def test_an_empty_transcript_produces_zeroed_metrics_without_crashing():
    transcript = _transcript("", (), duration_s=0.0)
    result = score_participant(transcript, topic_text=None, audio_path=None)
    assert result["speech_metrics"]["word_count"] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_participant_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.participant_scoring'`

- [ ] **Step 3: Implement**

Create `backend/app/rooms/participant_scoring.py`:

```python
"""Runs the solo trainer's existing scorers once per room participant.

Nothing here reimplements analysis — app/metrics/pipeline.py (fluency,
vocabulary, syntax, fillers), app/scoring/relevance.py (topic alignment via
local embeddings) and app/scoring/pronunciation.py (Azure) are used exactly
as the solo path uses them. The only new thing is the isolation wrapper:
in a room, one participant's failed scorer must not blank out anyone else's
report, so every scorer is caught individually and recorded with the
status/result/error convention from app/scoring/types.py.
"""
import logging
from dataclasses import asdict
from pathlib import Path

from app.metrics.pipeline import compute_all_metrics
from app.rooms.participant_analysis import ParticipantTranscript
from app.scoring.pronunciation import score_pronunciation
from app.scoring.relevance import score_relevance
from app.scoring.types import ScorerNotConfigured

logger = logging.getLogger("rooms.participant_scoring")


def _payload(result) -> dict | None:
    """Dataclass -> JSON-storable dict, dropping the fields stored separately.
    Same helper shape as app/scoring/pipeline.py's _payload."""
    if result.status != "ok":
        return None
    d = asdict(result)
    d.pop("status", None)
    d.pop("error_detail", None)
    return d


def score_participant(
    transcript: ParticipantTranscript,
    topic_text: str | None,
    audio_path: Path | None,
) -> dict:
    speech_metrics = compute_all_metrics(
        transcript.full_text, transcript.words, transcript.duration_s
    )

    # --- topic alignment ---
    if not topic_text:
        relevance_status, relevance_result, relevance_error = (
            "not_configured",
            None,
            "room has no topic_text set",
        )
    else:
        try:
            relevance = score_relevance(transcript.full_text, transcript.words, topic_text)
            relevance_status = relevance.status
            relevance_result = _payload(relevance)
            relevance_error = relevance.error_detail
        except Exception as exc:  # noqa: BLE001 — deliberate per-participant isolation boundary
            logger.exception("relevance scorer crashed for participant %s", transcript.participant_id)
            relevance_status, relevance_result, relevance_error = "error", None, str(exc)[:500]

    # --- pronunciation ---
    if audio_path is None or not Path(audio_path).exists():
        pronunciation_status, pronunciation_result, pronunciation_error = (
            "not_configured",
            None,
            "no recorded audio for this participant",
        )
    else:
        try:
            pronunciation = score_pronunciation(Path(audio_path))
            pronunciation_status = pronunciation.status
            pronunciation_result = _payload(pronunciation)
            pronunciation_error = pronunciation.error_detail
        except ScorerNotConfigured as exc:
            pronunciation_status, pronunciation_result, pronunciation_error = (
                "not_configured",
                None,
                str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("pronunciation scorer crashed for participant %s", transcript.participant_id)
            pronunciation_status, pronunciation_result, pronunciation_error = "error", None, str(exc)[:500]

    return {
        "speech_metrics": speech_metrics,
        "relevance_status": relevance_status,
        "relevance_result": relevance_result,
        "relevance_error": relevance_error,
        "pronunciation_status": pronunciation_status,
        "pronunciation_result": pronunciation_result,
        "pronunciation_error": pronunciation_error,
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_participant_scoring.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rooms/participant_scoring.py backend/tests/rooms/test_participant_scoring.py
git commit -m "Rooms: per-participant speech quality, topic alignment and pronunciation

Reuses the solo trainer's existing scorers unchanged; adds per-participant
failure isolation so one bad scorer never blanks the whole room report.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 15: Agreement / disagreement stance detection

Deterministic marker-phrase heuristic in the spirit of `live_stats.py` — no new model, no API call. Feeds the Teamwork and Listening competency scores in Task 16.

**Files:**
- Create: `backend/app/rooms/stance.py`
- Create: `backend/tests/rooms/test_stance.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `StanceCounts(agreements: int, disagreements: int, neutral: int)` (frozen dataclass); `classify_stance(text: str) -> str` (returns `"agreement" | "disagreement" | "neutral"`); `count_stances(texts: Sequence[str]) -> StanceCounts` — all in `app.rooms.stance`. Used by Tasks 16, 17.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/rooms/test_stance.py`:

```python
from app.rooms.stance import StanceCounts, classify_stance, count_stances


def test_explicit_agreement_is_detected():
    assert classify_stance("I agree with that point") == "agreement"
    assert classify_stance("Exactly, that's right") == "agreement"


def test_explicit_disagreement_is_detected():
    assert classify_stance("I disagree with that") == "disagreement"
    assert classify_stance("That's not quite right") == "disagreement"


def test_disagreement_wins_when_both_markers_appear():
    # "I agree, but ..." is a soft disagreement — the pushback is the point.
    assert classify_stance("I agree, but that ignores the cost") == "disagreement"


def test_a_plain_statement_is_neutral():
    assert classify_stance("The market grew by twelve percent last year") == "neutral"


def test_markers_must_be_whole_phrases_not_substrings():
    # "but" inside "distribute"/"buttons" must not read as disagreement
    assert classify_stance("We should distribute the buttons evenly") == "neutral"


def test_empty_text_is_neutral():
    assert classify_stance("") == "neutral"


def test_count_stances_totals_each_category():
    counts = count_stances([
        "I agree completely",
        "I disagree with that",
        "The figure was twelve percent",
        "Absolutely",
    ])
    assert counts == StanceCounts(agreements=2, disagreements=1, neutral=1)


def test_count_stances_on_no_texts_is_all_zero():
    assert count_stances([]) == StanceCounts(agreements=0, disagreements=0, neutral=0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_stance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.stance'`

- [ ] **Step 3: Implement**

Create `backend/app/rooms/stance.py`:

```python
"""Agreement/disagreement detection over discussion turns.

A deliberately small marker-phrase heuristic, in the same spirit as the
deterministic thresholds in app/rooms/live_stats.py — not an ML model. It
feeds the Teamwork/Listening competency signals (app/rooms/competencies.py):
a participant who only ever disagrees reads differently from one who builds
on others' points. The richer, nuanced read is the LLM pass's job
(app/scoring/group_dynamics.py); this just needs to be cheap, local and
explainable.

Matching is whole-phrase against a word-boundary regex so 'but' inside
'distribute' can't register as disagreement.
"""
import re
from dataclasses import dataclass
from typing import Sequence

AGREEMENT_MARKERS = (
    "i agree",
    "agreed",
    "exactly",
    "absolutely",
    "that's right",
    "thats right",
    "good point",
    "building on that",
    "to add to that",
)

DISAGREEMENT_MARKERS = (
    "i disagree",
    "i don't agree",
    "i dont agree",
    "but",
    "however",
    "on the other hand",
    "that's not",
    "thats not",
    "not quite",
    "i'd push back",
    "id push back",
)


@dataclass(frozen=True)
class StanceCounts:
    agreements: int
    disagreements: int
    neutral: int


def _contains_marker(text: str, markers: Sequence[str]) -> bool:
    for marker in markers:
        if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text):
            return True
    return False


def classify_stance(text: str) -> str:
    """One turn -> "agreement" | "disagreement" | "neutral".

    Disagreement wins ties: "I agree, but that ignores the cost" is pushback
    wearing a polite opener, and counting it as agreement would flatter the
    speaker's teamwork score for what is actually a challenge.
    """
    if not text:
        return "neutral"
    lowered = text.lower()
    if _contains_marker(lowered, DISAGREEMENT_MARKERS):
        return "disagreement"
    if _contains_marker(lowered, AGREEMENT_MARKERS):
        return "agreement"
    return "neutral"


def count_stances(texts: Sequence[str]) -> StanceCounts:
    agreements = disagreements = neutral = 0
    for text in texts:
        stance = classify_stance(text)
        if stance == "agreement":
            agreements += 1
        elif stance == "disagreement":
            disagreements += 1
        else:
            neutral += 1
    return StanceCounts(agreements=agreements, disagreements=disagreements, neutral=neutral)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_stance.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rooms/stance.py backend/tests/rooms/test_stance.py
git commit -m "Rooms: deterministic agreement/disagreement stance detection

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 16: Composite competency scores

Pure arithmetic over numbers the previous tasks already computed — no new external calls. Produces the seven 0-100 competency scores the spec names.

**Files:**
- Create: `backend/app/rooms/competencies.py`
- Create: `backend/tests/rooms/test_competencies.py`

**Interfaces:**
- Consumes: `ParticipantStats` (Plan 2 Task 1, `app.rooms.live_stats`); `StanceCounts` (Task 15).
- Produces: `CompetencyScores(leadership, engagement, listening, communication_clarity, topic_alignment, confidence, teamwork)` (frozen dataclass, all `float` 0-100); `compute_competencies(*, stats, speech_metrics, relevance_mean, stance, spoke_first, participant_count) -> CompetencyScores` in `app.rooms.competencies`. Used by Task 17.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/rooms/test_competencies.py`:

```python
import pytest

from app.rooms.competencies import compute_competencies
from app.rooms.live_stats import ParticipantStats
from app.rooms.stance import StanceCounts

BASE_METRICS = {
    "word_count": 200,
    "wpm_overall": 135.0,
    "filler_rate_per_100_words": 0.0,
    "mtld_score": 60.0,
    "mean_length_utterance": 12.0,
}


def _stats(**overrides) -> ParticipantStats:
    defaults = dict(
        participant_id="p1",
        talk_time_s=60.0,
        talk_time_pct=25.0,
        turn_count=6,
        interruptions_made=0,
        interruptions_received=0,
        longest_monologue_s=20.0,
        silence_pct=75.0,
    )
    defaults.update(overrides)
    return ParticipantStats(**defaults)


def _compute(**overrides):
    kwargs = dict(
        stats=_stats(),
        speech_metrics=dict(BASE_METRICS),
        relevance_mean=0.8,
        stance=StanceCounts(agreements=2, disagreements=1, neutral=3),
        spoke_first=False,
        participant_count=4,
    )
    kwargs.update(overrides)
    return compute_competencies(**kwargs)


def test_every_score_is_within_zero_to_one_hundred():
    scores = _compute()
    for name, value in scores.__dict__.items():
        assert 0.0 <= value <= 100.0, f"{name} out of range: {value}"


def test_speaking_first_raises_leadership():
    assert _compute(spoke_first=True).leadership > _compute(spoke_first=False).leadership


def test_topic_alignment_tracks_relevance():
    assert _compute(relevance_mean=0.9).topic_alignment == pytest.approx(90.0)
    assert _compute(relevance_mean=0.2).topic_alignment == pytest.approx(20.0)


def test_topic_alignment_is_zero_when_relevance_is_unavailable():
    assert _compute(relevance_mean=None).topic_alignment == 0.0


def test_fillers_lower_confidence():
    clean = _compute(speech_metrics={**BASE_METRICS, "filler_rate_per_100_words": 0.0})
    filled = _compute(speech_metrics={**BASE_METRICS, "filler_rate_per_100_words": 15.0})
    assert filled.confidence < clean.confidence


def test_interrupting_others_lowers_teamwork():
    polite = _compute(stats=_stats(interruptions_made=0))
    rude = _compute(stats=_stats(interruptions_made=6))
    assert rude.teamwork < polite.teamwork


def test_agreeing_and_building_raises_listening():
    supportive = _compute(stance=StanceCounts(agreements=6, disagreements=0, neutral=2))
    silent = _compute(stance=StanceCounts(agreements=0, disagreements=0, neutral=8))
    assert supportive.listening > silent.listening


def test_engagement_rewards_a_fair_share_of_airtime():
    # an even split across 4 people is 25% — that should score at least as well
    # as barely speaking at all
    fair = _compute(stats=_stats(talk_time_pct=25.0, turn_count=6))
    quiet = _compute(stats=_stats(talk_time_pct=2.0, turn_count=1))
    assert fair.engagement > quiet.engagement


def test_a_silent_participant_scores_without_crashing():
    scores = _compute(
        stats=_stats(talk_time_s=0.0, talk_time_pct=0.0, turn_count=0, longest_monologue_s=0.0, silence_pct=100.0),
        speech_metrics={**BASE_METRICS, "word_count": 0, "wpm_overall": 0.0, "mtld_score": 0.0, "mean_length_utterance": 0.0},
        relevance_mean=None,
        stance=StanceCounts(agreements=0, disagreements=0, neutral=0),
    )
    assert scores.engagement == pytest.approx(0.0, abs=5.0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_competencies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.rooms.competencies'`

- [ ] **Step 3: Implement**

Create `backend/app/rooms/competencies.py`:

```python
"""The seven composite competency scores shown on a participant's report card.

Pure arithmetic over numbers already computed elsewhere (live_stats,
participant_scoring, stance) — no external calls, no model. The weights are
deliberate but not empirically tuned; they are the kind of thing the
EVALUATION.md harness should eventually calibrate against human GD ratings,
exactly as the solo trainer's headline scoring was. Treat them as a
defensible starting point, not ground truth.

Design note: "more talking" is not simply "better". Engagement and Leadership
reward reaching a fair share of the floor and then flatten out, so a
participant who monopolises a discussion cannot max out the card — that
behaviour shows up as a high dominance index and lost Teamwork points
instead.
"""
from dataclasses import dataclass

from app.rooms.live_stats import ParticipantStats
from app.rooms.stance import StanceCounts

IDEAL_WPM = 135.0  # conversational pace; both much faster and much slower cost points
WPM_TOLERANCE = 60.0  # WPM distance from ideal at which the pace score hits zero
MAX_USEFUL_MTLD = 80.0  # lexical diversity at/above which vocabulary scores full marks
FILLER_PENALTY_PER_UNIT = 4.0  # points lost per filler-per-100-words


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _fair_share_ratio(talk_time_pct: float, participant_count: int) -> float:
    """1.0 once a participant reaches an even share of the floor, capped there."""
    if participant_count <= 0:
        return 0.0
    fair_share = 100.0 / participant_count
    return min(1.0, talk_time_pct / fair_share) if fair_share > 0 else 0.0


@dataclass(frozen=True)
class CompetencyScores:
    leadership: float
    engagement: float
    listening: float
    communication_clarity: float
    topic_alignment: float
    confidence: float
    teamwork: float


def compute_competencies(
    *,
    stats: ParticipantStats,
    speech_metrics: dict,
    relevance_mean: float | None,
    stance: StanceCounts,
    spoke_first: bool,
    participant_count: int,
) -> CompetencyScores:
    share = _fair_share_ratio(stats.talk_time_pct, participant_count)
    total_turns_of_speech = stance.agreements + stance.disagreements + stance.neutral

    # --- topic alignment: relevance is 0-1, the card is 0-100 ---
    topic_alignment = _clamp(relevance_mean * 100.0) if relevance_mean is not None else 0.0

    # --- confidence: penalised by fillers and by hesitant, very slow delivery ---
    filler_rate = speech_metrics.get("filler_rate_per_100_words", 0.0) or 0.0
    confidence = _clamp(100.0 - filler_rate * FILLER_PENALTY_PER_UNIT)

    # --- clarity: pace near conversational + varied vocabulary + fluent delivery ---
    wpm = speech_metrics.get("wpm_overall", 0.0) or 0.0
    pace_score = _clamp(100.0 - abs(IDEAL_WPM - wpm) / WPM_TOLERANCE * 100.0) if wpm > 0 else 0.0
    mtld = speech_metrics.get("mtld_score", 0.0) or 0.0
    vocabulary_score = _clamp(mtld / MAX_USEFUL_MTLD * 100.0)
    communication_clarity = _clamp(
        pace_score * 0.35 + vocabulary_score * 0.35 + confidence * 0.30
    )

    # --- engagement: showing up to the floor, and doing so repeatedly ---
    turn_score = _clamp(min(1.0, stats.turn_count / 5.0) * 100.0)
    engagement = _clamp(share * 100.0 * 0.6 + turn_score * 0.4)

    # --- listening: acknowledging others rather than only broadcasting ---
    if total_turns_of_speech == 0:
        listening = 0.0
    else:
        acknowledgement_rate = stance.agreements / total_turns_of_speech
        interruption_penalty = min(40.0, stats.interruptions_made * 8.0)
        listening = _clamp(40.0 + acknowledgement_rate * 60.0 - interruption_penalty)

    # --- leadership: initiative + earning the floor + staying on point ---
    leadership = _clamp(
        (10.0 if spoke_first else 0.0)
        + share * 40.0
        + turn_score * 0.25
        + topic_alignment * 0.25
    )

    # --- teamwork: contributing without steamrolling ---
    teamwork = _clamp(
        70.0
        + (stance.agreements * 5.0)
        - (stats.interruptions_made * 7.0)
        - max(0.0, (stats.talk_time_pct - 100.0 / max(participant_count, 1)) * 0.5)
    )

    return CompetencyScores(
        leadership=round(leadership, 1),
        engagement=round(engagement, 1),
        listening=round(listening, 1),
        communication_clarity=round(communication_clarity, 1),
        topic_alignment=round(topic_alignment, 1),
        confidence=round(confidence, 1),
        teamwork=round(teamwork, 1),
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_competencies.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rooms/competencies.py backend/tests/rooms/test_competencies.py
git commit -m "Rooms: composite competency scores (leadership, engagement, listening, clarity, alignment, confidence, teamwork)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 17: Wire per-participant analytics into the job and the report API

Extends `analyze_room_session` to run Tasks 14-16 per participant, stores the result, and exposes it through `GET /rooms/{id}/report` — alias-labeled, so anonymous mode stays anonymous.

**Files:**
- Modify: `backend/app/models/room_report.py`
- Create: `backend/alembic/versions/e4fa06b3c215_add_participant_analytics_to_room_reports.py`
- Modify: `backend/app/jobs/room_analysis.py`
- Modify: `backend/app/schemas/room_report.py`
- Modify: `backend/app/api/rooms.py`
- Create: `backend/tests/rooms/test_room_analytics_integration.py`

**Interfaces:**
- Consumes: `assemble_participant_transcripts` (Task 13); `score_participant` (Task 14); `count_stances` (Task 15); `compute_competencies` (Task 16); `room_participant_audio_path` (Plan 2 Task 7).
- Produces: `RoomReport.participant_analytics` (JSON column, keyed by participant id); `RoomParticipantReport.analytics` and `.competencies` on the report response.

- [ ] **Step 1: Write the failing integration test**

Create `backend/tests/rooms/test_room_analytics_integration.py`:

```python
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.room_report import RoomReport
from app.models.room_transcript_segment import RoomTranscriptSegment
from app.models.user import User


@pytest.fixture(autouse=True)
def _no_external_scorers(monkeypatch):
    # No Anthropic key -> qualitative pass takes the not_configured path.
    # No topic -> relevance takes the not_configured path. Both deterministic.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _words(*pairs):
    return [{"word": w, "start_s": s, "end_s": e} for w, s, e in pairs]


async def _seed(db_session, topic_text=None):
    host = User(email="ahost@example.com", password_hash=hash_password("pw"), name="Host")
    guest = User(email="aguest@example.com", password_hash=hash_password("pw"), name="Guest")
    db_session.add_all([host, guest])
    await db_session.flush()

    room = Room(
        host_user_id=host.id, mode=RoomMode.IDENTIFIED, status=RoomStatus.ENDED, topic_text=topic_text
    )
    db_session.add(room)
    await db_session.flush()

    p1 = RoomParticipant(room_id=room.id, user_id=host.id, alias_name="Host", livekit_identity="x1")
    p2 = RoomParticipant(room_id=room.id, user_id=guest.id, alias_name="Guest", livekit_identity="x2")
    db_session.add_all([p1, p2])
    await db_session.flush()

    db_session.add_all([
        RoomTranscriptSegment(
            room_id=room.id, participant_id=p1.id,
            text="I think we should focus on sustainability", start_s=0.0, end_s=4.0, is_final=True,
            words=_words(("I", 0.0, 0.2), ("think", 0.3, 0.6), ("we", 0.7, 0.9),
                         ("should", 1.0, 1.4), ("focus", 1.5, 2.0), ("on", 2.1, 2.3),
                         ("sustainability", 2.4, 4.0)),
        ),
        RoomTranscriptSegment(
            room_id=room.id, participant_id=p2.id,
            text="I agree with that point", start_s=5.0, end_s=8.0, is_final=True,
            words=_words(("I", 5.0, 5.2), ("agree", 5.3, 5.8), ("with", 5.9, 6.1),
                         ("that", 6.2, 6.5), ("point", 6.6, 8.0)),
        ),
    ])
    await db_session.commit()
    return room, host, guest, p1, p2


async def test_report_includes_per_participant_analytics(db_session, db_session_maker, monkeypatch):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, _, _, p1, p2 = await _seed(db_session)

    assert await analyze_room_session({}, str(room.id)) == "ok"

    report = (
        await db_session.execute(select(RoomReport).where(RoomReport.room_id == room.id))
    ).scalar_one()
    analytics = report.participant_analytics
    assert set(analytics) == {str(p1.id), str(p2.id)}
    assert analytics[str(p1.id)]["speech_metrics"]["word_count"] == 7
    assert analytics[str(p2.id)]["stance"]["agreements"] == 1


async def test_competency_scores_are_persisted_for_every_participant(db_session, db_session_maker, monkeypatch):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, _, _, p1, p2 = await _seed(db_session)
    await analyze_room_session({}, str(room.id))

    report = (
        await db_session.execute(select(RoomReport).where(RoomReport.room_id == room.id))
    ).scalar_one()
    competencies = report.participant_analytics[str(p1.id)]["competencies"]
    assert set(competencies) == {
        "leadership", "engagement", "listening", "communication_clarity",
        "topic_alignment", "confidence", "teamwork",
    }
    assert all(0.0 <= v <= 100.0 for v in competencies.values())


async def test_analytics_are_marked_not_configured_when_the_room_has_no_topic(
    db_session, db_session_maker, monkeypatch
):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, _, _, p1, _ = await _seed(db_session, topic_text=None)
    await analyze_room_session({}, str(room.id))

    report = (
        await db_session.execute(select(RoomReport).where(RoomReport.room_id == room.id))
    ).scalar_one()
    assert report.participant_analytics[str(p1.id)]["relevance_status"] == "not_configured"


async def test_report_api_exposes_analytics_and_competencies(client, db_session, db_session_maker, monkeypatch):
    from app.jobs.room_analysis import analyze_room_session

    monkeypatch.setattr("app.jobs.room_analysis.async_session_maker", db_session_maker)
    room, host, guest, p1, p2 = await _seed(db_session)
    await analyze_room_session({}, str(room.id))

    resp = await client.get(
        f"/rooms/{room.id}/report",
        headers={"Authorization": f"Bearer {create_access_token(str(guest.id))}"},
    )
    assert resp.status_code == 200
    rows = {p["label"]: p for p in resp.json()["participants"]}
    assert rows["Guest"]["is_you"] is True
    assert rows["Guest"]["competencies"]["listening"] >= 0.0
    assert rows["Host"]["analytics"]["speech_metrics"]["word_count"] == 7
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_room_analytics_integration.py -v`
Expected: FAIL with `AttributeError: 'RoomReport' object has no attribute 'participant_analytics'`

- [ ] **Step 3: Add the storage column**

In `backend/app/models/room_report.py`, add after `dominance_index`:

```python
    # Keyed by participant id; each value holds speech_metrics, relevance_*,
    # pronunciation_*, stance and competencies for that participant. JSON
    # rather than columns because the shape follows the scorers, which are
    # expected to grow — same reasoning as model_scores.py's result columns.
    participant_analytics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
```

Create `backend/alembic/versions/e4fa06b3c215_add_participant_analytics_to_room_reports.py`:

```python
"""add participant_analytics to room_reports

Revision ID: e4fa06b3c215
Revises: d3e9f5a2b104
Create Date: 2026-08-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e4fa06b3c215'
down_revision: Union[str, None] = 'd3e9f5a2b104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'room_reports',
        sa.Column('participant_analytics', sa.JSON(), nullable=False, server_default='{}'),
    )


def downgrade() -> None:
    op.drop_column('room_reports', 'participant_analytics')
```

Run: `cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head`
Expected: migration applies with no errors.

- [ ] **Step 4: Run the per-participant analytics inside the job**

In `backend/app/jobs/room_analysis.py`, add these imports:

```python
from dataclasses import asdict

from app.core.storage import room_participant_audio_path
from app.rooms.competencies import compute_competencies
from app.rooms.participant_analysis import assemble_participant_transcripts
from app.rooms.participant_scoring import score_participant
from app.rooms.stance import count_stances
```

Add this helper above `analyze_room_session`:

```python
def _build_participant_analytics(
    transcript_rows: list,
    participant_ids: list[str],
    stats: dict,
    room_id: str,
    topic_text: str | None,
) -> dict:
    """Per-participant speech quality, topic alignment, pronunciation, stance
    and competency scores. Each participant is wrapped independently so one
    bad transcript can't sink the whole room's report."""
    transcripts = assemble_participant_transcripts(transcript_rows)
    texts_by_participant: dict[str, list[str]] = {}
    for row in transcript_rows:
        texts_by_participant.setdefault(str(row.participant_id), []).append(row.text)

    first_speaker_id = (
        str(min(transcript_rows, key=lambda r: r.start_s).participant_id) if transcript_rows else None
    )

    analytics: dict = {}
    for participant_id in participant_ids:
        transcript = transcripts.get(participant_id)
        if transcript is None:
            analytics[participant_id] = {"status": "no_speech"}
            continue
        try:
            audio_path = room_participant_audio_path(room_id, participant_id)
            scored = score_participant(
                transcript, topic_text=topic_text, audio_path=audio_path
            )
            stance = count_stances(texts_by_participant.get(participant_id, []))
            relevance_mean = (scored["relevance_result"] or {}).get("mean_relevance")
            competencies = compute_competencies(
                stats=stats[participant_id],
                speech_metrics=scored["speech_metrics"],
                relevance_mean=relevance_mean,
                stance=stance,
                spoke_first=(participant_id == first_speaker_id),
                participant_count=len(participant_ids),
            )
            analytics[participant_id] = {
                **scored,
                "stance": asdict(stance),
                "competencies": asdict(competencies),
                "status": "ok",
            }
        except Exception as exc:  # noqa: BLE001 — per-participant isolation boundary
            logger.exception("per-participant analytics failed for %s in room %s", participant_id, room_id)
            analytics[participant_id] = {"status": "error", "error_detail": str(exc)[:500]}
    return analytics
```

In `analyze_room_session`, capture the room's topic while the first session is open — add this line right after `session_duration_s = ...`:

```python
        topic_text = room.topic_text
```

Then, inside the `try:` block, right after `stats_payload = {...}`, add:

```python
        participant_analytics = _build_participant_analytics(
            transcript_rows, participant_ids, stats, room_id, topic_text
        )
```

And add the new field to the `RoomReport(...)` construction, after `dominance_index=dominance,`:

```python
                    participant_analytics=participant_analytics,
```

- [ ] **Step 5: Expose it through the report API**

In `backend/app/schemas/room_report.py`, add the two fields to `RoomParticipantReport`:

```python
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
    analytics: dict = {}
    competencies: dict = {}
```

In `backend/app/api/rooms.py`, in `get_room_report`, replace the `participants = [...]` comprehension with:

```python
    analytics_by_participant = report.participant_analytics or {}
    participants = [
        RoomParticipantReport(
            participant_id=pid,
            label=all_participants[pid].alias_name if pid in all_participants else pid,
            is_you=(pid == str(requesting_participant.id)),
            analytics=analytics_by_participant.get(pid, {}),
            competencies=analytics_by_participant.get(pid, {}).get("competencies", {}),
            **{k: v for k, v in stats.items() if k != "participant_id"},
        )
        for pid, stats in report.participant_stats.items()
    ]
```

- [ ] **Step 6: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_room_analytics_integration.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Run the full suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (everything)

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/room_report.py backend/app/jobs/room_analysis.py \
        backend/app/schemas/room_report.py backend/app/api/rooms.py \
        backend/alembic/versions/e4fa06b3c215_add_participant_analytics_to_room_reports.py \
        backend/tests/rooms/test_room_analytics_integration.py
git commit -m "Rooms: per-participant analytics and competency scores in the post-session report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 18: 30-day raw-audio retention sweep

Implements the spec's retention rule: raw audio is deleted 30 days after a room ends; transcripts and reports are kept for progress tracking.

**Files:**
- Create: `backend/app/jobs/retention.py`
- Modify: `backend/app/jobs/worker.py`
- Create: `backend/tests/rooms/test_retention.py`

**Interfaces:**
- Consumes: `Room` (Plan 2 Task 3); `RoomParticipant` (Plan 2 Task 3); `room_participant_audio_path` (Plan 2 Task 7).
- Produces: `AUDIO_RETENTION_DAYS` (int) and `purge_expired_room_audio(ctx: dict) -> int` (returns the number of files deleted) in `app.jobs.retention`. Registered as an ARQ cron job.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/rooms/test_retention.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.core.storage import room_participant_audio_path
from app.models.room import Room, RoomMode, RoomStatus
from app.models.room_participant import RoomParticipant
from app.models.user import User


@pytest.fixture(autouse=True)
def _storage_dir(tmp_path):
    settings = get_settings()
    original = settings.storage_dir
    settings.storage_dir = str(tmp_path)
    yield
    settings.storage_dir = original


async def _room_ended_days_ago(db_session, days: int, email: str):
    user = User(email=email, password_hash=hash_password("pw"), name="U")
    db_session.add(user)
    await db_session.flush()
    room = Room(
        host_user_id=user.id,
        mode=RoomMode.IDENTIFIED,
        status=RoomStatus.ANALYZED,
        ended_at=datetime.now(timezone.utc) - timedelta(days=days),
    )
    db_session.add(room)
    await db_session.flush()
    participant = RoomParticipant(
        room_id=room.id, user_id=user.id, alias_name="U", livekit_identity="x"
    )
    db_session.add(participant)
    await db_session.commit()

    path = room_participant_audio_path(str(room.id), str(participant.id))
    path.write_bytes(b"fake audio")
    return room, participant, path


async def test_audio_older_than_the_window_is_deleted(db_session, db_session_maker, monkeypatch):
    from app.jobs.retention import purge_expired_room_audio

    monkeypatch.setattr("app.jobs.retention.async_session_maker", db_session_maker)
    _, participant, path = await _room_ended_days_ago(db_session, days=45, email="old@example.com")
    assert path.exists()

    deleted = await purge_expired_room_audio({})
    assert deleted == 1
    assert not path.exists()


async def test_recent_audio_is_kept(db_session, db_session_maker, monkeypatch):
    from app.jobs.retention import purge_expired_room_audio

    monkeypatch.setattr("app.jobs.retention.async_session_maker", db_session_maker)
    _, _, path = await _room_ended_days_ago(db_session, days=3, email="new@example.com")

    deleted = await purge_expired_room_audio({})
    assert deleted == 0
    assert path.exists()


async def test_the_sweep_is_safe_to_run_twice(db_session, db_session_maker, monkeypatch):
    from app.jobs.retention import purge_expired_room_audio

    monkeypatch.setattr("app.jobs.retention.async_session_maker", db_session_maker)
    await _room_ended_days_ago(db_session, days=45, email="twice@example.com")

    assert await purge_expired_room_audio({}) == 1
    assert await purge_expired_room_audio({}) == 0  # nothing left to delete, no error


async def test_a_room_that_never_ended_is_left_alone(db_session, db_session_maker, monkeypatch):
    from app.jobs.retention import purge_expired_room_audio

    monkeypatch.setattr("app.jobs.retention.async_session_maker", db_session_maker)
    user = User(email="live@example.com", password_hash=hash_password("pw"), name="U")
    db_session.add(user)
    await db_session.flush()
    room = Room(host_user_id=user.id, mode=RoomMode.IDENTIFIED, status=RoomStatus.LIVE, ended_at=None)
    db_session.add(room)
    await db_session.flush()
    participant = RoomParticipant(room_id=room.id, user_id=user.id, alias_name="U", livekit_identity="x")
    db_session.add(participant)
    await db_session.commit()
    path = room_participant_audio_path(str(room.id), str(participant.id))
    path.write_bytes(b"fake audio")

    assert await purge_expired_room_audio({}) == 0
    assert path.exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_retention.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.jobs.retention'`

- [ ] **Step 3: Implement the sweep**

Create `backend/app/jobs/retention.py`:

```python
"""Scheduled deletion of raw room audio.

Implements the retention half of the spec's Consent & retention section:
raw audio is deleted AUDIO_RETENTION_DAYS after a room ends, while
transcripts and reports are kept indefinitely so participants keep their
progress history. Deleting the recording does not delete the analysis.

Runs as an ARQ cron job (see app/jobs/worker.py). Idempotent: a file already
gone is not an error, so a re-run or an overlapping run is harmless.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.db import async_session_maker
from app.core.storage import room_participant_audio_path
from app.models.room import Room
from app.models.room_participant import RoomParticipant

logger = logging.getLogger("jobs.retention")

AUDIO_RETENTION_DAYS = 30


async def purge_expired_room_audio(ctx: dict) -> int:
    """Delete raw audio for every room that ended more than
    AUDIO_RETENTION_DAYS ago. Returns the number of files deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=AUDIO_RETENTION_DAYS)

    async with async_session_maker() as db:
        expired_rooms = (
            await db.execute(select(Room).where(Room.ended_at.is_not(None), Room.ended_at < cutoff))
        ).scalars().all()

        deleted = 0
        for room in expired_rooms:
            participants = (
                await db.execute(
                    select(RoomParticipant).where(RoomParticipant.room_id == room.id)
                )
            ).scalars().all()
            for participant in participants:
                path = room_participant_audio_path(str(room.id), str(participant.id))
                try:
                    if path.exists():
                        path.unlink()
                        deleted += 1
                except OSError:
                    logger.exception("failed to delete expired audio %s", path)

    if deleted:
        logger.info("retention sweep deleted %d expired room audio files", deleted)
    return deleted
```

- [ ] **Step 4: Register the cron job**

In `backend/app/jobs/worker.py`, add the import and the cron registration:

```python
from arq import cron

from app.jobs.retention import purge_expired_room_audio
```

Add to the `WorkerSettings` class, after `functions = [...]`:

```python
    # Daily at 03:00 — the spec's 30-day raw-audio retention window.
    cron_jobs = [cron(purge_expired_room_audio, hour=3, minute=0)]
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/rooms/test_retention.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite one last time**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (every test from Plan 2 Tasks 1-11 and this plan's Tasks 12-18)

- [ ] **Step 7: Commit**

```bash
git add backend/app/jobs/retention.py backend/app/jobs/worker.py backend/tests/rooms/test_retention.py
git commit -m "Rooms: 30-day raw-audio retention sweep (transcripts and reports retained)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Manual verification (needs real LiveKit Cloud + Azure accounts)

Extends the manual-verification list at the end of the room backend plan. After `LIVEKIT_*` and `AZURE_SPEECH_*` are set in `.env` and both the API server and the ARQ worker are restarted:

1. Create a room **with a topic** (`POST /rooms` with `topic_text: "Should AI be used in hiring decisions?"`), join from two tabs, and hold a short discussion where one person deliberately drifts off-topic and one person deliberately talks over the other.
2. While the room is live, confirm `room_transcript_segments` rows are appearing — with a non-empty `words` array and a `participant_id` that matches a real `room_participants.id`. This is the Task 12 fix; if `words` is null or the ids don't match, stop and fix before going further.
3. End the room, then check the ARQ worker log for `analyzed room ... dominance=...`.
4. `GET /rooms/{id}/report` and confirm, for each participant: non-zero `talk_time_s` (proves the identity fix), a populated `analytics.speech_metrics` (word count, WPM, filler count), `analytics.relevance_result.mean_relevance` that is visibly *lower* for the participant who drifted off-topic, and seven `competencies` values in 0-100.
5. Confirm the interrupter's `competencies.teamwork` and `competencies.listening` are lower than the other participant's.
6. Repeat with `mode: "anonymous"` and confirm every analytics row is still labeled "Speaker N" for a non-host participant.
7. Retention: temporarily set `AUDIO_RETENTION_DAYS = 0` in `app/jobs/retention.py`, run the sweep manually in a Python shell (`await purge_expired_room_audio({})`), confirm the `.wav` files under `storage/rooms/<room_id>/` are gone while `GET /rooms/{id}/report` still returns the full report. **Restore the constant to 30 afterwards.**

## Follow-on work (not this plan)

- **Room frontend (Plan 4):** join screen with QR code + pincode entry, the consent modal gating audio publication, the live host dashboard over `WS /rooms/{id}/live`, and the report page rendering the competency scores. The spec's "Session access" and "Consent" sections are frontend-side and are only *enabled* by this plan, not implemented by it.
- **Competency weight calibration:** the weights in `app/rooms/competencies.py` are a defensible starting point, not tuned. `EVALUATION.md`'s Spearman-correlation harness is the right tool once a handful of real sessions have human ratings to compare against.
