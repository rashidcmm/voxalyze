# Multi-Party GD Room MVP — Design

**Status:** Approved for planning
**Date:** 2026-08-11 (analytics/access/consent scope extended 2026-08-19)

> **2026-08-19 update:** a separate session (working without visibility into
> this repo) produced three standalone docs proposing a rebuilt-from-scratch
> platform (`docs/superpowers/plans/GD_Analytics_Architecture.md`,
> `Implementation_Checklist.md`, `Analytics_Algorithms_Reference.md`). Their
> useful content — the differentiator-analytics scope (topic alignment,
> speech quality, sentiment, composite competency scores) — has been folded
> into this spec below (see "Post-session analytics scope"). Their
> architecture choices (separate repos, Deepgram, OpenAI embeddings,
> MongoDB, rebuilding auth) were rejected as duplicating or contradicting
> what's already built/decided in this repo; those three docs are now marked
> superseded and point back here.

## Background

The existing app (`GD/Debate Speech Trainer`) is a solo, async practice tool: a user
records a 2-5 minute speech alone, it's transcribed and scored after the fact, and
they track improvement over time (see `README.md`, `TASKS.md` Days 1-5).

This spec covers extending that into **live, multi-person group-discussion rooms** —
several people debating/discussing together, live, with per-participant analysis
(who talks too much, who's not speaking, who interrupts, talk-time balance, etc.),
producing a post-session report. It's meant to help both interviewers running mock
GDs and peers practicing GDs among themselves ahead of on-campus placements.

### Why this needs its own spec (scope decomposition)

The original ask also included: anonymous rooms, live judgment during the session,
interviewer-facilitation tooling, and a phase-2 proctoring/anti-cheat system (gaze
tracking, tab/flag detection, HackerRank-style monitoring) for self-run mock
interviews. That's five separable subsystems. This spec scopes **only the first**:
a working multi-party room with live video/audio, live transcription, and a
post-session analysis report. Deeper facilitator tooling, richer live-session
judgment UX, and the phase-2 proctoring system are explicitly out of scope here and
will get their own specs once this MVP is real and validated.

### Viability note (research, Aug 2026)

Automated GD/meeting analysis (talk-time balance, dominance, interruption detection)
is an active, published research area, not a novel invention — see e.g. work on
AI-scored group discussions and "Meeting Mediator"-style conversational-balance
tools. Speaker diarization for meeting analytics is a solved, well-tooled space
(pyannote/NeMo), though this design avoids needing it at all (see Architecture).
Gaze/anti-cheat proctoring (phase 2) is exactly what HackerRank/Talview/Fabric
already ship, so it's feasible but a project of its own. This means the idea is
sound but not novel by itself — execution depth and evaluation rigor (which this
codebase already does more of than most student projects, via `EVALUATION.md`)
matter more than the concept.

## Goals

- Multiple people (≤6) join a live room with audio + video and talk to each other.
- Live, per-speaker transcription during the session.
- A host-only live stats view during the session (talk-time, interruptions, so far).
- Rooms can run "identified" (normal video, real names) or "anonymous" (camera off,
  alias name, real voice — voice-masking deferred).
- Any logged-in user can create a room (becoming its host) and share a join code;
  a room works equally for an interviewer-led session or self-conducted peer
  practice — the host role exists but doesn't have to be actively used.
- After the session ends, all participants get a post-session report: deterministic
  interaction metrics per participant, speech-quality/topic-alignment/pronunciation
  scoring and composite competency scores (see "Post-session analytics scope"),
  plus a neutral, evidence-tied LLM read on each participant's contribution (framed
  as observations, not personality labels).

## Non-goals (this spec)

- Voice-masking for anonymous mode (real-time pitch-shifting is its own effort).
- Recording or persisting video (video is live-only, not stored).
- Rich interviewer-facilitation controls beyond ending the room / seeing live stats
  (structured scoring UI, flagging, session scheduling — later spec).
- Any phase-2 proctoring: gaze tracking, tab-switch/fullscreen enforcement, keystroke
  analytics, anti-cheat flags for self-run mock interviews.
- Groups larger than ~6 (would need a different WebRTC/infra approach).

## Architecture

