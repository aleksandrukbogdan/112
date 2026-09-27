# -*- coding: utf-8 -*-
"""
Разбор реплики тренажёра 112 в голос и фон.

На вход приходит чистый текст, который уже видит обучаемый.
Хэштеги добавляются только здесь и в чат не возвращаются.

Если вместе с репликой передан билет (ситуация, группа, ФИО),
голос и фон берутся из билета и держатся на всех ответах заявителя.
Без билета остаётся прежний разбор по тексту: так говорят бригады.
"""
from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass

TTL_SEC = 90.0

_LOCK = threading.Lock()
_STICKY: dict | None = None

_OFFICIAL = re.compile(
    r"(выехал\w*|прибыл\w*\s+на место|приступил\w*|работы завершен\w*|"
    r"принял[,.]|выезжаем|адрес не понял|куда выезжать|"
    r"мы на другом вызове|держите в курсе|"
    r"сверил[аи]\s+по записи|старший оператор)",
    re.I,
)
_PANIC = re.compile(r"помогите|скорее|спасите|крич|паник|ужас|!", re.I)
_FEMALE = re.compile(r"мам[аыуеой]|женщин|бабуш|жен[аыу]\b|супруг[аи]\b|доч", re.I)
_MALE = re.compile(r"\bпапа\b|отец|мужчин|\bмуж\b|водитель|парень|дедуш", re.I)
_CHILD = re.compile(r"ребён\w*|ребен\w*|малыш|плач|мам[аыуеой]", re.I)
_FIRE = re.compile(r"пож\w*|горит|огон\w*|плам\w*|задым\w*|дыма|дыму|\bдым\b", re.I)
_CRASH = re.compile(r"\bдтп\b|авари\w*|столкнов\w*|тормоз\w*|машин\w*", re.I)
_DOG = re.compile(r"собак\w*|лает|\bлай\b", re.I)
_DOOR = re.compile(r"взлом\w*|ломят\w*|ломит\w*|выбива\w*\s+двер|ломают двер", re.I)
_FIGHT = re.compile(r"дерут\w*|драк\w*|бьют|палкам|прутам", re.I)
_MEDICAL = re.compile(r"сердц\w*|потеря\w*\s+сознан|без сознания|задых\w*|одышк\w*|плохо\s+женщин|плохо\s+мужчин", re.I)
_WATER = re.compile(r"в воду|тонет|утонул", re.I)
_SHORT = re.compile(
    r"^(\+?\d[\d\-\s()]{5,}\.?|да[,.! ]|нет[,.! ]|алло[.!]?|сейчас\b|хорошо[,.]?|понял[аи]?[,.]?)$",
    re.I,
)


@dataclass
class ScenePlan:
    voice: str
    text: str
    sound_track: str | None
    sound_volume: float
    official: bool
    scene: str
    inherited: bool


def reset_scene() -> None:
    global _STICKY
    with _LOCK:
        _STICKY = None


def _gender(text: str, sticky: dict | None) -> str | None:
    if _FEMALE.search(text):
        return "f"
    if _MALE.search(text):
        return "m"
    if sticky and time.monotonic() - sticky["at"] < TTL_SEC:
        return sticky.get("gender")
    return None


def _voice(kind: str, gender: str | None, panicked: bool) -> str:
    if kind == "child":
        return "male_panic" if gender == "m" else "female_panic"
    if kind == "medical":
        return "male_deep" if gender == "m" else "female_elderly"
    if kind == "fight" and gender != "f":
        return "male_panic"
    if kind in ("fire", "crash", "dog", "door", "fight", "water"):
        if panicked or kind in ("crash", "water"):
            return "male_panic" if gender == "m" else "female_panic"
        return "male_baritone" if gender == "m" else "female_warm"
    return "male_baritone" if gender == "m" else "female_warm"


