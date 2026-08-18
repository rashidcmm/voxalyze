> **Status: SUPERSEDED (2026-08-19).** Written in a separate session with no
> visibility into this repo, so it proposes rebuilding things that already
> exist here (auth, FastAPI backend, Next.js frontend, Postgres/Redis) and
> contradicts decisions already made by
> `docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md`
> (LiveKit instead of hand-rolled Peerjs P2P, Azure Streaming STT instead of
> Deepgram, no diarization needed since each participant has their own audio
> track, Postgres/Redis only — no MongoDB). This doc's useful analytics
> research (topic alignment, speech quality, sentiment, composite
> competency scores) was folded into that spec's "Post-session analytics
> scope" section; its rejected choices (separate repos, Deepgram, OpenAI
> embeddings, MongoDB, rebuilding auth) were not carried over. Kept here as
> dated research notes only — **not an active plan.**
>
> Also note: the `Analytics_Algorithms_Reference.md` code in this same
> folder has real bugs (multi-word filler phrases never match its
> tokenizer; interruption/dominance math assumes pre-sorted segments) — the
> tested, correct version lives at `backend/app/rooms/live_stats.py` per
> the 08-11 backend plan.

# GD Analytics Platform: Complete Architecture & Roadmap

**Project Name:** AI-Powered Group Discussion Analyzer  
**Target Users:** Students preparing for placement interviews, companies evaluating candidates  
**Core Differentiator:** Real-time multi-participant analytics + personalized speech quality insights

---

## 1. VISION & SCOPE

### 1.1 What Makes This Different
- **Google Meet/Zoom:** Generic video conferencing
- **Your Platform:** Specialized analytics for group discussion evaluation
  - Individual speech metrics (not just timestamps)
  - Group dynamics analysis
  - Competency-based feedback
  - Host dashboard + personal performance dashboards

### 1.2 Core Features at Launch (MVP)

**Multi-User Session Management**
- 5-8 participants in single session
- Optional host role (can participate or just observe)
- Real-time video/audio streaming with WebRTC

**Group-Level Analytics**
- Who speaks first? (establishes leadership)
- Turn-taking patterns & interruptions
- Topic alignment (off-topic detection)
- Speaking time distribution (dominance/participation)
- Agreement/disagreement detection
- Overlapping speech detection (simultaneous speakers)

**Individual Analytics**
- Speech duration per turn
- Pause patterns (confidence indicator)
- Vocabulary richness (unique word count, complexity)
- Grammar/filler word usage ("uh", "um", "like")
- Pronunciation clarity score
- Pace of speech (words per minute)
- Topic alignment of contributions

**Host Dashboard**
- Live participant metrics during session
- Real-time transcript with speaker attribution
- Post-session summary report

**Personal Dashboard**
- Individual performance card
- Comparison with group average
- Weak areas identified
- Improvement recommendations

---

## 2. DETAILED FEATURE BREAKDOWN

### 2.1 Real-Time Features

#### A. Speech-to-Text & Speaker Diarization
- **Input:** Audio stream from each participant
- **Processing:**
  - Voice Activity Detection (VAD) → identify speech regions
  - Speaker Diarization → map audio segments to speakers
  - ASR (Automatic Speech Recognition) → generate transcription
  - Timestamping → maintain millisecond-level precision

**Research-Backed Capabilities:**
- <cite index="10-1">Handle overlapping speech (simultaneous multiple speakers) explicitly</cite>
- <cite index="13-1">Detect interruptions by identifying overlapping speech where timestamps overlap without pause</cite>
- Acoustic feature extraction for speaker identification

**Libraries/Models:**
- `pyannote.audio` (speaker diarization)
- `Deepgram` or `AssemblyAI` (ASR with diarization)
- `Whisper` (open-source ASR alternative)

#### B. Turn-Taking & Interruption Detection
<cite index="11-1">Distinguish between backchannels ("mm-hmm") and genuine interruption attempts</cite>

**Metrics to Track:**
- <cite index="17-1">Successful interruption: Speaker A speaks → B starts speaking → A continues but stops after ≥X seconds, B continues ≥Y seconds</cite>
- Unsuccessful interruption attempts
- Backchannel frequency (supportive vs. dismissive tone)
- Turn acquisition time (1.5+ seconds = acquired floor)
- Overlap duration (when multiple people speak)

**Implementation:**
- Frame-by-frame speech detection
- Temporal clustering of speech segments
- Confidence scoring (probability user is done speaking)

