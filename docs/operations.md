# Agent Radio Operations

## Resource Usage

Measured on workbench (Ryzen 9 9950X3D, 64GB RAM, RTX 5080 16GB, Ubuntu). All numbers from a live system running in Docker (Icecast + Liquidsoap + brain).

### Process Memory (RSS)

| Process | Idle RSS | Notes |
|---------|----------|-------|
| brain (python) | ~1.9 GB | Kokoro model loaded in RAM. Torch tensors. FastAPI + uvicorn overhead. |
| liquidsoap | ~172 MB | Audio decoding, crossfade buffers, MP3 encoding. Stable. |
| icecast2 | ~18 MB | Minimal. Holds stream buffer, serves listeners. |
| **Total** | **~2.1 GB** | Without MusicGen. Add ~5 GB if MusicGen enabled. |

The brain is the heavy process. ~1.9 GB is almost entirely the Kokoro model and PyTorch runtime loaded in RAM. This is constant whether idle or processing announcements.

### CPU Usage

| Process | Idle | During announcement | Notes |
|---------|------|-------------------|-------|
| brain | <1% | ~6% spike (50-100ms) | TTS inference is a brief burst. Returns to idle immediately. |
| liquidsoap | ~2% | ~2% | Constant: MP3 encoding runs continuously regardless of listeners or announcements. |
| icecast2 | <1% | <1% | Near-zero when no listeners connected. Scales with listener count. |
| **System load** | **0.2-0.3** | **0.3-0.4** | Negligible on a modern CPU. |

Liquidsoap is the only process with constant CPU usage. It continuously decodes audio files, applies the mixing chain (crossfade, amplify, smooth_add), and encodes to MP3. This happens whether anyone is listening or not. On a 16-core Ryzen 9, 2% CPU is one core at ~30% utilization.

### GPU VRAM

| State | VRAM Used | Notes |
|-------|-----------|-------|
| Brain idle (Kokoro loaded) | ~1.0-1.5 GB | Model weights + CUDA context. Reserved even when not inferencing. |
| During TTS inference | ~1.5-2.0 GB | Brief spike during generation (~50ms). |
| MusicGen enabled (post-MVP) | +~5 GB | MusicGen-medium 1.5B parameters. |
| **Total with MusicGen** | **~7 GB** | Leaves ~9 GB free on RTX 5080 (16 GB total). |

Note: If the brain has been idle for a long period, PyTorch may release some CUDA memory back to the system. The model reloads on next inference (adds ~1-2s latency for the first announcement after a long idle).

### Disk

| Item | Size | Growth Rate | Notes |
|------|------|-------------|-------|
| Liquidsoap log | ~2.3 MB | ~100 KB/hour | Track changes, metadata updates, blank detection events. |
| Brain log | ~34 KB | ~5 KB/hour | Announcement events, mute/unmute, errors. |
| WAV temp files | ~17 MB (127 files) | Should be 0 | **Bug: WAV cleanup not running reliably.** Files should delete 60s after playback. Currently accumulating. ~96 KB per announcement. |
| Music library | Varies | Static | Operator-provided. Not managed by Agent Radio. |
| Kokoro model cache | ~200 MB | Static | Downloaded on first run to ~/.cache/huggingface/. |

**WAV cleanup bug:** The brain schedules WAV deletion via `threading.Timer(60, ...)` after pushing to Liquidsoap. On workbench, 127 files accumulated over a session. Either the timers aren't firing in Docker, or they were generated during testing before the cleanup code was added. This needs investigation.

**Log rotation:** Neither log has rotation configured. The Liquidsoap log will grow to ~2.4 MB/day. At that rate, it takes ~400 days to hit 1 GB. Not urgent, but production deployments should configure logrotate or Docker log drivers.

### Network

| Metric | Value | Notes |
|--------|-------|-------|
| Stream bitrate | 192 kbps (constant) | MP3, stereo, 44.1 kHz. Per-listener bandwidth. |
| Per listener | ~24 KB/s | 192,000 bits/sec = 24,000 bytes/sec. |
| 10 listeners | ~240 KB/s | Linear scaling. Icecast handles this trivially. |
| Webhook POST | ~1 KB/request | JSON payload. Negligible. |
| SSE connection | ~1 KB/min | Keepalive every 30s + event data. One connection per dashboard tab. |
| Icecast to Liquidsoap | ~24 KB/s | Same 192 kbps stream, always flowing regardless of listener count. |

The network cost is dominated by listeners. Each listener costs 192 kbps of sustained bandwidth. For a LAN deployment, even 50 listeners (12 Mbps) is well within gigabit ethernet capacity.

### Resource Profiles

#### Profile: Idle (no listeners, no announcements)

The radio is running but nobody is connected and no events are arriving.