def _match(text: str) -> str | None:
    if _CHILD.search(text):
        return "child"
    if _FIRE.search(text):
        return "fire"
    if _WATER.search(text):
        return "water"
    if _CRASH.search(text):
        return "crash"
    if _DOG.search(text):
        return "dog"
    if _DOOR.search(text):
        return "door"
    if _FIGHT.search(text):
        return "fight"
    if _MEDICAL.search(text):
        return "medical"
    return None


def _is_short(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text).strip()
    if _SHORT.match(compact):
        return True
    words = compact.replace(".", " ").split()
    return len(compact) <= 90 and len(words) <= 12 and _match(text) is None


def _bed(kind: str) -> str | None:
    return {
        "fire": "fire_inferno",
        "child": "child_crying",
        "crash": "traffic_highway",
        "dog": "dog_bark",
        "medical": "heavy_breathing",
        "water": None,
        "fight": None,
        "door": "door_slam",
    }.get(kind)


_OPEN_FIRE = re.compile(r"пож\w*|горит|огон\w*|плам\w*|возгор", re.I)
_SMOKE = re.compile(r"задым\w*|дыма|дыму|\bдым\b", re.I)
_GROUP_KIND = {"1": "fire", "2": "crash"}
_FIO_F = re.compile(r"(ова|ева|ёва|ина|ына|ая|яя)$", re.I)
_FIO_M = re.compile(r"(ов|ев|ёв|ин|ын|ий|ой|ых)$", re.I)


def _gender_person(fio: str, situaciya: str) -> str | None:
    """Пол заявителя из ФИО и описания билета, не из текущей фразы."""
    found = _gender(f"{fio or ''} {situaciya or ''}", None)
    if found:
        return found
    parts = [p for p in re.split(r"\s+", (fio or "").replace("—", " ").strip()) if p and p not in "-–"]
    if not parts:
        return None
    sur = parts[0]
    if _FIO_F.search(sur):
        return "f"
    if _FIO_M.search(sur):
        return "m"
    blob = " ".join(parts)
    if re.search(r"(вич|ьич|оглы)\b", blob, re.I):
        return "m"
    if re.search(r"(вна|чна)\b", blob, re.I):
        return "f"
    return None


def _smoke_only(text: str) -> bool:
    """Задымление без открытого огня звучит тише. «Пламени не видит» — это дым, не пожар."""
    raw = text or ""
    if not _SMOKE.search(raw):
        return False
    if re.search(r"пож\w*|горит|огон\w*|возгор", raw, re.I):
        return False
    if re.search(r"пламен\w*\s+не|не\s+\w*\s*плам|без\s+плам", raw, re.I):
        return True
    return not _OPEN_FIRE.search(raw)


def _volume(track: str | None, situaciya: str) -> float:
    """Громкость фона чуть плавает от реплики к реплике, сцена не меняется."""
    if not track:
        return 0.0
    if track == "fire_inferno" and _smoke_only(situaciya):
        base = 0.18
    elif track == "car_crash":
        base = 0.40
    elif track == "door_slam":
        base = 0.34
    else:
        base = 0.28
    vol = max(0.08, min(0.48, base + random.uniform(-0.04, 0.04)))
    # Ровно 0.30 движок подменяет громкостью из каталога дорожки.
    if abs(vol - 0.30) < 0.008:
        vol += 0.02
    return round(vol, 3)


def _from_ticket(raw: str, situaciya: str, fio: str, gruppa: str,
                 turn: int, voice_override: str) -> ScenePlan:
    kind = _match(situaciya or "") or _GROUP_KIND.get(str(gruppa or "").strip())
    gender = _gender_person(fio, situaciya)
    panicked = bool(_PANIC.search(situaciya or "")) or kind in ("crash", "water", "fight", "door")
    override = (voice_override or "").strip()
    if override and override != "auto":
        chosen = override
    else:
        chosen = _voice(kind or "neutral", gender, panicked if kind else False)
    n = int(turn or 1)
    if kind == "crash" and n <= 1:
        event, track = "car_crash", "car_crash"
    elif kind == "crash":
        event, track = None, "traffic_highway"
    elif kind == "door":
        event, track = "door_slam", "door_slam"
    else:
        event, track = None, _bed(kind) if kind else None
    tags = _tags(kind, panicked, event) if kind else ""
    spoken = f"{tags} {raw}".strip() if tags else raw
    return ScenePlan(
        voice=chosen,
        text=spoken,
        sound_track=track,
        sound_volume=_volume(track, situaciya),
        official=False,
        scene=kind or "neutral",
        inherited=n > 1,
    )


