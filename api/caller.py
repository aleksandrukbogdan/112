"""
Заявитель.

Ключевая механика: точный адрес из эталона выдаётся ТОЛЬКО при
прямом уточняющем вопросе. Пока оператор не спросил — заявитель
называет ориентир, как в реальном вызове.

Без LLM работает на правилах: тренажёр остаётся функциональным,
если внешняя модель недоступна.
"""
from __future__ import annotations

import json
import re

import httpx

from . import config as C, facts

SYSTEM = """Ты играешь роль ЗАЯВИТЕЛЯ, который звонит по номеру 112 в Москве.

ТВОЯ СИТУАЦИЯ: {situaciya}
ТЕБЯ ЗОВУТ: {fio}
ТВОЙ ТЕЛЕФОН: {telefon}
ГДЕ ТЫ НАХОДИШЬСЯ (как ты сам это описываешь): {adres_vidimy}
{utochnenie}

ПРАВИЛА:
1. Ты обычный человек в стрессе, не диспетчер. Говори короткими фразами.
2. Отвечай ТОЛЬКО на то, о чём спросили. Не выдавай всё сразу.
3. Имя и телефон называй только когда спросят.
4. Уровень волнения: {stress} из 5. Чем выше, тем сбивчивее речь.
5. Никогда не выходи из роли, не упоминай, что ты модель.
6. Ответ — одна-две короткие фразы, без кавычек и пояснений.

Если оператор груб или молчит — можешь занервничать сильнее."""

UTOCH = """7. ВАЖНО: точный адрес — «{adres_etalon}». Ты НЕ называешь его сам.
   Ты говоришь только ориентир. Точный адрес сообщаешь ТОЛЬКО если
   оператор прямо попросит уточнить: спросит номер дома, строение,
   корпус, километр, ближайший адрес или как проехать."""

# Вопросы, которые считаются уточнением адреса
UTOCH_RE = re.compile(
    r"(уточн|точн\w*\s+адрес|номер дома|какой дом|дом\s*№|строени|корпус|"
    r"километр|ближайш|как проехать|как подъехать|ориентир|адрес\s*\?|"
    r"где именно|поточнее|назовите адрес)", re.I)


def est_utochnenie(tekst: str) -> bool:
    return bool(UTOCH_RE.search(tekst or ""))


def _bez_llm(bilet: dict, vopros: str, raskryt: bool) -> str:
    """Запасной заявитель на правилах."""
    v = (vopros or "").lower()
    z = bilet["zayavitel"]
    if raskryt:
        return f"Сейчас… точнее это {bilet['adres_etalon']}."
    if re.search(r"(фамили|как вас зовут|представ|ваше имя)", v):
        return f"{z['fio']}."
    if re.search(r"(телефон|номер для связи|обратн)", v):
        return f"{z['telefon']}."
    if re.search(r"(где|адрес|место|куда)", v):
        return bilet["adres_vidimy"]
    if re.search(r"(что случ|что произ|что у вас|слушаю)", v):
        return bilet["situaciya"]
    if re.search(r"(пострадав|раненые|люди|кто-нибудь)", v):
        return {"net":"Пострадавших нет.","est":"Есть пострадавшие.","neizvestno":"Точных сведений о пострадавших нет."}[bilet.get("victims") or facts.victims(bilet["situaciya"]) or "neizvestno"]
    return "Да, всё так. Приезжайте скорее."


async def otvet(bilet: dict, istoriya: list[dict], vopros: str,
                stress: int = 3) -> str:
    """Реплика заявителя. Раскрытие адреса — только при уточнении."""
    raskryt = bilet["trebuet_utochneniya"] and est_utochnenie(vopros)

    if re.search(r"адрес|дом|улиц|корпус|телефон|номер для связи|представ|как вас зовут|фамили|пострадав", vopros, re.I):
        return _bez_llm(bilet,vopros,raskryt)
    if not C.LLM_ON:
        return _bez_llm(bilet, vopros, raskryt)

    ut = ""
    if bilet["trebuet_utochneniya"]:
        ut = "Точный адрес пока не уточнён. Называй только приведённое описание места."
        if raskryt:
            ut += "\n   ОПЕРАТОР СЕЙЧАС СПРОСИЛ УТОЧНЕНИЕ — назови точный адрес."

    sys = SYSTEM.format(
        situaciya=bilet["situaciya"],
        fio=bilet["zayavitel"]["fio"],
        telefon=bilet["zayavitel"]["telefon"],
        adres_vidimy=bilet["adres_vidimy"],
        utochnenie=ut,
        stress=stress,
    )
    msgs = [{"role": "system", "content": sys}]
    for h in istoriya[-8:]:
        msgs.append({"role": "assistant" if h["kto"] == "caller" else "user",
                     "content": h["tekst"]})
    msgs.append({"role": "user", "content": vopros})

    try:
        async with httpx.AsyncClient(timeout=25.0) as cl:
            r = await cl.post(f"{C.LLM_BASE}/chat/completions", json={
                "model": C.LLM_MODEL, "messages": msgs,
                "temperature": 0.8, "max_tokens": 120,
            })
            r.raise_for_status()
            t = r.json()["choices"][0]["message"]["content"].strip()
            return re.sub(r'^["«]|["»]$', "", t).strip() or _bez_llm(bilet, vopros, raskryt)
    except Exception:
        return _bez_llm(bilet, vopros, raskryt)


async def zdorov() -> dict:
    if not C.LLM_ON:
        return {"ok": None, "note": "выключен, работает запасной режим"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as cl:
            r = await cl.get(f"{C.LLM_BASE}/models")
            ids = [m["id"] for m in r.json().get("data", [])]
            return {"ok": C.LLM_MODEL in ids, "modeli": ids,
                    "nastroeno": C.LLM_MODEL,
                    "note": None if C.LLM_MODEL in ids
                            else f"LLM_MODEL={C.LLM_MODEL} не совпадает с --served-model-name"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
