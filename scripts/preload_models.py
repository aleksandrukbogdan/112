#!/usr/bin/env python3
"""
Предзагрузка весов моделей. САМОЕ ВАЖНОЕ, что нужно сделать заранее:
на площадке хакатона интернет обычно плохой, а веса качаются долго.

Запускать на хосте (нужен интернет):
    python3 scripts/preload_models.py

Скачивает в ./models/:
  - Silero TTS  (~60 МБ)
  - Silero VAD  (~2 МБ)
GigaAM тянется при первом старте контейнера в тот же volume ./models —
поэтому после первого docker compose up обязательно проверьте, что
./models непустая, и заархивируйте её как резервную копию.
"""
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

DOWNLOADS = [
    # Silero TTS. v4_ru — проверенный вариант.
    # ВНИМАНИЕ ПО ЛИЦЕНЗИИ: основной репозиторий Silero под CC-BY-NC 4.0.
    # Для коммерческого использования нужны MIT-модели (v5_cis_base).
    # Для хакатона годится любая, но в таблице лицензий это указать.
    ("https://models.silero.ai/models/tts/ru/v4_ru.pt", "silero/v4_ru.pt"),
    # Silero VAD — MIT, без оговорок
    ("https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
     "silero/silero_vad.onnx"),
]


def fetch(url: str, rel: str) -> None:
    dst = MODELS / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        print(f"  уже есть: {rel} ({dst.stat().st_size // 1024} КБ)")
        return
    print(f"  качаю:    {rel} <- {url}")
    try:
        urllib.request.urlretrieve(url, dst)
        print(f"  готово:   {rel} ({dst.stat().st_size // 1024} КБ)")
    except Exception as e:
        print(f"  ОШИБКА:   {rel}: {e}", file=sys.stderr)


def main() -> None:
    print(f"Каталог моделей: {MODELS}")
    MODELS.mkdir(parents=True, exist_ok=True)
    for url, rel in DOWNLOADS:
        fetch(url, rel)
    print()
    print("Дальше: docker compose up -d --build")
    print("GigaAM скачается сам при первом старте контейнера asr (это долго,")
    print("следите за docker compose logs -f asr).")


if __name__ == "__main__":
    main()
