#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Высокопроизводительный резидентный движок F5-TTS_RUSSIAN (f5_engine.py)
для мгновенного субсекундного инференса на GPU (NVIDIA RTX 5060 Ti).

Особенности:
- Резидентное хранение DiT и Vocos в VRAM GPU (без перезагрузки моделей).
- Поддержка отмены устаревших генераций (Single-Flight Lock & Request ID):
  при новом запросе все предыдущие автоматически отбрасываются.
- Исключительная блокировка GPU (_INFERENCE_LOCK) — защита от наложения параллельных расчетов.
- Точное по сэмплам смешивание фонов катастрофы (исключает broadcast shape mismatch).
- Автоматическая очистка кэша CUDA (torch.cuda.empty_cache()).
"""

import io
import os
import re
import sys
import time
import json
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List

import torch
import numpy as np
from scipy import signal
from scipy.signal import butter, sosfilt
import soundfile as sf
import torchaudio

# Безопасный загрузчик torchaudio для Windows (обход torchcodec)
def safe_torchaudio_load(filepath, *a, **kw):
    data, sr = sf.read(filepath)
    if data.ndim == 1:
        tensor = torch.from_numpy(data.astype(np.float32)).unsqueeze(0)
    else:
        tensor = torch.from_numpy(data.mean(axis=1).astype(np.float32)).unsqueeze(0)
    return tensor, sr

torchaudio.load = safe_torchaudio_load

STAND_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("HAKATON_DIR", str(STAND_DIR.parent)))
OUTPUTS_DIR = Path(os.environ.get("F5_OUTPUT_DIR", str(STAND_DIR / "outputs")))
VOICES_DIR = Path(os.environ.get("VOICES_DIR", str(STAND_DIR / "voices")))
MODELS_DIR = Path(os.environ.get("F5_MODEL_DIR", str(BASE_DIR / "models" / "F5-TTS_RUSSIAN")))
HF_CACHE = Path(os.environ.get("HF_HOME", str(BASE_DIR / "hf_cache")))

CKPT_FILE = Path(os.environ.get(
    "F5_CKPT",
    str(MODELS_DIR / "F5TTS_v1_Base_v4_winter" / "model_212000.safetensors"),
))
VOCAB_FILE = Path(os.environ.get(
    "F5_VOCAB",
    str(MODELS_DIR / "F5TTS_v1_Base" / "vocab.txt"),
))

os.environ.setdefault("HF_HOME", str(HF_CACHE))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE))

from f5_tts.model import DiT
from f5_tts.infer.utils_infer import (
    load_vocoder,
    load_model,
    preprocess_ref_audio_text,
    infer_process,
)

# Синглтон модели F5-TTS и блокировка инференса
_MODEL_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_GLOBAL_F5_MODEL = None
_GLOBAL_F5_VOCODER = None
_REF_CACHE: Dict[str, Tuple[Any, str]] = {}
_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Управление отменой и одиночным потоком генерации
_ACTIVE_GENERATION_ID = 0
_GEN_ID_LOCK = threading.Lock()


def register_new_generation() -> int:
    """Регистрирует новый запрос на генерацию и инвалидирует все предыдущие."""
    global _ACTIVE_GENERATION_ID
    with _GEN_ID_LOCK:
        _ACTIVE_GENERATION_ID += 1
        return _ACTIVE_GENERATION_ID


def is_generation_active(gen_id: int) -> bool:
    """Проверяет, является ли запрос gen_id всё ещё актуальным."""
    with _GEN_ID_LOCK:
        return gen_id == _ACTIVE_GENERATION_ID


def cancel_all_generations():
    """Сбрасывает текущий ID генерации, отменяя все активные/ожидающие задачи."""
    global _ACTIVE_GENERATION_ID
    with _GEN_ID_LOCK:
        _ACTIVE_GENERATION_ID += 1
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def get_f5_resources():
    """Загружает модель F5-TTS DiT и вокодер Vocos в VRAM GPU (один раз на весь срок работы сервера)."""
    global _GLOBAL_F5_MODEL, _GLOBAL_F5_VOCODER
    if _GLOBAL_F5_MODEL is None or _GLOBAL_F5_VOCODER is None:
        with _MODEL_LOCK:
            if _GLOBAL_F5_MODEL is None or _GLOBAL_F5_VOCODER is None:
                t0 = time.time()
                print(f"[F5Engine] Загрузка F5-TTS DiT и Vocos в VRAM {_DEVICE}...")
                vocoder = load_vocoder(vocoder_name="vocos", is_local=False)
                model_cfg = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
                model = load_model(
                    model_cls=DiT,
                    model_cfg=model_cfg,
                    ckpt_path=str(CKPT_FILE),
                    mel_spec_type="vocos",
                    vocab_file=str(VOCAB_FILE),
                    device=_DEVICE,
                )
                _GLOBAL_F5_VOCODER = vocoder
                _GLOBAL_F5_MODEL = model
                print(f"[F5Engine] Готово: F5-TTS загружена в VRAM за {time.time() - t0:.2f} сек. Теперь инференс будет мгновенным!")
    return _GLOBAL_F5_MODEL, _GLOBAL_F5_VOCODER


def get_voices_catalog() -> Dict[str, Any]:
    """Каталог эталонов. Wav берутся из VOICES_DIR, json — оттуда же или из образа."""
    catalog = {}
    baked = Path(__file__).resolve().parent / "voices_f5.json"
    voices_json = VOICES_DIR / "voices.json"
    if not voices_json.exists():
        voices_json = baked
    if voices_json.exists():
        try:
            with open(voices_json, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as e:
            print(f"[F5Engine] Ошибка чтения {voices_json}: {e}")
    return catalog


def get_cached_ref(voice_id: str, voices_catalog: Dict[str, Any]):
    """Возвращает кэшированный обработанный эталонный аудиофрагмент и текст."""
    if voice_id in _REF_CACHE:
        return _REF_CACHE[voice_id]

    ref_audio_path = None
    ref_text = "Здравствуйте, слушаю вас."

    if voice_id in voices_catalog:
        vinfo = voices_catalog[voice_id]
        ref_audio_path = str(VOICES_DIR / vinfo["wav"])
        ref_text = vinfo.get("text", ref_text)
    elif Path(voice_id).exists():
        ref_audio_path = voice_id
        ref_text = "Здравствуйте, я свидетель происшествия."
    else:
        candidates = list(VOICES_DIR.glob("*.wav"))
        if candidates:
            ref_audio_path = str(candidates[0])
            ref_text = "Здравствуйте, слушаю вас."

    if ref_audio_path and Path(ref_audio_path).exists():
        from .ref_fit import fit_ref_text
        try:
            ref_text = fit_ref_text(ref_text, float(sf.info(ref_audio_path).duration))
        except Exception:
            ref_text = fit_ref_text(ref_text, 12.0)
        ref_audio, ref_text_processed = preprocess_ref_audio_text(ref_audio_path, ref_text)
        _REF_CACHE[voice_id] = (ref_audio, ref_text_processed)
        return ref_audio, ref_text_processed

    raise FileNotFoundError(f"Не найден эталонный голос для {voice_id}")


_VOICE_IO = threading.Lock()
_VOICE_ID = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def invalidate_voice(voice_id: str) -> None:
    _REF_CACHE.pop(voice_id, None)


def _catalog_path() -> Path:
    return VOICES_DIR / "voices.json"


def _read_catalog_locked() -> tuple[Path, dict]:
    """Каталог на диске. Если файла ещё нет — копия семи встроенных, чтобы правка не исчезла."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    dest = _catalog_path()
    if not dest.exists():
        baked = Path(__file__).resolve().parent / "voices_f5.json"
        catalog = json.loads(baked.read_text(encoding="utf-8")) if baked.exists() else {}
        dest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        catalog = json.loads(dest.read_text(encoding="utf-8"))
    return dest, catalog


