"""Kokoro TTS engine: 82M parameter model, 24kHz output."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000


class KokoroEngine:
    """Wraps Kokoro KPipeline behind the TTSEngine protocol."""

    def __init__(self, voice: str = "am_michael", speed: float = 1.0):
        self._voice = voice
        self._speed = speed
        self._pipeline = None
        self._load()

    def _load(self) -> None:
        try:
            from kokoro import KPipeline

            logger.info("Loading Kokoro TTS model...")
            t0 = time.time()
            self._pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
            # Warm up: first synthesis triggers model load + CUDA kernel JIT
            for _ in self._pipeline("warm up", voice=self._voice):
                pass
            logger.info("Kokoro ready in %.1fs", time.time() - t0)
        except ImportError:
            logger.error("Kokoro not installed. pip install kokoro>=0.9")
        except Exception:
            logger.exception("Failed to load Kokoro model")

    def render(self, text: str, output_path: Path) -> bool:
        """Render text to a WAV file. Returns True on success."""
        if not text or not text.strip():
            logger.warning("Empty text, skipping TTS render")
            return False

        if self._pipeline is None:
            logger.error("Kokoro pipeline not loaded, cannot render")
            return False

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            chunks = []
            for _, _, audio in self._pipeline(
                text, voice=self._voice, speed=self._speed
            ):
                chunks.append(audio)

            if not chunks:
                logger.warning("Kokoro produced no audio for: %s", text[:80])
                return False

            combined = np.concatenate(chunks)
            duration = len(combined) / SAMPLE_RATE

            if duration < 0.1:
                logger.warning("Audio too short (%.2fs), skipping", duration)
                return False

            sf.write(str(output_path), combined, SAMPLE_RATE)
            logger.info(
                "Rendered %.1fs audio to %s", duration, output_path.name
            )
            return True
        except Exception:
            logger.exception("TTS render failed for: %s", text[:80])
            return False

    @property
    def name(self) -> str:
        return "kokoro"
