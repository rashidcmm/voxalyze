from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, progress, rooms, sessions, topics

app = FastAPI(title="GD/Debate Speech Trainer API")

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