def rename_voice(voice_id: str, voice_name: str) -> Dict[str, Any]:
    if not _VOICE_ID.match(voice_id or ""):
        raise ValueError("неизвестный голос")
    title = re.sub(r"\s+", " ", (voice_name or "")).strip()[:80]
    if len(title) < 1:
        raise ValueError("пустое имя")
    with _VOICE_IO:
        dest, catalog = _read_catalog_locked()
        if voice_id not in catalog:
            raise ValueError("такого голоса нет")
        catalog[voice_id]["name"] = title
        dest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        invalidate_voice(voice_id)
    return {"voice_id": voice_id, "voice_name": title}


def delete_voice(voice_id: str) -> None:
    if not _VOICE_ID.match(voice_id or ""):
        raise ValueError("неизвестный голос")
    with _VOICE_IO:
        dest, catalog = _read_catalog_locked()
        info = catalog.get(voice_id)
        if not info:
            raise ValueError("такого голоса нет")
        wav = info.get("wav") or ""
        del catalog[voice_id]
        dest.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        invalidate_voice(voice_id)
        if wav and not any(x in wav for x in ("/", "\\", "..")):
            path = (VOICES_DIR / wav).resolve()
            if path.parent == VOICES_DIR.resolve() and path.is_file():
                path.unlink()


def voice_wav_path(voice_id: str) -> Optional[Path]:
    if not _VOICE_ID.match(voice_id or ""):
        return None
    info = get_voices_catalog().get(voice_id) or {}
    wav = info.get("wav") or ""
    if not wav or any(x in wav for x in ("/", "\\", "..")):
        return None
    root = VOICES_DIR.resolve()
    path = (VOICES_DIR / wav).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def _decode_upload(raw: bytes, filename: str) -> Tuple[np.ndarray, int]:
    try:
        data, sr = sf.read(io.BytesIO(raw), dtype="float32")
        return np.asarray(data), int(sr)
    except Exception:
        pass
    suffix = Path(filename or "voice.bin").suffix.lower()
    if suffix not in (".wav", ".webm", ".ogg", ".mp3", ".m4a", ".flac", ".aac"):
        suffix = ".bin"
    src_path = dst_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
            src.write(raw)
            src_path = src.name
        dst_path = src_path + ".wav"
        run = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src_path, "-ar", "24000", "-ac", "1", "-f", "wav", dst_path],
            capture_output=True,
        )
        if run.returncode != 0 or not os.path.exists(dst_path):
            raise ValueError("не удалось прочитать аудио. Нужен wav, webm или mp3")
        data, sr = sf.read(dst_path, dtype="float32")
        return np.asarray(data), int(sr)
    finally:
        for p in (src_path, dst_path):
            if p and os.path.exists(p):
                os.remove(p)


