#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Менеджер реальных звуковых дорожек и фоновых эффектов (sound_manager.py)
Загружает, кэширует и обрабатывает аудиофайлы из папки D:\EXP\HAKATON\Sound.
Поддерживает акустические фильтры пространственного удаления (комната, за стеной, двор, вблизи).
"""

import os
import random
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.signal import butter, sosfilt

# Путь к папке со звуками. На сервере задаётся SOUND_DIR, локально это соседняя папка Sound.
_DEFAULT_SOUND = Path(__file__).resolve().parent.parent / "Sound"
SOUND_DIR = Path(os.environ.get("SOUND_DIR", str(_DEFAULT_SOUND)))
try:
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

# Каталог метаданных реальных звуков службы 112
SOUND_METADATA: Dict[str, Dict[str, Any]] = {
    "fire_inferno": {
        "title": "🔥 Пожар / рокот пламени и треск",
        "category": "catastrophe",
        "category_title": "Атмосферы катастроф",
        "default_filter": "room",
        "default_volume": 0.35,
        "tags": ["#пожар", "#огонь", "#пламя", "#дым", "#фон_пожар"],
        "aliases": ["fire", "fire_inferno", "пожар", "огонь", "пламя", "дым"]
    },
    "traffic_highway": {
        "title": "🚗 Скоростная автотрасса / шум потока машин",
        "category": "catastrophe",
        "category_title": "Атмосферы катастроф",
        "default_filter": "distance",
        "default_volume": 0.30,
        "tags": ["#дтп", "#трасса", "#трафик", "#машины", "#фон_дтп"],
        "aliases": ["traffic", "traffic_highway", "дтп", "трасса", "трафик", "машина"]
    },
    "siren_distant": {
        "title": "🚨 Отзвук сирены оперативных служб вдали",
        "category": "catastrophe",
        "category_title": "Атмосферы катастроф",
        "default_filter": "distance",
        "default_volume": 0.25,
        "tags": ["#сирена", "#скорая", "#полиция", "#фон_сирена"],
        "aliases": ["siren", "siren_distant", "сирена", "скорая", "полиция"]
    },
    "child_crying": {
        "title": "👶 Непрекращающийся детский плач",
        "category": "human",
        "category_title": "Физиология и плач",
        "default_filter": "room",
        "default_volume": 0.28,
        "tags": ["#плач_ребенка", "#детский_плач", "#ребенок"],
        "aliases": ["child_cry", "child_crying", "плач_ребенка", "плач_ребёнка", "детский_плач"]
    },
    "woman_crying": {
        "title": "💧 Рыдания и плач взрослой женщины",
        "category": "human",
        "category_title": "Физиология и плач",
        "default_filter": "near",
        "default_volume": 0.32,
        "tags": ["#плач_женщины", "#рыдания", "#всхлип", "#слезы", "#горе"],
        "aliases": ["woman_cry", "woman_crying", "плач_женщины", "женский_плач", "рыдания", "слезы"]
    },
    "heavy_breathing": {
        "title": "🫁 Тяжёлая одышка и судорожное дыхание",
        "category": "human",
        "category_title": "Физиология и плач",
        "default_filter": "near",
        "default_volume": 0.35,
        "tags": ["#одышка", "#задыхается", "#тяжелое_дыхание", "#дыхание"],
        "aliases": ["breath", "breathing", "heavy_breathing", "одышка", "задыхается", "тяжелое_дыхание"]
    },
    "cough_smoke": {
        "title": "💨 Приступообразный кашель в дыму",
        "category": "human",
        "category_title": "Физиология и плач",
        "default_filter": "near",
        "default_volume": 0.38,
        "tags": ["#кашель", "#дым", "#задыхается_кашель"],
        "aliases": ["cough", "cough_smoke", "кашель"]
    },
    "door_slam": {
        "title": "🚪 Взлом / сильный удар и хлопок двери",
        "category": "event",
        "category_title": "События происшествия",
        "default_filter": "room",
        "default_volume": 0.40,
        "tags": ["#взлом", "#дверь", "#ломится", "#удар_двери"],
        "aliases": ["door", "door_slam", "дверь", "взлом", "ломится", "удар_двери"]
    },
    "smoke_detector": {
        "title": "🚨 Писк пожарной сигнализации / датчик дыма",
        "category": "event",
        "category_title": "События происшествия",
        "default_filter": "room",
        "default_volume": 0.25,
        "tags": ["#датчик_дыма", "#пожарная_тревога", "#сигнализация"],
        "aliases": ["smoke_alarm", "smoke_detector", "датчик_дыма", "пожарная_тревога", "сигнализация"]
    },
    "car_crash": {
        "title": "💥 Визг тормозов и жесткий удар ДТП",
        "category": "event",
        "category_title": "События происшествия",
        "default_filter": "distance",
        "default_volume": 0.42,
        "tags": ["#удар_дтп", "#авария", "#столкновение"],
        "aliases": ["crash", "car_crash", "авария", "удар_дтп", "столкновение"]
    },
    "dog_bark": {
        "title": "🐕 Агрессивный лай собаки",
        "category": "event",
        "category_title": "События происшествия",
        "default_filter": "distance",
        "default_volume": 0.32,
        "tags": ["#собака", "#лай", "#нападение_собаки"],
        "aliases": ["dog", "dog_bark", "собака", "лай", "нападение_собаки"]
    },
    "stream-river-water": {
        "title": "🌊 Река / течение воды",
        "category": "catastrophe",
        "category_title": "Атмосферы катастроф",
        "default_filter": "distance",
        "default_volume": 0.26,
        "tags": ["#река", "#вода", "#фон_река"],
        "aliases": ["stream-river-water", "river", "река", "фон_река"]
    },
}

# Кэш предзагруженных и приведенных к 24 кГц аудиодорожек
_SOUND_CACHE: Dict[str, np.ndarray] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_INITIALIZED = False


def scan_sound_files() -> List[Dict[str, Any]]:
    """Сканирует папку Sound и возвращает список найденных звуковых файлов с метаданными."""
    if not SOUND_DIR.exists():
        return []
    
    supported_exts = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aiff", ".aif"}
    files = []
    for f in SOUND_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in supported_exts:
            try:
                info = sf.info(str(f))
                stem = f.stem
                meta = SOUND_METADATA.get(stem, {})
                title = meta.get("title", f"🎵 {f.stem}")
                category = meta.get("category", "other")
                category_title = meta.get("category_title", "Прочие звуки")
                tags = meta.get("tags", [f"#{stem}"])
                default_filter = meta.get("default_filter", "room")
                default_volume = meta.get("default_volume", 0.30)

                files.append({
                    "id": stem,
                    "filename": f.name,
                    "path": str(f),
                    "title": title,
                    "category": category,
                    "category_title": category_title,
                    "tags": tags,
                    "default_filter": default_filter,
                    "default_volume": default_volume,
                    "duration_sec": round(info.duration, 2),
                    "samplerate": info.samplerate,
                    "channels": info.channels,
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                })
            except Exception as e:
                print(f"[SoundManager] Ошибка чтения {f.name}: {e}")
    
    # Сортировка: сначала катастрофы, затем человек, затем события
    cat_order = {"catastrophe": 0, "human": 1, "event": 2, "other": 3}
    files.sort(key=lambda x: (cat_order.get(x["category"], 9), x["title"]))
    return files


def load_and_resample(file_path: Path, target_sr: int = 24000) -> np.ndarray:
    """Загружает файл, преобразует в моно float32 и ресемплирует в target_sr."""
    data, sr = sf.read(str(file_path))
    if data.ndim > 1:
        mono = data.mean(axis=1).astype(np.float32)
    else:
        mono = data.astype(np.float32)

    if sr != target_sr:
        gcd = np.gcd(sr, target_sr)
        up = target_sr // gcd
        down = sr // gcd
        mono = signal.resample_poly(mono, up, down).astype(np.float32)

    # Мягкая нормализация пика
    peak = np.max(np.abs(mono))
    if peak > 0.001:
        mono = mono / peak * 0.90

    return mono


def init_sound_cache(target_sr: int = 24000):
    """Инициализирует кэш звуков в памяти (быстрая фоновая загрузка)."""
    global _CACHE_INITIALIZED
    with _CACHE_LOCK:
        if _CACHE_INITIALIZED:
            return
        
        found = scan_sound_files()
        for item in found:
            f_path = Path(item["path"])
            stem = item["id"]
            try:
                audio = load_and_resample(f_path, target_sr=target_sr)
                _SOUND_CACHE[stem] = audio
                
                # Регистрация всех алиасов из SOUND_METADATA
                if stem in SOUND_METADATA:
                    for alias in SOUND_METADATA[stem].get("aliases", []):
                        _SOUND_CACHE[alias.lower()] = audio

                print(f"[SoundManager] Загружен звук '{stem}': {len(audio)/target_sr:.2f} сек ({target_sr} Гц)")
            except Exception as e:
                print(f"[SoundManager] Не удалось загрузить {f_path}: {e}")

        _CACHE_INITIALIZED = True


def get_cached_sound(track_id: str, target_sr: int = 24000) -> Optional[np.ndarray]:
    """Возвращает кэшированный 24 кГц массив аудио по ID или алиасу."""
    if not _CACHE_INITIALIZED:
        init_sound_cache(target_sr)
    
    clean_id = track_id.lower().strip()
    with _CACHE_LOCK:
        if clean_id in _SOUND_CACHE:
            return _SOUND_CACHE[clean_id]
        
        # Поиск по подстроке
        for k, v in _SOUND_CACHE.items():
            if clean_id in k.lower() or k.lower() in clean_id:
                return v

        # Попытка прямой загрузки
        for ext in [".wav", ".mp3", ".ogg", ".flac", ".aiff"]:
            candidate = SOUND_DIR / f"{track_id}{ext}"
            if candidate.exists():
                try:
                    audio = load_and_resample(candidate, target_sr)
                    _SOUND_CACHE[clean_id] = audio
                    return audio
                except Exception:
                    pass

    return None


def apply_acoustic_filter(
    audio: np.ndarray,
    sr: int = 24000,
    filter_type: str = "room",
    volume: float = 0.30
) -> np.ndarray:
    """
    Акустический фильтр пространственного размещения звука в помещении/на улице:
    - 'room': В комнате заявителя (мягкий срез верхов 3200 Гц, легкий комнатный объем).
    - 'muffled': За стеной / в соседней комнате (глухой низкочастотный звук 1100 Гц).
    - 'distance': Во дворе / на улице (удаление 15-20 м, полосовой 200-2400 Гц).
    - 'near': Вблизи / на руках (яркий, близкий плач, срез 4500 Гц).
    - 'raw': Без частотной фильтрации (только регулировка громкости).
    """
    if len(audio) == 0:
        return audio
    
    vol = max(0.0, min(float(volume), 1.5))
    f_type = filter_type.lower().strip()

    if f_type == "room":
        # Комната: срезаем ультра-высокие частоты для эффекта расстояния 2-3 м
        sos = butter(3, min(3200, sr * 0.45), btype="lowpass", fs=sr, output="sos")
        filtered = sosfilt(sos, audio)
        return np.float32(filtered * vol * 0.85)

    elif f_type == "muffled":
        # За стеной: глухой звук с крутым срезом (звукоизоляция стен)
        sos = butter(4, min(1100, sr * 0.45), btype="lowpass", fs=sr, output="sos")
        filtered = sosfilt(sos, audio)
        return np.float32(filtered * vol * 0.70)

    elif f_type == "distance":
        # Улица / двор: расстояние + открытое пространство
        low = max(100, min(200, sr * 0.1))
        high = min(2400, sr * 0.45)
        sos = butter(3, [low, high], btype="bandpass", fs=sr, output="sos")
        filtered = sosfilt(sos, audio)
        return np.float32(filtered * vol * 0.65)

    elif f_type == "near":
        # Вблизи / на руках заявителя
        sos = butter(2, min(4500, sr * 0.45), btype="lowpass", fs=sr, output="sos")
        filtered = sosfilt(sos, audio)
        return np.float32(filtered * vol * 1.15)

    elif f_type == "raw":
        return np.float32(audio * vol)

    # По умолчанию — комната
    sos = butter(3, min(3200, sr * 0.45), btype="lowpass", fs=sr, output="sos")
    return np.float32(sosfilt(sos, audio) * vol * 0.85)


_EFFECT_CLOCK: Dict[str, Dict[str, float]] = {}
_EFFECT_ORDER: List[str] = []
_EFFECT_LOCK = threading.Lock()


def reset_effect_clock(call_id: str = "") -> None:
    with _EFFECT_LOCK:
        if call_id:
            _EFFECT_CLOCK.pop(call_id, None)
        else:
            _EFFECT_CLOCK.clear()
            _EFFECT_ORDER.clear()


def _clock_slot(call_id: str, track_id: str) -> Dict[str, float]:
    slot = _EFFECT_CLOCK.get(call_id)
    if slot is None:
        slot = {}
        _EFFECT_CLOCK[call_id] = slot
        _EFFECT_ORDER.append(call_id)
        if len(_EFFECT_ORDER) > 200:
            old = _EFFECT_ORDER.pop(0)
            _EFFECT_CLOCK.pop(old, None)
    state = slot.get(track_id)
    if state is None:
        state = {"cursor": 0.0, "next_at": 0.0}
        slot[track_id] = state
    return state


def place_spaced_effect(
    track_id: str,
    target_samples: int,
    sr: int = 24000,
    filter_type: str = "room",
    volume: float = 0.30,
    call_id: str = "",
) -> np.ndarray:
    """Один проход wav в начале, следующий не раньше чем через 30 секунд речи."""
    from .effect_gap import plan_effect_starts

    out = np.zeros(target_samples, dtype=np.float32)
    raw_audio = get_cached_sound(track_id, target_sr=sr)
    if raw_audio is None or len(raw_audio) == 0 or target_samples <= 0:
        return out

    processed = apply_acoustic_filter(raw_audio, sr=sr, filter_type=filter_type, volume=volume)
    effect_len = len(processed)
    fade_len = min(int(0.12 * sr), effect_len // 4)
    if fade_len > 1:
        processed = processed.copy()
        processed[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
        processed[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)

    utter_dur = target_samples / float(sr)
    effect_dur = effect_len / float(sr)
    key = (call_id or "").strip()

    if not key:
        n = min(effect_len, target_samples)
        out[:n] += processed[:n]
        return out

    with _EFFECT_LOCK:
        state = _clock_slot(key, track_id)
        starts, cursor, next_at = plan_effect_starts(
            state["cursor"], utter_dur, effect_dur, state["next_at"],
        )
        state["cursor"] = cursor
        state["next_at"] = next_at

    for local in starts:
        start_i = int(round(local * sr))
        if start_i >= target_samples:
            continue
        room = target_samples - start_i
        n = min(effect_len, room)
        chunk = processed[:n]
        if n < effect_len and fade_len > 1 and n > fade_len:
            chunk = chunk.copy()
            chunk[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
        out[start_i:start_i + n] += chunk
    return out


def get_background_audio_slice(
    track_id: str,
    target_samples: int,
    sr: int = 24000,
    filter_type: str = "room",
    volume: float = 0.30,
    random_offset: bool = True
) -> np.ndarray:
    """
    Извлекает фрагмент фоновой дорожки точной длины target_samples,
    применяет выбранный акустический фильтр и громкость,
    сглаживает начало и конец (fade-in / fade-out).
    """
    raw_audio = get_cached_sound(track_id, target_sr=sr)
    if raw_audio is None or len(raw_audio) == 0:
        return np.zeros(target_samples, dtype=np.float32)

    total_len = len(raw_audio)

    # Выбор случайного смещения, если трек длиннее нужного отрезка
    if total_len > target_samples and random_offset:
        max_start = total_len - target_samples
        start_idx = random.randint(0, max_start)
        slice_audio = raw_audio[start_idx : start_idx + target_samples].copy()
    elif total_len >= target_samples:
        slice_audio = raw_audio[:target_samples].copy()
    else:
        # Зацикливание если трек короче необходимого времени
        repeats = (target_samples // total_len) + 1
        tiled = np.tile(raw_audio, repeats)
        slice_audio = tiled[:target_samples].copy()

    # Применяем фильтр и громкость
    processed = apply_acoustic_filter(slice_audio, sr=sr, filter_type=filter_type, volume=volume)

    # Плавное нарастание и затухание (0.15 сек), чтобы не было щелчков
    fade_len = min(int(0.15 * sr), target_samples // 4)
    if fade_len > 1:
        fade_in = np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
        fade_out = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
        processed[:fade_len] *= fade_in
        processed[-fade_len:] *= fade_out

    return processed
