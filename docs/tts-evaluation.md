# TTS Engine Evaluation

Last updated: 2026-04-04. Research for Phase 3 (Polish) TTS upgrade decision.

## Current Engine: Kokoro 82M

Kokoro is the Phase 1 default. Fast, small, CPU-capable, adequate quality for short announcements.

| Metric | Value |
|--------|-------|
| Parameters | 82M |
| Latency (10s clip, RTX 5080) | 46-62ms |
| VRAM | ~2-3 GB with torch |
| Voices | 54 built-in |
| Voice cloning | No |
| Emotion tags | No |
| License | Apache 2.0 |
| Install | pip install kokoro |
| TTS Arena ranking | #1 at time of selection |

**Strengths:** Sub-100ms generation. Runs on CPU as fallback. Tiny model, leaves GPU headroom for other workloads. Simple install, no heavy dependencies.

**Weaknesses:** Flat prosody on longer text. No voice cloning. No emotion control. Quality is adequate but not remarkable for expressive speech.

## Candidate: Orpheus 3B

Researched April 2026. Repo: github.com/canopyai/Orpheus-TTS

| Metric | Value |
|--------|-------|
| Parameters | 3B |
| Latency (10s clip, RTX 5080 est.) | 5-10 seconds |
| VRAM (FP8 via vLLM) | ~8.9 GB total |
| VRAM (GGUF Q8_0) | ~4 GB weights + runtime overhead |
| VRAM (GGUF Q4_K_M) | ~2.5 GB weights + runtime overhead |
| Voices | 8 English (tara, leah, jess, leo, dan, mia, zac, zoe) + 16 multilingual |
| Voice cloning | Yes, zero-shot from reference audio |
| Emotion tags | Yes: laugh, chuckle, sigh, cough, sniffle, groan, yawn, gasp |
| License | Apache 2.0 |
| Install | pip install orpheus-speech (requires vLLM) |
| Alternative runtime | Orpheus-FastAPI with llama-cpp-python (GGUF, lighter) |

**Strengths:** Noticeably better prosody and expressiveness. Emotion tags let announcements sound natural (a failure announcement can have a concerned tone). Zero-shot voice cloning from a few seconds of reference audio. Streaming/chunked output supported (~200ms time-to-first-audio).

**Weaknesses:** 100x slower than Kokoro (5-10s vs 50ms). Blows our <10s end-to-end SLA if TTS alone takes 5-10s. Development appears stalled (last commit Dec 2025, zero activity in 4 months, 122 open issues). vLLM dependency is heavy and may conflict with existing venv (recommend separate venv). The marketed smaller variants (1B, 400M, 150M) do not exist on HuggingFace; only the 3B is published.

**VRAM fit on RTX 5080 (16GB):** Yes. With Kokoro using ~2-3GB and Orpheus at FP8 using ~8.9GB, both could theoretically coexist but it would be tight. Practical approach: load one engine at a time, switch via config restart.

**API example (minimal):**
```python
from orpheus_tts import OrpheusModel
import wave

model = OrpheusModel(model_name="canopylabs/orpheus-tts-0.1-finetune-prod")

def render(text: str, output_path: str, voice: str = "tara") -> bool:
    try:
        chunks = model.generate_speech(prompt=text, voice=voice)
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            for chunk in chunks:
                wf.writeframes(chunk)
        return True
    except Exception:
        return False
```

**Verdict:** Keep as Phase 3 optional. The latency gap is too large for our SLA. The TTSEngine protocol supports engine switching via config (tts_engine: orpheus). Integration is straightforward but not worth prioritizing until we need richer voice expressiveness.

## Candidate: Voxtral TTS (Mistral)

Released March 2026. Repo: huggingface.co/mistralai/Voxtral-4B-TTS-2603

| Metric | Value |
|--------|-------|
| Parameters | 4B |
| Languages | 9 |
| Voice cloning | Yes, 3-second reference audio |
| License | CC BY-NC 4.0 (non-commercial only) |
| Quality | Beat ElevenLabs Flash v2.5 in 62.8% of blind evaluations |

