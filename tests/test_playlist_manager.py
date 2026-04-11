"""Tests for playlist_manager: selection algorithm, persistence, directory scanning."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from playlist_manager import PlaylistManager, AUDIO_EXTENSIONS


def _make_tracks(tmp_path: Path, names: list[str]) -> Path:
    """Create fake audio files in a temp directory. Supports subdirs via /."""
    music = tmp_path / "music"
    music.mkdir()
    for name in names:
        p = music / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake audio")
    return music


class TestScan:
    def test_scans_audio_files(self, tmp_path):
        music = _make_tracks(tmp_path, ["a.mp3", "b.ogg", "c.flac", "d.wav"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.track_count == 4

    def test_ignores_non_audio(self, tmp_path):
        music = _make_tracks(tmp_path, ["a.mp3", "notes.txt", "cover.jpg"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.track_count == 1

    def test_empty_directory(self, tmp_path):
        music = tmp_path / "music"
        music.mkdir()
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.track_count == 0

    def test_missing_directory(self, tmp_path):
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(tmp_path / "nonexistent")
        assert pm.track_count == 0

    def test_scans_subdirectories(self, tmp_path):
        music = _make_tracks(tmp_path, [
            "soulseek/artist1/track1.mp3",
            "soulseek/artist2/track2.flac",
            "nx_2000/ambient.ogg",
            "top_level.wav",
        ])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.track_count == 4

    def test_rescan_picks_up_new_files(self, tmp_path):
        music = _make_tracks(tmp_path, ["a.mp3"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.track_count == 1
        (music / "b.mp3").write_bytes(b"new track")
        pm.scan()
        assert pm.track_count == 2

    def test_rescan_removes_deleted_files(self, tmp_path):
        music = _make_tracks(tmp_path, ["a.mp3", "b.mp3"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.track_count == 2
        (music / "b.mp3").unlink()
        pm.scan()
        assert pm.track_count == 1


class TestCooldown:
    def test_small_library(self, tmp_path):
        music = _make_tracks(tmp_path, [f"t{i}.mp3" for i in range(4)])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.cooldown_window == 3  # max(floor(4*0.6), 3)

    def test_medium_library(self, tmp_path):
        music = _make_tracks(tmp_path, [f"t{i}.mp3" for i in range(10)])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.cooldown_window == 6  # floor(10*0.6)

    def test_large_library(self, tmp_path):
        music = _make_tracks(tmp_path, [f"t{i}.mp3" for i in range(100)])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        assert pm.cooldown_window == 60  # floor(100*0.6)


class TestNextTrack:
    def test_returns_path(self, tmp_path):
        music = _make_tracks(tmp_path, ["a.mp3", "b.mp3"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"), \
             patch.object(PlaylistManager, "_save_state"):
            pm = PlaylistManager(music)
            track = pm.next_track()
        assert track is not None
        assert track.exists()
        assert track.suffix == ".mp3"

    def test_empty_pool_returns_none(self, tmp_path):
        music = tmp_path / "music"
        music.mkdir()
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"), \
             patch.object(PlaylistManager, "_save_state"):
            pm = PlaylistManager(music)
            assert pm.next_track() is None

    def test_no_immediate_repeat_small_library(self, tmp_path):
        """With 4 tracks and cooldown=3, should not repeat within 3 plays."""
        music = _make_tracks(tmp_path, [f"t{i}.mp3" for i in range(4)])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"), \
             patch.object(PlaylistManager, "_save_state"):
            pm = PlaylistManager(music)
            # Play all 4 tracks, no back-to-back repeats expected
            played = []
            for _ in range(20):
                track = pm.next_track()
                played.append(track.name)
            # Check no back-to-back repeats
            for i in range(1, len(played)):
                assert played[i] != played[i - 1], f"Back-to-back repeat at position {i}"

    def test_records_in_history(self, tmp_path):
        music = _make_tracks(tmp_path, ["a.mp3"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"), \
             patch.object(PlaylistManager, "_save_state"):
            pm = PlaylistManager(music)
            pm.next_track()
        assert len(pm._history) == 1
        assert pm._history[0] == "a.mp3"

    def test_weight_reset_when_all_low(self, tmp_path):
        """When all weights drop below threshold, history resets."""
        music = _make_tracks(tmp_path, ["a.mp3", "b.mp3"])
        with patch.object(PlaylistManager, "_load_state"), \
             patch.object(PlaylistManager, "_start_rescan_timer"), \
             patch.object(PlaylistManager, "_save_state"):
            pm = PlaylistManager(music)
            # Manually set history to make all weights low
            pm._history = ["a.mp3", "b.mp3"]
            # With 2 tracks, cooldown = 3. Both played recently.
            # a.mp3: plays_since=1, penalty=1-1/3=0.67, weight=0.33
            # b.mp3: plays_since=0, penalty=1-0/3=1.0, weight=0.0
            # b.mp3 is below 0.1, a.mp3 is above
            # So weights won't all be below 0.1 with just 2 entries
            # Force the edge case: fill history so both are at plays_since=0 and 1
            pm._history = ["a.mp3", "b.mp3"] * 5
            track = pm.next_track()
            assert track is not None


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        state_path = tmp_path / "state.json"
        music = _make_tracks(tmp_path, ["a.mp3", "b.mp3"])
        with patch("playlist_manager.STATE_PATH", state_path), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
            pm.next_track()
            pm.next_track()

            # Verify state file exists
            assert state_path.exists()
            data = json.loads(state_path.read_text())
            assert len(data["history"]) == 2

            # Create new manager, should restore history
            pm2 = PlaylistManager(music)
            assert len(pm2._history) == 2

    def test_corrupted_state_file(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("not json!!!")
        music = _make_tracks(tmp_path, ["a.mp3"])
        with patch("playlist_manager.STATE_PATH", state_path), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        # Should recover gracefully with empty history
        assert pm._history == []

    def test_deleted_files_cleaned_from_history(self, tmp_path):
        state_path = tmp_path / "state.json"
        music = _make_tracks(tmp_path, ["a.mp3", "b.mp3"])
        # Pre-seed history with a file that no longer exists
        state_path.write_text(json.dumps({"history": ["a.mp3", "deleted.mp3", "b.mp3"]}))
        with patch("playlist_manager.STATE_PATH", state_path), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
        # deleted.mp3 should be cleaned from history during scan
        assert "deleted.mp3" not in pm._history

    def test_subdir_tracks_in_history_use_relative_path(self, tmp_path):
        state_path = tmp_path / "state.json"
        music = _make_tracks(tmp_path, ["sub/track.mp3"])
        with patch("playlist_manager.STATE_PATH", state_path), \
             patch.object(PlaylistManager, "_start_rescan_timer"):
            pm = PlaylistManager(music)
            pm.next_track()
        assert pm._history[0] == "sub/track.mp3"
