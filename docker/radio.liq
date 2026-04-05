# Agent Radio - Liquidsoap configuration (Docker)
# Identical to radio.liq except host references use Docker service names.

# --- Logging ---
settings.log.file := true
settings.log.file.path := "/tmp/agent-radio/liquidsoap.log"
settings.log.level := 3

settings.server.socket := true
settings.server.socket.path := "/tmp/agent-radio/liquidsoap.sock"

# --- Music source ---
music = playlist(
  "/music",
  mode="random",
  reload_mode="watch",
  prefetch=2
)

# Log track changes
music = source.on_track(music, fun(m) -> begin
  filename = list.assoc(default="unknown", "filename", m)
  title = list.assoc(default="", "title", m)
  log.important("TRACK_START: file=#{filename} title=#{title}")
end)

# Log metadata updates
music = source.on_metadata(music, fun(m) -> begin
  title = list.assoc(default="", "title", m)
  artist = list.assoc(default="", "artist", m)
  filename = list.assoc(default="", "filename", m)
  log.important("METADATA: title=#{title} artist=#{artist} file=#{filename}")
end)

# Skip tracks that produce silence (failed decode, corrupt file)
music = blank.skip(max_blank=5., threshold=-40., track_sensitive=true, music)

# Normalize to stereo
music = audio_to_stereo(music)

# Crossfade between tracks
music = crossfade(duration=3.0, music)

# Music volume control
music_volume = interactive.float("music_volume", 1.0)
music = amplify(music_volume, music)

# Safety net: generate silence if source fails completely
music = mksafe(music)

# Detect when mksafe fallback is generating silence
music = blank.detect(
  max_blank=10.,
  threshold=-40.,
  { log.severe("BLANK_DETECTED: mksafe fallback active, music source may be down") },
  on_noise={ log.important("BLANK_RECOVERED: music source resumed") },
  music
)

# --- Voice announcement queue ---
voice = request.queue(id="voice")
voice = amplify(3.5, voice)

# --- Ambient tones queue ---
tones = request.queue(id="tones")
tones = amplify(2.0, tones)

# --- Mix: duck music under tones, then duck combined under voice ---
music_with_tones = smooth_add(normal=music, special=tones)
radio = smooth_add(normal=music_with_tones, special=voice)

# --- Output to Icecast (Docker service name) ---
output.icecast(
  %mp3(bitrate=192, samplerate=44100, stereo=true),
  host="icecast",
  port=8000,
  password="agent-radio-src",
  mount="/stream",
  fallible=true,
  radio
)
