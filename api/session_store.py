"""Versioned content, ordered events and transactional completion for one API worker.

The existing application uses LIVE dictionaries. HTTP command serialization is
installed in main.py; multiworker deployment is deliberately not supported yet.
"""
import copy
import functools
import hashlib
import inspect
import json
import time
import typing
import uuid
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
    rules = {"norm":C.NORM,"dds":C.DDS,"speech":C.RECH,"grammar":D._GRAM,"rubric":Q.RUBRIC_VERSION}
    s["versions"] = {
        "scenario": content("scenario",s["bilet"]),
        "classifier": content("classifier",C.load("index")),
        "rules": content("rules",rules),
        "rubric": Q.RUBRIC_VERSION,
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

def event(s, typ, payload):
    row = db.q1("SELECT COALESCE(MAX(seq),0) n FROM session_events WHERE session_id=?",(s["id"],))
    eid = uuid.uuid4().hex
    db.ex("INSERT INTO session_events(session_id,seq,event_id,actor,ts,type,payload) VALUES(?,?,?,?,?,?,?)",
          (s["id"],row["n"]+1,eid,s["user_id"],time.time(),typ,dumps(payload)))
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
    for key in ("dialog","statusy","zamechaniya"):
        for item in s.get(key,[])[len(previous.get(key,[])):]:
            event(s,key,item)
    if s.get("zvonki") != previous.get("zvonki"):
        event(s,"calls_changed",{"calls":s.get("zvonki",[])})
    if s.get("sobytiya") != previous.get("sobytiya"):
        event(s,"incoming_changed",{"incoming":s.get("sobytiya",[])})
    if not previous.get("versions"):
        event(s,"session_started",{"versions":s["versions"],"kind":s.get("kind","ops112")})
    db.ex("UPDATE sessions SET data=? WHERE id=?",(dumps(s),s["id"]))

def command(live, kind="ops112", complete=False):
    """Wrap sync commands only; completion receipt and BKT commit atomically."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            bound=inspect.signature(fn).bind(*args,**kwargs)
            uid=bound.arguments.get("u",{}).get("id")
            sid=bound.arguments.get("sid")
            before=copy.deepcopy(live)
            try:
                with db.tx():
                    if complete:
                        receipt=db.q1("SELECT * FROM completion_receipts WHERE session_id=?",(sid,))
                        if receipt:
                            if receipt["user_id"]!=uid or receipt["kind"]!=kind:
                                from fastapi import HTTPException
                                raise HTTPException(403,"Чужое занятие или другой режим")
                            return json.loads(receipt["response"])
                        old=db.q1("SELECT * FROM sessions WHERE id=? AND state='done'",(sid,))
                        if old:
                            from fastapi import HTTPException
                            if old["user_id"]!=uid or old["kind"]!=kind:
                                raise HTTPException(403,"Чужое занятие")
                            # Do not recalculate pre-upgrade results or update BKT.
                            return {**json.loads(old["itog"]), "session_id":sid, "legacy_receipt":True}
                    answer=fn(*args,**kwargs)
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
                    return answer
            except Exception:
                live.clear();live.update(before)
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
