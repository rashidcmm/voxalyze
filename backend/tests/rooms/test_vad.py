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


def test_start_offset_shifts_every_timestamp_onto_the_shared_room_clock():
    """A participant whose first frame arrives 12s into the room must emit
    room-relative timestamps, not timestamps relative to their own arrival."""
    vad = StreamingVAD(participant_id="late", start_offset_s=12.0)
    for _ in range(10):  # 200ms of speech
        vad.push_frame(LOUD_FRAME)
    for _ in range(20):  # past HANGOVER_FRAMES, closes the segment
        vad.push_frame(SILENT_FRAME)

    start_s, end_s = vad.closed_segments[0]
    assert start_s == pytest.approx(12.0)
    assert end_s == pytest.approx(12.2, abs=0.001)


def test_flush_also_respects_the_start_offset():
    vad = StreamingVAD(participant_id="late", start_offset_s=5.0)
    for _ in range(5):
        vad.push_frame(LOUD_FRAME)
    vad.flush()
    start_s, end_s = vad.closed_segments[0]
    assert start_s == pytest.approx(5.0)
    assert end_s == pytest.approx(5.1, abs=0.001)
