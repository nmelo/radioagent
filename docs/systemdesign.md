# Agent Radio System Design

## 1. Module Structure

```
agent-radio/                    # Flat structure at repo root
  brain.py                    # Main entry point: FastAPI server + orchestration
  config.py                   # YAML config loading with RadioConfig dataclass
  script_generator.py         # Event JSON -> natural-language announcement text
  dashboard.html              # Single-file web UI (served by brain at GET /)
  tts/
    __init__.py               # TTSEngine Protocol definition
    kokoro_engine.py          # Kokoro 82M TTS implementation
  music/
    __init__.py               # Placeholder (AI generation is post-MVP)
  radio.liq                   # Liquidsoap configuration
  config.yaml                 # Runtime configuration
  config.yaml.example         # Config template
  start.sh                    # Starts Icecast + Liquidsoap + brain with health checks
  stop.sh                     # Graceful shutdown in reverse order
  pyproject.toml              # Python 3.12+ dependencies
  README.md                   # Quick start guide
  config/
    icecast.xml               # Icecast server configuration
  tests/                      # pytest test suite
  docs/                       # PRD, spec, system design, roadmap, evaluations
```

**Note:** Music files live at `/home/nmelo/Music` on workbench (hardcoded in radio.liq). The `config.yaml` `music_dir` field is used by config validation but Liquidsoap reads its own path directly.

**Deployment:** Clone repo to `/opt/agent-radio` on workbench. Create venv, install dependencies. Icecast managed by systemd. Liquidsoap and brain managed by start.sh/stop.sh.

**Dependency graph:**
```
brain.py
  +-- config.py (reads config.yaml)
  +-- script_generator.py (event -> text)
  +-- tts/ (text -> WAV file)
  +-- dashboard.html (served at GET /)
  +-- Liquidsoap (WAV file -> audio stream, via Unix socket)
  +-- Icecast (MP3 stream -> HTTP listeners, queried for now-playing metadata)
```

The brain communicates with Liquidsoap via Unix socket protocol (text commands) for voice push, skip, metadata queries, and music volume control. The brain queries Icecast's `/status-json.xsl` endpoint for current track metadata (title, artist from ICY tags).

## 2. Data Structures

### 2.1 Config

```python
@dataclass
class RadioConfig:
    # Music
    music_dir: Path
    music_ai_enabled: bool = False
    music_ai_prompt: str = "calm ambient music, soft pads, gentle drone"

    # TTS
    tts_engine: str = "kokoro"          # "kokoro" or "orpheus"
    tts_voice: str = "am_michael"       # Production uses "af_heart"
    tts_speed: float = 1.0

    # Webhook
    webhook_port: int = 8001
    webhook_rate_limit: int = 10        # seconds between announcements

    # Liquidsoap
    liquidsoap_socket: Path = Path("/tmp/agent-radio.sock")

    # Icecast
    icecast_host: str = "localhost"
    icecast_port: int = 8000
    icecast_mount: str = "/stream"
    icecast_password: str = "changeme"   # Production uses "agent-radio-src"

    # Announcements
    suppress_kinds: list[str] = field(default_factory=lambda: ["*.idle", "*.message"])
    max_announcement_words: int = 40
```

### 2.2 Webhook Event

```python
@dataclass
class WebhookEvent:
    detail: str                         # Required: what happened
    kind: str = "custom"                # Event type (dot notation)
    agent: str = ""                     # Agent name
    bead_id: str = ""                   # Task ID
    timestamp: str = ""                 # ISO 8601
    project: str = ""                   # Project name
```

### 2.3 Announcement Queue Entry

```python
@dataclass
class QueuedAnnouncement:
    text: str                           # Script to speak
    kind: str                           # For logging
    agent: str                          # For logging
    received_at: datetime               # For latency tracking
```

### 2.4 TTS Interface

```python
class TTSEngine(Protocol):
    def render(self, text: str, output_path: Path) -> bool:
        """Generate speech audio. Returns True on success."""
        ...

    @property
    def name(self) -> str:
        """Engine name for logging."""
        ...
```

## 3. Core Algorithms

### 3.1 Script Generation

