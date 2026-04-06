"""TTS engine interface."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSEngine(Protocol):
    """Protocol for text-to-speech engines."""

    def render(self, text: str, output_path: Path, voice: str | None = None) -> bool:
        """Generate speech audio from text.

        Args:
            voice: Override voice for this render. None uses engine default.

        Returns True on success, False on failure. Must not raise.
        """
        ...

    @property
    def name(self) -> str:
        """Engine name for logging."""
        ...
