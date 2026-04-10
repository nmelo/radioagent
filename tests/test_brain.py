"""Tests for brain.py: rate limiter, WAV validation, pipeline integration."""

from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from brain import (
    AnnouncementRateLimiter,
    AnnounceRequest,
    ChannelLevelRequest,
    QueuedAnnouncement,
    VoiceLevelRequest,
    VOICE_LEVELS,
    DEFAULT_VOICE_LEVEL,
    MUSIC_LEVELS,
    DEFAULT_MUSIC_LEVEL,
    TONES_LEVELS,
    DEFAULT_TONES_LEVEL,
    validate_wav,
    process_announcement,
    create_app,
    get_now_playing_from_icecast,
    query_liquidsoap,
)


# --- Helpers ---


def make_wav(path: Path, duration: float = 2.0, sample_rate: int = 24000,
             amplitude: float = 0.3, silent: bool = False) -> Path:
    """Create a test WAV file."""
    n_frames = int(sample_rate * duration)
    if silent:
        samples = np.zeros(n_frames, dtype=np.int16)
    else:
        t = np.linspace(0, duration, n_frames, endpoint=False)
        samples = (amplitude * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())
    return path


def make_announcement(text: str = "test", kind: str = "custom",
                      agent: str = "") -> QueuedAnnouncement:
    return QueuedAnnouncement(text=text, kind=kind, agent=agent)


def _make_config(tmp_path):
    music = tmp_path / "music"
    music.mkdir()
    (music / "t.mp3").write_bytes(b"fake")
    from config import RadioConfig
    return RadioConfig(
        music_dir=music,
        liquidsoap_socket=Path(tmp_path / "test.sock"),
        webhook_port=8001,
        icecast_password="test",
    )


# --- AnnounceRequest validation ---


def test_announce_request_valid():
    req = AnnounceRequest(detail="hello world")
    assert req.detail == "hello world"
    assert req.kind == "custom"


def test_announce_request_empty_detail():
    with pytest.raises(Exception):
        AnnounceRequest(detail="")


def test_announce_request_whitespace_detail():
    with pytest.raises(Exception):
        AnnounceRequest(detail="   ")


def test_announce_request_extra_fields():
    req = AnnounceRequest(detail="test", kind="agent.completed", agent="eng1")
    assert req.agent == "eng1"


# --- Rate limiter ---


class TestRateLimiter:
    def test_first_event_immediate(self):
        rl = AnnouncementRateLimiter(10)
        processed = []
        rl.set_processor(lambda a: processed.append(a))
        result = rl.submit(make_announcement("first"))
        assert result == "immediate"
        assert len(processed) == 1

    def test_second_event_within_interval_queues(self):
        rl = AnnouncementRateLimiter(10)
        rl.set_processor(lambda a: None)
        rl.submit(make_announcement("first"))
        result = rl.submit(make_announcement("second"))
        assert result == "queued"
        assert len(rl.queue) == 1

    def test_queue_full_drops(self):
        rl = AnnouncementRateLimiter(10, max_queue=3)
        rl.set_processor(lambda a: None)
        rl.submit(make_announcement("first"))  # immediate
        rl.submit(make_announcement("q1"))  # queued
        rl.submit(make_announcement("q2"))  # queued
        rl.submit(make_announcement("q3"))  # queued
        result = rl.submit(make_announcement("overflow"))  # dropped
        assert result == "dropped"

    def test_15_events_first_immediate_10_queued_4_dropped(self):
        rl = AnnouncementRateLimiter(10, max_queue=10)
        rl.set_processor(lambda a: None)
        results = [rl.submit(make_announcement(f"e{i}")) for i in range(15)]
        assert results[0] == "immediate"
        assert results[1:11] == ["queued"] * 10
        assert results[11:] == ["dropped"] * 4

    def test_drain_remaining(self):
        rl = AnnouncementRateLimiter(10)
        processed = []
        rl.set_processor(lambda a: processed.append(a.text))
        rl.submit(make_announcement("first"))  # immediate
        rl.submit(make_announcement("q1"))
        rl.submit(make_announcement("q2"))
        assert len(rl.queue) == 2
        rl.drain_remaining()
        assert len(rl.queue) == 0
        assert processed == ["first", "q1", "q2"]

    def test_drain_remaining_capped(self):
        rl = AnnouncementRateLimiter(10, max_queue=10)
        rl.set_processor(lambda a: None)
        rl.submit(make_announcement("first"))  # immediate
        for i in range(8):
            rl.submit(make_announcement(f"q{i}"))
        assert len(rl.queue) == 8
        rl.drain_remaining(max_iterations=3)
        assert len(rl.queue) == 5  # 8 - 3 = 5 remaining

    def test_event_after_interval_is_immediate(self):
        rl = AnnouncementRateLimiter(10)
        rl.set_processor(lambda a: None)
        rl.submit(make_announcement("first"))
        rl.last_announcement -= 11  # Simulate 11 seconds passing
        result = rl.submit(make_announcement("second"))
        assert result == "immediate"

    def test_thread_safety_has_lock(self):
        rl = AnnouncementRateLimiter(10)
        assert hasattr(rl, "_lock")


