# GD/Debate Speech Trainer

Solo speech analysis engine for group-discussion / debate practice: record a 2–5 minute
speech, get scored on fluency, vocabulary, structure, pronunciation and topic relevance, and
track improvement across sessions.

Built against a compressed **6-day** version of `gd-trainer-roadmap-v2.md` (the original was a
4-week plan). See `TASKS.md` for the day-by-day checklist and what got cut to fit the timeline.

## Stack

- **Backend:** FastAPI + SQLAlchemy 2.0 (async) + Alembic, Postgres, Redis, ARQ job queue
- **Frontend:** Next.js 16 (App Router, Turbopack) + TypeScript + Tailwind + Zustand
- **Local dev infra:** Postgres + Redis via `docker-compose.yml` — no cloud accounts needed
  until Day 4 (Azure Speech, LLM API) and Day 6 (deploy)

## Prerequisites

- Docker Desktop running
- Python 3.12+, Node 22+

## Running locally

**1. Infra (Postgres + Redis):**
```bash
docker compose up -d
```

**2. Backend:**
```bash
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux
cp .env.example .env   # then edit JWT_SECRET etc.
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m scripts.seed_topics
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

In a second terminal, start the job worker:
```bash
cd backend
./.venv/Scripts/python.exe -m arq app.jobs.worker.WorkerSettings
```

> **Port note:** if 8000 is already taken on your machine, run uvicorn on another port and
> point the frontend's `NEXT_PUBLIC_API_URL` at it (see `.env.local.example`).

**3. Frontend:**
```bash
cd frontend
npm install
cp .env.local.example .env.local   # point at your backend URL
npm run dev
```

Visit `http://localhost:3000`.

## Verifying it's working (Phase 0/1 DoD)

- `curl http://localhost:8000/health` → `{"status": "ok"}`
- `curl -X POST http://localhost:8000/health/job-test` → check the worker terminal logs the
  message (proves the queue works end to end)
- Sign up at `/signup`, refresh the page, confirm you're still logged in
- `curl "http://localhost:8000/topics/random?difficulty=2"` → returns a topic

## Project structure

```
/backend
  /app
    /core        config, db session, security (JWT/password hashing)
    /models      SQLAlchemy models
    /schemas     Pydantic request/response schemas
    /api         FastAPI routers
    /jobs        ARQ worker + job definitions
    /seed_data   static seed content (topic bank)
  /alembic       migrations
  /scripts       one-off scripts (seeding, etc.)
/frontend
  /src/app       Next.js App Router pages
  /src/lib       API client, Zustand stores
  /src/components  shared React components
docker-compose.yml   Postgres + Redis for local dev
TASKS.md             day-by-day build checklist (source of truth for progress)
gd-trainer-roadmap-v2.md   original 4-week roadmap this build compresses
```

## Status

Day 1 (scaffold, auth, topic bank) complete. See `TASKS.md` for what's done and what's next.
