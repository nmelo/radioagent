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

### Phase 1: Curated Music + TTS Announcements (MVP)

**Goal:** A continuously playing ambient music stream with voice announcements triggered by HTTP webhook. Curated music library (operator-provided files). Kokoro TTS for voice. Liquidsoap for mixing/streaming. Icecast for HTTP delivery.

**Packages to build:**
1. `config.py` - YAML config loading and validation
2. `radio.liq` - Liquidsoap config (playlist, voice queue, smooth_add, Icecast output)
3. `tts/kokoro_engine.py` - Kokoro TTS wrapper (text -> WAV)
4. `script_generator.py` - Event JSON -> natural-language text (templates, cleaning, suppression)
5. `brain.py` - FastAPI webhook server, rate limiter, announcement pipeline, Liquidsoap socket push
6. `start.sh` / `stop.sh` - Process orchestration

**Dependencies (sequential):**
```
config.py -> radio.liq -> kokoro_engine.py -> script_generator.py -> brain.py -> start.sh
```

Config must exist first. Liquidsoap config is needed to test TTS output. TTS and script generator are needed by the brain. Start script orchestrates everything.

**Success gate:**
- `./start.sh` launches all three processes without errors
- Open `http://<host>:8000/stream` in a browser, hear ambient music
- `curl -X POST http://<host>:8001/announce -d '{"detail":"test announcement"}'`
- Hear the voice announcement with smooth music ducking within 10 seconds
- Leave running for 4+ hours, verify stream still live

**Agent allocation:**
- eng1: config.py + brain.py (Python, core orchestration)
- eng2: radio.liq + start.sh (Liquidsoap config, process management)
- eng1 or eng2: tts/kokoro_engine.py + script_generator.py (depends on who finishes first)
- qa1: integration testing, soak testing

### Phase 2: AI Music Generation

**Goal:** MusicGen-medium generates ambient music clips on the GPU. Clips are dropped into the playlist directory for Liquidsoap to pick up. Falls back to curated library when GPU is unavailable.

**Packages to build:**
1. `music/ai_generator.py` - MusicGen wrapper (load model, generate clip, save WAV)
2. Background loop in `brain.py` - Stay N clips ahead, handle CUDA OOM gracefully
3. Config additions - `music_ai_enabled`, `music_ai_prompt`, generation interval

**Success gate:**
- Enable AI music in config, restart
- Hear AI-generated ambient music (different from curated library)
- Kill the GPU process (simulate OOM), verify seamless fallback to curated music
- GPU comes back, verify AI generation resumes

### Phase 3: Polish and Reliability

**Goal:** Production-quality radio station that runs unattended.

**Work:**
1. Stream metadata - Update Icecast now-playing via admin API after each track/announcement
2. Announcement priority - Critical events (failures, stuck) bypass rate limiter
3. Health endpoint - `GET /health` returns process status, uptime, announcement count
4. Systemd units - Service files for brain and Liquidsoap
5. Orpheus TTS support - Second TTS engine option with professional voice modes
6. Listening test - A/B Kokoro vs Orpheus, pick the default

**Success gate:**
- 24-hour unattended soak test passes
- Stream metadata shows in VLC/browser
- Health endpoint reports accurate status
- Systemd auto-restarts after simulated crash

### Phase 4: Scheduled Programming (Future)

**Goal:** Different music moods by time of day. Like writ-fm's show schedule but simpler.

**Work:**
1. Schedule config - Time slots with music moods (morning=gentle, afternoon=upbeat, night=ambient)
2. MusicGen prompt rotation - Different prompts per time slot
3. Curated playlist tagging - Music files tagged with mood, selected by schedule

### Phase 5: Web Dashboard (Future)

**Goal:** A simple web page showing stream status, recent announcements, and a built-in audio player.

**Work:**
1. Static HTML page served by the brain
2. Now-playing display, recent announcement log
3. Embedded audio player pointing at the Icecast stream
4. WebSocket for live updates

## 2. Milestone Summary

| Phase | Milestone | Key Deliverable |
|-------|-----------|----------------|
| 0 | Design complete | Four docs reviewed and approved |
| 1 | MVP streaming | Curated music + TTS announcements via Icecast |
| 2 | AI music | MusicGen ambient generation with GPU fallback |
| 3 | Production ready | Systemd, health checks, metadata, soak tested |
| 4 | Scheduled shows | Time-of-day music moods |
| 5 | Web dashboard | Status page with embedded player |

## 3. Agent Allocation

| Agent | Primary Responsibility |
|-------|----------------------|
| eng1 | Python code: brain, config, TTS engine, script generator |
| eng2 | Liquidsoap config, start/stop scripts, systemd units, MusicGen wrapper |
| pm | PRD, spec review, user journey validation |
| qa1 | Integration tests, soak tests, client compatibility |
| shipper | Release packaging, README, install docs |

## 4. Risk Gates

| Before Phase | Verify |
|-------------|--------|
| Phase 1 start | Liquidsoap installed and serves a test stream. Kokoro installed and generates a test WAV. Icecast installed and accessible from the network. |
| Phase 2 start | RTX 5080 has CUDA working. MusicGen-medium loads and generates a 30-second clip. VRAM headroom confirmed (5GB model + 2-3GB Kokoro + system). |
| Phase 3 start | Phase 1 MVP passes 4-hour soak test. At least 3 listener clients tested. |

## 5. Tech Stack Summary

| Component | Technology | Why |
|-----------|-----------|-----|
| Language | Python 3.11+ (uv managed) | ML models are Python-native |
| Webhook server | FastAPI + uvicorn | Async, fast, minimal |
| TTS (default) | Kokoro 82M | 200x+ real-time, #1 TTS Arena, CPU-capable |
| TTS (future) | Orpheus 3B | Professional voice modes, emotion tags |
| Music (curated) | MP3/FLAC/WAV files | Operator-provided, always available |
| Music (AI) | MusicGen-medium 1.5B | 2-5x real-time on RTX 5080, continuation API |
| Audio engine | Liquidsoap 2.x | Purpose-built radio automation, smooth_add ducking |
| Streaming | Icecast 2 | Standard HTTP audio streaming, any client works |
| Audio encoding | MP3 via Liquidsoap (libmp3lame) | Broadest client compatibility |
| Deployment | Ubuntu on workbench (192.168.1.100) | RTX 5080 for GPU workloads |
