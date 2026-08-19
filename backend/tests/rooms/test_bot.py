import numpy as np
import pytest

from app.core.config import get_settings
from app.core.storage import room_participant_audio_path
from app.rooms.bot import RoomBot
from app.rooms.registry import RoomRegistry
from app.rooms.vad import FRAME_SAMPLES

LOUD_FRAME = np.full(FRAME_SAMPLES, 20_000, dtype=np.int16)
SILENT_FRAME = np.zeros(FRAME_SAMPLES, dtype=np.int16)


class _FakeClock:
    """Controllable stand-in for time.monotonic so a test can place a
    participant's first frame at an exact point in room time."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


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


def test_close_participant_writes_a_valid_non_empty_wav():
    """Regression coverage for the normal close path, kept green alongside
    the late-frame fix below: a clean close still produces a valid,
    non-empty WAV file."""
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-5", registry=registry)
    bot.handle_audio_frame("participant-e", LOUD_FRAME)
    bot.handle_audio_frame("participant-e", LOUD_FRAME)
    bot.close_participant("participant-e")

    path = room_participant_audio_path("room-5", "participant-e")
    assert path.exists()
    size_after_close = path.stat().st_size
    assert size_after_close > 0


def test_a_late_frame_after_close_does_not_truncate_the_recording(caplog):
    """Regression test for the truncation bug: handle_audio_frame() called
    for a participant AFTER close_participant() must not reopen (and thus
    truncate) the already-closed WAV file."""
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-4", registry=registry)
    bot.handle_audio_frame("participant-d", LOUD_FRAME)
    bot.close_participant("participant-d")

    path = room_participant_audio_path("room-4", "participant-d")
    size_after_close = path.stat().st_size
    assert size_after_close > 0

    with caplog.at_level("WARNING"):
        bot.handle_audio_frame("participant-d", LOUD_FRAME)  # late frame, must not raise

    assert path.stat().st_size == size_after_close  # unchanged, not truncated to 0
    assert "participant-d" in caplog.text


def test_a_stray_frame_with_no_reconnect_behind_it_stays_dropped_forever(caplog):
    """A frame from a connection that never reconnects (no track_subscribed
    fired again for this identity) must keep being dropped no matter how
    many more arrive — the blackhole set only clears via reopen_participant,
    never on its own."""
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-6", registry=registry)
    bot.handle_audio_frame("participant-f", LOUD_FRAME)
    bot.close_participant("participant-f")

    path = room_participant_audio_path("room-6", "participant-f")
    size_after_close = path.stat().st_size

    with caplog.at_level("WARNING"):
        for _ in range(3):
            bot.handle_audio_frame("participant-f", LOUD_FRAME)

    assert path.stat().st_size == size_after_close
    assert caplog.text.count("participant-f") >= 3


def test_reconnecting_participant_resumes_recording_instead_of_staying_blackholed(caplog):
    """Reproduces the round-2 regression: run()'s participant_disconnected
    handler calls close_participant(), which used to blackhole the identity
    forever — including across a legitimate reconnect that reuses the same
    LiveKit identity (rejoin/refresh reuses the same DB participant row while
    left_at stays NULL). reopen_participant() (invoked from run()'s
    track_subscribed handler on every subscription, reconnect or not) must
    un-blackhole the identity so recording/VAD resume."""
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-7", registry=registry)

    bot.handle_audio_frame("participant-g", LOUD_FRAME)
    bot.close_participant("participant-g")
    path = room_participant_audio_path("room-7", "participant-g")
    size_after_close = path.stat().st_size
    assert size_after_close > 0

    # The same identity reconnects: run()'s on_track_subscribed fires again
    # and calls reopen_participant() before forwarding any frames.
    bot.reopen_participant("participant-g")

    with caplog.at_level("WARNING"):
        bot.handle_audio_frame("participant-g", LOUD_FRAME)  # must be recorded, not dropped
    assert "dropped a late audio frame" not in caplog.text

    # Recorded: the WAV file grew (prior audio preserved, not truncated, plus
    # the new post-reconnect frame appended).
    assert path.stat().st_size > size_after_close

    # VAD'd: enough further frames close a fresh speech segment for this
    # participant, proving push_frame() is live again, not silently no-op.
    # (The first close_participant() above already flushed one tiny segment
    # from the single pre-close LOUD_FRAME — expected, same as
    # test_close_participant_flushes_a_still_open_segment — so compare
    # against the count just before this reconnect activity rather than 0.)
    segments_before_reconnect_activity = len(registry.segments_for("room-7"))
    for _ in range(20):  # past HANGOVER_FRAMES
        bot.handle_audio_frame("participant-g", SILENT_FRAME)
    bot.close_participant("participant-g")

    segments = registry.segments_for("room-7")
    assert len(segments) > segments_before_reconnect_activity
    assert segments[-1].participant_id == "participant-g"


# --- Room-relative VAD timeline (final-review item 2) -------------------------


def test_a_late_joiners_segments_are_room_relative_not_arrival_relative():
    """Regression test for the cross-participant timeline bug: each
    participant's StreamingVAD used to start at 0.0 on its own first frame, so
    a participant who joined 30s in had their speech folded back onto the
    start of the discussion — fabricating interruptions and skewing talk-time
    percentages in app/rooms/live_stats.py, which compares every participant's
    segments on one shared axis."""
    clock = _FakeClock()
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-clock-1", registry=registry, clock=clock)

    # Participant A speaks right at the start of the room.
    for _ in range(10):
        bot.handle_audio_frame("participant-a", LOUD_FRAME)
    for _ in range(20):
        bot.handle_audio_frame("participant-a", SILENT_FRAME)

    # Participant B's first frame only arrives 30 real seconds into the room.
    clock.advance(30.0)
    for _ in range(10):
        bot.handle_audio_frame("participant-b", LOUD_FRAME)
    for _ in range(20):
        bot.handle_audio_frame("participant-b", SILENT_FRAME)

    by_participant = {s.participant_id: s for s in registry.segments_for("room-clock-1")}

    # A (first speaker, no offset) is unaffected — pre-existing behavior intact.
    assert by_participant["participant-a"].start_s == pytest.approx(0.0)
    assert by_participant["participant-a"].end_s == pytest.approx(0.2, abs=0.01)

    # B lands 30s in, not near zero — the two no longer overlap at all.
    assert by_participant["participant-b"].start_s == pytest.approx(30.0, abs=0.01)
    assert by_participant["participant-b"].end_s == pytest.approx(30.2, abs=0.01)
    assert by_participant["participant-b"].start_s > by_participant["participant-a"].end_s


def test_the_room_clock_reference_is_fixed_at_bot_construction():
    """The offset is measured from when the bot (i.e. the room) started, not
    from when the previous participant's VAD happened to be created."""
    clock = _FakeClock()
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-clock-2", registry=registry, clock=clock)

    clock.advance(5.0)
    bot.handle_audio_frame("p1", LOUD_FRAME)
    clock.advance(5.0)
    bot.handle_audio_frame("p2", LOUD_FRAME)

    bot.close_participant("p1")
    bot.close_participant("p2")

    by_participant = {s.participant_id: s for s in registry.segments_for("room-clock-2")}
    assert by_participant["p1"].start_s == pytest.approx(5.0, abs=0.01)
    assert by_participant["p2"].start_s == pytest.approx(10.0, abs=0.01)


