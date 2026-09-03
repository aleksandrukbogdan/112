"""
Движок оценки.

Правило проекта: ИИ извлекает признаки, правила ставят баллы.
Здесь нет ни одного обращения к модели — только арифметика по событиям.

Оценка считается заново из ленты событий, поэтому смена профиля порогов
пересчитывает всю историю, не трогая исходные данные.
"""
from __future__ import annotations


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute(events: list[dict], scenario: dict, thresholds: dict) -> dict:
    checklist = scenario.get("checklist", [])
    timing = scenario.get("timing", {})

    # ---- разбор ленты -------------------------------------------------
    first_intent: dict[str, int] = {}
    forbidden: list[dict] = []
    pauses_own_turn = 0
    fillers = 0
    wpm_samples: list[float] = []
    operator_turns = 0
    facts = 0
    dispatch_ms: int | None = None
    services_ok = False
    call_end_ms = 0

    for e in events:
        k, p, t = e["kind"], e.get("payload") or {}, e["t_ms"]
        if k == "intent_detected":
            first_intent.setdefault(p.get("intent", ""), t)
        elif k == "operator_utterance":
            operator_turns += 1
            fillers += len(p.get("fillers") or [])
            if p.get("wpm"):
                wpm_samples.append(float(p["wpm"]))
        elif k == "fact_revealed":
            facts += 1
        elif k == "forbidden_phrase":
            forbidden.append(p)
        elif k == "pause_detected":
            if p.get("is_operator_turn"):
                pauses_own_turn += 1
        elif k == "card_dispatched":
            dispatch_ms = t
            services_ok = bool(p.get("is_correct"))
        elif k == "call_end":
            call_end_ms = t

    # ---- ось 1: протокол (60%) ---------------------------------------
    earned = 0
    possible = 0
    breakdown: dict[str, dict] = {}

    for item in checklist:
        possible += item["points"]
        hit = item["id"] in first_intent
        pts = item["points"] if hit else item.get("miss_penalty", 0)
        earned += pts
        breakdown[item["id"]] = {
            "label": item.get("label", item["id"]),
            "hit": hit,
            "points": pts,
            "at_ms": first_intent.get(item["id"]),
        }

    # маршрутизация — отдельный пункт, не из чек-листа
    possible += 20
    if services_ok:
        earned += 20
        breakdown["routed_correctly"] = {"label": "Верная маршрутизация", "hit": True, "points": 20}
    else:
        earned -= 20
        breakdown["routed_correctly"] = {"label": "Верная маршрутизация", "hit": False, "points": -20}

    for f in forbidden:
        pen = int(f.get("penalty", -10))
        earned += pen
        breakdown[f"forbidden::{f.get('label', 'фраза')}"] = {
            "label": f.get("label", "Запрещённая формулировка"),
            "hit": False,
            "points": pen,
        }

    protocol = _clamp(100.0 * earned / possible) if possible else 0.0

    # ---- ось 2: скорость (25%) ---------------------------------------
    # ВАЖНО: оценивается момент, когда оператор ЗАДАЛ вопрос,
    # а не когда получил ответ. Иначе оператор наказывается за панику заявителя.
    speed_pts, speed_max = 0.0, 0.0
    speed_detail = {}

    def _speed(name: str, actual_ms: int | None, target_sec: int, weight: float):
        nonlocal speed_pts, speed_max
        speed_max += weight
        if actual_ms is None:
            speed_detail[name] = {"actual_sec": None, "target_sec": target_sec, "score": 0.0}
            return
        actual = actual_ms / 1000.0
        ratio = target_sec / max(actual, 1.0)
        got = weight * _clamp(ratio, 0.0, 1.0)
        speed_pts += got
        speed_detail[name] = {
            "actual_sec": round(actual, 1),
            "target_sec": target_sec,
            "score": round(got / weight * 100, 1),
        }

    _speed("ask_address", first_intent.get("asked_address"),
           thresholds.get("time_to_ask_address_sec", timing.get("time_to_ask_address_sec", 25)), 35)
    _speed("ask_victims", first_intent.get("asked_victims"),
           thresholds.get("time_to_ask_victims_sec", timing.get("time_to_ask_victims_sec", 45)), 30)
    _speed("dispatch", dispatch_ms,
           thresholds.get("time_to_dispatch_sec", timing.get("time_to_dispatch_sec", 75)), 35)

    speed = _clamp(100.0 * speed_pts / speed_max) if speed_max else 0.0

    # ---- ось 3: манера (15%) -----------------------------------------
    composure = 100.0
    composure -= pauses_own_turn * 5      # только паузы на СВОЁМ ходу
    composure -= fillers * 2
    if wpm_samples:
        avg = sum(wpm_samples) / len(wpm_samples)
        lo = thresholds.get("speech_rate_min_wpm", 90)
        hi = thresholds.get("speech_rate_max_wpm", 190)
        if avg < lo or avg > hi:
            composure -= 10
    composure = _clamp(composure)

    # ---- итог ---------------------------------------------------------
    w = thresholds.get("weights", {"protocol": 0.60, "speed": 0.25, "composure": 0.15})
    total = protocol * w["protocol"] + speed * w["speed"] + composure * w["composure"]

    # ---- стоп-факторы --------------------------------------------------
    stop: list[str] = []
    if "asked_address" not in first_intent:
        stop.append("Адрес не запрошен")
    if dispatch_ms is None:
        stop.append("Карточка не отправлена")
    elif not services_ok:
        stop.append("Неверная маршрутизация")
    for f in forbidden:
        if int(f.get("penalty", 0)) <= -25:
            stop.append(f.get("label", "Запрещённая формулировка"))

    return {
        "protocol": round(protocol, 2),
        "speed": round(speed, 2),
        "composure": round(composure, 2),
        "total": round(total, 2),
        "stop_factors": stop,
        "passed": len(stop) == 0 and total >= 60,
        "breakdown": {
            "checklist": breakdown,
            "speed": speed_detail,
            "composure": {
                "pauses_own_turn": pauses_own_turn,
                "fillers": fillers,
                "avg_wpm": round(sum(wpm_samples) / len(wpm_samples), 1) if wpm_samples else None,
            },
            "stats": {
                "operator_turns": operator_turns,
                "facts_revealed": facts,
                "info_efficiency": round(facts / operator_turns, 2) if operator_turns else 0.0,
                "call_duration_sec": round(call_end_ms / 1000.0, 1) if call_end_ms else None,
                "dispatch_sec": round(dispatch_ms / 1000.0, 1) if dispatch_ms else None,
            },
            "weights": w,
        },
    }
