"""Автономная проверка движка сценария и скоринга — без БД, Docker и сети."""
import json, sys, types, asyncio
sys.path.insert(0, 'services/api')

# заглушаем config/llm, чтобы не тянуть окружение
import app.config as cfg
object.__setattr__(cfg.settings, 'llm_enabled', False)

from app.scenario_engine import engine
from app import intents as I
from app import scoring

sc = json.load(open('content/scenarios/fire_apartment_01.json', encoding='utf-8'))
print("Сценарий:", sc['title'], "| фактов:", len(sc['facts']), "| чек-лист:", len(sc['checklist']))

st = engine.start("sess-1", sc)
events = [{"t_ms": 0, "kind": "call_start", "payload": {"scenario": sc["slug"]}}]

def turn(text, t_ms):
    """Имитирует ход оператора и возвращает события."""
    out = []
    out.append({"t_ms": t_ms, "kind": "operator_utterance",
                "payload": {"text": text, "fillers": I.count_fillers(text), "wpm": 140}})
    for f in I.forbidden_hits(text, sc.get("forbidden", [])):
        out.append({"t_ms": t_ms, "kind": "forbidden_phrase", "payload": f})
    hits = I.by_rules(text, sc["intent_rules"])
    for h in hits:
        out.append({"t_ms": t_ms, "kind": "intent_detected",
                    "payload": {"intent": h, "confidence": 0.95, "method": "rules"}})
    revealed = engine.apply_intents(st, hits, t_ms)
    for f in revealed:
        out.append({"t_ms": t_ms, "kind": "fact_revealed",
                    "payload": {"fact_id": f["id"], "field": f.get("field")}})
    reply = engine.caller_line(st, revealed)
    print(f"  [{t_ms/1000:5.1f}с] оператор: {text}")
    print(f"           интенты: {hits or '—'}  раскрыто: {[f['id'] for f in revealed] or '—'}")
    print(f"           заявитель: {reply['text']}")
    return out

print("\n--- ХОРОШИЙ ПРОГОН ---")
events += turn("Служба 112, что случилось?", 4000)
events += turn("Назовите адрес происшествия", 14000)
events += turn("Уточните квартиру и подъезд", 30000)
events += turn("Есть ли пострадавшие, кто-то есть в квартире?", 41000)
events += turn("На балконе есть газовые баллоны?", 52000)
events += turn("Вы можете выйти на лестницу?", 60000)
events += turn("Закройте дверь на кухню, намочите полотенце", 68000)
events.append({"t_ms": 72000, "kind": "card_dispatched",
               "payload": {"services": ["01","03"], "is_correct": True}})
events.append({"t_ms": 78000, "kind": "call_end", "payload": {}})

thr = {"time_to_ask_address_sec":25,"time_to_ask_victims_sec":45,"time_to_dispatch_sec":75,
       "speech_rate_min_wpm":90,"speech_rate_max_wpm":190,
       "weights":{"protocol":0.6,"speed":0.25,"composure":0.15}}
r = scoring.compute(events, sc, thr)
print(f"\n  протокол {r['protocol']}  скорость {r['speed']}  манера {r['composure']}  ИТОГ {r['total']}")
print("  стоп-факторы:", r['stop_factors'] or "нет", "| зачёт:", r['passed'])
assert r['passed'], "хороший прогон должен быть зачтён"
assert not r['stop_factors']

print("\n--- ПЛОХОЙ ПРОГОН (не спросил адрес, грубость, не та служба) ---")
engine.drop("sess-1")
st = engine.start("sess-2", sc)
ev2 = [{"t_ms": 0, "kind": "call_start", "payload": {"scenario": sc["slug"]}}]
ev2 += turn("Что у вас случилось", 9000)
ev2 += turn("Успокойтесь и не кричите на меня", 25000)
ev2 += turn("Есть пострадавшие?", 96000)
ev2.append({"t_ms": 120000, "kind": "card_dispatched",
            "payload": {"services": ["02"], "is_correct": False}})
ev2.append({"t_ms": 125000, "kind": "call_end", "payload": {}})
r2 = scoring.compute(ev2, sc, thr)
print(f"\n  протокол {r2['protocol']}  скорость {r2['speed']}  манера {r2['composure']}  ИТОГ {r2['total']}")
print("  стоп-факторы:", r2['stop_factors'], "| зачёт:", r2['passed'])
assert not r2['passed']
assert "Адрес не запрошен" in r2['stop_factors']
assert "Неверная маршрутизация" in r2['stop_factors']

print("\n--- САМОРАСКРЫТИЕ ФАКТА ПО ТАЙМАУТУ ---")
engine.drop("sess-2")
st = engine.start("sess-3", sc)
sp = engine.spontaneous(st, 95000)
print("  через 95с сам выдал:", [f['id'] for f in sp])
assert any(f['id'] == 'f_child' for f in sp), "f_child должен раскрыться сам после 90с"

print("\n--- ФАКТ НЕ РАСКРЫВАЕТСЯ БЕЗ ВОПРОСА ---")
engine.drop("sess-3")
st = engine.start("sess-4", sc)
res = engine.apply_intents(st, ["greeting"], 1000)
print("  после приветствия раскрыто:", [f['id'] for f in res] or "ничего")
assert res == [], "без нужного вопроса факты раскрываться не должны"

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
