"""Explainable analytics: programmes, rubric versions and missing evidence stay separate."""
import copy
import json
import math
import time
from . import db, bkt, quality as Q, config as C, domain as D

LABELS={x["kod"]:x["nazvanie"] for x in C.SKILLS}
LABELS.update(podtverzhdenie="Подтверждение приёма",proverka="Проверка карточки",
              peredacha="Передача бригаде",statusy="Статусы по фактам")

def effective(row):
    original=json.loads(row["itog"]) if isinstance(row["itog"],str) else row["itog"]
    review=db.q1("SELECT * FROM evidence_reviews WHERE session_id=? ORDER BY revision DESC LIMIT 1",(row["id"],))
    return json.loads(review["data"]) if review else original

def attempts(uid,kind):
    rows=db.q("SELECT * FROM sessions WHERE user_id=? AND kind=? AND state='done' ORDER BY finished,id",(uid,kind))
    for row in rows:
        row["itog"]=effective(row)
    return rows

def interval(success,n):
    if not n: return None
    z=1.96; p=success/n; denominator=1+z*z/n
    center=(p+z*z/(2*n))/denominator
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denominator
    return [round(max(0,center-radius),3),round(min(1,center+radius),3)]

def forecast_metrics(session_ids):
    # One vote per attempt, the first saved forecast BEFORE the deadline.
    selected={}
    for row in db.q("SELECT * FROM forecasts WHERE kind='call' AND fakt IS NOT NULL ORDER BY created,id"):
        if row["subject"] not in session_ids or row["subject"] in selected: continue
        p=json.loads(row["data"]); y=json.loads(row["fakt"])
        if p.get("t_prognoza_sec",0)>=p.get("proverka_na_sec",75): continue
        selected[row["subject"]]=(float(p["veroyatnost"]),int(bool(y["uspel"])),p.get("method","legacy"))
    pairs=list(selected.values()); n=len(pairs)
    buckets=[]
    for i in range(5):
        xs=[(p,y) for p,y,_ in pairs if min(4,int(p*5))==i]
        if xs:
            buckets.append({"bin":i,"n":len(xs),"predicted":sum(p for p,y in xs)/len(xs),
                            "observed":sum(y for p,y in xs)/len(xs)})
    return {"n":n,"unit":"attempt","policy":"first_predeadline",
            "brier":round(sum((p-y)**2 for p,y,_ in pairs)/n,4) if n else None,
            "accuracy":round(sum((p>=.5)==bool(y) for p,y,_ in pairs)/n,3) if n else None,
            "calibration":buckets,"methods":sorted({m for _,_,m in pairs}),
            "warning":"Описательная статистика, не независимая проверка ML; при малой выборке выводы ненадёжны"}

def profile(uid,kind):
    if kind not in Q.PROGRAMS: raise ValueError("Unknown programme")
    all_rows=attempts(uid,kind)
    from . import session_store as store
    rows=[r for r in all_rows if r["itog"].get("rubric_version")==Q.RUBRIC_VERSION and store.independent(json.loads(r["data"]))]
    skills=[]
    for skill in Q.PROGRAMS[kind]:
        obs=[]
        for row in rows:
            values=[f["proyden"] for f in row["itog"]["fakty"]
                    if Q.FAKT_SKILL.get(f["kod"])==skill and f.get("proyden") is not None]
            if values: obs.append((row,all(values)))
        n=len(obs); good=sum(v for r,v in obs)
        skills.append({"code":skill,"name":LABELS.get(skill,skill),"n":n,
            "rate":round(good/n,3) if n else None,"interval95":interval(good,n),
            "recent_rate":round(sum(v for r,v in obs[-10:])/min(n,10),3) if n else None,
            "status":"not_observed" if not n else "practice" if good/n<.8 else "observed_success",
            "failed_attempts":[r["id"] for r,v in obs if not v][-5:]})
    weak=sorted(skills,key=lambda s:(s["n"]>0,s["rate"] if s["rate"] is not None else -1))[:3]
    scenarios=[json.loads(r["data"]) for r in db.q("SELECT data FROM scenarios WHERE status='published' ORDER BY id")]
    plan=[]
    for skill in weak:
        if skill["n"] and skill["rate"]>=.8: continue
        pool=scenarios
        if skill["code"]=="adres": pool=[s for s in pool if s.get("trebuet_utochneniya")]
        # Low difficulty first; repeated correct practice precedes time pressure.
        pool=sorted(pool,key=lambda s:(s.get("slozhnost",1),s["id"]))
        plan.append({"skill":skill["code"],"name":skill["name"],
            "reason":"Навык ещё не проверен" if not skill["n"] else f"Ошибки в {skill['n']-round(skill['rate']*skill['n'])} из {skill['n']} попыток",
            "action":"Диагностическая попытка" if not skill["n"] else "Целевая тренировка и повторный разбор",
            "criterion":"Проверить навык в трёх разных сценариях; это учебная цель, не автоматический допуск",
            "selection":"Правила по навыку и сложности; покрытие подтвердить преподавателю",
            "scenarios":[{"id":s["id"],"name":s.get("nazvanie",s["id"])} for s in pool[:3]]})
    from . import curriculum
    plan=curriculum.recommendations(uid,kind,skills)
    return {"kind":kind,"rubric_version":Q.RUBRIC_VERSION,"n":len(rows),
            "legacy_excluded":len(all_rows)-len(rows),"passed":sum(r["itog"].get("passed") is True for r in rows),
            "skills":skills,"plan":plan,"bkt":bkt.profil(uid,kind),
            "bkt_note":"Параметры BKT не обучены на ваших данных. Не использовать для допуска к работе.",
            "forecasts":forecast_metrics({r["id"] for r in rows}),
            "attempts":[{"id":r["id"],"finished":r["finished"],"ball":r["itog"]["ball"],
                          "passed":r["itog"].get("passed"),"manual_ball":r["override_ball"],
                          "scenario":r["scenario_id"]} for r in rows]}

