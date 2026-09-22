"""
Ядро: сверка адреса, граф доказательств, оценка.

ПРИНЦИП: здесь нет ИИ. Все факты считаются правилами и сверяются
с эталоном из билета либо с классификатором. Каждый факт несёт
ссылку на норму, по которой он проверен.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any

from . import config as C

# ---------------------------------------------------------------- адрес

# Служебные слова, не влияющие на совпадение адреса
_SHUM = {
    "г", "гор", "город", "ул", "улица", "пр", "просп", "проспект", "пер", "переулок",
    "ш", "шоссе", "б", "бул", "бульвар", "наб", "набережная", "пл", "площадь",
    "д", "дом", "к", "корп", "корпус", "стр", "строение", "вл", "владение",
    "кв", "квартира", "под", "подъезд", "эт", "этаж", "код", "домофон",
    "мкр", "микрорайон", "пос", "посёлок", "поселок", "дер", "деревня",
    "обл", "область", "р-н", "район", "км", "мо", "москва",
}

_ZAMENY = [
    (r"\bмо\b", "московская область"),
    (r"\bнм\b", "новая москва"),
    (r"\bш\.", "шоссе"),
    (r"\bул\.", "улица"),
    (r"\bд\.", "дом"),
]


def norm_txt(s: str) -> str:
    """Нормализация для сравнения: ё→е, нижний регистр, без пунктуации."""
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    for pat, rep in _ZAMENY:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> set[str]:
    """Значимые токены адреса: слова длиннее 2 и все числа."""
    out = set()
    for w in norm_txt(s).split():
        if w.isdigit():
            out.add(w)
        elif len(w) > 2 and w not in _SHUM:
            out.add(w)
    return out


def sravnit_adres(vvod: str, etalon: str) -> dict:
    """
    Сверка введённого адреса с эталоном из билета.

    Эталон в билетах стоит курсивом в скобках: заявитель называет
    ориентир, оператор обязан довести до точного адреса.
    Это самая ценная проверка во всём тренажёре.
    """
    tv, te = tokens(vvod), tokens(etalon)
    if not te:
        return {"sovpalo": False, "dolya": 0.0, "naydeno": [], "propushcheno": []}

    # числа (дом, корпус, километр) весят больше слов
    chisla_e = {t for t in te if t.isdigit()}
    chisla_v = {t for t in tv if t.isdigit()}
    slova_e = te - chisla_e
    slova_v = tv - chisla_e

    sovp_ch = chisla_e & chisla_v
    sovp_sl = slova_e & slova_v

    ves_ch = 2.0
    itog = (len(sovp_ch) * ves_ch + len(sovp_sl))
    maks = (len(chisla_e) * ves_ch + len(slova_e)) or 1
    dolya = round(itog / maks, 3)

    return {
        "sovpalo": dolya >= 0.7,
        "dolya": dolya,
        "naydeno": sorted(sovp_ch | sovp_sl),
        "propushcheno": sorted(te - tv),
        "etalon": etalon,
        "vvod": vvod,
    }


# ---------------------------------------------------------------- речь

def proverit_rech(tekst: str) -> list[dict]:
    """Нарушения правил речи по методике МЧС."""
    low = norm_txt(tekst)
    out = []
    for kod, rule in C.RECH.items():
        for obr in rule["obrazcy"]:
            if norm_txt(obr) in low:
                out.append({
                    "pravilo": kod,
                    "ves": rule["ves"],
                    "opisanie": rule["opisanie"],
                    "fragment": obr,
                    "zamena": rule.get("zamena", {}).get(obr),
                    "istochnik": "Методика МЧС России",
                })
    return out


# ---------------------------------------------------------------- грамматика

_GRAM = [
    (r"\bкороч?е\b", "разговорное слово"),
    (r"\bтипа\b", "разговорное слово"),
    (r"\bкак бы\b", "слово-паразит"),
    (r"\bв общем-то\b", "слово-паразит"),
    (r"\bзвОнит\b", "ударение"),
    (r"\bложить\b", "правильно «класть»"),
    (r"\bихний\b", "правильно «их»"),
    (r"\bпо приезду\b", "правильно «по приезде»"),
    (r"\bсогласно приказа\b", "правильно «согласно приказу»"),
    (r"\bоплатить за\b", "правильно «оплатить что-либо»"),
    (r"(.)\1{3,}", "повтор символов"),
    (r"[!?]{3,}", "избыточная пунктуация"),
]


def proverit_grammatiku(tekst: str) -> list[dict]:
    """
    Проверка ручного ввода. Требование ТЗ: отчёт о занятии должен
    содержать сведения о грамматике.
    """
    out = []
    t = (tekst or "")
    low = t.lower().replace("ё", "е")
    for pat, opis in _GRAM:
        m = re.search(pat, low)
        if m:
            out.append({"fragment": m.group(0), "zamechanie": opis})
    if t and t.strip() and t.strip()[0].islower():
        out.append({"fragment": t.strip()[:18], "zamechanie": "начать с заглавной буквы"})
    if len(t.split()) < 4 and t.strip():
        out.append({"fragment": t.strip()[:24], "zamechanie": "описание слишком короткое"})
    return out


# ---------------------------------------------------------------- граф доказательств

@dataclass
class Fakt:
    kod: str
    nazvanie: str
    proyden: bool
    ves: int
    istochnik: str
    detali: dict = field(default_factory=dict)
    t_ms: int | None = None

    def to_dict(self):
        return asdict(self)


def postroit_grafu(sess: dict) -> list[Fakt]:
    """
    Граф доказательств. Восемь фактов, все детерминированные.
    Каждый со ссылкой на норму, по которой проверен.
    """
    bil = sess["bilet"]
    k = sess["kartochka"]
    f: list[Fakt] = []
    idx = C.load("index")

    # 1. адрес против эталона
    adr = sravnit_adres(k.get("adres_polny", ""), bil["adres_etalon"])
    f.append(Fakt("adres", "Адрес доведён до эталона", adr["sovpalo"], 3,
                  "Эталон билета", adr, sess.get("t_adres_ms")))

    # 2. тип происшествия
    kod = k.get("tip_kod", "")
    poz = idx.get(kod)
    verno_grp = bool(poz) and poz["g"] == bil["gruppa"]
    # у сгенерированных сценариев эталон точнее: ещё и признак 1-го уровня
    if verno_grp and bil.get("pr1_etalon"):
        verno_grp = poz["pr1"] == bil["pr1_etalon"]
    f.append(Fakt("tip", "Тип происшествия выбран верно", verno_grp, 3,
                  "Классификатор 0.46.24",
                  {"vybrano": kod, "itog": poz["itog"] if poz else None,
                   "gruppa_vybrana": poz["g"] if poz else None,
                   "gruppa_etalon": bil["gruppa"]}, sess.get("t_tip_ms")))

    # 3. службы против классификатора
    nado = set(poz["sluzhby"]) if poz else set()
    bylo = set(k.get("sluzhby", []))
    sovp = bool(nado) and nado.issubset(bylo)
    f.append(Fakt("sluzhby", "Службы назначены верно", sovp or (not nado and bool(bylo)), 3,
                  "Классификатор 0.46.24, колонки маршрутизации",
                  {"nado": sorted(nado), "naznacheno": sorted(bylo),
                   "propushcheno": sorted(nado - bylo),
                   "lishnie": sorted(bylo - nado)}))

    # 4. норматив 75 с
    t = sess.get("t_otpravki_sec")
    v_norm = t is not None and t <= C.NORM["kartochka_sec"]
    f.append(Fakt("normativ", f"Карточка отправлена за {C.NORM['kartochka_sec']} с",
                  bool(v_norm), 3, C.NORM["istochnik"],
                  {"fakt_sec": t, "normativ_sec": C.NORM["kartochka_sec"]}))

    # 5. учебный тайминг из ТЗ
    ut = sess.get("tayming_sec") or C.UCHEBNY_TAYMING_SEC
    f.append(Fakt("uchebny_tayming", f"Учебный лимит {ut} с",
                  t is not None and t <= ut, 1,
                  "ТЗ ДГОЧСиПБ: тайминг задаёт преподаватель, по умолчанию 30 с",
                  {"fakt_sec": t, "limit_sec": ut}))

    # 6. обязательные поля
    obyaz = ["adres_polny", "opisanie", "zayavitel_fio", "zayavitel_telefon", "tip_kod"]
    net = [x for x in obyaz if not str(k.get(x, "")).strip()]
    f.append(Fakt("polnota", "Обязательные поля заполнены", not net, 2,
                  "Приложение к ПП РФ 1931 (структура УКИО)",
                  {"ne_zapolneno": net, "vsego": len(obyaz)}))

    # 7. речь
    nar = sess.get("narusheniya_rechi", [])
    f.append(Fakt("rech", "Правила речи соблюдены", not nar, 2,
                  "Методика МЧС России",
                  {"narusheniy": len(nar), "spisok": nar[:6]}))

    # 8. грамматика
    gram = proverit_grammatiku(k.get("opisanie", ""))
    f.append(Fakt("grammatika", "Грамматика описания", not gram, 1,
                  "ТЗ ДГОЧСиПБ: отчёт должен содержать сведения о грамматике",
                  {"zamechaniy": len(gram), "spisok": gram[:6]}))

    return f


def ocenit(sess: dict) -> dict:
    """Оценка из графа. Никаких моделей — только веса фактов."""
    fakty = postroit_grafu(sess)
    nabrano = sum(x.ves for x in fakty if x.proyden)
    vsego = sum(x.ves for x in fakty) or 1
    ball = round(100 * nabrano / vsego)

    po_navykam = {}
    karta = {"adres": "adres", "tip": "tip", "sluzhby": "sluzhby",
             "normativ": "tayming", "uchebny_tayming": "tayming",
             "rech": "rech", "grammatika": "rech", "polnota": "polnota"}
    for x in fakty:
        nav = karta.get(x.kod)
        if nav:
            po_navykam.setdefault(nav, []).append(x.proyden)
    navyki = {k: round(sum(v) / len(v), 2) for k, v in po_navykam.items()}

    return {
        "ball": ball,
        "nabrano": nabrano,
        "vsego": vsego,
        "fakty": [x.to_dict() for x in fakty],
        "navyki": navyki,
        "verdikt": "зачёт" if ball >= 70 else "не зачтено",
    }


# ---------------------------------------------------------------- прогноз

def prognoz_v_vyzove(sess: dict, t_sec: float) -> dict:
    """
    Прогноз ВНУТРИ вызова: уложится ли в норматив.
    Проверяется через 45 секунд после выдачи — прямо при экспертах.
    """
    norm = C.NORM["kartochka_sec"]
    k = sess["kartochka"]
    obyaz = ["adres_polny", "opisanie", "zayavitel_fio", "zayavitel_telefon", "tip_kod"]
    gotovo = sum(1 for x in obyaz if str(k.get(x, "")).strip())
    dolya = gotovo / len(obyaz)

    ostalos = max(0.0, norm - t_sec)
    # темп: сколько полей в секунду
    temp = (gotovo / t_sec) if t_sec > 0 else 0
    nado = len(obyaz) - gotovo
    nuzhno_sec = (nado / temp) if temp > 0 else 999

    p = 0.5
    if nado == 0:
        p = 0.98
    elif nuzhno_sec <= ostalos * 0.7:
        p = 0.85
    elif nuzhno_sec <= ostalos:
        p = 0.62
    elif ostalos <= 0:
        p = 0.05
    else:
        p = max(0.05, round(0.5 * ostalos / max(nuzhno_sec, 1), 2))

    return {
        "predmet": "uspeet_v_normativ",
        "veroyatnost": round(p, 2),
        "t_prognoza_sec": round(t_sec, 1),
        "proverka_na_sec": norm,
        "osnovanie": {
            "poley_gotovo": gotovo, "poley_vsego": len(obyaz),
            "dolya": round(dolya, 2),
            "ostalos_sec": round(ostalos, 1),
            "nuzhno_sec": round(min(nuzhno_sec, 999), 1),
        },
    }
