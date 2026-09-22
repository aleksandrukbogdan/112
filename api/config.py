"""
Конфигурация тренажёра.

Принцип: всё нормативное — в этом модуле и в data/, ничего в бизнес-логике.
Меняется норматив — правится здесь, код не трогается.
"""
import json
import os
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/app/data"))

LLM_BASE = os.environ.get("LLM_BASE_URL", "http://host.docker.internal:1212/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen3-VL")
LLM_ON = os.environ.get("LLM_ENABLED", "true").lower() == "true"

# --- Нормативы. ПП РФ от 12.11.2021 № 1931, п. 9 подп. «р» ---
NORM = {
    "otvet_sredniy_sec": 8,
    "otvet_max_sec": 20,
    "kartochka_sec": 75,
    "podtverzhdenie_dds_sec": 30,
    "obratny_vyzov_sec": 10,
    "obratny_vyzov_popytok": 3,
    "konsultaciya_sec": 120,
    "psiholog_sec": 1800,
    "hranenie_let": 3,
    "istochnik": "ПП РФ 1931, п. 9 подп. «р» · форма 1/112 приказа МЧС № 192",
}

# ТЗ ДГОЧСиПБ: «Устанавливать временные рамки… По умолчанию значение — 30 сек»
UCHEBNY_TAYMING_SEC = int(os.environ.get("UCHEBNY_TAYMING_SEC", "30"))

# --- Правила речи. Источник: методика МЧС России ---
RECH = {
    "chastica_ne": {
        "ves": 2,
        "opisanie": "Избегать частицу «не» в побуждениях",
        "obrazcy": ["не паникуйте", "не волнуйтесь", "не кричите", "не переживайте",
                    "не бойтесь", "не отключайтесь", "не нервничайте"],
        "zamena": {
            "не паникуйте": "успокойтесь, слушайте меня",
            "не волнуйтесь": "я с вами, отвечайте на вопросы",
            "не кричите": "говорите спокойнее, я вас слышу",
            "не бойтесь": "я рядом, помощь уже идёт",
            "не отключайтесь": "оставайтесь на линии",
        },
    },
    "katastrofizatory": {
        "ves": 3,
        "opisanie": "Исключить слова «паника», «катастрофа», «ужас»",
        "obrazcy": ["паника", "паникуйте", "катастрофа", "ужас", "ужасно",
                    "кошмар", "трагедия", "чудовищно", "всё пропало"],
    },
    "neprofessionalno": {
        "ves": 3,
        "opisanie": "Без сленга, жаргона, раздражения (APCO/NENA 1.107.2-2025)",
        "obrazcy": ["ну и что", "я же сказал", "вы меня не слушаете", "сколько можно",
                    "успокойтесь уже", "это не ко мне", "я не обязан", "перезвоните позже"],
    },
}

# --- Направления реагирования (колонки классификатора + МВД) ---
ROUTES = [
    {"kod": "101", "nazvanie": "Пожарно-спасательная служба", "korotko": "101",
     "hotkey": "1", "color": "#ef4444"},
    {"kod": "ODS_PSC", "nazvanie": "ОДС ПСЦ", "korotko": "ОДС ПСЦ",
     "hotkey": "2", "color": "#f97316"},
    {"kod": "SMP", "nazvanie": "Скорая медицинская помощь", "korotko": "СМП",
     "hotkey": "3", "color": "#22c55e"},
    {"kod": "MGPSS", "nazvanie": "МГПСС", "korotko": "МГПСС",
     "hotkey": "4", "color": "#3b82f6"},
    {"kod": "DS112", "nazvanie": "Дежурная служба АРМ-112", "korotko": "ДС-112",
     "hotkey": "5", "color": "#a855f7"},
    {"kod": "MVD", "nazvanie": "Полиция", "korotko": "Полиция",
     "hotkey": "6", "color": "#6366f1"},
]
ROUTES_BY_KOD = {r["kod"]: r for r in ROUTES}

# --- Навыки для профиля и прогноза ---
SKILLS = [
    {"kod": "adres", "nazvanie": "Адрес", "opisanie": "Довести адрес до точного"},
    {"kod": "tip", "nazvanie": "Тип происшествия", "opisanie": "Верная классификация"},
    {"kod": "sluzhby", "nazvanie": "Службы", "opisanie": "Верный состав ДДС"},
    {"kod": "tayming", "nazvanie": "Тайминг", "opisanie": "Уложиться в норматив"},
    {"kod": "rech", "nazvanie": "Речь", "opisanie": "Методика МЧС"},
    {"kod": "polnota", "nazvanie": "Полнота", "opisanie": "Обязательные поля карточки"},
]

_cache: dict = {}


def load(name: str):
    if name not in _cache:
        _cache[name] = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return _cache[name]