def save_uploaded_voice(raw: bytes, filename: str, text: str, voice_name: str) -> Dict[str, Any]:
    """Кладёт эталон в каталог голосов. Если voices.json ещё нет — копирует семь встроенных."""
    spoken = re.sub(r"\s+", " ", (text or "")).strip()
    if len(spoken) < 8:
        raise ValueError("нужен текст эталона, хотя бы одна фраза")
    if not raw or len(raw) < 1000:
        raise ValueError("пустая запись")
    if len(raw) > 15 * 1024 * 1024:
        raise ValueError("файл больше 15 МБ")

    data, sr = _decode_upload(raw, filename)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = np.asarray(data, dtype=np.float32)
    if sr <= 0 or len(data) == 0:
        raise ValueError("пустая запись")
    duration = len(data) / sr
    if duration < 1.2:
        raise ValueError("запись короче 1,5 секунды. Произнесите фразу целиком, 3–5 секунд")
    if duration > 12.0:
        data = data[: int(12.0 * sr)]
        duration = 12.0
    if sr != 24000:
        new_len = max(1, int(len(data) * 24000 / sr))
        data = signal.resample(data, new_len).astype(np.float32)
        sr = 24000
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    if peak < 0.01:
        raise ValueError("в записи тишина")
    data = data / peak * 0.95

    voice_id = f"voice_{int(time.time())}"
    title = re.sub(r"\s+", " ", (voice_name or "")).strip()[:80] or "Свой голос"
    with _VOICE_IO:
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        dest_json = VOICES_DIR / "voices.json"
        if not dest_json.exists():
            baked = Path(__file__).resolve().parent / "voices_f5.json"
            catalog = json.loads(baked.read_text(encoding="utf-8")) if baked.exists() else {}
        else:
            catalog = json.loads(dest_json.read_text(encoding="utf-8"))
        wav_name = f"{voice_id}.wav"
        sf.write(str(VOICES_DIR / wav_name), data, sr, format="WAV")
        catalog[voice_id] = {
            "name": title,
            "text": spoken[:400],
            "wav": wav_name,
            "speaker_type": "custom",
        }
        dest_json.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        invalidate_voice(voice_id)
    return {
        "voice_id": voice_id,
        "voice_name": title,
        "duration_sec": round(duration, 2),
        "text": spoken[:400],
    }


