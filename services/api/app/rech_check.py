"""
Проверка речи оператора по методике МЧС.
Источник правил: config/rechevye_pravila.yaml
"""
from __future__ import annotations
import re
from . import config_loader as C


def proverit(tekst: str) -> list[dict]:
    """Находит нарушения правил речи. Возвращает список замечаний."""
    pr = C.rechevye_pravila()["zapreshcheno"]
    low = tekst.lower()
    out: list[dict] = []

    for kod, rule in pr.items():
        if kod == "slozhnye_frazy":
            for s in re.split(r"[.!?]+", tekst):
                n = len(s.split())
                if n > rule.get("max_slov_v_predlozhenii", 15):
                    out.append({"pravilo": kod, "ves": rule["ves"],
                                "opisanie": rule["opisanie"],
                                "fragment": s.strip()[:60], "slov": n})
            continue
        for obr in rule.get("obrazcy", []):
            if obr in low:
                zam = rule.get("zamena", {}).get(obr)
                out.append({"pravilo": kod, "ves": rule["ves"],
                            "opisanie": rule["opisanie"], "fragment": obr,
                            "zamena": zam})
    return out


def shtraf(narusheniya: list[dict]) -> int:
    return sum(n["ves"] for n in narusheniya)