def review(sid,changes,reason,actor):
    """Append-only review. Original result/receipt remains immutable; recompute this programme's BKT."""
    from fastapi import HTTPException
    from . import session_store as store
    with db.tx():
        row=db.q1("SELECT * FROM sessions WHERE id=? AND state='done'",(sid,))
        if not row: raise HTTPException(404,"Завершённая попытка не найдена")
        prior=effective(row)
        if prior.get("rubric_version")!=Q.RUBRIC_VERSION:
            raise HTTPException(409,"Архивная рубрика: пересмотр критериев новой рубрикой запрещён")
        valid={f["kod"] for f in prior["fakty"]}
        if (not changes or set(changes)-valid or len(reason.strip())<5 or
            any(v is not None and type(v) is not bool for v in changes.values())):
            raise HTTPException(422,"Нужны известные критерии, boolean/null и причина от 5 символов")
        facts=[]
        for f in copy.deepcopy(prior["fakty"]):
            if f["kod"] in changes: f["proyden"]=changes[f["kod"]]
            facts.append(D.Fakt(**{k:f[k] for k in ("kod","nazvanie","proyden","ves","istochnik","detali")},t_ms=f.get("t_ms")))
        result={**prior,**Q.result(facts,row["kind"],critical=prior.get("critical_codes"),threshold=prior.get("pass_threshold",70))}
        evidence={f["kod"]:f for f in prior["fakty"]}
        for f in result["fakty"]:
            for key in ("evidence_event_ids","evaluated_at_event_id"):
                if key in evidence[f["kod"]]: f[key]=evidence[f["kod"]][key]
        rev=db.q1("SELECT COALESCE(MAX(revision),0) n FROM evidence_reviews WHERE session_id=?",(sid,))["n"]+1
        result["review"]={"revision":rev,"reason":reason.strip(),"actor":actor["id"],"ts":time.time()}
        db.ex("INSERT INTO evidence_reviews(session_id,revision,actor,reason,data,created) VALUES(?,?,?,?,?,?)",
              (sid,rev,actor["id"],reason.strip(),store.dumps(result),time.time()))
        # No reinterpretation of old mixed-programme BKT rows or old rubrics.
        for skill in Q.PROGRAMS[row["kind"]]:
            db.ex("DELETE FROM bkt WHERE user_id=? AND skill=?",(row["user_id"],bkt.skill_key(row["kind"],skill)))
        for past in attempts(row["user_id"],row["kind"]):
            if past["itog"].get("rubric_version")==Q.RUBRIC_VERSION and store.independent(json.loads(past["data"])):
                bkt.primenit(row["user_id"],past["itog"]["fakty"],row["kind"])
        db.audit(actor,"evidence_review",{"session":sid,"revision":rev,"changes":changes,"reason":reason})
        return result
