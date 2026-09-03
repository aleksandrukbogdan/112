#!/usr/bin/env python3
"""
Предсинтез голосового банка.

Зачем: живая цепочка ASR -> LLM -> TTS не укладывается в бюджет в 1 секунду.
Но реплики заявителя известны заранее — они лежат в сценарии. Значит их можно
синтезировать до занятия, обработать телефонным фильтром и сложить в кэш.
Во время вызова они играют мгновенно, а живой синтез остаётся только
для редких свободных ответов.

Побочная выгода: панические реплики можно переслушать и переозвучить офлайн,
а не надеяться на удачную генерацию в реальном времени.

Синтез идёт через /api/tts, а не напрямую в сервис TTS: так параметры
гарантированно совпадают с боевыми и кэш действительно попадает.

    python3 scripts/prewarm_voicebank.py
    python3 scripts/prewarm_voicebank.py --check    # только показать, что нужно
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

def _api_base() -> str:
    """Адрес API. Порт берём из .env, чтобы скрипты не ломались при его смене."""
    import os
    from pathlib import Path

    port = os.getenv("API_PORT")
    if not port:
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("API_PORT="):
                    port = line.split("=", 1)[1].strip()
                    break
    return f"http://localhost:{port or '21121'}"


API = _api_base()
ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "content" / "scenarios"


def collect(scenario: dict) -> list[tuple[str, str, str]]:
    """Все реплики заявителя из сценария: (текст, эмоция, откуда)."""
    out: list[tuple[str, str, str]] = []

    op = scenario.get("opening_line")
    if op and op.get("text"):
        out.append((op["text"], op.get("emotion", "panic"), "первая реплика"))

    for f in scenario.get("facts", []):
        line = f.get("line") or {}
        if line.get("text"):
            out.append((line["text"], line.get("emotion", "stressed"), f"факт {f['id']}"))

    for emotion, lines in (scenario.get("fallback_lines") or {}).items():
        for t in lines:
            out.append((t, emotion, f"заготовка ({emotion})"))

    # реплика при неуверенном распознавании — зашита в main.py
    out.append(("Что? Я не поняла... повторите, пожалуйста!", "panic", "переспрос"))

    # убираем дубли, сохраняя порядок
    seen, uniq = set(), []
    for text, emotion, src in out:
        key = (text, emotion)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((text, emotion, src))
    return uniq


def synth(text: str, emotion: str) -> tuple[bool, int, str]:
    """Возвращает (успех, байт, состояние кэша)."""
    qs = urllib.parse.urlencode({"text": text, "emotion": emotion})
    try:
        with urllib.request.urlopen(f"{API}/api/tts?{qs}", timeout=180) as r:
            data = r.read()
            return True, len(data), "ok"
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return False, 0, "TTS выключен"
        return False, 0, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, 0, str(e)[:60]


def main() -> int:
    check_only = "--check" in sys.argv

    files = sorted(SCENARIOS.glob("*.json"))
    if not files:
        print(f"Сценарии не найдены в {SCENARIOS}")
        return 1

    total: list[tuple[str, str, str, str]] = []
    for f in files:
        sc = json.loads(f.read_text(encoding="utf-8"))
        for text, emotion, src in collect(sc):
            total.append((sc["slug"], text, emotion, src))

    print(f"Сценариев: {len(files)}   реплик к синтезу: {len(total)}\n")

    if check_only:
        cur = None
        for slug, text, emotion, src in total:
            if slug != cur:
                cur = slug
                print(f"  {slug}")
            print(f"    [{emotion:8}] {src:22} {text[:58]}")
        print("\nЗапустите без --check, чтобы синтезировать.")
        return 0

    try:
        urllib.request.urlopen(f"{API}/health", timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"API недоступен: {e}\nПоднимите стек: make up-speech")
        return 1

    ok = failed = 0
    bytes_total = 0
    t0 = time.time()

    for i, (slug, text, emotion, src) in enumerate(total, 1):
        good, size, note = synth(text, emotion)
        if good:
            ok += 1
            bytes_total += size
            print(f"  {i:3}/{len(total)}  [{emotion:8}] {text[:52]:<52} {size // 1024:4} КБ")
        else:
            failed += 1
            print(f"  {i:3}/{len(total)}  СБОЙ [{emotion:8}] {text[:40]} — {note}")
            if note == "TTS выключен":
                print("\n  Включите TTS_ENABLED=true в .env и поднимите профиль speech:")
                print("      make up-speech")
                return 1

    dt = time.time() - t0
    print(f"\nГотово: {ok} реплик, {bytes_total / 1024 / 1024:.1f} МБ, {dt:.0f} с")
    if failed:
        print(f"Не синтезировано: {failed}")

    try:
        with urllib.request.urlopen("http://localhost:8082/voicebank/stats", timeout=10) as r:
            st = json.loads(r.read())
            print(f"В голосовом банке: {st['count']} файлов, "
                  f"{st['bytes'] / 1024 / 1024:.1f} МБ")
    except Exception:  # noqa: BLE001
        pass

    print("\nТеперь эти реплики играют из кэша за считанные миллисекунды.")
    print("Проверьте бюджет задержки: python3 scripts/smoke_latency.py -n 5")
    return 0


if __name__ == "__main__":
    sys.exit(main())
