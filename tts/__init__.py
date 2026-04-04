"""TTS engine interface."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSEngine(Protocol):
    """Protocol for text-to-speech engines."""

    def render(self, text: str, output_path: Path) -> bool:
        """Generate speech audio from text.

        Returns True on success, False on failure. Must not raise.
        """
        ...

    @property
    def name(self) -> str:
        """Engine name for logging."""
        ...
