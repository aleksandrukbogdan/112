"""
Клиент к vLLM (OpenAI-совместимый API).

Две тонкости под конкретное развёртывание:

1. Имя модели — это --served-model-name, а не путь к весам.
2. У vLLM включён --enable-prefix-caching, поэтому статичная часть промпта
   идёт ПЕРВОЙ, а меняющаяся — последней. Тогда KV-кэш переиспользуется
   между ходами диалога и второй-третий ход считается заметно быстрее.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger("llm")

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.llm_timeout_sec)
    return _client


async def chat(
    system: str,
    user: str,
    temperature: float = 0.8,
    max_tokens: int = 120,
    extra: dict | None = None,
) -> str:
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": system},   # статично -> кэшируется
            {"role": "user", "content": user},       # меняется -> в конце
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra:
        payload.update(extra)

    r = await client().post(f"{settings.llm_base_url}/chat/completions", json=payload)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


async def chat_json(
    system: str,
    user: str,
    schema: dict,
    temperature: float = 0.0,
    max_tokens: int = 128,
) -> str:
    """Структурированный вывод: vLLM гарантирует соответствие схеме."""
    return await chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        extra={"guided_json": schema},
    )


async def health() -> dict:
    try:
        r = await client().get(f"{settings.llm_base_url}/models", timeout=5.0)
        r.raise_for_status()
        names = [m["id"] for m in r.json().get("data", [])]
        return {"ok": settings.llm_model in names, "models": names}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