# --- Frame chunking (final-review item 3) ------------------------------------


def test_chunk_frames_carries_a_remainder_into_the_next_call():
    """rtc.AudioStream commonly delivers 10ms/160-sample events at 16kHz. The
    old slicing loop produced an empty range for those (range(0, -159, 320)),
    silently dropping every frame of every participant's audio."""
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-chunk-1", registry=registry)

    half = np.arange(160, dtype=np.int16)
    assert bot.chunk_frames("p", half) == []  # nothing complete yet, but buffered

    frames = bot.chunk_frames("p", half + 160)
    assert len(frames) == 1
    assert frames[0].size == FRAME_SAMPLES
    assert np.array_equal(frames[0], np.arange(320, dtype=np.int16))


def test_chunk_frames_loses_no_samples_across_many_odd_sized_events():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-chunk-2", registry=registry)

    # 40 events of 160 samples each = 6400 samples = exactly 20 full frames.
    source = np.arange(6400, dtype=np.int16)
    emitted = []
    for i in range(0, 6400, 160):
        for frame in bot.chunk_frames("p", source[i : i + 160]):
            assert frame.size == FRAME_SAMPLES
            emitted.append(frame)

    assert len(emitted) == 20
    assert np.array_equal(np.concatenate(emitted), source)  # in order, nothing lost


