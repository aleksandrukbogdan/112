"""Durable state, short atomic commands, snapshots and idempotent completion.

LIVE is a compatibility cache. PostgreSQL row locks and SQLite write transactions
serialize mutations; AI requests execute outside transactions.
"""
import copy
import functools
import hashlib
import inspect
import json
import time
import typing
import uuid
import contextvars
from pathlib import Path
from fastapi import HTTPException

request_command = contextvars.ContextVar("request_command", default=None)
from . import db, config as C, quality as Q

def dumps(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

def content(kind, data):
    raw = dumps(data)
    key = hashlib.sha256(raw.encode()).hexdigest()
    db.ex("INSERT INTO content_versions(hash,kind,data,created) VALUES(?,?,?,?) ON CONFLICT(hash) DO NOTHING",
          (key,kind,raw,time.time()))
    return key

def freeze(s):
    if s.get("versions"):
        return
    from . import domain as D
    rules = copy.deepcopy({"norm":C.NORM,"dds":C.DDS,"speech":C.RECH,"grammar":D._GRAM,"rubric":Q.RUBRIC_VERSION})
    from . import curriculum
    rules = curriculum.rules_for(s, rules)
    s.setdefault("revision", 0)
    s.setdefault("field_versions", {})
    s.setdefault("mode", "practice")
    s.setdefault("helper", {"allowed": s["mode"] == "practice", "enabled": False, "exposed": False})
    code = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
            for name in ("quality.py", "domain.py", "dds_rules.py", "facts.py")}
    rules["evaluator"] = code
    s["versions"] = {
        "scenario": content("scenario",s["bilet"]),
        "classifier": content("classifier",C.load("index")),
        "rules": content("rules",rules),
        "rubric": Q.RUBRIC_VERSION,
        "evaluator": content("evaluator", code),
    }
    s["_rules"] = copy.deepcopy(rules)

def index(s):
    key = s.get("versions",{}).get("classifier")
    if key:
        row = db.q1("SELECT data FROM content_versions WHERE hash=?",(key,))
        if not row:
            raise ValueError("Classifier snapshot missing: " + key)
        return json.loads(row["data"])
    return C.load("index")

def event(s, typ, payload, actor=None):
    row = db.q1("SELECT COALESCE(MAX(seq),0) n FROM session_events WHERE session_id=?",(s["id"],))
    eid = uuid.uuid4().hex
    db.ex("INSERT INTO session_events(session_id,seq,event_id,actor,ts,type,payload) VALUES(?,?,?,?,?,?,?)",
          (s["id"],row["n"]+1,eid,actor if actor is not None else s["user_id"],time.time(),typ,dumps(payload)))
    return eid

def persist(s):
    row = db.q1("SELECT data FROM sessions WHERE id=?",(s["id"],))
    previous = json.loads(row["data"]) if row else {}
    freeze(s)
    old_card = previous.get("kartochka",{})
    for key,value in s.get("kartochka",{}).items():
        if not key.startswith("_") and value != old_card.get(key):
            event(s,"field_changed",{"field":key,"before":old_card.get(key),"after":value})
            s["_field_edits"] = s.get("_field_edits", 0) + 1
            s.setdefault("field_versions", {})[key] = previous.get("field_versions", {}).get(key, 0) + 1
    for key in ("dialog","statusy","zamechaniya"):
        for item in s.get(key,[])[len(previous.get(key,[])):]:
            event(s,key,item)
    if s.get("zvonki") != previous.get("zvonki"):
        event(s,"calls_changed",{"calls":s.get("zvonki",[])})
    if s.get("sobytiya") != previous.get("sobytiya"):
        event(s,"incoming_changed",{"incoming":s.get("sobytiya",[])})
    if not previous.get("versions"):
        event(s,"session_started",{"versions":s["versions"],"kind":s.get("kind","ops112")})
    s["revision"] = previous.get("revision", 0) + 1
    db.ex("UPDATE sessions SET data=? WHERE id=?",(dumps(s),s["id"]))
    snapshot(s)