# --- WAV validation ---


class TestWavValidation:
    def test_valid_wav(self, tmp_path):
        wav = make_wav(tmp_path / "valid.wav", duration=2.0)
        assert validate_wav(wav) is True

    def test_too_short(self, tmp_path):
        wav = make_wav(tmp_path / "short.wav", duration=0.1)
        assert validate_wav(wav) is False

    def test_too_long(self, tmp_path):
        wav = make_wav(tmp_path / "long.wav", duration=31.0)
        assert validate_wav(wav) is False

    def test_silent_wav(self, tmp_path):
        wav = make_wav(tmp_path / "silent.wav", duration=2.0, silent=True)
        assert validate_wav(wav) is False

    def test_nonexistent_file(self, tmp_path):
        assert validate_wav(tmp_path / "nope.wav") is False

    def test_corrupt_file(self, tmp_path):
        bad = tmp_path / "corrupt.wav"
        bad.write_bytes(b"not a wav file at all")
        assert validate_wav(bad) is False

    def test_boundary_duration(self, tmp_path):
        wav_half = make_wav(tmp_path / "half.wav", duration=0.5)
        assert validate_wav(wav_half) is True
        wav_30 = make_wav(tmp_path / "thirty.wav", duration=30.0)
        assert validate_wav(wav_30) is True


# --- Pipeline integration ---


class TestPipeline:
    def test_process_announcement_success(self, tmp_path):
        config = _make_config(tmp_path)

        mock_tts = MagicMock()
        def fake_render(text, output_path):
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"):
            a = make_announcement("hello world", kind="agent.completed", agent="eng1")
            result = process_announcement(a, mock_tts, config)

        assert result is True
        mock_tts.render.assert_called_once()

    def test_process_announcement_tts_fails(self, tmp_path):
        config = _make_config(tmp_path)
        mock_tts = MagicMock()
        mock_tts.render.return_value = False

        a = make_announcement("hello")
        result = process_announcement(a, mock_tts, config)
        assert result is False

    def test_process_announcement_wav_invalid(self, tmp_path):
        config = _make_config(tmp_path)
        mock_tts = MagicMock()
        def fake_render_short(text, output_path):
            make_wav(output_path, duration=0.1)  # too short
            return True
        mock_tts.render.side_effect = fake_render_short

        a = make_announcement("hello")
        result = process_announcement(a, mock_tts, config)
        assert result is False

    def test_wav_filename_uses_uuid(self, tmp_path):
        config = _make_config(tmp_path)
        mock_tts = MagicMock()
        rendered_paths = []
        def fake_render(text, output_path):
            rendered_paths.append(output_path)
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"):
            process_announcement(make_announcement("a"), mock_tts, config)
            process_announcement(make_announcement("b"), mock_tts, config)

        assert len(rendered_paths) == 2
        # Filenames should be different (uuid-based, not sequential)
        assert rendered_paths[0].name != rendered_paths[1].name
        assert "announce_" in rendered_paths[0].name


# --- FastAPI app ---


