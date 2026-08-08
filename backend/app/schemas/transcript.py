from pydantic import BaseModel


class WordResponse(BaseModel):
    word: str
    start_s: float
    end_s: float
    confidence: float | None

    class Config:
        from_attributes = True


class TranscriptResponse(BaseModel):
    full_text: str
    provider: str
    model: str
    duration_s: float
    words: list[WordResponse]

    class Config:
        from_attributes = True
