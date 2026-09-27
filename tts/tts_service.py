# -*- coding: utf-8 -*-
"""
Голосовой контракт тренажёра: GET /tts?text=... -> audio/wav.

Модель держится в VRAM. Запросы идут по одному, без отмены уже начатой фразы:
оператор должен дослушать ответ заявителя.
"""
from __future__ import annotations

import os
import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from .scene import ScenePlan, classify

NFE_STEP = int(os.environ.get("F5_NFE_STEP", "12"))
_GEN_ID: int | None = None
_GEN_LOCK = threading.Lock()


def _stable_gen_id() -> int:
    """Один id на процесс, чтобы стендовый сброс устаревших запросов не обрывал фразу."""
    global _GEN_ID
    with _GEN_LOCK:
        if _GEN_ID is None:
            from .f5_engine import register_new_generation
            _GEN_ID = register_new_generation()
        return _GEN_ID


def render_reply(text: str, keep: bool = False) -> tuple[bytes, dict]:
    plan: ScenePlan = classify(text)
    from .f5_engine import OUTPUTS_DIR, synthesize_f5_fast

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"t112_{uuid.uuid4().hex}.wav"
    meta = synthesize_f5_fast(
        plan.text,
        voice=plan.voice,
        nfe_step=NFE_STEP,
        telephone=True,
        sound_track=plan.sound_track,
        sound_volume=plan.sound_volume if plan.sound_track else 0.30,
        output_name=name,
        gen_id=_stable_gen_id(),
    )
    if not meta or meta.get("cancelled") or meta.get("status") == "cancelled":
        raise RuntimeError(meta.get("error") if isinstance(meta, dict) else "синтез отменён")
    path = OUTPUTS_DIR / meta["file_name"]
    data = path.read_bytes()
    meta["scene"] = plan.scene
    meta["inherited"] = plan.inherited
    meta["official"] = plan.official
    meta["planned_voice"] = plan.voice
    meta["planned_track"] = plan.sound_track
    if not keep:
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
    return data, meta


app = FastAPI(title="t112-f5")


@app.on_event("startup")
def startup():
    def warm():
        try:
            from .f5_engine import get_cached_ref, get_f5_resources, get_voices_catalog
            from .scene import reset_scene
            from .sound_manager import init_sound_cache
            get_f5_resources()
            init_sound_cache()
            catalog = get_voices_catalog()
            for voice_id in (
                "female_panic", "female_warm", "female_deep", "female_elderly",
                "male_baritone", "male_deep", "male_panic",
            ):
                if voice_id in catalog:
                    get_cached_ref(voice_id, catalog)
            _stable_gen_id()
            render_reply("Алло.", keep=False)
            reset_scene()
        except Exception as exc:
            print(f"[t112-f5] прогрев не удался: {exc}")

    threading.Thread(target=warm, daemon=True).start()


@app.get("/health")
def health():
    from .f5_engine import CKPT_FILE, VOCAB_FILE, _DEVICE
    ready = CKPT_FILE.exists() and VOCAB_FILE.exists()
    return {
        "ok": ready,
        "tts": ready,
        "engine": "f5",
        "device": _DEVICE,
        "nfe_step": NFE_STEP,
        "ckpt": str(CKPT_FILE),
    }


@app.get("/tts")
def tts(text: str):
    text = (text or "").strip()[:600]
    if not text:
        raise HTTPException(400, "пустой текст")
    try:
        data, _meta = render_reply(text, keep=False)
    except Exception as exc:
        raise HTTPException(500, f"синтез не удался: {exc}") from exc
    if len(data) < 44 or data[:4] != b"RIFF":
        raise HTTPException(500, "синтез вернул не wav")
    return Response(data, media_type="audio/wav")
