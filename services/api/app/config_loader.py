"""
Загрузчик конфигурации.

ПРИНЦИП ПРОЕКТА: в коде — механика, в config/ — всё нормативное.
Ни одного классификатора, порога, вопроса или названия службы
в исходниках. Меняется норматив — правится YAML, код не трогаем.

Проверка принципа: переход на другой субъект РФ должен быть
копированием config/regions/so.yaml. Если пришлось лезть в .py —
значит что-то нормативное просочилось в код, и это баг.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
REGION = os.environ.get("REGION", "so")


def _read(rel: str) -> dict:
    p = CONFIG_DIR / rel
    if not p.exists():
        raise FileNotFoundError(f"нет файла конфигурации: {p}")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def normativy() -> dict:
    """Сроки из ПП 1931 п.9 подп.«р»."""
    return _read("normativy.yaml")


@lru_cache(maxsize=1)
def routes() -> list[dict]:
    """13 направлений реагирования (форма 2/112)."""
    return _read("routes.yaml")["routes"]


@lru_cache(maxsize=1)
def routes_by_kod() -> dict[str, dict]:
    return {r["kod"]: r for r in routes()}


@lru_cache(maxsize=1)
def ukio() -> dict:
    """Структура УЧОЛ и УКИО."""
    return _read("ukio.yaml")


@lru_cache(maxsize=1)
def rechevye_pravila() -> dict:
    """Правила речи (методика МЧС) и критерии оценки (APCO/NENA)."""
    return _read("rechevye_pravila.yaml")


@lru_cache(maxsize=1)
def region() -> dict:
    return _read(f"regions/{REGION}.yaml")


@lru_cache(maxsize=32)
def classifier(name: str) -> dict:
    """Классификатор по имени файла без расширения."""
    return _read(f"classifiers/{name}.yaml")


@lru_cache(maxsize=32)
def protocol(name: str = "_base") -> dict:
    return _read(f"protocols/{name}.yaml")


# ---------------------------------------------------------------- производные

def obyazatelnye_polya() -> list[str]:
    """Ключи полей общей части УКИО с req: true."""
    u = ukio()
    out = []
    for group in u["obshchaya"].values():
        for f in group:
            if f.get("req"):
                out.append(f["key"])
    return out


def polya_karty(sluzhba: str | None = None) -> list[dict]:
    """Плоский список полей: общая часть плюс специальная, если указана."""
    u = ukio()
    out: list[dict] = []
    for gname, group in u["obshchaya"].items():
        for f in group:
            out.append({**f, "group": gname, "sekciya": "obshchaya"})
    if sluzhba:
        spec = u["special"].get(sluzhba)
        if spec:
            for f in spec["polya"]:
                out.append({**f, "group": sluzhba, "sekciya": "special"})
    return out


def sluzhby_dlya_tipa(kod_tipa: str) -> list[str]:
    """Какие службы привлекаются при этом типе происшествия."""
    for item in classifier("tip_proisshestviya")["items"]:
        if item["kod"] == kod_tipa:
            return item.get("sluzhby", [])
    return []


def forma_smp_dlya_povoda(kod_povoda: str) -> str | None:
    """
    Экстренная или неотложная форма по поводу.
    Источник правила: приказ Минздрава 388н, п.11 и п.13.
    """
    for item in classifier("povod_smp")["items"]:
        if item["kod"] == kod_povoda:
            return item.get("forma")
    return None


def uroven_chs_po_chislam(postradavshih: int = 0, pogibshih: int = 0) -> str:
    """
    Автоматический расчёт уровня ЧС.
    В боевом АРМ такой расчёт есть, с пометкой об автоматической установке.
    """
    best = "NET"
    for item in classifier("uroven_chs")["items"]:
        if (postradavshih >= item.get("postradavshih_ot", 0)
                or pogibshih >= item.get("pogibshih_ot", 0)):
            best = item["kod"]
    return best


def zaglushki() -> list[dict]:
    """
    Какие части конфигурации ещё не заменены настоящими документами.
    Показывается в интерфейсе, чтобы на демо не выдавать заглушку
    за утверждённый норматив.
    """
    out = []
    import glob
    for p in glob.glob(str(CONFIG_DIR / "**" / "*.yaml"), recursive=True):
        try:
            with open(p, encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
        except Exception:
            continue
        if isinstance(d, dict) and str(d.get("status", "")).upper() == "ZAGLUSHKA":
            out.append({
                "fayl": str(Path(p).relative_to(CONFIG_DIR)),
                "zamenit_na": d.get("zamenit_na", ""),
                "vremenno": d.get("istochnik_vremenno", ""),
            })
    return out


def sbros_kesha() -> None:
    """Перечитать конфигурацию без перезапуска контейнера."""
    for fn in (normativy, routes, routes_by_kod, ukio,
               rechevye_pravila, region, classifier, protocol):
        fn.cache_clear()


def svodka() -> dict:
    """Что загружено — для /health и стартового лога."""
    return {
        "region": region()["nazvanie"],
        "operator": region()["operator_112"]["korotko"],
        "marshrutov": len(routes()),
        "spec_chastey": len(ukio()["special"]),
        "poley_vsego": sum(len(g) for g in ukio()["obshchaya"].values())
                       + sum(len(v["polya"]) for v in ukio()["special"].values()),
        "obyazatelnyh": len(obyazatelnye_polya()),
        "zaglushek": len(zaglushki()),
        "normativ_kartochki_sec": normativy()["opros_i_kartochka_sec"],
    }