def parse_f5_tags(text: str) -> Dict[str, Any]:
    """
    Извлекает хэштеги (#паника, #крик, #плач, #шок, #шепот, #агрессия и др.)
    и формирует полный набор параметров для F5-TTS и процедурного DSP-движка.
    """
    tags_found = []
    emotion = "neutral"
    breath_intro = False
    sob_intro = False
    pause_intro = False
    ambience = "none"
    speed = 1.0
    pitch_semitones = 0.0
    distress = 0.0
    tremolo = 0.0
    whisper_mode = False
    cfg_strength = 2.0
    suggested_voice = None

    # 1. Теги Boson / Higgs (<|...|>)
    higgs_tags = re.findall(r"<\|([^|>]+)\|>", text)
    sound_track = None
    for tag in higgs_tags:
        tags_found.append(f"<|{tag}|>")
        tl = tag.lower()
        if "child_cry" in tl or "baby_cry" in tl:
            sound_track = "child_cry"
        elif "fear" in tl or "screaming" in tl or "shouting" in tl:
            emotion = "panic"
            speed = max(speed, 1.25)
            pitch_semitones = max(pitch_semitones, 2.5)
            distress = max(distress, 0.75)
            tremolo = max(tremolo, 0.15)
            breath_intro = True
            cfg_strength = 2.6
            suggested_voice = "female_panic"
        elif "sadness" in tl or "crying" in tl:
            emotion = "cry"
            speed = min(speed, 0.92)
            pitch_semitones = 1.2
            distress = max(distress, 0.35)
            tremolo = max(tremolo, 0.28)
            sob_intro = True
            cfg_strength = 2.2
            suggested_voice = "female_panic"
        elif "sigh" in tl:
            breath_intro = True
        elif "whisper" in tl:
            emotion = "whisper"
            speed = 0.90
            pitch_semitones = -1.2
            whisper_mode = True
            suggested_voice = "female_deep"
        elif "anger" in tl:
            emotion = "aggressive"
            speed = max(speed, 1.18)
            pitch_semitones = -0.8
            distress = max(distress, 0.65)
            suggested_voice = "male_deep"

    # 2. Русские и английские хэштеги (#...)
    hashtags = re.findall(r"#([a-zA-Zа-яА-ЯёЁ_0-9]+)", text)
    for h in hashtags:
        hl = h.lower()
        tags_found.append(f"#{h}")

        # 0. Реальные звуковые дорожки из папки Sound (D:\EXP\HAKATON\Sound)
        if hl in ["плач_ребенка", "плач_ребёнка", "детский_плач", "ребенок_плачет", "плач_детей", "child_cry", "child_crying"]:
            sound_track = "child_crying"
            if emotion == "neutral":
                emotion = "panic"
                suggested_voice = "female_panic"

        elif hl in ["плач_женщины", "женский_плач", "плач_матери", "woman_cry", "woman_crying"]:
            sound_track = "woman_crying"
            emotion = "cry"
            suggested_voice = "female_panic"

        elif hl in ["одышка_звук", "тяжелое_дыхание", "тяжёлое_дыхание", "дыхание_звук"]:
            sound_track = "heavy_breathing"

        elif hl in ["кашель", "кашляет", "першение", "cough"]:
            sound_track = "cough_smoke"

        elif hl in ["взлом", "ломится", "удар_двери", "хлопок_двери", "дверь"]:
            sound_track = "door_slam"

        elif hl in ["датчик_дыма", "пожарная_тревога", "сигнализация", "сигнализация_дым", "smoke_alarm"]:
            sound_track = "smoke_detector"

        elif hl in ["удар_дтп", "звук_дтп", "звук_аварии", "столкновение", "crash"]:
            sound_track = "car_crash"

        elif hl in ["собака", "лай", "нападение_собаки", "пес", "пёс", "собаки", "dog"]:
            sound_track = "dog_bark"

        elif hl in ["фон_пожар", "огонь_звук", "звук_огня"]:
            sound_track = "fire_inferno"
            ambience = "fire"

        elif hl in ["фон_трасса", "фон_дтп", "звук_трассы"]:
            sound_track = "traffic_highway"
            ambience = "traffic"

        elif hl in ["фон_сирена", "звук_сирены"]:
            sound_track = "siren_distant"
            ambience = "siren"

        elif hl in ["река", "фон_река", "stream-river-water", "river"]:
            sound_track = "stream-river-water"

        elif hl.startswith("звук_") or hl.startswith("фон_"):
            cand_track = hl.replace("звук_", "").replace("фон_", "")
            if cand_track:
                sound_track = cand_track

        # 1. Паника, крик, острый страх
        elif hl in ["крик", "паника", "страх", "ужас", "scream", "panic", "fear"]:
            emotion = "panic"
            speed = max(speed, 1.25)
            pitch_semitones = 2.5
            distress = max(distress, 0.75)
            tremolo = max(tremolo, 0.15)
            breath_intro = True
            cfg_strength = 2.6
            suggested_voice = "female_panic"

        # 2. Плач, слёзы, рыдания
        elif hl in ["плач", "рыдания", "всхлип", "слезы", "слёзы", "cry", "sob"]:
            emotion = "cry"
            speed = min(speed, 0.92)
            pitch_semitones = 1.2
            distress = max(distress, 0.35)
            tremolo = max(tremolo, 0.28)
            sob_intro = True
            cfg_strength = 2.2
            suggested_voice = "female_panic"
            if not sound_track:
                sound_track = "woman_crying"

        # 3. Шок, оцепенение, ступор
        elif hl in ["шок", "оцепенение", "ступор", "shock"]:
            emotion = "shock"
            speed = 0.80
            pitch_semitones = -1.5
            tremolo = max(tremolo, 0.10)
            pause_intro = True
            cfg_strength = 1.8
            suggested_voice = "male_panic"

        # 4. Агрессия, гнев, истерика
        elif hl in ["агрессия", "гнев", "злость", "истерика", "anger", "aggressive"]:
            emotion = "aggressive"
            speed = max(speed, 1.18)
            pitch_semitones = -0.8
            distress = max(distress, 0.65)
            cfg_strength = 2.4
            suggested_voice = "male_deep"

        # 5. Одышка, задыхается, физиология
        elif hl in ["вдох", "одышка", "задыхается", "gasp", "breath"]:
            breath_intro = True
            if not sound_track and hl in ["одышка", "задыхается"]:
                sound_track = "heavy_breathing"
            if emotion == "neutral":
                emotion = "weakness"
                speed = 0.85
                suggested_voice = "female_elderly"

        # 6. Шёпот, скрытый звонок
        elif hl in ["шепот", "тихо", "whisper"]:
            emotion = "whisper"
            speed = 0.90
            pitch_semitones = -1.2
            whisper_mode = True
            suggested_voice = "female_deep"

        # 7. Роли и персоны
        elif hl in ["пожилой", "бабушка", "дедушка", "сердце", "старик"]:
            suggested_voice = "female_elderly"
            speed = 0.85
        elif hl in ["мужчина", "парень", "водитель"]:
            if emotion != "panic":
                suggested_voice = "male_baritone"
            else:
                suggested_voice = "male_panic"
        elif hl in ["спокойный", "норма", "спокойно"]:
            emotion = "neutral"
            speed = 1.0
            pitch_semitones = 0.0
            distress = 0.0
            tremolo = 0.0
            suggested_voice = "female_warm"

        # 8. Катастрофы и окружение
        elif hl in ["пожар", "огонь", "дым", "fire"]:
            ambience = "fire"
            if not sound_track:
                sound_track = "fire_inferno"
        elif hl in ["дтп", "авария", "трасса", "машина", "traffic"]:
            ambience = "traffic"
            if not sound_track:
                sound_track = "traffic_highway"
        elif hl in ["сирена", "скорая", "siren"]:
            ambience = "siren"
            if not sound_track:
                sound_track = "siren_distant"
        elif hl in ["тишина", "чисто", "silence"]:
            ambience = "none"
            sound_track = "none"

    # Очищаем текст от тегов
    clean_text = re.sub(r"<\|[^|>]*\|>", " ", text)
    clean_text = re.sub(r"#[a-zA-Zа-яА-ЯёЁ_0-9]+", " ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    # Эмоциональная пунктуация для F5-TTS
    if clean_text:
        if emotion == "panic" and not clean_text.endswith(("!", "!?", "...")):
            clean_text = clean_text.rstrip(". ") + "!"
        elif emotion in ["cry", "shock"] and not clean_text.startswith("..."):
            clean_text = "... " + clean_text

    return {
        "clean_text": clean_text,
        "tags_found": tags_found,
        "emotion": emotion,
        "breath_intro": breath_intro,
        "sob_intro": sob_intro,
        "pause_intro": pause_intro,
        "ambience": ambience,
        "sound_track": sound_track,
        "speed": speed,
        "pitch_semitones": pitch_semitones,
        "distress": distress,
        "tremolo": tremolo,
        "whisper_mode": whisper_mode,
        "cfg_strength": cfg_strength,
        "suggested_voice": suggested_voice,
    }


def apply_pitch_shift(audio: np.ndarray, sr: int, semitones: float) -> np.ndarray:
    """Чистый субсекундный сдвиг тона на GPU через torchaudio CUDA (в 30 раз быстрее CPU)."""
    if abs(semitones) < 0.1 or len(audio) == 0:
        return audio
    try:
        dev = _DEVICE if torch.cuda.is_available() else "cpu"
        tensor = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0).to(dev)
        shifted = torchaudio.functional.pitch_shift(
            tensor, sr, n_steps=float(semitones),
            n_fft=1024, win_length=1024, hop_length=256
        )
        return np.float32(shifted.squeeze(0).cpu().numpy())
    except Exception as e:
        tensor = torch.from_numpy(audio.astype(np.float32)).unsqueeze(0)
        shifted = torchaudio.functional.pitch_shift(tensor, sr, n_steps=float(semitones))
        return np.float32(shifted.squeeze(0).numpy())


