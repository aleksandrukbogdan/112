#!/usr/bin/env python3
"""
Тестовый набор формулировок оператора.

Классификатор интентов — самое хрупкое место системы. Этот набор ловит
ложные сработки правил ДО того, как они всплывут на демо.
Прогонять после каждой правки intent_rules.

    python3 scripts/test_intents.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

import app.config as cfg
object.__setattr__(cfg.settings, "llm_enabled", False)
from app.intents import by_rules  # noqa: E402

# (фраза, ожидаемые интенты, запрещённые интенты)
CASES = [
    ("Служба 112, что у вас случилось?",            ["greeting", "asked_what_happened"], []),
    ("Оператор 112, слушаю вас",                    ["greeting"], []),
    ("Назовите адрес происшествия",                 ["asked_address"], []),
    ("Куда ехать? Какая улица?",                    ["asked_address"], []),
    ("Где вы находитесь сейчас",                    ["asked_address"], []),
    ("Какой номер квартиры и подъезд",              ["asked_address_detail"], ["asked_victims"]),
    ("На каком этаже",                              ["asked_address_detail"], []),
    ("Есть ли пострадавшие?",                       ["asked_victims"], ["asked_address_detail"]),
    ("Кто-то есть в квартире?",                     ["asked_victims"], ["asked_address_detail"]),
    ("В квартире есть дети?",                       ["asked_victims"], ["asked_address_detail"]),
    ("На балконе есть газовые баллоны?",            ["asked_hazards"], ["asked_evacuation"]),
    ("Объект газифицирован?",                       ["asked_hazards"], []),
    ("Вы можете выйти из квартиры?",                ["asked_evacuation"], []),
    ("Можете выбраться на улицу?",                  ["asked_evacuation"], []),
    ("Как проехать во двор, машины стоят?",         ["asked_access_routes"], []),
    ("Есть шлагбаум на въезде?",                    ["asked_access_routes"], []),
    ("Как вас зовут, представьтесь",                ["asked_caller_name"], []),
    ("Закройте дверь и намочите полотенце",         ["gave_instructions"], []),
    ("Не пользуйтесь лифтом, выходите по лестнице", ["gave_instructions"], []),
    ("Я вас слышу, помощь уже выехала",             ["calming"], []),
    ("Оставайтесь на связи",                        ["calming"], []),
]


def main() -> int:
    sc = json.loads((ROOT / "content/scenarios/fire_apartment_01.json").read_text(encoding="utf-8"))
    rules = sc["intent_rules"]

    failed = 0
    for text, expect, forbid in CASES:
        hits = set(by_rules(text, rules))
        miss = [e for e in expect if e not in hits]
        wrong = [f for f in forbid if f in hits]
        if miss or wrong:
            failed += 1
            print(f"  ПРОВАЛ  «{text}»")
            if miss:
                print(f"          не распознано: {miss}")
            if wrong:
                print(f"          ложно сработало: {wrong}")
            print(f"          получено: {sorted(hits) or '—'}")
        else:
            print(f"  ок      «{text}» -> {sorted(hits)}")

    print(f"\n{len(CASES) - failed} из {len(CASES)} прошли")
    if failed:
        print("Правьте intent_rules в сценарии и прогоняйте снова.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