#### C. Topic Alignment
- Extract discussion topic/prompt from host input
- Use NLP to compare participant utterances against topic
- Score each turn: 0-100 (off-topic → on-topic)
- Highlight off-topic speakers for host awareness

**Model Approach:**
- Sentence embeddings (sentence-transformers)
- Cosine similarity to topic embedding
- Semantic relevance scoring

#### D. Sentiment & Debate Detection
- Detect agreement/disagreement signals
- Identify arguments vs. passive agreement
- Flag hostile language (if relevant)
- Track who supports whose ideas

**Methods:**
- Sentiment analysis on transcript segments
- Negation detection ("I disagree", "but that's wrong")
- Discourse markers ("however", "on the other hand")

#### E. Eye Contact & Body Language (Computer Vision)
- Each participant's webcam feed → visual analysis
- Eye contact detection (looking at camera vs. away)
- Facial expressions (engagement vs. boredom)
- Hand gestures (emphatic speaking)
- Nodding frequency (agreement signal)

**Tools:**
- MediaPipe (pose + face detection)
- OpenCV (gaze estimation)
- TensorFlow Lite (on-device inference)

---

### 2.2 Post-Session Analytics

#### A. Speech Quality Metrics

**Pronunciation & Clarity**
- Phonetic accuracy (ASR confidence scores)
- Accent consistency
- Word clarity index (speech rate vs. intelligibility)
- Articulation rate (syllables per second)

**Vocabulary Analysis**
- Unique word count (lexical diversity)
- Common word frequency (filler words: "uh", "um", "like", "you know")
- Sentence length distribution
- Use of complex/technical vocabulary
- Repeated phrases detection

**Grammar & Fluency**
- Filler word frequency per turn
- Self-corrections detected in transcript
- Sentence completeness rate
- Hesitation patterns

**Delivery Quality**
- Average speaking rate (WPM)
- Pace variance (monotone vs. dynamic)
- Silent pauses (ms between turns)
- Verbal pacing (comfortable vs. rushed)

#### B. Individual Performance Scoring
Composite scores across:
1. **Leadership** (0-100): Who initiates? Frequency of proposals?
2. **Engagement** (0-100): Speaking time, turn count, response latency
3. **Listening** (0-100): Acknowledgments, backchannels, building on others' ideas
4. **Communication Clarity** (0-100): Vocabulary, grammar, pronunciation
5. **Topic Alignment** (0-100): % of on-topic statements
6. **Confidence** (0-100): Inverse of filler words, pause frequency
7. **Teamwork** (0-100): Support for others' ideas, conflict resolution

#### C. Group Dynamics Summary
- **Hierarchy Map:** Who has floor dominance? Who's marginalized?
- **Cliques:** Do any subgroups align strongly?
- **Polarization:** Are there opposing viewpoints?
- **Inclusivity:** Is everyone getting airtime?
- **Consensus Level:** How much agreement in final discussion?

---

## 3. ARCHITECTURE

### 3.1 High-Level System Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT TIER                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │  Next.js     │  │   WebRTC     │  │  Computer    │           │
│  │  Frontend    │──│   Peer.js    │──│  Vision      │           │
│  │  (React)     │  │              │  │  (MediaPipe) │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│         │                │                    │                  │
│         └────────────────┼────────────────────┘                  │
│                          │ (Web Socket)                          │
└──────────────────────────┼──────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                   BACKEND TIER                                   │
├──────────────────────────┼──────────────────────────────────────┤
│                          │                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │           FastAPI / Node.js Signaling Server             │   │
│  │   (WebSocket: SDP offers, ICE candidates, control)       │   │
│  └──────────────────────────────────────────────────────────┘   │
│         ↑                                     ↑                  │
│         │                                     │                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Deepgram   │  │   Chroma/    │  │  Claude API  │          │
│  │   ASR/       │  │  Pinecone    │  │  (LLM for    │          │
│  │ Diarization  │  │  (Embeddings)│  │  analysis)   │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         ↑                ↑                    ↑                  │
│         │                │                    │                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │     Core Analytics Engine (Python/Node.js)              │   │
│  │  • Turn-taking detector                                  │   │
│  │  • Topic relevance scorer                                │   │
│  │  • Speech quality analyzer                               │   │
│  │  • Group dynamics calculator                             │   │
│  └──────────────────────────────────────────────────────────┘   │
│         ↑                                                        │
│         │                                                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            MongoDB / PostgreSQL                           │   │
│  │  • Sessions, users, transcripts                           │   │
│  │  • Analytics cache, reports                              │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Tech Stack Recommendation