def svodka() -> dict:
    tree, idx, bil = load("tree"), load("index"), load("bilety")
    man = load("manifest")
    return {
        "region": "Москва",
        "operator": "ГБУ «Система 112»",
        "zakazchik": "Департамент ГОЧСиПБ города Москвы",
        "klassifikator": {
            "versiya": "0.46.24",
            "grupp": len(tree),
            "poziciy": len(idx),
            "sha256": man.get("classifier_v046.24.xlsx", "")[:16],
        },
        "bilety": {
            "vsego": len(bil),
            "s_utochneniem": sum(1 for b in bil if b["trebuet_utochneniya"]),
        },
        "normativ_sec": NORM["kartochka_sec"],
        "uchebny_tayming_sec": UCHEBNY_TAYMING_SEC,
        "marshrutov": len(ROUTES),
        "sluzhb": len(load("sluzhby")),
        "chto_sluchilos": len(load("chto_sluchilos")),
        "llm": {"on": LLM_ON, "model": LLM_MODEL},
    }


# ================================================================ режим «Диспетчер ДДС»
# Основание — ответы заказчика на вопросы (сентябрь 2026):
#  • система «поставляет» диспетчеру ДДС готовую карточку от оператора 112;
#  • у ДДС есть доступ к записи исходного разговора (п. 1.1 — «Да»),
#    поэтому ошибки оператора 112 в карточке проверяемы;
#  • бригады диспетчер выбирает вручную (п. 1.2), связь — IP-телефон (п. 1.3, 2);
#  • статусы проставляет только диспетчер ДДС (п. 1.5), по факту получения информации;
#  • алгоритм действий при ошибке в карточке — универсальный (п. 3).
# Всё ниже — настройки: меняются без правки кода.

DDS = {
    "podtverzhdenie_sec": 30,          # ПП 1931 п.9 «р»: подтверждение приёма ДДС
    "svoevremenno_sec": 60,            # учебный лимит: статус не позже N с после основания
    "veroyatnost_oshibki": 0.7,        # доля карточек, в которые система вносит ошибку оператора 112
    # этапы работы бригады после выезда, секунды (сжатое учебное время)
    "etapy_sec": {"pribytie": 45, "raboty": 25, "zaversheno": 50},
    "vhodyashchiy_zvonok_zhdat_sec": 25,   # сколько звонит входящий, потом «пропущен»
    # статусы службы. osnovanie — событие, после которого статус допустим
    "statusy": [
        {"kod": "prinyata", "nazvanie": "Принята", "osnovanie": "postuplenie", "kommentariy": True},
        {"kod": "ne_prinyata", "nazvanie": "Не принята", "osnovanie": "ne_kompetenciya", "kommentariy": True},
        {"kod": "nachalo", "nazvanie": "Начало реагирования", "osnovanie": "vyezd", "kommentariy": False},
        {"kod": "pribytie", "nazvanie": "Прибытие", "osnovanie": "pribytie", "kommentariy": False},
        {"kod": "raboty", "nazvanie": "Проведение работ", "osnovanie": "raboty", "kommentariy": False},
        {"kod": "zaversheno", "nazvanie": "Работы завершены", "osnovanie": "zaversheno", "kommentariy": True},
        {"kod": "otkaz", "nazvanie": "Отказ от выполнения работ", "osnovanie": "otkaz", "kommentariy": True},
    ],
    # какие ошибки оператора 112 может содержать карточка
    "oshibki": ["dom", "telefon", "postradavshie", "korpus"],
    "istochnik_statusov": "Памятка ГБУ «Система 112» для ДДС: статусы — по факту получения информации",
}

# Службы, которые 112 назначает по группе происшествия (для учебной карточки).
# Упрощение до получения опросных карт по каждому сценарию от заказчика.
GRUPPA_SLUZHBY = {
    "1": ["Служба 101"], "2": ["Служба 101", "Служба 102", "Служба 103"],
    "4": ["Служба 102", "ФСБ"], "6": ["Служба 101", "Деп. ЖКХ"],
    "12": ["Служба 101", "Служба 102", "Служба 103"], "13": ["Служба 104"],
    "14": ["Деп. ЖКХ"], "15": ["Служба 102"], "17": ["Служба 101", "Служба 103"],
    "18": ["Служба 102", "Служба 103"], "19": ["Служба 102"], "22": ["Служба 103"],
    "23": ["Служба 102"],
}

# переопределение этапов через окружение: DDS_ETAPY_SEC="45,25,50"
if os.environ.get("DDS_ETAPY_SEC"):
    _a, _b, _c = (int(x) for x in os.environ["DDS_ETAPY_SEC"].split(","))
    DDS["etapy_sec"] = {"pribytie": _a, "raboty": _b, "zaversheno": _c}
if os.environ.get("DDS_VEROYATNOST_OSHIBKI"):
    DDS["veroyatnost_oshibki"] = float(os.environ["DDS_VEROYATNOST_OSHIBKI"])

SKILLS_DDS = [
    {"kod": "podtverzhdenie", "nazvanie": "Подтверждение", "opisanie": "Принять карточку за 30 с"},
    {"kod": "proverka", "nazvanie": "Проверка карточки", "opisanie": "Найти ошибку по записи"},
    {"kod": "peredacha", "nazvanie": "Передача бригаде", "opisanie": "Полно передать сведения"},
    {"kod": "statusy", "nazvanie": "Статусы по факту", "opisanie": "Статус только после основания"},
]
SKILLS = SKILLS + SKILLS_DDS