The script generator maps event JSON to natural-language text. It uses a template system, not an LLM (LLM generation would add seconds of latency and an API dependency).

```python
def generate_script(event: WebhookEvent, max_words: int = 40) -> str | None:
    """Returns announcement text, or None if the event should be suppressed."""

    # Check suppression rules (glob matching on kind)
    if is_suppressed(event.kind):
        return None

    # Truncate detail to max_words
    detail = truncate_words(clean_text(event.detail), max_words)

    # Select template based on kind
    if event.kind.endswith(".completed"):
        if event.agent:
            return f"{event.agent} finished: {detail}"
        return f"Completed: {detail}"

    if event.kind.endswith(".failed"):
        if event.agent:
            return f"Heads up. {event.agent} hit a failure: {detail}"
        return f"Failure: {detail}"

    if event.kind.endswith(".stuck"):
        if event.agent:
            return f"{event.agent} appears stuck. {detail}"
        return f"Something is stuck. {detail}"

    # Default: use detail verbatim
    if event.agent:
        return f"{event.agent}: {detail}"
    return detail
```

`clean_text()` strips markdown formatting, code fences, URLs, and commit hashes longer than 8 characters. The goal is text that sounds natural when spoken aloud.

### 3.2 Rate Limiting

Token bucket with 1 token per `rate_limit` seconds. Events that arrive when the bucket is empty go into a FIFO queue (max 10). A background task drains the queue at the rate limit interval.

```python
class AnnouncementRateLimiter:
    def __init__(self, interval_seconds: int, max_queue: int = 10):
        self.interval = interval_seconds
        self.last_announcement = 0.0
        self.queue: deque[QueuedAnnouncement] = deque(maxlen=max_queue)

    def submit(self, announcement: QueuedAnnouncement) -> bool:
        """Submit for announcement. Returns True if queued, False if dropped."""
        now = time.time()
        if now - self.last_announcement >= self.interval:
            self.last_announcement = now
            return self._process(announcement)
        if len(self.queue) < self.queue.maxlen:
            self.queue.append(announcement)
            return True
        return False  # Queue full, drop

    async def drain_loop(self):
        """Background task that processes queued announcements."""
        while True:
            await asyncio.sleep(self.interval)
            if self.queue:
                announcement = self.queue.popleft()
                self._process(announcement)
```

### 3.3 Liquidsoap Communication

Push voice clips via Unix socket using Liquidsoap's telnet protocol:

```python
import socket

def push_to_liquidsoap(socket_path: Path, wav_path: Path) -> bool:
    """Push a WAV file to Liquidsoap's voice request queue."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(str(socket_path))
        command = f"voice.push {wav_path}\n"
        sock.sendall(command.encode())
        response = sock.recv(1024).decode()
        sock.close()
        return "OK" in response or "queued" in response.lower()
    except (socket.error, socket.timeout) as e:
        logger.warning(f"Liquidsoap push failed: {e}")
        return False
```

### 3.4 WAV Validation

Before pushing to Liquidsoap, validate the TTS output:

```python
import wave

def validate_wav(path: Path) -> bool:
    """Check that a WAV file is valid for announcement."""
    try:
        with wave.open(str(path), 'r') as w:
            duration = w.getnframes() / w.getframerate()
            if duration < 0.5:
                logger.warning(f"WAV too short: {duration:.1f}s")
                return False
            if duration > 30.0:
                logger.warning(f"WAV too long: {duration:.1f}s")
                return False
        return True
    except wave.Error:
        return False
```

## 4. Command Implementations

### 4.1 brain.py (main entry point)

```python
async def main():
    config = load_config("config.yaml")
    tts = create_tts_engine(config)
    rate_limiter = AnnouncementRateLimiter(config.webhook_rate_limit)

    # Start background tasks
    asyncio.create_task(rate_limiter.drain_loop())
    if config.music_ai_enabled:
        asyncio.create_task(music_ai_loop(config))

    # Start FastAPI webhook server
    app = create_webhook_app(config, tts, rate_limiter)
    uvicorn_config = uvicorn.Config(app, host="0.0.0.0", port=config.webhook_port)
    server = uvicorn.Server(uvicorn_config)
    await server.serve()
```

### 4.2 Announcement Pipeline (per event)

