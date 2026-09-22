"""
Байесовское отслеживание знаний (Corbett & Anderson, 1994).

Для каждого обучающегося и каждого навыка хранится вероятность
освоения p. После каждого вызова она обновляется по результату
соответствующего факта графа доказательств.

Четыре параметра, все интерпретируемы:
  P_INIT   — вероятность, что навык уже освоен до обучения
  P_LEARN  — вероятность освоить навык за одну попытку
  P_GUESS  — вероятность выполнить верно, не владея навыком
  P_SLIP   — вероятность ошибиться, владея навыком

Навык считается освоенным при p >= MASTERY (0,95 — стандартный порог).
"""
from __future__ import annotations

import math
import time

from . import db

P_INIT, P_LEARN, P_GUESS, P_SLIP = 0.10, 0.10, 0.25, 0.10
MASTERY = 0.95

# факт графа доказательств → навык
FAKT_NAVYK = {
    "adres": "adres", "tip": "tip", "sluzhby": "sluzhby",
    "normativ": "tayming", "polnota": "polnota", "rech": "rech",
    # режим ДДС
    "podtverzhdenie": "podtverzhdenie", "proverka": "proverka",
    "peredacha": "peredacha", "po_faktu": "statusy",
}


def obnovit(p: float, verno: bool) -> float:
    """Один шаг BKT: апостериорная вероятность, затем переход обучения."""
    if verno:
        post = p * (1 - P_SLIP) / (p * (1 - P_SLIP) + (1 - p) * P_GUESS)
    else:
        post = p * P_SLIP / (p * P_SLIP + (1 - p) * (1 - P_GUESS))
    return post + (1 - post) * P_LEARN


def do_osvoeniya(p: float) -> int:
    """
    Сколько ещё успешных попыток нужно до порога освоения.
    Прямое следствие модели: считаем шаги обновления при верных ответах.
    """
    n = 0
    while p < MASTERY and n < 60:
        p = obnovit(p, True)
        n += 1
    return n


def profil(uid: int) -> dict[str, dict]:
    rows = db.q("SELECT skill,p,n FROM bkt WHERE user_id=?", (uid,))
    have = {r["skill"]: r for r in rows}
    out = {}
    for sk in set(FAKT_NAVYK.values()):
        r = have.get(sk)
        p = r["p"] if r else P_INIT
        out[sk] = {"p": round(p, 3), "n": r["n"] if r else 0,
                   "osvoen": p >= MASTERY, "ostalos": do_osvoeniya(p)}
    return out


def primenit(uid: int, fakty: list[dict]) -> dict[str, dict]:
    """Обновить профиль по фактам одного вызова."""
    prof = profil(uid)
    for f in fakty:
        sk = FAKT_NAVYK.get(f["kod"])
        if not sk:
            continue
        p0 = prof[sk]["p"]
        p1 = obnovit(p0, bool(f["proyden"]))
        n1 = prof[sk]["n"] + 1
        db.ex("INSERT INTO bkt(user_id,skill,p,n,updated) VALUES(?,?,?,?,?) "
              "ON CONFLICT(user_id,skill) DO UPDATE SET p=excluded.p,n=excluded.n,"
              "updated=excluded.updated", (uid, sk, p1, n1, time.time()))
    return profil(uid)


def prognoz_attestacii(uid: int) -> dict:
    """
    Прогноз: сколько вызовов до освоения всех навыков.
    Лимитирует самый слабый навык.
    """
    prof = profil(uid)
    slabyy = min(prof.items(), key=lambda kv: kv[1]["p"])
    vyzovov = max(v["ostalos"] for v in prof.values())
    return {
        "vyzovov_do_attestacii": vyzovov,
        "limitiruyushchiy_navyk": slabyy[0],
        "p_limit": slabyy[1]["p"],
        "vse_osvoeny": all(v["osvoen"] for v in prof.values()),
        "parametry": {"P_INIT": P_INIT, "P_LEARN": P_LEARN,
                      "P_GUESS": P_GUESS, "P_SLIP": P_SLIP, "MASTERY": MASTERY},
        "model": "Bayesian Knowledge Tracing (Corbett & Anderson, 1994)",
    }
