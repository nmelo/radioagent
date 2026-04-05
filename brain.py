"""Agent Radio brain: webhook server and announcement pipeline."""

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

logger = logging.getLogger(__name__)

WAV_DIR = Path("/tmp/agent-radio")


# --- Data models ---


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
                         config: RadioConfig) -> bool:
    """Full announcement pipeline: TTS -> validate -> push."""
    text = announcement.text
    wav_path = WAV_DIR / f"announce_{uuid.uuid4().hex[:12]}.wav"

    # Render TTS
    t0 = time.time()
    if not tts.render(text, wav_path):
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
        lambda a: process_announcement(a, tts, config)
    )
    drain_task = None
    announcement_history: deque[dict] = deque(maxlen=20)
    sse_clients: list[asyncio.Queue] = []
    music_muted = False

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

    @app.get("/now-playing")
    def now_playing():
        data = get_now_playing_from_icecast(config.icecast_host, config.icecast_port, config.icecast_mount)
        data["muted"] = music_muted
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
        result = query_liquidsoap(config.liquidsoap_socket, "var.set music_volume = 1.0")
        if result is None:
            return JSONResponse(status_code=503, content={"status": "error", "message": "Liquidsoap unavailable"})
        music_muted = False
        logger.info("Music unmuted")
        _broadcast_sse(sse_clients, "mute", {"muted": False})
        return {"status": "ok", "muted": False}

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
        # Run through script generator: clean, truncate, suppress
        event = WebhookEvent(detail=req.detail, kind=req.kind, agent=req.agent)
        script = generate_script(
            event,
            suppress_kinds=config.suppress_kinds,
            max_words=config.max_announcement_words,
        )
        if script is None:
            logger.info("Suppressed [%s] from %s", req.kind, req.agent or "unknown")
            return {"status": "suppressed"}

        # Record in history and broadcast to SSE clients
        record = {
            "text": script,
            "agent": req.agent,
            "kind": req.kind,
            "timestamp": datetime.now().isoformat(),
        }
        announcement_history.appendleft(record)
        _broadcast_sse(sse_clients, "announcement", record)

        entry = QueuedAnnouncement(
            text=script,
            kind=req.kind,
            agent=req.agent,
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

    config = load_config(Path(__file__).parent / "config.yaml")
    tts = KokoroEngine(voice=config.tts_voice, speed=config.tts_speed)
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
