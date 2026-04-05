# Agent Radio

A LAN radio station that gives you ambient audio awareness of your AI coding agents. Music plays continuously. When agents finish tasks, hit errors, or need attention, a voice announces it over the stream. You hear what's happening from the kitchen, the couch, or wherever you are.

## How it works

1. Ambient music plays via Liquidsoap + Icecast
2. External systems POST events to a webhook
3. The Brain translates events to speech via Kokoro TTS
4. Liquidsoap ducks the music and plays the announcement
5. Any HTTP audio client connects to the stream

## Quick start

```bash
# On the streaming server (Ubuntu + GPU for TTS)
git clone https://github.com/nmelo/agent-radio.git /opt/agent-radio
cd /opt/agent-radio
cp config.yaml.example config.yaml  # edit passwords

# Install deps
python3 -m venv venv
venv/bin/pip install fastapi uvicorn pyyaml kokoro soundfile

# Start (requires Icecast2 and Liquidsoap 2.2+ already installed)
sudo systemctl start icecast2
liquidsoap radio.liq &
venv/bin/python brain.py &
```

Listen: `http://<host>:8000/stream`
Dashboard: `http://<host>:8001/`
Announce: `curl -X POST http://<host>:8001/announce -H 'Content-Type: application/json' -d '{"detail":"Hello from Agent Radio"}'`

## Architecture

```
webhook POST -> Brain (FastAPI) -> Kokoro TTS -> WAV file
                                                    |
                                                    v
Music dir -> Liquidsoap [playlist + crossfade + smooth_add] -> Icecast -> listeners
```

Two processes. Brain handles webhooks, TTS, and pushes WAV paths to Liquidsoap via Unix socket. Liquidsoap handles all audio: mixing, ducking, encoding, streaming. They communicate over a socket. If Brain crashes, music keeps playing.

## Stack

- **Python 3.11+** with FastAPI/uvicorn
- **Kokoro TTS** (82M params, 50ms per clip on GPU)
- **Liquidsoap 2.2+** for audio mixing and streaming
- **Icecast2** for HTTP audio delivery

## Config

See `config.yaml.example`. Key settings: `music_dir`, `tts_voice`, `webhook_port`, `icecast_password`.

## Webhook API

```bash
# Simple announcement
curl -X POST http://host:8001/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail":"Phase 1 is complete"}'

# With agent and event kind (triggers templates)
curl -X POST http://host:8001/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail":"Auth refactor done","agent":"eng1","kind":"agent.completed"}'
```

## License

MIT
