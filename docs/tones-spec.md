# Ambient Tones Spec

A third audio channel for Agent Radio: short sound effects that layer under music at low volume, conveying event information without interrupting thought. The audio equivalent of Weiser's dangling string.

## The Idea

After a day of listening, the operator stops consciously hearing the tones. They become peripheral. A rising chime means someone picked up work. A resolved chord means something shipped. A brief dissonance means something broke. The operator knows the rhythm of the session without looking at anything or processing words. The tones are the texture of productive work.

When the tones stop, the silence communicates too: nobody is working.

## Calm Tech Principles

These principles from Weiser/Brown govern every design decision below:

1. **Peripheral, not central.** Tones must not demand attention. Volume at 20-30% of music. Duration under 2 seconds. No ducking. They layer under the music like ambient room sounds.
2. **Learned, not explained.** The operator doesn't need a legend. After hearing a rising chime every time an agent starts, the association forms naturally. The sounds are chosen for emotional congruence (rising = beginning, resolving = completion) so the mapping feels obvious even before it's learned.
3. **Less is more.** Not every event gets a tone. The vocabulary is small (8-10 sounds) and the sounds are simple. Adding more tones or longer sounds degrades the calm.
4. **The operator controls the periphery.** Tones can be toggled off independently. The three-layer control model becomes: Stream (transport), Music (play/pause), Voice (announcements), Tones (ambient events).

## Event-to-Tone Mapping

### Sound Vocabulary

Each tone has an emotional shape that maps to its meaning. The operator never needs to memorize this table; the associations are intuitive.

| Event Kind | Tone Name | Sound Description | Duration | Why This Sound |
|-----------|-----------|-------------------|----------|----------------|
| `*.started` | rise | Two ascending notes (C5, G5). Clean sine wave with soft attack and quick decay. | 0.8s | Rising interval = beginning, energy, opening |
| `*.completed` | resolve | Major chord (C4, E4, G4) fading together. Warm, satisfying. | 1.2s | Resolution = closure, completion, satisfaction |
| `*.failed` | dissonant | Minor second interval (E4, F4). Brief, slightly tense. Not alarming, just "off." | 0.6s | Dissonance = something wrong, needs attention |
| `*.stuck` | pulse | Single low note (G3) with tremolo (volume oscillation). Sounds like a slow heartbeat. | 1.5s | Pulsing = waiting, unresolved, ongoing |
| `*.idle` | hum | Single soft tone (C3), very quiet, fades quickly. Barely there. | 0.5s | Low quiet hum = dormant, resting, backgrounded |
| `*.stopped` | descend | Two descending notes (G5, C5). Mirror of "rise." | 0.8s | Falling interval = ending, stepping away |
| `deploy.*` | bell | Bright bell-like tone, higher register (C6). Clear but brief. | 1.0s | Bell = announcement without words, something notable happened |
| `milestone.*` | chord_long | Rich major chord (C4, E4, G4, C5) with longer sustain. | 2.0s | Fuller sound = bigger event, worth a moment of awareness |

### Events That Don't Get Tones

| Event Kind | Why No Tone |
|-----------|-------------|
| `custom` | Manual announcements. Voice-only; adding a tone would feel redundant. |
| `*.message` | Chat messages between agents. Too frequent, too low-signal. |
| Any suppressed kind | If the brain suppresses it for voice, check the tone mapping separately. Tones have their own routing. |

### Event Routing Matrix

When the brain receives a webhook event, it routes to voice, tones, or both based on event kind and mute states:

