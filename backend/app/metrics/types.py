from typing import Protocol


class WordLike(Protocol):
    """Duck-typed so this works against either app.transcription.WordTiming
    (dataclass) or app.models.transcript.Word (ORM row) without coupling
    the metrics package to either."""

    word: str
    start_s: float
    end_s: float
