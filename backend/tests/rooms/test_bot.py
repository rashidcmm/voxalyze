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
