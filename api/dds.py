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

from . import bkt, config as C, db, domain as D, facts, dds_rules, curriculum
from .auth import current_user, role
from . import session_store as store

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
    return facts.victims(sit) or "neizvestno"


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


def sobrat_kartochku(bil: dict, sluzhba: str | None, seed: int | None = None, exercise_profile="standard") -> dict:
    rnd = random.Random(seed)
    gr = C.load("tree").get(bil["gruppa"], {})
    pravda = {"adres": bil["adres_etalon"], "telefon": bil["zayavitel"]["telefon"],
              "fio": bil["zayavitel"]["fio"], "postradavshie": bil.get("victims") or _postradavshie(bil["situaciya"]),
              "opisanie": bil["situaciya"]}
    kart = dict(pravda)
    oshibka = None
    if exercise_profile == "card_check" and rnd.random() < 0.7:
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
    return facts.transmission([{"tekst": tekst}], pravda)


def _faza(s: dict) -> str:
    if s.get("world_phase"):
        return s["world_phase"]
    v = s.get("t_vyezd")
    if v is None:
        return "ne_vyehala"
    dt = _now() - v
    e = s.get("_rules", {}).get("dds", C.DDS)["etapy_sec"]
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
    messages = [{**m, "id": c["id"]+":"+str(i)} for c in s["zvonki"] if c["kontakt"]["id"] == br["id"]
                for i,m in enumerate(c["dialog"]) if m["kto"] == "dispetcher"]
    p = facts.transmission(messages, s["kartochka"]["_pravda"])
    call["peredano"] = p
    if not s.get("brigada") or s["brigada"] == br["id"]:
        s["peredano"] = p
    if not s.get("t_vyezd"):
        if p["adres"]:
            s["t_vyezd"] = _now()
            s["brigada"] = br["id"]
            s["peredano"] = p
            _osnovanie(s, "vyezd", "звонок бригаде")
            e = s.get("_rules", {}).get("dds", C.DDS)["etapy_sec"]
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
        for code,flag,text in (("ne_kompetenciya","outside_competence","Карточка вне нашей компетенции. Передайте в соответствующую службу с пояснением."),
                               ("duplicate","duplicate","Это повторная карточка по уже зарегистрированному происшествию."),
                               ("otkaz","cancel_allowed","Реагирование отменено: необходимость работ не подтвердилась.")):
            if s["bilet"].get(flag):
                _osnovanie(s,code,"сообщение руководителя")
                return text
        if dds_rules.medical(s) and s["bilet"].get("no_dispatch"):
            _osnovanie(s,"zaversheno","сообщение руководителя 103")
            return "Обращение обработано без выезда бригады. Работы завершены, результат внесите в комментарий."
        return "Принял информацию. Держите в курсе."
    return "Слушаю."


# ================================================================ API

class Start(BaseModel):
    scenario_id: str
    assignment_id: int | None = None
    sluzhba: str | None = None
    exercise_profile: str = "standard"
    lesson_id: str | None = None


def _mine(sid: str, u: dict) -> dict:
    s = store.load(sid, u, "dds")
    LIVE[sid] = s
    return s


def _persist(s: dict) -> None:
    store.persist(s)


@router.get("/sluzhby")
def sluzhby(u=Depends(current_user)):
    return _sluzhby()


@router.get("/nastroyki")
def nastroyki(u=Depends(current_user)):
    return {"statusy": C.DDS["statusy"], "podtverzhdenie_sec": C.DDS["podtverzhdenie_sec"],
            "svoevremenno_sec": C.DDS["svoevremenno_sec"],
            "istochnik": C.DDS["istochnik_statusov"]}


