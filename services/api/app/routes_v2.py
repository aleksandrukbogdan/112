"""
Маршруты API, общие для обоих режимов.

Подключается в main.py:
    from app.routes_v2 import router as v2
    app.include_router(v2)

Режимы:
  /api/v2/train/*     тренажёр — заявитель генерируется LLM
  /api/v2/assist/*    помощник — заявитель живой, реплики из ASR

Логика опроса общая (ProtocolEngine), различается источник реплик
и то, что помощник ничего не оценивает в эфире — только логирует.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config_loader as C
from . import rech_check
from . import session_store as ST
from .protocol_engine import ProtocolEngine

router = APIRouter(prefix="/api/v2", tags=["v2"])


async def _load(sid: str):
    """Читает сессию из Redis. 404, если её нет или истёк TTL."""
    got = await ST.prochitat(sid)
    if not got:
        raise HTTPException(404, "сессия не найдена или истекла")
    return got


# ---------------------------------------------------------------- конфигурация

@router.get("/config")
def get_config():
    """Всё, что нужно фронту для отрисовки. Меняется правкой YAML."""
    return {
        "region": C.region(),
        "normativy": C.normativy(),
        "routes": C.routes(),
        "ukio": C.ukio(),
        "rechevye_pravila": C.rechevye_pravila(),
        "zaglushki": C.zaglushki(),
        "svodka": C.svodka(),
    }


@router.get("/config/classifier/{name}")
def get_classifier(name: str):
    try:
        return C.classifier(name)
    except FileNotFoundError:
        raise HTTPException(404, f"нет классификатора {name}")


@router.post("/config/reload")
def reload_config():
    """Перечитать YAML без перезапуска контейнера."""
    C.sbros_kesha()
    return {"ok": True, "svodka": C.svodka()}


# ---------------------------------------------------------------- сессия

class NachatVyzov(BaseModel):
    rezhim: str = "train"                 # train | assist
    tip_proisshestviya: str | None = None
    scenario_id: str | None = None
    operator: str | None = None


@router.post("/session")
async def nachat(body: NachatVyzov):
    sid = str(uuid.uuid4())
    eng = ProtocolEngine(tip_proisshestviya=body.tip_proisshestviya)
    data = {
        "id": sid, "rezhim": body.rezhim,
        "scenario_id": body.scenario_id, "operator": body.operator,
        "nachalo": time.time(), "otpravlena": False,
        "sluzhby": [], "narusheniya_rechi": [], "hints_log": [], "reakcii": [],
    }
    await ST.sozdat(sid, data, eng)
    return {
        "session_id": sid,
        "rezhim": body.rezhim,
        "privetstvie": eng.p["vhod"]["privetstvie"].replace(
            "{mo}", C.region().get("mo_default", "")),
        "normativ_sec": C.normativy()["opros_i_kartochka_sec"],
        "podskazki": [h.to_dict() for h in eng.podskazki(0)],
    }


class Replika(BaseModel):
    kto: str                              # operator | caller
    tekst: str
    t_ms: int | None = None


@router.post("/session/{sid}/replika")
async def replika(sid: str, body: Replika):
    """
    Одна реплика диалога. Общая точка для обоих режимов:
    в тренажёре caller приходит от LLM, в помощнике — от ASR.
    """
    s, eng = await _load(sid)
    t_ms = body.t_ms if body.t_ms is not None else int((time.time() - s["nachalo"]) * 1000)

    eng.observe(body.tekst, body.kto, t_ms)

    narush = []
    if body.kto == "operator":
        narush = rech_check.proverit(body.tekst)
        if narush:
            s["narusheniya_rechi"].extend(
                [{**n, "t_ms": t_ms, "fraza": body.tekst[:120]} for n in narush])

    podskazki = [h.to_dict() for h in eng.podskazki(t_ms)]
    s["hints_log"].append({"t_ms": t_ms, "hints": podskazki})
    await ST.sohranit(sid, s, eng)

    return {
        "podskazki": podskazki,
        "narusheniya_rechi": narush,      # в помощнике фронт их не показывает
        "gotovnost": eng.gotovnost(),
        "kontekst": sorted(eng.s.kontekst),
    }


class Pole(BaseModel):
    key: str
    value: Any


@router.post("/session/{sid}/pole")
async def pole(sid: str, body: Pole):
    """Оператор заполнил поле карточки."""
    s, eng = await _load(sid)
    eng.zapolnit(body.key, body.value)

    # автоматика, которая есть в боевом АРМ
    avto: dict[str, Any] = {}
    z = eng.s.zapolnennye
    if body.key in ("chislo_postradavshih", "chislo_pogibshih"):
        uroven = C.uroven_chs_po_chislam(
            int(z.get("chislo_postradavshih") or 0),
            int(z.get("chislo_pogibshih") or 0))
        eng.zapolnit("uroven_chs", uroven)
        avto["uroven_chs"] = {"value": uroven, "avto": True,
                              "primechanie": "уровень установлен автоматически"}
    if body.key == "tip_proisshestviya":
        eng.tip = body.value
        s["sluzhby"] = [r["kod"] for r in eng.rekomendovannye_sluzhby()]
        avto["sluzhby"] = eng.rekomendovannye_sluzhby()

    t_ms = int((time.time() - s["nachalo"]) * 1000)
    await ST.sohranit(sid, s, eng)
    return {
        "avto": avto,
        "podskazki": [h.to_dict() for h in eng.podskazki(t_ms)],
        "gotovnost": eng.gotovnost(),
    }


@router.get("/session/{sid}/podskazki")
async def podskazki(sid: str):
    """Опрос подсказок по таймеру, без реплики (для тикающего таймера)."""
    s, eng = await _load(sid)
    t_ms = int((time.time() - s["nachalo"]) * 1000)
    return {"podskazki": [h.to_dict() for h in eng.podskazki(t_ms)],
            "gotovnost": eng.gotovnost()}


class Reakciya(BaseModel):
    hint_id: str
    reaction: str                          # accepted | dismissed | ignored


@router.post("/session/{sid}/hint")
async def hint_reaction(sid: str, body: Reakciya):
    """Оператор принял или отклонил подсказку. Записывается всегда."""
    s, eng = await _load(sid)
    s.setdefault("reakcii", []).append(
        {"hint_id": body.hint_id, "reaction": body.reaction,
         "t_ms": int((time.time() - s["nachalo"]) * 1000)})
    await ST.sohranit(sid, s, eng)
    return {"ok": True}


class Otpravka(BaseModel):
    sluzhby: list[str]


@router.post("/session/{sid}/otpravit")
async def otpravit(sid: str, body: Otpravka):
    """Отправка карточки в ДДС — фиксируется время норматива."""
    s, eng = await _load(sid)
    g = eng.gotovnost()
    if not g["gotova"]:
        return {"ok": False, "prichina": "не заполнены обязательные поля",
                "ne_zapolneno": g["ne_zapolneno"]}
    s["otpravlena"] = True
    s["t_otpravki_sec"] = g["proshlo_sec"]
    s["sluzhby"] = body.sluzhby
    await ST.sohranit(sid, s, eng)
    rb = C.routes_by_kod()
    return {"ok": True, "t_sec": g["proshlo_sec"],
            "v_normativ": g["v_normativ"],
            "normativ_sec": g["normativ_sec"],
            "sluzhby": [rb[k]["nazvanie"] for k in body.sluzhby if k in rb]}


# ---------------------------------------------------------------- итог

@router.post("/session/{sid}/finish")
async def finish(sid: str):
    """
    Итог вызова.
    В тренажёре показывается сразу. В помощнике — уходит в разбор,
    оператору в эфире оценок не показываем.
    """
    s, eng = await _load(sid)
    g = eng.gotovnost()
    norm = C.normativy()

    # скорость: 100 при укладывании в норматив, дальше линейно вниз
    t = s.get("t_otpravki_sec", g["proshlo_sec"])
    n = norm["opros_i_kartochka_sec"]
    speed = 100 if t <= n else max(0, int(100 - (t - n) / n * 100))

    # протокол: доля закрытых обязательных полей и заданных вопросов
    prot = int(100 * (g["zapolneno_obyazatelnyh"] / max(1, g["vsego_obyazatelnyh"])))

    # самообладание: 100 минус штрафы за нарушения правил речи МЧС
    shtraf = sum(x["ves"] for x in s["narusheniya_rechi"])
    comp = max(0, 100 - shtraf * 5)

    itog = {
        "session_id": sid,
        "rezhim": s["rezhim"],
        "vremya_sec": t,
        "normativ_sec": n,
        "v_normativ": t <= n,
        "ocenki": {"protokol": prot, "skorost": speed, "samoobladanie": comp,
                   "itogo": int((prot + speed + comp) / 3)},
        "kartochka": eng.s.zapolnennye,
        "sluzhby": s["sluzhby"],
        "zadano_voprosov": g["zadano_voprosov"],
        "vsego_voprosov": g["vsego_voprosov"],
        "narusheniya_rechi": s["narusheniya_rechi"],
        "podskazok_pokazano": sum(len(h["hints"]) for h in s["hints_log"]),
        "reakcii": s.get("reakcii", []),
        "istochniki": {
            "normativy": norm["istochnik"],
            "protokol": eng.p.get("istochnik"),
            "rech": C.rechevye_pravila()["istochnik"],
        },
    }
    await ST.sohranit_itog(sid, itog)
    await ST.udalit(sid)
    return itog


# ---------------------------------------------------------------- отчёты

@router.get("/otchet/forma1")
async def forma1():
    """
    Форма 1/112 приказа МЧС № 192 — временные параметры.
    Слева требование норматива, справа фактические данные.
    """
    n = C.normativy()
    it = await ST.itogi()
    times = [x["vremya_sec"] for x in it] or [0]
    sred = round(sum(times) / len(times), 1)
    v_norm = sum(1 for x in it if x.get("v_normativ"))
    return {
        "forma": "1/112",
        "istochnik": "Приказ МЧС России от 14.03.2022 № 192, приложение № 1",
        "subekt": C.region()["nazvanie"],
        "vyzovov_v_vyborke": len(it),
        "ulozhilis_v_normativ": v_norm,
        "stroki": [
            {"n": 1, "nazvanie": "Максимальное время ожидания ответа, секунд",
             "trebovanie": n["otvet_max_sec"], "fakt": None},
            {"n": 2, "nazvanie": "Среднее время ожидания ответа, секунд",
             "trebovanie": n["otvet_sredniy_sec"], "fakt": None},
            {"n": 3, "nazvanie": "Среднее время опроса до доступности карточки, секунд",
             "trebovanie": n["opros_i_kartochka_sec"], "fakt": sred},
            {"n": 4, "nazvanie": "Максимальное время подтверждения ДДС, секунд",
             "trebovanie": n["podtverzhdenie_dds_sec"], "fakt": None},
            {"n": 5, "nazvanie": "Максимальное время до обратного вызова, секунд",
             "trebovanie": n["obratny_vyzov_sec"], "fakt": None},
            {"n": 6, "nazvanie": "Минимальное количество попыток обратного вызова",
             "trebovanie": n["obratny_vyzov_popytok"], "fakt": None},
            {"n": 7, "nazvanie": "Максимальное время ожидания ответа при обратном вызове, минут",
             "trebovanie": n["obratny_vyzov_ozhidanie_sec"] // 60, "fakt": None},
            {"n": 8, "nazvanie": "Время консультативного обслуживания, минут",
             "trebovanie": n["konsultaciya_sec"] // 60, "fakt": None},
            {"n": 9, "nazvanie": "Максимальное время психологической поддержки, минут",
             "trebovanie": n["psiholog_sec"] // 60, "fakt": None},
            {"n": 10, "nazvanie": "Минимальный срок хранения информации, лет",
             "trebovanie": n["hranenie_let"], "fakt": None},
        ],
    }


@router.get("/otchet/forma2")
async def forma2():
    """
    Форма 2/112 — статистика вызовов по направлениям реагирования.
    Нумерация строк соответствует приказу.
    """
    rb = C.routes()
    it = await ST.itogi()
    schet: dict[str, int] = {}
    nev: dict[str, int] = {}
    for s in it:
        for k in s.get("sluzhby", []):
            schet[k] = schet.get(k, 0) + 1
        nv = (s.get("kartochka") or {}).get("nevyezdnoy")
        if nv:
            nev[nv] = nev.get(nv, 0) + 1
    nevyezd = C.classifier("nevyezdnye")["items"]
    return {
        "forma": "2/112",
        "istochnik": "Приказ МЧС России от 14.03.2022 № 192, приложение № 2",
        "subekt": C.region()["nazvanie"],
        "vsego": len(it),
        "po_napravleniyam": [
            {"stroka": r.get("forma_2_112"), "nazvanie": r["nazvanie"],
             "kod": r["kod"], "kolichestvo": schet.get(r["kod"], 0)}
            for r in rb],
        "bez_reagirovaniya": [
            {"stroka": v["forma_2_112"], "nazvanie": v["nazvanie"],
             "kolichestvo": nev.get(v["kod"], 0)}
            for v in nevyezd],
    }
