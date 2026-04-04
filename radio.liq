# Agent Radio - Liquidsoap config (tracer bullet)

settings.server.socket := true
settings.server.socket.path := "/tmp/agent-radio.sock"

music = playlist("/opt/agent-radio/music", mode="random", reload_mode="watch")
voice = request.queue(id="voice")

radio = smooth_add(normal=music, special=voice)

output.icecast(
  %mp3(bitrate=128),
  host="localhost",
  port=8000,
  password="agent-radio-src",
  mount="/stream",
  radio
)
