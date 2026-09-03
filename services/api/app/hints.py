"""
Журнал подсказок.

Каждая показанная подсказка и реакция оператора записываются.

Это не техническая прихоть. В мае 2026 пожарное управление Сиэтла,
применявшее ИИ-подсказки диспетчерам с декабря 2023, не смогло
объяснить журналистам, как измеряет успешность технологии.
Если через два года спросят, как система влияла на решения, —
ответ должен быть в базе, а не в памяти.
"""
from __future__ import annotations
import json
from typing import Any


async def zapisat(conn, session_id: str, t_ms: int, hints: list[Any]) -> None:
    for h in hints:
        d = h.to_dict() if hasattr(h, "to_dict") else dict(h)
        await conn.execute(
            """INSERT INTO hint_log (session_id, t_ms, hint_id, kind, payload)
               VALUES ($1,$2,$3,$4,$5)""",
            session_id, t_ms, d["id"], d["kind"], json.dumps(d, ensure_ascii=False))


async def reakciya(conn, session_id: str, hint_id: str, reaction: str) -> None:
    """reaction: accepted | dismissed | ignored"""
    await conn.execute(
        """UPDATE hint_log SET reaction=$3, reacted_at=now()
           WHERE session_id=$1 AND hint_id=$2 AND reaction IS NULL""",
        session_id, hint_id, reaction)


async def statistika(conn, session_id: str) -> dict:
    rows = await conn.fetch(
        """SELECT kind, reaction, COUNT(*) n FROM hint_log
           WHERE session_id=$1 GROUP BY 1,2""", session_id)
    out: dict = {"vsego": 0, "po_vidam": {}, "po_reakcii": {}}
    for r in rows:
        out["vsego"] += r["n"]
        out["po_vidam"][r["kind"]] = out["po_vidam"].get(r["kind"], 0) + r["n"]
        k = r["reaction"] or "ignored"
        out["po_reakcii"][k] = out["po_reakcii"].get(k, 0) + r["n"]
    return out
