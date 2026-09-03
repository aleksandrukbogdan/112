"""
ASR + распознавание эмоций на базе GigaAM.
Две модели в одном контейнере — экономим память GPU и время загрузки.

ВНИМАНИЕ: сигнатуру transcribe(word_timestamps=...) проверьте на своей версии
пакета — это делает smoke-тест. Если параметра нет, поднимите версию пакета
или переключитесь на faster-whisper (фолбэк заложен в план, раздел D2).
"""
import os
import time
import tempfile
import logging

from fastapi import FastAPI, UploadFile, File, HTTPException

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("asr")

ASR_NAME = os.getenv("GIGAAM_MODEL", "v3_e2e_rnnt")
EMO_NAME = os.getenv("GIGAAM_EMO_MODEL", "emo")

app = FastAPI(title="t112-asr")
_state = {"asr": None, "emo": None}


@app.on_event("startup")
def load_models():
    import gigaam

    t0 = time.time()
    _state["asr"] = gigaam.load_model(ASR_NAME)
    log.info("ASR %s loaded in %.1fs", ASR_NAME, time.time() - t0)

    t0 = time.time()
    _state["emo"] = gigaam.load_model(EMO_NAME)
    log.info("EMO %s loaded in %.1fs", EMO_NAME, time.time() - t0)


@app.get("/health")
def health():
    return {"ok": _state["asr"] is not None, "asr_model": ASR_NAME}


def _to_tmp(data: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), timestamps: bool = True):
    """Реплика оператора -> текст + пословные таймкоды."""
    path = _to_tmp(await file.read())
    try:
        t0 = time.time()
        try:
            res = _state["asr"].transcribe(path, word_timestamps=timestamps)
        except TypeError:
            # старая сигнатура без word_timestamps
            res = _state["asr"].transcribe(path)
        latency_ms = int((time.time() - t0) * 1000)

        if isinstance(res, str):
            return {"text": res, "words": [], "latency_ms": latency_ms}

        words = [
            {"start": w.start, "end": w.end, "text": w.text}
            for w in (getattr(res, "words", None) or [])
        ]
        text = getattr(res, "transcription", None) or getattr(res, "text", "")
        return {"text": text, "words": words, "latency_ms": latency_ms}

    except Exception as e:
        log.exception("transcribe failed")
        raise HTTPException(500, str(e))
    finally:
        os.unlink(path)


@app.post("/emotion")
async def emotion(file: UploadFile = File(...)):
    """Реплика оператора -> сырые вероятности эмоций. Без вердикта — это принципиально."""
    path = _to_tmp(await file.read())
    try:
        t0 = time.time()
        probs = _state["emo"].get_probs(path)
        return {"probs": probs, "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        log.exception("emotion failed")
        raise HTTPException(500, str(e))
    finally:
        os.unlink(path)
