"""YAML config loading into RadioConfig dataclass with validation."""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from fnmatch import translate as glob_translate
from pathlib import Path
from re import compile as re_compile

import yaml

logger = logging.getLogger(__name__)


@dataclass
class RadioConfig:
    """Radio configuration loaded from config.yaml."""

    # Music
    music_dir: Path = field(default_factory=lambda: Path("/opt/agent-radio/music"))
    music_ai_enabled: bool = False
    music_ai_prompt: str = "calm ambient music, soft pads, gentle drone, deep reverb"

    # TTS
    tts_engine: str = "kokoro"
    tts_voice: str = "am_michael"
    tts_speed: float = 1.0

    # Webhook
    webhook_port: int = 8001
    webhook_rate_limit: int = 10

    # Liquidsoap
    liquidsoap_socket: Path = field(default_factory=lambda: Path("/tmp/agent-radio.sock"))

    # Icecast
    icecast_host: str = "localhost"
    icecast_port: int = 8000
    icecast_mount: str = "/stream"
    icecast_password: str = "changeme"

    # Tones
    tones_dir: Path = field(default_factory=lambda: Path("/opt/agent-radio/audio/tones"))

    # Announcements
    suppress_kinds: list[str] = field(default_factory=lambda: ["*.idle", "*.message"])
    max_announcement_words: int = 40

    # Per-project voice casting (project name -> Kokoro voice ID)
    project_voices: dict[str, str] = field(default_factory=dict)

    def get_project_voice(self, project: str) -> str | None:
        """Return the voice for a project, or None if no mapping exists."""
        if not project:
            return None
        return self.project_voices.get(project) or self.project_voices.get("_default")

    def collect_extra_voices(self) -> list[str]:
        """Return unique voices from project_voices for TTS warmup."""
        return list(set(self.project_voices.values()))


def _validate_port(value: int, name: str) -> None:
    if not (1 <= value <= 65535):
        raise ValueError(f"{name} must be between 1 and 65535, got {value}")


def _validate_suppress_kinds(kinds: list[str]) -> list[str]:
    """Return only kinds with valid glob syntax, warn on invalid ones."""
    valid = []
    for pattern in kinds:
        try:
            re_compile(glob_translate(pattern))
            valid.append(pattern)
        except Exception:
            logger.warning("Invalid glob pattern in suppress_kinds, skipping: %s", pattern)
    return valid


def load_config(path: str | Path = "config.yaml") -> RadioConfig:
    """Load and validate config from a YAML file.

    Raises FileNotFoundError if the file doesn't exist.
    Raises ValueError if validation fails (bad port).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    # Map YAML keys to dataclass fields, ignoring unknown keys
    known_fields = {f.name for f in RadioConfig.__dataclass_fields__.values()}
    kwargs = {}
    for key, value in raw.items():
        if key in known_fields:
            kwargs[key] = value

    # Convert Path fields from strings
    for path_field in ("music_dir", "liquidsoap_socket", "tones_dir"):
        if path_field in kwargs and isinstance(kwargs[path_field], str):
            kwargs[path_field] = Path(kwargs[path_field])

    config = RadioConfig(**kwargs)

    # Validate ports
    _validate_port(config.webhook_port, "webhook_port")
    _validate_port(config.icecast_port, "icecast_port")

    # Warn if music_dir is missing or empty (liquidsoap owns playback, not brain)
    if not config.music_dir.exists():
        logger.warning("music_dir does not exist: %s", config.music_dir.resolve())
    elif not any(config.music_dir.iterdir()):
        logger.warning("music_dir is empty: %s", config.music_dir)

    # Validate suppress_kinds globs
    config.suppress_kinds = _validate_suppress_kinds(config.suppress_kinds)

    return config
