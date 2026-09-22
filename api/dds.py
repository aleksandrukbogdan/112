"""
Режим «Диспетчер ДДС».

Что происходит (по ответам заказчика):
  1. Система, играя оператора 112, «поставляет» карточку в службу обучаемого.
     С вероятностью DDS["veroyatnost_oshibki"] в карточку внесена ошибка
     оператора 112 (номер дома, телефон, признак пострадавших, корпус).
  2. Диспетчеру доступна запись исходного разговора 112 — в ней правда.
  3. Диспетчер подтверждает приём (норматив 30 с), проверяет карточку,
     отмечает найденные расхождения.
  4. Выбирает бригаду вручную и звонит старшему по IP-телефону,
     передаёт сведения. Бригада выезжает, только если передан адрес.
  5. Старший бригады сам звонит на этапах: прибытие, начало работ, завершение.
     Диспетчер может и сам позвонить и спросить обстановку.
  6. Статусы — только по факту: каждый допустим лишь после своего основания.

Смысловые решения (передан ли адрес, выехала ли бригада, какая фаза)
принимаются правилами. Модель, если включена, только формулирует реплики.
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import bkt, config as C, db, domain as D
from .auth import current_user, role

router = APIRouter(prefix="/api/dds", tags=["dds"])
LIVE: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _sluzhby() -> list[dict]:
    return C.load("sluzhby")


def _find_sl(korotko: str) -> dict | None:
    for s in _sluzhby():
        if s["korotko"] == korotko or s["korotko"].startswith(korotko):
            return s
    return None


# ================================================================ карточка от «оператора 112»

def _postradavshie(sit: str) -> str:
    s = sit.lower()
    if re.search(r"пострадавших нет|без пострадавших|б/п|пострадавших людей нет", s):
        return "net"
    if re.search(r"пострадал|травм|кровотеч|без сознания|ожог|ранен|плохо|пострадавш|судорог|не дышит|задыха", s):
        return "est"
    return "neizvestno"


POST_RU = {"net": "нет", "est": "есть", "neizvestno": "нет данных"}


def _isporti(pole: str, pravda: dict, rnd: random.Random) -> tuple[str, str] | None:
    """Ошибка оператора 112 в одном поле. Возвращает (было_в_карточке, правда)."""
    if pole == "dom":
        m = list(re.finditer(r"(дом|д\.|вл\.?)\s*(\d+)", pravda["adres"]))
        if not m:
            return None
        g = m[-1]
        n = int(g.group(2))
        novyy = str(n + rnd.choice([-3, -2, -1, 1, 2, 3]) if n > 3 else n + rnd.choice([1, 2, 3]))
        return pravda["adres"][:g.start(2)] + novyy + pravda["adres"][g.end(2):], pravda["adres"]
    if pole == "korpus":
        m = re.search(r",?\s*(корп\.?|корпус|стр\.?|строение)\s*\d+\w*", pravda["adres"])
        if not m:
            return None
        return (pravda["adres"][:m.start()] + pravda["adres"][m.end():]).strip(" ,"), pravda["adres"]
    if pole == "telefon":
        t = pravda["telefon"]
        pos = [i for i, ch in enumerate(t) if ch.isdigit()][3:]
        if not pos:
            return None
        i = rnd.choice(pos)
        d = str((int(t[i]) + rnd.randint(1, 8)) % 10)
        return t[:i] + d + t[i + 1:], t
    if pole == "postradavshie":
        if pravda["postradavshie"] == "neizvestno":
            return None
        return ("net" if pravda["postradavshie"] == "est" else "est"), pravda["postradavshie"]
    return None


def _zapis(bil: dict, pravda: dict) -> list[dict]:
    """Запись исходного разговора 112. В ней — правда."""
    z = [("operator", "Служба 112, оператор слушает. Что у вас случилось?"),
         ("zayavitel", bil["situaciya"]),
         ("operator", "Назовите адрес, где это происходит."),
         ("zayavitel", bil["adres_vidimy"])]
    if bil.get("trebuet_utochneniya"):
        z += [("operator", "Уточните, пожалуйста, точный адрес: улица, дом, строение."),
              ("zayavitel", f"Сейчас… {bil['adres_etalon']}.")]
    z += [("operator", "Есть пострадавшие?"),
          ("zayavitel", {"net": "Нет, пострадавших нет.", "est": "Да, есть пострадавшие.",
                         "neizvestno": "Не знаю, мне отсюда не видно."}[pravda["postradavshie"]]),
          ("operator", "Представьтесь, пожалуйста."),
          ("zayavitel", bil["zayavitel"]["fio"] + "."),
          ("operator", "Номер телефона для связи?"),
          ("zayavitel", bil["zayavitel"]["telefon"] + "."),
          ("operator", "Информацию передаю в службы. Оставайтесь на связи.")]
    t, out = 0.0, []
    for kto, tekst in z:
        out.append({"kto": kto, "tekst": tekst, "t_sec": round(t, 1)})
        t += 2.0 + len(tekst) / 18
    return out


def sobrat_kartochku(bil: dict, sluzhba: str | None, seed: int | None = None) -> dict:
    rnd = random.Random(seed)
    gr = C.load("tree").get(bil["gruppa"], {})
    pravda = {"adres": bil["adres_etalon"], "telefon": bil["zayavitel"]["telefon"],
              "fio": bil["zayavitel"]["fio"], "postradavshie": _postradavshie(bil["situaciya"]),
              "opisanie": bil["situaciya"]}
    kart = dict(pravda)
    oshibka = None
    if rnd.random() < C.DDS["veroyatnost_oshibki"]:
        for pole in rnd.sample(C.DDS["oshibki"], len(C.DDS["oshibki"])):
            r = _isporti(pole, pravda, rnd)
            if r:
                kart_pole = "adres" if pole in ("dom", "korpus") else pole
                kart[kart_pole] = r[0]
                oshibka = {"pole": kart_pole, "vid": pole, "v_kartochke": r[0], "pravda": r[1]}
                break
    sl = list(C.GRUPPA_SLUZHBY.get(bil["gruppa"], ["Служба 102"]))
    if sluzhba and sluzhba not in sl:
        sl.append(sluzhba)
    moya = sluzhba or sl[0]
    return {
        "nomer": str(36814000 + rnd.randint(100, 999)),
        "sozdana": _now(),
        "operator_112": f"Опер. АРМ {rnd.randint(1, 9)}, УМЦ",
        "gruppa": bil["gruppa"],
        "tip": gr.get("nazvanie", "Происшествие"),
        "adres": kart["adres"],
        "opisanie": kart["opisanie"],
        "zayavitel_fio": kart["fio"],
        "aon": kart["telefon"],
        "postradavshie": kart["postradavshie"],
        "sluzhby": [{"korotko": s, "status": "Добавлена", "moya": s == moya} for s in sl],
        "moya_sluzhba": moya,
        "_pravda": pravda, "_oshibka": oshibka,
    }


def _publichnaya(k: dict) -> dict:
    return {x: v for x, v in k.items() if not x.startswith("_")}


def _kontakty(sl: str) -> list[dict]:
    fam = ["Петров", "Сидоренко", "Кузьмин", "Орлов", "Лебедев", "Ковалёв"]
    br = [{"id": f"br{i}", "kto": "brigada", "nazvanie": f"Бригада № {i} — старший {fam[i]}",
           "telefon": f"IP 7{i}{random.randint(100, 999)}"} for i in (1, 2, 3)]
    return br + [
        {"id": "ruk", "kto": "rukovoditel", "nazvanie": f"Дежурный руководитель: {sl}", "telefon": "IP 7000"},
        {"id": "op112", "kto": "operator112", "nazvanie": "Старший оператор 112", "telefon": "IP 112"},
        {"id": "zayav", "kto": "zayavitel", "nazvanie": "Заявитель (обратный звонок)", "telefon": "АОН"},
    ]


# ================================================================ собеседники

def _peredano(tekst: str, pravda: dict) -> dict:
    """Что из обязательного диспетчер передал бригаде (по всем своим репликам звонка)."""
    adr = D.sravnit_adres(tekst, pravda["adres"])
    op = D.tokens(pravda["opisanie"])
    sut = len(op & D.tokens(tekst)) >= 2
    tel_d = re.sub(r"\D", "", pravda["telefon"])[-4:]
    tel = bool(tel_d) and tel_d in re.sub(r"\D", "", tekst)
    post = bool(re.search(r"пострадав|травм|без сознания|раненых|жертв", tekst.lower()))
    return {"adres": adr["sovpalo"], "adres_dolya": adr["dolya"], "sut": sut,
            "telefon": tel, "postradavshie": post}


def _faza(s: dict) -> str:
    v = s.get("t_vyezd")
    if not v:
        return "ne_vyehala"
    dt = _now() - v
    e = C.DDS["etapy_sec"]
    if dt >= e["pribytie"] + e["raboty"] + e["zaversheno"]:
        return "zaversheno"
    if dt >= e["pribytie"] + e["raboty"]:
        return "raboty"
    if dt >= e["pribytie"]:
        return "pribytie"
    return "vyezd"


FAZA_TEKST = {
    "vyezd": "Выехали, следуем к месту.",
    "pribytie": "Прибыли на место, проводим разведку.",
    "raboty": "Приступили к работам.",
    "zaversheno": "Работы завершены, бригада освобождается.",
}


def _osnovanie(s: dict, kod: str, via: str) -> None:
    """Зафиксировать основание для статуса: диспетчер узнал о событии."""
    if kod not in s["osnovaniya"]:
        s["osnovaniya"][kod] = {"t": _now(), "via": via}


def _otvet_brigady(s: dict, call: dict, tekst: str) -> str:
    low = tekst.lower()
    br = call["kontakt"]
    if s.get("brigada") and s["brigada"] != br["id"]:
        return "Мы на другом вызове. Работает бригада, которую вы уже направили."
    if not s.get("t_vyezd"):
        vse = " ".join(m["tekst"] for m in call["dialog"] if m["kto"] == "dispetcher")
        p = _peredano(vse, s["kartochka"]["_pravda"])
        call["peredano"] = p
        if p["adres"]:
            s["t_vyezd"] = _now()
            s["brigada"] = br["id"]
            s["peredano"] = p
            _osnovanie(s, "vyezd", "звонок бригаде")
            e = C.DDS["etapy_sec"]
            t0 = s["t_vyezd"]
            s["sobytiya"] += [
                {"id": f"ev{i}", "t": t0 + dt, "faza": f, "kontakt": br, "status": "ozhidaet"}
                for i, (f, dt) in enumerate([("pribytie", e["pribytie"]),
                                             ("raboty", e["pribytie"] + e["raboty"]),
                                             ("zaversheno", e["pribytie"] + e["raboty"] + e["zaversheno"])])]
            dop = "" if p["sut"] else " Что там случилось?"
            return "Принял, выезжаем." + dop
        if re.search(r"адрес|улиц|дом|проезд|шоссе|переул|проспект|км", low):
            return "Адрес не понял, повторите полностью: улица, дом, строение."
        return "Слушаю. Куда выезжать? Назовите адрес."
    f = _faza(s)
    if f in ("pribytie", "raboty", "zaversheno"):
        _osnovanie(s, f, "звонок диспетчера бригаде")
        if f in ("raboty", "zaversheno"):
            _osnovanie(s, "pribytie", "звонок диспетчера бригаде")
        if f == "zaversheno":
            _osnovanie(s, "raboty", "звонок диспетчера бригаде")
    return FAZA_TEKST[f]


def _otvet(s: dict, call: dict, tekst: str) -> str:
    kto = call["kontakt"]["kto"]
    pr = s["kartochka"]["_pravda"]
    low = tekst.lower()
    if kto == "brigada":
        return _otvet_brigady(s, call, tekst)
    if kto == "zayavitel":
        if re.search(r"адрес|где|дом|улиц", low):
            return f"Я же говорил: {pr['adres']}."
        if re.search(r"телефон|номер", low):
            return f"{pr['telefon']}."
        if re.search(r"пострадав", low):
            return {"net": "Пострадавших нет.", "est": "Да, есть пострадавшие.",
                    "neizvestno": "Не знаю."}[pr["postradavshie"]]
        return "Да, всё так. Когда приедут?"
    if kto == "operator112":
        if re.search(r"адрес|дом|телефон|пострадав|ошиб|провер|уточн", low):
            return "Сверила по записи разговора: " + (
                f"адрес — {pr['adres']}; телефон — {pr['telefon']}; "
                f"пострадавшие — {POST_RU[pr['postradavshie']]}.")
        return "Старший оператор 112 слушает."
    if kto == "rukovoditel":
        return "Принял информацию. Держите в курсе."
    return "Слушаю."


# ================================================================ API

class Start(BaseModel):
    scenario_id: str
    assignment_id: int | None = None
    sluzhba: str | None = None


def _mine(sid: str, u: dict) -> dict:
    s = LIVE.get(sid)
    if not s:
        raise HTTPException(404, "занятие не найдено или завершено")
    if s["user_id"] != u["id"]:
        raise HTTPException(403, "чужое занятие")
    return s


def _persist(s: dict) -> None:
    db.ex("UPDATE sessions SET data=? WHERE id=?",
          (json.dumps(s, ensure_ascii=False, default=str), s["id"]))


@router.get("/sluzhby")
def sluzhby(u=Depends(current_user)):
    return _sluzhby()


@router.get("/nastroyki")
def nastroyki(u=Depends(current_user)):
    return {"statusy": C.DDS["statusy"], "podtverzhdenie_sec": C.DDS["podtverzhdenie_sec"],
            "svoevremenno_sec": C.DDS["svoevremenno_sec"],
            "istochnik": C.DDS["istochnik_statusov"]}


@router.post("/session")
def start(b: Start, u=Depends(role("trainee", "teacher"))):
    row = db.q1("SELECT * FROM scenarios WHERE id=?", (b.scenario_id,))
    if not row or (row["status"] != "published" and u["role"] == "trainee"):
        raise HTTPException(404, "сценарий недоступен")
    bil = json.loads(row["data"])
    sl = b.sluzhba
    if b.assignment_id:
        a = db.q1("SELECT dds_sluzhba FROM assignments WHERE id=?", (b.assignment_id,))
        sl = sl or (a or {}).get("dds_sluzhba")
    k = sobrat_kartochku(bil, sl)
    sid = str(uuid.uuid4())
    s = {"id": sid, "kind": "dds", "user_id": u["id"], "scenario_id": b.scenario_id,
         "assignment_id": b.assignment_id, "bilet": bil, "nachalo": _now(),
         "kartochka": k, "zapis": _zapis(bil, k["_pravda"]),
         "kontakty": _kontakty(k["moya_sluzhba"]),
         "osnovaniya": {"postuplenie": {"t": _now(), "via": "карточка поступила"}},
         "statusy": [], "zamechaniya": [], "zvonki": [], "sobytiya": [],
         "brigada": None, "t_vyezd": None, "peredano": None, "narusheniya_rechi": []}
    LIVE[sid] = s
    db.ex("INSERT INTO sessions(id,user_id,scenario_id,kind,assignment_id,started,state,data) "
          "VALUES(?,?,?,?,?,?,?,?)",
          (sid, u["id"], b.scenario_id, "dds", b.assignment_id, s["nachalo"], "live",
           json.dumps(s, ensure_ascii=False, default=str)))
    return {"session_id": sid, "kartochka": _publichnaya(k), "zapis": s["zapis"],
            "kontakty": s["kontakty"], "statusy": C.DDS["statusy"],
            "podtverzhdenie_sec": C.DDS["podtverzhdenie_sec"]}


class Status(BaseModel):
    status: str
    kommentariy: str = ""


@router.post("/{sid}/status")
def status(sid: str, b: Status, u=Depends(current_user)):
    """Статус ставится всегда — проверка «по факту» идёт в оценке, как в жизни."""
    s = _mine(sid, u)
    st = next((x for x in C.DDS["statusy"] if x["kod"] == b.status), None)
    if not st:
        raise HTTPException(400, "нет такого статуса")
    s["statusy"].append({"kod": st["kod"], "nazvanie": st["nazvanie"], "t": _now(),
                         "kommentariy": b.kommentariy.strip()})
    for x in s["kartochka"]["sluzhby"]:
        if x["moya"]:
            x["status"] = st["nazvanie"]
    _persist(s)
    osn = s["osnovaniya"].get(st["osnovanie"])
    return {"ok": True, "osnovanie_bylo": bool(osn),
            "preduprezhdenie": None if osn else
            "Основания для этого статуса ещё не было. Памятка требует ставить статус по факту."}


class Zamech(BaseModel):
    pole: str
    verno: str
    kommentariy: str = ""


@router.post("/{sid}/zamechanie")
def zamechanie(sid: str, b: Zamech, u=Depends(current_user)):
    """Диспетчер отмечает ошибку оператора 112 в карточке (универсальный алгоритм, п. 3)."""
    s = _mine(sid, u)
    s["zamechaniya"].append({"pole": b.pole, "verno": b.verno.strip(),
                             "kommentariy": b.kommentariy, "t": _now()})
    _persist(s)
    return {"ok": True}


class Zvonok(BaseModel):
    kontakt: str


@router.post("/{sid}/call")
def call(sid: str, b: Zvonok, u=Depends(current_user)):
    s = _mine(sid, u)
    k = next((x for x in s["kontakty"] if x["id"] == b.kontakt), None)
    if not k:
        raise HTTPException(404, "нет такого абонента")
    cid = f"c{len(s['zvonki']) + 1}"
    otvetil = not (k["kto"] == "brigada" and random.random() < 0.1)   # иногда не берут трубку
    privet = {"brigada": "Старший бригады слушает.", "rukovoditel": "Дежурный руководитель, слушаю.",
              "operator112": "Старший оператор 112 слушает.", "zayavitel": "Алло?"}[k["kto"]]
    c = {"id": cid, "kontakt": k, "ishod": "otvetil" if otvetil else "ne_otvetil",
         "nachalo": _now(), "konec": None, "vhodyashchiy": False,
         "dialog": [{"kto": "abonent", "tekst": privet, "t": _now()}] if otvetil else []}
    s["zvonki"].append(c)
    if otvetil and k["kto"] == "brigada" and s.get("t_vyezd") and s.get("brigada") == k["id"]:
        rep = _otvet_brigady(s, c, "обстановка")
        c["dialog"].append({"kto": "abonent", "tekst": rep, "t": _now()})
    _persist(s)
    return {"call_id": cid, "ishod": c["ishod"], "dialog": c["dialog"]}


class Say(BaseModel):
    tekst: str


@router.post("/{sid}/call/{cid}/say")
def say(sid: str, cid: str, b: Say, u=Depends(current_user)):
    s = _mine(sid, u)
    c = next((x for x in s["zvonki"] if x["id"] == cid), None)
    if not c or c["konec"] or c["ishod"] != "otvetil":
        raise HTTPException(400, "звонок не активен")
    nar = D.proverit_rech(b.tekst)
    if nar:
        s["narusheniya_rechi"] += [{**n, "t": _now()} for n in nar]
    c["dialog"].append({"kto": "dispetcher", "tekst": b.tekst, "t": _now()})
    otv = _otvet(s, c, b.tekst)
    c["dialog"].append({"kto": "abonent", "tekst": otv, "t": _now()})
    _persist(s)
    return {"otvet": otv, "narusheniya_rechi": nar, "vyezd": bool(s.get("t_vyezd"))}


@router.post("/{sid}/call/{cid}/end")
def end(sid: str, cid: str, u=Depends(current_user)):
    s = _mine(sid, u)
    c = next((x for x in s["zvonki"] if x["id"] == cid), None)
    if c and not c["konec"]:
        c["konec"] = _now()
        _persist(s)
    return {"ok": True}


@router.get("/{sid}/events")
def events(sid: str, u=Depends(current_user)):
    """Опрос раз в секунду: входящие звонки старшего бригады, чьё время пришло."""
    s = _mine(sid, u)
    now = _now()
    out = []
    for e in s["sobytiya"]:
        if e["status"] == "ozhidaet" and now >= e["t"]:
            e["status"] = "zvonit"
        if e["status"] == "zvonit":
            if now - e["t"] > C.DDS["vhodyashchiy_zvonok_zhdat_sec"]:
                e["status"] = "propushchen"
            else:
                out.append({"id": e["id"], "ot": e["kontakt"]["nazvanie"], "faza": e["faza"]})
    return {"vhodyashchie": out, "faza": _faza(s), "t_sec": round(now - s["nachalo"], 1),
            "propushcheno": sum(1 for e in s["sobytiya"] if e["status"] == "propushchen")}


@router.post("/{sid}/incoming/{eid}/answer")
def answer(sid: str, eid: str, u=Depends(current_user)):
    s = _mine(sid, u)
    e = next((x for x in s["sobytiya"] if x["id"] == eid), None)
    if not e or e["status"] != "zvonit":
        raise HTTPException(400, "звонок уже завершён")
    e["status"] = "prinyat"
    _osnovanie(s, e["faza"], "входящий от старшего бригады")
    tekst = "Диспетчер, это старший бригады. " + FAZA_TEKST[e["faza"]]
    cid = f"c{len(s['zvonki']) + 1}"
    s["zvonki"].append({"id": cid, "kontakt": e["kontakt"], "ishod": "otvetil", "nachalo": _now(),
                        "konec": None, "vhodyashchiy": True,
                        "dialog": [{"kto": "abonent", "tekst": tekst, "t": _now()}]})
    _persist(s)
    return {"call_id": cid, "tekst": tekst, "faza": e["faza"]}


# ================================================================ оценка

def ocenit(s: dict) -> dict:
    k = s["kartochka"]
    t0 = s["nachalo"]
    F: list[D.Fakt] = []
    st = s["statusy"]
    first = st[0] if st else None
    prin = next((x for x in st if x["kod"] == "prinyata"), None)
    tp = round(prin["t"] - t0, 1) if prin else None
    F.append(D.Fakt("podtverzhdenie", f"Приём подтверждён за {C.DDS['podtverzhdenie_sec']} с",
                    bool(prin) and first["kod"] == "prinyata" and tp <= C.DDS["podtverzhdenie_sec"], 3,
                    "ПП РФ 1931, п. 9 подп. «р»: подтверждение ДДС — 30 с",
                    {"fakt_sec": tp, "normativ_sec": C.DDS["podtverzhdenie_sec"]}))

    osh = k["_oshibka"]
    zam = s["zamechaniya"]
    if osh:
        hit = next((z for z in zam if z["pole"] == osh["pole"]), None)
        if osh["pole"] == "adres":
            verno = bool(hit) and D.sravnit_adres(hit["verno"], osh["pravda"])["dolya"] >= 0.9
        elif osh["pole"] == "telefon":
            verno = bool(hit) and re.sub(r"\D", "", hit["verno"]) == re.sub(r"\D", "", osh["pravda"])
        else:
            verno = bool(hit) and hit["verno"] in (osh["pravda"], POST_RU.get(osh["pravda"]))
        F.append(D.Fakt("proverka", "Ошибка оператора 112 найдена и исправлена", verno, 3,
                        "Ответ заказчика п. 1.1: ДДС имеет доступ к записи разговора",
                        {"pole": osh["pole"], "v_kartochke": osh["v_kartochke"],
                         "pravda": osh["pravda"], "otmecheno": hit["verno"] if hit else None}))
        lozh = [z for z in zam if z["pole"] != osh["pole"]]
    else:
        F.append(D.Fakt("proverka", "Карточка без ошибок — расхождений не заявлено", not zam, 3,
                        "Ответ заказчика п. 1.1", {"otmecheno": [z["pole"] for z in zam]}))
        lozh = zam
    F.append(D.Fakt("lozhnye", "Нет ложных замечаний", not lozh, 1, "Универсальный алгоритм, п. 3",
                    {"lozhnyh": len(lozh)}))

    p = s.get("peredano") or {}
    ok_p = bool(p) and p.get("adres") and p.get("sut") and (p.get("telefon") or p.get("postradavshie"))
    F.append(D.Fakt("peredacha", "Бригаде переданы адрес, суть и сведения о пострадавших/заявителе",
                    bool(ok_p), 3, "Памятка для ДДС: передача информации исполнителям",
                    {"brigada_vyehala": bool(s.get("t_vyezd")), **p}))

    osn = s["osnovaniya"]
    bez = []
    pozdno = []
    for x in st:
        cfg = next(c for c in C.DDS["statusy"] if c["kod"] == x["kod"])
        o = osn.get(cfg["osnovanie"])
        if not o or o["t"] > x["t"]:
            bez.append(x["nazvanie"])
        elif x["t"] - o["t"] > C.DDS["svoevremenno_sec"] and x["kod"] != "prinyata":
            pozdno.append(f"{x['nazvanie']}: {round(x['t'] - o['t'])} с")
    F.append(D.Fakt("po_faktu", "Статусы только по факту получения информации", bool(st) and not bez, 3,
                    C.DDS["istochnik_statusov"], {"bez_osnovaniya": bez}))
    F.append(D.Fakt("svoevremenno", f"Статус обновлён не позже {C.DDS['svoevremenno_sec']} с после сведений",
                    bool(st) and not pozdno, 2, "Учебный норматив, настраивается", {"pozdno": pozdno}))
    zaversh = any(x["kod"] == "zaversheno" for x in st)
    F.append(D.Fakt("zaversheno", "Реагирование доведено до «Работы завершены»", zaversh, 2,
                    "Памятка для ДДС", {"poslednij": st[-1]["nazvanie"] if st else None}))
    nuzhen = {c["kod"] for c in C.DDS["statusy"] if c["kommentariy"]}
    bez_k = [x["nazvanie"] for x in st if x["kod"] in nuzhen and not x["kommentariy"]]
    F.append(D.Fakt("kommentarii", "Комментарии к статусам заполнены", bool(st) and not bez_k, 1,
                    "Памятка для ДДС", {"bez_kommentariya": bez_k}))
    nar = s["narusheniya_rechi"]
    F.append(D.Fakt("rech", "Правила речи в переговорах", not nar, 1, "Методика МЧС России",
                    {"narusheniy": len(nar), "spisok": nar[:5]}))

    nab = sum(f.ves for f in F if f.proyden)
    vse = sum(f.ves for f in F)
    ball = round(100 * nab / vse)
    grp = {"podtverzhdenie": ["podtverzhdenie"], "proverka": ["proverka", "lozhnye"],
           "peredacha": ["peredacha"], "statusy": ["po_faktu", "svoevremenno", "zaversheno", "kommentarii"],
           "rech": ["rech"]}
    fk = {f.kod: f.proyden for f in F}
    navyki = {g: round(sum(fk[x] for x in ks) / len(ks), 2) for g, ks in grp.items()}
    return {"ball": ball, "nabrano": nab, "vsego": vse, "fakty": [f.to_dict() for f in F],
            "navyki": navyki, "verdikt": "зачёт" if ball >= 70 else "не зачтено",
            "t_podtverzhdeniya": tp}


@router.post("/{sid}/finish")
def finish(sid: str, u=Depends(current_user)):
    s = _mine(sid, u)
    oc = ocenit(s)
    t = round(_now() - s["nachalo"], 1)
    v_norm = oc["t_podtverzhdeniya"] is not None and oc["t_podtverzhdeniya"] <= C.DDS["podtverzhdenie_sec"]
    itog = {"t_sec": t, "v_normativ": v_norm, "kind": "dds", **oc}
    prof = bkt.primenit(u["id"], oc["fakty"]) if u["role"] == "trainee" else {}
    db.ex("UPDATE sessions SET state='done',finished=?,data=?,ball=?,t_sec=?,v_norm=?,itog=? WHERE id=?",
          (_now(), json.dumps(s, ensure_ascii=False, default=str), oc["ball"], t,
           1 if v_norm else 0, json.dumps(itog, ensure_ascii=False), sid))
    LIVE.pop(sid, None)
    return {**itog, "session_id": sid, "bkt": prof,
            "oshibka_bylo": s["kartochka"]["_oshibka"],
            "zapis": s["zapis"]}


def live_rows() -> list[dict]:
    out = []
    for s in list(LIVE.values()):
        usr = db.q1("SELECT name FROM users WHERE id=?", (s["user_id"],))
        st = s["statusy"]
        out.append({"id": s["id"], "user": usr["name"] if usr else "?", "rezhim": "ДДС",
                    "scenario": s["scenario_id"], "t_sec": round(_now() - s["nachalo"], 1),
                    "status": st[-1]["nazvanie"] if st else "не принята",
                    "zvonkov": len(s["zvonki"]), "faza": _faza(s),
                    "zamechaniy": len(s["zamechaniya"]), "narusheniy": len(s["narusheniya_rechi"])})
    return out