| Event Kind | Voice | Tone | Rationale |
|-----------|-------|------|-----------|
| `*.completed` | Yes | resolve | Important enough for both channels |
| `*.failed` | Yes | dissonant | Critical. Voice explains, tone alerts. |
| `*.stuck` | Yes | pulse | Important. Voice explains, tone signals. |
| `*.started` | No | rise | Too frequent for voice. Perfect as peripheral tone. |
| `*.stopped` | No | descend | Too frequent for voice. Brief tone is sufficient. |
| `*.idle` | No | hum | Currently suppressed for voice. Gains audio presence as a near-silent tone. |
| `deploy.*` | Yes | bell | Milestone. Both channels. |
| `milestone.*` | Yes | chord_long | Major milestone. Both channels. |
| `custom` | Yes | No | Manual text. Voice only. |
| `*.message` | No | No | Suppressed in both channels. |

Key insight: tones unlock audio presence for events that are currently suppressed (`*.started`, `*.stopped`, `*.idle`). The binary "voice or nothing" becomes "voice, tone, both, or nothing."

### Independent Mute States

Voice and tones are independently mutable:

| Voice | Tones | What the Operator Hears |
|-------|-------|------------------------|
| On | On | Full experience: music + spoken announcements + ambient tones |
| On | Off | Music + spoken announcements only (current behavior) |
| Off | On | Music + ambient tones only (text-only wire feed, no voice, tones convey rhythm) |
| Off | Off | Music only (or silence if music also muted) |

The "voice off, tones on" combination is particularly interesting: the operator hears the session's rhythm without interruption. Tones become the primary information channel. Wire feed shows text for details.

## Liquidsoap Integration

### New Source: Tones Request Queue

```liquidsoap
# Third source: ambient tones
tones = request.queue(id="tones")
tones = amplify(0.25, tones)  # 25% of music volume
```

### Mixing Chain

```liquidsoap
# Layer tones on music (add = no ducking, tones sit under music)
music_with_tones = add([music, tones])

# Voice ducks the combined music+tones channel
radio = smooth_add(normal=music_with_tones, special=voice)
```

This means:
- Tones play alongside music at low volume. No ducking, no interruption.
- When a voice announcement plays, smooth_add ducks the entire music+tones channel. This is correct: you don't want tones competing with voice.
- Tones that arrive during a voice announcement are ducked to near-silence. They're already quiet (25% volume), so ducked they're essentially inaudible. Fine. The brain doesn't need to coordinate timing.

### Full Updated radio.liq Mixing Section

Replace lines 62-67 of the current radio.liq:

```liquidsoap
# --- Voice announcement queue ---
voice = request.queue(id="voice")
voice = amplify(3.5, voice)

# --- Ambient tones queue ---
tones = request.queue(id="tones")
tones = amplify(0.25, tones)

# --- Mix: layer tones on music, duck combined under voice ---
music_with_tones = add([music, tones])
radio = smooth_add(normal=music_with_tones, special=voice)
```

## Brain Integration

### New Endpoint: POST /tone

```
POST /tone
Content-Type: application/json

{"kind": "agent.completed"}
```

Response: `{"status": "ok", "tone": "resolve"}` or `{"status": "skipped", "reason": "muted"}` or `{"status": "skipped", "reason": "no mapping"}`

The brain:
1. Looks up the tone name from the event kind (using the mapping table above)
2. If no mapping exists, returns `skipped`
3. If tones are muted, returns `skipped`
4. Resolves the tone name to a WAV file path from the tone library directory
5. Pushes the WAV to Liquidsoap's `tones` request queue via socket (`tones.push /path/to/resolve.wav`)
6. Returns `ok`

### Webhook Pipeline Change

The existing `/announce` endpoint currently routes events to voice only. With tones, the pipeline becomes:

```
POST /announce arrives
  -> Script generator (suppress check, template, clean)
  -> If not suppressed for voice AND voice not muted:
       -> TTS render -> WAV validate -> voice queue push
  -> Tone router (separate from voice suppression):
       -> If event kind has a tone mapping AND tones not muted:
            -> Push tone WAV to tones queue
  -> Record in history, broadcast SSE (always, regardless of mute states)
```

Voice and tone routing are independent. An event can trigger both, either, or neither.

### No Rate Limiting for Tones

