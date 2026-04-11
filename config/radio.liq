# Agent Radio - Liquidsoap configuration
# Robust music playback with logging and blank detection

# --- Logging ---
# In Docker, set LIQUIDSOAP_LOG_TO_FILE=false so logs go to stdout (docker compose logs)
settings.log.file := (environment.get(default="true", "LIQUIDSOAP_LOG_TO_FILE") == "true")
settings.log.file.path := "/tmp/agent-radio/liquidsoap.log"
settings.log.level := 3

settings.server.socket := true
settings.server.socket.path := environment.get(default="/tmp/agent-radio.sock", "LIQUIDSOAP_SOCKET_PATH")

# --- Music source ---
# Brain's /next-track endpoint manages selection with recency-penalized shuffle.
# Liquidsoap requests the next track via curl; brain returns a file path.
# v2.2 API: request.dynamic takes a function returning a URI string.
brain_port = environment.get(default="8001", "BRAIN_PORT")
music = request.dynamic(id="music",
  fun () -> process.read("curl -sf http://localhost:#{brain_port}/next-track")
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

# Normalize volume across tracks using ReplayGain tags (no-op if tags absent)
enable_replaygain_metadata()

# Crossfade between tracks (reduced from 5s to 3s to limit stall window)
music = crossfade(duration=3.0, music)

# Music volume control (0.0-1.0 via socket: var.set music_volume = X)
# Default 0.19 = level 2, moderate background level
# Placed after blank.skip and crossfade so muting doesn't trigger skip logic
music_volume = interactive.float("music_volume", 0.19)
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
# User-controlled voice volume (0.0-1.0 via socket: var.set voice_volume = 0.85)
# Default 0.85 = level 4 "Loud", preserving approximate pre-existing behavior
voice_volume = interactive.float("voice_volume", 0.85)
voice = amplify(voice_volume, voice)
voice = amplify(3.5, voice)

# --- Ambient tones queue ---
tones = request.queue(id="tones")
# User-controlled tones volume (0.0-1.0 via socket: var.set tones_volume = X)
# Default 0.7 = level 3 "Normal"
tones_volume = interactive.float("tones_volume", 0.7)
tones = amplify(tones_volume, tones)
tones = amplify(2.0, tones)

# --- Mix: tones overlay on music, then duck combined under voice ---
# Tones use add() not smooth_add(): short clips (0.5-2s) get eaten by
# smooth_add's transition ramp. add() sums audio directly, which is the
# right semantic anyway — a brief chime shouldn't duck the music.
music_with_tones = add(normalize=false, [music, tones])
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
