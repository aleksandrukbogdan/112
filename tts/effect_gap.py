# -*- coding: utf-8 -*-
"""Когда один и тот же wav можно включить снова.

В начале диалога эффект звучит один раз, с начала файла, без зацикливания.
Следующий запуск — не раньше чем через GAP_SEC секунд речи этого эффекта.
Короткий файл (одышка, хлопок, лай) повторяется, только если он целиком
помещается в оставшийся кусок реплики. Длинный фон в одну реплику не
влезает: его снова пускают с начала, если в реплике ещё есть речь.
"""
from __future__ import annotations

GAP_SEC = 30.0
# До этой длины повтор ждёт реплику, в которую файл входит целиком.
FULL_FIT_LIMIT = 12.0
# Длиннее — достаточно пары секунд, чтобы было слышно начало.
MIN_PARTIAL_SEC = 2.0


def repeat_fits(room: float, effect_dur: float) -> bool:
    if effect_dur <= FULL_FIT_LIMIT:
        return effect_dur <= room + 0.05
    return room >= MIN_PARTIAL_SEC


def plan_effect_starts(
    cursor: float,
    utter_dur: float,
    effect_dur: float,
    next_at: float,
) -> tuple[list[float], float, float]:
    """Смещения внутри реплики, новый курсор диалога и время следующего запуска.

    cursor — сколько секунд речи этого эффекта уже прошло.
    next_at — с какой секунды диалога эффект снова разрешён. 0 значит «ещё не играл».
    """
    if utter_dur <= 0.05:
        return [], cursor, next_at
    end = cursor + utter_dur
    starts: list[float] = []
    opening = next_at <= 0.001 and cursor <= 0.001
    t = cursor if next_at < cursor else next_at
    if opening:
        starts.append(0.0)
        t = GAP_SEC
    while t < end - 0.05:
        local = t - cursor
        room = utter_dur - local
        if repeat_fits(room, effect_dur):
            if not starts or abs(starts[-1] - local) > 0.2:
                starts.append(local)
            t += GAP_SEC
        else:
            break
    new_next = (cursor + starts[-1] + GAP_SEC) if starts else next_at
    return starts, end, new_next
