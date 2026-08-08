import uuid

from pydantic import BaseModel


class TopicResponse(BaseModel):
    id: uuid.UUID
    text: str
    category: str
    difficulty: int

    class Config:
        from_attributes = True
