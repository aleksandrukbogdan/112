#!/usr/bin/env python3
"""
Приёмочный тест. Проверяет цепочку целиком, без docker и без БД.

    python3 scripts/test_v2.py

Проверяется:
  1. конфигурация читается и непротиворечива
  2. движок протокола раскрывает условные ветки по обстановке
  3. правила речи МЧС ловят нарушения
  4. автоматика УКИО (уровень ЧС, состав служб)
  5. норматив 75 секунд
  6. отчёты по формам приказа МЧС № 192
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("CONFIG_DIR", str(ROOT / "config"))
os.environ.setdefault("REGION", "so")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

OK, FAIL = 0, 0


def check(name, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [ok]   {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


print("\n1. Конфигурация")
from app import config_loader as C

sv = C.svodka()
check("регион загружен", sv["region"] == "Свердловская область", sv["region"])
check("13 направлений реагирования", sv["marshrutov"] == 13, str(sv["marshrutov"]))
check("6 специальных частей УКИО", sv["spec_chastey"] == 6)
check("норматив карточки 75 с", sv["normativ_kartochki_sec"] == 75)
check("обязательных полей 6", sv["obyazatelnyh"] == 6)
check("заглушки помечены", sv["zaglushek"] >= 1, f"{sv['zaglushek']} шт.")
check("лесной пожар → 01 + Авиалесоохрана + Рослесхоз",
      set(C.sluzhby_dlya_tipa("POZHAR_LES")) == {"01", "AVIALESOOHRANA", "ROSLESHOZ"})
check("кровотечение → экстренная форма (388н п.11з)",
      C.forma_smp_dlya_povoda("KROVOTECH") == "ekstrennaya")
check("констатация смерти → неотложная (388н п.13б)",
      C.forma_smp_dlya_povoda("KONSTATACIYA") == "neotlozhnaya")
check("уровень ЧС при 12 пострадавших",
      C.uroven_chs_po_chislam(12, 0) == "MUNICIPALNAYA")

print("\n2. Движок протокола")
from app.protocol_engine import ProtocolEngine


def progon(tip, repliki):
    e = ProtocolEngine(tip_proisshestviya=tip)
    for kto, txt, t in repliki:
        e.observe(txt, kto, t)
    return e


e = progon("DTP_TRASSA", [("operator", "Что произошло?", 4000),
                          ("caller", "Авария на трассе, машина в кювете", 9000)])
h = [x.text for x in e.podskazki(t_ms=10000)]
check("ДТП на трассе → спрашивает километр", any("километр" in x.lower() for x in h))
check("ДТП на трассе → спрашивает направление", any("направлени" in x.lower() for x in h))

e = progon("POZHAR_DOM", [("operator", "Что случилось?", 4000),
                          ("caller", "Горит частный дом, точный адрес не знаю", 9000)])
h = [x.text for x in e.podskazki(t_ms=10000)]
check("частный дом → спрашивает газификацию",
      any("газифицирован" in x.lower() for x in h))
check("нет адреса → спрашивает ориентиры", any("ориентир" in x.lower() for x in h))

e = progon("MED_SOSTOYANIE", [("operator", "Что у вас?", 4000),
                              ("caller", "Боже мой помогите помогите он не дышит!!!", 8000)])
check("паника → правило о психологе (прил. № 13)",
      any(x.kind == "rule" and "психолог" in x.text.lower()
          for x in e.podskazki(t_ms=9000)))

e = progon("PRORYV_VODY", [("operator", "Слушаю вас", 4000),
                           ("caller", "В подъезде прорвало трубу, второй этаж", 9000)])
check("спокойный вызов → нет ложных срабатываний",
      len(e.s.kontekst) == 0, str(sorted(e.s.kontekst)))
check("переводчик не срабатывает без указания языка",
      not any(x.id == "per_perevodchik" for x in e.podskazki(t_ms=10000)))

e = progon("MED_SOSTOYANIE", [("operator", "Слушаю", 1000)])
check("до 60% норматива — без тревоги",
      not any(x.id == "t_trevoga" for x in e.podskazki(t_ms=30000)))
check("после 85% — тревога",
      any(x.kind == "timing" and x.ves == 3 for x in e.podskazki(t_ms=70000)))
check("после 100% — превышение",
      any(x.id == "t_prosrocheno" for x in e.podskazki(t_ms=80000)))

print("\n3. Правила речи (методика МЧС)")
from app.rech_check import proverit, shtraf

n = proverit("Не паникуйте, успокойтесь! Это же катастрофа.")
check("ловит частицу «не»", any(x["pravilo"] == "chastica_ne" for x in n))
check("ловит слово «катастрофа»", any(x["pravilo"] == "katastrofizatory" for x in n))
check("предлагает замену", any(x.get("zamena") for x in n))
check("чистая фраза без штрафа",
      shtraf(proverit("Здравствуйте, оператор-112. Слушаю вас.")) == 0)
check("ловит непрофессиональный тон",
      any(x["pravilo"] == "neprofessionalno"
          for x in proverit("Ну и что вы хотите, я же сказал")))

print("\n4. API обоих режимов")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes_v2 import router

app = FastAPI()
app.include_router(router)
c = TestClient(app)

check("GET /api/v2/config", c.get("/api/v2/config").status_code == 200)

for rezhim in ("train", "assist"):
    r = c.post("/api/v2/session",
               json={"rezhim": rezhim, "tip_proisshestviya": "POZHAR_DOM"}).json()
    sid = r["session_id"]
    check(f"сессия «{rezhim}» создана", bool(sid))
    check(f"приветствие из прил. № 11 ({rezhim})", "оператор-112" in r["privetstvie"])

    c.post(f"/api/v2/session/{sid}/replika",
           json={"kto": "operator", "tekst": "Что произошло?", "t_ms": 4000})
    rr = c.post(f"/api/v2/session/{sid}/replika",
                json={"kto": "caller",
                      "tekst": "Горит частный дом, адрес не знаю", "t_ms": 9000}).json()
    check(f"контекст распознан ({rezhim})",
          "chastny_dom" in rr["kontekst"] and "net_tochnogo_adresa" in rr["kontekst"])

    for k, v in [("rajon", "Сысертский"), ("mesto_okato", "EKB"), ("ulica", "Лесная"),
                 ("dom", "14"), ("tip_proisshestviya", "POZHAR_DOM"),
                 ("opisanie", "Горит частный дом")]:
        c.post(f"/api/v2/session/{sid}/pole", json={"key": k, "value": v})

    res = c.post(f"/api/v2/session/{sid}/pole",
                 json={"key": "chislo_postradavshih", "value": 12}).json()
    check(f"уровень ЧС считается автоматически ({rezhim})",
          res["avto"].get("uroven_chs", {}).get("value") == "MUNICIPALNAYA")

    o = c.post(f"/api/v2/session/{sid}/otpravit", json={"sluzhby": ["01"]}).json()
    check(f"карточка отправлена ({rezhim})", o["ok"] is True)
    check(f"уложились в норматив ({rezhim})", o["v_normativ"] is True)

    f = c.post(f"/api/v2/session/{sid}/finish").json()
    check(f"итог с оценками ({rezhim})", f["ocenki"]["protokol"] == 100)
    check(f"источники указаны ({rezhim})", "1931" in f["istochniki"]["normativy"])

r = c.post("/api/v2/session", json={"rezhim": "train"}).json()
sid = r["session_id"]
c.post(f"/api/v2/session/{sid}/replika",
       json={"kto": "operator", "tekst": "Не паникуйте! Это катастрофа!", "t_ms": 5000})
f = c.post(f"/api/v2/session/{sid}/finish").json()
check("нарушения речи снижают самообладание",
      f["ocenki"]["samoobladanie"] < 100, str(f["ocenki"]["samoobladanie"]))

print("\n4а. Сессии в Redis (переживают перезапуск процесса)")
import asyncio
from app import session_store as ST

check("Redis доступен", asyncio.run(ST.zdorov()))

r = c.post("/api/v2/session", json={"rezhim": "assist",
                                    "tip_proisshestviya": "DTP_TRASSA"}).json()
sid2 = r["session_id"]
c.post(f"/api/v2/session/{sid2}/replika",
       json={"kto": "caller", "tekst": "Авария на трассе", "t_ms": 5000})
c.post(f"/api/v2/session/{sid2}/pole", json={"key": "rajon", "value": "Богдановичский"})

# имитируем перезапуск процесса: сбрасываем все пулы и кэши
ST._pools.clear()
C.sbros_kesha()

d = c.get(f"/api/v2/session/{sid2}/podskazki").json()
check("сессия читается после сброса памяти процесса", "gotovnost" in d)
got = asyncio.run(ST.prochitat(sid2))
check("состояние движка восстановлено",
      got is not None and got[1].s.zapolnennye.get("rajon") == "Богдановичский")
check("контекст восстановлен", "dtp_na_trasse" in got[1].s.kontekst)
check("несуществующая сессия → 404",
      c.get("/api/v2/session/00000000-0000-0000-0000-000000000000/podskazki").status_code == 404)

print("\n5. Отчёты по приказу МЧС № 192")
f1 = c.get("/api/v2/otchet/forma1").json()
check("форма 1/112: 10 строк", len(f1["stroki"]) == 10)
check("форма 1/112: норматив 75 с в строке 3", f1["stroki"][2]["trebovanie"] == 75)
f2 = c.get("/api/v2/otchet/forma2").json()
check("форма 2/112: 13 направлений", len(f2["po_napravleniyam"]) == 13)
check("форма 2/112: 3 вида невыездных", len(f2["bez_reagirovaniya"]) == 3)

print("\n6. Сценарии")
import glob
import json

sc = [json.load(open(p, encoding="utf-8"))
      for p in glob.glob(str(ROOT / "content" / "scenarios" / "*.json"))]
check("сценариев не меньше 10", len(sc) >= 10, f"{len(sc)} шт.")
sluzhby = {s for x in sc for s in x.get("sluzhby", [])}
check("покрыты службы 01, 02, 03, 04, ЖКХ",
      {"01", "02", "03", "04", "JKH"} <= sluzhby, str(sorted(sluzhby)))
check("есть невыездные вызовы", sum(1 for x in sc if x.get("nevyezdnoy")) >= 2)

print(f"\n{'=' * 46}")
print(f"  проверок пройдено: {OK}, провалено: {FAIL}")
print("=" * 46)
sys.exit(0 if FAIL == 0 else 1)
