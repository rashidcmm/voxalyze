import pytest

from app.rooms.live_stats import SpeechSegment, ParticipantStats, compute_participant_stats, compute_session_dominance


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
