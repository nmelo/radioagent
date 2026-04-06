# Audio Tools for Agent Radio

Tools to install on workbench (Ubuntu) for audio management.

## Install (one command)

```bash
sudo apt install -y \
  ffmpeg \
  sox libsox-fmt-all \
  bs1770gain \
  mediainfo \
  audiowaveform \
  curl jq libxml2-utils

# rsgain (check if in repos first, otherwise grab the .deb)
sudo apt install -y rsgain 2>/dev/null || {
  wget -q https://github.com/complexlogic/rsgain/releases/latest/download/rsgain_amd64.deb
  sudo dpkg -i rsgain_amd64.deb
  sudo apt install -f -y
}
```

## Tool Summary

| Task | Primary Tool | Secondary |
|------|-------------|-----------|
| Music loudness normalization | rsgain (tags) | ffmpeg loudnorm (destructive) |
| TTS loudness normalization | ffmpeg loudnorm (two-pass) | sox norm (peak only) |
| Real-time LUFS monitoring | ffmpeg ebur128 filter | sox stats (dBFS only) |
| File metadata inspection | soxi (quick) / ffprobe (detailed) | mediainfo (tags) |
| Stream health monitoring | curl + Icecast JSON API | ffprobe + Liquidsoap telnet |
| Format conversion | ffmpeg | sox (WAV/FLAC only) |
| Silence trimming | sox | ffmpeg silenceremove |
| Volume adjustment | sox vol/norm | ffmpeg volume filter |

## 1. Music Loudness Normalization

### rsgain (recommended)

Writes ReplayGain tags at target LUFS. Non-destructive. Liquidsoap reads tags at playout time via `amplify(override="replaygain_track_gain", ...)`.

```bash
# Tag all files in a directory to -14 LUFS (Spotify standard)
rsgain easy -S -14 /music/album/

# Track mode (each file independent)
rsgain easy -S -14 -a never /music/singles/
```

### ffmpeg loudnorm (destructive alternative)

Two-pass: measure then apply. Rewrites the audio file.

```bash
# Pass 1: Measure
ffmpeg -i input.flac -af loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null - 2>&1 | tail -12

# Pass 2: Apply (using measured values from pass 1)
ffmpeg -i input.flac -af "loudnorm=I=-14:TP=-1:LRA=11:measured_I=-23.5:measured_TP=-4.2:measured_LRA=7.3:measured_thresh=-34.0:offset=-0.1:linear=true" -ar 48000 output.flac
```

Use `linear=true` in pass 2 for pure gain (no compression). Without it, ffmpeg may apply dynamic range compression.

## 2. TTS Output Normalization

Kokoro outputs quiet WAVs. Normalize each to -14 LUFS after generation. This replaces the hardcoded `amplify(3.5)` in Liquidsoap.

```bash
#!/bin/bash
# normalize-tts.sh INPUT [OUTPUT] [TARGET_LUFS]
INPUT="$1"
OUTPUT="${2:-${INPUT%.wav}.norm.wav}"
TARGET_LUFS="${3:--14}"

# Pass 1: Measure
STATS=$(ffmpeg -i "$INPUT" -af "loudnorm=I=${TARGET_LUFS}:TP=-1:LRA=11:print_format=json" -f null - 2>&1)
measured_I=$(echo "$STATS" | grep -o '"input_i" *: *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
measured_TP=$(echo "$STATS" | grep -o '"input_tp" *: *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
measured_LRA=$(echo "$STATS" | grep -o '"input_lra" *: *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
measured_thresh=$(echo "$STATS" | grep -o '"input_thresh" *: *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')
offset=$(echo "$STATS" | grep -o '"target_offset" *: *"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"')

# Pass 2: Apply
ffmpeg -y -i "$INPUT" \
  -af "loudnorm=I=${TARGET_LUFS}:TP=-1:LRA=11:measured_I=${measured_I}:measured_TP=${measured_TP}:measured_LRA=${measured_LRA}:measured_thresh=${measured_thresh}:offset=${offset}:linear=true" \
  -ar 48000 "$OUTPUT"
```

## 3. Real-Time Stream Monitoring

### LUFS monitoring (ffmpeg ebur128)

```bash
# Real-time LUFS of the Icecast stream (momentary/short-term/integrated + true peak)
ffmpeg -i http://YOUR_HOST:8000/stream -filter_complex ebur128=peak=true -f null - 2>&1 | grep -E "M:|S:|I:|Peak:"
```

Output: `t: 3.5 TARGET:-14 LUFS M: -16.2 S: -15.8 I: -15.5 LUFS LRA: 5.3 LU FTPK: -3.2 -3.1 dBFS`

### dBFS quick check (sox)

```bash
# 10-second sample from stream
ffmpeg -i http://YOUR_HOST:8000/stream -t 10 -f wav - 2>/dev/null | sox -t wav - -n stats
```

## 4. File Inspection

```bash
# Quick header info (instant, no decoding)
soxi track.flac
soxi -d track.flac          # just duration
soxi -r track.flac          # just sample rate

# Detailed JSON (for scripting)
ffprobe -v quiet -print_format json -show_format -show_streams track.flac

# Just duration in seconds
ffprobe -v quiet -show_entries format=duration -of csv=p=0 track.flac

# Check stream codec/bitrate
ffprobe -v quiet -print_format json -show_format -show_streams -timeout 5000000 http://YOUR_HOST:8000/stream

# ReplayGain tags
mediainfo --Inform="Audio;RG=%replay_gain_track_gain%" track.flac
```

## 5. Stream Health Monitoring

```bash
# Icecast JSON status (mount info, listeners, bitrate)
curl -s http://YOUR_HOST:8000/status-json.xsl | jq '.icestats.source'

# Simple up/down check
curl -sf -o /dev/null -m 5 http://YOUR_HOST:8000/stream && echo "UP" || echo "DOWN"

# Liquidsoap telnet (current track, queue status)
echo "request.on_air" | nc -q1 localhost 1234
echo "radio.remaining" | nc -q1 localhost 1234

# Validate stream audio integrity (10 seconds)
ffmpeg -i http://YOUR_HOST:8000/stream -t 10 -f null - 2>&1 | grep -i "error\|warning"
```

## 6. Audio Manipulation (sox)

```bash
# Peak normalize to -1 dBFS
sox input.wav output.wav norm -1

# Trim silence from beginning and end
sox input.wav output.wav silence 1 0.1 -50d reverse silence 1 0.1 -50d reverse

# Adjust volume by +6dB
sox input.wav output.wav vol 6dB

# Concatenate files
sox file1.wav file2.wav file3.wav combined.wav

# Fade in 2s, fade out 3s
sox input.wav output.wav fade t 2 0 3

# Convert to 16-bit 44.1kHz WAV
sox input.flac -b 16 -r 44100 output.wav

# RMS/peak stats
sox input.wav -n stats
```

## Gotchas

- sox `norm` is peak normalization (dBFS), NOT loudness normalization (LUFS). Use rsgain or ffmpeg loudnorm for perceptual loudness matching.
- ffmpeg loudnorm single-pass uses dynamic compression. Always use two-pass with `linear=true` for music.
- rsgain writes ReplayGain tags (non-destructive). Liquidsoap must be configured to read them.
- sox cannot encode MP3. Use ffmpeg for MP3 encoding.
- ffprobe hangs on unreachable streams. Use `-timeout 5000000` (5s in microseconds).
- Icecast `/status-json.xsl` returns `source` as object (1 mount) or array (multiple). Handle both.