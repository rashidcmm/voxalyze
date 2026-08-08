"""Fluency & delivery metrics (Phase 4) — all computed from word-level
timestamps, nothing here calls an LLM or any external service.
"""
import re
from dataclasses import dataclass, field
from typing import Sequence

from app.metrics.types import WordLike

# Natural pause: a gap long enough to be a breath/thought break.
# Hesitation pause: a gap long enough to read as searching for words.
# These thresholds are the roadmap's own numbers, not tuned against data —
# flag as a real Phase 7 (evaluation harness) target if they need adjusting.
NATURAL_PAUSE_S = 0.5
HESITATION_PAUSE_S = 2.0

ROLLING_WINDOW_S = 15.0

# India-aware filler list per the roadmap. Multi-word fillers are matched as
# n-grams over the lowercased word sequence; single-word ones by membership.
SINGLE_WORD_FILLERS = {"um", "uh", "er", "like", "actually", "basically", "matlab", "na", "haan"}
MULTI_WORD_FILLERS = [
    ("you", "know"),
    ("i", "mean"),
    ("so", "yeah"),
]

_WORD_RE = re.compile(r"[a-z']+")


def _normalize(word: str) -> str:
    """Strip punctuation, lowercase — 'Um,' and 'um' should match the same filler."""
    m = _WORD_RE.findall(word.lower())
    return m[0] if m else ""


@dataclass
class FluencyMetrics:
    word_count: int
    duration_s: float
    wpm_overall: float
    wpm_rolling_windows: list[dict] = field(default_factory=list)  # [{start_s, end_s, wpm}]
    filler_count: int = 0
    filler_rate_per_100_words: float = 0.0
    natural_pause_count: int = 0
    hesitation_pause_count: int = 0
    total_pause_duration_s: float = 0.0
    longest_pause_s: float = 0.0
    speech_to_silence_ratio: float = 0.0


def compute_fluency_metrics(words: Sequence[WordLike], duration_s: float) -> FluencyMetrics:
    word_count = len(words)
    if word_count == 0 or duration_s <= 0:
        return FluencyMetrics(word_count=0, duration_s=duration_s, wpm_overall=0.0)

    wpm_overall = word_count / (duration_s / 60)

    # --- rolling WPM, in fixed 15s buckets by word start time (shows pace variation) ---
    wpm_rolling_windows: list[dict] = []
    n_windows = max(1, int(duration_s // ROLLING_WINDOW_S) + 1)
    for i in range(n_windows):
        window_start = i * ROLLING_WINDOW_S
        window_end = min(window_start + ROLLING_WINDOW_S, duration_s)
        window_len = window_end - window_start
        if window_len <= 0:
            continue
        count = sum(1 for w in words if window_start <= w.start_s < window_end)
        wpm_rolling_windows.append(
            {
                "start_s": round(window_start, 2),
                "end_s": round(window_end, 2),
                "wpm": round(count / (window_len / 60), 1),
            }
        )

    # --- filler detection ---
    normalized = [_normalize(w.word) for w in words]
    filler_count = sum(1 for tok in normalized if tok in SINGLE_WORD_FILLERS)
    for a, b in MULTI_WORD_FILLERS:
        for i in range(len(normalized) - 1):
            if normalized[i] == a and normalized[i + 1] == b:
                filler_count += 1
    filler_rate_per_100_words = (filler_count / word_count) * 100

    # --- pause analysis (gaps between consecutive words) ---
    natural_pause_count = 0
    hesitation_pause_count = 0
    total_pause_duration_s = 0.0
    longest_pause_s = 0.0
    for prev, nxt in zip(words, words[1:]):
        gap = nxt.start_s - prev.end_s
        if gap >= NATURAL_PAUSE_S:
            natural_pause_count += 1
            total_pause_duration_s += gap
            longest_pause_s = max(longest_pause_s, gap)
            if gap >= HESITATION_PAUSE_S:
                hesitation_pause_count += 1

    speaking_time_s = sum(w.end_s - w.start_s for w in words)
    silence_time_s = max(duration_s - speaking_time_s, 0.001)  # avoid div-by-zero
    speech_to_silence_ratio = speaking_time_s / silence_time_s

    return FluencyMetrics(
        word_count=word_count,
        duration_s=duration_s,
        wpm_overall=round(wpm_overall, 1),
        wpm_rolling_windows=wpm_rolling_windows,
        filler_count=filler_count,
        filler_rate_per_100_words=round(filler_rate_per_100_words, 2),
        natural_pause_count=natural_pause_count,
        hesitation_pause_count=hesitation_pause_count,
        total_pause_duration_s=round(total_pause_duration_s, 2),
        longest_pause_s=round(longest_pause_s, 2),
        speech_to_silence_ratio=round(speech_to_silence_ratio, 3),
    )