Tones are under 2 seconds and extremely quiet. They don't compete for attention the way voice announcements do. No rate limiter is needed. If 5 agents complete work in 10 seconds, the operator hears 5 quick resolved chords layered over the music. This is fine; it communicates "burst of completions" which is useful information.

However, if 50 tones arrive in 1 second (pathological case), they'd stack and distort. Simple guard: max 1 tone per second per event kind. Drop excess silently.

### Tone Mute State

Add to brain.py alongside `music_muted` and `announcements_muted`:
- `tones_muted: bool = False`
- `POST /mute-tones` and `POST /unmute-tones`
- SSE event: `tones-mute` with `{"muted": bool}`
- Include `tones_muted` in `GET /now-playing` response

### Config Additions

```yaml
# Tones
tones_enabled: true
tones_dir: /opt/agent-radio/tones    # Directory containing tone WAV files
tones_volume: 0.25                    # Liquidsoap amplify factor (0.0-1.0)
```

## Sound Library

### Directory Structure

```
tones/
  rise.wav          # *.started
  resolve.wav       # *.completed
  dissonant.wav     # *.failed
  pulse.wav         # *.stuck
  hum.wav           # *.idle
  descend.wav       # *.stopped
  bell.wav          # deploy.*
  chord_long.wav    # milestone.*
```

### Sound Specifications

All tones share these properties:
- Format: WAV, 44.1kHz, 16-bit, mono (Liquidsoap handles stereo conversion)
- Peak level: -6 dBFS (headroom for mixing; Liquidsoap amplify(0.25) brings them to ~-18 dBFS relative to music)
- Attack: soft (10-50ms fade-in). No hard transients. Tones should not startle.
- Decay: natural fade to silence. No abrupt cutoff.
- No reverb in the source file. Liquidsoap and the music provide the ambient space.

### Generating Tones with sox

The tone library can be synthesized entirely with sox. No need for external sound packs.

```bash
#!/bin/bash
# generate-tones.sh - Create the Agent Radio tone library
set -euo pipefail
DIR="${1:-./tones}"
mkdir -p "$DIR"

# rise: C5 (523Hz) -> G5 (784Hz), ascending fifth
sox -n "$DIR/rise.wav" \
  synth 0.4 sine 523.25 fade t 0.02 0.4 0.15 : \
  synth 0.4 sine 783.99 fade t 0.02 0.4 0.2 \
  norm -6

# resolve: C4+E4+G4 major chord, warm fade
sox -n "$DIR/resolve.wav" \
  synth 1.2 sine 261.63 sine 329.63 sine 392.00 \
  fade t 0.05 1.2 0.6 \
  norm -6

# dissonant: E4+F4 minor second, brief tension
sox -n "$DIR/dissonant.wav" \
  synth 0.6 sine 329.63 sine 349.23 \
  fade t 0.02 0.6 0.3 \
  norm -6

# pulse: G3 with tremolo (volume oscillation at 2Hz)
sox -n "$DIR/pulse.wav" \
  synth 1.5 sine 196.00 tremolo 2 50 \
  fade t 0.05 1.5 0.4 \
  norm -6

# hum: C3 very quiet, quick fade
sox -n "$DIR/hum.wav" \
  synth 0.5 sine 130.81 \
  fade t 0.05 0.5 0.3 \
  norm -12

# descend: G5 (784Hz) -> C5 (523Hz), descending fifth
sox -n "$DIR/descend.wav" \
  synth 0.4 sine 783.99 fade t 0.02 0.4 0.15 : \
  synth 0.4 sine 523.25 fade t 0.02 0.4 0.2 \
  norm -6

# bell: C6 bright, bell-like with harmonics
sox -n "$DIR/bell.wav" \
  synth 1.0 sine 1046.50 sine 2093.00 sine 3139.50 \
  fade t 0.01 1.0 0.7 \
  norm -6

# chord_long: C4+E4+G4+C5 full major chord, longer sustain
sox -n "$DIR/chord_long.wav" \
  synth 2.0 sine 261.63 sine 329.63 sine 392.00 sine 523.25 \
  fade t 0.05 2.0 1.0 \
  norm -6

echo "Generated $(ls "$DIR"/*.wav | wc -l) tone files in $DIR"
```

