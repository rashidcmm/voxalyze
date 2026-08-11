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

- [x] Fluency: WPM (overall + rolling 15s), filler rate/100 words (India-aware filler list),
      pause analysis (>0.5s / >2.0s), speech-to-silence ratio
- [x] Vocabulary: MTLD (not TTR), repeated-3-gram rate
- [x] Syntax (spaCy): MLU, clauses/sentence, subordination ratio, passive %, discourse markers
- [x] `session_metrics` table, one row per session, computed atomically in the same
      transaction/commit as the transcript (Phase 4 folded into the Phase 3 job rather than
      a separate queued step — cheap enough not to need its own job hop)
- [ ] *(stretch, cut from 6-day scope)* CEFR band distribution, AWL coverage %

**Day 3 DoD — tested against 3 real TTS-generated samples through the full pipeline
(upload → whisper → metrics), results reported honestly, including where they didn't
separate as hoped:**

| metric | fluent+dense | halting+filler | short+repetitive | separated as expected? |
|---|---|---|---|---|
| wpm_overall | 143.3 | 48.7 | 156.6 | yes — halting much slower |
| filler_rate_per_100_words | 0.0 | 19.23 | 0.0 | yes |
| mtld_score | 209.5 | 94.6 | 7.4 | yes — clean monotonic separation |
| repeated_trigram_rate | 0.0 | 0.0 | 0.565 | yes |
| discourse_marker_rate | 4.48 | 0.0 | 0.0 | yes |
| speech_to_silence_ratio | 3.35 | **5.18** | 1.44 | **no** — halting came out *higher* than fluent |
| hesitation_pause_count (>2s) | 0 | 0 | 0 | not tested — no sample produced a gap that long |

Root cause of the one miss, confirmed by inspecting the actual transcript: the "halting"
sample was synthesized by slowing the TTS engine's articulation rate (`Rate=-3`), which
lengthens each recognized word's own duration rather than inserting real silent gaps —
so `speaking_time_s` (sum of word durations) went up, not down. Separately, Whisper itself
dropped the "um"/"uh" tokens from that sample entirely (it still correctly kept "like",
"you know", "so yeah", "i mean", "basically" — which is why filler_rate still separated
correctly). Both are artifacts of using slowed-down TTS as a stand-in for genuine human
hesitation, not a bug in the pause-analysis code — real recordings with actual silent
pauses should behave as designed, but this is exactly the kind of thing Phase 7's real
evaluation harness (human recordings, human ratings) needs to confirm rather than assume.

## Day 4 — Model Layer (3 scorers)

- [x] Azure pronunciation assessment: accuracy/fluency/prosody + word-level error types,
      cached per session, framed as clarity/intelligibility (not "wrong accent")
      (`app/scoring/pronunciation.py` — REST API, chunked to ~45s pieces for the
      short-audio endpoint's limit, duration-weighted aggregation)
- [x] Topic relevance: utterance-level embeddings (all-MiniLM-L6-v2) vs topic statement,
      cosine similarity → mean relevance + drift curve over time (`app/scoring/relevance.py`,
      local, no API key — verified end-to-end against a real synthetic transcript)
- [x] Argument quality (LLM, Anthropic API): strict JSON schema via `client.messages.parse`,
      Pydantic validation, retry once on schema failure (`app/scoring/argument_quality.py`).
      Uses `claude-opus-5`; `temperature=0` isn't accepted by that model (see module
      docstring) — `effort: "low"` used instead for near-deterministic output, per current
      Anthropic API guidance.
- [x] `model_scores` table (migration `85cb62ba58af`), `score_session` ARQ job chained after
      transcription, `GET /sessions/{id}/scores` — each of the 3 scorers independently
      isolated (`app/scoring/pipeline.py`): a missing API key or a scorer crash never blocks
      the other two or fails the whole job, recorded as `status=not_configured`/`error`
      per-scorer rather than silently zeroed

**Day 4 DoD — partially verified this session:**
- Each scorer runs standalone: relevance verified end-to-end (real embedding download +
  cosine similarity separation on a synthetic transcript); pronunciation's audio
  decode/re-encode path verified against a synthetic WAV; both API-dependent scorers
  (pronunciation, argument quality) verified to fail gracefully with `not_configured`
  when their keys aren't set (they aren't, yet — see README "External accounts needed")
- App + worker import cleanly with all jobs/routes registered; not yet run against a live
  Postgres/Redis in this session (Docker Desktop wasn't running) — full pipeline timing
  (<90s) and the actual Azure/Anthropic-configured path are untested until you add those
  keys and run it for real

## Day 5 — Scoring & Learning Curve + Mini Evaluation Harness

- [x] Combine into headline dimensions (Fluency, Vocabulary, Clarity, Relevance, Argumentation)
      with documented weights, normalized for topic difficulty (`app/scoring/headline.py` —
      weights and normalization curves are hand-picked defaults, explicitly flagged as a
      Phase 7 calibration target, not fit against data)
- [x] EWMA-smoothed trend line; `GET /me/progress`; multi-line + radar chart (hand-rolled
      SVG, no new frontend dependency — `src/components/charts/`)
- [x] Feedback page: headline scores, relevance drift curve, top filler words, LLM improvement
      points, transcript with slow/hesitant segments highlighted (`/sessions/{id}/feedback`)
- [x] Mini evaluation harness built (`scripts/evaluate.py`: Spearman correlation + LLM
      variance) and `EVALUATION.md` written — **not yet run against real data**: this needs
      actual recordings and human ratings, which only you can produce (see EVALUATION.md)

**Day 5 DoD:** unverified against real sessions — needs Docker Desktop running + at least a
few real recordings scored end to end. tsc/eslint clean, backend imports clean, headline
scoring logic verified with synthetic inputs.

## Day 6 — Deploy & Package (flexes to local-demo if only 5 days available)

- [ ] Deploy backend + worker + Redis + Postgres + frontend (or: local run + demo video)
- [ ] Rate-limit auth/upload endpoints, cap audio length server-side
- [ ] Seed demo account with 6 completed sessions
- [ ] README: architecture diagram, deterministic-vs-model split, evaluation results
- [ ] 2-minute demo video

**Day 6 DoD:** a stranger signs up on the deployed URL (or watches the video), records, and
sees feedback, untouched by you.