**Frontend**
- **Framework:** Next.js 14+ (App Router)
- **Video:** Peerjs / LiveKit SDK (WebRTC management)
- **Vision:** MediaPipe Web (eye contact, pose detection)
- **State:** React Query + Zustand
- **UI:** Tailwind CSS + shadcn/ui
- **Real-time:** Socket.IO client

**Backend**
- **Primary API:** FastAPI (Python) — async-first, great for real-time
  - Alternative: Node.js + Express for unified JS ecosystem
- **Signaling Server:** Socket.IO (FastAPI-socketio) or native WebSocket
- **ASR/Diarization:** 
  - Deepgram API (cloud) — simplest, lowest latency
  - Self-hosted Whisper + Pyannote (cost-effective, privacy)
- **NLP/Embeddings:** 
  - OpenAI Embeddings API (GPT-3.5-turbo-3.5, cost: ~$0.02 per 1K)
  - HuggingFace (sentence-transformers) — self-hosted, free
- **Analytics Engine:** Python
  - Libraries: `nltk`, `spacy`, `textblob` (grammar), `pydub` (audio)
- **Database:** 
  - MongoDB (flexible schemas for transcripts/analytics)
  - PostgreSQL (transactions, relational data)
  - Combined approach: Postgres for metadata, MongoDB for blobs
- **Caching:** Redis (session state, real-time leaderboards)
- **Storage:** AWS S3 / MinIO (video/audio recordings)
- **Message Queue:** Celery + Redis (async tasks: transcription, reports)

**Deployment**
- **Frontend:** Vercel / Netlify
- **Backend:** AWS EC2 / DigitalOcean / Railway
- **Containerization:** Docker + Docker Compose

---

## 4. MODULE BREAKDOWN & TASK DISTRIBUTION

### MODULE 1: User & Session Management
**Responsibility:** Auth, sessions, participants, permissions

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| User signup/login (JWT) | Backend | M | 3-4 days |
| Email verification (Brevo) | Backend | M | 2 days |
| Password reset flow | Backend | M | 2 days |
| Session creation & participant invite | Backend | M | 3-4 days |
| Roles: Host, Participant, Observer | Backend | M | 2 days |
| Rate limiting & abuse prevention | Backend | L | 3 days |

**Databases:** Users (PostgreSQL), Sessions (PostgreSQL)

---

### MODULE 2: Real-Time Video & Audio Infrastructure
**Responsibility:** WebRTC signaling, peer-to-peer streaming, media handling

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| WebRTC signaling server (SDP/ICE) | Backend | H | 5-6 days |
| Peerjs integration (client) | Frontend | M | 4 days |
| Audio stream capture per participant | Frontend | M | 3 days |
| Local recording (MediaRecorder API) | Frontend | M | 2 days |
| Server-side recording aggregation | Backend | H | 4-5 days |
| Network quality monitoring | Both | M | 3 days |

**Tech:** Peerjs, Socket.IO, WebRTC, MediaRecorder API

---

### MODULE 3: Speech-to-Text & Diarization
**Responsibility:** Convert audio → text + speaker labels + timestamps

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Deepgram SDK integration | Backend | M | 3 days |
| Stream audio chunks to ASR API | Backend | H | 4 days |
| Speaker diarization pipeline | Backend | H | 5 days |
| Real-time transcript buffering | Backend | M | 2 days |
| Fallback to local Whisper (optional) | Backend | L | 2 days |
| Transcript storage & retrieval | Backend | M | 3 days |

**Databases:** Transcripts (MongoDB), Segments (PostgreSQL for indexing)

---

### MODULE 4: Core Analytics Engine
**Responsibility:** Extract insights from transcripts + metadata

#### 4A. Turn-Taking & Interruption Analysis

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Detect speaker transitions (diarization → timestamps) | Backend | M | 3 days |
| Classify interruptions vs. backchannels | Backend | H | 5 days |
| Calculate turn statistics (duration, frequency, latency) | Backend | M | 2 days |
| Dominance/participation scoring | Backend | M | 3 days |

#### 4B. Topic Alignment

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Extract topic embedding (sentence-transformers) | Backend | M | 2 days |
| Score each turn for relevance | Backend | M | 3 days |
| Real-time topic deviation alerts | Backend | M | 2 days |

