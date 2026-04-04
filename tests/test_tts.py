"""Tests for TTS engine protocol and KokoroEngine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tts import TTSEngine
from tts.kokoro_engine import KokoroEngine, SAMPLE_RATE


class FakeEngine:
    """Minimal TTSEngine implementation for protocol testing."""

    def render(self, text: str, output_path: Path) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("fake audio")
        return True

    @property
    def name(self) -> str:
        return "fake"


def test_fake_engine_satisfies_protocol():
    engine = FakeEngine()
    assert isinstance(engine, TTSEngine)


def test_fake_engine_render(tmp_path):
    engine = FakeEngine()
    out = tmp_path / "test.wav"
    assert engine.render("hello", out) is True
    assert out.exists()


def test_kokoro_engine_satisfies_protocol():
    with patch("tts.kokoro_engine.KokoroEngine._load"):
        engine = KokoroEngine.__new__(KokoroEngine)
        engine._voice = "am_michael"
        engine._speed = 1.0
        engine._pipeline = None
    assert isinstance(engine, TTSEngine)


def test_kokoro_name():
    with patch("tts.kokoro_engine.KokoroEngine._load"):
        engine = KokoroEngine.__new__(KokoroEngine)
        engine._voice = "am_michael"
        engine._speed = 1.0
        engine._pipeline = None
    assert engine.name == "kokoro"


def test_empty_string_returns_false(tmp_path):
    with patch("tts.kokoro_engine.KokoroEngine._load"):
        engine = KokoroEngine.__new__(KokoroEngine)
        engine._voice = "am_michael"
        engine._speed = 1.0
        engine._pipeline = MagicMock()
    assert engine.render("", tmp_path / "out.wav") is False
    assert engine.render("   ", tmp_path / "out2.wav") is False


def test_pipeline_not_loaded_returns_false(tmp_path):
    with patch("tts.kokoro_engine.KokoroEngine._load"):
        engine = KokoroEngine.__new__(KokoroEngine)
        engine._voice = "am_michael"
        engine._speed = 1.0
        engine._pipeline = None
    assert engine.render("hello world", tmp_path / "out.wav") is False


def test_render_creates_output_directory(tmp_path):
    """Verify render creates parent dirs if they don't exist."""
    import numpy as np

    mock_pipeline = MagicMock()
    audio_chunk = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1s silence
    mock_pipeline.return_value = iter([("g", "p", audio_chunk)])

    with patch("tts.kokoro_engine.KokoroEngine._load"):
        engine = KokoroEngine.__new__(KokoroEngine)
        engine._voice = "am_michael"
        engine._speed = 1.0
        engine._pipeline = mock_pipeline

    nested = tmp_path / "deep" / "nested" / "dir" / "out.wav"
    result = engine.render("test text", nested)
    assert result is True
    assert nested.exists()


def test_render_with_mock_pipeline(tmp_path):
    """Verify render writes a valid WAV with mocked Kokoro output."""
    import numpy as np

    mock_pipeline = MagicMock()
    audio_chunk = np.random.randn(SAMPLE_RATE * 2).astype(np.float32)  # 2s
    mock_pipeline.return_value = iter([("g", "p", audio_chunk)])

    with patch("tts.kokoro_engine.KokoroEngine._load"):
        engine = KokoroEngine.__new__(KokoroEngine)
        engine._voice = "am_michael"
        engine._speed = 1.0
        engine._pipeline = mock_pipeline

    out = tmp_path / "test.wav"
    assert engine.render("hello world", out) is True
    assert out.exists()

    import soundfile as sf
    data, sr = sf.read(str(out))
    assert sr == SAMPLE_RATE
    assert len(data) / sr > 0.5