def _event(kind: str, sticky: dict | None) -> str | None:
    if kind == "crash" and not (sticky and sticky.get("crash_used")):
        return "car_crash"
    if kind == "door":
        return "door_slam"
    return None


def classify(text: str, *, situaciya: str = "", fio: str = "", gruppa: str = "",
             turn: int = 0, voice: str = "", call_id: str = "") -> ScenePlan:
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    # call_id различает занятия на стороне API; сцена полностью задаётся билетом и номером реплики.
    _ = call_id
    if (situaciya or "").strip() or str(gruppa or "").strip():
        return _from_ticket(raw, situaciya, fio, gruppa, turn, voice)
    return _from_utterance(raw)


def _from_utterance(raw: str) -> ScenePlan:
    global _STICKY
    now = time.monotonic()
    with _LOCK:
        sticky = _STICKY
        fresh = bool(sticky) and now - sticky["at"] < TTL_SEC

        if _OFFICIAL.search(raw):
            voice = "female_warm" if re.search(r"сверила", raw, re.I) else "male_baritone"
            return ScenePlan(voice, raw, None, 0.0, True, "official", False)

        kind = _match(raw)
        gender = _gender(raw, sticky if fresh else None)
        panicked = bool(_PANIC.search(raw))

        if kind is None and _is_short(raw) and fresh:
            _STICKY["at"] = now
            if gender:
                _STICKY["gender"] = gender
            return ScenePlan(
                voice=_STICKY["voice"],
                text=raw,
                sound_track=_STICKY.get("bed"),
                sound_volume=0.22 if _STICKY.get("bed") else 0.0,
                official=False,
                scene=_STICKY.get("scene") or "neutral",
                inherited=True,
            )

        if kind is None:
            voice = _voice("neutral", gender, False)
            # Длинная нейтральная фраза сбрасывает чужой фон. Служебная до сюда не доходит.
            _STICKY = {
                "at": now,
                "voice": voice,
                "gender": gender,
                "bed": None,
                "scene": "neutral",
                "crash_used": False,
            }
            return ScenePlan(voice, raw, None, 0.0, False, "neutral", False)

        event = _event(kind, sticky if fresh and sticky.get("scene") == kind else None)
        bed = _bed(kind)
        track = event or bed
        voice = _voice(kind, gender, panicked)
        tags = _tags(kind, panicked, event)
        _STICKY = {
            "at": now,
            "voice": voice,
            "gender": gender,
            "bed": bed,
            "scene": kind,
            "crash_used": bool(event == "car_crash" or (fresh and sticky and sticky.get("crash_used") and kind == "crash")),
        }
        if event == "car_crash":
            _STICKY["crash_used"] = True
        spoken = f"{tags} {raw}".strip() if tags else raw
        volume = 0.30 if track else 0.0
        return ScenePlan(voice, spoken, track, volume, False, kind, False)


def _tags(kind: str, panicked: bool, event: str | None) -> str:
    if kind == "child":
        return "#плач_ребенка #вдох" if panicked else "#плач_ребенка"
    if kind == "fire":
        return "#пожар #крик #вдох" if panicked else "#пожар"
    if kind == "crash":
        if event == "car_crash":
            return "#удар_дтп #паника #вдох"
        return "#дтп"
    if kind == "dog":
        return "#собака #паника" if panicked else "#собака"
    if kind == "door":
        return "#взлом #паника"
    if kind == "fight":
        return "#паника #крик" if panicked else "#паника"
    if kind == "water":
        return "#паника #вдох"
    if kind == "medical":
        return "#одышка"
    return ""