#### 4C. Speech Quality Analysis

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Filler word detection ("uh", "um", "like") | Backend | M | 2 days |
| Vocabulary richness (TFIDF, lexical diversity) | Backend | M | 3 days |
| Grammar analysis (spaCy, TextBlob) | Backend | M | 3 days |
| Speech rate (WPM) calculation | Backend | M | 1 day |
| Pause pattern analysis | Backend | M | 2 days |
| Confidence scoring (inverse of hesitation) | Backend | M | 2 days |

#### 4D. Sentiment & Debate

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Sentiment analysis per turn | Backend | M | 2 days |
| Agreement/disagreement detection | Backend | M | 3 days |
| Argument mapping (who supports whom) | Backend | H | 4 days |

**Tech:** spaCy, NLTK, sentence-transformers, TextBlob, transformers (HuggingFace)

---

### MODULE 5: Computer Vision Analytics
**Responsibility:** Eye contact, engagement, body language from video

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| MediaPipe face detection setup | Frontend | M | 2 days |
| Eye contact scoring (gaze to camera) | Frontend | M | 3 days |
| Engagement detection (face + pose) | Frontend | M | 3 days |
| Facial expression analysis (smile, frown) | Frontend | M | 2 days |
| Gesture detection (hand movement) | Frontend | M | 3 days |
| Performance optimization (GPU acceleration) | Frontend | L | 3 days |

**Tech:** MediaPipe, TensorFlow.js, OpenCV.js

---

### MODULE 6: Analytics Dashboard & Reporting
**Responsibility:** Display insights to host + individual users

#### 6A. Host Dashboard

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Live participant cards (speaking time, engagement) | Frontend | M | 3 days |
| Real-time transcript view with speaker labels | Frontend | M | 4 days |
| Interrupt alerts & alerts | Frontend | M | 2 days |
| Topic alignment overlay | Frontend | M | 2 days |
| Group dynamics heatmap | Frontend | M | 3 days |
| Session summary export (PDF) | Backend | M | 3 days |

#### 6B. Individual Analytics Dashboard

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Personal performance card | Frontend | M | 2 days |
| 7 competency scores (leadership, clarity, etc.) | Frontend | M | 3 days |
| Comparison with group average | Frontend | M | 2 days |
| Weak areas & improvement tips | Backend/Frontend | M | 3 days |
| Speech sample playback with annotations | Frontend | M | 3 days |

**Databases:** Analytics Cache (MongoDB/Redis)

---

### MODULE 7: Security & Compliance
**Responsibility:** Data privacy, encryption, access control

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| End-to-end encryption (WebRTC, storage) | Backend | H | 5 days |
| GDPR compliance (data deletion, export) | Backend | H | 4 days |
| Recording consent management | Backend | M | 2 days |
| API key management (Deepgram, Claude, etc.) | DevOps | M | 2 days |
| Rate limiting & DDoS protection | Backend | M | 3 days |

---

### MODULE 8: Testing & Deployment Pipeline
**Responsibility:** QA, CI/CD, monitoring

| Task | Owner | Complexity | Est. Time |
|------|-------|-----------|-----------|
| Unit tests (analytics engine) | Backend | M | 4 days |
| Integration tests (WebRTC + ASR) | Both | H | 5 days |
| Load testing (10+ concurrent sessions) | DevOps | H | 4 days |
| CI/CD pipeline (GitHub Actions) | DevOps | M | 3 days |
| Monitoring & alerting (Sentry, LogRocket) | DevOps | M | 2 days |
| Docker setup & deployment automation | DevOps | M | 3 days |

---

## 5. PHASED DELIVERY ROADMAP

### **PHASE 0: MVP Foundation (Weeks 1-4)**
Focus: Core video conferencing + basic transcription

**Deliverables:**
- [ ] User auth system (login, signup)
- [ ] Session creation & participant joining
- [ ] WebRTC P2P video/audio (Peerjs)
- [ ] Deepgram ASR integration (single speaker transcript)
- [ ] Basic speaker diarization
- [ ] Transcript storage in DB
- [ ] Simple transcript viewer (frontend)

**Not included:** Analytics, dashboards, vision

---

### **PHASE 1: Group Analytics Core (Weeks 5-9)**
Focus: Multi-user analytics

**Deliverables:**
- [ ] Turn-taking detection (timestamps, interruption classification)
- [ ] Speaking time distribution & dominance scoring
- [ ] Topic relevance scoring (semantic similarity)
- [ ] Filler word & speech quality analysis
- [ ] Host dashboard (live participant cards)
- [ ] Per-user performance cards (basic metrics)