@router.post("/session")
@store.command(LIVE, kind="dds")
def start(b: Start, u=Depends(role("trainee", "teacher"))):
    row = db.q1("SELECT * FROM scenarios WHERE id=?", (b.scenario_id,))
    if not row or (row["status"] != "published" and u["role"] == "trainee"):
        raise HTTPException(404, "сценарий недоступен")
    bil = curriculum.enrich(json.loads(row["data"]))
    sl = b.sluzhba
    a = store.assignment(b.assignment_id, u, b.scenario_id, "dds")
    if a:
        if sl and a.get("dds_sluzhba") and sl != a["dds_sluzhba"]:
            raise HTTPException(403, "Служба не соответствует назначению")
        sl = sl or a.get("dds_sluzhba")
    if b.exercise_profile not in ("standard", "card_check"):
        raise HTTPException(422, "Неизвестный профиль упражнения")
    from . import lessons
    if lessons.dispatch_item.get():bil=curriculum.enrich(lessons.dispatch_item.get()["data"])
    policy = lessons.start_policy(u, b.lesson_id, b.scenario_id, "dds")
    sl = policy.get("service") or sl
    if sl and sl not in bil.get("service_profiles",[]) and not bil.get("outside_competence"):
        raise HTTPException(422, "Сценарий не предназначен для выбранной службы")
    k = sobrat_kartochku(bil, sl, exercise_profile=b.exercise_profile)
    sid = str(uuid.uuid4())
    s = {"id": sid, "kind": "dds", "user_id": u["id"], "scenario_id": b.scenario_id,
         "assignment_id": b.assignment_id, "bilet": bil, "nachalo": _now(),
         "lesson_id": b.lesson_id, "lesson_policy": policy, "mode": policy.get("mode", "practice"),
         "helper": {"allowed": policy.get("helper_allowed", True), "enabled":False, "exposed":False},
         "exercise_profile": b.exercise_profile,
         "source_kind":(lessons.dispatch_item.get() or {}).get("source","system"),
         "source_session":(lessons.dispatch_item.get() or {}).get("source_session"),
         "kartochka": k, "zapis": _zapis(bil, k["_pravda"]),
         "kontakty": _kontakty(k["moya_sluzhba"]),
         "osnovaniya": {"postuplenie": {"t": _now(), "via": "карточка поступила"}},
         "statusy": [], "zamechaniya": [], "zvonki": [], "sobytiya": [],
         "brigada": None, "t_vyezd": None, "peredano": None, "narusheniya_rechi": []}
    LIVE[sid] = s
    store.freeze(s)
    db.ex("INSERT INTO sessions(id,user_id,scenario_id,kind,assignment_id,started,state,data) "
          "VALUES(?,?,?,?,?,?,?,?)",
          (sid, u["id"], b.scenario_id, "dds", b.assignment_id, s["nachalo"], "live",
           json.dumps(s, ensure_ascii=False, default=str)))
    store.event(s, "session_started", {"versions": s["versions"], "kind": "dds"})
    store.snapshot(s)
    return {"session_id": sid, "started": s["nachalo"], "server_time": _now(), "exercise_profile": s["exercise_profile"], "kartochka": _publichnaya(k), "zapis": s["zapis"],
            "kontakty": s["kontakty"], "statusy": [x for x in C.DDS["statusy"] if not dds_rules.medical(s) or x["kod"] not in ("ne_prinyata","otkaz")],
            "podtverzhdenie_sec": C.DDS["podtverzhdenie_sec"]}


class Status(BaseModel):
    status: str
    kommentariy: str = ""


@router.post("/{sid}/status")
@store.command(LIVE, kind="dds")
def status(sid: str, b: Status, u=Depends(current_user)):
    """Статус ставится всегда — проверка «по факту» идёт в оценке, как в жизни."""
    s = _mine(sid, u)
    st = next((x for x in s.get("_rules", {}).get("dds", C.DDS)["statusy"] if x["kod"] == b.status), None)
    if not st:
        raise HTTPException(400, "нет такого статуса")
    if dds_rules.medical(s) and b.status in ("ne_prinyata", "otkaz"):
        raise HTTPException(422, "Эти статусы неприменимы к службе 103")
    s["statusy"].append({"id": uuid.uuid4().hex, "kod": st["kod"], "nazvanie": st["nazvanie"], "t": _now(),
                         "kommentariy": b.kommentariy.strip()})
    for x in s["kartochka"]["sluzhby"]:
        if x["moya"]:
            x["status"] = st["nazvanie"]
    _persist(s)
    osn = dds_rules.status_reason(s, st["kod"])
    return {"ok": True, "osnovanie_bylo": bool(osn),
            "preduprezhdenie": None if osn else
            "Основания для этого статуса ещё не было. Памятка требует ставить статус по факту."}


class Zamech(BaseModel):
    pole: str
    verno: str
    kommentariy: str = ""


