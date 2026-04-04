# Agent Radio - Liquidsoap configuration
# Widen of tracer: adds crossfade, empty-dir fallback, serial voice queue.

# --- Socket server for brain commands ---
settings.server.socket := true
settings.server.socket.path := "/tmp/agent-radio.sock"

# --- Music source ---
# Playlist in random mode, watches directory for new files.
# reload_mode="watch" picks up new tracks without restart.
music = playlist("/opt/agent-radio/music", mode="random", reload_mode="watch")

# Crossfade between tracks (5s overlap with fade-out/fade-in).
music = crossfade(duration=5.0, music)

# Fallback: if playlist is empty or all tracks fail, emit silence
# rather than crashing. mksafe() wraps with blank() as last resort.
music = mksafe(music)

# --- Voice announcement queue ---
# Brain pushes WAV paths via socket: voice.push /path/to/file.wav
# request.queue is serial by default: second push waits for first to finish.
voice = request.queue(id="voice")

# --- Mix: duck music under voice ---
# smooth_add reduces music volume when voice is active (cosine fade),
# restores when voice ends. No overlap between voice clips because
# request.queue processes items sequentially.
radio = smooth_add(normal=music, special=voice)

# --- Output to Icecast ---
output.icecast(
  %mp3(bitrate=128),
  host="localhost",
  port=8000,
  password="agent-radio-src",
  mount="/stream",
  radio
)
