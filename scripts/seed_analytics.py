#!/usr/bin/env python3
"""
Наполняет аналитику проведёнными вызовами.

Панель аналитики без данных выглядит пусто, и разрабатывать её невозможно.
Скрипт прогоняет через настоящий API несколько десятков вызовов с разным
качеством работы, поэтому данные получаются не выдуманные, а реальные —
просто быстро сгенерированные.

    python3 scripts/seed_analytics.py [сколько_вызовов]
"""
from __future__ import annotations

import json
import random
import sys
import urllib.request

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

TRAINEES = [
    ("aaaaaaaa-0000-0000-0000-000000000001", "Абрамова", 0.90),
    ("aaaaaaaa-0000-0000-0000-000000000002", "Валеев",   0.75),
    ("aaaaaaaa-0000-0000-0000-000000000003", "Гущина",   0.60),
    ("aaaaaaaa-0000-0000-0000-000000000004", "Дорохов",  0.82),
    ("aaaaaaaa-0000-0000-0000-000000000005", "Ерёмина",  0.68),
    ("aaaaaaaa-0000-0000-0000-000000000006", "Жарков",   0.52),
]

# (реплика, вероятность произнести при навыке 1.0)
SCRIPT = [
    ("Служба 112, что у вас случилось?",        0.95),
    ("Назовите адрес происшествия",             0.98),
    ("Какой номер квартиры и подъезд?",         0.80),
    ("Есть ли пострадавшие?",                   0.85),
    ("На балконе есть газовые баллоны?",        0.45),
    ("Вы можете выйти из квартиры?",            0.50),
    ("Как проехать во двор, машины стоят?",     0.25),
    ("Закройте дверь и намочите полотенце",     0.70),
]
RUDE = "Успокойтесь и не кричите на меня"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def run_one(trainee_id, skill):
    s = req("POST", "/api/sessions",
            {"trainee_id": trainee_id, "scenario_slug": "fire_apartment_01"})
    sid = s["session_id"]

    for text, base_p in SCRIPT:
        if random.random() < base_p * skill:
            req("POST", f"/api/sessions/{sid}/say", {"text": text})

    if random.random() < (1 - skill) * 0.5:
        req("POST", f"/api/sessions/{sid}/say", {"text": RUDE})

    req("POST", f"/api/sessions/{sid}/card",
        {"field": "common.ulica", "value": "Ленина"})

    services = ["01", "03"] if random.random() < 0.6 + skill * 0.35 else ["01"]
    req("POST", f"/api/sessions/{sid}/dispatch", {"services": services})
    return req("POST", f"/api/sessions/{sid}/finish")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 36
    try:
        req("GET", "/health")
    except Exception as e:
        print(f"API недоступен: {e}\nПоднимите стек: make up")
        return 1

    print(f"Генерирую {n} вызовов…")
    done = 0
    for i in range(n):
        tid, name, base = TRAINEES[i % len(TRAINEES)]
        # навык растёт от попытки к попытке — появится кривая обучения
        skill = min(0.98, base + (i // len(TRAINEES)) * 0.03 + random.uniform(-0.06, 0.06))
        try:
            r = run_one(tid, skill)
            done += 1
            mark = "зачёт " if r["passed"] else "незачёт"
            print(f"  {i+1:3}/{n}  {name:9} балл {r['total']:5.1f}  {mark}")
        except Exception as e:
            print(f"  {i+1:3}/{n}  {name:9} ошибка: {e}")

    print(f"\nГотово: {done} вызовов. Откройте http://localhost:3000/analytics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
