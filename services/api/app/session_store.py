"""
Хранилище сессий в Redis.

Зачем не в памяти процесса: при нескольких воркерах uvicorn запрос
может прийти в другой процесс, и сессия «потеряется». Плюс перезапуск
контейнера обнуляет все идущие вызовы. На хакатоне это выглядит
как случайные пропадания — искать причину будет некогда.

Состояние ProtocolEngine сериализуемо целиком: движок не держит
ссылок на соединения или файлы, только словари и множества.
"""

from __future__ import annotations

import json
import os
from typing import Any

import redis.asyncio as aioredis

from .protocol_engine import ProtocolEngine, Sostoyanie

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
TTL_SEC = int(os.environ.get("SESSION_TTL_SEC", 6 * 3600))
PREFIX = "t112:sess:"

# Пул привязан к event loop. Одного глобального пула недостаточно:
# при нескольких воркерах uvicorn и в тестах, где на каждый запрос
# создаётся свой loop, соединение из чужого цикла падает с
# «Event loop is closed».
_pools: dict[int, aioredis.Redis] = {}


async def redis() -> aioredis.Redis:
    import asyncio
    loop_id = id(asyncio.get_running_loop())
    pool = _pools.get(loop_id)
    if pool is None:
        pool = aioredis.from_url(REDIS_URL, encoding="utf-8",
                                 decode_responses=True,
                                 health_check_interval=30)
        _pools[loop_id] = pool
    return pool


async def zakryt() -> None:
    """Закрыть пулы — вызывается в lifespan при остановке."""
    import asyncio
    loop_id = id(asyncio.get_running_loop())
    pool = _pools.pop(loop_id, None)
    if pool is not None:
        await pool.aclose()


async def zdorov() -> bool:
    try:
        r = await redis()
        return await r.ping()
    except Exception:
        return False


# ---------------------------------------------------------------- сериализация

def _engine_v_dict(e: ProtocolEngine) -> dict:
    return {
        "protokol": e.protokol_name,
        "tip": e.tip,
        "zadannye": sorted(e.s.zadannye),
        "zapolnennye": e.s.zapolnennye,
        "kontekst": sorted(e.s.kontekst),
        "repliki": e.s.repliki,
        "nachalo_ms": e.s.nachalo_ms,
        "sluzhby": e.s.sluzhby,
    }


def _dict_v_engine(d: dict) -> ProtocolEngine:
    e = ProtocolEngine(protokol=d.get("protokol", "_base"),
                       tip_proisshestviya=d.get("tip"))
    e.s = Sostoyanie(
        zadannye=set(d.get("zadannye", [])),
        zapolnennye=d.get("zapolnennye", {}),
        kontekst=set(d.get("kontekst", [])),
        repliki=[tuple(x) for x in d.get("repliki", [])],
        nachalo_ms=d.get("nachalo_ms", 0),
        sluzhby=d.get("sluzhby", []),
    )
    return e


# ---------------------------------------------------------------- API хранилища

async def sozdat(sid: str, data: dict, engine: ProtocolEngine) -> None:
    r = await redis()
    payload = {**data, "_engine": _engine_v_dict(engine)}
    await r.set(PREFIX + sid, json.dumps(payload, ensure_ascii=False), ex=TTL_SEC)


async def prochitat(sid: str) -> tuple[dict, ProtocolEngine] | None:
    r = await redis()
    raw = await r.get(PREFIX + sid)
    if not raw:
        return None
    d = json.loads(raw)
    eng = _dict_v_engine(d.pop("_engine"))
    return d, eng


async def sohranit(sid: str, data: dict, engine: ProtocolEngine) -> None:
    """Перезаписывает сессию, продлевая TTL."""
    await sozdat(sid, data, engine)


async def udalit(sid: str) -> None:
    r = await redis()
    await r.delete(PREFIX + sid)


async def spisok_id() -> list[str]:
    """Идентификаторы живых сессий — для отчётов."""
    r = await redis()
    out = []
    async for k in r.scan_iter(match=PREFIX + "*", count=200):
        out.append(k[len(PREFIX):])
    return out


async def vse_sessii() -> list[dict]:
    """Все живые сессии без движков — для форм 1/112 и 2/112."""
    r = await redis()
    ids = await spisok_id()
    if not ids:
        return []
    raw = await r.mget([PREFIX + i for i in ids])
    out = []
    for x in raw:
        if not x:
            continue
        d = json.loads(x)
        d.pop("_engine", None)
        out.append(d)
    return out


# ---------------------------------------------------------------- завершённые
# Итоги вызовов живут дольше сессий: по ним строится аналитика.

ITOG_PREFIX = "t112:itog:"
ITOG_TTL = int(os.environ.get("ITOG_TTL_SEC", 30 * 24 * 3600))


async def sohranit_itog(sid: str, itog: dict) -> None:
    r = await redis()
    await r.set(ITOG_PREFIX + sid, json.dumps(itog, ensure_ascii=False), ex=ITOG_TTL)
    await r.lpush("t112:itogi", sid)
    await r.ltrim("t112:itogi", 0, 4999)


async def itogi(limit: int = 500) -> list[dict]:
    r = await redis()
    ids = await r.lrange("t112:itogi", 0, limit - 1)
    if not ids:
        return []
    raw = await r.mget([ITOG_PREFIX + i for i in ids])
    return [json.loads(x) for x in raw if x]