def test_chunk_frames_handles_event_sizes_that_are_not_multiples_of_a_frame():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-chunk-3", registry=registry)

    source = np.arange(1000, dtype=np.int16)  # 3 full frames + 40 leftover samples
    emitted = []
    for i in range(0, 1000, 137):  # deliberately ugly event size
        emitted.extend(bot.chunk_frames("p", source[i : i + 137]))

    assert len(emitted) == 3
    assert all(f.size == FRAME_SAMPLES for f in emitted)
    assert np.array_equal(np.concatenate(emitted), source[:960])

    # The 40-sample tail is retained, not discarded — it starts the next frame.
    emitted.extend(bot.chunk_frames("p", np.arange(1000, 1280, dtype=np.int16)))
    assert len(emitted) == 4
    assert np.array_equal(emitted[3], np.arange(960, 1280, dtype=np.int16))


def test_chunk_frames_keeps_each_participants_residual_separate():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-chunk-4", registry=registry)

    assert bot.chunk_frames("p1", np.zeros(160, dtype=np.int16)) == []
    assert bot.chunk_frames("p2", np.zeros(160, dtype=np.int16)) == []
    # p1 completing its own frame must not consume p2's buffered half.
    assert len(bot.chunk_frames("p1", np.zeros(160, dtype=np.int16))) == 1
    assert bot.chunk_frames("p2", np.zeros(100, dtype=np.int16)) == []


# --- Transcriber lifecycle (final-review item 9) -----------------------------


class _FakeTranscriber:
    instances: list = []

    def __init__(self, loop):
        self.loop = loop
        self.closed = False
        _FakeTranscriber.instances.append(self)

    def push_pcm(self, data):
        pass

    def close(self):
        self.closed = True


def test_reopening_a_track_closes_the_previous_transcriber(monkeypatch):
    """A reconnect fires track_subscribed again for the same identity. The old
    transcriber used to be overwritten in place — never closed, leaking its
    push stream and recognizer."""
    _FakeTranscriber.instances = []
    monkeypatch.setattr("app.rooms.bot.AzureStreamingTranscriber", _FakeTranscriber)

    registry = RoomRegistry()
    bot = RoomBot(room_id="room-transcriber-1", registry=registry)

    bot._start_transcriber("participant-x", loop=None)
    bot._start_transcriber("participant-x", loop=None)

    first, second = _FakeTranscriber.instances
    assert first.closed is True
    assert second.closed is False
    assert bot._transcribers["participant-x"] is second


def test_a_failing_transcriber_close_does_not_block_the_replacement(monkeypatch, caplog):
    class _BadCloseTranscriber(_FakeTranscriber):
        def close(self):
            raise RuntimeError("azure sdk blew up on close")

    _FakeTranscriber.instances = []
    monkeypatch.setattr("app.rooms.bot.AzureStreamingTranscriber", _BadCloseTranscriber)

    registry = RoomRegistry()
    bot = RoomBot(room_id="room-transcriber-2", registry=registry)
    bot._start_transcriber("participant-y", loop=None)
    with caplog.at_level("WARNING"):
        bot._start_transcriber("participant-y", loop=None)

    assert "failed to close the previous transcriber" in caplog.text
    assert bot._transcribers["participant-y"] is _FakeTranscriber.instances[-1]


# --- Identity validation / state cleanup (final-review item 12) --------------


def test_a_non_uuid_livekit_identity_is_rejected(caplog):
    """participant.identity arrives over the wire from LiveKit and becomes a
    filesystem path component via room_participant_audio_path — one trust hop
    further out than the server-generated ids storage.py assumes."""
    import uuid as _uuid

    registry = RoomRegistry()
    bot = RoomBot(room_id="room-identity-1", registry=registry)

    with caplog.at_level("WARNING"):
        assert bot._is_valid_identity("../../etc/passwd") is False
        assert bot._is_valid_identity("") is False
        assert bot._is_valid_identity(None) is False
    assert "non-uuid identity" in caplog.text

    assert bot._is_valid_identity(str(_uuid.uuid4())) is True


def test_close_participant_pops_the_vad_and_residual_buffer():
    registry = RoomRegistry()
    bot = RoomBot(room_id="room-cleanup-1", registry=registry)
    bot.handle_audio_frame("participant-z", LOUD_FRAME)
    bot.chunk_frames("participant-z", np.zeros(100, dtype=np.int16))
    assert "participant-z" in bot._vads

    bot.close_participant("participant-z")
    assert "participant-z" not in bot._vads
    assert "participant-z" not in bot._frame_residuals
