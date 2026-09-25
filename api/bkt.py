"""BKT baseline; mastery is model state, not certified readiness."""
import time
from . import db
from .quality import PROGRAMS, FAKT_SKILL

P_INIT, P_LEARN, P_GUESS, P_SLIP = .1, .1, .25, .1
MASTERY = .95
FAKT_NAVYK = FAKT_SKILL

def obnovit(p, verno, params=None):
    p = max(0., min(1., float(p)))
    learn, guess, slip = (params or (P_LEARN, P_GUESS, P_SLIP))
    num = p * ((1-slip) if verno else slip)
    den = num + (1-p) * (guess if verno else (1-guess))
    post = num / den if den else p
    return post + (1-post) * learn

def do_osvoeniya(p):
    n = 0
    while p < MASTERY and n < 60:
        p = obnovit(p, True)
        n += 1
    return n

def profil(uid, kind="ops112"):
    if kind not in PROGRAMS:
        raise ValueError("Unknown programme")
    have = {r["skill"]: r for r in db.q("SELECT skill,p,n FROM bkt WHERE user_id=?", (uid,))}
    out = {}
    for skill in PROGRAMS[kind]:
        r = have.get(kind + ":" + skill)
        p, n = (float(r["p"]), int(r["n"])) if r else (P_INIT, 0)
        out[skill] = {"p": round(p, 6), "n": n, "osvoen": n > 0 and p >= MASTERY,
                      "status": "not_observed" if not n else ("mastered" if p >= MASTERY else "practice"),
                      "ostalos": do_osvoeniya(p) if n else None}
    return out

def primenit(uid, fakty, kind="ops112"):
    observations = {}
    for f in fakty:
        skill = FAKT_SKILL.get(f["kod"])
        if skill in PROGRAMS[kind] and f.get("proyden") is not None:
            observations.setdefault(skill, []).append(bool(f["proyden"]))
    have = {r["skill"]: r for r in db.q("SELECT skill,p,n FROM bkt WHERE user_id=?", (uid,))}
    for skill, values in observations.items():
        key = kind + ":" + skill
        old = have.get(key, {"p": P_INIT, "n": 0})
        p = obnovit(old["p"], all(values))
        db.ex("INSERT INTO bkt(user_id,skill,p,n,updated) VALUES(?,?,?,?,?) "
              "ON CONFLICT(user_id,skill) DO UPDATE SET p=excluded.p,n=excluded.n,updated=excluded.updated",
              (uid, key, p, old["n"]+1, time.time()))
    return profil(uid, kind)

def prognoz_attestacii(uid, kind="ops112"):
    profile = profil(uid, kind)
    unknown = [k for k,v in profile.items() if not v["n"]]
    weak = min(profile, key=lambda k: profile[k]["p"])
    return {"kind": kind, "vyzovov_do_attestacii": None if unknown else max(v["ostalos"] for v in profile.values()),
            "limitiruyushchiy_navyk": weak, "p_limit": profile[weak]["p"],
            "vse_osvoeny": all(v["osvoen"] for v in profile.values()), "ne_provereny": unknown,
            "forecast_type": "conditional_best_case", "certification": False,
            "explanation": "Условное число успешных повторений каждого навыка; не число вызовов и не допуск к работе",
            "parametry": {"P_INIT":P_INIT,"P_LEARN":P_LEARN,"P_GUESS":P_GUESS,"P_SLIP":P_SLIP,"MASTERY":MASTERY},
            "model": "BKT baseline v2, parameters not fitted"}