def apply_whisper_filter(audio: np.ndarray, sr: int = 24000) -> np.ndarray:
    """Акустическая эмуляция шёпота: вырезание низких частот + аспирационный шум связок."""
    if len(audio) == 0:
        return audio
    sos = butter(3, [550, 4600], btype='bandpass', fs=sr, output='sos')
    filtered = sosfilt(sos, audio)
    env = np.abs(filtered)
    win = int(0.01 * sr)
    if win > 1:
        env = signal.convolve(env, np.ones(win)/win, mode='same')
    noise = np.random.normal(0, 0.02, len(audio))
    whisper = filtered * 0.65 + noise * env * 0.45
    return np.float32(whisper * 0.85)


def apply_pitch_vibrato(audio: np.ndarray, sr: int, freq: float = 6.5, depth_semitones: float = 1.2) -> np.ndarray:
    """Физиологический тремор связок (дрожание частоты тона от страха/рыданий)."""
    if depth_semitones <= 0.04 or len(audio) == 0:
        return audio
    ratio_dev = (2.0 ** (depth_semitones / 12.0)) - 1.0
    max_shift = max(2, min(int(sr * (ratio_dev / (2.0 * np.pi * freq))), int(0.02 * sr)))
    t = np.arange(len(audio))
    phase = 2.0 * np.pi * freq * t / sr
    shift = max_shift * np.sin(phase)
    indices = np.clip(t + shift, 0, len(audio) - 1)
    floor_idx = np.floor(indices).astype(int)
    ceil_idx = np.clip(floor_idx + 1, 0, len(audio) - 1)
    frac = np.float32(indices - floor_idx)
    return np.float32((1.0 - frac) * audio[floor_idx] + frac * audio[ceil_idx])


def apply_distress_saturation(audio: np.ndarray, sr: int, intensity: float = 0.5) -> np.ndarray:
    """Акустический надрыв связок: подъем форманты крика (2.2-3.6 кГц) + нелинейная сатурация tanh."""
    if intensity <= 0.04 or len(audio) == 0:
        return audio
    nyq = 0.5 * sr
    low = min(2200.0 / nyq, 0.85)
    high = min(3600.0 / nyq, 0.95)
    if high > low:
        sos = butter(2, [low, high], btype='bandpass', output='sos')
        scream_band = sosfilt(sos, audio) * (1.6 * intensity)
        mixed = audio + scream_band
    else:
        mixed = audio
    drive = 1.0 + 1.2 * intensity
    saturated = np.tanh(mixed * drive) / np.tanh(drive)
    return np.float32(saturated)


