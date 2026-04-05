# Agent Radio Roadmap

## 1. Phases

### Phase 0: Discovery and Design

**Goal:** All four project documents written. The team knows what to build, why, how, and in what order.

**Work:**
1. PM writes docs/prd.md (problem, users, success criteria, journeys)
2. Super orchestrates spec discovery (define behaviors, constraints)
3. Arch writes docs/systemdesign.md (architecture, modules, build order)
4. Super writes docs/roadmap.md (this document)

**Success gate:** Operator reviews all four documents. Team can start Phase 1 without asking clarifying questions.

### Phase 1: MVP (Complete)

**Goal:** Continuously playing ambient music stream with voice announcements, web dashboard, and operator controls. Shipped.

**What shipped:**
- `config.py` with RadioConfig dataclass and YAML validation
- `radio.liq` with playlist, voice queue, smooth_add ducking, crossfade, blank detection
- `tts/kokoro_engine.py` wrapping Kokoro 82M (TTSEngine protocol)
- `script_generator.py` with event kind templates, text cleaning, suppression
- `brain.py` with FastAPI webhook server, rate limiter, announcement pipeline, Liquidsoap socket push, SSE events, dashboard API endpoints
- `dashboard.html` with now-playing, wire feed (typing animation), skip, mute/unmute, volume, auto-reconnect
- `start.sh` / `stop.sh` with health checks, PID management, process monitoring
- Deployed to workbench (192.168.1.100) via git clone to /opt/agent-radio

**Build approach:** Tracer bullet (thin end-to-end slice first), then widened each component.

**Success gate (passed):**
- `./start.sh` launches all three processes, logs URLs
- Dashboard at `http://192.168.1.100:8001/` shows now-playing and plays stream
- `curl -X POST http://192.168.1.100:8001/announce -d '{"detail":"test"}'` produces audible voice
- Music ducks during announcements, restores after
- Mute/unmute, skip, volume all functional
- QA integration pass completed

### Phase 2: Hardening and Features (Current)

**Goal:** Production reliability, dashboard polish, audio normalization, and AI music generation.

**Work:**
1. Three-layer dashboard controls (connect/disconnect, music play/pause, announcement voice on/off)
2. Announcement mute/unmute endpoints (voice off, text-only wire feed)
3. Audio normalization pipeline (rsgain for music, ffmpeg loudnorm for TTS; see docs/audio-tools.md)
4. AI music generation via MusicGen (tested, config-gated behind `music_ai_enabled`)
5. Dashboard auto-reconnect reliability fix
6. `initech announce` CLI integration (feature request submitted)

**Success gate:**
- Three-layer controls working: connect/disconnect, independent music and voice toggles
- TTS normalized to match music loudness (no more hardcoded amplify(3.5))
- AI music generation toggleable via config
- 24-hour soak test passes

### Phase 3: Polish and Reliability

**Goal:** Production-quality radio station that runs unattended.

**Work:**
1. Announcement priority levels (critical events bypass rate limiter)
2. Health endpoint (`GET /health` with process status, uptime, announcement count)
3. Systemd units for brain and Liquidsoap
4. TTS engine evaluation: benchmark Orpheus 3B, Fish Speech on RTX 5080 (see docs/tts-evaluation.md)
5. A/B listening test if a candidate beats Kokoro on quality within latency budget
6. 24-hour unattended soak test with monitoring

**Success gate:**
- Systemd auto-restarts after simulated crash
- Health endpoint reports accurate status
- Soak test passes with no manual intervention

### Phase 4: DJ Personality and Scheduled Programming

**Goal:** Transform robotic announcements into a creative radio experience. Time-of-day programming.

**Work:**
1. DJ skill for Claude Code agents (creative, contextual, fun announcements)
2. Schedule config (time slots with music moods: morning=gentle, afternoon=upbeat, night=ambient)
3. Station IDs and time-of-day greetings
4. MusicGen prompt rotation per time slot
5. Curated playlist tagging by mood

### Phase 5: Full Radio Station (Long-term Vision)

**Goal:** A complete AI radio station with programmed shows, DJ personalities, and station identity. Inspired by writ-fm.

**Work:**
1. Show scheduling (named shows with hosts, segments, bumpers)
2. DJ personality system (multiple personalities, context-aware banter)
3. Station identity (jingles, sweepers, IDs, imaging)
4. Listener interaction (request system, dedications, callouts)
5. Cross-show continuity (DJs reference each other, running jokes, callbacks)
6. Public streaming option (beyond LAN)

## 2. Milestone Summary

| Phase | Milestone | Status | Key Deliverable |
|-------|-----------|--------|----------------|
| 0 | Design complete | Done | Four docs reviewed and approved |
| 1 | MVP streaming | Done | Music + TTS + dashboard + controls via Icecast |
| 2 | Hardening | Current | Three-layer controls, audio normalization, AI music |
| 3 | Production ready | Future | Systemd, health checks, TTS evaluation, soak test |
| 4 | DJ personality | Future | Creative announcements, scheduled programming |
| 5 | Full radio station | Vision | Shows, DJs, station identity, listener interaction |

## 3. Agent Allocation

| Agent | Primary Responsibility |
|-------|----------------------|
| eng1 | Python code: brain, config, TTS engine, script generator, dashboard API |
| eng2 | Liquidsoap config, start/stop scripts, systemd units, dashboard frontend |
| pm | PRD, spec review, bead grooming, research (TTS eval, audio tools) |
| qa1 | Integration tests, soak tests, client compatibility |
| shipper | Deployment to workbench, README, git ops |

## 4. Risk Gates

| Before Phase | Verify |
|-------------|--------|
| Phase 2 start | Phase 1 MVP QA passed. Dashboard functional. Stream stable for 4+ hours. (Passed.) |
| Phase 3 start | Audio normalization pipeline working. Three-layer controls shipped. AI music toggleable. |
| Phase 4 start | Phase 3 soak test passes. Health endpoint operational. Systemd auto-restart verified. |

## 5. Tech Stack Summary

| Component | Technology | Why |
|-----------|-----------|-----|
| Language | Python 3.12+ (venv managed) | ML models are Python-native |
| Webhook server | FastAPI + uvicorn | Async, fast, CORS, SSE support |
| TTS (default) | Kokoro 82M | 50ms/clip on RTX 5080, CPU-capable, 54 voices |
| TTS (candidates) | Orpheus 3B, Fish Speech | See docs/tts-evaluation.md |
| Music (curated) | MP3/FLAC/WAV files | Operator-provided, ~/Music on workbench |
| Music (AI) | MusicGen-medium 1.5B | Tested, config-gated, post-MVP |
| Audio engine | Liquidsoap 2.2.4 | Radio automation, smooth_add ducking, crossfade |
| Streaming | Icecast 2 | HTTP MP3 streaming, status JSON API |
| Audio encoding | MP3 192kbps via Liquidsoap | Broadest client compatibility |
| Dashboard | Single HTML file (vanilla JS) | No build tools, served by brain |
| Audio tools | sox, ffmpeg, rsgain, bs1770gain | See docs/audio-tools.md |
| Deployment | Ubuntu on workbench (192.168.1.100) | RTX 5080, git clone to /opt/agent-radio |
