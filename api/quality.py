"""Conservative checks. Unknown address structure requires review, not fuzzy acceptance."""
import re
import unicodedata

RUBRIC_VERSION = "112-quality-1"
PROGRAMS = {
    "ops112": ("adres", "tip", "sluzhby", "tayming", "polnota", "rech"),
    "dds": ("podtverzhdenie", "proverka", "peredacha", "statusy", "rech"),
}
FAKT_SKILL = {
    "adres": "adres", "tip": "tip", "sluzhby": "sluzhby", "normativ": "tayming",
    "polnota": "polnota", "rech": "rech", "podtverzhdenie": "podtverzhdenie",
    "proverka": "proverka", "lozhnye": "proverka", "peredacha": "peredacha",
    "po_faktu": "statusy", "svoevremenno": "statusy", "zaversheno": "statusy",
    "kommentarii": "statusy",
}

def normalized(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text or "")).lower().replace("ё", "е")).strip()

def address_parts(text):
    s = normalized(text)
    # Numeric street names remain in the street component. Unit labels preserve roles.
    patterns = {
        "house": r"(?<!\w)(?:дом|д\.)\s*([0-9]+[а-яa-z]?(?:[/-][0-9]+[а-яa-z]?)?)",
        "corpus": r"(?<!\w)(?:корпус|корп\.?|к\.)\s*([0-9]+[а-яa-z]?)",
        "building": r"(?<!\w)(?:строение|стр\.?)\s*([0-9]+[а-яa-z]?)",
        "apartment": r"(?<!\w)(?:квартира|кв\.)\s*([0-9]+[а-яa-z]?)",
        "km": r"(?<!\w)([0-9]+(?:[.,][0-9]+)?)\s*км\b",
    }
    parts = {}
    for name, pattern in patterns.items():
        found = list(re.finditer(pattern, s))
        if found:
            parts[name] = tuple(m.group(1).replace(",", ".") for m in found)
            s = re.sub(pattern, " ", s)
    # Expand road types rather than remove them: улица and переулок differ.
    for a, b in [(r"\bул\.", "улица"), (r"\bпросп\.", "проспект"),
                 (r"\bпер\.", "переулок"), (r"\bш\.", "шоссе"),
                 (r"\bг\.", "город"), (r"\bобл\.", "область")]:
        s = re.sub(a, b, s)
    s = re.sub(r"\bгород\b", " ", s)
    parts["location"] = tuple(re.findall(r"[\w/-]+", s))
    return parts

def compare_address(value, expected, spoken=False):
    a, b = address_parts(value), address_parts(expected)
    errors = []
    for key in set(a) | set(b):
        if key == "location" and spoken:
            # Speech includes other words; require the complete location phrase in order.
            want, got = b[key], a[key]
            good = bool(want) and any(got[i:i+len(want)] == want for i in range(len(got)))
        else:
            good = a.get(key) == b.get(key)
        if not good:
            errors.append(key)
    valid = bool(normalized(value)) and bool(normalized(expected)) and not errors
    return {"sovpalo": valid, "dolya": 1.0 if valid else 0.0,
            "naydeno": [k for k in b if k not in errors], "propushcheno": sorted(errors),
            "etalon": expected, "vvod": value, "components": {"actual": a, "expected": b},
            "review_required": not valid}

def routes(position, card):
    if not position:
        return []
    out = set(position.get("sluzhby", []))
    if position.get("smp", {}).get(card.get("postradavshie")):
        out.add("SMP")
    if card.get("pravonarushenie") is True and position.get("mvd_pri_pravonarushenii"):
        out.add("MVD")
    return sorted(out)

def result(facts, kind, critical=None):
    required = set(critical or ({"adres", "tip", "sluzhby", "polnota"} if kind == "ops112"
                   else {"podtverzhdenie", "proverka", "peredacha", "po_faktu", "zaversheno"}))
    observed = [f for f in facts if f.proyden is not None]
    total = sum(f.ves for f in observed) or 1
    earned = sum(f.ves for f in observed if f.proyden)
    failed = sorted(required - {f.kod for f in facts if f.proyden is True})
    skills = {}
    for f in observed:
        sk = FAKT_SKILL.get(f.kod)
        if sk in PROGRAMS[kind]:
            skills.setdefault(sk, []).append(bool(f.proyden))
    ball = round(100 * earned / total)
    return {"ball": ball, "nabrano": earned, "vsego": total,
            "fakty": [f.to_dict() for f in facts],
            "navyki": {sk: sum(v)/len(v) for sk,v in skills.items()},
            "critical_errors": failed, "passed": ball >= 70 and not failed,
            "verdikt": "зачёт" if ball >= 70 and not failed else "не зачтено",
            "rubric_version": RUBRIC_VERSION, "kind": kind}

def public_scenario(data):
    return {k: data.get(k) for k in ("id", "nazvanie", "bilet", "vyzov", "situaciya",
            "adres_vidimy", "trebuet_utochneniya", "slozhnost", "source", "status")}

def validate_card(card):
    from fastapi import HTTPException
    # tip_itog is the existing UI's display label; never used as grading evidence.
    texts = {"adres_polny", "opisanie", "zayavitel_fio", "zayavitel_telefon", "tip_kod", "tip_itog"}
    allowed = texts | {"postradavshie", "pravonarushenie", "sluzhby"}
    for key, value in card.items():
        if key not in allowed:
            raise HTTPException(422, "Неизвестное поле карточки: " + key)
        if key in texts and (not isinstance(value, str) or len(value) > 5000):
            raise HTTPException(422, "Поле должно быть строкой до 5000 символов: " + key)
        if key == "pravonarushenie" and type(value) is not bool:
            raise HTTPException(422, "pravonarushenie: требуется boolean")
        if key == "postradavshie" and value not in ("net", "est", "ne_na_meste", "neizvestno", "", None):
            raise HTTPException(422, "Неизвестное значение postradavshie")
        if key == "sluzhby" and (not isinstance(value, list) or len(value)>100 or
                                  any(not isinstance(x,str) for x in value)):
            raise HTTPException(422, "sluzhby: требуется список кодов")
