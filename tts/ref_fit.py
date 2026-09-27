# -*- coding: utf-8 -*-
"""Текст эталона не должен быть длиннее записи.

Иначе модель дочитывает карточку голоса уже в той части, которую слышит ученик.
"""
from __future__ import annotations

import re


def fit_ref_text(text: str, audio_sec: float) -> str:
    spoken = re.sub(r"\s+", " ", (text or "")).strip()
    if not spoken:
        return spoken
    span = min(max(float(audio_sec or 0), 0.5), 12.0)
    budget = max(24, int(span * 15))
    if len(spoken) <= budget:
        return spoken
    cut = spoken[:budget]
    space = cut.rfind(" ")
    if space >= 16:
        cut = cut[:space]
    cut = cut.rstrip(" ,;:-—")
    if cut and cut[-1] not in ".!?":
        cut += "."
    return cut
