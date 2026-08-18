> **Status: SUPERSEDED (2026-08-19).** Written blind to this repo — auth
> (signup/login/JWT/Brevo email verification/password reset/rate limiting),
> FastAPI backend, and Next.js frontend it treats as open decisions/Day-1
> work are already built and hardened here (`backend/app/api/auth.py`,
> `backend/app/core/{security,email,email_tokens,rate_limit}.py`). The
> proposed separate `gd-analytics-backend`/`gd-analytics-frontend` repos,
> MongoDB, Deepgram, and OpenAI-embeddings choices were rejected in favor of
> extending the existing monorepo per
> `docs/superpowers/specs/2026-08-11-multiparty-gd-room-mvp-design.md`
> (LiveKit, Azure Streaming STT, local sentence-transformers, Postgres/
> Redis only). Kept here as dated research notes only — **not an active
> plan.**

# GD Analytics Platform: Implementation Checklist

## IMMEDIATE NEXT STEPS (This Week)

### 1. Tech Stack Finalization
- [ ] Decide: FastAPI vs Node.js for backend?
  - FastAPI: Better for async speech processing, ML pipelines
  - Node.js: Unified JS ecosystem, simpler for full-stack devs
- [ ] Decide: Deepgram (cloud) vs Whisper + Pyannote (self-hosted)?
  - Deepgram: Simpler, lower setup time, ~$0.01/min
  - Self-hosted: Cost-effective at scale, privacy-first, 2-3 day setup
- [ ] Choose embeddings: OpenAI API vs HuggingFace (local)?

**Recommendation for Placement Season Timeline:**
- Backend: FastAPI (Python is better for analytics)
- ASR: Deepgram API (simplest, lowest time-to-market)
- Embeddings: OpenAI Embeddings (reliable, low cost ~$0.01 per 1K tokens)

---

### 2. Infrastructure Setup
```bash
# Backend repo structure
gd-analytics-backend/
├── app/
│   ├── main.py                 # FastAPI entry point
│   ├── auth/                   # User login/signup/jwt
│   ├── sessions/               # Session management
│   ├── websocket/              # WebRTC signaling
│   ├── transcription/          # Deepgram integration
│   ├── analytics/              # Core analytics engine
│   │   ├── turn_taking.py
│   │   ├── speech_quality.py
│   │   ├── topic_analysis.py
│   │   ├── sentiment.py
│   │   └── group_dynamics.py
│   └── database/               # DB models (SQLAlchemy)
├── docker-compose.yml          # Postgres, Redis, Mongo
├── requirements.txt
└── .env.example

# Frontend repo structure
gd-analytics-frontend/
├── app/
│   ├── (app)/                  # Next.js app router
│   │   ├── sessions/[id]/page.tsx      # Main session page
│   │   ├── dashboard/page.tsx          # Analytics dashboard
│   │   └── profile/page.tsx            # Personal analytics
│   ├── components/
│   │   ├── VideoGrid.tsx               # WebRTC video tiles
│   │   ├── TranscriptViewer.tsx        # Live transcript
│   │   ├── AnalyticsCard.tsx           # Metric displays
│   │   └── HostDashboard.tsx           # Live host view
│   └── lib/
│       ├── webrtc.ts                   # Peerjs setup
│       └── analytics.ts                # API calls
└── package.json
```

- [ ] Initialize Git repos (backend + frontend)
- [ ] Create `.env` template with required keys:
  - `DEEPGRAM_API_KEY`
  - `OPENAI_API_KEY` (for embeddings)
  - `BREVO_API_KEY` (for email)
  - `JWT_SECRET`
  - `DATABASE_URL` (Postgres)
  - `REDIS_URL`
  - `MONGODB_URL`
- [ ] Set up Docker Compose (Postgres + Redis + Mongo local)

---

### 3. Phase 0 Task Breakdown (Week 1)

**Backend (3-4 days):**

**Day 1-2: User & Session Management**
```python
# Priority: Get auth working first
# app/auth/models.py → User, RefreshToken
# app/auth/routes.py → /signup, /login, /refresh
# Use JWT with 30-min access token + 7-day refresh

# Database schema:
# - users (id, email, password_hash, created_at)
# - sessions (id, host_id, participants[], topic, created_at, status)
# - participants (user_id, session_id, joined_at)
```

**Day 3-4: WebRTC Signaling**
```python
# app/websocket/handlers.py → manage SDP offers/answers, ICE candidates
# Use Socket.IO for signaling
# Broadcast participant list on join
# Handle disconnect/reconnect

from fastapi_socketio import SocketManager
# Emit events:
# - user_joined(user_id, name)
# - offer(sdp_offer)
# - answer(sdp_answer)
# - ice_candidate(candidate)
# - user_left(user_id)
```

