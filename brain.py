"""Agent Radio brain - tracer bullet. Webhook to TTS to Liquidsoap."""

import socket
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
import yaml
from fastapi import FastAPI
from kokoro import KPipeline
from pydantic import BaseModel

# Load config
with open(Path(__file__).parent / "config.yaml") as f:
    config = yaml.safe_load(f)

# Init Kokoro TTS pipeline (stays warm across requests)
print("Loading Kokoro TTS pipeline...")
t0 = time.time()
tts = KPipeline(lang_code="a")
# Warm up with a throwaway synthesis
for _ in tts("warm up", voice=config["tts_voice"]):
    pass
print(f"TTS ready in {time.time() - t0:.1f}s")

WAV_DIR = Path("/tmp/agent-radio")
WAV_DIR.mkdir(exist_ok=True)

counter = 0
app = FastAPI()


class AnnounceRequest(BaseModel):
    detail: str


def push_to_liquidsoap(wav_path: Path) -> bool:
    """Push a WAV file path to Liquidsoap's voice queue via Unix socket."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(config["liquidsoap_socket"])
        s.sendall(f"voice.push {wav_path}\r\n".encode())
        # Read response until END
        resp = b""
        while b"END" not in resp:
            chunk = s.recv(1024)
            if not chunk:
                break
            resp += chunk
        s.close()
        decoded = resp.decode().strip()
        print(f"Liquidsoap response: {decoded}")
        return True
    except Exception as e:
        print(f"Socket push failed: {e}")
        return False


@app.post("/announce")
def announce(req: AnnounceRequest):
    global counter
    counter += 1

    # Render TTS
    wav_path = WAV_DIR / f"announce_{counter:04d}.wav"
    t0 = time.time()
    chunks = []
    for _, _, audio in tts(req.detail, voice=config["tts_voice"]):
        chunks.append(audio)
    combined = np.concatenate(chunks)
    sf.write(str(wav_path), combined, 24000)
    elapsed = time.time() - t0
    duration = len(combined) / 24000

    print(f"TTS: '{req.detail}' -> {wav_path.name} ({duration:.1f}s audio in {elapsed:.3f}s)")

    # Push to Liquidsoap
    push_to_liquidsoap(wav_path)

    return {"status": "queued", "duration": round(duration, 1)}


if __name__ == "__main__":
    print(f"Agent Radio brain on port {config['webhook_port']}")
    uvicorn.run(app, host="0.0.0.0", port=config["webhook_port"])
