"""
TTS-сервис: Silero (CPU) + постобработка ffmpeg + кэш предсинтеза.

Ключевая идея из плана (раздел D6): реплики сценария синтезируются ЗАРАНЕЕ
и во время звонка играются из кэша. Живой синтез — только для редких
свободных ответов. Поэтому /synth сначала смотрит в кэш.

Веса Silero кладутся в ./models/silero/ заранее — см. scripts/preload_models.py
"""
import os
import io
import time
import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path

import torch
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tts")

MODEL_PATH = os.getenv("SILERO_MODEL_PATH", "/models/silero/v4_ru.pt")
SPEAKER = os.getenv("SILERO_SPEAKER", "xenia")
VOICEBANK = Path(os.getenv("VOICEBANK_DIR", "/voicebank"))
SR = 48000

VOICEBANK.mkdir(parents=True, exist_ok=True)
torch.set_num_threads(int(os.getenv("TORCH_THREADS", "4")))

app = FastAPI(title="t112-tts")
_state = {"model": None}


class SynthRequest(BaseModel):
    text: str
    speaker: str | None = None
    emotion: str = "neutral"   # neutral | stressed | panic
    telephone: bool = True     # имитация телефонного тракта
    cache: bool = True


# Пресеты постобработки. Панику одним TTS не сделать — гибрид описан в плане (D6),
# здесь только акустическая часть: темп + питч.
PRESETS = {
    "neutral": {"atempo": 1.00, "pitch": 1.00},
    "stressed": {"atempo": 1.09, "pitch": 1.04},
    "panic": {"atempo": 1.18, "pitch": 1.08},
}


@app.on_event("startup")
def load_model():
    if not Path(MODEL_PATH).exists():
        log.error("Модель не найдена: %s — запустите scripts/preload_models.py", MODEL_PATH)
        return
    t0 = time.time()
    model = torch.package.PackageImporter(MODEL_PATH).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    _state["model"] = model
    log.info("Silero loaded in %.1fs from %s", time.time() - t0, MODEL_PATH)


@app.get("/health")
def health():
    return {"ok": _state["model"] is not None, "model_path": MODEL_PATH}


def _cache_key(req: SynthRequest) -> str:
    raw = f"{req.text}|{req.speaker or SPEAKER}|{req.emotion}|{req.telephone}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _postprocess(wav_bytes: bytes, emotion: str, telephone: bool) -> bytes:
    """Темп, питч, полоса телефонного канала, компрессия — всё через ffmpeg."""
    p = PRESETS.get(emotion, PRESETS["neutral"])
    chain = []

    if p["pitch"] != 1.0:
        # сдвиг питча = изменение sample rate + компенсация темпа
        chain.append(f"asetrate={SR}*{p['pitch']}")
        chain.append(f"aresample={SR}")
        chain.append(f"atempo={1/p['pitch']:.4f}")
    if p["atempo"] != 1.0:
        chain.append(f"atempo={p['atempo']:.4f}")
    if telephone:
        chain += ["highpass=f=300", "lowpass=f=3400",
                  "acompressor=threshold=-18dB:ratio=4"]

    if not chain:
        return wav_bytes

    with tempfile.NamedTemporaryFile(suffix=".wav") as src, \
         tempfile.NamedTemporaryFile(suffix=".wav") as dst:
        src.write(wav_bytes)
        src.flush()
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-i", src.name, "-af", ",".join(chain), dst.name]
        subprocess.run(cmd, check=True)
        return Path(dst.name).read_bytes()


@app.post("/synth")
def synth(req: SynthRequest):
    key = _cache_key(req)
    cached = VOICEBANK / f"{key}.wav"

    if req.cache and cached.exists():
        return Response(
            content=cached.read_bytes(),
            media_type="audio/wav",
            headers={"X-Cache": "hit", "X-Latency-Ms": "0"},
        )

    if _state["model"] is None:
        raise HTTPException(503, "Модель не загружена — см. scripts/preload_models.py")

    t0 = time.time()
    try:
        audio = _state["model"].apply_tts(
            text=req.text, speaker=req.speaker or SPEAKER, sample_rate=SR
        )
    except Exception as e:
        log.exception("synth failed")
        raise HTTPException(500, str(e))

    buf = io.BytesIO()
    sf.write(buf, audio.numpy(), SR, format="WAV", subtype="PCM_16")
    out = _postprocess(buf.getvalue(), req.emotion, req.telephone)

    if req.cache:
        cached.write_bytes(out)

    return Response(
        content=out,
        media_type="audio/wav",
        headers={"X-Cache": "miss", "X-Latency-Ms": str(int((time.time() - t0) * 1000))},
    )


@app.get("/voicebank/stats")
def stats():
    files = list(VOICEBANK.glob("*.wav"))
    return {"count": len(files), "bytes": sum(f.stat().st_size for f in files)}
