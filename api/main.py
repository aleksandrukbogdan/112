"""
Тренажёр оператора ДДС · Москва. API.

Роли: admin / teacher / trainee. Хранилище: SQLite на томе.
Модель и голос — внешние сервисы; при их отсутствии всё продолжает работать.
"""
from __future__ import annotations

import json
import asyncio
import copy
import os
import re
import shutil
import time
import uuid
import hashlib
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from . import auth, bkt, caller, config as C, db, dds, domain as D, generator, reports
from .auth import current_user, role
from . import quality as Q
from . import session_store as store
from . import insights
from . import forecast_ml
from . import access, curriculum, lessons, helper, training, materials, scenario_tools, analytics

VOICE_URL = os.environ.get("VOICE_URL", "http://voice:8090")      # Vosk (запасной ASR) + Piper (TTS)
ASR_URL = os.environ.get("ASR_URL", "http://asr:8091")            # GigaAM на GPU — основной ASR
ASR_ENGINE = os.environ.get("ASR_ENGINE", "auto")                 # auto | gigaam | vosk

app = FastAPI(title="Тренажёр оператора ДДС · Москва", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

app.include_router(dds.router)
app.include_router(lessons.router)
for extra in (helper,training,materials,scenario_tools,analytics):
    app.include_router(extra.router)

LIVE: dict[str, dict] = {}
NEED = ["opisanie", "adres_polny", "zayavitel_fio", "zayavitel_telefon", "tip_kod"]


@app.middleware("http")
async def serialize_commands(request, call_next):
    # No class-wide lock. Atomic state commands lock their own durable records.
    key = request.headers.get("Idempotency-Key")
    ctx = None
    if key and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if len(key)>128:
            return Response('{"detail":"Invalid command key"}', status_code=422, media_type="application/json")
        body = await request.body()
        ctx = {"key":key, "path":request.method+":"+request.url.path, "body_hash":hashlib.sha256(body).hexdigest()}
    token = store.request_command.set(ctx)
    try:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        store.request_command.reset(token)


def reload_live():
    LIVE.clear()
    dds.LIVE.clear()
    for row in db.q("SELECT * FROM sessions WHERE state='live'"):
        with db.tx():
            row = db.lock_row('sessions','id',row['id'])
            if row['state'] != 'live':continue
            state = json.loads(row['data'])
            if not state.get('versions'):
                state['snapshot_origin'] = 'upgrade_not_original'
                store.freeze(state)
                state['versions']['rubric'] = '112-quality-1'
                state['_rules']['rubric'] = '112-quality-1'
                state['versions']['rules'] = store.content('rules',state['_rules'])
                store.persist(state)
            (dds.LIVE if row.get('kind') == 'dds' else LIVE)[row['id']] = state


# ================================================================ старт

@app.on_event("startup")
def startup():
    db.conn()
    with db.tx() as boot_tx:
        if db.PG:boot_tx.execute('SELECT pg_advisory_xact_lock(112202610)')
        made = auth.ensure_defaults()
        if made:
            print("\n" + "=" * 60 + "\n  ПЕРВЫЙ ЗАПУСК — созданы учётные записи:", flush=True)
            for m in made:
                print(f"    {m['role']:8} {m['login']:10} пароль: {m['password']}", flush=True)
            print("  Смените пароли в разделе «Администрирование».\n" + "=" * 60, flush=True)
        if not db.q1("SELECT id FROM scenarios WHERE source='bilet' LIMIT 1"):
            with db.tx() as c:
                for b in C.load("bilety"):
                    c.execute("INSERT INTO scenarios(id,source,status,data,created,validated,comment) "
                              "VALUES(?,?,?,?,?,?,?)",
                              (b["id"], "bilet", "published", json.dumps(b, ensure_ascii=False),
                               time.time(), time.time(), "банк билетов ДГОЧСиПБ"))
        curriculum.migrate_bank()
        access.bootstrap()
    reload_live()
    if os.environ.get("SCHEDULER_ENABLED", "true").lower()=="true":lessons.start_scheduler()
    db.start_backup_thread(int(os.environ.get("BACKUP_PERIOD_SEC", 86400)))


def tayming() -> int:
    return int(db.setting("uchebny_tayming_sec", C.UCHEBNY_TAYMING_SEC))


# ================================================================ служебное

async def _get(url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=3) as cl:
            r = await cl.get(url)
            return r.json()
    except Exception:
        return None


async def voice_health() -> dict:
    """
    Три компонента речи: GigaAM (GPU), Vosk (CPU, запасной), Piper (синтез).
    Распознавание работает, если жив хотя бы один движок.
    """
    g = await _get(f"{ASR_URL}/health") if ASR_ENGINE in ("auto", "gigaam") else None
    v = await _get(f"{VOICE_URL}/health")
    giga = bool(g and g.get("ok"))
    vosk = bool(v and v.get("asr")) and ASR_ENGINE in ("auto", "vosk")
    tts = bool(v and v.get("tts"))
    engine = "gigaam" if giga else "vosk" if vosk else None
    note = None
    if not engine and not tts:
        note = "голосовые сервисы не запущены — работает текстовый режим"
    elif g and not giga:
        note = "GigaAM не загрузился: " + str(g.get("error"))[:160] + (" — работает Vosk" if vosk else "")
    elif not giga and ASR_ENGINE != "vosk":
        note = "GigaAM не запущен (make up-gpu) — распознаёт Vosk" if vosk else None
    return {"ok": bool(engine) and tts, "asr": bool(engine), "tts": tts,
            "engine": engine, "tts_engine": v.get("tts_engine") if v else None, "gigaam": g, "vosk": vosk, "note": note}


@app.get("/health")
async def health():
    try:
        db.q1("SELECT 1 AS x")
        dbs = f"{db.backend()} — работает"
    except Exception as e:
        dbs = f"{db.backend()} — ОШИБКА: {e}"[:160]
    return {"ok": "ОШИБКА" not in dbs, "db": dbs, "llm": await caller.zdorov(), "voice": await voice_health(),
            "svodka": C.svodka(), "zanyatiy_idyot": db.q1("SELECT COUNT(*) n FROM sessions WHERE state='live'")["n"],
            "zaversheno": db.q1("SELECT COUNT(*) n FROM sessions WHERE state='done'")["n"]}


# ================================================================ вход

class Login(BaseModel):
    login: str
    password: str


@app.post("/api/login")
def login(b: Login):
    u = db.q1("SELECT * FROM users WHERE login=?", (b.login.strip().lower(),))
    if not u or not u["active"] or not auth.check_pw(b.password, u["pw_hash"]):
        db.audit(None, "login_fail", {"login": b.login})
        raise HTTPException(401, "неверный логин или пароль")
    db.audit(u, "login")
    return {"token": auth.make_token(u["id"]),
            "user": {k: u[k] for k in ("id", "login", "name", "role", "group_id")}}


@app.get("/api/me")
def me(u=Depends(current_user)):
    return u


class NewPw(BaseModel):
    old: str
    new: str


@app.post("/api/me/password")
def change_pw(b: NewPw, u=Depends(current_user)):
    row = db.q1("SELECT pw_hash FROM users WHERE id=?", (u["id"],))
    if not auth.check_pw(b.old, row["pw_hash"]):
        raise HTTPException(400, "старый пароль неверен")
    if len(b.new) < 6:
        raise HTTPException(400, "пароль короче 6 символов")
    db.ex("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(b.new), u["id"]))
    db.audit(u, "password_change")
    return {"ok": True}


# ================================================================ справочники

@app.get("/api/config")
def api_config(u=Depends(current_user)):
    return {"svodka": C.svodka(), "normativy": C.NORM, "uchebny_tayming_sec": tayming(),
            "routes": C.ROUTES, "skills": C.SKILLS, "dds": C.DDS,
            "chto_sluchilos": C.load("chto_sluchilos"),
            "gruppy": {k: v["nazvanie"] for k, v in C.load("tree").items()}}


@app.get("/api/tree")
def tree(gruppa: str | None = None, u=Depends(current_user)):
    t = C.load("tree")
    if gruppa:
        if gruppa not in t:
            raise HTTPException(404, "нет такой группы")
        return t[gruppa]
    return {k: {"kod": v["kod"], "nazvanie": v["nazvanie"]} for k, v in t.items()}


@app.get("/api/classifier/search")
def cls_search(q: str = "", u=Depends(current_user)):
    ql, out = q.lower().strip(), []
    for kod, it in C.load("index").items():
        s = f"{it['itog']} {it['pr1']} {it['pr2']} {it['pr3']}".lower()
        if not ql or ql in s:
            out.append({"kod": kod, "itog": it["itog"], "gruppa": it["g"],
                        "put": " → ".join(x for x in (it["pr1"], it["pr2"], it["pr3"])
                                          if x and x != "—")})
        if len(out) >= 60:
            break
    return out


@app.get("/api/spravka")
def spravka(u=Depends(current_user)):
    n = C.NORM
    return {"razdely": [
        {"nazvanie": "Порядок приёма вызова", "punkty": [
            "Представиться: «Служба 112, оператор слушает вас».",
            "Выяснить, что случилось, — со слов заявителя.",
            "Установить точный адрес. Ориентир — не адрес: уточнить улицу, дом, корпус, строение, километр.",
            "Выяснить наличие пострадавших и угрозу людям.",
            "Записать ФИО и телефон заявителя.",
            "Выбрать тип происшествия по классификатору — службы подтянутся автоматически.",
            "Проверить состав служб и отправить карточку.",
            f"Уложиться в {n['kartochka_sec']} с — норматив ПП РФ 1931."]},
        {"nazvanie": "Нормативы времени (ПП РФ 1931, п. 9 подп. «р»)", "punkty": [
            f"Среднее ожидание ответа — {n['otvet_sredniy_sec']} с, максимальное — {n['otvet_max_sec']} с.",
            f"Опрос и доступность карточки в ДДС — {n['kartochka_sec']} с.",
            f"Подтверждение приёма диспетчером ДДС — {n['podtverzhdenie_dds_sec']} с.",
            f"При обрыве — обратный вызов в течение {n['obratny_vyzov_sec']} с, не менее {n['obratny_vyzov_popytok']} попыток.",
            "Консультация — 2 минуты, психологическая поддержка — до 30 минут."]},
        {"nazvanie": "Правила речи (методика МЧС России)", "punkty": [
            "Говорить спокойно, чётко, короткими фразами, в побудительном наклонении.",
            "Избегать частицы «не»: вместо «не паникуйте» — «успокойтесь, слушайте меня».",
            "Исключить слова «паника», «катастрофа», «ужас».",
            "Без сленга, жаргона, раздражения. Обращаться к заявителю по имени."]},
        {"nazvanie": "Условная маршрутизация (классификатор 0.46.24)", "punkty": [
            "Службы по типу происшествия подтягиваются автоматически.",
            "СМП зависит от признака «Пострадавшие»: нет / есть / не на месте.",
            "Полиция подключается при признаке «Правонарушение».",
            "Службы можно добавить вручную: Alt+1…6."]}],
        "istochniki": ["ПП РФ от 12.11.2021 № 1931", "Приказ МЧС России от 14.03.2022 № 192",
                       "Классификатор происшествий ДГОЧСиПБ v0.46.24",
                       "Методика МЧС России по психологической поддержке",
                       "APCO/NENA ANS 1.107.2-2025"]}


# ================================================================ сценарии

def _sc(row: dict) -> dict:
    d = json.loads(row["data"])
    d.update({"status": row["status"], "source": row["source"], "comment": row.get("comment")})
    d["nazvanie"] = (f"Билет {d['bilet']}, вызов {d['vyzov']}" if d.get("bilet")
                     else f"Сгенерирован: {str(d.get('tip_etalon', ''))[:48]}")
    return d


@app.get("/api/scenarios")
def scenarios(status: str | None = None, u=Depends(current_user)):
    if u["role"] == "trainee":
        rows = db.q("SELECT * FROM scenarios WHERE status='published' ORDER BY source, id")
    elif status:
        rows = db.q("SELECT * FROM scenarios WHERE status=? ORDER BY source, id", (status,))
    else:
        rows = db.q("SELECT * FROM scenarios ORDER BY CASE status WHEN 'draft' THEN 0 ELSE 1 END, source, id")
    rows=[r for r in rows if u['role']!='teacher' or r['status']=='published' or r.get('created_by') in (None,u['id'])]
    return [Q.public_scenario(_sc(r)) if u["role"] == "trainee" else _sc(r) for r in rows]


class Gen(BaseModel):
    kod: str
    slozhnost: int = 3
    utochnenie: bool = True
    kolichestvo: int = 1


@app.post("/api/scenarios/generate")
async def gen(b: Gen, u=Depends(role("teacher"))):
    out = []
    for _ in range(max(1, min(b.kolichestvo, 5))):
        try:
            s = await generator.sgenerirovat(b.kod, b.slozhnost, b.utochnenie, u["id"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.ex("INSERT INTO scenarios(id,source,status,data,created_by,created) VALUES(?,?,?,?,?,?)",
              (s["id"], "gen", "draft", json.dumps(s, ensure_ascii=False), u["id"], time.time()))
        out.append(s)
    db.audit(u, "scenario_generate", {"kod": b.kod, "n": len(out)})
    return out


class Valid(BaseModel):
    status: str
    data: dict[str, Any] | None = None
    comment: str | None = None


@app.post("/api/scenarios/{sid}/validate")
def validate(sid: str, b: Valid, u=Depends(role("teacher"))):
    """ТЗ: подтверждение сгенерированных эталонов — только преподаватель."""
    if b.status not in ("published", "rejected"):
        raise HTTPException(400, "status: published | rejected")
    row = db.q1("SELECT * FROM scenarios WHERE id=?", (sid,))
    access.scenario(u,row,edit=True)
    data = json.loads(row["data"])
    if b.data:
        for k in ("situaciya", "adres_vidimy", "adres_etalon", "slozhnost"):
            if k in b.data and b.data[k] not in (None, ""):
                data[k] = b.data[k]
        if isinstance(b.data.get("zayavitel"), dict):
            data["zayavitel"].update(b.data["zayavitel"])
        data["trebuet_utochneniya"] = data["adres_vidimy"].strip() != data["adres_etalon"].strip()
    errors=curriculum.validate(data)
    if b.status=="published" and errors:raise HTTPException(422,errors)
    db.ex("UPDATE scenarios SET status=?,data=?,validated_by=?,validated=?,comment=? WHERE id=?",
          (b.status, json.dumps(data, ensure_ascii=False), u["id"], time.time(), b.comment, sid))
    db.audit(u, f"scenario_{b.status}", {"id": sid, "comment": b.comment})
    return {"ok": True}


# ================================================================ группы и пользователи

@app.get("/api/groups")
def groups(u=Depends(role("admin", "teacher"))):
    rows = db.q("SELECT g.*, (SELECT COUNT(*) FROM users WHERE group_id=g.id AND role='trainee') n "
                "FROM groups g ORDER BY name")
    return rows if u["role"]=="admin" else [r for r in rows if r["id"] in access.groups(u)]


class NewGroup(BaseModel):
    name: str


@app.post("/api/groups")
def add_group(b: NewGroup, u=Depends(role("admin"))):
    if not b.name.strip():
        raise HTTPException(400, "пустое название")
    try:
        gid = db.ex("INSERT INTO groups(name,created) VALUES(?,?)", (b.name.strip(), time.time()))
    except Exception:
        raise HTTPException(400, "такая группа уже есть")
    db.audit(u, "group_create", {"name": b.name})
    return {"id": gid}


@app.get("/api/users")
def users(u=Depends(role("admin", "teacher"))):
    rows = db.q("SELECT u.id,u.login,u.name,u.role,u.group_id,u.active,g.name AS gruppa "
                "FROM users u LEFT JOIN groups g ON g.id=u.group_id ORDER BY u.role,u.name")
    return rows if u["role"]=="admin" else [r for r in rows if r["group_id"] in access.groups(u) or r["id"]==u["id"]]


class NewUser(BaseModel):
    login: str
    name: str
    role: str
    password: str
    group_id: int | None = None


@app.post("/api/users")
def add_user(b: NewUser, u=Depends(role("admin"))):
    if b.role not in ("admin", "teacher", "trainee"):
        raise HTTPException(400, "неверная роль")
    if len(b.password) < 6:
        raise HTTPException(400, "пароль короче 6 символов")
    if not b.login.strip() or not b.name.strip():
        raise HTTPException(400, "логин и имя обязательны")
    try:
        uid = db.ex("INSERT INTO users(login,name,role,pw_hash,group_id,created) VALUES(?,?,?,?,?,?)",
                    (b.login.strip().lower(), b.name.strip(), b.role, auth.hash_pw(b.password),
                     b.group_id if b.role == "trainee" else None, time.time()))
    except Exception:
        raise HTTPException(400, "такой логин уже есть")
    db.audit(u, "user_create", {"login": b.login, "role": b.role})
    return {"id": uid}


class EditUser(BaseModel):
    active: bool | None = None
    group_id: int | None = None
    password: str | None = None
    name: str | None = None


@app.post("/api/users/{uid}")
def edit_user(uid: int, b: EditUser, u=Depends(role("admin"))):
    if uid == u["id"] and b.active is False:
        raise HTTPException(400, "нельзя отключить себя")
    if b.active is not None:
        db.ex("UPDATE users SET active=? WHERE id=?", (1 if b.active else 0, uid))
    if b.group_id is not None:
        db.ex("UPDATE users SET group_id=? WHERE id=?", (b.group_id or None, uid))
    if b.name:
        db.ex("UPDATE users SET name=? WHERE id=?", (b.name, uid))
    if b.password:
        if len(b.password) < 6:
            raise HTTPException(400, "пароль короче 6 символов")
        db.ex("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(b.password), uid))
    db.audit(u, "user_edit", {"id": uid, "active": b.active, "group_id": b.group_id,
                              "password_reset": bool(b.password)})
    return {"ok": True}


# ================================================================ назначения

@app.get("/api/assignments")
def assignments(u=Depends(current_user)):
    if u["role"] == "trainee":
        rows = db.q("SELECT * FROM assignments WHERE group_id=? ORDER BY created DESC",
                    (u["group_id"] or -1,))
    else:
        rows = db.q("SELECT a.*, g.name gruppa FROM assignments a "
                    "LEFT JOIN groups g ON g.id=a.group_id ORDER BY a.created DESC")
    if u["role"]=="teacher":rows=[r for r in rows if r["group_id"] in access.groups(u)]
    for r in rows:
        sc = db.q1("SELECT * FROM scenarios WHERE id=?", (r["scenario_id"],))
        r["scenario"] = _sc(sc) if sc else None
        if r["scenario"] and u["role"] == "trainee":
            r["scenario"] = Q.public_scenario(r["scenario"])
        if u["role"] == "trainee":
            r["vypolneno"] = db.q1(
                "SELECT id,itog,ball,override_ball FROM sessions WHERE user_id=? AND assignment_id=? "
                "AND state='done' ORDER BY finished DESC LIMIT 1", (u["id"], r["id"]))
            if r["vypolneno"]:
                r["vypolneno"]["passed"] = insights.effective(r["vypolneno"]).get("passed")
                r["vypolneno"].pop("itog", None)
    return rows


class NewAssign(BaseModel):
    group_id: int
    scenario_ids: list[str]
    rezhim: str = "ops112"
    dds_sluzhba: str | None = None
    tayming_sec: int | None = None
    deadline: str | None = None


@app.post("/api/assignments")
def add_assign(b: NewAssign, u=Depends(role("teacher"))):
    access.group(u,b.group_id)
    n = 0
    for sid in b.scenario_ids:
        row = db.q1("SELECT status FROM scenarios WHERE id=?", (sid,))
        if not row or row["status"] != "published":
            continue
        db.ex("INSERT INTO assignments(group_id,scenario_id,rezhim,dds_sluzhba,tayming_sec,deadline,"
              "created_by,created) VALUES(?,?,?,?,?,?,?,?)",
              (b.group_id, sid, b.rezhim if b.rezhim in ("ops112", "dds") else "ops112",
               b.dds_sluzhba, b.tayming_sec, b.deadline, u["id"], time.time()))
        n += 1
    db.audit(u, "assign", {"group": b.group_id, "n": n})
    return {"naznacheno": n}


@app.delete("/api/assignments/{aid}")
def del_assign(aid: int, u=Depends(role("teacher"))):
    row=db.q1("SELECT * FROM assignments WHERE id=?",(aid,))
    if not row:raise HTTPException(404,"Назначение не найдено")
    access.group(u,row["group_id"])
    if row["created_by"]!=u["id"]:raise HTTPException(403,"Чужое назначение")
    db.ex("DELETE FROM assignments WHERE id=?", (aid,))
    db.audit(u, "assign_delete", {"id": aid})
    return {"ok": True}


# ================================================================ занятие

def _persist(sid: str) -> None:
    store.persist(LIVE[sid])


def _live(sid: str, u: dict) -> dict:
    s=store.load(sid,u,"ops112")
    LIVE[sid]=s
    return s


class Start(BaseModel):
    scenario_id: str
    assignment_id: int | None = None
    lesson_id: str | None = None


@app.post("/api/session")
@store.command(LIVE)
def start(b: Start, u=Depends(role("trainee", "teacher"))):
    row = db.q1("SELECT * FROM scenarios WHERE id=?", (b.scenario_id,))
    if not row or (row["status"] != "published" and u["role"] == "trainee"):
        raise HTTPException(404, "сценарий недоступен")
    sc = curriculum.enrich(json.loads(row["data"]))
    if lessons.dispatch_item.get():sc=curriculum.enrich(lessons.dispatch_item.get()["data"])
    policy=lessons.start_policy(u,b.lesson_id,b.scenario_id,"ops112")
    tm = policy.get("card_sec", tayming())
    a = store.assignment(b.assignment_id, u, b.scenario_id, "ops112")
    if a and a["tayming_sec"]:
        tm = a["tayming_sec"]
    sid = str(uuid.uuid4())
    s = {"id": sid, "user_id": u["id"], "bilet": sc, "scenario_id": b.scenario_id,
         "assignment_id": b.assignment_id, "kind":"ops112", "nachalo": time.time(), "dialog": [],
         "lesson_id":b.lesson_id, "lesson_policy":policy, "mode":policy.get("mode","practice"),
         "source_kind":(lessons.dispatch_item.get() or {}).get("source","system"),
         "source_session":(lessons.dispatch_item.get() or {}).get("source_session"),
         "helper":{"allowed":policy.get("helper_allowed",True),"enabled":False,"exposed":False},
         "kartochka": {}, "narusheniya_rechi": [], "t_adres_ms": None, "t_tip_ms": None,
         "t_otpravki_sec": None, "adres_raskryt": False, "tayming_sec": tm}
    LIVE[sid] = s
    store.freeze(s)
    db.ex("INSERT INTO sessions(id,user_id,scenario_id,assignment_id,started,state,data) "
          "VALUES(?,?,?,?,?,?,?)", (sid, u["id"], b.scenario_id, b.assignment_id,
                                    s["nachalo"], "live", json.dumps(s, ensure_ascii=False)))
    store.event(s, "session_started", {"versions": s["versions"], "kind": "ops112"})
    store.snapshot(s)
    return {"session_id": sid, "started":s["nachalo"], "server_time":time.time(), "privetstvie": "Служба 112, оператор слушает вас.",
            "normativ_sec": C.NORM["kartochka_sec"], "tayming_sec": tm,
            "scenario": {k: sc.get(k) for k in ("id", "bilet", "vyzov", "slozhnost",
                                                 "situaciya", "trebuet_utochneniya")}}


class Replika(BaseModel):
    tekst: str


@app.post("/api/session/{sid}/replika")
async def replika(sid: str, b: Replika, u=Depends(current_user)):
    if not b.tekst.strip() or len(b.tekst)>5000:raise HTTPException(422,"Реплика: от 1 до 5000 символов")
    turn=uuid.uuid4().hex
    with db.tx():
        db.lock_row("sessions","id",sid)
        cached=store.receipt(u["id"])
        if cached is not None:return cached
        s=_live(sid,u)
        if s.get("pending_reply") and time.time()-s["pending_reply"]["ts"]<90:
            raise HTTPException(409,"Предыдущая реплика ещё обрабатывается")
        t_ms=int((time.time()-s["nachalo"])*1000)
        nar=D.proverit_rech(b.tekst,s.get("_rules",{}).get("speech"))
        s["narusheniya_rechi"].extend([{**n,"t_ms":t_ms} for n in nar])
        s["dialog"].append({"id":turn,"kto":"operator","tekst":b.tekst,"t_ms":t_ms})
        bil=copy.deepcopy(s["bilet"])
        utoch=bil["trebuet_utochneniya"] and caller.est_utochnenie(b.tekst)
        if utoch:s["adres_raskryt"]=True
        s["pending_reply"]={"id":turn,"ts":time.time()}
        store.persist(s);history=copy.deepcopy(s["dialog"])
    try:
        otv=await caller.otvet(bil,history,b.tekst,stress=min(5,bil["slozhnost"]))
    except Exception:
        otv=caller._bez_llm(bil,b.tekst,utoch)
    with db.tx():
        db.lock_row("sessions","id",sid)
        s=_live(sid,u)
        if s.get("pending_reply",{}).get("id")!=turn:raise HTTPException(409,"Ответ устарел")
        s.pop("pending_reply",None)
        s["dialog"].append({"id":uuid.uuid4().hex,"kto":"caller","tekst":otv,"t_ms":int((time.time()-s["nachalo"])*1000)})
        store.persist(s);LIVE[sid]=s
        answer={"otvet":otv,"narusheniya_rechi":nar,"adres_raskryt":s["adres_raskryt"],"podskazka":None}
        store.save_receipt(u["id"],answer)
    return answer


class Pole(BaseModel):
    key: str
    value: Any
    expected_version: int | None = None
    client_id: str | None = Field(default=None,max_length=100)
    client_seq: int | None = Field(default=None,ge=0)


def _pereschitat(s: dict) -> dict:
    """СМП и полиция в классификаторе условны: три колонки СМП, признак правонарушения."""
    k = s["kartochka"]
    poz = store.index(s).get(str(k.get("tip_kod", "")))
    if not poz:
        k["sluzhby"] = []
        return {}
    sl = list(poz["sluzhby"])
    pr = {x: "безусловно по классификатору" for x in sl}
    post = k.get("postradavshie")
    if post and poz.get("smp", {}).get(post):
        sl.append("SMP")
        pr["SMP"] = {"net": "пострадавших нет", "est": "есть пострадавшие",
                     "ne_na_meste": "пострадавшие не на месте"}[post]
    if k.get("pravonarushenie") and poz.get("mvd_pri_pravonarushenii"):
        sl.append("MVD")
        pr["MVD"] = "признак правонарушения"
    sl = sorted(set(sl))
    k["sluzhby"] = sl
    return {"sluzhby": sl, "itog": poz["itog"], "prichiny": pr, "istochnik": "классификатор 0.46.24"}


@app.post("/api/session/{sid}/pole")
@store.command(LIVE)
def pole(sid: str, b: Pole, u=Depends(current_user)):
    Q.validate_card({b.key: b.value})
    s = _live(sid, u)
    if b.expected_version is not None and b.expected_version != s.get("field_versions",{}).get(b.key,0):
        raise HTTPException(409,"Поле изменилось: обновите карточку")
    if b.client_id and b.client_seq is not None:
        stamp=b.client_id+":"+b.key
        prior=s.setdefault('manual_sequences',{}).get(stamp,-1)
        if b.client_seq<=prior:return {"ok":True,"ignored_stale":True,"field_versions":s.get('field_versions',{})}
        s['manual_sequences'][stamp]=b.client_seq
    t_ms = int((time.time() - s["nachalo"]) * 1000)
    s["kartochka"][b.key] = b.value
    if b.key == "adres_polny" and s["t_adres_ms"] is None and str(b.value).strip():
        s["t_adres_ms"] = t_ms
    if b.key == "tip_kod" and s["t_tip_ms"] is None and str(b.value).strip():
        s["t_tip_ms"] = t_ms
    avto = _pereschitat(s) if b.key in ("tip_kod", "postradavshie", "pravonarushenie") else {}
    _persist(sid)
    return {"ok": True, "avto": avto, "field_versions":s.get("field_versions",{}), "revision":s.get("revision")}


@app.get("/api/session/{sid}/prognoz")
@store.command(LIVE)
def prognoz(sid: str, u=Depends(current_user)):
    s = _live(sid, u)
    t = time.time() - s["nachalo"]
    p = forecast_ml.forecast(s, t, D.prognoz_v_vyzove(s, t))
    p["id"] = db.ex("INSERT INTO forecasts(kind,subject,created,data) VALUES(?,?,?,?)",
                    ("call", sid, time.time(), json.dumps(p)))
    return p


class Otpravka(BaseModel):
    sluzhby: list[str] = []
    kartochka: dict[str, Any] = {}


@app.post("/api/session/{sid}/otpravit")
@store.command(LIVE, complete=True)
def otpravit(sid: str, b: Otpravka, u=Depends(current_user)):
    Q.validate_card(b.kartochka)
    s = _live(sid, u)
    t = round(time.time() - s["nachalo"], 1)
    s["t_otpravki_sec"] = t
    for k, v in (b.kartochka or {}).items():
        s["kartochka"][k] = v
        if k == "adres_polny" and s["t_adres_ms"] is None and str(v).strip():
            s["t_adres_ms"] = int(t * 1000)
    if b.sluzhby:
        Q.validate_card({"sluzhby": b.sluzhby})
        s["kartochka"]["sluzhby"] = b.sluzhby
    _persist(sid)
    oc = D.ocenit(s)
    itog = {"t_sec": t, "v_normativ": t <= s["_rules"]["norm"]["kartochka_sec"], "assisted":bool(s.get("helper",{}).get("exposed")), "mode":s.get("mode","practice"), "parent_session":s.get("parent_session"), **oc}

    for f in db.q("SELECT * FROM forecasts WHERE kind='call' AND subject=? AND fakt IS NULL", (sid,)):
        p = json.loads(f["data"])
        verno = (p["veroyatnost"] >= 0.5) == itog["v_normativ"]
        db.ex("UPDATE forecasts SET fakt=?,fakt_at=?,verno=? WHERE id=?",
              (json.dumps({"uspel": itog["v_normativ"], "t_sec": t}), time.time(),
               1 if verno else 0, f["id"]))

    prof, att = {}, None
    if u["role"] == "trainee" and store.independent(s) and oc.get('rubric_version')==Q.RUBRIC_VERSION:
        prof = bkt.primenit(u["id"], oc["fakty"])
        att = bkt.prognoz_attestacii(u["id"])
        # A best-case BKT count is not a time-to-attestation prediction.
    db.ex("UPDATE sessions SET state='done',finished=?,data=?,ball=?,t_sec=?,v_norm=?,itog=? WHERE id=?",
          (time.time(), json.dumps(s, ensure_ascii=False, default=str), oc["ball"], t,
           1 if itog["v_normativ"] else 0, json.dumps(itog, ensure_ascii=False), sid))
    LIVE.pop(sid, None)
    return {**itog, "session_id": sid, "bkt": prof, "attestaciya": att}


@app.get("/api/session/{sid}/razbor")
def razbor(sid: str, u=Depends(current_user)):
    r = access.session(u,sid)
    if not r:
        raise HTTPException(404, "нет занятия")
    if u["role"] == "trainee" and r["user_id"] != u["id"]:
        raise HTTPException(403, "чужое занятие")
    if u["role"] == "trainee" and r["state"] != "done":
        raise HTTPException(409, "Разбор доступен после завершения")
    d = json.loads(r["data"])
    return {"session": {k: r[k] for k in ("id", "user_id", "scenario_id", "started", "finished",
                                          "ball", "t_sec", "override_ball", "override_reason")},
            "itog": insights.effective(r) if r["itog"] else None,
            "original_itog": json.loads(r["itog"]) if r["itog"] else None,
            "versions": d.get("versions"), "snapshot_origin": d.get("snapshot_origin", "original"),
            "events": [{**e,"payload":json.loads(e["payload"])} for e in db.q(
                "SELECT * FROM session_events WHERE session_id=? ORDER BY seq",(sid,))],
            "reviews": [{**e,"data":json.loads(e["data"])} for e in db.q(
                "SELECT * FROM evidence_reviews WHERE session_id=? ORDER BY revision",(sid,))],
            "ai_analysis":d.get("ai_analysis"), "helper":d.get("helper"), "parent_session":d.get("parent_session"),
            "snapshot_points":[r["seq"] for r in db.q("SELECT seq FROM session_snapshots WHERE session_id=? ORDER BY seq",(sid,))],
            "helper_proposals":[{**p,"data":json.loads(p["data"]),"decision":json.loads(p["decision"]) if p["decision"] else None,"reviewed":json.loads(p["reviewed"]) if p["reviewed"] else None} for p in db.q("SELECT * FROM helper_proposals WHERE session_id=? ORDER BY created",(sid,))],
            "calls": d.get("zvonki", []),
            "dialog": d.get("dialog", []), "kartochka": d.get("kartochka", {}),
            "bilet": d.get("bilet"),
            "prognozy": [{**x, "data": json.loads(x["data"]),
                          "fakt": json.loads(x["fakt"]) if x["fakt"] else None}
                         for x in db.q("SELECT * FROM forecasts WHERE subject=?", (sid,))]}


class Override(BaseModel):
    ball: int
    reason: str


class EvidenceReview(BaseModel):
    changes: dict[str, Any]
    reason: str


@app.post("/api/session/{sid}/review")
def review_evidence(sid: str, b: EvidenceReview, u=Depends(role("teacher"))):
    access.session(u,sid,completed=True)
    return insights.review(sid, b.changes, b.reason, u)


@app.get("/api/insights/{uid}")
def user_insights(uid: int, kind: str = "ops112", u=Depends(current_user)):
    uid = uid or u["id"]
    access.learner(u,uid)
    if u["role"] == "trainee" and uid != u["id"]:
        raise HTTPException(403, "Чужая аналитика")
    if kind not in Q.PROGRAMS:
        raise HTTPException(422, "Неизвестная программа")
    return insights.profile(uid, kind)


@app.post("/api/session/{sid}/override")
def override(sid: str, b: Override, u=Depends(role("teacher"))):
    """ТЗ: оценку меняет только преподаватель, с записью в журнал. Администратор — нет."""
    r = access.session(u,sid,completed=True)
    if not r:
        raise HTTPException(404, "занятие не найдено или не завершено")
    if not (0 <= b.ball <= 100) or len(b.reason.strip()) < 5:
        raise HTTPException(400, "балл 0–100 и причина не короче 5 символов")
    db.ex("UPDATE sessions SET override_ball=?,override_by=?,override_reason=? WHERE id=?",
          (b.ball, u["id"], b.reason.strip(), sid))
    db.audit(u, "grade_override", {"session": sid, "bylo": r["ball"], "stalo": b.ball,
                                   "prichina": b.reason})
    return {"ok": True}


@app.get("/api/live")
def live(u=Depends(role("teacher"))):
    items=analytics.monitor(u=u)["cards"]
    return [{**r,"user":r["name"],"rezhim":r["kind"],"t_sec":r["elapsed"],"narusheniy":0} for r in items]


# ================================================================ аналитика

def _done(user_id=None, group_id=None, kind=None, actor=None) -> list[dict]:
    sql = ("SELECT s.* FROM sessions s JOIN users u ON u.id=s.user_id "
           "WHERE s.state='done' AND u.role='trainee'")
    args: list = []
    if kind:
        sql += " AND s.kind=?"; args.append(kind)
    if user_id:
        sql += " AND s.user_id=?"; args.append(user_id)
    if group_id:
        sql += " AND u.group_id=?"; args.append(group_id)
    rows = db.q(sql + " ORDER BY s.finished", tuple(args))
    if actor and actor["role"]=="teacher":
        permitted=access.groups(actor)
        rows=[r for r in rows if db.q1("SELECT group_id FROM users WHERE id=?",(r["user_id"],))["group_id"] in permitted]
    for r in rows:
        r["itog"] = insights.effective(r)
        r["gruppa"] = json.loads(r["data"])["bilet"]["gruppa"]
        r.pop("data", None)
    return rows


def _navyki(rows) -> list[dict]:
    nav: dict[str, list] = {}
    for x in rows:
        for k, v in x["itog"]["navyki"].items():
            if v is not None:
                nav.setdefault(k, []).append(v)
    known = {s["kod"] for s in C.SKILLS}
    nav = {k: v for k, v in nav.items() if k in known}
    return sorted([{"kod": k, "nazvanie": next(s["nazvanie"] for s in C.SKILLS if s["kod"] == k),
                    "znachenie": round(sum(v) / len(v), 2), "n": len(v)} for k, v in nav.items()],
                  key=lambda x: x["znachenie"])


def _heat(rows) -> list[dict]:
    tr = C.load("tree")
    karta = {"adres": "adres", "tip": "tip", "sluzhby": "sluzhby", "normativ": "tayming",
             "rech": "rech", "polnota": "polnota", "podtverzhdenie": "podtverzhdenie",
             "proverka": "proverka", "peredacha": "peredacha", "po_faktu": "statusy"}
    cells: dict = {}
    for x in rows:
        for f in x["itog"]["fakty"]:
            k = karta.get(f["kod"])
            if not k or f.get("proyden") is None:
                continue
            c = cells.setdefault((x["gruppa"], k), {"v": 0, "p": 0, "s": []})
            c["v"] += 1
            if not f["proyden"]:
                c["p"] += 1
                c["s"].append(x["id"])
    return [{"gruppa": g, "gruppa_nazvanie": tr.get(g, {}).get("nazvanie", g), "skill": s,
             "dolya_provalov": round(c["p"] / c["v"], 2), "n": c["v"], "sessii": c["s"][:20]}
            for (g, s), c in cells.items()]


def _rekom(navyki, uid) -> list[dict]:
    if not navyki:
        return []
    pub = [_sc(r) for r in db.q("SELECT * FROM scenarios WHERE status='published'")]
    sdelano = {r["scenario_id"]: r["ball"] for r in
               db.q("SELECT scenario_id, MAX(ball) ball FROM sessions WHERE user_id=? "
                    "AND state='done' GROUP BY scenario_id", (uid or -1,))}
    out = []
    for nv in navyki[:2]:
        if nv["znachenie"] >= 0.8:
            continue
        if nv["kod"] == "adres":
            kand, pr = [b for b in pub if b["trebuet_utochneniya"]], "адрес нужно уточнять"
        elif nv["kod"] == "tayming":
            kand, pr = [b for b in pub if b["slozhnost"] >= 4], "высокая сложность"
        else:
            kand, pr = [b for b in pub if b["slozhnost"] >= 3], "отработка навыка"
        kand = [b for b in kand if sdelano.get(b["id"], 0) < 70][:3]
        out.append({"navyk": nv["nazvanie"], "znachenie": nv["znachenie"], "n": nv["n"],
                    "tekst": f"«{nv['nazvanie']}» — {int(nv['znachenie'] * 100)}% по {nv['n']} "
                             f"вызовам. Нерешённые на зачёт билеты, где {pr}:",
                    "bilety": [{"id": b["id"], "nazvanie": b["nazvanie"],
                                "situaciya": b["situaciya"][:90]} for b in kand]})
    return out


def _tochnost(kind, subjects=None) -> dict:
    rows = db.q("SELECT * FROM forecasts WHERE kind=? AND fakt IS NOT NULL ORDER BY created", (kind,))
    if subjects is not None:
        rows = [r for r in rows if r["subject"] in subjects]
    return {"vsego": len(rows),
            "tochnost": round(sum(r["verno"] for r in rows) / len(rows), 2) if rows else None,
            "spisok": [{"data": json.loads(r["data"]), "fakt": json.loads(r["fakt"]),
                        "verno": bool(r["verno"]), "created": r["created"]} for r in rows[-12:]]}


def _an_user(uid: int) -> dict:
    rows = _done(user_id=uid)
    nav = _navyki(rows)
    return {"user": db.q1("SELECT id,name,login,group_id FROM users WHERE id=?", (uid,)),
            "n": len(rows),
            "sredniy_ball": round(sum(r["itog"]["ball"] for r in rows) / len(rows)) if rows else None,
            "v_normativ": sum(1 for r in rows if r["v_norm"]),
            "navyki": nav, "heatmap": _heat(rows),
            "bkt": bkt.profil(uid), "attestaciya": bkt.prognoz_attestacii(uid),
            "dinamika": [{"i": i + 1, "ball": r["itog"]["ball"], "t_sec": r["t_sec"],
                          "id": r["id"], "scenario": r["scenario_id"]} for i, r in enumerate(rows)],
            "prognozy_vyzov": _tochnost("call", [r["id"] for r in rows]),
            "prognozy_attestaciya": _tochnost("attestation", [str(uid)]),
            "rekomendacii": _rekom(nav, uid)}


@app.get("/api/analytics/me")
def an_me(u=Depends(role("trainee"))):
    return _an_user(u["id"])


@app.get("/api/analytics/user/{uid}")
def an_user(uid: int, u=Depends(role("teacher"))):
    access.learner(u,uid)
    return _an_user(uid)


@app.get("/api/analytics/group/{gid}")
def an_group(gid: int, u=Depends(role("teacher", "admin"))):
    access.group(u,gid)
    rows = _done(group_id=gid,actor=u)
    members = db.q("SELECT id,name,login FROM users WHERE group_id=? AND role='trainee' ORDER BY name",
                   (gid,))
    table = []
    for m in members:
        mr = [r for r in rows if r["user_id"] == m["id"]]
        att = bkt.prognoz_attestacii(m["id"])
        nv = _navyki(mr)
        table.append({**m, "n": len(mr),
                      "ball": round(sum(r["itog"]["ball"] for r in mr) / len(mr)) if mr else None,
                      "v_norm": sum(1 for r in mr if r["v_norm"]),
                      "slabyy": nv[0]["nazvanie"] if nv else None,
                      "do_attestacii": att["vyzovov_do_attestacii"], "gotov": att["vse_osvoeny"]})
    nav = _navyki(rows)
    return {"group": db.q1("SELECT * FROM groups WHERE id=?", (gid,)),
            "n": len(rows), "uchastnikov": len(members),
            "sredniy_ball": round(sum(r["itog"]["ball"] for r in rows) / len(rows)) if rows else None,
            "v_normativ": sum(1 for r in rows if r["v_norm"]),
            "navyki": nav, "heatmap": _heat(rows), "obuchayushchiesya": table,
            "tipichnye_oshibki": [f"{x['nazvanie']}: {int(x['znachenie'] * 100)}% (n={x['n']})"
                                  for x in nav if x["znachenie"] < 0.8][:3],
            "prognozy_vyzov": _tochnost("call", [r["id"] for r in rows]),
            "dinamika": [{"i": i + 1, "ball": r["itog"]["ball"]} for i, r in enumerate(rows)]}


@app.get("/api/analytics/drill")
def drill(ids: str, u=Depends(role("teacher"))):
    out = []
    for sid in ids.split(",")[:30]:
        access.session(u,sid)
        r = db.q1("SELECT s.id,s.scenario_id,s.ball,s.t_sec,s.finished,u.name "
                  "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=?", (sid,))
        if r:
            out.append(r)
    return out


# ================================================================ отчёты

@app.get("/api/otchet/forma1")
def forma1(u=Depends(role("teacher", "admin"))):
    rows = _done(kind="ops112",actor=u)
    ts = [r["t_sec"] for r in rows] or [0]
    n = C.NORM
    S = lambda i, name, tr, f=None: {"n": i, "nazvanie": name, "trebovanie": tr, "fakt": f}
    return {"forma": "1/112", "istochnik": "Приказ МЧС России от 14.03.2022 № 192, приложение № 1",
            "subekt": "город Москва", "vyzovov": len(rows), "stroki": [
                S(1, "Максимальное время ожидания ответа, сек", n["otvet_max_sec"]),
                S(2, "Среднее время ожидания ответа, сек", n["otvet_sredniy_sec"]),
                S(3, "Среднее время опроса до доступности карточки, сек", n["kartochka_sec"],
                  round(sum(ts) / len(ts), 1)),
                S(4, "Максимальное время подтверждения ДДС, сек", n["podtverzhdenie_dds_sec"]),
                S(5, "Максимальное время до обратного вызова, сек", n["obratny_vyzov_sec"]),
                S(6, "Минимальное количество попыток обратного вызова", n["obratny_vyzov_popytok"]),
                S(7, "Ожидание ответа при обратном вызове, мин", 1),
                S(8, "Время консультативного обслуживания, мин", 2),
                S(9, "Максимальное время психологической поддержки, мин", 30),
                S(10, "Минимальный срок хранения информации, лет", 3)]}


@app.get("/api/otchet/forma2")
def forma2(u=Depends(role("teacher", "admin"))):
    rows = _done(kind="ops112",actor=u)
    sch: dict[str, int] = {}
    for x in rows:
        for f in x["itog"]["fakty"]:
            if f["kod"] == "sluzhby":
                for s in f["detali"].get("naznacheno", []):
                    sch[s] = sch.get(s, 0) + 1
    return {"forma": "2/112", "istochnik": "Приказ МЧС России от 14.03.2022 № 192, приложение № 2",
            "subekt": "город Москва", "vsego": len(rows),
            "po_napravleniyam": [{"nazvanie": r["nazvanie"], "kod": r["kod"],
                                  "kolichestvo": sch.get(r["kod"], 0)} for r in C.ROUTES]}


def _auth_q(token: str) -> dict:
    """Скачивание файлов по ссылке: токен в строке запроса."""
    uid = auth.read_token(token)
    if not uid:
        raise HTTPException(401, "требуется вход")
    user=db.q1("SELECT id,login,name,role,group_id FROM users WHERE id=? AND active=1", (uid,))
    if not user:raise HTTPException(401,"Учётная запись отключена")
    return user


@app.get("/api/otchet/zanyatie/{sid}.pdf")
def pdf_lesson(sid: str, token: str = ""):
    u = _auth_q(token)
    r = access.session(u,sid,completed=True)
    if not r:
        raise HTTPException(404, "нет завершённого занятия")
    if u["role"] == "trainee" and r["user_id"] != u["id"]:
        raise HTTPException(403, "чужое занятие")
    r["itog"], r["data"] = insights.effective(r), json.loads(r["data"])
    usr = db.q1("SELECT name FROM users WHERE id=?", (r["user_id"],))
    return Response(reports.otchet_zanyatiya(r, usr, r["data"].get("bilet", {})),
                    media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="otchet-{sid[:8]}.pdf"'})


@app.get("/api/otchet/sertifikat/{uid}.pdf")
def pdf_cert(uid: int, token: str = ""):
    u = _auth_q(token)
    access.learner(u,uid)
    rows = _done(user_id=uid)
    if not rows:
        raise HTTPException(400, "нет завершённых занятий")
    prof = bkt.profil(uid)
    stat = {"n": len(rows), "ball": round(sum(r["itog"]["ball"] for r in rows) / len(rows)),
            "v_norm": round(100 * sum(1 for r in rows if r["v_norm"]) / len(rows)),
            "osvoeno": sum(1 for v in prof.values() if v["osvoen"]), "navykov": len(prof)}
    return Response(reports.sertifikat(db.q1("SELECT name FROM users WHERE id=?", (uid,)), stat),
                    media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="sertifikat-{uid}.pdf"'})


@app.get("/api/otchet/export.xlsx")
def xlsx(token: str = "", group_id: int | None = None):
    u = _auth_q(token)
    if u["role"] not in ("teacher", "admin"):
        raise HTTPException(403, "недостаточно прав")
    if group_id:access.group(u,group_id)
    rows = _done(group_id=group_id,actor=u)
    us = {x["id"]: x for x in db.q("SELECT id,name FROM users")}
    return Response(reports.excel(rows, us),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="t112-zanyatiya.xlsx"'})


@app.get("/api/my/sessions")
def my_sessions(u=Depends(current_user)):
    return db.q("SELECT id,scenario_id,kind,started,ball,override_ball,override_reason,t_sec,v_norm "
                "FROM sessions WHERE user_id=? AND state='done' ORDER BY finished DESC", (u["id"],))


@app.get("/api/sessions")
def all_sessions(group_id: int | None = None, u=Depends(role("teacher"))):
    sql = ("SELECT s.id,s.scenario_id,s.kind,s.started,s.ball,s.override_ball,s.override_reason,"
           "s.t_sec,s.v_norm,u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.state='done'")
    args: tuple = ()
    if group_id:
        sql += " AND u.group_id=?"; args = (group_id,)
    rows=db.q(sql + " ORDER BY s.finished DESC LIMIT 300", args)
    permitted=access.groups(u)
    return [r for r in rows if db.q1("SELECT u.group_id FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.id=?",(r["id"],))["group_id"] in permitted]


# ================================================================ администрирование

@app.get("/api/admin/system")
async def admin_system(u=Depends(role("admin"))):
    h = await health()
    db.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    du = shutil.disk_usage(str(db.BACKUP_DIR))
    return {**h, "db_size": db.size_bytes(), "db_backend": db.backend(),
            "disk_free_gb": round(du.free / 1e9, 1),
            "users": db.q1("SELECT COUNT(*) n FROM users")["n"],
            "scenarios": db.q("SELECT status, COUNT(*) n FROM scenarios GROUP BY status"),
            "tayming": tayming(), "backups": len(db.list_backups())}


@app.get("/api/admin/audit")
def admin_audit(limit: int = 200, u=Depends(role("admin"))):
    return db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (min(limit, 1000),))


@app.get("/api/admin/backups")
def backups(u=Depends(role("admin"))):
    return db.list_backups()


@app.post("/api/admin/backup")
def backup(u=Depends(role("admin"))):
    f = db.backup_now()
    db.audit(u, "backup", {"file": f})
    return {"file": Path(f).name}


@app.get("/api/admin/backup/{name}")
def backup_get(name: str, token: str = ""):
    u = _auth_q(token)
    if u["role"] != "admin":
        raise HTTPException(403, "недостаточно прав")
    f = db.BACKUP_DIR / Path(name).name
    if not f.exists():
        raise HTTPException(404, "нет файла")
    return FileResponse(f, filename=f.name)


@app.post("/api/admin/restore/{name}")
def restore(name: str, u=Depends(role("admin"))):
    """Восстановить базу из резервной копии. Перед этим — свежая копия текущего состояния."""
    f = db.BACKUP_DIR / Path(name).name
    if not f.exists():
        raise HTTPException(404, "нет файла")
    safety = db.backup_now()
    n = db.restore(str(f))
    reload_live()
    db.audit(u, "restore", {"file": f.name, "strahovochnaya_kopiya": Path(safety).name})
    return {"ok": True, "vosstanovleno": n, "strahovka": Path(safety).name}


class Settings(BaseModel):
    uchebny_tayming_sec: int


@app.post("/api/admin/settings")
def settings(b: Settings, u=Depends(role("admin", "teacher"))):
    if not 10 <= b.uchebny_tayming_sec <= 600:
        raise HTTPException(400, "от 10 до 600 секунд")
    db.set_setting("uchebny_tayming_sec", b.uchebny_tayming_sec)
    db.audit(u, "settings", b.model_dump())
    return {"ok": True}


# ================================================================ голос

def _tts_session(sid: str, u: dict) -> dict | None:
    s = LIVE.get(sid) or dds.LIVE.get(sid)
    if not s:
        return None
    if s.get("user_id") != u["id"] and u["role"] not in ("teacher", "admin"):
        return None
    return s


def _caller_turn(s: dict, text: str) -> int:
    """Номер реплики заявителя: по записи разговора или по живому диалогу."""
    wanted = (text or "").strip()
    zapis = [z for z in (s.get("zapis") or []) if z.get("kto") in ("zayavitel", "caller")]
    if zapis:
        for i, z in enumerate(zapis, 1):
            if (z.get("tekst") or "").strip() == wanted:
                return i
        return 2
    live = [m for m in (s.get("dialog") or []) if m.get("kto") == "caller"]
    return max(1, len(live))


def _ticket_params(s: dict, text: str) -> dict:
    bil = s.get("bilet") or {}
    z = bil.get("zayavitel") or {}
    params = {
        "situaciya": bil.get("situaciya") or "",
        "fio": z.get("fio") or "",
        "gruppa": str(bil.get("gruppa") or ""),
        "turn": _caller_turn(s, text),
        "call_id": s.get("id") or "",
    }
    bid = str(bil.get("id") or "")
    voice_binds = db.setting("voice_binds", {}) or {}
    sound_binds = db.setting("sound_binds", {}) or {}
    if not isinstance(voice_binds, dict):
        voice_binds = {}
    if not isinstance(sound_binds, dict):
        sound_binds = {}
    pinned = voice_binds.get(bid)
    if pinned == "auto":
        pass
    elif isinstance(pinned, str) and pinned not in ("", "inherit"):
        params["voice"] = pinned
    else:
        chosen = db.setting("caller_voice", "auto") or "auto"
        if isinstance(chosen, str) and chosen not in ("", "auto"):
            params["voice"] = chosen
    sound = sound_binds.get(bid)
    if isinstance(sound, str) and sound not in ("", "auto"):
        params["sound"] = sound
    return params


class VoiceChoice(BaseModel):
    voice_id: str


class VoiceName(BaseModel):
    name: str = ""


class VoiceBind(BaseModel):
    id: str
    voice: str = ""
    sound: str = ""


class VoiceBinds(BaseModel):
    items: list[VoiceBind] = []


@app.get("/api/admin/voices")
async def admin_voices(u=Depends(role("admin"))):
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get(f"{VOICE_URL}/voices")
            r.raise_for_status()
            catalog = r.json()
    except Exception:
        raise HTTPException(503, "каталог голосов недоступен")
    selected = db.setting("caller_voice", "auto") or "auto"
    return {"voices": catalog.get("voices", []), "selected": selected}


@app.post("/api/admin/voices/select")
async def admin_voice_select(b: VoiceChoice, u=Depends(role("admin"))):
    vid = (b.voice_id or "auto").strip()
    if vid != "auto":
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", vid):
            raise HTTPException(400, "неизвестный голос")
        try:
            async with httpx.AsyncClient(timeout=10) as cl:
                r = await cl.get(f"{VOICE_URL}/voices")
                r.raise_for_status()
                ids = {v.get("id") for v in r.json().get("voices", [])}
        except Exception:
            raise HTTPException(503, "каталог голосов недоступен")
        if vid not in ids:
            raise HTTPException(400, "такого голоса нет")
    db.set_setting("caller_voice", vid)
    db.audit(u, "voice_select", {"voice": vid})
    return {"ok": True, "voice_id": vid}


@app.get("/api/admin/voices/{voice_id}/audio")
async def admin_voice_audio(voice_id: str, token: str = ""):
    u = _auth_q(token)
    if not u or u.get("role") != "admin":
        raise HTTPException(403, "недостаточно прав")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", voice_id or ""):
        raise HTTPException(400, "неизвестный голос")
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.get(f"{VOICE_URL}/voices/{voice_id}/audio")
            r.raise_for_status()
    except Exception:
        raise HTTPException(503, "эталон недоступен")
    return Response(r.content, media_type="audio/wav")


@app.post("/api/admin/voices")
async def admin_voice_upload(file: UploadFile = File(...), text: str = Form(""),
                             voice_name: str = Form(""), u=Depends(role("admin"))):
    raw = await file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(400, "файл больше 15 МБ")
    try:
        async with httpx.AsyncClient(timeout=40) as cl:
            r = await cl.post(f"{VOICE_URL}/voices", data={"text": text, "voice_name": voice_name},
                              files={"file": (file.filename or "voice.webm", raw,
                                              file.content_type or "application/octet-stream")})
    except Exception:
        raise HTTPException(503, "не удалось сохранить голос")
    if r.status_code >= 400:
        detail = "не удалось сохранить голос"
        try:
            detail = r.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(r.status_code, detail)
    saved = r.json()
    db.audit(u, "voice_upload", {"voice": saved.get("voice_id"), "name": saved.get("voice_name")})
    return saved


def _drop_voice_binds(vid: str) -> None:
    binds = db.setting("voice_binds", {}) or {}
    if not isinstance(binds, dict):
        return
    left = {k: v for k, v in binds.items() if v != vid}
    if left != binds:
        db.set_setting("voice_binds", left)


@app.patch("/api/admin/voices/{voice_id}")
async def admin_voice_rename(voice_id: str, b: VoiceName, u=Depends(role("admin"))):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", voice_id or ""):
        raise HTTPException(400, "неизвестный голос")
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.patch(f"{VOICE_URL}/voices/{voice_id}", json={"name": b.name})
    except Exception:
        raise HTTPException(503, "каталог голосов недоступен")
    if r.status_code >= 400:
        detail = "не удалось переименовать"
        try:
            detail = r.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(r.status_code, detail)
    saved = r.json()
    db.audit(u, "voice_rename", {"voice": voice_id, "name": saved.get("voice_name")})
    return saved


@app.delete("/api/admin/voices/{voice_id}")
async def admin_voice_delete(voice_id: str, u=Depends(role("admin"))):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", voice_id or ""):
        raise HTTPException(400, "неизвестный голос")
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.delete(f"{VOICE_URL}/voices/{voice_id}")
    except Exception:
        raise HTTPException(503, "каталог голосов недоступен")
    if r.status_code >= 400:
        detail = "не удалось удалить"
        try:
            detail = r.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(r.status_code, detail)
    if (db.setting("caller_voice", "auto") or "auto") == voice_id:
        db.set_setting("caller_voice", "auto")
    _drop_voice_binds(voice_id)
    db.audit(u, "voice_delete", {"voice": voice_id})
    return {"ok": True}


@app.get("/api/admin/voices/board")
async def admin_voice_board(u=Depends(role("admin"))):
    calls = []
    for b in C.load("bilety"):
        z = b.get("zayavitel") or {}
        calls.append({
            "id": b.get("id") or "",
            "bilet": b.get("bilet"),
            "vyzov": b.get("vyzov"),
            "situaciya": (b.get("situaciya") or "")[:180],
            "fio": z.get("fio") or "",
            "gruppa": str(b.get("gruppa") or ""),
        })
    auto: dict[str, str] = {}
    sounds: list = []
    try:
        async with httpx.AsyncClient(timeout=25) as cl:
            r = await cl.post(f"{VOICE_URL}/classify", json={"items": calls})
            if r.status_code == 200:
                for it in r.json().get("items", []):
                    auto[str(it.get("id") or "")] = it.get("sound") or ""
            s = await cl.get(f"{VOICE_URL}/sounds")
            if s.status_code == 200:
                sounds = s.json().get("sounds", [])
    except Exception:
        pass
    voice_binds = db.setting("voice_binds", {}) or {}
    sound_binds = db.setting("sound_binds", {}) or {}
    if not isinstance(voice_binds, dict):
        voice_binds = {}
    if not isinstance(sound_binds, dict):
        sound_binds = {}
    out = []
    for c in calls:
        out.append({
            "id": c["id"],
            "bilet": c["bilet"],
            "vyzov": c["vyzov"],
            "situaciya": c["situaciya"],
            "auto_sound": auto.get(c["id"], ""),
            "voice": voice_binds.get(c["id"], ""),
            "sound": sound_binds.get(c["id"], ""),
        })
    return {"sounds": sounds, "calls": out}


@app.post("/api/admin/voices/binds")
async def admin_voice_binds(b: VoiceBinds, u=Depends(role("admin"))):
    known = None
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get(f"{VOICE_URL}/voices")
            if r.status_code == 200:
                known = {v.get("id") for v in r.json().get("voices", [])}
    except Exception:
        known = None
    voice_binds: dict[str, str] = {}
    sound_binds: dict[str, str] = {}
    for it in b.items:
        if not re.fullmatch(r"b\d\d_v\d", it.id or ""):
            continue
        voice = (it.voice or "").strip()
        if voice == "auto":
            voice_binds[it.id] = "auto"
        elif voice and re.fullmatch(r"[A-Za-z0-9_]{1,64}", voice) and (known is None or voice in known):
            voice_binds[it.id] = voice
        sound = (it.sound or "").strip()
        if sound == "none":
            sound_binds[it.id] = "none"
        elif sound and sound != "auto" and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sound):
            sound_binds[it.id] = sound
    db.set_setting("voice_binds", voice_binds)
    db.set_setting("sound_binds", sound_binds)
    db.audit(u, "voice_binds", {"voices": len(voice_binds), "sounds": len(sound_binds)})
    return {"ok": True, "voices": len(voice_binds), "sounds": len(sound_binds)}


@app.post("/api/voice/asr")
async def v_asr(audio: UploadFile = File(...), u=Depends(current_user)):
    """
    Сначала GigaAM на GPU, при недоступности — Vosk на CPU.
    В ответе поле engine: какой движок реально распознал реплику.
    """
    raw = await audio.read()
    files = lambda: {"audio": (audio.filename or "a.webm", raw, audio.content_type or "audio/webm")}
    order = {"gigaam": [ASR_URL], "vosk": [VOICE_URL]}.get(ASR_ENGINE, [ASR_URL, VOICE_URL])
    last = None
    for base in order:
        try:
            async with httpx.AsyncClient(timeout=40) as cl:
                r = await cl.post(f"{base}/asr", files=files())
            if r.status_code == 400:                 # плохое аудио — второй движок не поможет
                raise HTTPException(400, r.json().get("detail", "плохое аудио"))
            r.raise_for_status()
            j = r.json()
            j.setdefault("engine", "gigaam" if base == ASR_URL else "vosk")
            return j
        except HTTPException:
            raise
        except Exception as e:
            last = e
    raise HTTPException(503, "распознавание речи недоступно")


@app.get("/api/voice/tts")
async def v_tts(text: str, token: str = "", sid: str = "", voice: str = "", plain: int = 0):
    u = _auth_q(token)
    if not u:
        raise HTTPException(401, "требуется вход")
    params = {"text": text}
    if plain or voice:
        if u.get("role") != "admin":
            raise HTTPException(403, "недостаточно прав")
        params["plain"] = 1
        if voice:
            params["voice"] = voice
    elif sid:
        s = _tts_session(sid, u)
        if s and s.get("bilet"):
            params.update(_ticket_params(s, text))
    try:
        async with httpx.AsyncClient(timeout=40) as cl:
            r = await cl.get(f"{VOICE_URL}/tts", params=params)
            r.raise_for_status()
            return Response(r.content, media_type="audio/wav")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "голосовой сервис недоступен")


# ================================================================ фронт

WEB = Path(__file__).parent.parent / "web"


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB / "index.html").read_text(encoding="utf-8")


@app.get("/static/{name}")
def static(name: str):
    f = WEB / Path(name).name
    if f.suffix not in (".js", ".css") or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="application/javascript" if f.suffix == ".js" else "text/css")


@app.get("/readyz")
def readyz():
    db.q1("SELECT 1 AS n")
    return {"ok":True,"release":"2026.09.28-plan","rubric":Q.RUBRIC_VERSION,"database":db.backend()}


class ScopeGrant(BaseModel):
    teacher_id:int
    group_id:int
    allowed:bool=True

@app.get("/api/admin/scopes")
def get_scopes(u=Depends(role("admin"))):
    return db.q("SELECT * FROM teacher_groups")

@app.post("/api/admin/scopes")
def set_scope(b:ScopeGrant,u=Depends(role("admin"))):
    if not db.q1("SELECT id FROM users WHERE id=? AND role='teacher'",(b.teacher_id,)) or not db.q1("SELECT id FROM groups WHERE id=?",(b.group_id,)):
        raise HTTPException(422,"Выберите существующих преподавателя и группу")
    if b.allowed:db.ex("INSERT INTO teacher_groups(teacher_id,group_id) VALUES(?,?) ON CONFLICT DO NOTHING",(b.teacher_id,b.group_id))
    else:db.ex("DELETE FROM teacher_groups WHERE teacher_id=? AND group_id=?",(b.teacher_id,b.group_id))
    db.audit(u,"group_scope",b.model_dump())
    return {"ok":True}