def command(live, kind="ops112", complete=False):
    """Wrap sync commands only; completion receipt and BKT commit atomically."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            bound=inspect.signature(fn).bind(*args,**kwargs)
            uid=bound.arguments.get("u",{}).get("id")
            sid=bound.arguments.get("sid")
            before = None
            new_ids = []
            try:
                with db.tx():
                    if uid:
                        db.lock_row("users", "id", uid)
                    if sid:
                        row = db.lock_row("sessions", "id", sid)
                        if row and row["state"] == "live":
                            live[sid] = json.loads(row["data"])
                        before = copy.deepcopy(live.get(sid))
                    existing = set(live)
                    cached = receipt(uid)
                    if cached is not None:
                        return cached
                    if complete:
                        completion=db.q1("SELECT * FROM completion_receipts WHERE session_id=?",(sid,))
                        if completion:
                            if completion["user_id"]!=uid or completion["kind"]!=kind:
                                from fastapi import HTTPException
                                raise HTTPException(403,"Чужое занятие или другой режим")
                            return json.loads(completion["response"])
                        old=db.q1("SELECT * FROM sessions WHERE id=? AND state='done'",(sid,))
                        if old:
                            from fastapi import HTTPException
                            if old["user_id"]!=uid or old["kind"]!=kind:
                                raise HTTPException(403,"Чужое занятие")
                            # Do not recalculate pre-upgrade results or update BKT.
                            return {**json.loads(old["itog"]), "session_id":sid, "legacy_receipt":True}
                    answer=fn(*args,**kwargs)
                    new_ids = [key for key in live if key not in existing and live[key].get("user_id") == uid]
                    if complete:
                        row=db.q1("SELECT data FROM sessions WHERE id=?",(sid,))
                        s=json.loads(row["data"])
                        freeze(s)
                        event_id=event(s,"session_completed",{"ball":answer["ball"],"passed":answer.get("passed"),
                                               "versions":s["versions"]})
                        answer["versions"]=s["versions"]
                        observations=db.q("SELECT event_id,type,payload FROM session_events WHERE session_id=? ORDER BY seq",(sid,))
                        fields={"adres":{"adres_polny"},"tip":{"tip_kod"},
                                "sluzhby":{"tip_kod","sluzhby","postradavshie","pravonarushenie"}}
                        for fact in answer.get("fakty",[]):
                            code=fact["kod"]
                            ids=[]
                            for e in observations:
                                payload=json.loads(e["payload"])
                                if (e["type"]=="field_changed" and (code=="polnota" or payload.get("field") in fields.get(code,set()))
                                    or e["type"]=="dialog" and code=="rech"
                                    or e["type"]=="zamechaniya" and code in ("proverka","lozhnye")
                                    or e["type"]=="statusy" and code in ("podtverzhdenie","po_faktu","svoevremenno","zaversheno","kommentarii")
                                    or e["type"]=="calls_changed" and code in ("peredacha","rech")):
                                    ids.append(e["event_id"])
                            fact["evidence_event_ids"]=ids
                            fact["evaluated_at_event_id"]=event_id
                        # Store the same rubric result the client receives.
                        db.ex("UPDATE sessions SET data=?,itog=? WHERE id=?",(dumps(s),dumps(answer),sid))
                        db.ex("INSERT INTO completion_receipts(session_id,user_id,kind,response,created) VALUES(?,?,?,?,?)",
                              (sid,uid,kind,dumps(answer),time.time()))
                    save_receipt(uid, answer)
                    if not complete:
                        target = live.get(sid) if sid else live.get(answer.get("session_id")) if isinstance(answer, dict) else None
                        if target:
                            snapshot(target)
                    return answer
            except Exception:
                if sid:
                    if before is None:
                        live.pop(sid, None)
                    else:
                        live[sid] = before
                for key in list(live):
                    if live[key].get("user_id") == uid and not db.q1("SELECT id FROM sessions WHERE id=?", (key,)):
                        live.pop(key, None)
                raise
        # FastAPI resolves forward annotations in wrapper globals otherwise.
        hints=typing.get_type_hints(fn)
        sig=inspect.signature(fn)
        wrapped.__signature__=sig.replace(parameters=[p.replace(annotation=hints.get(p.name,p.annotation))
            for p in sig.parameters.values()],return_annotation=hints.get("return",sig.return_annotation))
        return wrapped
    return decorate

def assignment(assignment_id, user, scenario_id, kind):
    if not assignment_id:
        return None
    from fastapi import HTTPException
    row=db.q1("SELECT * FROM assignments WHERE id=?",(assignment_id,))
    if (not row or row["scenario_id"]!=scenario_id or row.get("rezhim","ops112")!=kind or
        (user["role"]=="trainee" and row["group_id"]!=user.get("group_id"))):
        raise HTTPException(403,"Назначение не соответствует группе, сценарию или режиму")
    return row


def receipt(uid):
    ctx = request_command.get()
    if not ctx or not uid:
        return None
    row = db.q1("SELECT * FROM command_receipts WHERE user_id=? AND key=?", (uid, ctx["key"]))
    if not row:
        return None
    if row["path"] != ctx["path"] or row["body_hash"] != ctx["body_hash"]:
        raise HTTPException(409, "Ключ команды уже использован с другим содержимым")
    return json.loads(row["response"]) if row["response"] is not None else None


def save_receipt(uid, answer):
    ctx = request_command.get()
    if ctx and uid:
        db.ex("INSERT INTO command_receipts(user_id,key,path,body_hash,response,status,created) VALUES(?,?,?,?,?,?,?) "
              "ON CONFLICT(user_id,key) DO NOTHING", (uid,ctx["key"],ctx["path"],ctx["body_hash"],dumps(answer),200,time.time()))


def snapshot(s):
    row = db.q1("SELECT COALESCE(MAX(seq),0) n FROM session_events WHERE session_id=?", (s["id"],))
    db.ex("INSERT INTO session_snapshots(session_id,seq,ts,data) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",
          (s["id"],row["n"],time.time(),dumps(s)))


def load(sid, user=None, kind=None):
    row = db.q1("SELECT * FROM sessions WHERE id=?", (sid,))
    if not row:
        raise HTTPException(404, "Попытка не найдена")
    if user and row["user_id"] != user["id"]:
        raise HTTPException(403, "Чужая попытка")
    if kind and row["kind"] != kind:
        raise HTTPException(409, "Другой режим попытки")
    if row["state"] != "live":
        raise HTTPException(409, "Попытка уже завершена")
    return json.loads(row["data"])


def independent(s):
    return not (s.get("helper", {}).get("exposed") or s.get("parent_session") or s.get("interventions"))