```
Frontend (Next.js)          Backend (FastAPI)                External
──────────────────          ──────────────────                ────────
Join room (LiveKit    ──►   POST /rooms/{code}/join      
 React SDK: publish          → issues LiveKit token    ──►   LiveKit Cloud
 own audio+video)             + assigns alias if                (media routing;
                               anonymous mode                     free tier)

                             Backend also joins each
                             room as a server-side
                             participant, subscribes to
                             each participant's audio  ──►   Azure Speech
                             track, streams it to a            (streaming STT,
                             per-participant STT session         free tier;
                                                                  1 socket per
                                                                  participant)

                             Live stats engine (per-track
                             VAD + STT timestamps) computes
                             talk-time/turns/overlaps in
                             real time

Host dashboard         ◄──  WS /rooms/{id}/live
 (live stats, host-only)     (host-only stream)

Host ends room          ──► POST /rooms/{id}/end
                             → finalizes recordings, enqueues
                             ARQ job: analyze_room_session   ──►  (reuses existing
                                                                    ARQ worker)

All participants        ◄──  GET /rooms/{id}/report
 (after job completes)       (deterministic stats + LLM
                              qualitative read, Claude call
                              reusing argument_quality.py's
                              schema-validated pattern)
```

**Key simplification:** each participant publishes their own audio track (not a
mixed room feed), so speaker attribution is free — no acoustic diarization/speaker-
clustering model (pyannote etc.) is needed anywhere in this pipeline, just per-track
voice-activity detection (VAD) + streaming speech-to-text.

**Infra choices (confirmed):**
- **LiveKit Cloud (free tier)** for room media (audio/video transport, per-track
  server-side subscription). Chosen over self-hosting an SFU (real infra project on
  its own) or hand-rolled P2P mesh (bandwidth-heavy, ICE/TURN edge cases risk demo
  reliability).
- **Azure Speech streaming STT (free tier)** for live per-participant transcription
  — reuses the account/key already in this project (`AZURE_SPEECH_KEY`/`_REGION`,
  currently used for pronunciation scoring). Chosen over local streaming Whisper
  (CPU risk running up to 6 concurrent decode streams) or the browser Web Speech API
  (inconsistent quality, awkward to get text server-side for live stats).

## Data model

New tables, following the existing `sessions`/`transcripts`/`session_metrics`
pattern:

- **`rooms`** — `id`, `host_user_id`, `mode` (`identified` | `anonymous`), `status`
  (`waiting` | `live` | `ended` | `analyzed`), `join_code`, `max_participants`
  (≤6), `created_at`, `ended_at`
- **`room_participants`** — `id`, `room_id`, `user_id` (always known server-side,
  even in anonymous mode — needed for the participant's own history and to prevent
  abuse), `alias_name` (assigned if `mode=anonymous`, e.g. "Speaker 3"),
  `livekit_identity`, `joined_at`, `left_at`
- **`room_audio_tracks`** — per-participant recorded audio (mirrors existing session
  audio storage; video is not recorded — see Non-goals)
- **`room_transcript_segments`** — `room_id`, `participant_id`, `text`, `start_ts`,
  `end_ts`, `is_final` (built incrementally from Azure streaming STT, finalized
  post-session)
- **`room_reports`** — `room_id`, `generated_at`, per-participant JSON (talk-time %,
  turn count, interruptions made/received, longest monologue, silence %, dominance
  index) + LLM qualitative section + overall summary

**Anonymous-mode privacy:** anonymity holds end-to-end. The post-session report
keeps other participants alias-labeled (e.g. "Speaker 2 interrupted Speaker 4").
Each participant still sees their *own* metrics tied to their own account (for
personal progress tracking on their dashboard), but never learns which alias
corresponded to which other real participant.

## API / WebSocket surface

```
POST   /rooms                    create room (mode, max_participants) → join_code
POST   /rooms/{code}/join        join → LiveKit token + alias (if anonymous)
WS     /rooms/{id}/live          host-only live stats stream
POST   /rooms/{id}/end           host ends session → finalize + enqueue analysis job
GET    /rooms/{id}/report        post-session report (all participants, once ready)
GET    /rooms                    list rooms the user hosted/joined (dashboard)
```

## Lifecycle & roles

- Room creator is automatically its host: can end the room and sees the live-stats
  dashboard. Nothing requires them to actively moderate — in a self-conducted peer
  session, the host is just one of the participants who happens to hold those
  controls.
- Mode (`identified`/`anonymous`) is fixed at room creation, not changeable mid-room.
- **Identified mode:** normal video + real display name.
- **Anonymous mode:** alias assigned on join; video track is never published (camera
  off in the UI, not just hidden); audio publishes normally (voice unmasked, per
  Non-goals).

## Session access

