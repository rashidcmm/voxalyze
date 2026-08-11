from pydantic import BaseModel


class ModelScoresResponse(BaseModel):
    pronunciation_status: str
    pronunciation_result: dict | None
    pronunciation_error: str | None
    relevance_status: str
    relevance_result: dict | None
    relevance_error: str | None
    argument_status: str
    argument_result: dict | None
    argument_error: str | None

    class Config:
        from_attributes = True
