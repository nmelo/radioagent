# Hear your build. Don't watch it.

Radio Agent is a LAN radio station that gives you ambient audio awareness of your AI coding agents. Music plays continuously. When agents finish tasks, hit errors, or need attention, a voice announces it over the stream.

You hear what's happening from the kitchen, the couch, or wherever you are.

**[radioagent.live](https://radioagent.live)**

## Install

```bash
curl -sSfL https://radioagent.live/install.sh | bash
```

Or with Docker:

```bash
git clone https://github.com/nmelo/radioagent.git && cd radioagent
docker compose -f deploy/docker/docker-compose.yml up
```

Dashboard: `http://localhost:8001` | Stream: `http://localhost:8000/stream`

![Radio Agent Dashboard](assets/dashboard.png)

## What you hear

**Task completes** -- music ducks, a voice says *"eng1 just shipped the auth refactor, single commit, tests green"*, music fades back up.

**Something breaks** -- a low tone plays under the music, then the voice: *"Build failed on the payments module, missing dependency"*.

**Agent starts working** -- a short chime plays under the music. No voice interrupt. You know something kicked off without looking up.

After a day of listening, you stop consciously hearing the tones but you still know when agents are busy. Designed around [calm technology](https://calmtech.com/papers/coming-age-calm-technology) principles: information moves between periphery and center, and the system disappears into the background when not needed.

## Three channels

| Channel | What it does | Attention level |
|---------|-------------|----------------|
| **Music** | Continuous ambient playback from your library | Peripheral |
| **Voice** | Spoken announcements via TTS, ducks the music | Center-pull |
| **Tones** | Short sound effects for agent state changes | Peripheral |

Each channel is independently mutable from the dashboard.

## Architecture

```
webhook POST -> Brain (FastAPI) -> Kokoro TTS -> WAV file
                                                    |
                                                    v
Music dir -> Liquidsoap [playlist + crossfade + smooth_add] -> Icecast -> listeners
```

Two processes. Brain handles webhooks, TTS, and pushes WAV paths to Liquidsoap via Unix socket. Liquidsoap handles all audio mixing, ducking, encoding, and streaming. If Brain crashes, music keeps playing.

## DJ Skill

Radio Agent ships with a Claude Code skill that transforms robotic agent messages into creative radio callouts. Install it and your agents become DJs.

```bash
cp -r skills/dj ~/.claude/skills/dj
```

Or download `dj.skill` directly from the dashboard.

## Webhook API

```bash
# Simple announcement
curl -X POST http://localhost:8001/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail":"Phase 1 is complete"}'

# With agent and event kind (triggers voice + tone)
curl -X POST http://localhost:8001/announce \
  -H 'Content-Type: application/json' \
  -d '{"detail":"Auth refactor done","agent":"eng1","kind":"agent.completed"}'
```

Works with any tool that can POST JSON: Claude Code hooks, GitHub Actions, shell scripts, CI pipelines.

## Stack

- **Python 3.12** with FastAPI/uvicorn
- **Kokoro TTS** (82M params, 50ms per clip on GPU, works on CPU too)
- **Liquidsoap 2.2+** for audio mixing and streaming
- **Icecast2** for HTTP audio delivery

## Credits

Inspired by [WRIT-FM](https://github.com/keltokhy/writ-fm), a 24/7 AI-powered internet radio station.

## License

MIT