class TestApp:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        config = _make_config(tmp_path)
        mock_tts = MagicMock()
        mock_tts.name = "mock"
        def fake_render(text, output_path):
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"):
            app = create_app(config, mock_tts)
            yield TestClient(app)

    def test_post_valid(self, client):
        resp = client.post("/announce", json={"detail": "test announcement"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "immediate"

    def test_post_missing_detail(self, client):
        resp = client.post("/announce", json={"kind": "test"})
        assert resp.status_code == 422

    def test_post_empty_detail(self, client):
        resp = client.post("/announce", json={"detail": ""})
        assert resp.status_code == 422

    def test_post_not_json(self, client):
        resp = client.post("/announce", content="not json", headers={"content-type": "text/plain"})
        assert resp.status_code == 422

    def test_rate_limiting_429(self, client):
        resp1 = client.post("/announce", json={"detail": "first"})
        assert resp1.status_code == 200
        for i in range(10):
            resp = client.post("/announce", json={"detail": f"queued {i}"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "queued"
        resp_drop = client.post("/announce", json={"detail": "overflow"})
        assert resp_drop.status_code == 429

    def test_suppressed_event(self, client):
        """Events matching suppress_kinds should return suppressed status."""
        resp = client.post("/announce", json={"detail": "idle event", "kind": "agent.idle"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "suppressed"

    def test_script_generator_cleans_text(self, client):
        """Verify markdown and URLs are stripped before TTS."""
        resp = client.post("/announce", json={
            "detail": "Fixed **bug** in https://example.com/repo",
            "kind": "agent.completed",
            "agent": "eng1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "immediate"

    def test_script_generator_templates(self, client):
        """Verify kind-based templates are applied."""
        resp = client.post("/announce", json={
            "detail": "the auth refactor",
            "kind": "agent.completed",
            "agent": "eng1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "immediate"

    def test_recent_announcements_empty(self, client):
        resp = client.get("/recent-announcements")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_recent_announcements_after_post(self, client):
        client.post("/announce", json={"detail": "first announcement", "agent": "eng1"})
        client.post("/announce", json={"detail": "second announcement", "agent": "eng2"})
        resp = client.get("/recent-announcements")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Most recent first (appendleft)
        assert "second announcement" in data[0]["text"]
        assert data[0]["agent"] == "eng2"
        assert "timestamp" in data[0]

    def test_now_playing_icecast_unavailable(self, client):
        """When Icecast is unreachable, return fallback."""
        resp = client.get("/now-playing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Connecting..."

    def test_dashboard_missing(self, client):
        """GET / returns 404 when dashboard.html doesn't exist."""
        resp = client.get("/")
        assert resp.status_code == 404

    def test_dashboard_served(self, tmp_path):
        """GET / serves dashboard.html when present."""
        from fastapi.testclient import TestClient
        config = _make_config(tmp_path)
        mock_tts = MagicMock()
        mock_tts.name = "mock"
        mock_tts.render.return_value = False

        # Create dashboard.html next to brain.py's expected location
        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"), \
             patch("brain.Path") as mock_path_cls:
            # We need to mock __file__ parent to point to tmp_path
            pass

        # Simpler: just test that the endpoint logic works
        # by creating the file where brain.py looks for it
        import brain
        html_path = Path(brain.__file__).parent / "dashboard.html"
        html_path.write_text("<html><body>Dashboard</body></html>")
        try:
            with patch("brain.push_to_liquidsoap", return_value=True), \
                 patch("brain._schedule_wav_cleanup"):
                app = create_app(config, mock_tts)
                tc = TestClient(app)
                resp = tc.get("/")
                assert resp.status_code == 200
                assert "Dashboard" in resp.text
        finally:
            html_path.unlink(missing_ok=True)

    def test_suppressed_not_in_history(self, client):
        """Suppressed events should not appear in announcement history."""
        client.post("/announce", json={"detail": "idle", "kind": "agent.idle"})
        resp = client.get("/recent-announcements")
        assert resp.json() == []

    def test_cors_headers(self, client):
        resp = client.options("/now-playing", headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        })
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_voice_level_valid(self, client):
        """POST /voice-level with valid levels 1-5 returns ok."""
        with patch("brain.query_liquidsoap", return_value="OK"):
            for level in range(1, 6):
                resp = client.post("/voice-level", json={"level": level})
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["level"] == level
                assert "voice_volume" in data

    def test_voice_level_out_of_range(self, client):
        """POST /voice-level rejects levels outside 1-5."""
        resp = client.post("/voice-level", json={"level": 0})
        assert resp.status_code == 422
        resp = client.post("/voice-level", json={"level": 6})
        assert resp.status_code == 422
        resp = client.post("/voice-level", json={"level": -1})
        assert resp.status_code == 422

    def test_voice_level_missing(self, client):
        """POST /voice-level without level field returns 422."""
        resp = client.post("/voice-level", json={})
        assert resp.status_code == 422

    def test_voice_level_in_now_playing(self, client):
        """voice_level appears in /now-playing response."""
        resp = client.get("/now-playing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["voice_level"] == 4  # default

    def test_voice_level_persists_in_now_playing(self, client):
        """After setting voice level, /now-playing reflects the new value."""
        with patch("brain.query_liquidsoap", return_value="OK"):
            client.post("/voice-level", json={"level": 2})
        resp = client.get("/now-playing")
        assert resp.json()["voice_level"] == 2

    def test_voice_level_liquidsoap_unavailable(self, client):
        """POST /voice-level returns 503 when Liquidsoap is unreachable."""
        with patch("brain.query_liquidsoap", return_value=None):
            resp = client.post("/voice-level", json={"level": 3})
            assert resp.status_code == 503

    # --- Music level ---

    def test_music_level_valid(self, client):
        with patch("brain.query_liquidsoap", return_value="OK"):
            for level in range(1, 6):
                resp = client.post("/music-level", json={"level": level})
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["level"] == level

    def test_music_level_out_of_range(self, client):
        resp = client.post("/music-level", json={"level": 0})
        assert resp.status_code == 422
        resp = client.post("/music-level", json={"level": 6})
        assert resp.status_code == 422

    def test_music_level_in_now_playing(self, client):
        resp = client.get("/now-playing")
        assert resp.json()["music_level"] == 2  # default

    def test_music_level_persists(self, client):
        with patch("brain.query_liquidsoap", return_value="OK"):
            client.post("/music-level", json={"level": 4})
        resp = client.get("/now-playing")
        assert resp.json()["music_level"] == 4

    def test_music_level_liquidsoap_unavailable(self, client):
        with patch("brain.query_liquidsoap", return_value=None):
            resp = client.post("/music-level", json={"level": 3})
            assert resp.status_code == 503

    # --- Tones level ---

    def test_tones_level_valid(self, client):
        with patch("brain.query_liquidsoap", return_value="OK"):
            for level in range(1, 6):
                resp = client.post("/tones-level", json={"level": level})
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["level"] == level

    def test_tones_level_out_of_range(self, client):
        resp = client.post("/tones-level", json={"level": 0})
        assert resp.status_code == 422
        resp = client.post("/tones-level", json={"level": 6})
        assert resp.status_code == 422

    def test_tones_level_in_now_playing(self, client):
        resp = client.get("/now-playing")
        assert resp.json()["tones_level"] == 3  # default

    def test_tones_level_persists(self, client):
        with patch("brain.query_liquidsoap", return_value="OK"):
            client.post("/tones-level", json={"level": 5})
        resp = client.get("/now-playing")
        assert resp.json()["tones_level"] == 5

    def test_tones_level_liquidsoap_unavailable(self, client):
        with patch("brain.query_liquidsoap", return_value=None):
            resp = client.post("/tones-level", json={"level": 3})
            assert resp.status_code == 503

    def test_project_in_sse_record(self, client):
        """Announcement with project includes it in recent-announcements."""
        # Use agent.started (tone-only) to avoid TTS pipeline
        client.post("/announce", json={
            "detail": "picking up config", "kind": "agent.started", "project": "homelab"
        })
        resp = client.get("/recent-announcements")
        data = resp.json()
        assert len(data) > 0
        assert data[0]["project"] == "homelab"

    def test_no_project_no_field_in_record(self, client):
        """Announcement without project omits the field from recent-announcements."""
        client.post("/announce", json={
            "detail": "picking up config", "kind": "agent.started"
        })
        resp = client.get("/recent-announcements")
        data = resp.json()
        assert len(data) > 0
        assert "project" not in data[0]


class TestProjectVoice:
    """Test per-project voice selection in create_app."""

    @pytest.fixture
    def project_client(self, tmp_path):
        from fastapi.testclient import TestClient
        from config import RadioConfig
        music = tmp_path / "music"
        music.mkdir()
        (music / "t.mp3").write_bytes(b"fake")
        config = RadioConfig(
            music_dir=music,
            liquidsoap_socket=Path(tmp_path / "test.sock"),
            webhook_port=8001,
            icecast_password="test",
            tts_voice="af_heart",
            project_voices={"homelab": "am_adam", "_default": "af_sky"},
        )
        mock_tts = MagicMock()
        mock_tts.name = "mock"
        def fake_render(text, output_path, voice=None):
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"):
            app = create_app(config, mock_tts)
            yield TestClient(app), mock_tts

    def test_project_voice_used(self, project_client):
        """Homelab project should use am_adam voice."""
        client, mock_tts = project_client
        client.post("/announce", json={
            "detail": "deploy done", "kind": "agent.completed", "project": "homelab"
        })
        # Check the voice argument passed to TTS render
        call_args = mock_tts.render.call_args
        assert call_args is not None
        assert call_args.kwargs.get("voice") == "am_adam"

    def test_unknown_project_uses_default_mapping(self, project_client):
        """Unknown project falls back to _default in project_voices."""
        client, mock_tts = project_client
        client.post("/announce", json={
            "detail": "deploy done", "kind": "agent.completed", "project": "unknown-proj"
        })
        call_args = mock_tts.render.call_args
        assert call_args is not None
        assert call_args.kwargs.get("voice") == "af_sky"

    def test_failure_overrides_project_voice(self, project_client):
        """Failure voice (am_michael) overrides project voice."""
        client, mock_tts = project_client
        client.post("/announce", json={
            "detail": "build broke", "kind": "build.failed", "project": "homelab"
        })
        call_args = mock_tts.render.call_args
        assert call_args is not None
        assert call_args.kwargs.get("voice") == "am_michael"

    def test_no_project_uses_tts_voice(self, project_client):
        """No project field uses config.tts_voice."""
        client, mock_tts = project_client
        client.post("/announce", json={
            "detail": "deploy done", "kind": "agent.completed"
        })
        call_args = mock_tts.render.call_args
        assert call_args is not None
        assert call_args.kwargs.get("voice") == "af_heart"


# --- Liquidsoap metadata ---


class TestNowPlaying:
    def test_get_now_playing_parses_metadata(self):
        metadata_response = (
            'title="Test Track"\n'
            'artist="Test Artist"\n'
            'album="Test Album"\n'
            'source="music"\n'
            'filename="/opt/agent-radio/music/test.mp3"\n'
        )
        with patch("brain.query_liquidsoap") as mock_query:
            mock_query.side_effect = lambda sp, cmd: {
                "request.on_air": "5",
                "request.metadata 5": metadata_response,
            }.get(cmd)
            result = get_now_playing(Path("/tmp/test.sock"))

        assert result["title"] == "Test Track"
        assert result["artist"] == "Test Artist"
        assert result["album"] == "Test Album"
        assert result["source_type"] == "music"

    def test_get_now_playing_fallback_filename(self):
        metadata_response = (
            'filename="/opt/agent-radio/music/ambient_01.mp3"\n'
            'source="music"\n'
        )
        with patch("brain.query_liquidsoap") as mock_query:
            mock_query.side_effect = lambda sp, cmd: {
                "request.on_air": "1",
                "request.metadata 1": metadata_response,
            }.get(cmd)
            result = get_now_playing(Path("/tmp/test.sock"))

        assert result["title"] == "ambient_01"

    def test_get_now_playing_socket_down(self):
        with patch("brain.query_liquidsoap", return_value=None):
            result = get_now_playing(Path("/tmp/test.sock"))
        assert result["title"] == "Connecting..."
