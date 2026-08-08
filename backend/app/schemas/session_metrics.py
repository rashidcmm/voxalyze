from pydantic import BaseModel


class SessionMetricsResponse(BaseModel):
    word_count: int
    wpm_overall: float
    wpm_rolling_windows: list[dict]
    filler_count: int
    filler_rate_per_100_words: float
    natural_pause_count: int
    hesitation_pause_count: int
    total_pause_duration_s: float
    longest_pause_s: float
    speech_to_silence_ratio: float
    mtld_score: float
    repeated_trigram_rate: float
    mean_length_utterance: float
    clauses_per_sentence: float
    subordination_ratio: float
    passive_voice_pct: float
    discourse_marker_rate_per_100_words: float

    class Config:
        from_attributes = True
