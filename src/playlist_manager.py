"""Smart playlist manager with recency-penalized weighted shuffle.

Scans a music directory for audio files, selects the next track using
a weighted shuffle that penalizes recently played tracks, and persists
play history across restarts.
"""

from __future__ import annotations

import json
import logging
import math
import random
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav"}
STATE_PATH = Path("/tmp/agent-radio/playlist_state.json")
HISTORY_MAX = 200
RESCAN_INTERVAL = 30  # seconds


class PlaylistManager:
    """Manages track selection with recency-penalized weighted shuffle."""

    def __init__(self, music_dir: Path):
        self._music_dir = music_dir
        self._tracks: list[Path] = []
        self._history: list[str] = []  # filenames (not full paths)
        self._lock = threading.Lock()
        self._rescan_timer: threading.Timer | None = None

        self._load_state()
        self.scan()
        self._start_rescan_timer()

    def scan(self) -> None:
        """Scan the music directory for audio files."""
        if not self._music_dir.exists():
            logger.warning("Music directory does not exist: %s", self._music_dir)
            return

        found = sorted(
            p for p in self._music_dir.iterdir()
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        )

        with self._lock:
            old_names = {t.name for t in self._tracks}
            new_names = {t.name for t in found}
            added = new_names - old_names
            removed = old_names - new_names

            self._tracks = found

            # Clean history entries referencing files no longer in the pool
            before = len(self._history)
            self._history = [h for h in self._history if h in new_names]
            cleaned = before - len(self._history)
            if cleaned:
                logger.info("Cleaned %d stale entries from play history", cleaned)

            if added:
                logger.info("Added %d new tracks to pool", len(added))

            logger.debug("Playlist: %d tracks in pool", len(self._tracks))

    @property
    def track_count(self) -> int:
        with self._lock:
            return len(self._tracks)

    @property
    def cooldown_window(self) -> int:
        """Cooldown window scales with library size: 60% of library, minimum 3."""
        return max(math.floor(len(self._tracks) * 0.6), 3)

    def next_track(self) -> Path | None:
        """Select the next track using weighted shuffle with recency penalty."""
        with self._lock:
            if not self._tracks:
                logger.warning("No tracks available")
                return None

            weights = self._compute_weights()

            # Edge case: all weights below threshold -> reset
            if all(w < 0.1 for w in weights):
                logger.info("All weights below threshold, resetting history")
                self._history.clear()
                weights = [1.0] * len(self._tracks)

            # Weighted random selection
            selected = random.choices(self._tracks, weights=weights, k=1)[0]

            # Record in history
            self._history.append(selected.name)
            if len(self._history) > HISTORY_MAX:
                self._history = self._history[-HISTORY_MAX:]

            self._save_state()

            logger.info("Selected: %s (pool=%d, cooldown=%d)",
                        selected.name, len(self._tracks), self.cooldown_window)
            return selected

    def _compute_weights(self) -> list[float]:
        """Compute selection weights for all tracks based on recency."""
        cooldown = self.cooldown_window
        weights = []
        for track in self._tracks:
            name = track.name
            plays_since = self._plays_since_last(name)
            if plays_since is None:
                # Never played -> full weight
                weights.append(1.0)
            else:
                penalty = max(0.0, 1.0 - (plays_since / cooldown))
                weights.append(max(0.0, 1.0 - penalty))
        return weights

    def _plays_since_last(self, filename: str) -> int | None:
        """Return how many tracks have been played since this one, or None if never played."""
        try:
            # Search from end of history (most recent first)
            idx = len(self._history) - 1 - self._history[::-1].index(filename)
            return len(self._history) - 1 - idx
        except ValueError:
            return None

    def _save_state(self) -> None:
        """Persist play history to disk."""
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps({
                "history": self._history,
                "history_max": HISTORY_MAX,
            }))
        except OSError as e:
            logger.warning("Failed to save playlist state: %s", e)

    def _load_state(self) -> None:
        """Restore play history from disk."""
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text())
                self._history = data.get("history", [])[:HISTORY_MAX]
                logger.info("Restored playlist state: %d history entries", len(self._history))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load playlist state: %s", e)
            self._history = []

    def _start_rescan_timer(self) -> None:
        """Schedule periodic directory rescan."""
        self._rescan_timer = threading.Timer(RESCAN_INTERVAL, self._rescan_tick)
        self._rescan_timer.daemon = True
        self._rescan_timer.start()

    def _rescan_tick(self) -> None:
        """Rescan and reschedule."""
        try:
            self.scan()
        except Exception:
            logger.exception("Rescan failed")
        self._start_rescan_timer()

    def stop(self) -> None:
        """Cancel the rescan timer."""
        if self._rescan_timer:
            self._rescan_timer.cancel()
            self._rescan_timer = None
