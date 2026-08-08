# Build Checklist — GD/Debate Speech Trainer (6-day compressed plan)

Full rationale and scope-cut reasoning: see the plan this was built from, and
`gd-trainer-roadmap-v2.md` for the original 4-week version this compresses.

## Day 1 — Scaffold + Auth + Topic Bank

- [x] Monorepo layout (`/backend`, `/frontend`), `.gitignore`
- [x] `docker-compose.yml` — Postgres 16 + Redis 7, both healthy
- [x] Backend: FastAPI app, SQLAlchemy 2.0 (async), Alembic wired
- [x] First migration (`users` + `topics` tables) runs clean on `alembic upgrade head`
- [x] ARQ worker + dummy job — enqueue via `/health/job-test`, verified picked up and logged
- [x] Auth: `users` table, `POST /auth/signup`, `POST /auth/login` (JWT), `GET /auth/me`
- [x] `topics` table + seed script (58 topics, 5 categories, 3 difficulty levels, idempotent)
- [x] `GET /topics/random?difficulty=N&category=X` — tested with and without filters
- [x] Frontend: Next.js 16 scaffold (TS + Tailwind + App Router, Turbopack) + Zustand
- [x] Frontend: `/signup`, `/login`, protected route wrapper, dashboard placeholder
- [x] Git init + first commit

**Day 1 DoD:** `/health` returns ok · dummy job enqueued and processed by worker ·
`alembic upgrade head` clean · signup/login persists across refresh ·
`GET /topics/random?difficulty=2` returns a topic — **fully verified end to end
(curl-level for the API, tsc+eslint clean and all routes 200 for the frontend;
full browser click-through not yet done — no browser automation available this
session, worth a manual click-through before you move on).**

## Day 2 — Record & Store + Transcription

- [x] `POST /sessions` — creates session with assigned topic, status `recording`
- [x] Frontend `/practice/{id}`: topic display, 60s prep timer, `MediaRecorder` record/stop
- [x] `POST /sessions/{id}/audio` — accepts blob, writes to local storage, status `uploaded`
- [x] Handle: mic permission denied, tab closed mid-recording (beforeunload warning), upload
      failure + retry (blob kept in memory, retry button re-POSTs)
- [x] `Transcriber` protocol + `LocalWhisperTranscriber` (faster-whisper, word-level timestamps)
- [x] Transcription enqueued on upload; job idempotent (unique `transcripts.session_id` +
      early-return check) + retries with exponential backoff (5s, 25s) via `arq Retry`
- [x] `transcripts` + `words` tables
- [x] `GET /sessions/{id}` status transitions: uploaded → transcribing → transcribed
- [x] `GET /sessions/{id}/audio` (playback) and `GET /sessions/{id}/transcript` added
      (needed for the DoD's "audio plays back" check and for verifying transcripts)

**Day 2 DoD — fully verified:**
- Real speech (Windows TTS-generated WAV, not silence) uploaded and transcribed correctly by
  faster-whisper `base.en`, filler words ("Um,", "uh,") preserved, word timestamps sane
- Idempotency: re-invoking the job for an already-transcribed session returns
  `already_transcribed` and does not duplicate `words` rows (checked: 62 words before/after)
- Retry/failure path: simulated a missing audio file — try 1 & 2 raised `Retry` with 5s/25s
  backoff as designed, try 3 raised terminally and persisted `status=failed` +
  `failure_reason` on the session
- Kill-mid-job resume: live test (isolated Redis db to avoid colliding with the real app
  worker) — killed the whole worker process tree ~0.1s into an 8s job, a freshly started
  worker reclaimed and completed the same job ~30s later once ARQ's in-progress lock
  (`job_timeout + 10s`) expired
- Audio playback endpoint verified byte-for-byte against the uploaded file
- Frontend: tsc + eslint clean, all routes 200; full browser click-through still not done
  (no browser automation available this session)

## Day 3 — Deterministic Metrics Engine

- [ ] Fluency: WPM (overall + rolling 15s), filler rate/100 words (India-aware filler list),
      pause analysis (>0.5s / >2.0s), speech-to-silence ratio
- [ ] Vocabulary: MTLD (not TTR), repeated-3-gram rate
- [ ] Syntax (spaCy): MLU, clauses/sentence, subordination ratio, passive %, discourse markers
- [ ] `session_metrics` table, one row per session
- [ ] *(stretch, cut from 6-day scope)* CEFR band distribution, AWL coverage %

**Day 3 DoD:** three deliberately different sample recordings (fluent / halting+filler-heavy /
short+repetitive) produce metrics that separate clearly in the expected direction.

## Day 4 — Model Layer (3 scorers)

- [ ] Azure pronunciation assessment: accuracy/fluency/prosody + word-level error types,
      cached per session, framed as clarity/intelligibility (not "wrong accent")
- [ ] Topic relevance: utterance-level embeddings (all-MiniLM-L6-v2) vs topic statement,
      cosine similarity → mean relevance + drift curve over time
- [ ] Argument quality (LLM, Anthropic API): strict JSON schema, `temperature=0`,
      Pydantic validation, retry once on schema failure

**Day 4 DoD:** each scorer runs standalone on a sample file and returns a validated result ·
full pipeline (upload → transcript → metrics → 3 scorers → stored) completes < 90s.

## Day 5 — Scoring & Learning Curve + Mini Evaluation Harness

- [ ] Combine into headline dimensions (Fluency, Vocabulary, Clarity, Relevance, Argumentation)
      with documented weights, normalized for topic difficulty
- [ ] EWMA-smoothed trend line; `GET /me/progress`; multi-line + radar chart
- [ ] Feedback page: headline scores, relevance drift curve, top filler words, LLM improvement
      points, transcript with slow/hesitant segments highlighted
- [ ] Mini evaluation harness: 6–8 recordings, self (+1 rater if available), Spearman
      correlation per dimension, `EVALUATION.md` with honest results incl. sample-size caveat

**Day 5 DoD:** after 5 sessions, dashboard trends are interpretable in 10s without explanation.

## Day 6 — Deploy & Package (flexes to local-demo if only 5 days available)

- [ ] Deploy backend + worker + Redis + Postgres + frontend (or: local run + demo video)
- [ ] Rate-limit auth/upload endpoints, cap audio length server-side
- [ ] Seed demo account with 6 completed sessions
- [ ] README: architecture diagram, deterministic-vs-model split, evaluation results
- [ ] 2-minute demo video

**Day 6 DoD:** a stranger signs up on the deployed URL (or watches the video), records, and
sees feedback, untouched by you.
