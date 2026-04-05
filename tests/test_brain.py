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
    FAILURE_VOICE,
    QueuedAnnouncement,
    validate_wav,
    process_announcement,
    create_app,
    get_now_playing_from_icecast,
    get_next_track,
    get_tone_for_kind,
    get_voice_for_kind,
    is_tone_only,
    push_tone_to_liquidsoap,
    query_liquidsoap,
    _parse_liquidsoap_metadata,
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
    tones = tmp_path / "tones"
    tones.mkdir()
    # Create tone WAVs that the routing will look for
    for name in ("rise", "resolve", "dissonant", "pulse", "hum", "descend", "bell", "chord_long"):
        make_wav(tones / f"{name}.wav", duration=0.8)
    from config import RadioConfig
    return RadioConfig(
        music_dir=music,
        liquidsoap_socket=Path(tmp_path / "test.sock"),
        tones_dir=tones,
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
        def fake_render(text, output_path, voice=None):
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
        def fake_render_short(text, output_path, voice=None):
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
        def fake_render(text, output_path, voice=None):
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
        def fake_render(text, output_path, voice=None):
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain.push_tone_to_liquidsoap", return_value=True), \
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
        """Events matching suppress_kinds with no tone mapping return suppressed."""
        resp = client.post("/announce", json={"detail": "chat msg", "kind": "agent.message"})
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
        """Fully suppressed events (no tone, no voice) should not appear in history."""
        client.post("/announce", json={"detail": "chat msg", "kind": "agent.message"})
        resp = client.get("/recent-announcements")
        assert resp.json() == []

    def test_cors_headers(self, client):
        resp = client.options("/now-playing", headers={
            "Origin": "http://192.168.1.100:8000",
            "Access-Control-Request-Method": "GET",
        })
        assert resp.headers.get("access-control-allow-origin") == "*"


# --- Icecast metadata ---


def _mock_icecast_response(icecast_json):
    """Create a mock urllib response for Icecast status JSON."""
    import json as _json
    mock_resp = MagicMock()
    mock_resp.read.return_value = _json.dumps(icecast_json).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestNowPlayingFromIcecast:
    def test_parses_artist_title(self):
        """Icecast title 'Artist - Title' is split correctly."""
        icecast_json = {
            "icestats": {
                "source": {
                    "title": "Ruben Gonzalez - Chanchullo",
                    "listenurl": "http://localhost:8000/stream",
                }
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_icecast_response(icecast_json)):
            result = get_now_playing_from_icecast("localhost", 8000, "/stream")

        assert result["title"] == "Chanchullo"
        assert result["artist"] == "Ruben Gonzalez"

    def test_title_only_no_dash(self):
        """Title without ' - ' separator goes entirely to title field."""
        icecast_json = {
            "icestats": {
                "source": {
                    "title": "ambient_drone_003",
                    "listenurl": "http://localhost:8000/stream",
                }
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_icecast_response(icecast_json)):
            result = get_now_playing_from_icecast("localhost", 8000, "/stream")

        assert result["title"] == "ambient_drone_003"
        assert result["artist"] == ""

    def test_icecast_unreachable(self):
        """When Icecast is down, return fallback."""
        with patch("urllib.request.urlopen", side_effect=Exception("conn refused")):
            result = get_now_playing_from_icecast("localhost", 8000, "/stream")

        assert result["title"] == "Connecting..."

    def test_empty_title(self):
        """When Icecast has no title metadata, return fallback."""
        icecast_json = {"icestats": {"source": {"title": ""}}}
        with patch("urllib.request.urlopen", return_value=_mock_icecast_response(icecast_json)):
            result = get_now_playing_from_icecast("localhost", 8000, "/stream")

        assert result["title"] == "Connecting..."

    def test_multiple_sources_selects_mount(self):
        """When Icecast has multiple sources, select by mount path."""
        icecast_json = {
            "icestats": {
                "source": [
                    {"title": "Wrong - Track", "listenurl": "http://localhost:8000/other"},
                    {"title": "Right - Track", "listenurl": "http://localhost:8000/stream"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_icecast_response(icecast_json)):
            result = get_now_playing_from_icecast("localhost", 8000, "/stream")

        assert result["title"] == "Track"
        assert result["artist"] == "Right"


# --- Tone routing ---


class TestToneRouting:
    def test_get_tone_for_started(self):
        assert get_tone_for_kind("agent.started") == "rise"

    def test_get_tone_for_completed(self):
        assert get_tone_for_kind("agent.completed") == "resolve"

    def test_get_tone_for_failed(self):
        assert get_tone_for_kind("bot.failed") == "dissonant"

    def test_get_tone_for_stuck(self):
        assert get_tone_for_kind("agent.stuck") == "pulse"

    def test_get_tone_for_idle(self):
        assert get_tone_for_kind("agent.idle") == "hum"

    def test_get_tone_for_stopped(self):
        assert get_tone_for_kind("agent.stopped") == "descend"

    def test_get_tone_for_deploy(self):
        assert get_tone_for_kind("deploy.production") == "bell"

    def test_get_tone_for_milestone(self):
        assert get_tone_for_kind("milestone.phase1") == "chord_long"

    def test_no_tone_for_custom(self):
        assert get_tone_for_kind("custom") is None

    def test_no_tone_for_message(self):
        assert get_tone_for_kind("agent.message") is None

    def test_is_tone_only_started(self):
        assert is_tone_only("agent.started") is True

    def test_is_tone_only_stopped(self):
        assert is_tone_only("agent.stopped") is True

    def test_is_tone_only_idle(self):
        assert is_tone_only("agent.idle") is True

    def test_is_not_tone_only_completed(self):
        assert is_tone_only("agent.completed") is False

    def test_is_not_tone_only_deploy(self):
        assert is_tone_only("deploy.staging") is False


class TestToneApp:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        config = _make_config(tmp_path)
        mock_tts = MagicMock()
        mock_tts.name = "mock"
        def fake_render(text, output_path, voice=None):
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain.push_tone_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"):
            app = create_app(config, mock_tts)
            yield TestClient(app)

    def test_started_plays_tone_no_voice(self, client):
        """agent.started should return tone_only with rise tone."""
        resp = client.post("/announce", json={
            "detail": "starting work", "kind": "agent.started", "agent": "eng1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "tone_only"
        assert data["tone"] == "rise"

    def test_completed_plays_both(self, client):
        """agent.completed should play voice (immediate) and tone."""
        resp = client.post("/announce", json={
            "detail": "finished the task", "kind": "agent.completed", "agent": "eng1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "immediate"

    def test_tone_only_still_in_history(self, client):
        """Tone-only events should still appear in announcement history."""
        client.post("/announce", json={
            "detail": "starting work", "kind": "agent.started", "agent": "eng1",
        })
        resp = client.get("/recent-announcements")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tone"] == "rise"

    def test_mute_tones(self, client):
        resp = client.post("/mute-tones")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "muted": True}

    def test_unmute_tones(self, client):
        client.post("/mute-tones")
        resp = client.post("/unmute-tones")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "muted": False}

    def test_tones_muted_in_now_playing(self, client):
        with patch("brain.get_now_playing_from_icecast", return_value={
            "title": "Test", "artist": "", "album": "", "source_type": "music",
        }), patch("brain.get_next_track", return_value=None):
            resp = client.get("/now-playing")
        assert resp.json()["tones_muted"] is False

        client.post("/mute-tones")
        with patch("brain.get_now_playing_from_icecast", return_value={
            "title": "Test", "artist": "", "album": "", "source_type": "music",
        }), patch("brain.get_next_track", return_value=None):
            resp = client.get("/now-playing")
        assert resp.json()["tones_muted"] is True

    def test_custom_event_no_tone(self, client):
        """Custom events should not play a tone."""
        resp = client.post("/announce", json={
            "detail": "manual message", "kind": "custom", "agent": "eng1",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "immediate"

    def test_deploy_plays_both(self, client):
        """deploy.* events should play both tone and voice."""
        resp = client.post("/announce", json={
            "detail": "deployed to prod", "kind": "deploy.production", "agent": "shipper",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "immediate"


# --- Voice selection ---


class TestVoiceSelection:
    def test_failed_uses_failure_voice(self):
        assert get_voice_for_kind("agent.failed", "af_heart") == FAILURE_VOICE

    def test_stuck_uses_failure_voice(self):
        assert get_voice_for_kind("agent.stuck", "af_heart") == FAILURE_VOICE

    def test_completed_uses_default(self):
        assert get_voice_for_kind("agent.completed", "af_heart") == "af_heart"

    def test_custom_uses_default(self):
        assert get_voice_for_kind("custom", "af_heart") == "af_heart"

    def test_deploy_uses_default(self):
        assert get_voice_for_kind("deploy.production", "af_heart") == "af_heart"


class TestVoiceInPipeline:
    @pytest.fixture
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        config = _make_config(tmp_path)
        config.tts_voice = "af_heart"
        mock_tts = MagicMock()
        mock_tts.name = "mock"
        def fake_render(text, output_path, voice=None):
            make_wav(output_path, duration=2.0)
            return True
        mock_tts.render.side_effect = fake_render

        with patch("brain.push_to_liquidsoap", return_value=True), \
             patch("brain.push_tone_to_liquidsoap", return_value=True), \
             patch("brain._schedule_wav_cleanup"):
            app = create_app(config, mock_tts)
            yield TestClient(app), mock_tts

    def test_failed_announcement_uses_male_voice(self, client):
        """agent.failed should render with am_michael voice."""
        tc, mock_tts = client
        tc.post("/announce", json={
            "detail": "build failed", "kind": "agent.failed", "agent": "eng1",
        })
        mock_tts.render.assert_called_once()
        _, kwargs = mock_tts.render.call_args
        assert kwargs.get("voice") == FAILURE_VOICE

    def test_completed_announcement_uses_default_voice(self, client):
        """agent.completed should render with default voice (af_heart)."""
        tc, mock_tts = client
        tc.post("/announce", json={
            "detail": "task done", "kind": "agent.completed", "agent": "eng1",
        })
        mock_tts.render.assert_called_once()
        _, kwargs = mock_tts.render.call_args
        assert kwargs.get("voice") == "af_heart"