@router.post("/{sid}/zamechanie")
@store.command(LIVE, kind="dds")
def zamechanie(sid: str, b: Zamech, u=Depends(current_user)):
    """Диспетчер отмечает ошибку оператора 112 в карточке (универсальный алгоритм, п. 3)."""
    s = _mine(sid, u)
    if s.get("exercise_profile") != "card_check":
        raise HTTPException(403, "Проверка чужого заполнения — отдельное упражнение")
    s["zamechaniya"].append({"pole": b.pole, "verno": b.verno.strip(),
                             "kommentariy": b.kommentariy, "t": _now()})
    _persist(s)
    return {"ok": True}


class Zvonok(BaseModel):
    kontakt: str


@router.post("/{sid}/call")
@store.command(LIVE, kind="dds")
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
@store.command(LIVE, kind="dds")
def say(sid: str, cid: str, b: Say, u=Depends(current_user)):
    s = _mine(sid, u)
    c = next((x for x in s["zvonki"] if x["id"] == cid), None)
    if not c or c["konec"] or c["ishod"] != "otvetil":
        raise HTTPException(400, "звонок не активен")
    nar = D.proverit_rech(b.tekst, s.get("_rules", {}).get("speech"))
    if nar:
        s["narusheniya_rechi"] += [{**n, "t": _now()} for n in nar]
    c["dialog"].append({"kto": "dispetcher", "tekst": b.tekst, "t": _now()})
    otv = _otvet(s, c, b.tekst)
    c["dialog"].append({"kto": "abonent", "tekst": otv, "t": _now()})
    _persist(s)
    return {"otvet": otv, "narusheniya_rechi": nar, "vyezd": bool(s.get("t_vyezd"))}


@router.post("/{sid}/call/{cid}/end")
@store.command(LIVE, kind="dds")
def end(sid: str, cid: str, u=Depends(current_user)):
    s = _mine(sid, u)
    c = next((x for x in s["zvonki"] if x["id"] == cid), None)
    if c and not c["konec"]:
        c["konec"] = _now()
        _persist(s)
    return {"ok": True}


@router.get("/{sid}/events")
@store.command(LIVE, kind="dds")
def events(sid: str, u=Depends(current_user)):
    """Опрос раз в секунду: входящие звонки старшего бригады, чьё время пришло."""
    s = _mine(sid, u)
    now = _now()
    out = []
    for e in s["sobytiya"]:
        if e["status"] == "ozhidaet" and now >= e["t"]:
            e["status"] = "zvonit"
        if e["status"] == "zvonit":
            if now - e["t"] > s.get("_rules", {}).get("dds", C.DDS)["vhodyashchiy_zvonok_zhdat_sec"]:
                e["status"] = "propushchen"
            else:
                out.append({"id": e["id"], "ot": e["kontakt"]["nazvanie"]})
    _persist(s)
    return {"vhodyashchie": out, "faza": known_phase(s), "t_sec": round(now - s["nachalo"], 1),
            "propushcheno": sum(1 for e in s["sobytiya"] if e["status"] == "propushchen"), "notices":s.get("visible_notices",[])}


@router.post("/{sid}/incoming/{eid}/answer")
@store.command(LIVE, kind="dds")
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
    if s.get("versions", {}).get("rubric") == "112-quality-1":
        from .legacy_scoring import ocenit_dds
        return ocenit_dds(s)
    from .dds_rules import assess
    return assess(s)


@router.post("/{sid}/finish")
@store.command(LIVE, kind="dds", complete=True)
def finish(sid: str, u=Depends(current_user)):
    s = _mine(sid, u)
    _persist(s)
    oc = ocenit(s)
    t = round(_now() - s["nachalo"], 1)
    v_norm = oc["t_podtverzhdeniya"] is not None and oc["t_podtverzhdeniya"] <= s.get("_rules", {}).get("dds", C.DDS)["podtverzhdenie_sec"]
    itog = {"t_sec": t, "v_normativ": v_norm, "kind": "dds", "assisted":bool(s.get("helper",{}).get("exposed")), "mode":s.get("mode","practice"), "parent_session":s.get("parent_session"), **oc}
    prof = bkt.primenit(u["id"], oc["fakty"], "dds") if u["role"] == "trainee" and store.independent(s) and oc.get('rubric_version')==D.Q.RUBRIC_VERSION else {}
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


def known_phase(s):
    return next((code for code in ("zaversheno","raboty","pribytie","vyezd") if code in s.get("osnovaniya",{})),"ne_vyehala")