def generate_ambience_audio(ambience_type: str, target_length: int, sr: int = 24000) -> np.ndarray:
    """Синтез процедурного фона катастрофы с точным совпадением количества сэмплов."""
    n_samples = int(target_length)
    if n_samples <= 0:
        return np.zeros(0, dtype=np.float32)
    duration_sec = n_samples / sr

    if ambience_type == "fire":
        noise = np.random.normal(0, 0.05, n_samples)
        sos_rumble = butter(3, 350, btype="lowpass", fs=sr, output="sos")
        rumble = sosfilt(sos_rumble, noise) * 1.5
        crackle = np.zeros(n_samples)
        n_crackles = int(duration_sec * 18)
        if n_samples > 200 and n_crackles > 0:
            c_pos = np.random.randint(0, n_samples - 200, n_crackles)
            for p in c_pos:
                c_len = min(n_samples - p, np.random.randint(20, 100))
                if c_len > 0:
                    crackle[p:p + c_len] += np.random.uniform(-0.15, 0.15, c_len) * np.hanning(c_len)
        result = np.float32(rumble + crackle)
        return result[:n_samples]
    elif ambience_type == "traffic":
        noise = np.random.normal(0, 0.04, n_samples)
        sos = butter(3, [150, 1200], btype="bandpass", fs=sr, output="sos")
        result = np.float32(sosfilt(sos, noise))
        return result[:n_samples]
    elif ambience_type == "siren":
        t = np.linspace(0, duration_sec, n_samples, endpoint=False)
        freq = 700.0 + 350.0 * np.sin(2 * np.pi * 0.45 * t)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        result = np.float32(0.04 * np.sin(phase))
        return result[:n_samples]
    return np.zeros(n_samples, dtype=np.float32)


def generate_sob_audio(duration_sec: float = 0.45, sr: int = 24000) -> np.ndarray:
    n_samples = int(duration_sec * sr)
    t = np.linspace(0, duration_sec, n_samples)
    envelope = (np.sin(np.pi * t / duration_sec) ** 2) * (0.6 + 0.4 * np.sin(2 * np.pi * 7 * t))
    noise = np.random.normal(0, 0.32, n_samples) * envelope
    sos = butter(4, [450 / (0.5 * sr), 2600 / (0.5 * sr)], btype="bandpass", output="sos")
    return np.float32(sosfilt(sos, noise))


def generate_breath_audio(duration_sec: float = 0.40, sr: int = 24000) -> np.ndarray:
    n_samples = int(duration_sec * sr)
    t = np.linspace(0, duration_sec, n_samples)
    envelope = (np.sin(np.pi * t / duration_sec) ** 1.8)
    noise = np.random.normal(0, 0.35, n_samples) * envelope
    sos = butter(3, [600 / (0.5 * sr), 3200 / (0.5 * sr)], btype="bandpass", output="sos")
    return np.float32(sosfilt(sos, noise))


def apply_telephone_filter(audio: np.ndarray, sr: int = 24000) -> np.ndarray:
    """Реалистичный тракт 112 (G.711: полоса 300-3400 Гц + мягкий клиппинг)."""
    sos = butter(4, [300, 3400], btype="bandpass", fs=sr, output="sos")
    filtered = sosfilt(sos, audio)
    clipped = np.tanh(filtered * 1.3) * 0.88
    return np.float32(clipped)


def estimate_safe_duration(ref_audio_sec: float, ref_text: str, gen_text: str, speed: float = 1.0) -> float:
    """
    Интеллектуальный расчет необходимой длительности генерации для русской речи.
    Полностью устраняет обрезку фраз из-за нехватки фреймов в DiT-модели.
    
    Учитывает:
    - Реальную среднюю скорость русской речи (~12-14 символов/сек, т.е. 0.072-0.080 сек/символ).
    - Интонационные паузы на знаках препинания: запятые (0.22с), точки/восклицания/вопросы (0.35с).
    - Влияние коэффициента скорости (speed) с безопасным диапазоном.
    - Гарантированный запас (headroom ~10% + 0.35с), чтобы DiT успел закончить последнее слово.
    """
    clean_gen = gen_text.strip()
    chars_count = len(clean_gen)
    if chars_count == 0:
        return ref_audio_sec + 2.0

    ref_chars = max(len(ref_text.strip()), 1)
    ref_rate = ref_audio_sec / ref_chars if ref_chars > 0 else 0.075

    # Безопасный темп русской речи: не быстрее 0.072 сек/символ (иначе фразы обрезаются)
    base_rate = max(ref_rate, 0.072)

    commas = clean_gen.count(',') + clean_gen.count(';')
    sentence_stops = len(re.findall(r'[.!?]+', clean_gen))
    dashes = clean_gen.count('—') + clean_gen.count(' - ')

    pause_time = (commas * 0.22) + (sentence_stops * 0.35) + (dashes * 0.20)
    raw_gen_sec = (chars_count * base_rate) + pause_time

    clamped_speed = max(0.70, min(float(speed), 1.25))
    gen_sec = raw_gen_sec / clamped_speed
    safe_gen_sec = gen_sec * 1.10 + 0.35

    return ref_audio_sec + safe_gen_sec


