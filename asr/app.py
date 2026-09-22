"""
Распознавание речи: GigaAM v3 (SberDevices, MIT) на GPU.

Лучшая открытая модель для русской речи; на спонтанной и телефонной речи
заметно точнее Vosk. Работает и на CPU, но медленнее — поэтому основной
режим GPU, а Vosk в контейнере voice остаётся запасным.

  POST /asr     аудио (webm/ogg/wav/mp3) -> {"text", "words", "latency_ms", "engine"}
  GET  /health  -> состояние модели и устройства

Веса лежат в образе (/models/gigaam) — скачаны при сборке, сеть не нужна.
"""
import logging
import os
import subprocess
import tempfile
import time

from fastapi import FastAPI, File, HTTPException, UploadFile

log = logging.getLogger("asr")
logging.basicConfig(level=logging.INFO)

MODEL_NAME = os.environ.get("GIGAAM_MODEL", "v3_e2e_rnnt")
MODEL_DIR = os.environ.get("GIGAAM_DIR", "/models/gigaam")
DEVICE = os.environ.get("GIGAAM_DEVICE", "")          # пусто = cuda, если есть
MAX_SEC = 24.5                                         # у GigaAM лимит 25 с на реплику

app = FastAPI(title="t112-asr-gigaam")
S = {"model": None, "device": None, "error": None, "load_sec": None}


@app.on_event("startup")
def load():
    try:
        import gigaam
        import torch
        dev = DEVICE or ("cuda" if torch.cuda.is_available() else "cpu")
        t0 = time.time()
        S["model"] = gigaam.load_model(MODEL_NAME, device=dev, download_root=MODEL_DIR,
                                       fp16_encoder=(dev == "cuda"))
        S["device"] = dev
        S["load_sec"] = round(time.time() - t0, 1)
        if dev == "cuda":
            S["gpu"] = torch.cuda.get_device_name(0)
        log.info("GigaAM %s загружен на %s за %.1f с", MODEL_NAME, dev, S["load_sec"])
    except Exception as e:                              # не падаем: health покажет причину
        S["error"] = f"{type(e).__name__}: {e}"
        log.exception("GigaAM не загрузился")


@app.get("/health")
def health():
    return {"ok": S["model"] is not None, "engine": f"gigaam-{MODEL_NAME}",
            "device": S["device"], "gpu": S.get("gpu"), "load_sec": S["load_sec"],
            "error": S["error"]}


@app.post("/asr")
async def asr(audio: UploadFile = File(...)):
    if S["model"] is None:
        raise HTTPException(503, S["error"] or "модель не загружена")
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "пустое аудио")
    with tempfile.NamedTemporaryFile(suffix=".in") as src, \
         tempfile.NamedTemporaryFile(suffix=".wav") as dst:
        src.write(raw); src.flush()
        # в wav 16 кГц моно и обрезка до лимита модели
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src.name,
                            "-t", str(MAX_SEC), "-ar", "16000", "-ac", "1", dst.name],
                           capture_output=True)
        if r.returncode != 0:
            raise HTTPException(400, "не удалось декодировать аудио")
        t0 = time.time()
        try:
            res = S["model"].transcribe(dst.name, word_timestamps=True)
        except TypeError:
            res = S["model"].transcribe(dst.name)
        except Exception as e:
            raise HTTPException(500, f"распознавание не удалось: {e}")
    text = res if isinstance(res, str) else (getattr(res, "text", None)
                                             or getattr(res, "transcription", "") or "")
    words = [] if isinstance(res, str) else [
        {"text": w.text, "start": round(w.start, 2), "end": round(w.end, 2)}
        for w in (getattr(res, "words", None) or [])]
    text = text.strip()
    return {"text": text[:1].upper() + text[1:], "words": words,
            "latency_ms": int((time.time() - t0) * 1000),
            "engine": f"gigaam-{MODEL_NAME}", "device": S["device"]}