---

### **PHASE 2: Advanced Analytics (Weeks 10-14)**
Focus: Deeper insights + polish

**Deliverables:**
- [ ] Grammar & vocabulary analysis
- [ ] Sentiment & debate detection
- [ ] Argument mapping (who supports whom)
- [ ] Confidence scoring (hesitation patterns)
- [ ] Individual analytics dashboard (7 competencies)
- [ ] Group dynamics heatmap & insights

---

### **PHASE 3: Vision & Polish (Weeks 15-18)**
Focus: Computer vision + refinements

**Deliverables:**
- [ ] Eye contact detection (MediaPipe)
- [ ] Engagement scoring
- [ ] Facial expression analysis
- [ ] Gesture recognition
- [ ] PDF report generation
- [ ] Performance optimization

---

### **PHASE 4: Security & Scale (Weeks 19-22)**
Focus: Hardening + production readiness

**Deliverables:**
- [ ] E2E encryption
- [ ] GDPR compliance
- [ ] Load testing (10+ sessions)
- [ ] Monitoring & alerting
- [ ] Docker deployment
- [ ] Rate limiting & abuse prevention

---

## 6. RESEARCH INSIGHTS & BEST PRACTICES

### From Industry Research:

1. **Interruption Taxonomy** <cite index="15-1">Differentiate between "turn-taking" and "interruptions"; understand false starts and repair mechanisms</cite>

2. **Backchannel Recognition** <cite index="11-1">Backchannels ("mm-hmm") are distinct from real interruption attempts and require separate classification</cite>

3. **Latency-Accuracy Tradeoff** <cite index="12-1">Lower thresholds for turn-taking detection reduce latency but increase false positives (unwanted interruptions); higher thresholds delay detection but reduce false positives</cite>

4. **Floor Acquisition** <cite index="15-1">A turn (or floor) is acquired if a speaker is not interrupted for >1.5 seconds</cite>

5. **Diarization Challenges** <cite index="10-1">Overlapping speech requires advanced handling; systems that assign each frame to single speaker lose critical information</cite>

6. **Group Discussion Evaluation** <cite index="8-1">GD assessments measure communication skills, analytical thinking, teamwork, and leadership; AI can automate this evaluation via NLP and sentiment analysis</cite>

7. **Focus Group Analytics** <cite index="7-1">NLP and sentiment analysis streamline qualitative analysis, capturing complex emotional and thematic insights from group discussions</cite>

---

## 7. ADVANCED FEATURES (Post-MVP)

### 7.1 Not in Scope for MVP but Worth Planning

**AI-Generated Feedback**
- Use Claude API to generate personalized coaching notes
- "You interrupted 3 times—try active listening by asking follow-ups"
- Topic-aware suggestions

**Gamification**
- Leaderboards (who's improving fastest?)
- Achievement badges (first to speak, most aligned, best listener)
- Practice streaks

**Benchmark Comparisons**
- Compare your metrics against past sessions
- Industry benchmarks (if applicable)
- Goal tracking

**Mock Interview Mode**
- Pre-set topics with HR-style questions
- Time limits for each discussion phase
- Simulated HR interviewer feedback

**Export & Sharing**
- PDF reports for self-improvement
- Shareable links for mentors/coaches
- Embedded performance widgets

---

## 8. DEPLOYMENT CHECKLIST

- [ ] Environment variables secured (Deepgram, Claude, Brevo keys)
- [ ] Database backups automated
- [ ] CORS/CSRF configured
- [ ] Rate limiting deployed
- [ ] SSL/TLS certificates active
- [ ] CDN for frontend assets (Vercel/Cloudflare)
- [ ] Error tracking (Sentry)
- [ ] Usage monitoring (PostHog / Mixpanel)
- [ ] Support email/contact form ready
- [ ] Terms of Service & Privacy Policy drafted

---

## 9. SUCCESS METRICS

**Performance:**
- Session startup time: <5 seconds
- Transcript latency: <2 seconds behind real-time
- API response time: <200ms (p95)

**User Experience:**
- Completion rate: >85% (sessions finish without dropout)
- NPS score: >40
- Feature adoption: >60% use analytics dashboard

**Business:**
- Cost per session: <$0.50 (Deepgram ASR ~$0.01/min, hosting ~$100/month for 1000 sessions)
- Churn rate: <10% MoM

---

**Document Version:** 1.0  
**Last Updated:** August 2026  
**Status:** Ready for Implementation
