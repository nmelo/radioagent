# Agent Radio - Liquidsoap configuration
# Robust music playback with logging and blank detection

# --- Logging ---
settings.log.file := true
settings.log.file.path := "/tmp/agent-radio/liquidsoap.log"
settings.log.level := 3

settings.server.socket := true
settings.server.socket.path := environment.get(default="/tmp/agent-radio.sock", "LIQUIDSOAP_SOCKET_PATH")

# --- Music source ---
music_dir = environment.get(default="/opt/radioagent/music", "RADIO_MUSIC_DIR")
music = playlist(
  id="music",
  music_dir,
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
# After 5s of silence at -40dB threshold, skip to next track
music = blank.skip(max_blank=5., threshold=-40., track_sensitive=true, music)

# Normalize to stereo (handles mono files, prevents crossfade channel mismatch)
music = audio_to_stereo(music)

# Crossfade between tracks (reduced from 5s to 3s to limit stall window)
music = crossfade(duration=3.0, music)

# Music volume control (mute/unmute via socket: var.set music_volume = 0.0/1.0)
# Placed after blank.skip and crossfade so muting doesn't trigger skip logic
music_volume = interactive.float("music_volume", 1.0)
music = amplify(music_volume, music)

# Safety net: generate silence if source fails completely
music = mksafe(music)

# Detect when mksafe fallback is generating silence (entire pipeline down)
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

# --- Output to Icecast ---
output.icecast(
  %mp3(bitrate=192, samplerate=44100, stereo=true),
  host=environment.get(default="localhost", "ICECAST_HOST"),
  port=8000,
  password=environment.get(default="CHANGE_ME_source", "ICECAST_SOURCE_PASSWORD"),
  mount="/stream",
  fallible=true,
  radio
)
