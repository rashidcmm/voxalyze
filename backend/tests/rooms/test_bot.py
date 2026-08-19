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
