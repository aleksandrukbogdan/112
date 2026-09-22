"""
Голосовой сервис: распознавание (Vosk) и синтез (Piper).

Оба движка работают на CPU и без сети. Модели скачиваются
один раз при сборке образа и лежат внутри него.

  POST /asr   аудио (webm/ogg/wav) -> {"text": "..."}
  GET  /tts   ?text=...           -> audio/wav
"""
import json
import os
import subprocess
import tempfile
import wave

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response

VOSK_MODEL = os.environ.get("VOSK_MODEL", "/models/vosk")
PIPER_MODEL = os.environ.get("PIPER_MODEL", "/models/piper/ru_RU-irina-medium.onnx")

app = FastAPI(title="t112-voice")
_model = None


def model():
    global _model
    if _model is None:
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        _model = Model(VOSK_MODEL)
    return _model


@app.get("/health")
def health():
    return {"ok": os.path.isdir(VOSK_MODEL) and os.path.exists(PIPER_MODEL),
            "asr": os.path.isdir(VOSK_MODEL), "tts": os.path.exists(PIPER_MODEL)}


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


@app.get("/tts")
def tts(text: str):
    text = (text or "").strip()[:600]
    if not text:
        raise HTTPException(400, "пустой текст")
    with tempfile.NamedTemporaryFile(suffix=".wav") as out:
        r = subprocess.run(["piper", "--model", PIPER_MODEL, "--output_file", out.name],
                           input=text.encode(), capture_output=True)
        if r.returncode != 0:
            raise HTTPException(500, "синтез не удался: " + r.stderr.decode()[-200:])
        return Response(open(out.name, "rb").read(), media_type="audio/wav")