No scheduling/matchmaking system — sourcing participants is manual (a
WhatsApp/Discord group, a classroom) and out of scope. The existing
`join_code` (8-char, unambiguous alphabet, already in the Data model) is the
whole mechanism: the host shares a join link (`{frontend_base_url}/rooms/
join/{join_code}`) plus, on the room's waiting screen, a QR code encoding
that same link for in-person sharing (read aloud or scanned — no new
backend endpoint, purely a frontend render of the existing join URL).

## Consent & retention

- **Consent:** before a participant's audio/video track is published (i.e.
  before the LiveKit `join_room` call resolves into an actual publish),
  the client shows an explicit opt-in modal — "This session will be
  recorded (audio only) for analysis. Continue?" — with Join/Decline. A
  decline does not join the room. This is a frontend gate; no new backend
  state beyond what `room_participants.joined_at` already implies (a join
  row only exists once consent was given).
- **Retention:** raw audio (`room_participants.audio_path`, i.e.
  `room_audio_tracks`) is auto-deleted 30 days after `rooms.ended_at`.
  Transcripts (`room_transcript_segments`) and reports (`room_reports`) are
  retained indefinitely for the participant's own progress tracking,
  matching how the existing solo-session transcripts/scores already behave
  — only the raw recording gets a retention window. Deletion is a scheduled
  ARQ job (daily sweep), not covered further here — implementation detail
  for the analytics-extension plan.
- Video is still never recorded (existing Non-goal) — nothing to retain or
  delete there.

## Cost note

At ~100 users / MVP scale (Dec 2026 interview season), the chosen free
tiers are enough to ship without paying for anything: LiveKit Cloud's Build
tier (5,000 WebRTC minutes/month, 50GB egress) comfortably covers this
volume, and Azure Speech's free tier covers streaming STT + pronunciation
scoring at the same scale. Revisit only if usage grows well past this —
self-hosting LiveKit only starts paying off past roughly 150 concurrent
sessions.

## Live analytics (host-only, during the session)

Computed from per-track VAD + STT timestamps:

- **Talk-time %** — active-speech duration per participant ÷ elapsed session time
- **Turns** — count of speech-segment starts (adjacent same-speaker segments merged
  below a short gap threshold)
- **Interruption** — participant B's segment starts while A's is still active
  (overlap > ~300ms) and A stops shortly after → counted as "B interrupted A",
  tracked both ways (made/received) per participant
- **Non-participation** — flags participants whose talk-time % falls under a low
  threshold
- **Dominance index** — a normalized talk-time-skew measure across participants
  (same family as published speaking-time/dominance research) — fully
  deterministic, no LLM involved

## Post-session report

1. **Deterministic metrics** (same as the live stats, finalized over the full
   session) — per participant: talk-time %, turns, interruptions made/received,
   longest monologue, silence %, dominance index.
2. **LLM qualitative pass** — reuses the `argument_quality.py` pattern: strict JSON
   schema via `client.messages.parse`, Pydantic-validated, isolated so a failure or
   missing key never blocks the deterministic report (same `not_configured`/`error`
   per-scorer isolation as the existing `app/scoring/pipeline.py`). Fed the full
   speaker-labeled transcript plus the deterministic stats. Prompted to return
   neutral, evidence-tied behavioral observations (e.g. "frequently spoke over
   others without yielding," "built on other participants' points with specific
   references") — explicitly guarded against personality-trait labels (no
   "arrogant," "rude," etc.) since those are subjective, potentially biased claims
   about a real person, especially in an interview-evaluation context.

## Post-session analytics scope (extension)

This is the actual differentiator vs. a plain video call, and the reason a
participant would want a report beyond talk-time/interruptions. All of it
reuses existing solo-trainer modules, run per participant per room instead
of once per solo session — no new ASR/embedding/LLM providers needed:

- **Topic alignment** — score each participant's transcript segments
  against the room's discussion topic. Reuses `app/scoring/relevance.py`
  (local `sentence-transformers`, already used for the solo trainer's
  topic-relevance score) unchanged in method, just applied per participant
  instead of per solo session.
- **Pronunciation** — reuses `app/scoring/pronunciation.py` (Azure Speech,
  already configured) per participant's audio.
- **Speech quality (vocabulary, grammar, filler words, fluency)** — reuses
  `app/metrics/vocabulary.py`, `syntax.py`, `fluency.py`, `text_utils.py`
  per participant's transcript. (Note for the implementation plan: verify
  the existing filler-word matching handles multi-word phrases like "you
  know" correctly against however transcripts are tokenized here — this
  was found to be silently broken in the external draft docs' version and
  is worth a direct unit-test check before trusting it in this context.)
