# Agent Radio PRD

## 1. Problem Statement

### 1.1 The Problem

Developers running multi-agent AI coding sessions need ambient awareness of what's happening across their agent fleet. Current notification channels (terminal TUI, browser companion, Slack/Discord webhooks) all require looking at a screen. There is no audio channel: nothing you can hear from the kitchen, the couch, or while walking around the house.

Alert sounds (beeps, chimes) are annoying and lack context. You hear a ding but don't know what happened. Email/Slack notifications pile up silently. The gap is an ambient audio feed that communicates meaningful information without demanding visual attention.

### 1.2 Why Now

Local AI models for music generation (MusicGen) and text-to-speech (Kokoro, Orpheus) can run on consumer GPUs. Liquidsoap handles real-time audio mixing and streaming. Icecast serves HTTP audio streams to any device. The entire stack is open-source and runs on a single machine. None of this was practical two years ago.

## 2. User

### 2.1 Primary User

A developer running AI coding agents who wants ambient awareness of agent activity without being tethered to a screen. They have a local network with a GPU-equipped machine (for music and voice generation) and one or more listening devices (laptop, phone, kitchen speaker).

### 2.2 Secondary Users (Future)

Team members who want to monitor shared agent sessions remotely. CI/CD operators who want audio alerts for pipeline events. Anyone who benefits from ambient notification via an audio stream.

## 3. Success Criteria

### 3.1 Core Success

1. The radio plays continuously without interruption for 24+ hours
2. Announcements are audible and intelligible over the ambient music
3. The music-to-voice transition sounds smooth (no clicks, pops, or jarring volume changes)
4. End-to-end announcement latency (webhook POST to voice audible on stream) is under 10 seconds
5. Any HTTP audio client can connect and listen (browser, VLC, phone app)

### 3.2 Measurable Checks

- Continuous uptime: start the radio, leave it running overnight, verify stream is still live in the morning
- Announcement clarity: play 10 announcements, verify all are intelligible by ear
- Transition quality: no audible artifacts during 20 consecutive duck-and-restore cycles
- Latency: measure time from POST to audible output, verify < 10 seconds for 95% of events
- Client compatibility: connect from Chrome, Safari, VLC, and an iPhone, verify all play

## 4. Non-Goals

- Bidirectional communication (listeners cannot send commands back through the stream)
- Video or visual components
- Public internet streaming (LAN only for v1)
- Music generation model training (use pretrained models only)
- Integration with any specific event source (the webhook interface is generic JSON)
- Mobile app development (use browser or VLC)
- Scheduled programming or time-of-day music moods (future phase)
- Multiple simultaneous streams
- Stream recording or archival

## 5. User Journeys

### 5.1 Start the Radio

```
$ ssh workbench
$ cd /opt/agent-radio
$ ./start.sh
[agent-radio] Icecast already running (systemd), adopting
[agent-radio] Starting Liquidsoap... socket ready
[agent-radio] Starting brain... listening on :8001
[agent-radio] Stream: http://192.168.1.100:8000/stream
[agent-radio] Webhook: http://192.168.1.100:8001/announce
[agent-radio] Dashboard: http://192.168.1.100:8001/
```

Operator opens `http://192.168.1.100:8001/` in a browser. The dashboard shows a Connect button. Click it to start listening. Ambient music plays from the curated library (~/ Music on workbench). The wire feed shows announcement history and live events.

### 5.2 Announcement Arrives

External system POSTs:
```json
{
  "kind": "agent.completed",
  "agent": "eng1",
  "detail": "Finished the auth refactor. Commit abc123.",
  "project": "initech"
}
```

The listener hears:
- Music gently fades down over ~1 second
- A calm voice says: "eng1 finished the auth refactor"
- Music gently fades back up over ~1 second

### 5.3 Using the Dashboard

The operator opens `http://192.168.1.100:8001/` in a browser. The dashboard shows:

- **Now Playing**: current track title, artist, album (from Icecast metadata). A Skip button advances to the next track.
- **Up Next**: the next track in the shuffle queue.
- **Wire Feed**: live announcement text with a typing animation as voice plays. Previous announcements fade with age. Failure events get a red accent.
- **Player Controls**: Connect/Disconnect (stream transport), Music Play/Pause (server-side mute), Voice On/Off (announcement mute), Volume (client-side).

When an announcement arrives, the ON AIR badge pulses red, music ducks, and the announcement text types out character by character in the wire feed.

### 5.4 GPU Becomes Unavailable (Post-MVP)

This journey applies when AI music generation is enabled. MusicGen fails with CUDA OOM. Brain logs a warning and stops generating new clips. Liquidsoap continues playing from the curated library. The listener hears no interruption. When the GPU is free again, brain resumes AI music generation.

### 5.5 Manual Announcement

```
$ curl -X POST http://192.168.1.100:8001/announce \
    -H 'Content-Type: application/json' \
    -d '{"detail": "Phase 3 is complete. Shipping v1.7.0."}'
```

The listener hears the announcement within seconds. The dashboard wire feed shows the text simultaneously.

## 6. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| TTS model produces garbled output | Unintelligible announcement | Validate audio duration/RMS before pushing to Liquidsoap. Drop silent or too-short clips. |
| MusicGen generates unpleasant music | Listener turns off the radio | Curated fallback library provides known-good ambient music. AI clips are pre-screened (RMS, duration). |
| Liquidsoap crashes | Stream goes silent | Systemd auto-restart. Brain detects Liquidsoap socket gone and logs alert. |
| Icecast crashes | Listeners disconnected | Systemd auto-restart. Liquidsoap reconnects automatically. |
| GPU OOM from concurrent workloads | No AI music generation | Curated library fallback. Music generation is non-critical path. |
| Webhook flood (100 events/second) | TTS backlog, delayed announcements | Rate limit: max 1 announcement per 10 seconds. Queue excess, drop if queue > 10. |

## 7. Scope Boundaries

### 7.1 MVP Scope (Shipped)

- Continuous music playback via Liquidsoap + Icecast (curated library from ~/Music on workbench)
- HTTP webhook endpoint for receiving announcement events (POST /announce)
- Event-to-script translation via template-based script generator (JSON -> natural language)
- TTS generation via Kokoro 82M (pluggable TTSEngine protocol for future engines)
- Voice announcement injection with automatic music ducking (Liquidsoap smooth_add)
- Configuration via YAML file (config.yaml)
- Start/stop scripts with process monitoring and health checks
- Web dashboard with now-playing, wire feed (typing animation), skip, mute/unmute, volume
- Server-Sent Events for real-time dashboard updates
- Stream metadata via Icecast status API

### 7.2 Post-MVP (Build Later, If Needed)

- AI music generation via MusicGen (tested, not enabled by default)
- Scheduled programming (different moods by time of day)
- Station ID jingles between announcements
- Announcement priority levels (critical events get different TTS treatment)
- Multiple TTS engine support (Orpheus 3B, Fish Speech; see docs/tts-evaluation.md)
- Announcement mute/unmute (independent from music mute)
- Three-layer dashboard controls (connect/disconnect, music, voice)

### 7.3 Never Build

- Listener-to-agent communication via audio
- DRM or access control on the stream
- A mobile app
- Cloud-hosted streaming
