# -*- coding: utf-8 -*-
"""Проверка, что код F5 лежит в репозитории и в git не попадают веса.

Запускать из каталога 112 перед коммитом:
    python scripts/pack_f5.py
По SSH и scp скрипт ничего не отправляет. Веса уже на сервере в /data/models.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
NEED = [
    "tts/f5_engine.py",
    "tts/sound_manager.py",
    "tts/scene.py",
    "tts/tts_service.py",
    "tts/Dockerfile",
    "tts/requirements-docker.txt",
    "tts/voices_f5.json",
    "tts/__init__.py",
    "voice/app.py",
    "docker-compose.yml",
]
FORBIDDEN_NAMES = (".safetensors", ".onnx", ".pt")


def main() -> int:
    missing = [name for name in NEED if not (ROOT / name).exists()]
    if missing:
        print("MISSING:")
        print("\n".join(missing))
        return 1
    print("repo files: ok")
    print("weights stay on the server, not in git:")
    print("  /data/models/F5-TTS_RUSSIAN")
    print("  /data/models   (Vocos cache)")
    print("  /data/112/voice/Sound")
    print("  /data/112/voice/Voices")
    print("GPU 1 only. After git pull on the server:")
    print("  docker compose --profile gpu up -d --build tts voice")
    heavy = []
    for path in (ROOT / "tts").rglob("*"):
        if path.is_file() and path.suffix in FORBIDDEN_NAMES:
            heavy.append(str(path))
    if heavy:
        print("DO NOT COMMIT:")
        print("\n".join(heavy))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
