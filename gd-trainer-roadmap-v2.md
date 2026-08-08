# Speech Analysis Engine — 4-Week Build Roadmap (v2)

**Positioning:** "AI speech analysis engine for GD/debate practice — measures pronunciation, vocabulary, structure and topic relevance from raw audio, and tracks improvement over sessions."

**What changed from v1 and why:**
- **Live multiplayer is OUT of the MVP.** It's the expensive half, it's not what you want to defend in interviews, and it needs a second human to demo. Solo-first.
- **Solo mode moved from Phase 8 (fallback) to Phase 2 (core).** It exercises 100% of the analysis pipeline with 0% of the WebRTC risk.
- **MongoDB → Postgres.** Your queries are joins and time-series aggregations. Also: SQL gets tested directly in placement interviews.
- **A background job queue is now mandatory, not optional.** Transcription takes 30–90s. This is real backend engineering and interviewers ask about it.
- **Phase 7 (evaluation harness) is new and is the most important phase.** It's what makes this a real project instead of a wrapper.

**Ground rule:** feed your AI coding tool ONE step at a time. Verify, commit, move on. Do not let it jump ahead.

---

## Hard scope

**IN:**
- Solo recorded practice: pick/receive a topic, speak 2–5 minutes, get scored
- Deterministic metrics: fluency, filler words, pace, pauses, vocabulary richness
- Pronunciation scoring (Azure), topic relevance (embeddings), argument quality (LLM)
- Per-session feedback page + cross-session learning curve
- An evaluation harness proving the scorer correlates with human judgment

