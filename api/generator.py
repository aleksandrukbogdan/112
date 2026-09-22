"""
Генератор учебных сценариев.

ТЗ: «настройка нейросети на генерацию конкретных типов происшествий»,
«автоматическое формирование эталонных ответов», «подтверждение
преподавателем». Поэтому генератор делает только ЧЕРНОВИК.
К обучающемуся сценарий попадает после утверждения.

Эталон типа и служб берётся из классификатора, а не из модели:
модель придумывает только обстоятельства и речь заявителя.
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid

import httpx

from . import config as C

ULICY = [
    ("ул. Тверская", "Тверской"), ("Ленинский проспект", "Гагаринский"),
    ("ул. Профсоюзная", "Академический"), ("Варшавское шоссе", "Нагорный"),
    ("ул. Байкальская", "Гольяново"), ("Каширское шоссе", "Москворечье-Сабурово"),
    ("ул. Академика Янгеля", "Чертаново Центральное"), ("проспект Мира", "Мещанский"),
    ("ул. Новый Арбат", "Арбат"), ("Щёлковское шоссе", "Восточный"),
    ("ул. Братиславская", "Марьино"), ("Рязанский проспект", "Рязанский"),
    ("ул. Митинская", "Митино"), ("Волгоградский проспект", "Кузьминки"),
    ("ул. Сущёвский Вал", "Марьина Роща"), ("Кутузовский проспект", "Дорогомилово"),
]
ORIENTIRY = [
    "рядом с остановкой автобуса", "напротив торгового центра", "у входа в метро",
    "возле детской площадки", "за продуктовым магазином", "около школы",
    "напротив аптеки", "у шлагбаума во двор",
]
IMENA = [
    "Смирнова Ольга Петровна", "Кузнецов Андрей Викторович", "Попова Мария Сергеевна",
    "Васильев Дмитрий Олегович", "Новикова Елена Ивановна", "Фёдоров Игорь Павлович",
    "Морозова Анна Алексеевна", "Волков Сергей Николаевич",
]


def _telefon() -> str:
    return f"9{random.randint(10, 99)}-{random.randint(100, 999)}-" \
           f"{random.randint(10, 99)}-{random.randint(10, 99)}"


def _shablon(poz: dict, slozhnost: int, utochnenie: bool) -> dict:
    ul, _ = random.choice(ULICY)
    dom = random.randint(1, 140)
    etalon = f"Москва, {ul}, дом {dom}"
    if random.random() < 0.4:
        etalon += f", корп. {random.randint(1, 4)}"
    vidimy = f"Москва, {ul}, {random.choice(ORIENTIRY)}" if utochnenie else etalon
    tip = poz["itog"] or poz["pr1"]
    detali = " ".join(x for x in (poz["pr1"], poz["pr2"], poz["pr3"]) if x and x != "—")
    return {
        "situaciya": f"{tip[:1].upper()}{tip[1:]}. {detali}."
                     + (" Заявитель взволнован, говорит сбивчиво." if slozhnost >= 4 else ""),
        "zayavitel": {"fio": random.choice(IMENA), "telefon": _telefon()},
        "adres_vidimy": vidimy,
        "adres_etalon": etalon,
    }


PROMPT = """Составь учебный вызов на номер 112 в Москве.
Тип происшествия (строго): {tip}
Признаки: {priznaki}
Сложность: {slozhnost} из 5
{ut}
Верни ТОЛЬКО JSON без пояснений:
{{"situaciya": "2-3 предложения, что происходит, со слов заявителя",
  "fio": "ФИО заявителя",
  "telefon": "9XX-XXX-XX-XX",
  "adres_vidimy": "как заявитель описывает место",
  "adres_etalon": "точный адрес в Москве: улица, дом, при необходимости корпус"}}"""


async def sgenerirovat(kod: str, slozhnost: int = 3, utochnenie: bool = True,
                       avtor: int | None = None) -> dict:
    poz = C.load("index").get(kod)
    if not poz:
        raise ValueError("нет такого кода в классификаторе")

    data = None
    istochnik = "шаблон"
    if C.LLM_ON:
        ut = ("Заявитель НЕ знает точного адреса: в adres_vidimy только ориентир, "
              "точный адрес — в adres_etalon." if utochnenie
              else "adres_vidimy и adres_etalon совпадают.")
        pr = ", ".join(x for x in (poz["pr1"], poz["pr2"], poz["pr3"]) if x and x != "—")
        try:
            async with httpx.AsyncClient(timeout=40) as cl:
                r = await cl.post(f"{C.LLM_BASE}/chat/completions", json={
                    "model": C.LLM_MODEL, "temperature": 0.9, "max_tokens": 400,
                    "messages": [{"role": "user", "content": PROMPT.format(
                        tip=poz["itog"], priznaki=pr, slozhnost=slozhnost, ut=ut)}]})
                r.raise_for_status()
                txt = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", txt, re.S)
                j = json.loads(m.group(0))
                data = {"situaciya": j["situaciya"],
                        "zayavitel": {"fio": j["fio"], "telefon": j["telefon"]},
                        "adres_vidimy": j["adres_vidimy"],
                        "adres_etalon": j["adres_etalon"]}
                istochnik = f"модель {C.LLM_MODEL}"
        except Exception:
            data = None
    if data is None:
        data = _shablon(poz, slozhnost, utochnenie)

    sid = f"gen_{uuid.uuid4().hex[:8]}"
    return {
        "id": sid, **data,
        "trebuet_utochneniya": data["adres_vidimy"].strip() != data["adres_etalon"].strip(),
        "gruppa": poz["g"], "kod_etalon": kod, "tip_etalon": poz["itog"],
        "pr1_etalon": poz["pr1"],
        "slozhnost": slozhnost, "bilet": None, "vyzov": None,
        "sgenerirovan": {"istochnik": istochnik, "ts": time.time(), "avtor": avtor},
    }
