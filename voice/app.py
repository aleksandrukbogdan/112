"""
Голосовой сервис: распознавание (Vosk) и синтез.

Синтез сначала уходит в F5-TTS (F5_URL). Если сервис не ответил,
остаётся Piper, чтобы занятие не осталось без звука.

  POST /asr   аудио (webm/ogg/wav) -> {"text": "..."}
  GET  /tts   ?text=...           -> audio/wav
"""
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response

VOSK_MODEL = os.environ.get("VOSK_MODEL", "/models/vosk")
PIPER_MODEL = os.environ.get("PIPER_MODEL", "/models/piper/ru_RU-irina-medium.onnx")
F5_URL = os.environ.get("F5_URL", "").strip().rstrip("/")

app = FastAPI(title="t112-voice")
_model = None


def model():
    global _model
    if _model is None:
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        _model = Model(VOSK_MODEL)
    return _model


def _f5_up() -> bool:
    if not F5_URL:
        return False
    try:
        with urllib.request.urlopen(F5_URL + "/health", timeout=0.8) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


@app.get("/health")
def health():
    f5 = _f5_up()
    piper = os.path.exists(PIPER_MODEL)
    return {"ok": os.path.isdir(VOSK_MODEL) and (f5 or piper),
            "asr": os.path.isdir(VOSK_MODEL), "tts": f5 or piper,
            "tts_engine": "f5" if f5 else "piper" if piper else None}


@app.post("/asr")
async def asr(audio: UploadFile = File(...)):
    from vosk import KaldiRecognizer
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "пустое аудио")
    with tempfile.NamedTemporaryFile(suffix=".bin") as src, \
         tempfile.NamedTemporaryFile(suffix=".wav") as dst:
        src.write(raw); src.flush()
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src.name,
                            "-ar", "16000", "-ac", "1", "-f", "wav", dst.name],
                           capture_output=True)
        if r.returncode != 0:
            raise HTTPException(400, "не удалось декодировать аудио")
        wf = wave.open(dst.name, "rb")
        rec = KaldiRecognizer(model(), wf.getframerate())
        parts = []
        while True:
            d = wf.readframes(4000)
            if not d:
                break
            if rec.AcceptWaveform(d):
                parts.append(json.loads(rec.Result()).get("text", ""))
        parts.append(json.loads(rec.FinalResult()).get("text", ""))
    text = " ".join(p for p in parts if p).strip()
    return {"text": text[:1].upper() + text[1:] if text else ""}


def _piper(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav") as out:
        r = subprocess.run(["piper", "--model", PIPER_MODEL, "--output_file", out.name],
                           input=text.encode(), capture_output=True)
        if r.returncode != 0:
            raise HTTPException(500, "синтез не удался: " + r.stderr.decode()[-200:])
        return open(out.name, "rb").read()


def _f5(text: str) -> bytes | None:
    if not F5_URL:
        return None
    query = urllib.parse.urlencode({"text": text})
    try:
        with urllib.request.urlopen(F5_URL + "/tts?" + query, timeout=35) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"[voice] F5 недоступен, остаётся Piper: {exc}")
        return None
    if len(data) > 44 and data[:4] == b"RIFF":
        return data
    print("[voice] F5 вернул не wav, остаётся Piper")
    return None


@app.get("/tts")
def tts(text: str):
    text = (text or "").strip()[:600]
    if not text:
        raise HTTPException(400, "пустой текст")
    data = _f5(text)
    if data is None:
        if not os.path.exists(PIPER_MODEL):
            raise HTTPException(503, "синтез не удался")
        data = _piper(text)
    return Response(data, media_type="audio/wav")
