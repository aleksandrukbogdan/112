"""Проверка маршрутизации речи: GigaAM → Vosk → текстовый режим. python3 scripts/test_voice.py"""
import os, sys, tempfile, threading, time
tmp = tempfile.mkdtemp()
from pathlib import Path as _P
os.chdir(_P(__file__).resolve().parent.parent)
os.environ.update(DATA_DIR="data", DB_PATH=f"{tmp}/t.db", BACKUP_DIR=f"{tmp}/bk", LLM_ENABLED="false",
                  ASR_URL="http://127.0.0.1:18091", VOICE_URL="http://127.0.0.1:18090")
sys.path.insert(0, ".")
import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
STATE = {"giga": True}
def mk(name):
    a = FastAPI()
    @a.get("/health")
    def h():
        if name == "giga": return {"ok": STATE["giga"], "gpu": "NVIDIA A100", "error": None if STATE["giga"] else "CUDA out of memory"}
        return {"ok": True, "asr": True, "tts": True}
    @a.post("/asr")
    async def r(audio: UploadFile = File(...)):
        if name == "giga" and not STATE["giga"]: raise HTTPException(503, "down")
        return {"text": f"ответ {name}", "latency_ms": 100}
    return a
srv = []
for name, port in (("giga", 18091), ("vosk", 18090)):
    s = uvicorn.Server(uvicorn.Config(mk(name), port=port, log_level="error"))
    threading.Thread(target=s.run, daemon=True).start(); srv.append(s)
time.sleep(2)
from fastapi.testclient import TestClient
from api.main import app
with TestClient(app) as c:
    tok = c.post("/api/login", json={"login": "trainee", "password": "trainee112"}).json()["token"]
    H = {"Authorization": "Bearer " + tok}
    f = lambda: {"audio": ("a.webm", b"x", "audio/webm")}
    v = c.get("/health").json()["voice"]
    print("1. оба живы    → распознаёт:", v["engine"], "| ответ:", c.post("/api/voice/asr", files=f(), headers=H).json()["text"])
    STATE["giga"] = False
    v = c.get("/health").json()["voice"]
    print("2. GigaAM упал → распознаёт:", v["engine"], "| ответ:", c.post("/api/voice/asr", files=f(), headers=H).json()["text"])
    print("   пояснение:", v["note"])
    for s in srv: s.should_exit = True
    time.sleep(1.5)
    v = c.get("/health").json()["voice"]
    ok3 = not v["asr"]
    print("3. оба мертвы  → asr:", v["asr"], "| код:", c.post("/api/voice/asr", files=f(), headers=H).status_code, "|", v["note"])
