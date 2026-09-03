#!/usr/bin/env python3
"""
Сквозной прогон вызова через работающий API.

Проверяет всю цепочку целиком: сессия -> диалог -> раскрытие фактов ->
карточка -> маршрутизация -> оценка -> разбор.

Запускать после `make up`:
    python3 scripts/smoke_call.py

Ничего, кроме стандартной библиотеки, не требует.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
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
TRAINEE = "aaaaaaaa-0000-0000-0000-000000000001"
SCENARIO = "fire_apartment_01"

OK, FAIL = "  ок   ", "  СБОЙ "
failures: list[str] = []


def req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"{OK if cond else FAIL}{name}{(' — ' + detail) if detail else ''}")
    if not cond:
        failures.append(name)
    return cond


def main() -> int:
    print("=" * 62)
    print("ШАГ 1. Доступность сервисов")
    print("=" * 62)
    try:
        h = req("GET", "/health")
    except urllib.error.URLError as e:
        print(f"{FAIL}API недоступен: {e}")
        print("\n  Поднимите стек: make up")
        return 1

    check("API отвечает", True)
    check("База данных доступна", h["checks"]["db"] is True)
    for name, key in (("Языковая модель", "llm"), ("Распознавание речи", "asr"),
                      ("Синтез речи", "tts")):
        st = h["checks"].get(key, {})
        state = st.get("ok")
        note = "выключен" if state is None else ("готов" if state else st.get("error", "недоступен"))
        print(f"  инфо  {name}: {note}")

    print("\n" + "=" * 62)
    print("ШАГ 2. Сценарии загружены")
    print("=" * 62)
    sc = req("GET", "/api/scenarios")
    check("Сценарии есть в базе", len(sc) > 0, f"найдено {len(sc)}")
    check("Эталонный сценарий на месте", any(s["slug"] == SCENARIO for s in sc))

    print("\n" + "=" * 62)
    print("ШАГ 3. Начало вызова")
    print("=" * 62)
    s = req("POST", "/api/sessions", {"trainee_id": TRAINEE, "scenario_slug": SCENARIO})
    sid = s["session_id"]
    print(f"  инфо  идентификатор сессии: {sid}")
    print(f"  инфо  заявитель: {s['opening']['text']}")
    check("Сессия создана", bool(sid))
    check("Заявитель начал разговор", bool(s["opening"]["text"]))

    print("\n" + "=" * 62)
    print("ШАГ 4. Диалог и раскрытие сведений")
    print("=" * 62)

    def say(text: str, expect_fact: str | None = None) -> dict:
        r = req("POST", f"/api/sessions/{sid}/say", {"text": text})
        got = r["revealed"]
        print(f"  --> {text}")
        print(f"      интенты: {r['intents']['intents']} ({r['intents']['method']})")
        print(f"      заявитель: {r['caller']['text']}")
        if expect_fact:
            check(f"Раскрыт факт {expect_fact}", expect_fact in got, f"получено {got}")
        return r

    r = say("Служба 112, что у вас случилось?")
    r = say("Назовите адрес происшествия", "f_street")
    r = say("Какой номер квартиры и подъезд?", "f_flat")
    r = say("Есть ли пострадавшие?", "f_child")
    r = say("На балконе есть газовые баллоны?", "f_gas")
    r = say("Вы можете выйти из квартиры?", "f_evac")
    r = say("Закройте дверь и намочите полотенце")

    m = r["metrics"]
    check("Сведения накапливаются", m["facts_revealed"] >= 5,
          f"{m['facts_revealed']} из {m['facts_total']}")
    check("Момент вопроса об адресе зафиксирован", m["ask_address_ms"] is not None)

    print("\n" + "=" * 62)
    print("ШАГ 5. Защита от выдумывания фактов")
    print("=" * 62)
    s2 = req("POST", "/api/sessions", {"trainee_id": TRAINEE, "scenario_slug": SCENARIO})
    sid2 = s2["session_id"]
    r2 = req("POST", f"/api/sessions/{sid2}/say", {"text": "Здравствуйте, слушаю вас"})
    check("Без вопроса адрес не выдаётся", "f_street" not in r2["revealed"],
          f"раскрыто: {r2['revealed'] or 'ничего'}")
    check("Заявитель адрес не назвал", "Ленина" not in r2["caller"]["text"],
          r2["caller"]["text"][:50])
    req("POST", f"/api/sessions/{sid2}/finish")

    print("\n" + "=" * 62)
    print("ШАГ 6. Карточка происшествия")
    print("=" * 62)
    c1 = req("POST", f"/api/sessions/{sid}/card",
             {"field": "common.ulica", "value": "Ленина"})
    check("Верное значение принято", c1["is_correct"] is True)
    c2 = req("POST", f"/api/sessions/{sid}/card",
             {"field": "common.kvartira", "value": "12"})
    check("Ошибочное значение помечено", c2["is_correct"] is False,
          "введено 12 вместо 47")
    req("POST", f"/api/sessions/{sid}/card", {"field": "common.kvartira", "value": "47"})

    print("\n" + "=" * 62)
    print("ШАГ 7. Маршрутизация и оценка")
    print("=" * 62)
    d = req("POST", f"/api/sessions/{sid}/dispatch", {"services": ["01", "03"]})
    check("Службы выбраны верно", d["is_correct"] is True)

    res = req("POST", f"/api/sessions/{sid}/finish")
    print(f"  инфо  протокол {res['protocol']} · скорость {res['speed']} "
          f"· манера {res['composure']} · ИТОГ {res['total']}")
    check("Оценка посчитана", res["total"] > 0)
    check("Стоп-факторов нет", not res["stop_factors"], str(res["stop_factors"]))
    check("Вызов зачтён", res["passed"] is True)

    hit = [k for k, v in res["breakdown"]["checklist"].items() if v["hit"]]
    check("Чек-лист заполнен", len(hit) >= 6, f"выполнено пунктов: {len(hit)}")

    print("\n" + "=" * 62)
    print("ШАГ 8. Лента событий")
    print("=" * 62)
    rep = req("GET", f"/api/sessions/{sid}/report")
    kinds = {e["kind"] for e in rep["timeline"]}
    check("Лента событий записана", len(rep["timeline"]) > 10,
          f"{len(rep['timeline'])} событий")
    for k in ("call_start", "operator_utterance", "intent_detected",
              "fact_revealed", "card_dispatched", "call_end"):
        check(f"Событие {k} есть в ленте", k in kinds)

    print("\n" + "=" * 62)
    print("ШАГ 9. Протокол в PDF")
    print("=" * 62)
    try:
        import urllib.request as u
        with u.urlopen(f"{API}/api/sessions/{sid}/report.pdf", timeout=60) as r:
            pdf = r.read()
        check("PDF сформирован", pdf[:4] == b"%PDF", f"{len(pdf) // 1024} КБ")
        with open("protokol.pdf", "wb") as f:
            f.write(pdf)
        print("  инфо  сохранён как protokol.pdf")
    except Exception as e:  # noqa: BLE001
        check("PDF сформирован", False, str(e)[:80])

    print("\n" + "=" * 62)
    print("ШАГ 10. Попадание в аналитику")
    print("=" * 62)
    an = req("GET", "/api/analytics")
    check("Вызов виден в аналитике",
          any(r["session_id"] == sid for r in an["rows"]),
          f"строк всего: {len(an['rows'])}")

    print("\n" + "=" * 62)
    if failures:
        print(f"ПРОВАЛЕНО ПРОВЕРОК: {len(failures)}")
        for f in failures:
            print(f"  — {f}")
        return 1
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ. Цепочка работает целиком.")
    print(f"\nОткройте разбор: {API}/api/sessions/{sid}/report")
    print("Панель аналитики: http://localhost:3000/analytics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
