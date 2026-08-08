"""Orchestrates the three metric modules into one flat dict matching the
session_metrics table's columns — the single place that knows how fluency +
vocabulary + syntax results map onto storage.
"""
from typing import Sequence

from app.metrics.fluency import compute_fluency_metrics
from app.metrics.syntax import compute_syntax_metrics
from app.metrics.types import WordLike
from app.metrics.vocabulary import compute_vocabulary_metrics


def compute_all_metrics(full_text: str, words: Sequence[WordLike], duration_s: float) -> dict:
    fluency = compute_fluency_metrics(words, duration_s)
    vocabulary = compute_vocabulary_metrics(full_text)
    syntax = compute_syntax_metrics(full_text)

    return {
        # fluency & delivery
        "word_count": fluency.word_count,
        "wpm_overall": fluency.wpm_overall,
        "wpm_rolling_windows": fluency.wpm_rolling_windows,
        "filler_count": fluency.filler_count,
        "filler_rate_per_100_words": fluency.filler_rate_per_100_words,
        "natural_pause_count": fluency.natural_pause_count,
        "hesitation_pause_count": fluency.hesitation_pause_count,
        "total_pause_duration_s": fluency.total_pause_duration_s,
        "longest_pause_s": fluency.longest_pause_s,
        "speech_to_silence_ratio": fluency.speech_to_silence_ratio,
        # vocabulary
        "mtld_score": vocabulary.mtld_score,
        "repeated_trigram_rate": vocabulary.repeated_trigram_rate,
        # syntax
        "mean_length_utterance": syntax.mean_length_utterance,
        "clauses_per_sentence": syntax.clauses_per_sentence,
        "subordination_ratio": syntax.subordination_ratio,
        "passive_voice_pct": syntax.passive_voice_pct,
        "discourse_marker_rate_per_100_words": syntax.discourse_marker_rate_per_100_words,
    }
