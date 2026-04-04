"""Agent Radio brain: webhook server and announcement pipeline."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import time
import wave
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from config import RadioConfig, load_config
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

    def set_processor(self, fn):
        self._process_fn = fn

    def submit(self, announcement: QueuedAnnouncement) -> str:
        """Submit an announcement. Returns 'immediate', 'queued', or 'dropped'."""
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
            now = time.time()
            if now - self.last_announcement >= self.interval:
                announcement = self.queue.popleft()
                self.last_announcement = now
                if self._process_fn:
                    self._process_fn(announcement)

    def drain_remaining(self):
        """Synchronously process all remaining queued announcements."""
        while self.queue:
            announcement = self.queue.popleft()
            if self._process_fn:
                self._process_fn(announcement)


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


# --- Announcement pipeline ---

_counter = 0


def process_announcement(announcement: QueuedAnnouncement, tts: TTSEngine,
                         config: RadioConfig) -> bool:
    """Full announcement pipeline: TTS -> validate -> push."""
    global _counter
    _counter += 1

    text = announcement.text
    wav_path = WAV_DIR / f"announce_{_counter:04d}.wav"

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
        duration = wav_path.stat().st_size  # rough size for log
        logger.info(
            "Announced [%s] %s: '%s' (rendered in %.3fs)",
            announcement.kind, announcement.agent, text[:60], elapsed,
        )
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
        logger.info("Brain stopped")

    app = FastAPI(lifespan=lifespan)

    @app.post("/announce")
    def announce(req: AnnounceRequest):
        entry = QueuedAnnouncement(
            text=req.detail,
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
    loop = asyncio.new_event_loop()

    def handle_signal(sig, frame):
        logger.info("Received %s, initiating shutdown...", signal.Signals(sig).name)
        loop.call_soon_threadsafe(server.should_exit.__bool__)
        server.should_exit = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()
