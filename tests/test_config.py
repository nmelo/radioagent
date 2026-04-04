"""Tests for config.py: YAML loading, validation, defaults."""

import textwrap
from pathlib import Path

import pytest

from config import RadioConfig, load_config


@pytest.fixture
def music_dir(tmp_path):
    d = tmp_path / "music"
    d.mkdir()
    (d / "track.mp3").write_bytes(b"fake")
    return d


@pytest.fixture
def config_file(tmp_path, music_dir):
    """Write a minimal valid config and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""\
        music_dir: {music_dir}
        icecast_password: test123
    """))
    return p


def test_load_valid_config(config_file, music_dir):
    cfg = load_config(config_file)
    assert isinstance(cfg, RadioConfig)
    assert cfg.music_dir == music_dir
    assert cfg.icecast_password == "test123"


def test_defaults_applied(config_file):
    cfg = load_config(config_file)
    assert cfg.tts_engine == "kokoro"
    assert cfg.tts_voice == "am_michael"
    assert cfg.tts_speed == 1.0
    assert cfg.webhook_port == 8001
    assert cfg.webhook_rate_limit == 10
    assert cfg.icecast_host == "localhost"
    assert cfg.icecast_port == 8000
    assert cfg.icecast_mount == "/stream"
    assert cfg.liquidsoap_socket == Path("/tmp/agent-radio.sock")
    assert cfg.suppress_kinds == ["*.idle", "*.message"]
    assert cfg.max_announcement_words == 40
    assert cfg.music_ai_enabled is False


def test_missing_config_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(tmp_path / "nonexistent.yaml")


def test_music_dir_does_not_exist(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("music_dir: /no/such/directory\n")
    with pytest.raises(ValueError, match="music_dir does not exist"):
        load_config(p)


def test_invalid_webhook_port(tmp_path, music_dir):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""\
        music_dir: {music_dir}
        webhook_port: 0
    """))
    with pytest.raises(ValueError, match="webhook_port must be between 1 and 65535"):
        load_config(p)


def test_invalid_icecast_port(tmp_path, music_dir):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""\
        music_dir: {music_dir}
        icecast_port: 99999
    """))
    with pytest.raises(ValueError, match="icecast_port must be between 1 and 65535"):
        load_config(p)


def test_extra_keys_ignored(tmp_path, music_dir):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""\
        music_dir: {music_dir}
        unknown_future_key: some_value
        another_key: 42
    """))
    cfg = load_config(p)
    assert cfg.music_dir == music_dir
    assert not hasattr(cfg, "unknown_future_key")


def test_empty_music_dir_warns(tmp_path, caplog):
    empty_dir = tmp_path / "empty_music"
    empty_dir.mkdir()
    p = tmp_path / "config.yaml"
    p.write_text(f"music_dir: {empty_dir}\n")
    cfg = load_config(p)
    assert cfg.music_dir == empty_dir
    assert "music_dir is empty" in caplog.text


def test_all_fields_from_yaml(tmp_path, music_dir):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""\
        music_dir: {music_dir}
        music_ai_enabled: true
        music_ai_prompt: "lo-fi beats"
        tts_engine: orpheus
        tts_voice: bf_alice
        tts_speed: 1.2
        webhook_port: 9000
        webhook_rate_limit: 5
        liquidsoap_socket: /tmp/custom.sock
        icecast_host: 10.0.0.1
        icecast_port: 9090
        icecast_mount: /live
        icecast_password: secret
        suppress_kinds:
          - "*.idle"
        max_announcement_words: 20
    """))
    cfg = load_config(p)
    assert cfg.music_ai_enabled is True
    assert cfg.music_ai_prompt == "lo-fi beats"
    assert cfg.tts_engine == "orpheus"
    assert cfg.tts_voice == "bf_alice"
    assert cfg.tts_speed == 1.2
    assert cfg.webhook_port == 9000
    assert cfg.webhook_rate_limit == 5
    assert cfg.liquidsoap_socket == Path("/tmp/custom.sock")
    assert cfg.icecast_host == "10.0.0.1"
    assert cfg.icecast_port == 9090
    assert cfg.icecast_mount == "/live"
    assert cfg.icecast_password == "secret"
    assert cfg.suppress_kinds == ["*.idle"]
    assert cfg.max_announcement_words == 20


def test_path_fields_converted_from_strings(config_file):
    cfg = load_config(config_file)
    assert isinstance(cfg.music_dir, Path)
    assert isinstance(cfg.liquidsoap_socket, Path)
