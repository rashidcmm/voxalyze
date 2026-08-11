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