**Frontend (2-3 days):**

**Day 1-2: Session UI + Peerjs Integration**
```tsx
// pages/sessions/[id]/page.tsx
// Components: VideoGrid, Controls (start/stop), Participant List
// Peerjs setup:
import Peerjs from 'peerjs';

const peer = new Peer();
peer.on('open', (peerId) => {
  socket.emit('join_session', { userId, peerId });
});

// Listen for other participants
socket.on('user_joined', (user) => {
  const call = peer.call(user.peerId, localStream);
});
```

**Day 3: Audio Capture & Streaming**
```tsx
// Capture local microphone
const localStream = await navigator.mediaDevices.getUserMedia({
  audio: { echoCancellation: true, noiseSuppression: true },
  video: { width: 1280, height: 720 },
});

// Send audio to backend for recording
```

---

### 4. Deepgram Integration (Day 4-5)

**Backend:**
```python
# app/transcription/deepgram.py
from deepgram import DeepgramClient

async def stream_transcribe(audio_stream):
    """Stream audio to Deepgram, get back diarized transcript"""
    dg_client = DeepgramClient(api_key=settings.DEEPGRAM_API_KEY)
    
    options = DeepgramLiveOptions(
        model="nova-2",
        language="en",
        smart_format=True,
        diarize=True,  # Speaker diarization
        multi_channel=True,  # Handle multiple audio streams
    )
    
    async with dg_client.live.v("1").open(options) as dgconnection:
        # Buffer audio from each participant
        # Deepgram returns: { speech: "...", speaker: 0, start_time: ms, end_time: ms }
```

**Store transcript:**
```python
# Database model
class Transcript(Base):
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("session.id"))
    speaker_id = Column(Integer, ForeignKey("user.id"))
    text = Column(String)
    start_time = Column(Integer)  # milliseconds
    end_time = Column(Integer)
    confidence = Column(Float)  # ASR confidence
    created_at = Column(DateTime, default=datetime.utcnow)
```

---

### 5. Week 2: Analytics Engine (Core Metrics)

**Priority Order:**
1. **Turn-taking** (2 days)
   - Calculate from transcript timestamps
   - Detect interruptions
   - Speaker switching latency

```python
# app/analytics/turn_taking.py
def analyze_turns(transcripts: List[Transcript]):
    """
    Returns:
    - speaking_time: {user_id: total_seconds}
    - turn_count: {user_id: num_turns}
    - interruptions: {user_id: {successful: count, unsuccessful: count}}
    - avg_turn_duration: {user_id: seconds}
    """
    speakers = {}
    for t in transcripts:
        if t.speaker_id not in speakers:
            speakers[t.speaker_id] = []
        speakers[t.speaker_id].append((t.start_time, t.end_time, t.text))
    
    # Detect overlaps (simultaneous speech)
    overlaps = []
    for i, (user1_turns) in enumerate(speakers.items()):
        for j, (user2_turns) in enumerate(speakers.items()):
            if i >= j: continue
            # Check if any turns overlap
    
    return {
        'speaking_time': speaking_time,
        'interruptions': interruptions,
        # ...
    }
```

2. **Topic Alignment** (2 days)
   - Embed session topic
   - Score each participant turn
   - Flag off-topic statements

```python
# app/analytics/topic_analysis.py
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')

def analyze_topic_alignment(session_topic: str, transcripts: List[Transcript]):
    topic_embedding = model.encode(session_topic)
    
    results = {}
    for t in transcripts:
        turn_embedding = model.encode(t.text)
        similarity = cosine_similarity(topic_embedding, turn_embedding)
        results[t.speaker_id].append({
            'turn': t.text,
            'alignment_score': similarity * 100,
            'on_topic': similarity > 0.5  # Threshold
        })
    
    return results
```

3. **Speech Quality** (3 days)
   - Filler words
   - Vocabulary analysis
   - Pause patterns

```python
# app/analytics/speech_quality.py
import nltk
from collections import Counter

FILLER_WORDS = {'uh', 'um', 'like', 'you know', 'basically', 'honestly'}

def analyze_speech_quality(transcripts: List[Transcript]):
    for user_id, turns in group_by_user(transcripts):
        text = ' '.join([t.text for t in turns])
        tokens = nltk.word_tokenize(text.lower())
        
        filler_count = sum(1 for t in tokens if t in FILLER_WORDS)
        unique_words = len(set(tokens))
        avg_word_length = sum(len(w) for w in tokens) / len(tokens)
        
        yield {
            'user_id': user_id,
            'filler_word_percentage': (filler_count / len(tokens)) * 100,
            'vocabulary_richness': unique_words,
            'avg_word_length': avg_word_length,
            'confidence_score': 100 - filler_percentage,  # Inverse
        }
```