```python
async def handle_announcement(event: WebhookEvent, tts: TTSEngine,
                                config: RadioConfig) -> bool:
    # 1. Generate script
    text = generate_script(event, config.max_announcement_words)
    if text is None:
        return False  # Suppressed

    # 2. Generate TTS
    wav_path = Path(f"/tmp/agent-radio/announce_{next_id()}.wav")
    if not tts.render(text, wav_path):
        logger.warning(f"TTS failed for: {text}")
        return False

    # 3. Validate WAV
    if not validate_wav(wav_path):
        wav_path.unlink(missing_ok=True)
        return False

    # 4. Push to Liquidsoap
    ok = push_to_liquidsoap(config.liquidsoap_socket, wav_path)
    if ok:
        logger.info(f"Announced: [{event.kind}] {text}")
    else:
        wav_path.unlink(missing_ok=True)
    return ok
```

### 4.3 start.sh

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

echo "[agent-radio] Starting Icecast..."
sudo systemctl start icecast2

echo "[agent-radio] Starting Liquidsoap..."
liquidsoap radio.liq &
LIQUIDSOAP_PID=$!

sleep 2  # Wait for Liquidsoap socket

echo "[agent-radio] Starting brain..."
uv run python brain.py &
BRAIN_PID=$!

echo "[agent-radio] Streaming at http://$(hostname -I | awk '{print $1}'):8000/stream"
echo "[agent-radio] Webhook endpoint at http://$(hostname -I | awk '{print $1}'):8001/announce"

# Wait for either process to exit
wait -n $LIQUIDSOAP_PID $BRAIN_PID
kill $LIQUIDSOAP_PID $BRAIN_PID 2>/dev/null
```

## 5. Testing Strategy

### 5.1 Unit Tests

| Module | What to test |
|--------|-------------|
| `script_generator.py` | Template selection by event kind, text cleaning, truncation, suppression |
| `config.py` | YAML loading, default values, validation (missing music_dir, invalid port) |
| `tts/kokoro_engine.py` | Mock the Kokoro model, verify render produces a WAV file |
| Rate limiter | Submit within rate, submit over rate, queue overflow, drain loop |

### 5.2 Integration Tests

| Test | What it verifies |
|------|-----------------|
| WAV validation | Real WAV files: valid, too short, too long, corrupt |
| Liquidsoap socket push | Mock Unix socket, verify command format |
| Webhook endpoint | POST valid JSON, POST missing fields, rate limiting responses |
| Full pipeline (no audio) | Event -> script -> mock TTS -> mock Liquidsoap push |

### 5.3 Manual Tests

| Test | Procedure |
|------|-----------|
| End-to-end audio | Start everything, POST an event, listen for announcement |
| Transition quality | POST 10 rapid events, listen for smooth ducking on each |
| 24-hour soak | Leave running overnight, verify stream alive in the morning |
| Client compatibility | Connect from Chrome, Safari, VLC, iPhone |

## 6. Build Order

### Phase 1: Curated Music + TTS Announcements (MVP)

Build order (sequential, each depends on the previous):

1. **Config and project scaffold** - config.yaml, pyproject.toml, config.py
2. **Liquidsoap config** - radio.liq with playlist + voice queue + smooth_add + Icecast output
3. **TTS engine (Kokoro)** - kokoro_engine.py wrapping Kokoro's render API
4. **Script generator** - Event templates, text cleaning, kind suppression
5. **Webhook server** - FastAPI endpoint, rate limiter, announcement pipeline
6. **Start/stop scripts** - Orchestrate all three processes
7. **Integration testing** - End-to-end: POST event, hear announcement

### Phase 2: AI Music Generation

1. **MusicGen wrapper** - Load model, generate clips, save to playlist directory
2. **Background generation loop** - Stay N clips ahead, handle GPU failures
3. **Fallback logic** - Detect GPU unavailable, switch to curated

### Phase 3: Polish

1. **Stream metadata** - Update Icecast now-playing via admin API
2. **Announcement priority** - Critical events bypass rate limiter
3. **Multiple TTS voices** - Different voices for different event kinds
4. **Monitoring** - Health endpoint, uptime metrics, announcement count
