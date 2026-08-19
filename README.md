# GD/Debate Speech Trainer

Solo speech analysis engine for group-discussion / debate practice: record a 2–5 minute
speech, get scored on fluency, vocabulary, structure, pronunciation and topic relevance, and
track improvement across sessions.

Built against a compressed **6-day** version of `gd-trainer-roadmap-v2.md` (the original was a
4-week plan). See `TASKS.md` for the day-by-day checklist and what got cut to fit the timeline.

## Stack

- **Backend:** FastAPI + SQLAlchemy 2.0 (async) + Alembic, Postgres, Redis, ARQ job queue,
  faster-whisper (local transcription, `base.en` int8 — first run downloads the model from
  Hugging Face, needs internet once)
- **Frontend:** Next.js 16 (App Router, Turbopack) + TypeScript + Tailwind + Zustand
- **Local dev infra:** Postgres + Redis via `docker-compose.yml` — no cloud accounts needed
  until Day 4 (Azure Speech, LLM API) and Day 6 (deploy)

## Prerequisites

- Docker Desktop running
- Python 3.12+, Node 22+
- For Day 4 model scorers (optional — the app runs fine without them, see below):
  an Azure Speech key/region and an Anthropic API key

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

## External accounts needed (Day 4+)

The app works end-to-end without these — fluency/vocabulary/syntax metrics (Day 3),
relevance scoring (local embeddings), headline scores, and the dashboard all work with
zero cloud accounts. Two of the five headline dimensions (**Clarity**, **Argumentation**)
just show as unscored until you add the keys below; nothing crashes.

1. **Azure Speech** (pronunciation → "Clarity" dimension) — free F0 tier:
   - Go to [portal.azure.com](https://portal.azure.com) → Create a resource → search
     "Speech" → create it on the **Free F0** pricing tier (5 audio hours/month free, no
     credit card charge on F0 itself, but Azure does require a card on file for the
     subscription)
   - Copy **Key 1** and the **Region** from the resource's "Keys and Endpoint" page
   - Put them in `backend/.env` as `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION`
2. **Anthropic API** (argument-quality scorer) — pay-as-you-go, pennies per session:
   - Go to [console.anthropic.com](https://console.anthropic.com) → Settings → API Keys →
     Create Key
   - Put it in `backend/.env` as `ANTHROPIC_API_KEY`
   - Defaults to `claude-opus-5`; set `ANTHROPIC_MODEL=claude-haiku-4-5` in `.env` if you'd
     rather trade judgment quality for a cheaper/faster scorer — this is a config change,
     no code edit needed

3. **LiveKit Cloud** (multi-party GD rooms) — free tier is enough for local dev:
   - Go to [cloud.livekit.io](https://cloud.livekit.io) → create a project → Settings → Keys
     → create an API key
   - Put the project's websocket URL and the key/secret in `backend/.env` as
     `LIVEKIT_URL` (e.g. `wss://your-project.livekit.cloud`), `LIVEKIT_API_KEY` and
     `LIVEKIT_API_SECRET`
   - Without them, `POST /rooms/{code}/join` returns a 503 with a pointer here; the rest of
     the app is unaffected
   - `ROOM_MAX_PARTICIPANTS` (default `6`) caps how large a room may be requested

Restart the worker (`arq app.jobs.worker.WorkerSettings`) after adding keys — it loads them
at process start.

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

Days 1-5 built (scaffold/auth, record+transcribe, deterministic metrics, model-layer
scorers, headline scoring + progress/feedback UI). See `TASKS.md` for what's done, what's
verified vs. not, and what's next (Day 6: deploy).

## Evaluation harness

`EVALUATION.md` + `backend/scripts/evaluate.py` — Spearman correlation between the
pipeline's headline scores and human ratings, plus LLM-scorer variance. Needs real
recordings and ratings to run; see `EVALUATION.md` for the collection steps.
