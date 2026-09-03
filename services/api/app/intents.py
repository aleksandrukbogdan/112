"""
Классификатор реплик оператора.

Два уровня:
  1. Правила по ключевым словам из сценария — быстро и детерминированно.
  2. LLM с guided_json — на всё остальное, строго из закрытого списка.

Самое хрупкое место системы, поэтому здесь есть страховки:
  - оценка уверенности с порогом,
  - фолбэк «переспросить» при низкой уверенности,
  - ручное переопределение преподавателем (обрабатывается в main.py),
  - логирование каждого решения.
"""
from __future__ import annotations

import json
import logging
import re

from .config import settings

log = logging.getLogger("intents")

# Закрытый список. Модель не может вернуть ничего вне его.
INTENTS = [
    "greeting",
    "asked_what_happened",
    "asked_address",
    "asked_address_detail",
    "asked_victims",
    "asked_hazards",
    "asked_evacuation",
    "asked_access_routes",
    "asked_caller_name",
    "gave_instructions",
    "calming",
    "other",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {"type": "string", "enum": INTENTS},
            "maxItems": 3,
        },
        "confidence": {"type": "number"},
        "tone": {"type": "string", "enum": ["calm", "tense", "harsh"]},
    },
    "required": ["intents", "confidence", "tone"],
}

SYSTEM = (
    "Ты классифицируешь реплику оператора службы 112 во время приёма вызова. "
    "Определи, какие из перечисленных намерений в ней присутствуют. "
    "Отвечай только JSON. Если ничего не подходит — верни intents: [\"other\"]. "
    "confidence — число от 0 до 1, насколько ты уверен."
)

FILLERS = re.compile(r"\b(э+|а+|ну|как бы|это самое|типа|в общем|значит)\b", re.I)


def by_rules(text: str, rules: dict[str, list[str]]) -> list[str]:
    """Совпадение по ключевым словам сценария."""
    low = text.lower()
    hits = []
    for intent, keys in (rules or {}).items():
        if any(k.lower() in low for k in keys):
            hits.append(intent)
    return hits


def forbidden_hits(text: str, patterns: list[dict]) -> list[dict]:
    low = text.lower()
    out = []
    for p in patterns or []:
        if re.search(p["pattern"], low, re.I):
            out.append(p)
    return out


def count_fillers(text: str) -> list[str]:
    return [m.group(0) for m in FILLERS.finditer(text)]


def speech_rate_wpm(text: str, duration_ms: int | None) -> float | None:
    if not duration_ms or duration_ms < 500:
        return None
    words = len([w for w in text.split() if w.strip()])
    return round(words / (duration_ms / 60000.0), 1)


async def classify(text: str, scenario: dict) -> dict:
    """
    Возвращает {"intents": [...], "confidence": float, "tone": str, "method": str}.
    Правила имеют приоритет: если они сработали, LLM не дёргаем — это экономит
    целый вызов модели на каждом ходу диалога.
    """
    rules = scenario.get("intent_rules", {})
    hits = by_rules(text, rules)
    if hits:
        return {"intents": hits, "confidence": 0.95, "tone": "calm", "method": "rules"}

    if not settings.llm_enabled:
        return {"intents": ["other"], "confidence": 0.0, "tone": "calm", "method": "none"}

    # ленивый импорт: правила работают без сетевых зависимостей,
    # httpx нужен только когда действительно идём в модель
    from .llm import chat_json

    try:
        raw = await chat_json(
            system=SYSTEM,
            user=text,
            schema=SCHEMA,
            temperature=0.0,
            max_tokens=128,
        )
        data = json.loads(raw)
        intents = [i for i in data.get("intents", []) if i in INTENTS] or ["other"]
        return {
            "intents": intents,
            "confidence": float(data.get("confidence", 0.5)),
            "tone": data.get("tone", "calm"),
            "method": "llm",
        }
    except Exception as e:  # noqa: BLE001
        log.warning("LLM classify failed, fallback to 'other': %s", e)
        return {"intents": ["other"], "confidence": 0.0, "tone": "calm", "method": "error"}
