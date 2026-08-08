"""Seed the topics table. Idempotent: skips topics whose text already exists.

Usage (from backend/):
    .venv/Scripts/python.exe -m scripts.seed_topics
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.db import async_session_maker
from app.models.topic import Topic
from app.seed_data.topics import TOPICS


async def seed() -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(Topic.text))
        existing = set(result.scalars().all())

        inserted = 0
        for text, category, difficulty in TOPICS:
            if text in existing:
                continue
            session.add(Topic(text=text, category=category, difficulty=difficulty))
            inserted += 1

        await session.commit()
        print(f"Seeded {inserted} new topics ({len(TOPICS) - inserted} already existed).")


if __name__ == "__main__":
    asyncio.run(seed())
