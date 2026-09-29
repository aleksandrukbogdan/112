"""Frozen evaluator for attempts started before the 2026-09-28 upgrade."""
import re
from . import legacy_domain as D, config as C
POST_RU={"net":"нет","est":"есть","neizvestno":"нет данных"}

def ocenit_dds(s: dict) -> dict:
    cfg_dds = s.get("_rules", {}).get("dds", C.DDS)
    k = s["kartochka"]
    t0 = s["nachalo"]
    F: list[D.Fakt] = []
    st = s["statusy"]
    first = st[0] if st else None
    prin = next((x for x in st if x["kod"] == "prinyata"), None)
    tp = round(prin["t"] - t0, 1) if prin else None
    F.append(D.Fakt("podtverzhdenie", f"Приём подтверждён за {cfg_dds['podtverzhdenie_sec']} с",
                    bool(prin) and first["kod"] == "prinyata" and tp <= s.get("_rules", {}).get("dds", C.DDS)["podtverzhdenie_sec"], 3,
                    "ПП РФ 1931, п. 9 подп. «р»: подтверждение ДДС — 30 с",
                    {"fakt_sec": tp, "normativ_sec": s.get("_rules", {}).get("dds", C.DDS)["podtverzhdenie_sec"]}))

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
        cfg = next(c for c in s.get("_rules", {}).get("dds", C.DDS)["statusy"] if c["kod"] == x["kod"])
        o = osn.get(cfg["osnovanie"])
        if not o or o["t"] > x["t"]:
            bez.append(x["nazvanie"])
        elif x["t"] - o["t"] > s.get("_rules", {}).get("dds", C.DDS)["svoevremenno_sec"] and x["kod"] != "prinyata":
            pozdno.append(f"{x['nazvanie']}: {round(x['t'] - o['t'])} с")
    F.append(D.Fakt("po_faktu", "Статусы только по факту получения информации", bool(st) and not bez, 3,
                    s.get("_rules", {}).get("dds", C.DDS)["istochnik_statusov"], {"bez_osnovaniya": bez}))
    F.append(D.Fakt("svoevremenno", f"Статус обновлён не позже {cfg_dds['svoevremenno_sec']} с после сведений",
                    bool(st) and not pozdno, 2, "Учебный норматив, настраивается", {"pozdno": pozdno}))
    zaversh = any(x["kod"] == "zaversheno" for x in st)
    F.append(D.Fakt("zaversheno", "Реагирование доведено до «Работы завершены»", zaversh, 2,
                    "Памятка для ДДС", {"poslednij": st[-1]["nazvanie"] if st else None}))
    nuzhen = {c["kod"] for c in s.get("_rules", {}).get("dds", C.DDS)["statusy"] if c["kommentariy"]}
    bez_k = [x["nazvanie"] for x in st if x["kod"] in nuzhen and not x["kommentariy"]]
    F.append(D.Fakt("kommentarii", "Комментарии к статусам заполнены", bool(st) and not bez_k, 1,
                    "Памятка для ДДС", {"bez_kommentariya": bez_k}))
    nar = s["narusheniya_rechi"]
    speech = any(m.get("kto") == "dispetcher" and m.get("tekst", "").strip()
                 for call in s.get("zvonki", []) for m in call.get("dialog", []))
    F.append(D.Fakt("rech", "Правила речи в переговорах", not nar if speech else None, 1, "Методика МЧС России",
                    {"narusheniy": len(nar), "spisok": nar[:5]}))

    return {**D.Q.result(F, "dds"), "rubric_version":"112-quality-1", "t_podtverzhdeniya": tp}
