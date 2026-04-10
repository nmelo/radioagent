"""Radio Agent brain: webhook server and announcement pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import socket
import threading
import time
import uuid
import wave
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from config import RadioConfig, load_config
from script_generator import WebhookEvent, generate_script
from tts import TTSEngine
from tts.kokoro_engine import KokoroEngine

from fnmatch import fnmatch

# src/brain.py -> repo root is two levels up
PROJECT_ROOT = Path(__file__).parent.parent

logger = logging.getLogger(__name__)

WAV_DIR = Path("/tmp/agent-radio")


# --- Tone routing ---

# Event kind glob -> tone WAV filename (without extension)
_TONE_MAP = {
    "*.started": "rise",
    "*.completed": "resolve",
    "*.failed": "dissonant",
    "*.stuck": "pulse",
    "*.idle": "hum",
    "*.stopped": "descend",
    "deploy.*": "bell",
    "milestone.*": "chord_long",
}

# Event kinds that play a tone but skip voice (too frequent for TTS)
_TONE_ONLY_KINDS = {"*.started", "*.stopped", "*.idle"}


def get_tone_for_kind(kind: str) -> str | None:
    """Return tone WAV name for an event kind, or None if no mapping."""
    for pattern, tone_name in _TONE_MAP.items():
        if fnmatch(kind, pattern):
            return tone_name
    return None


def is_tone_only(kind: str) -> bool:
    """Return True if this event kind plays tone but skips voice."""
    return any(fnmatch(kind, p) for p in _TONE_ONLY_KINDS)


# Event kinds that use the failure voice (am_michael) instead of default
_FAILURE_VOICE_KINDS = {"*.failed", "*.stuck"}
FAILURE_VOICE = "am_michael"


def get_voice_for_kind(kind: str, default_voice: str) -> str:
    """Return the TTS voice to use for an event kind."""
    if any(fnmatch(kind, p) for p in _FAILURE_VOICE_KINDS):
        return FAILURE_VOICE
    return default_voice


# --- Data models ---


# Level -> Liquidsoap interactive.float value (multiplied by channel's fixed base amp)
VOICE_LEVELS = {1: 0.3, 2: 0.5, 3: 0.7, 4: 0.85, 5: 1.0}
DEFAULT_VOICE_LEVEL = 4

MUSIC_LEVELS = {1: 0.15, 2: 0.3, 3: 0.5, 4: 0.7, 5: 1.0}
DEFAULT_MUSIC_LEVEL = 2

TONES_LEVELS = {1: 0.3, 2: 0.5, 3: 0.7, 4: 0.85, 5: 1.0}
DEFAULT_TONES_LEVEL = 3


class ChannelLevelRequest(BaseModel):
    level: int

    @field_validator("level")
    @classmethod
    def level_in_range(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError("level must be between 1 and 5")
        return v


# Backwards compat alias
VoiceLevelRequest = ChannelLevelRequest


class AnnounceRequest(BaseModel):
    detail: str
    kind: str = "custom"
    agent: str = ""
    bead_id: str = ""
    timestamp: str = ""
    project: str = ""

    @field_validator("detail")
    @classmethod
    def detail_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("detail must not be empty")
        return v


@dataclass
class QueuedAnnouncement:
    text: str
    kind: str
    agent: str
    voice: str | None = None
    received_at: datetime = field(default_factory=datetime.now)


# --- Rate limiter ---


class AnnouncementRateLimiter:
    """Token bucket rate limiter with FIFO queue for announcements."""

    def __init__(self, interval_seconds: int, max_queue: int = 10):
        self.interval = interval_seconds
        self.max_queue = max_queue
        self.last_announcement: float = 0.0
        self.queue: deque[QueuedAnnouncement] = deque(maxlen=max_queue)
        self._process_fn = None
        self._lock = threading.Lock()

    def set_processor(self, fn):
        self._process_fn = fn

    def submit(self, announcement: QueuedAnnouncement) -> str:
        """Submit an announcement. Returns 'immediate', 'queued', or 'dropped'."""
        with self._lock:
            now = time.time()
            if now - self.last_announcement >= self.interval:
                self.last_announcement = now
                if self._process_fn:
                    self._process_fn(announcement)
                return "immediate"
            if len(self.queue) < self.max_queue:
                self.queue.append(announcement)
                return "queued"
            return "dropped"

    async def drain_loop(self):
        """Background task that processes queued announcements at the rate limit."""
        while True:
            await asyncio.sleep(1)
            if not self.queue:
                continue
            with self._lock:
                now = time.time()
                if now - self.last_announcement >= self.interval:
                    announcement = self.queue.popleft()
                    self.last_announcement = now
                else:
                    continue
            if self._process_fn:
                self._process_fn(announcement)

    def drain_remaining(self, max_iterations: int = 10):
        """Process remaining queued announcements, capped to avoid blocking."""
        count = 0
        while self.queue and count < max_iterations:
            announcement = self.queue.popleft()
            if self._process_fn:
                self._process_fn(announcement)
            count += 1
        if self.queue:
            logger.warning("Shutdown drain capped at %d, dropping %d remaining",
                           max_iterations, len(self.queue))


# --- WAV validation ---


def validate_wav(path: Path, min_duration: float = 0.5, max_duration: float = 30.0,
                 min_rms: float = 0.001) -> bool:
    """Validate a WAV file: duration bounds and non-silence check."""
    try:
        with wave.open(str(path), "r") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            duration = frames / rate

            if duration < min_duration:
                logger.warning("WAV too short: %.2fs < %.1fs", duration, min_duration)
                return False
            if duration > max_duration:
                logger.warning("WAV too long: %.2fs > %.1fs", duration, max_duration)
                return False

            # RMS silence check
            raw = w.readframes(frames)
            if sampwidth == 2:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            else:
                samples = np.frombuffer(raw, dtype=np.float32)

            if channels > 1:
                samples = samples[::channels]  # Take first channel

            rms = float(np.sqrt(np.mean(samples ** 2)))
            if rms < min_rms:
                logger.warning("WAV is silent: RMS %.6f < %.4f", rms, min_rms)
                return False

        return True
    except (wave.Error, FileNotFoundError, EOFError) as e:
        logger.warning("Invalid WAV file %s: %s", path, e)
        return False


# --- Liquidsoap socket communication ---


def push_to_liquidsoap(socket_path: Path, wav_path: Path, retries: int = 3) -> bool:
    """Push a WAV file to Liquidsoap's voice queue via Unix socket."""
    for attempt in range(1, retries + 1):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect(str(socket_path))
            s.sendall(f"voice.push {wav_path}\r\n".encode())
            resp = b""
            while b"END" not in resp:
                chunk = s.recv(1024)
                if not chunk:
                    break
                resp += chunk
            s.close()
            logger.debug("Liquidsoap response: %s", resp.decode().strip())
            return True
        except (socket.error, socket.timeout) as e:
            logger.warning("Liquidsoap push attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(1)
    return False


def push_tone_to_liquidsoap(socket_path: Path, wav_path: Path) -> bool:
    """Push a tone WAV to Liquidsoap's tones queue. Single attempt, no retries."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(str(socket_path))
        s.sendall(f"tones.push {wav_path}\r\n".encode())
        resp = b""
        while b"END" not in resp:
            chunk = s.recv(1024)
            if not chunk:
                break
            resp += chunk
        s.close()
        return True
    except (socket.error, socket.timeout) as e:
        logger.warning("Tone push failed: %s", e)
        return False


# --- Liquidsoap metadata query ---

_METADATA_RE = re.compile(r'^(\w+)="(.*)"$')


def query_liquidsoap(socket_path: Path, command: str) -> str | None:
    """Send a command to Liquidsoap and return the response body (before END)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(str(socket_path))
        s.sendall(f"{command}\r\n".encode())
        resp = b""
        while b"END" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        s.close()
        return resp.decode().replace("END", "").strip()
    except (socket.error, socket.timeout):
        return None


def _parse_liquidsoap_metadata(raw: str) -> dict[str, str]:
    """Parse Liquidsoap metadata response into a dict."""
    meta = {}
    for line in raw.splitlines():
        m = _METADATA_RE.match(line.strip())
        if m:
            meta[m.group(1)] = m.group(2)
    return meta


def get_next_track(socket_path: Path) -> dict | None:
    """Query Liquidsoap for the next prefetched track's metadata.

    Supports both Liquidsoap 2.2 (request.on_air + request.alive) and
    2.3+ (request.all) by trying the 2.2 API first and falling back.
    """
    # Try Liquidsoap 2.2: request.on_air + request.alive
    on_air = query_liquidsoap(socket_path, "request.on_air")
    if on_air and "ERROR" not in on_air.upper():
        alive = query_liquidsoap(socket_path, "request.alive")
        if not alive:
            return None
        on_air_rids = set(on_air.strip().split())
        alive_rids = alive.strip().split()
        next_rids = [rid for rid in alive_rids if rid not in on_air_rids]
    else:
        # Liquidsoap 2.3+: request.on_air removed, use request.all
        all_rids_raw = query_liquidsoap(socket_path, "request.all")
        if not all_rids_raw or "ERROR" in all_rids_raw.upper():
            return None
        rids = all_rids_raw.strip().split()
        # First RID is the on-air track, rest are prefetched
        next_rids = rids[1:] if len(rids) >= 2 else []

    if not next_rids:
        return None

    raw = query_liquidsoap(socket_path, f"request.metadata {next_rids[0]}")
    if not raw:
        return None

    meta = _parse_liquidsoap_metadata(raw)
    title = meta.get("title", "")
    artist = meta.get("artist", "")
    album = meta.get("album", "")

    # Fallback to filename if no title tag
    if not title:
        filename = meta.get("filename", "")
        if filename:
            title = Path(filename).stem.replace("_", " ").replace("-", " ")

    if not title:
        return None

    return {"title": title, "artist": artist, "album": album}


def get_now_playing_from_icecast(host: str, port: int, mount: str) -> dict:
    """Query Icecast status JSON for the track metadata listeners actually hear.

    Icecast reflects metadata at the point audio is encoded and sent to clients,
    so it stays in sync with what's audible (unlike Liquidsoap's request.on_air
    which can be one track ahead during crossfade).
    """
    import urllib.request

    fallback = {"title": "Connecting...", "artist": "", "album": "", "source_type": "unknown"}

    try:
        url = f"http://{host}:{port}/status-json.xsl"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return fallback

    source = data.get("icestats", {}).get("source")
    if not source:
        return fallback

    # Icecast may return a list of sources or a single dict
    if isinstance(source, list):
        source = next((s for s in source if s.get("listenurl", "").endswith(mount)), None)
        if not source:
            return fallback

    icy_title = source.get("title", "")
    if not icy_title:
        return fallback

    # Liquidsoap sends metadata as "Artist - Title" in the icy stream.
    # Split on the first " - " to separate artist from title.
    artist = ""
    title = icy_title
    if " - " in icy_title:
        artist, title = icy_title.split(" - ", 1)

    return {
        "title": title,
        "artist": artist,
        "album": "",
        "source_type": "music",
    }


# --- SSE broadcast ---


def _broadcast_sse(clients: list[asyncio.Queue], event_type: str, data: dict) -> None:
    """Push an SSE event to all connected clients. Safe to call from any thread."""
    for q in list(clients):
        try:
            q.put_nowait({"event": event_type, "data": data})
        except asyncio.QueueFull:
            pass


# --- WAV cleanup ---

_CLEANUP_DELAY_SECONDS = 60


def _schedule_wav_cleanup(path: Path) -> None:
    """Delete a WAV file after a delay, giving Liquidsoap time to read it."""
    def _delete():
        path.unlink(missing_ok=True)
        logger.debug("Cleaned up %s", path.name)
    threading.Timer(_CLEANUP_DELAY_SECONDS, _delete).start()


# --- Announcement pipeline ---


def process_announcement(announcement: QueuedAnnouncement, tts: TTSEngine,
                         config: RadioConfig, voice: str | None = None) -> bool:
    """Full announcement pipeline: TTS -> validate -> push."""
    text = announcement.text
    wav_path = WAV_DIR / f"announce_{uuid.uuid4().hex[:12]}.wav"

    # Render TTS
    t0 = time.time()
    if not tts.render(text, wav_path, voice=voice):
        logger.warning("TTS render failed for: %s", text[:80])
        return False
    elapsed = time.time() - t0

    # Validate WAV
    if not validate_wav(wav_path):
        wav_path.unlink(missing_ok=True)
        return False

    # Push to Liquidsoap
    ok = push_to_liquidsoap(config.liquidsoap_socket, wav_path)
    if ok:
        logger.info(
            "Announced [%s] %s: '%s' (rendered in %.3fs)",
            announcement.kind, announcement.agent, text[:60], elapsed,
        )
        _schedule_wav_cleanup(wav_path)
    else:
        logger.warning("Liquidsoap push failed, cleaning up %s", wav_path.name)
        wav_path.unlink(missing_ok=True)

    return ok


# --- App factory ---


def create_app(config: RadioConfig, tts: TTSEngine) -> FastAPI:
    rate_limiter = AnnouncementRateLimiter(
        config.webhook_rate_limit, max_queue=10
    )
    rate_limiter.set_processor(
        lambda a: process_announcement(a, tts, config, voice=a.voice)
    )
    drain_task = None
    announcement_history: deque[dict] = deque(maxlen=20)
    sse_clients: list[asyncio.Queue] = []
    music_muted = False
    announcements_muted = False
    tones_muted = False
    voice_level = DEFAULT_VOICE_LEVEL
    music_level = DEFAULT_MUSIC_LEVEL
    tones_level = DEFAULT_TONES_LEVEL

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal drain_task
        WAV_DIR.mkdir(exist_ok=True)
        drain_task = asyncio.create_task(rate_limiter.drain_loop())
        logger.info("Brain started on port %d", config.webhook_port)
        logger.info("Stream: http://localhost:%d%s", config.icecast_port, config.icecast_mount)
        yield
        # Shutdown: drain remaining announcements
        logger.info("Shutting down, draining %d queued announcements...", len(rate_limiter.queue))
        drain_task.cancel()
        rate_limiter.drain_remaining()
        # Close SSE clients
        for q in sse_clients:
            await q.put(None)
        sse_clients.clear()
        logger.info("Brain stopped")

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    def dashboard():
        html_path = Path(__file__).parent / "dashboard.html"
        if html_path.exists():
            return FileResponse(html_path, media_type="text/html")
        return JSONResponse(status_code=404, content={"error": "dashboard.html not found"})

    @app.get("/favicon.ico")
    def favicon():
        ico_path = PROJECT_ROOT / "website" / "favicon.ico"
        if ico_path.exists():
            return FileResponse(ico_path, media_type="image/x-icon")
        return JSONResponse(status_code=404, content={"error": "favicon.ico not found"})

    @app.get("/assets/apple-touch-icon.png")
    def apple_touch_icon():
        icon_path = PROJECT_ROOT / "website" / "assets" / "apple-touch-icon.png"
        if icon_path.exists():
            return FileResponse(icon_path, media_type="image/png")
        return JSONResponse(status_code=404, content={"error": "apple-touch-icon.png not found"})

    @app.get("/skill/dj.skill")
    def download_dj_skill():
        skill_path = PROJECT_ROOT / "skills" / "dj.skill"
        if skill_path.exists():
            return FileResponse(skill_path, filename="dj.skill", media_type="application/octet-stream")
        return JSONResponse(status_code=404, content={"error": "dj.skill not found"})

    @app.get("/stream")
    def stream_proxy():
        """Reverse-proxy the Icecast audio stream so the dashboard works without a separate port."""
        import urllib.request
        icecast_url = f"http://{config.icecast_host}:{config.icecast_port}{config.icecast_mount}"
        try:
            upstream = urllib.request.urlopen(icecast_url)
        except Exception:
            return JSONResponse(status_code=502, content={"error": "Icecast stream unavailable"})

        def generate():
            try:
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(generate(), media_type="audio/mpeg")

    @app.get("/tones/{name}.wav")
    def serve_tone(name: str):
        if not all(c.isalnum() or c == '_' for c in name):
            return JSONResponse(status_code=400, content={"error": "invalid tone name"})
        tone_path = config.tones_dir / f"{name}.wav"
        if tone_path.exists():
            return FileResponse(tone_path, media_type="audio/wav")
        return JSONResponse(status_code=404, content={"error": "tone not found"})

    @app.get("/now-playing")
    def now_playing():
        data = get_now_playing_from_icecast(config.icecast_host, config.icecast_port, config.icecast_mount)
        data["muted"] = music_muted
        data["announcements_muted"] = announcements_muted
        data["tones_muted"] = tones_muted
        data["voice_level"] = voice_level
        data["music_level"] = music_level
        data["tones_level"] = tones_level
        data["next"] = get_next_track(config.liquidsoap_socket)
        data["music_dir"] = str(config.music_dir)
        return data

    @app.get("/recent-announcements")
    def recent_announcements():
        return list(announcement_history)

    @app.post("/skip")
    def skip_track():
        result = query_liquidsoap(config.liquidsoap_socket, "music.skip")
        if result is not None:
            return {"status": "skipped"}
        return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})

    @app.post("/mute")
    def mute_music():
        nonlocal music_muted
        result = query_liquidsoap(config.liquidsoap_socket, "var.set music_volume = 0.0")
        if result is None:
            return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})
        music_muted = True
        logger.info("Music muted")
        _broadcast_sse(sse_clients, "mute", {"muted": True})
        return {"status": "ok", "muted": True}

    @app.post("/unmute")
    def unmute_music():
        nonlocal music_muted
        vol = MUSIC_LEVELS[music_level]
        result = query_liquidsoap(config.liquidsoap_socket, f"var.set music_volume = {vol}")
        if result is None:
            return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})
        music_muted = False
        logger.info("Music unmuted (restored level %d, volume=%.2f)", music_level, vol)
        _broadcast_sse(sse_clients, "mute", {"muted": False})
        return {"status": "ok", "muted": False}

    @app.post("/mute-announcements")
    def mute_announcements():
        nonlocal announcements_muted
        announcements_muted = True
        logger.info("Announcements muted")
        _broadcast_sse(sse_clients, "announcements-mute", {"muted": True})
        return {"status": "ok", "muted": True}

    @app.post("/unmute-announcements")
    def unmute_announcements():
        nonlocal announcements_muted
        announcements_muted = False
        logger.info("Announcements unmuted")
        _broadcast_sse(sse_clients, "announcements-mute", {"muted": False})
        return {"status": "ok", "muted": False}

    @app.post("/mute-tones")
    def mute_tones():
        nonlocal tones_muted
        tones_muted = True
        logger.info("Tones muted")
        _broadcast_sse(sse_clients, "tones-mute", {"muted": True})
        return {"status": "ok", "muted": True}

    @app.post("/unmute-tones")
    def unmute_tones():
        nonlocal tones_muted
        tones_muted = False
        logger.info("Tones unmuted")
        _broadcast_sse(sse_clients, "tones-mute", {"muted": False})
        return {"status": "ok", "muted": False}

    @app.post("/voice-level")
    def set_voice_level(req: ChannelLevelRequest):
        nonlocal voice_level
        vol = VOICE_LEVELS[req.level]
        result = query_liquidsoap(config.liquidsoap_socket, f"var.set voice_volume = {vol}")
        if result is None:
            return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})
        voice_level = req.level
        logger.info("Voice level set to %d (voice_volume=%.2f)", req.level, vol)
        _broadcast_sse(sse_clients, "voice-level", {"level": req.level})
        return {"status": "ok", "level": req.level, "voice_volume": vol}

    @app.post("/music-level")
    def set_music_level(req: ChannelLevelRequest):
        nonlocal music_level
        vol = MUSIC_LEVELS[req.level]
        result = query_liquidsoap(config.liquidsoap_socket, f"var.set music_volume = {vol}")
        if result is None:
            return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})
        music_level = req.level
        logger.info("Music level set to %d (music_volume=%.2f)", req.level, vol)
        _broadcast_sse(sse_clients, "music-level", {"level": req.level})
        return {"status": "ok", "level": req.level, "music_volume": vol}

    @app.post("/tones-level")
    def set_tones_level(req: ChannelLevelRequest):
        nonlocal tones_level
        vol = TONES_LEVELS[req.level]
        result = query_liquidsoap(config.liquidsoap_socket, f"var.set tones_volume = {vol}")
        if result is None:
            return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})
        tones_level = req.level
        logger.info("Tones level set to %d (tones_volume=%.2f)", req.level, vol)
        _broadcast_sse(sse_clients, "tones-level", {"level": req.level})
        return {"status": "ok", "level": req.level, "tones_volume": vol}

    @app.get("/events")
    async def events(request: Request):
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        sse_clients.append(q)

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(q.get(), timeout=30)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if item is None:
                        break
                    yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
            finally:
                if q in sse_clients:
                    sse_clients.remove(q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/announce")
    def announce(req: AnnounceRequest):
        event = WebhookEvent(detail=req.detail, kind=req.kind, agent=req.agent,
                             project=req.project)

        # --- Tone routing (independent of voice) ---
        tone_name = get_tone_for_kind(req.kind)
        tone_played = False
        if tone_name and not tones_muted:
            tone_path = config.tones_dir / f"{tone_name}.wav"
            if tone_path.exists():
                tone_played = push_tone_to_liquidsoap(config.liquidsoap_socket, tone_path)
            else:
                logger.warning("Tone file missing: %s", tone_path)

        # --- Voice routing ---
        tone_only = is_tone_only(req.kind)

        script = generate_script(
            event,
            suppress_kinds=config.suppress_kinds,
            max_words=config.max_announcement_words,
        )

        # Events suppressed for voice AND with no tone mapping are fully suppressed
        if script is None and not tone_name:
            logger.info("Suppressed [%s] from %s", req.kind, req.agent or "unknown")
            return {"status": "suppressed"}

        # Record in history and broadcast to SSE (always, for any routed event)
        display_text = script or req.detail
        record = {
            "text": display_text,
            "agent": req.agent,
            "kind": req.kind,
            "timestamp": datetime.now().isoformat(),
        }
        if req.project:
            record["project"] = req.project
        if tone_name:
            record["tone"] = tone_name
        announcement_history.appendleft(record)
        _broadcast_sse(sse_clients, "announcement", record)

        # Tone-only events: no voice, we're done
        if tone_only or script is None:
            return {"status": "tone_only", "tone": tone_name} if tone_name else {"status": "suppressed"}

        # Voice muted: text recorded but no TTS
        if announcements_muted:
            logger.info("Announcement muted, text only: %s", display_text[:60])
            return {"status": "muted"}

        # Voice pipeline: TTS -> validate -> push
        # Priority: failure voice > project voice > default voice
        default_voice = config.get_project_voice(req.project) or config.tts_voice
        voice = get_voice_for_kind(req.kind, default_voice)
        entry = QueuedAnnouncement(
            text=script,
            kind=req.kind,
            agent=req.agent,
            voice=voice,
        )
        result = rate_limiter.submit(entry)

        if result == "dropped":
            return JSONResponse(
                status_code=429,
                content={"status": "dropped", "message": "Rate limited, queue full"},
            )

        return {"status": result}

    return app


# --- Entry point ---


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config(PROJECT_ROOT / "config.yaml")
    extra_voices = [FAILURE_VOICE] + config.collect_extra_voices()
    tts = KokoroEngine(voice=config.tts_voice, speed=config.tts_speed,
                       extra_voices=extra_voices)
    app = create_app(config, tts)

    uv_config = uvicorn.Config(
        app, host="0.0.0.0", port=config.webhook_port, log_level="info"
    )
    server = uvicorn.Server(uv_config)

    # Graceful shutdown on SIGINT/SIGTERM
    def handle_signal(sig, frame):
        logger.info("Received %s, initiating shutdown...", signal.Signals(sig).name)
        server.should_exit = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
