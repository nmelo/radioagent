# Agent Radio Spec

## 1. Core Model

Agent Radio is a continuously playing audio stream that combines ambient music with voice announcements. External systems send events via HTTP webhook. Each event is translated to a natural-language sentence, rendered as speech by a TTS engine, and crossfaded into the music stream. Listeners connect via any HTTP audio client.

The system has three processes:
- **Brain** (Python/FastAPI): receives events, generates scripts, renders TTS, pushes voice clips to the audio engine, serves the web dashboard and API
- **Audio Engine** (Liquidsoap): plays music, ducks volume for voice announcements, encodes MP3, streams to Icecast
- **Icecast**: HTTP streaming server, serves MP3 to listeners

The brain is the intelligence. Liquidsoap is the audio pipeline. They communicate via Unix socket (brain pushes WAV files to Liquidsoap's request queue). The brain also serves a web dashboard at `GET /` with real-time updates via Server-Sent Events.

## 2. Components

### 2.1 Webhook Server

HTTP server (FastAPI) listening on a configurable port (default 8001).

**Endpoint:** `POST /announce`

**Request body (JSON):**
```json
{
  "kind": "agent.completed",
  "agent": "eng1",
  "bead_id": "ini-abc.1",
  "detail": "Finished the auth refactor. Commit abc123.",
  "timestamp": "2026-04-04T12:00:00-04:00",
  "project": "initech"
}
```

Required field: `detail` (the text to announce). All others optional.

**Response:** `200 OK` with `{"status": "queued"}` or `429 Too Many Requests` if rate limited.

**Rate limiting:** Max 1 announcement per 10 seconds. If events arrive faster, they queue (max 10). Queue overflow drops oldest events. This prevents a webhook flood from creating a 5-minute announcement backlog.

### 2.2 Script Generator

Translates JSON event payloads into natural-language sentences suitable for TTS.

**Rules:**
- If `agent` and `kind` are present, generate a contextual sentence: "eng1 finished the auth refactor"
- If only `detail` is present, use it verbatim (truncated to 200 chars)
- Strip markdown, code blocks, commit hashes longer than 8 chars, and URLs
- Keep announcements under 15 seconds of speech (~40 words)
- Prepend agent name if present: "eng1: ..." (helps listener identify who)

**Event kind templates:**

| Kind pattern | Template |
|-------------|----------|
| `*.completed` | "{agent} finished: {detail_summary}" |
| `*.failed` | "Heads up. {agent} hit a failure: {detail_summary}" |
| `*.stuck` | "{agent} appears stuck: {detail_summary}" |
| `*.started` | "{agent} started working" |
| `*.stopped` | "{agent} stopped" |
| `*.idle` | (suppressed, too noisy) |
| default | "{detail_summary}" |

Suppressed events produce no announcement. The `*.idle` kind is suppressed by default since agents go idle frequently and the announcements would be constant noise.

### 2.3 TTS Engine

Generates speech audio from text. Pluggable interface with two implementations:

**Kokoro (default, shipped):**
- 82M parameter model, runs on CPU or GPU
- 200x+ real-time on GPU, 3-5x on CPU
- 54 built-in voices, configurable via `tts_voice` in config.yaml (default: `af_heart`)
- Output: WAV file (24kHz, 16-bit, mono). Liquidsoap resamples to 44.1kHz for streaming.
- 10-second announcement generates in ~50ms on RTX 5080
- Voice clips are amplified 3.5x in Liquidsoap to match music loudness

**Orpheus (optional, future, see docs/tts-evaluation.md):**
- 3B parameter model, requires GPU with FP8 quantization (~9GB VRAM)
- 8 English voices + emotion tags (laugh, sigh, etc.) + zero-shot voice cloning
- 10-second announcement generates in 5-10 seconds (100x slower than Kokoro)
- Not prioritized for Phase 2 due to latency; revisit in Phase 3

**Interface:**
```
render(text: str, output_path: Path, voice: str) -> bool
```

Returns True on success, False on failure. The brain handles failures by logging and skipping the announcement.

### 2.4 Music Source

Provides continuous ambient music to Liquidsoap.

**Curated library (always available):**
- Operator places ambient music files (MP3/FLAC/WAV) in a configured directory
- Liquidsoap plays them in random order with crossfading between tracks
- This is the primary music source for MVP

**AI-generated (post-MVP):**
- MusicGen-medium (1.5B params, ~5GB VRAM) generates 30-60 second ambient clips
- Background thread stays 2-3 clips ahead of playback
- Generated clips are dropped into the playlist directory as WAV files
- Liquidsoap picks them up via `reload_mode="watch"`
- Falls back to curated library when GPU is unavailable

**Music prompts (for AI generation):**
- "calm ambient music, soft synthesizer pads, gentle drone, deep reverb, 60 bpm"
- "lo-fi ambient, warm analog synth, tape hiss, relaxing, dreamy"
- Prompts are configurable in config.yaml

### 2.5 Audio Engine (Liquidsoap)

Handles all audio mixing, ducking, encoding, and streaming.

**Liquidsoap configuration (~30 lines):**
```
# Music source: playlist with crossfade
music = playlist(config.music_dir, mode="random", reload_mode="watch")
music = crossfade(music)

# Voice announcements: request queue fed by brain via socket
voice = request.queue(id="voice")

# Automatic ducking: music ducks when voice is active
radio = smooth_add(normal=music, special=voice)

# Output to Icecast
output.icecast(%mp3(bitrate=128),
  host="localhost", port=8000,
  password=config.icecast_password,
  mount="/stream",
  radio)
```

`smooth_add` handles the ducking automatically: when the voice source becomes active, the music volume is reduced; when voice finishes, music volume restores. The transition curves are Liquidsoap's built-in cosine fades.

**Liquidsoap socket server:** Listens on a Unix socket. The brain pushes voice clips via:
```
echo 'voice.push /tmp/announce_001.wav' | socat - UNIX-CONNECT:/path/to/radio.sock
```

### 2.6 Icecast Server

Standard Icecast2 installation serving MP3 streams on port 8000. Configuration is a standard `icecast.xml` with source password, stream mount point, and listener limits. No custom code needed.

Listeners connect via `http://<host>:8000/stream`. Supports MP3 with ICY metadata for now-playing information.

### 2.7 Web Dashboard

Single HTML file (`dashboard.html`) served by the brain at `GET /`. Inline CSS and vanilla JS, no build tools, no external dependencies except Google Fonts (DM Mono, DM Sans, Instrument Serif).

**Features:**
- Now Playing: current track title/artist/album from Icecast metadata, Skip button
- Up Next: next track in the shuffle queue
- Wire Feed: live announcement text with character-by-character typing animation, blinking cursor, age-faded opacity, failure event red accents
- Player Controls: play/pause (server-side music mute), volume slider (client-side), mute toggle
- ON AIR badge: pulses red during active announcements
- Auto-reconnect: exponential backoff (2s to 30s) on stream failure

**Data sources:**
- `GET /now-playing` polled every 3 seconds (fallback)
- `GET /events` SSE stream for real-time announcement and mute events
- `GET /recent-announcements` loaded once on page open

### 2.8 Brain API Endpoints

The brain exposes the following HTTP endpoints (FastAPI on port 8001):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serve dashboard.html |
| `GET` | `/now-playing` | Current track metadata from Icecast + next track + mute state |
| `GET` | `/recent-announcements` | Last 20 announcements (text, agent, kind, timestamp) |
| `GET` | `/events` | SSE stream: announcement, now-playing, and mute events |
| `POST` | `/announce` | Submit announcement event (detail required, kind/agent/project optional) |
| `POST` | `/skip` | Skip current music track via Liquidsoap |
| `POST` | `/mute` | Mute music (Liquidsoap volume=0.0), broadcast SSE |
| `POST` | `/unmute` | Unmute music (Liquidsoap volume=1.0), broadcast SSE |

All endpoints return JSON. CORS is enabled for all origins (LAN use).

## 3. Behaviors

### 3.1 Startup

1. `./scripts/start.sh` checks for stale PID files and port conflicts
2. Starts Icecast via systemd (or adopts existing instance)
3. Starts Liquidsoap, waits up to 15 seconds for Unix socket
4. Starts brain (`venv/bin/python -m brain`), waits up to 30 seconds for port 8001
5. Music begins playing immediately from curated library
6. Brain logs stream URL, webhook endpoint, and dashboard URL
7. Monitoring loop checks process health every 5 seconds

### 3.2 Announcement Flow

1. Webhook POST arrives at `:8001/announce`
2. Brain validates JSON, extracts fields
3. Rate limiter checks: if < 10 seconds since last announcement, queue the event
4. Script generator produces natural-language text from event
5. TTS engine renders text to WAV file in `/tmp/agent-radio/`
6. Brain validates WAV (exists, > 0.5 seconds, < 30 seconds, RMS above silence threshold)
7. Brain pushes WAV path to Liquidsoap voice queue via Unix socket
8. Liquidsoap ducks music, plays voice, restores music
9. Brain logs the announcement (event kind, agent, text, duration)
10. Temporary WAV file is deleted after playback (Liquidsoap reads it once)

### 3.3 Failure Recovery

| Failure | Behavior |
|---------|----------|
| TTS generation fails | Log warning, skip this announcement, continue playing music |
| TTS produces silence | Detected by RMS check (step 6), skipped |
| Liquidsoap socket gone | Brain retries connection every 5 seconds, queues events meanwhile |
| Icecast restarts | Liquidsoap auto-reconnects, listeners must reconnect (standard Icecast behavior) |
| Brain crashes | Music continues (Liquidsoap is independent), no new announcements until brain restarts |
| Webhook floods | Rate limiter queues up to 10 events, drops oldest on overflow |

### 3.4 Shutdown

1. Brain receives SIGINT/SIGTERM
2. Brain stops accepting webhooks
3. Brain drains the announcement queue (processes remaining events)
4. Brain disconnects from Liquidsoap socket
5. Liquidsoap continues playing music (independent process, operator stops separately)

## 4. Data Model

### 4.1 Configuration (config.yaml)

```yaml
# Music
music_dir: /path/to/ambient/music      # Directory of MP3/FLAC/WAV files
music_ai_enabled: false                 # Enable MusicGen AI generation (post-MVP)
music_ai_prompt: "calm ambient music, soft pads, gentle drone, deep reverb"

# TTS
tts_engine: kokoro                      # kokoro or orpheus
tts_voice: am_michael                   # Voice name (engine-specific)
tts_speed: 1.0                          # Speech speed multiplier

# Webhook server
webhook_port: 8001
webhook_rate_limit: 10                  # Seconds between announcements

# Liquidsoap
liquidsoap_socket: /tmp/agent-radio.sock

# Icecast
icecast_host: localhost
icecast_port: 8000
icecast_mount: /stream
icecast_password: changeme

# Announcements
suppress_kinds:                         # Event kinds to silently ignore
  - "*.idle"
  - "*.message"
max_announcement_words: 40              # Truncate longer announcements
```

### 4.2 Announcement Queue

In-memory FIFO. Max 10 entries. Each entry:
```python
@dataclass
class QueuedAnnouncement:
    text: str               # Natural-language script
    kind: str               # Event kind (for logging)
    agent: str              # Agent name (for logging)
    received_at: datetime   # When the webhook arrived
```

### 4.3 Temporary Files

TTS output WAVs written to `/tmp/agent-radio/announce_NNNN.wav`. Sequentially numbered. Deleted after Liquidsoap plays them. The directory is created on startup and cleaned on shutdown.

## 5. Constraints

1. **Liquidsoap is the audio authority.** The brain never touches audio samples directly. All audio mixing, ducking, and encoding happens in Liquidsoap. The brain's only audio interaction is pushing file paths to Liquidsoap's request queue.

2. **Announcements are fire-and-forget.** The brain does not confirm that Liquidsoap played a clip. It pushes the path and moves on. If Liquidsoap fails to play it, the announcement is silently lost.

3. **Music never stops.** If the voice queue is empty, music plays at full volume. If TTS fails, music plays at full volume. The only way music stops is if Liquidsoap crashes or runs out of files.

4. **One announcement at a time.** The voice request queue is serial. If two announcements arrive simultaneously, the second waits for the first to finish. No overlapping voices.

5. **No state persistence.** The brain is stateless across restarts. No database, no replay of missed events, no announcement history beyond the current log output.

6. **LAN only.** Icecast binds to `0.0.0.0` but is not designed for public internet exposure. No authentication, no TLS, no CDN.
