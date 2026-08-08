from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models.topic import Topic
from app.schemas.topic import TopicResponse

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/random", response_model=TopicResponse)
async def random_topic(
    difficulty: Annotated[int | None, Query(ge=1, le=3)] = None,
    category: Annotated[str | None, Query()] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Topic)
    if difficulty is not None:
        stmt = stmt.where(Topic.difficulty == difficulty)
    if category is not None:
        stmt = stmt.where(Topic.category == category)
    stmt = stmt.order_by(func.random()).limit(1)

    result = await db.execute(stmt)
    topic = result.scalar_one_or_none()
    if topic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No matching topic found")
    return topic