These are starting points. Pure sine waves are clean but thin. Improvements for later:
- Triangle or soft-saw waves for warmer timbre
- Slight detuning between chord notes for natural shimmer
- ADSR envelopes (numpy script) for more organic attack/decay shapes
- Replace with curated CC0 sounds from freesound.org if the synthetic tones feel too clinical

The sox approach is good enough to test the concept. If the operator likes the feature, invest in richer sounds.

## Dashboard Integration

### Fourth Channel Toggle

The CHANNELS panel gains a third column (or on mobile, a third row):

```
+----------------------------+-----------------------------+-------------------+
|  MUSIC                     |  VOICE                      |  TONES            |
|  [|| PAUSE]  [>> SKIP]     |  [(()) ON]                  |  [(~) ON]         |
+----------------------------+-----------------------------+-------------------+
```

The tones toggle:
- ON: tones play normally. Toggle shows "(~) ON" with amber accent.
- OFF: tones suppressed. Toggle shows "(~) OFF" with dim text.
- Icon: "(~)" suggesting a sound wave or vibration.

SSE integration: listen for `tones-mute` events to sync state.

### Tone Activity Indicator (Optional)

A subtle visual indicator in the wire feed or header when a tone plays. Not a full typing animation (that's for voice), just a brief flash or pulse. A small waveform icon that glows amber for 0.5 seconds when a tone fires. This gives the "multi-sensory peripheralization" the Weiser paper recommends: hear the tone AND see a brief visual cue.

This is optional because it risks adding visual noise. Test without it first.

## Edge Cases

- **Tone WAV file missing:** Log warning, skip the tone. Don't crash.
- **Tones directory doesn't exist:** Log warning at startup, disable tones. Continue without them.
- **Liquidsoap tones queue not configured:** Brain detects this when the first `tones.push` fails. Disable tones for the session, log warning.
- **Tone + voice arrive simultaneously:** Both push to their respective queues. Liquidsoap handles mixing. The tone is ducked by smooth_add along with the music. Barely audible during voice, which is correct.
- **Rapid tones (burst of events):** They stack in the Liquidsoap queue. Multiple short tones playing in quick succession sounds like a burst of activity, which is accurate. Guard: max 1 per second per event kind.
- **All three channels muted + stream connected:** Silent stream. Wire feed shows text. Operator can unmute any channel independently.
- **Tone arrives while disconnected from dashboard:** Brain pushes to Liquidsoap regardless. The stream has the tone whether or not the dashboard is open. Dashboard mute state only affects future tones.

## How to Verify

1. Generate the tone library with the sox script
2. Start the radio with the updated radio.liq (add tones queue and mixing chain)
3. Push a tone manually: `echo 'tones.push /opt/agent-radio/tones/resolve.wav' | socat - UNIX-CONNECT:/tmp/agent-radio.sock`
4. Listen: you should hear a quiet chord layered under the music. No ducking.
5. Push a voice announcement at the same time: the tone should duck with the music.
6. POST /announce with kind "agent.started": hear the "rise" tone but no voice.
7. POST /announce with kind "agent.completed": hear both the "resolve" tone and the voice announcement.
8. Toggle tones off in dashboard. POST another event. No tone heard, voice still works.
9. Run for 30 minutes with agents active. The tones should fade into the background. If you're consciously noticing every tone, the volume is too high.

## Test 9 Is the Real Test

If after 30 minutes the operator is still consciously hearing every tone, something is wrong. Either the volume is too high, the tones are too long, or there are too many event types mapped. Reduce until they disappear into the music. The goal is peripheral texture, not notification.

The calibration question: "Did you notice the tones?" The ideal answer is "Not really, but I knew eng1 was busy."