def trim_trailing_silence(audio: np.ndarray, sr: int = 24000, threshold: float = 0.008, pad_sec: float = 0.25) -> np.ndarray:
    """Удаляет лишнюю тишину в самом конце фразы, сохраняя естественные 0.25с затухания/дыхания."""
    if len(audio) == 0:
        return audio
    win = int(0.02 * sr)
    rms = np.array([np.sqrt(np.mean(audio[i:i+win]**2)) for i in range(0, len(audio)-win, win)])
    voiced = np.where(rms > threshold)[0]
    if len(voiced) == 0:
        return audio
    last_voiced_sample = (voiced[-1] + 1) * win
    keep_samples = min(len(audio), last_voiced_sample + int(pad_sec * sr))
    return audio[:keep_samples]


from .sound_manager import get_background_audio_slice


def synthesize_f5_fast(
    text: str,
    voice: str = "female_panic",
    pitch: float = 0.0,
    speed: float = 1.0,
    nfe_step: int = 16,
    cfg_strength: float = 2.0,
    tremolo: float = 0.0,
    distress: float = 0.0,
    telephone: bool = True,
    sound_track: Optional[str] = None,
    sound_filter: str = "room",
    sound_volume: float = 0.30,
    output_name: str = "",
    gen_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Выполняет мгновенный резидентный синтез F5-TTS в VRAM GPU без перезапуска Python.
    Поддерживает одиночный поток генерации (Single-Flight Lock) и отмену устаревших запросов.
    """
    if gen_id is None:
        gen_id = register_new_generation()

    t_start = time.time()

    # Быстрый выход если запрос уже не актуален
    if not is_generation_active(gen_id):
        return {
            "status": "cancelled",
            "error": "Запрос отменен более новой генерацией",
            "cancelled": True,
        }

    # Исключительная блокировка GPU для предотвращения параллельных коллизий
    with _INFERENCE_LOCK:
        # Проверяем, не появился ли за время ожидания лока более свежий запрос
        if not is_generation_active(gen_id):
            return {
                "status": "cancelled",
                "error": "Запрос отменен более новой генерацией",
                "cancelled": True,
            }

        try:
            model, vocoder = get_f5_resources()
            catalog = get_voices_catalog()

            tag_info = parse_f5_tags(text)
            clean_text = tag_info["clean_text"]
            if not clean_text:
                clean_text = "Помогите, пожалуйста!"

            # Выбор эталонного голоса
            chosen_voice_id = voice
            if chosen_voice_id == "auto" or chosen_voice_id not in catalog:
                if tag_info["suggested_voice"] and tag_info["suggested_voice"] in catalog:
                    chosen_voice_id = tag_info["suggested_voice"]
                elif "female_panic" in catalog:
                    chosen_voice_id = "female_panic"
                elif "female_deep" in catalog:
                    chosen_voice_id = "female_deep"

            ref_audio, ref_text_processed = get_cached_ref(chosen_voice_id, catalog)
            ref_data, ref_sr = sf.read(ref_audio)
            ref_audio_sec = len(ref_data) / ref_sr

            effective_speed = speed if speed != 1.0 else tag_info.get("speed", 1.0)
            effective_pitch = pitch if pitch != 0.0 else tag_info.get("pitch_semitones", 0.0)
            effective_distress = distress if distress > 0.01 else tag_info.get("distress", 0.0)
            effective_tremolo = tremolo if tremolo > 0.01 else tag_info.get("tremolo", 0.0)
            effective_cfg = cfg_strength if cfg_strength != 2.0 else tag_info.get("cfg_strength", 2.0)
            whisper_mode = tag_info.get("whisper_mode", False)

            # Расчет безопасной длительности для предотвращения обрезки русских фраз
            total_fix_duration = estimate_safe_duration(
                ref_audio_sec=ref_audio_sec,
                ref_text=ref_text_processed,
                gen_text=clean_text,
                speed=effective_speed,
            )

            # Перед тяжелым инференсом еще раз проверяем актуальность
            if not is_generation_active(gen_id):
                return {
                    "status": "cancelled",
                    "error": "Запрос отменен более новой генерацией",
                    "cancelled": True,
                }

            t_infer = time.time()
            wav, sr, _ = infer_process(
                ref_audio,
                ref_text_processed,
                clean_text,
                model,
                vocoder,
                mel_spec_type="vocos",
                target_rms=0.1,
                cross_fade_duration=0.15,
                nfe_step=max(8, min(nfe_step, 48)),
                cfg_strength=float(effective_cfg),
                sway_sampling_coef=-1.0,
                speed=effective_speed,
                fix_duration=total_fix_duration,
                device=_DEVICE,
            )
            inference_sec = time.time() - t_infer

            # Если во время генерации пришел более новый запрос — не перезаписываем результат
            if not is_generation_active(gen_id):
                return {
                    "status": "cancelled",
                    "error": "Запрос отменен более новой генерацией",
                    "cancelled": True,
                }

            # Удаляем тишину на хвосте, если DiT закончил фразу раньше выделенного лимита времени
            wav = trim_trailing_silence(wav, sr, threshold=0.008, pad_sec=0.25)

            # 1. Сдвиг питча без изменения темпа (через torchaudio)
            if abs(effective_pitch) > 0.1:
                wav = apply_pitch_shift(wav, sr, effective_pitch)

            # 2. Тремор связок
            if effective_tremolo > 0.01:
                wav = apply_pitch_vibrato(wav, sr, freq=6.5, depth_semitones=float(effective_tremolo) * 4.5)

            # 3. Надрыв связок (форманта крика 2.2-3.6 кГц + tanh сатурация)
            if effective_distress > 0.01:
                wav = apply_distress_saturation(wav, sr, intensity=float(effective_distress))

            # 4. Шёпот (аспирационный шум и срез низких частот)
            if whisper_mode:
                wav = apply_whisper_filter(wav, sr)

            # 5. Процедурные звуки (пауза / вдох / всхлип)
            stems = []
            if tag_info.get("pause_intro"):
                stems.append(np.zeros(int(0.35 * sr), dtype=np.float32))
            if tag_info.get("breath_intro"):
                stems.append(generate_breath_audio(0.38, sr))
            if tag_info.get("sob_intro"):
                stems.append(generate_sob_audio(0.42, sr))

            if stems:
                silence = np.zeros(int(0.06 * sr), dtype=np.float32)
                full_intro = []
                for s in stems:
                    full_intro.extend([s, silence])
                wav = np.concatenate(full_intro + [wav])

            # 6. Фон катастрофы (безопасное точное смешивание равной длины)
            chosen_sound = sound_track if (sound_track and sound_track != "none") else tag_info.get("sound_track")
            real_ambiences = {
                "fire_inferno", "traffic_highway", "siren_distant",
                "stream-river-water", "cough_smoke", "woman_crying",
                "smoke_detector", "heavy_breathing", "child_crying",
                "car_crash", "dog_bark", "door_slam",
            }
            
            # Процедурный шум генерируем только если нет реального аудиотрека этой же катастрофы
            if tag_info["ambience"] != "none" and (chosen_sound not in real_ambiences):
                amb = generate_ambience_audio(tag_info["ambience"], len(wav), sr)
                min_l = min(len(wav), len(amb))
                if min_l > 0:
                    wav = wav[:min_l] * 0.92 + amb[:min_l] * 0.40

            # 6.5. Фоновая дорожка реальных звуков из папки Sound
            if chosen_sound and chosen_sound != "none":
                eff_filter = sound_filter
                eff_vol = sound_volume
                from .sound_manager import SOUND_METADATA
                if chosen_sound in SOUND_METADATA:
                    meta = SOUND_METADATA[chosen_sound]
                    if sound_filter == "room" and "default_filter" in meta:
                        eff_filter = meta["default_filter"]
                    if abs(sound_volume - 0.30) < 1e-4 and "default_volume" in meta:
                        eff_vol = meta["default_volume"]

                sound_bg = get_background_audio_slice(
                    track_id=chosen_sound,
                    target_samples=len(wav),
                    sr=sr,
                    filter_type=eff_filter,
                    volume=eff_vol,
                )
                if len(sound_bg) > 0:
                    min_s = min(len(wav), len(sound_bg))
                    wav = wav[:min_s] * 0.92 + sound_bg[:min_s]

            # 7. Телефонный тракт
            if telephone:
                wav = apply_telephone_filter(wav, sr)

            # Нормализация
            max_val = np.max(np.abs(wav))
            if max_val > 0.01:
                wav = wav / max_val * 0.95

            # Сохранение WAV
            if not output_name:
                output_name = f"call_f5_{time.strftime('%Y%m%d_%H%M%S')}.wav"
            out_path = OUTPUTS_DIR / output_name
            sf.write(str(out_path), wav.astype(np.float32), sr, format="WAV")

            duration_sec = round(len(wav) / sr, 2)
            latency_sec = round(time.time() - t_start, 2)

            meta = {
                "file_name": output_name,
                "voice_used": chosen_voice_id,
                "duration_sec": duration_sec,
                "latency_sec": latency_sec,
                "inference_sec": round(inference_sec, 2),
                "clean_text": clean_text,
                "tags_detected": tag_info["tags_found"],
                "emotion": tag_info["emotion"],
                "ambience": tag_info["ambience"],
                "sound_track": chosen_sound or "none",
                "sound_filter": sound_filter,
                "sound_volume": sound_volume,
                "pitch": effective_pitch,
                "speed": effective_speed,
                "nfe_step": nfe_step,
                "cfg_strength": effective_cfg,
                "tremor": effective_tremolo,
                "distress_intensity": effective_distress,
                "whisper": whisper_mode,
            }

            meta_path = out_path.with_suffix(".json")
            with open(meta_path, "w", encoding="utf-8") as jf:
                json.dump(meta, jf, ensure_ascii=False, indent=2)

            return meta
        finally:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
