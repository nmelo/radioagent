# Agent Radio - Liquidsoap configuration

settings.server.socket := true
settings.server.socket.path := "/tmp/agent-radio.sock"

# --- Music source ---
music = playlist("/srv/agent-radio/music", mode="random", reload_mode="watch")
music = crossfade(duration=5.0, music)
music = mksafe(music)

# --- Voice announcement queue ---
voice = request.queue(id="voice")
voice = amplify(3.5, voice)

# --- Mix: duck music under voice ---
radio = smooth_add(normal=music, special=voice)

# --- Output to Icecast ---
output.icecast(
  %mp3(bitrate=192, samplerate=44100, stereo=true),
  host="localhost",
  port=8000,
  password="agent-radio-src",
  mount="/stream",
  fallible=true,
  radio
)