---

### 6. Frontend Dashboard (Week 3)

**Host Dashboard:**
```tsx
// components/HostDashboard.tsx
// Real-time display of:
// - Participant cards (name, speaking time, engagement %)
// - Live transcript with speaker labels
// - Topic alignment indicator
// - Interruption count per person
// - Group dynamics heatmap (who's talking)

export function HostDashboard({ sessionId }) {
  const [analytics, setAnalytics] = useState(null);
  
  useEffect(() => {
    const interval = setInterval(() => {
      fetch(`/api/sessions/${sessionId}/live-analytics`)
        .then(r => r.json())
        .then(setAnalytics);
    }, 2000);  // Refresh every 2 seconds
    
    return () => clearInterval(interval);
  }, [sessionId]);
  
  return (
    <div className="grid grid-cols-4 gap-4">
      {analytics?.participants.map(p => (
        <ParticipantCard key={p.id} participant={p} />
      ))}
    </div>
  );
}
```

**Personal Analytics:**
```tsx
// pages/dashboard/page.tsx
// Show individual performance after session ends
// Metrics:
// - Speaking time (vs group avg)
// - Interruption count
// - Topic alignment %
// - Speech quality score (vocabulary, filler words, pace)
// - Engagement level (from video CV)
// - Comparison radar chart
```

---

## DEPLOYMENT CHECKLIST

### Environment Variables (`.env`)
```
# Backend
DEEPGRAM_API_KEY=xxxx
OPENAI_API_KEY=xxxx
BREVO_API_KEY=xxxx
JWT_SECRET=super_secret_key
DATABASE_URL=postgresql://user:password@localhost:5432/gd_analytics
REDIS_URL=redis://localhost:6379/0
MONGODB_URL=mongodb://localhost:27017/gd_analytics

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WEBSOCKET_URL=ws://localhost:8000
```

### Docker Compose (Local Dev)
```yaml
version: '3.8'
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: gd_analytics
      POSTGRES_PASSWORD: devpass
    ports:
      - "5432:5432"
  
  redis:
    image: redis:7
    ports:
      - "6379:6379"
  
  mongo:
    image: mongo:6
    ports:
      - "27017:27017"
  
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
      - mongo
    environment:
      - DATABASE_URL=postgresql://postgres:devpass@postgres:5432/gd_analytics
```

---

## QUICK WIN: Minimum Viable First Feature (Day 1)

Get this working first to build momentum:

1. **Login page** (Next.js form → FastAPI endpoint)
2. **Create session page** (name, topic, max participants)
3. **Join session page** (Peerjs video grid)
4. **Basic transcript viewer** (scroll through live text)

This gives you:
- Auth working ✓
- WebRTC working ✓
- ASR integration working ✓
- Database connected ✓

From here, analytics are additive.

---

## RESEARCH TO DO (Parallel)

1. **Deepgram diarization accuracy** — Test on sample GD recordings
2. **Sentence-transformers performance** — How good is "all-MiniLM-L6-v2" for topic alignment?
3. **Eye contact detection** — MediaPipe face mesh + head pose estimation (MediaPipe tutorial)
4. **Load testing** — How many concurrent WebRTC connections before server bottleneck?

---

## COST ESTIMATION (Annual for 500 active users, 2 sessions/user/month)

| Service | Monthly | Annual |
|---------|---------|--------|
| Deepgram (60k min @ $0.01/min) | $600 | $7,200 |
| OpenAI Embeddings (120M tokens) | $240 | $2,880 |
| Brevo (email verification) | Free | - |
| AWS S3 (video storage, 100GB) | $50 | $600 |
| Backend Hosting (2x EC2 t3.medium) | $100 | $1,200 |
| Frontend (Vercel) | $25 | $300 |
| Database (RDS Postgres) | $50 | $600 |
| **Total** | **$1,065** | **$12,780** |
| **Per Session** | ~$1.06 | - |

**Optimization:** Self-host Whisper + Pyannote → save $600/month on Deepgram

---

## QUESTIONS FOR YOU

Before you start coding, clarify:

1. **Recording consent:** Do you want users to opt-in before session starts? (For GDPR)
2. **Data retention:** How long to keep recordings after session? (Delete after 30 days? Archive?)
3. **Analytics granularity:** Real-time (every 5 sec) vs batch (post-session)? → Impacts architecture
4. **Scaling target:** 10 users? 1000 users? → Affects tech choices
5. **Brevo setup:** Do you have a Brevo account + API key ready?

---

**Next Action:** Choose your tech stack, then start with AUTH module.
Estimated start-to-first-working-feature: **3-4 days**