**OUT (say this out loud, it's a feature not a gap):**
- Live 1:1 or group voice rooms, matchmaking, WebRTC
- Video, mobile app, payments, leaderboards, accents beyond English

If you finish early, the first thing to add is an **AI opponent** (LLM + Azure TTS, 500K chars/month free), not multiplayer. It demos better and costs less.

---

## Stack

| Layer | Choice | Note |
|---|---|---|
| Frontend | Next.js + Tailwind + Zustand | You know it |
| Backend | FastAPI | You know it; also where the audio/ML libraries live |
| Database | **Postgres** (Neon/Supabase free tier) | Joins + window functions for trends |
| Jobs | ARQ or Celery + Redis (Upstash free tier) | Transcription can't run in a request handler |
| Audio capture | Browser `MediaRecorder` → upload | No WebRTC needed |
| Transcription | `faster-whisper` locally (dev) / OpenAI API (prod) | One interface, two implementations |
| Pronunciation | Azure Speech pronunciation assessment | F0 free tier — verify current limits before relying on it |
| Relevance | `sentence-transformers` (all-MiniLM-L6-v2), local | Free, no API |
| NLP features | spaCy | Parsing, POS, clause counts |
| Qualitative scoring | Any LLM API, strict JSON output | Pennies per hundred sessions |
| Storage | Local disk (dev) → Cloudflare R2 / Supabase Storage (prod) | Free tiers |
| Deploy | Render/Railway + Vercel | Free tier |

**Cost projection:** effectively ₹0 in development. In production, roughly ₹0.50 per 5-minute session for transcription, plus negligible LLM cost. 200 demo sessions ≈ ₹150.

---

## Phase 0 — Scaffold (Day 1)

- Monorepo: `/backend` (FastAPI), `/frontend` (Next.js)
- Postgres provisioned, SQLAlchemy + Alembic wired, first migration runs
- Redis provisioned, worker process starts and processes a dummy job
- Git initialised, both apps pushed

**DoD:** `/health` returns ok, a dummy job enqueued from an endpoint is picked up by the worker and logged, `alembic upgrade head` works on a clean DB.

---

## Phase 1 — Auth + Topic Bank (Days 2–3)

Keep this thin. Nobody will ask you about it.

- `users` table: `id, email, password_hash, name, created_at`
- `POST /auth/signup`, `POST /auth/login` (JWT), `GET /auth/me`
- `topics` table, seeded with 40–60 GD/debate topics tagged `category` (current-affairs / tech / ethics / abstract / india-specific) and `difficulty` (1–3)
- Frontend: `/signup`, `/login`, protected route wrapper, dashboard placeholder

**DoD:** You can sign up, refresh the page and stay logged in, and `GET /topics/random?difficulty=2` returns a topic.

---

## Phase 2 — Record & Store (Days 4–6)

This is the whole "capture" layer. No analysis yet.

- `POST /sessions` — creates a session with an assigned topic, status `recording`
- Frontend `/practice/{id}`: shows the topic, 60s prep timer, then a record button using `MediaRecorder` (webm/opus), a live timer, and a stop button
- `POST /sessions/{id}/audio` — accepts the blob, writes to storage, sets status `uploaded`
- Handle: mic permission denied, tab closed mid-recording, upload failure with retry

**DoD:** You can record 3 minutes, close the browser, reopen the session, and the audio file plays back from storage. The failure cases show a sensible message, not a crash.

---

## Phase 3 — Transcription Layer (Days 7–9)

The first piece of real engineering. Build it as an abstraction from day one.

- Define `Transcriber` protocol: `transcribe(path) -> TranscriptResult` with **word-level timestamps**
- Implement `LocalWhisperTranscriber` (faster-whisper, `base.en` or `small.en`, int8)
- Implement `APITranscriber` (OpenAI transcription endpoint), selected by env var
- Enqueue a transcription job when audio upload completes; job is **idempotent** (re-running must not duplicate rows) and retries on failure with backoff
- Store `transcripts` (session_id, full_text, provider, model, duration_s) and `words` (transcript_id, word, start_s, end_s, confidence)
- `GET /sessions/{id}` returns status: `uploaded → transcribing → transcribed → scoring → scored → failed`

**DoD:** Upload audio, watch the status transition without polling the DB manually, and get back a transcript with per-word timings. Kill the worker mid-job and confirm the job resumes cleanly on restart. Flip the env var and confirm both providers produce the same shape.

*Interview note: word-level timestamps are non-negotiable. Every deterministic metric below depends on them.*

---

## Phase 4 — Deterministic Metrics Engine (Days 10–14)

**This is the core of the project. Spend the most time here.** Everything below is computed from timestamps and text — no LLM involved, fully reproducible.

**Fluency & delivery**
- Words per minute (overall + rolling 15s window, to show pace variation)
- Filler rate per 100 words. Build an India-aware filler list: `um, uh, er, like, you know, actually, basically, I mean, matlab, na, haan, so yeah`
- Pause analysis: count and total duration of gaps >0.5s (natural) and >2.0s (hesitation); longest pause
- Speech-to-silence ratio

**Vocabulary**
- **MTLD or MATTR, not raw type-token ratio** — TTR is length-dependent, so a 2-minute and a 5-minute speech aren't comparable. Knowing this is a real interview differentiator.
- CEFR band distribution: map tokens against a public A1–C2 wordlist, report % in B2+
- Academic Word List coverage %
- Repetition: rate of repeated 3-grams

**Syntax** (spaCy)
- Mean length of utterance, clauses per sentence, subordination ratio
- Passive voice %, discourse-marker usage (`however, therefore, furthermore`)

Store all of these in a `session_metrics` table, one row per session, one column per metric.

**DoD:** Record three deliberately different speeches — one fluent and dense, one halting and full of fillers, one short and repetitive — and confirm the metrics separate them clearly and in the direction you'd expect. If they don't, the metric is wrong; fix it before moving on.

---

## Phase 5 — Model Layer (Days 15–18)

Three independent scorers, each in its own module, each individually testable.

**5.1 Pronunciation (Azure)**
- Use unscripted pronunciation assessment on the audio; capture accuracy, fluency, prosody and word-level error types (mispronunciation / omission / insertion)
- Cache results per session — don't burn the 5-hour free tier re-running

> **Important product + ethics call:** these models score against native-speaker references and will systematically mark Indian English features as errors. Do **not** surface this as "your pronunciation is wrong." Frame it as **clarity / intelligibility**, surface only the specific words flagged as hardest to understand, and say explicitly in your UI that accent is not being graded. This is both the right call and one of the best answers you can give when an interviewer asks about model bias.

**5.2 Topic relevance (embeddings, local)**
- Split transcript into utterances by pause boundaries
- Embed each utterance and the topic statement with all-MiniLM-L6-v2
- Per-utterance cosine similarity → mean relevance score, plus a **drift curve** over time showing where the speaker wandered off-topic
- This visual is your best screenshot for the resume

**5.3 Argument quality (LLM)**
- Send transcript + rubric, demand strict JSON matching a fixed schema
- Score ONLY qualitative dimensions: argument structure, evidence use, persuasiveness, coherence, counter-argument handling
- Never ask it to count anything — you already computed those in Phase 4
- Set `temperature=0`, validate the response against a Pydantic model, retry once on schema failure

**DoD:** Each scorer runs standalone on a sample file and returns a validated result. The full pipeline (upload → transcript → metrics → three scorers → stored scores) completes in under 90 seconds.

---

## Phase 6 — Scoring & Learning Curve (Days 19–21)

- Combine into 4–5 headline dimensions with documented weights: Fluency, Vocabulary, Clarity, Relevance, Argumentation
- **Normalise for topic difficulty** so a hard topic doesn't look like regression
- Smooth the trend line with EWMA — a raw per-session line will look like noise
- `GET /me/progress` returns per-dimension series; render as a multi-line chart + a radar chart for the latest session
- Feedback page: headline scores, the relevance drift curve, top 3 filler words with counts, 3 concrete improvement points from the LLM, and the transcript with slow/hesitant segments highlighted

**DoD:** After 5 sessions the dashboard shows per-dimension trends that a stranger can interpret in 10 seconds without you explaining it.

---

## Phase 7 — Evaluation Harness (Days 22–24)

**Do not skip this. This is the phase that makes the project.**

- Collect 12–15 recordings of varying quality (yourself, friends, deliberately bad ones)
- Have 2–3 people independently rate each on your rubric; compute inter-rater agreement so you know your human baseline
- Compute **Spearman correlation** between your pipeline scores and the human mean, per dimension
- Run the same 5 recordings through the LLM scorer 3× each and measure score variance
- Write up: which dimensions correlate well, which don't, and what you changed as a result

**DoD:** A short `EVALUATION.md` in the repo with a correlation table and a variance table. Whatever the numbers say — even if some dimensions correlate badly — report them honestly. "Relevance correlated at 0.71 but persuasiveness only at 0.34, so I stopped surfacing persuasiveness as a number and made it qualitative feedback instead" is a *stronger* answer than a suspiciously perfect result.

---

## Phase 8 — Deploy & Package (Days 25–28)

- Deploy backend + worker + Redis + Postgres + frontend; confirm the API transcriber path works in prod (the local Whisper model will not fit in a free-tier container — this is exactly why you built the abstraction)
- Rate-limit auth and upload endpoints; cap audio length server-side
- Seed a demo account with 6 completed sessions so the trend chart is populated the moment anyone opens it
- README with an architecture diagram, the deterministic-vs-model split explained, and the evaluation results
- Record a 2-minute demo video

**DoD:** A stranger signs up on the deployed URL, records 2 minutes, and sees feedback — with you not touching anything.

---

## Interview questions you must be able to answer cold

Write your answers down before your first interview.

1. Why is filler counting done in code and argument quality done by an LLM?
2. What happens if the worker dies mid-transcription? Why is the job idempotent?
3. Why MTLD instead of type-token ratio?
4. How do you know your scores mean anything? (→ Phase 7)
5. Why Postgres over Mongo here?
6. How would you scale this to 500 concurrent users? (→ queue depth, worker autoscaling, storage egress, per-user rate limits)
7. What's biased about your pronunciation scoring and what did you do about it?
8. What would break first if you added live 8-person group GD? (→ per-track egress, overlap detection, per-speaker attribution)

---

## Vibe-coding split

- **Generate freely:** auth, forms, Tailwind/UI, dashboard charts, CRUD endpoints, Alembic migrations, deployment config
- **Write and understand line by line:** the transcription job and its failure handling, every metric in Phase 4, the scoring combination logic in Phase 6, the evaluation harness

Roughly 25% of the code, ~90% of what gets asked about.

---

## What to tell your AI coding assistant, verbatim

> "We are on Phase [N] only, from the roadmap. Do not implement anything from later phases even if it seems related. Build only what's described in this step, then stop and tell me how to test it against the Definition of Done."

---

## After the 4 weeks (in this order)

1. **AI opponent** — LLM generates counter-arguments, Azure TTS speaks them, you rebut. Turns a recorder into a sparring partner. Best effort-to-impressiveness ratio available.
2. **Live 1:1** via LiveKit — use **track egress** (per-participant audio files) so you never need speaker diarization.
3. **Group GD (8–12)** — same track-egress trick, plus overlap detection from timestamps for interruption analysis.
4. Difficulty tiers by target role, mobile app via Expo.