**Strengths:** Best open-weights quality as of March 2026. 9 languages. Voice cloning from 3 seconds of audio. Streaming support. Backed by Mistral (well-funded, active development expected).

**Weaknesses:** Non-commercial license (CC BY-NC 4.0) is the blocker. If Agent Radio is ever used commercially or distributed, Voxtral cannot be included. 4B parameters means higher VRAM than Orpheus 3B. Latency numbers not yet benchmarked on consumer GPUs.

**Verdict:** Evaluate in Phase 3 if license permits. Quality is compelling but the non-commercial restriction may be a non-starter depending on project direction.

## Candidate: Fish Speech / Fish Audio S2

Active development as of March 2026. Repo: github.com/fishaudio/fish-speech

| Metric | Value |
|--------|-------|
| Languages | 80+ |
| Training data | 10M+ hours |
| Architecture | Dual-AR (two autoregressive stages) |
| Voice cloning | Yes |
| License | Apache 2.0 |
| Quality | Lowest WER on Seed-TTS Eval benchmark |

**Strengths:** Most actively developed open-source TTS project. Massive language coverage. Lowest word error rate on standard benchmarks. Apache 2.0 license. Commercial API available as fallback (fish.audio).

**Weaknesses:** Less documentation on local deployment compared to Kokoro/Orpheus. Model sizes and VRAM requirements need verification at evaluation time. Community is smaller than Kokoro's.

**Verdict:** Strong Phase 3 candidate. Actively maintained, permissive license, excellent benchmarks. Worth a head-to-head comparison with Orpheus on our hardware.

## Other Models (not recommended)

| Model | Why not |
|-------|---------|
| Parler TTS (HuggingFace, 880M/2.3B) | Interesting text-prompt voice control but not leading on quality. Apache 2.0. Worth watching but not a priority. |
| XTTS v2 (Coqui) | Coqui AI shut down Dec 2025. Community fork exists but no official support. Declining ecosystem. |
| Sesame CSM (1B) | Conversational focus, multi-speaker. Less impressive for single-speaker announcements. |
| Dia (Nari Labs, 1.6B) | Non-verbal cues (laughter, breathing). Newer, less mature than Orpheus. Worth watching. |

## Phase 3 Evaluation Plan

When Phase 3 begins:

1. **Benchmark on RTX 5080:** Run Kokoro, Orpheus (GGUF Q8_0), and Fish Speech on the same 10 test sentences. Measure: generation time, VRAM usage, output quality (subjective listening test).
2. **Latency test:** Can Orpheus or Fish Speech stay under 3 seconds for a 10-second clip with quantization? If yes, they fit our <10s SLA with margin.
3. **A/B listening test:** Play 10 announcements from each engine to the operator. Blind comparison. Is the quality difference audible and worth the latency cost?
4. **Integration test:** Swap tts_engine in config, verify TTSEngine protocol works with the new engine. Measure end-to-end latency (webhook POST to audible voice).
5. **Decision:** If a candidate beats Kokoro on quality without blowing the latency budget, make it the new default. Otherwise, keep Kokoro and offer alternatives as optional.

## Sources

- Orpheus-TTS GitHub: github.com/canopyai/Orpheus-TTS
- Orpheus 3B on HuggingFace: huggingface.co/canopylabs/orpheus-3b-0.1-ft
- Orpheus VRAM discussion: github.com/canopyai/Orpheus-TTS/issues/9
- Orpheus GGUF quantizations: huggingface.co/Mungert/orpheus-3b-0.1-ft-GGUF
- Orpheus-FastAPI (GGUF runtime): github.com/Lex-au/Orpheus-FastAPI
- Voxtral TTS announcement: mistral.ai/news/voxtral-tts
- Voxtral on HuggingFace: huggingface.co/mistralai/Voxtral-4B-TTS-2603
- Fish Speech GitHub: github.com/fishaudio/fish-speech
- TTS Arena V2 leaderboard: huggingface.co/spaces/tts-agi/tts-arena-v2
- 12 TTS models compared (Inferless): inferless.com/learn/comparing-different-text-to-speech---tts--models-part-2
- Best open-source TTS 2026 (BentoML): bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models