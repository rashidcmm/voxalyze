from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, progress, rooms, sessions, topics
from app.core.config import DEFAULT_JWT_SECRET, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.env != "dev" and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is still the default placeholder. Set a real secret in your "
            "environment before running with ENV != dev."
        )
    yield


app = FastAPI(title="GD/Debate Speech Trainer API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(topics.router)
app.include_router(sessions.router)
app.include_router(progress.router)
app.include_router(rooms.router)
