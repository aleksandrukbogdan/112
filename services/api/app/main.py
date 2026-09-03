"""
API тренажёра оператора 112.

Устроен так, чтобы каждый слой проверялся отдельно:
  1. /health          — что вообще живо
  2. POST /api/sessions            — начать вызов
  3. WS /ws/session/{id}           — диалог текстом (работает БЕЗ ASR и TTS)
  4. POST /api/sessions/{id}/utterance — то же самое, но аудио
  5. POST /api/sessions/{id}/finish    — оценка
  6. GET  /api/sessions/{id}/report    — разбор
  7. GET  /api/analytics               — данные для панели

Текстовый режим — не заглушка, а осознанное решение: он позволяет отладить
всю логику сценария и скоринга, пока речевые модели ещё не подключены,
и остаётся аварийным режимом на демо.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from . import db, intents as intent_mod, scoring
from .config import settings
from .llm import health as llm_health
from .scenario_engine import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


# ====================================================================== запуск

@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(30):
        try:
            await db.init()
            break
        except Exception as e:  # noqa: BLE001
            log.warning("БД ещё не готова (%s/30): %s", attempt + 1, e)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("Не удалось подключиться к БД")

    await load_scenarios()
    yield
    await db.close()


app = FastAPI(title="Тренажёр 112 — API", lifespan=lifespan)

# Режимы «обучение» и «поддержка» на общем движке протокола.
# Вся нормативка — в config/, код от неё не зависит.
from .routes_v2 import router as router_v2
app.include_router(router_v2)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)


async def load_scenarios() -> None:
    """Заливает JSON-сценарии из каталога в БД при старте."""
    d = Path(settings.scenarios_dir)
    if not d.exists():
        log.warning("Каталог сценариев не найден: %s", d)
        return
    for f in sorted(d.glob("*.json")):
        try:
            sc = json.loads(f.read_text(encoding="utf-8"))
            await db.upsert_scenario(sc)
            log.info("Сценарий загружен: %s", sc["slug"])
        except Exception as e:  # noqa: BLE001
            log.error("Не удалось загрузить %s: %s", f.name, e)


# ====================================================================== здоровье

@app.get("/health")
async def health():
    from . import session_store as _st
    from . import config_loader as _cfg
    checks = {"api": True, "db": await db.healthy(), "redis": await _st.zdorov()}
    try:
        checks["config"] = _cfg.svodka()
    except Exception as e:  # noqa: BLE001
        checks["config"] = {"ok": False, "error": str(e)}
    llm = await llm_health() if settings.llm_enabled else {"ok": None, "note": "выключен"}
    checks["llm"] = llm

    async with httpx.AsyncClient(timeout=4.0) as c:
        for name, url, on in (
            ("asr", f"{settings.asr_url}/health", settings.asr_enabled),
            ("tts", f"{settings.tts_url}/health", settings.tts_enabled),
        ):
            if not on:
                checks[name] = {"ok": None, "note": "выключен"}
                continue
            try:
                r = await c.get(url)
                checks[name] = r.json()
            except Exception as e:  # noqa: BLE001
                checks[name] = {"ok": False, "error": str(e)}

    # Redis критичен: без него не создаётся ни одна сессия
    core_ok = bool(checks["db"]) and bool(checks["redis"])
    return {"ok": core_ok, "checks": checks}


# ====================================================================== сценарии

@app.get("/api/scenarios")
async def scenarios():
    return await db.list_scenarios()


# ====================================================================== сессия

class StartSession(BaseModel):
    trainee_id: str
    scenario_slug: str
    instructor_id: str | None = None
    mode: str = "training"


@app.post("/api/sessions")
async def start_session(body: StartSession):
    sc = await db.get_scenario(body.scenario_slug)
    if not sc:
        raise HTTPException(404, f"Сценарий не найден: {body.scenario_slug}")

    row = await db.create_session(body.trainee_id, sc["_id"], body.mode, body.instructor_id)
    sid = row["id"]

    st = engine.start(sid, sc)
    await db.log_event(sid, 0, "call_start", {"scenario": sc["slug"]})

    opening = sc.get("opening_line", {"text": "Алло!", "emotion": "panic"})
    await db.log_event(sid, st.t_ms(), "caller_utterance",
                       {**opening, "source": "scenario", "node": "opening"})

    return {
        "session_id": sid,
        "attempt_no": row["attempt_no"],
        "scenario": {
            "slug": sc["slug"], "title": sc["title"],
            "difficulty": sc.get("difficulty"),
            "injections": sc.get("injections", []),
            "timing": sc.get("timing", {}),
            "checklist": sc.get("checklist", []),
        },
        "opening": opening,
    }


# ====================================================================== ядро диалога

async def handle_operator_text(
    sid: str, text: str, duration_ms: int | None = None, words: list | None = None
) -> dict:
    """
    Один ход диалога. Общая точка для текстового и голосового входа —
    поэтому логика сценария и оценки всегда одна и та же.
    """
    st = engine.get(sid)
    if st is None:
        raise HTTPException(409, "Сессия не активна — начните вызов заново")
    if st.ended:
        raise HTTPException(409, "Вызов уже завершён")

    t = st.t_ms()
    sc = st.scenario

    fillers = intent_mod.count_fillers(text)
    wpm = intent_mod.speech_rate_wpm(text, duration_ms)
    await db.log_event(sid, t, "operator_utterance", {
        "text": text, "fillers": fillers, "wpm": wpm,
        "duration_ms": duration_ms, "words": words or [],
    })

    # пауза на своём ходу
    if duration_ms is None and st.operator_turns:
        pass  # в текстовом режиме паузы не измеряем

    # запрещённые формулировки
    for f in intent_mod.forbidden_hits(text, sc.get("forbidden", [])):
        await db.log_event(sid, t, "forbidden_phrase", f)

    # классификация
    cls = await intent_mod.classify(text, sc)
    min_conf = 0.7
    accepted = cls["confidence"] >= min_conf

    for i in cls["intents"]:
        await db.log_event(sid, t, "intent_detected", {
            "intent": i, "confidence": cls["confidence"],
            "method": cls["method"], "accepted": accepted,
        })

    revealed: list[dict] = []
    if accepted:
        revealed = engine.apply_intents(st, cls["intents"], t)
    revealed += engine.spontaneous(st, t)

    for fact in revealed:
        await db.log_event(sid, st.t_ms(), "fact_revealed", {
            "fact_id": fact["id"], "field": fact.get("field"),
            "weight": fact.get("weight", 0),
        })

    # ответ заявителя
    if not accepted and not revealed:
        reply = {
            "text": "Что? Я не поняла... повторите, пожалуйста!",
            "emotion": "panic", "source": "low_confidence", "fact_ids": [],
        }
    else:
        reply = engine.caller_line(st, revealed)

    await db.log_event(sid, st.t_ms(), "caller_utterance", reply)

    audio_url = None
    if settings.tts_enabled:
        audio_url = "/api/tts?" + urlencode(
            {"text": reply["text"], "emotion": reply["emotion"]}
        )

    return {
        "type": "caller_utterance",
        "recognized": text,      # что услышала система — показываем оператору
        "caller": reply,
        "audio_url": audio_url,
        "intents": cls,
        "revealed": [f["id"] for f in revealed],
        "metrics": engine.live_metrics(st),
    }


class TextTurn(BaseModel):
    text: str
    duration_ms: int | None = None


@app.post("/api/sessions/{sid}/say")
async def say(sid: str, body: TextTurn):
    """Текстовый ход. Именно им проверяется вся логика без микрофона."""
    return await handle_operator_text(sid, body.text, body.duration_ms)


@app.post("/api/sessions/{sid}/utterance")
async def utterance(
    sid: str,
    file: UploadFile = File(...),
    duration_ms: int | None = None,
    silence_ms: int | None = None,
):
    """
    Голосовой ход: аудио -> ASR -> та же логика, что и в текстовом режиме.

    silence_ms — сколько тишины было перед репликой. Клиент измеряет это точнее,
    чем можно восстановить постфактум, а без него ось «манера» всегда идеальна.
    """
    if not settings.asr_enabled:
        raise HTTPException(503, "ASR выключен (ASR_ENABLED=false)")

    st = engine.get(sid)
    if st is None:
        raise HTTPException(409, "Сессия не активна")

    raw = await file.read()
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f"{settings.asr_url}/transcribe",
                         files={"file": ("chunk.wav", raw, "audio/wav")})
        r.raise_for_status()
        asr = r.json()

    text = (asr.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "Речь не распознана")

    # Пауза засчитывается только когда очередь оператора: если в этот момент
    # говорил заявитель или шла инъекция, это не растерянность оператора.
    if silence_ms and silence_ms > 2000:
        await db.log_event(sid, st.t_ms(), "pause_detected", {
            "duration_sec": round(silence_ms / 1000.0, 2),
            "is_operator_turn": True,
        })

    words = asr.get("words") or []
    dur = duration_ms or (int(words[-1]["end"] * 1000) if words else None)

    # эмоциональная окраска реплики оператора — вспомогательный сигнал
    if settings.asr_enabled:
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                e = await c.post(f"{settings.asr_url}/emotion",
                                 files={"file": ("chunk.wav", raw, "audio/wav")})
                if e.status_code == 200:
                    await db.log_event(sid, st.t_ms(), "operator_utterance",
                                       {"text": text, "emotion_probs": e.json().get("probs"),
                                        "acoustic_only": True})
        except Exception as exc:  # noqa: BLE001
            log.debug("Эмоции не посчитаны: %s", exc)

    return await handle_operator_text(sid, text, dur, words)


# ====================================================================== карточка и отправка

class CardField(BaseModel):
    field: str
    value: str


@app.post("/api/sessions/{sid}/card")
async def card(sid: str, body: CardField):
    st = engine.get(sid)
    if st is None:
        raise HTTPException(409, "Сессия не активна")
    ok = engine.set_card_field(st, body.field, body.value)
    await db.log_event(sid, st.t_ms(), "card_field_filled", {
        "field": body.field, "value": body.value, "is_correct": ok,
    })
    return {"field": body.field, "is_correct": ok, "metrics": engine.live_metrics(st)}


class Dispatch(BaseModel):
    services: list[str]


@app.post("/api/sessions/{sid}/dispatch")
async def dispatch(sid: str, body: Dispatch):
    st = engine.get(sid)
    if st is None:
        raise HTTPException(409, "Сессия не активна")
    t = st.t_ms()
    ok = engine.dispatch(st, body.services, t)
    for s in body.services:
        await db.log_event(sid, t, "service_selected", {"service": s})
    await db.log_event(sid, t, "card_dispatched", {
        "services": body.services, "is_correct": ok,
        "expected": st.scenario.get("services", []),
    })
    return {"is_correct": ok, "metrics": engine.live_metrics(st)}


class Injection(BaseModel):
    injection_id: str


@app.post("/api/sessions/{sid}/inject")
async def inject(sid: str, body: Injection):
    st = engine.get(sid)
    if st is None:
        raise HTTPException(409, "Сессия не активна")
    spec = engine.inject(st, body.injection_id)
    if not spec:
        raise HTTPException(404, f"Инъекция не найдена: {body.injection_id}")
    await db.log_event(sid, st.t_ms(), "injection_applied", {
        "injection": body.injection_id, "label": spec.get("label"),
    })
    if st.dropped:
        await db.log_event(sid, st.t_ms(), "line_dropped", {})
    return {"applied": spec, "metrics": engine.live_metrics(st)}


# ====================================================================== завершение и разбор

@app.post("/api/sessions/{sid}/finish")
async def finish(sid: str):
    st = engine.get(sid)
    if st is not None:
        await db.log_event(sid, st.t_ms(), "call_end", {"ended_by": "operator"})
        st.ended = True

    await db.finish_session(sid)

    ev = await db.events(sid)
    slug = next((e["payload"].get("scenario") for e in ev if e["kind"] == "call_start"), None)
    sc = await db.get_scenario(slug) if slug else None
    if not sc:
        raise HTTPException(500, "Сценарий сессии не найден")

    thr_id, thr = await db.session_threshold(sid)
    result = scoring.compute(ev, sc, thr)
    await db.save_score(sid, thr_id, result)
    engine.drop(sid)
    return result


@app.get("/api/sessions/{sid}/report.pdf")
async def report_pdf_file(sid: str):
    """Протокол разбора в PDF — то, что подшивается в дело аттестации."""
    from . import report_pdf

    ev = await db.events(sid)
    if not ev:
        raise HTTPException(404, "События сессии не найдены")
    slug = next((e["payload"].get("scenario") for e in ev if e["kind"] == "call_start"), None)
    sc = await db.get_scenario(slug) if slug else None
    if not sc:
        raise HTTPException(500, "Сценарий сессии не найден")

    thr_id, thr = await db.session_threshold(sid)
    payload = {
        "session_id": sid,
        "scenario": {"slug": sc["slug"], "title": sc["title"]},
        "timeline": ev,
        "score": scoring.compute(ev, sc, thr),
    }

    try:
        pdf = report_pdf.build(payload, await db.session_meta(sid))
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="protokol-{sid[:8]}.pdf"'},
    )


@app.get("/api/sessions/{sid}/report")
async def report(sid: str):
    ev = await db.events(sid)
    if not ev:
        raise HTTPException(404, "События сессии не найдены")
    slug = next((e["payload"].get("scenario") for e in ev if e["kind"] == "call_start"), None)
    sc = await db.get_scenario(slug) if slug else None
    thr_id, thr = await db.session_threshold(sid)
    return {
        "session_id": sid,
        "scenario": {"slug": sc["slug"], "title": sc["title"]} if sc else None,
        "timeline": ev,
        "score": scoring.compute(ev, sc, thr) if sc else None,
    }


# ====================================================================== TTS-прокси

@app.get("/api/tts")
async def tts(text: str = "", emotion: str = "neutral"):
    if not settings.tts_enabled:
        raise HTTPException(503, "TTS выключен (TTS_ENABLED=false)")
    body = text
    async with httpx.AsyncClient(timeout=90.0) as c:
        r = await c.post(f"{settings.tts_url}/synth", json={
            "text": body, "emotion": emotion, "telephone": True, "cache": True,
        })
        r.raise_for_status()
    return Response(content=r.content, media_type="audio/wav",
                    headers={"Cache-Control": "public, max-age=86400"})


# ====================================================================== аналитика

@app.get("/api/analytics")
async def analytics():
    return {"rows": await db.analytics_rows()}


# ====================================================================== WebSocket

class Hub:
    """Комнаты по session_id: обучаемый и преподаватель видят одно и то же."""

    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = {}

    async def join(self, sid: str, ws: WebSocket) -> None:
        await ws.accept()
        self.rooms.setdefault(sid, set()).add(ws)

    def leave(self, sid: str, ws: WebSocket) -> None:
        self.rooms.get(sid, set()).discard(ws)

    async def broadcast(self, sid: str, msg: dict) -> None:
        dead = []
        for ws in list(self.rooms.get(sid, set())):
            try:
                await ws.send_json(msg)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.leave(sid, ws)


hub = Hub()


@app.websocket("/ws/session/{sid}")
async def ws_session(ws: WebSocket, sid: str):
    await hub.join(sid, ws)
    try:
        st = engine.get(sid)
        if st:
            await ws.send_json({"type": "metrics", "metrics": engine.live_metrics(st)})

        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")

            if kind == "operator_utterance":
                out = await handle_operator_text(
                    sid, msg.get("text", ""), msg.get("duration_ms")
                )
                await hub.broadcast(sid, out)

            elif kind == "card_field":
                st = engine.get(sid)
                if st:
                    ok = engine.set_card_field(st, msg["field"], msg["value"])
                    await db.log_event(sid, st.t_ms(), "card_field_filled", {
                        "field": msg["field"], "value": msg["value"], "is_correct": ok,
                    })
                    await hub.broadcast(sid, {
                        "type": "card_field", "field": msg["field"], "is_correct": ok,
                        "metrics": engine.live_metrics(st),
                    })

            elif kind == "injection":
                st = engine.get(sid)
                if st:
                    spec = engine.inject(st, msg["injection_id"])
                    if spec:
                        await db.log_event(sid, st.t_ms(), "injection_applied", {
                            "injection": msg["injection_id"], "label": spec.get("label"),
                        })
                        await hub.broadcast(sid, {
                            "type": "injection", "injection": spec,
                            "metrics": engine.live_metrics(st),
                        })

            elif kind == "override_intent":
                # страховка: преподаватель засчитывает вопрос вручную
                st = engine.get(sid)
                if st:
                    t = st.t_ms()
                    await db.log_event(sid, t, "intent_detected", {
                        "intent": msg["intent"], "confidence": 1.0,
                        "method": "manual", "accepted": True, "overridden": True,
                    })
                    revealed = engine.apply_intents(st, [msg["intent"]], t)
                    for f in revealed:
                        await db.log_event(sid, st.t_ms(), "fact_revealed", {
                            "fact_id": f["id"], "field": f.get("field"),
                            "weight": f.get("weight", 0),
                        })
                    reply = engine.caller_line(st, revealed)
                    await db.log_event(sid, st.t_ms(), "caller_utterance", reply)
                    await hub.broadcast(sid, {
                        "type": "caller_utterance", "caller": reply,
                        "revealed": [f["id"] for f in revealed],
                        "metrics": engine.live_metrics(st),
                    })

            elif kind == "ping":
                st = engine.get(sid)
                await ws.send_json({
                    "type": "metrics",
                    "metrics": engine.live_metrics(st) if st else None,
                })

    except WebSocketDisconnect:
        hub.leave(sid, ws)
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка в WebSocket: %s", e)
        hub.leave(sid, ws)