- **Sentiment / agreement-disagreement** — net new, lightweight: a small
  marker-phrase heuristic ("I agree" / "I disagree" / "however" / "on the
  other hand" etc.) per segment, in the same spirit as the deterministic
  `live_stats.py` heuristics — not a new ML model. An LLM-based version is
  out of scope for MVP; the existing qualitative LLM pass can already
  surface agreement/disagreement in prose if useful.
- **Composite competency scores** — per participant, combine the above plus
  the existing deterministic stats (talk-time, turns, interruptions,
  dominance) into simple weighted 0-100 scores: Leadership, Engagement,
  Listening, Communication Clarity, Topic Alignment, Confidence, Teamwork.
  Pure arithmetic over already-computed numbers (no new external calls) —
  the exact weights are an implementation-plan detail, not a design
  decision that needs to be locked here.

All of the above are deterministic/local-model outputs and follow the same
graceful-degradation rule as the rest of this spec: if a given scorer isn't
configured or fails for one participant, that participant's report shows
that section as `not_configured`/`error` without blocking the rest of the
report (same `app/scoring/pipeline.py` isolation pattern already used).

This is new implementation scope beyond Plan 1/2 (backend/frontend for the
room itself) — see "Deferred (future specs)" for how it's sequenced.

## Error handling

- LiveKit token issuance/join failure → clear error to the joining client, room
  stays in `waiting`.
- A participant's Azure STT stream failing mid-session → that participant's live
  transcript pauses (logged), doesn't take down the room or other participants'
  streams; recorded audio still lets the post-session job recover their transcript
  from the stored recording (retryable, same idempotency spirit as the existing
  transcription job).
- LLM qualitative pass failing/not configured → post-session report still ships
  with deterministic metrics only, qualitative section marked `not_configured`/
  `error` (mirrors `app/scoring/pipeline.py`'s existing per-scorer isolation).
- Host ends room mid-analysis / server restart → `analyze_room_session` job is
  idempotent and resumable, same pattern as the existing transcription job's
  kill-mid-job resume behavior (Day 2 DoD).

## Testing / evaluation plan

Extends the existing evaluation harness pattern (`EVALUATION.md`,
`scripts/evaluate.py`):

- Before trusting live audio, validate the interruption/talk-time/dominance
  heuristics against **synthetic multi-track transcripts** with engineered, known
  overlap patterns (same spirit as Day 3's three deliberately-different TTS
  samples) — confirm the heuristics correctly flag known interruptions and
  talk-time skew before they ever touch a real multi-person call.
- Once real rooms are running: collect a handful of real multi-person sessions,
  have participants (or an outside rater) label interruptions/dominance manually,
  and compare against the pipeline's output — same spirit as the existing Spearman-
  correlation harness, extended to multi-speaker interaction metrics.
- LLM qualitative pass: spot-check outputs against the "no personality labels, only
  evidence-tied observations" guardrail; flag and revise the prompt if it drifts.

## Deferred (future specs)

Sequencing, nearest first:

1. **This spec (Plan 1: backend, Plan 2: frontend)** — the room itself:
   join/live call/talk-time-interruption-dominance stats/LLM qualitative
   pass.
2. **Post-session analytics extension** (scoped above, not yet planned
   task-by-task) — topic alignment, pronunciation, speech quality,
   sentiment, composite scores. Next spec-to-plan once 1 is implemented and
   manually verified.
3. **Phase 2: engagement/proctoring** — webcam eye-contact/engagement
   signals and basic self-run-mock-interview proctoring (tab-switch,
   fullscreen enforcement). Client-side only, aggregate metrics sent to the
   backend — never raw frames or stored video, consistent with this spec's
   video non-goal above. Gets its own brainstorming pass once 1-2 are real;
   not scoped further here deliberately (see "Why this needs its own spec").

Also deferred, unscoped (backlog, not yet worth a spec):

- Voice-masking for anonymous mode.
- Video recording/playback of sessions.
- Interviewer-facilitation tooling (structured scoring UI, flags, scheduling).
- Rooms larger than ~6 participants (would need an SFU-aware redesign of the
  live-stats fan-out).
- Post-MVP product ideas carried over from the 2026-08-19 external draft
  docs, worth revisiting once 1-3 above are real: AI-generated coaching
  notes (extends the existing LLM qualitative pass), gamification
  (streaks/badges/leaderboards), benchmark comparisons against past
  sessions, a structured mock-interview mode (preset topics + timed
  phases), and exportable/shareable PDF reports.
