"""Слой доступа к БД. Ничего сложного: пул asyncpg и несколько запросов."""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from .config import settings

log = logging.getLogger("db")

_pool: asyncpg.Pool | None = None


async def init() -> None:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=8)
    log.info("Пул подключений к БД создан")


async def close() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Пул не инициализирован — вызовите db.init()")
    return _pool


async def healthy() -> bool:
    try:
        async with pool().acquire() as c:
            await c.fetchval("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- сценарии

async def upsert_scenario(sc: dict) -> str:
    async with pool().acquire() as c:
        return await c.fetchval(
            """
            INSERT INTO scenario (slug, title, difficulty, services, incident_type, body)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (slug) DO UPDATE SET
                title = EXCLUDED.title,
                difficulty = EXCLUDED.difficulty,
                services = EXCLUDED.services,
                incident_type = EXCLUDED.incident_type,
                body = EXCLUDED.body,
                version = scenario.version + 1
            RETURNING id::text
            """,
            sc["slug"], sc["title"], sc.get("difficulty", 1),
            sc.get("services", []), sc.get("incident_type"), json.dumps(sc),
        )


async def list_scenarios() -> list[dict]:
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT id::text, slug, title, difficulty, services, incident_type
               FROM scenario WHERE is_active ORDER BY difficulty, title"""
        )
    return [dict(r) for r in rows]


async def get_scenario(slug: str) -> dict | None:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT id::text, body FROM scenario WHERE slug = $1", slug
        )
    if not row:
        return None
    body = row["body"]
    body = json.loads(body) if isinstance(body, str) else body
    body["_id"] = row["id"]
    return body


# ---------------------------------------------------------------- сессии

async def create_session(
    trainee_id: str, scenario_id: str, mode: str = "training",
    instructor_id: str | None = None,
) -> dict:
    async with pool().acquire() as c:
        attempt = await c.fetchval(
            """SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM session
               WHERE trainee_id = $1::uuid AND scenario_id = $2::uuid""",
            trainee_id, scenario_id,
        )
        thr = await c.fetchval(
            "SELECT id::text FROM threshold_profile WHERE is_default LIMIT 1"
        )
        row = await c.fetchrow(
            """INSERT INTO session
                   (trainee_id, instructor_id, scenario_id, threshold_id, mode, attempt_no)
               VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6)
               RETURNING id::text, attempt_no, started_at""",
            trainee_id, instructor_id, scenario_id, thr, mode, attempt,
        )
    return dict(row)


async def finish_session(session_id: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            "UPDATE session SET ended_at = now() WHERE id = $1::uuid AND ended_at IS NULL",
            session_id,
        )


# ---------------------------------------------------------------- события

async def log_event(session_id: str, t_ms: int, kind: str, payload: dict[str, Any] | None = None) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO session_event (session_id, t_ms, kind, payload)
               VALUES ($1::uuid, $2, $3::event_kind, $4::jsonb)""",
            session_id, t_ms, kind, json.dumps(payload or {}, ensure_ascii=False),
        )


async def events(session_id: str) -> list[dict]:
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT t_ms, kind::text, payload FROM session_event
               WHERE session_id = $1::uuid ORDER BY t_ms, id""",
            session_id,
        )
    out = []
    for r in rows:
        p = r["payload"]
        out.append({
            "t_ms": r["t_ms"],
            "kind": r["kind"],
            "payload": json.loads(p) if isinstance(p, str) else p,
        })
    return out


# ---------------------------------------------------------------- оценка

async def save_score(session_id: str, threshold_id: str, sc: dict) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO session_score
                   (session_id, threshold_id, protocol_score, speed_score,
                    composure_score, total_score, stop_factors, passed, breakdown)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb)
               ON CONFLICT (session_id) DO UPDATE SET
                   protocol_score = EXCLUDED.protocol_score,
                   speed_score = EXCLUDED.speed_score,
                   composure_score = EXCLUDED.composure_score,
                   total_score = EXCLUDED.total_score,
                   stop_factors = EXCLUDED.stop_factors,
                   passed = EXCLUDED.passed,
                   breakdown = EXCLUDED.breakdown,
                   computed_at = now()""",
            session_id, threshold_id,
            sc["protocol"], sc["speed"], sc["composure"], sc["total"],
            sc["stop_factors"], sc["passed"],
            json.dumps(sc["breakdown"], ensure_ascii=False),
        )


async def session_threshold(session_id: str) -> tuple[str, dict]:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT tp.id::text AS id, tp.values
               FROM session s JOIN threshold_profile tp ON tp.id = s.threshold_id
               WHERE s.id = $1::uuid""",
            session_id,
        )
    v = row["values"]
    return row["id"], (json.loads(v) if isinstance(v, str) else v)


async def session_meta(session_id: str) -> dict:
    """Данные для шапки протокола: кто, когда, какая попытка."""
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT u.full_name AS trainee, s.attempt_no, s.started_at,
                      i.full_name AS instructor
               FROM session s
               JOIN app_user u ON u.id = s.trainee_id
               LEFT JOIN app_user i ON i.id = s.instructor_id
               WHERE s.id = $1::uuid""",
            session_id,
        )
    if not row:
        return {}
    return {
        "trainee": row["trainee"],
        "attempt": row["attempt_no"],
        "instructor": row["instructor"] or "—",
        "date": row["started_at"].strftime("%d.%m.%Y") if row["started_at"] else "",
    }


# ---------------------------------------------------------------- аналитика

ANALYTICS_SQL = """
SELECT
    s.id::text                       AS session_id,
    u.full_name                      AS trainee,
    COALESCE(g.name, '—')            AS group_name,
    sc.slug                          AS scenario_slug,
    sc.title                         AS scenario_title,
    sc.difficulty                    AS difficulty,
    s.attempt_no                     AS attempt,
    s.mode                           AS mode,
    s.started_at                     AS started_at,
    t.ask_address_ms, t.got_address_ms, t.ask_victims_ms, t.dispatch_ms,
    e.info_efficiency, e.operator_pauses, e.forbidden_count, e.injections_count,
    sco.protocol_score, sco.speed_score, sco.composure_score,
    sco.total_score, sco.stop_factors, sco.passed, sco.breakdown
FROM session s
JOIN app_user u          ON u.id = s.trainee_id
LEFT JOIN org_group g    ON g.id = u.group_id
JOIN scenario sc         ON sc.id = s.scenario_id
LEFT JOIN v_session_timing t     ON t.session_id = s.id
LEFT JOIN v_session_efficiency e ON e.session_id = s.id
LEFT JOIN session_score sco      ON sco.session_id = s.id
WHERE sco.session_id IS NOT NULL
ORDER BY s.started_at DESC
LIMIT 1000
"""


async def analytics_rows() -> list[dict]:
    async with pool().acquire() as c:
        rows = await c.fetch(ANALYTICS_SQL)
    out = []
    for r in rows:
        d = dict(r)
        d["started_at"] = d["started_at"].isoformat() if d["started_at"] else None
        b = d.get("breakdown")
        d["breakdown"] = json.loads(b) if isinstance(b, str) else (b or {})
        for k in ("protocol_score", "speed_score", "composure_score", "total_score",
                  "info_efficiency"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        out.append(d)
    return out