| Resource | Usage |
|----------|-------|
| CPU | ~2% (Liquidsoap encoding) |
| RAM | ~2.1 GB (brain 1.9 GB + liquidsoap 172 MB + icecast 18 MB) |
| GPU VRAM | ~1.0-1.5 GB (Kokoro model loaded) |
| Network out | ~24 KB/s (Liquidsoap -> Icecast, internal) |
| Disk I/O | Minimal (reads music files, writes logs) |

Liquidsoap encodes and sends to Icecast continuously. Icecast accepts the stream and holds it in a buffer, discarding old data when nobody is listening. This is by design: the stream must be ready for instant connection.

#### Profile: 1 Listener (dashboard open, occasional announcements)

Typical usage: operator has the dashboard open, agents send events every few minutes.

| Resource | Usage |
|----------|-------|
| CPU | ~2-3% steady, ~6% spikes on announcements |
| RAM | ~2.1 GB (same as idle) |
| GPU VRAM | ~1.5 GB (same as idle, brief spikes during TTS) |
| Network out | ~48 KB/s (24 KB/s stream + 24 KB/s to listener) |
| Disk I/O | Brief WAV writes (~96 KB per announcement) |

The dashboard's SSE connection and /now-playing polling add negligible load (~1 KB/min).

#### Profile: Active Session (5 agents, frequent events)

A busy coding session: 5 agents sending completions, starts, failures. Announcements every 10-30 seconds. 1-3 listeners.

| Resource | Usage |
|----------|-------|
| CPU | ~3-5% steady, ~6% spikes every 10s |
| RAM | ~2.1 GB (rate limiter queue adds bytes, not megabytes) |
| GPU VRAM | ~1.5-2.0 GB (more frequent TTS inference) |
| Network out | ~72-120 KB/s (stream + 1-3 listeners) |
| Disk I/O | ~10 KB/s (WAV writes + deletes + logs) |

Rate limiter caps announcements at 1 per 10 seconds. Even with 5 agents flooding events, TTS fires at most 6 times per minute. Each inference is ~50ms. Total TTS CPU time: ~300ms per minute, or 0.5% of one core.

### Can This Run on a Cheap VPS?

**Without GPU (CPU-only Kokoro):**

| VPS Spec | Works? | Notes |
|----------|--------|-------|
| 1 vCPU, 2 GB RAM | No | Brain alone needs ~1.9 GB. OOM likely. |
| 2 vCPU, 4 GB RAM | Marginal | Tight on RAM. TTS at ~5x realtime means 2s per announcement. Works but fragile. |
| 4 vCPU, 8 GB RAM | Yes | Comfortable headroom. ~5x realtime TTS. Good for a few listeners. |
| 8 vCPU, 16 GB RAM | Yes | Handles 10+ listeners and frequent announcements without strain. |

**With GPU (cloud GPU instance):**

| Instance | Works? | Notes |
|----------|--------|-------|
| T4 (16 GB VRAM, cheap) | Yes | Kokoro at ~36x realtime. ~280ms per clip. Good. |
| L4 (24 GB VRAM) | Yes | Kokoro at ~81x realtime. Room for MusicGen. |
| A10G (24 GB VRAM) | Yes | Kokoro at ~96x realtime. Overkill for TTS alone. |

**Minimum viable spec:** 4 vCPU, 8 GB RAM, no GPU. This gives you music + voice announcements with ~2 second TTS latency. Good enough for a personal setup with 1-3 listeners.

**Recommended spec:** Any machine with a GPU (even an old GTX 1060 6GB). The GPU drops TTS from 2 seconds to 50ms, which makes the announcement experience feel instant.

## WAV Cleanup Issue

As of 2026-04-05, 127 WAV files accumulated in `/tmp/agent-radio/` totaling 17 MB. These should be cleaned up 60 seconds after Liquidsoap plays them. The `threading.Timer` cleanup in brain.py may not be firing reliably in the Docker environment. 

Workaround until fixed:
```bash
# Manual cleanup of WAV files older than 5 minutes
find /tmp/agent-radio -name 'announce_*.wav' -mmin +5 -delete
```

Or add to crontab:
```
*/10 * * * * find /tmp/agent-radio -name 'announce_*.wav' -mmin +5 -delete 2>/dev/null
```

## Monitoring Checklist

Quick health check commands for operators:

```bash
# Is the stream live?
curl -sf -o /dev/null -m 5 http://localhost:8000/stream && echo "UP" || echo "DOWN"

# Is the brain responding?
curl -sf http://localhost:8001/now-playing | head -1 && echo "OK" || echo "DOWN"

# Current listeners
curl -s http://localhost:8000/status-json.xsl | python3 -c \
  'import sys,json; d=json.load(sys.stdin); print(f"Listeners: {d[\"icestats\"][\"source\"][\"listeners\"]}")'

# Process memory
ps -eo pid,rss,comm --sort=-rss | grep -E 'python|liquidsoap|icecast' | head -5

# GPU VRAM
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader

# WAV file accumulation (should be near 0)
ls /tmp/agent-radio/announce_*.wav 2>/dev/null | wc -l

# Log sizes
du -sh /tmp/agent-radio/*.log
```