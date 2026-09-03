"""
Движок протокола опроса.

Один и тот же класс работает в ОБОИХ режимах:
  тренажёр — реплики заявителя генерирует LLM
  помощник — реплики заявителя приходят из ASR живого вызова

Различие только в источнике реплик. Логика «что уже спросили,
что осталось, что подсказать» — общая.

Класс НИЧЕГО не знает о конкретных вопросах: всё из config/protocols/.
Придёт «Алгоритм опроса по видам происшествий» — заменим YAML.

Подход к отслеживанию заданных вопросов опирается на идею из работы
Corti «MultiQT: Multimodal Learning for Real-Time Question Tracking
in Speech» (arXiv:2005.00812): по ходу вызова размечать, какие вопросы
протокола уже прозвучали. Здесь реализована лексическая версия;
место под ML-классификатор оставлено в methode `_pohozh`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import config_loader as C


# ---------------------------------------------------------------- модели

@dataclass
class Vopros:
    id: str
    text: str
    field: str | None = None
    ves: int = 2
    opt: bool = False
    blok: str = ""
    obosnovanie: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "field": self.field,
                "ves": self.ves, "opt": self.opt, "blok": self.blok,
                "obosnovanie": self.obosnovanie}


@dataclass
class Hint:
    """Подсказка оператору. Каждая обязательно логируется."""
    id: str
    kind: str            # question | field | timing | risk | draft | rule
    text: str
    ves: int = 2
    field: str | None = None
    obosnovanie: str = ""
    istochnik: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "text": self.text,
                "ves": self.ves, "field": self.field,
                "obosnovanie": self.obosnovanie, "istochnik": self.istochnik}


@dataclass
class Sostoyanie:
    """Что уже известно к текущему моменту вызова."""
    zadannye: set[str] = field(default_factory=set)      # id заданных вопросов
    zapolnennye: dict[str, Any] = field(default_factory=dict)
    kontekst: set[str] = field(default_factory=set)      # флаги обстановки
    repliki: list[tuple[str, str, int]] = field(default_factory=list)
    nachalo_ms: int = 0
    sluzhby: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- движок

class ProtocolEngine:
    def __init__(self, protokol: str = "_base", tip_proisshestviya: str | None = None):
        self.protokol_name = protokol          # нужно для сериализации в Redis
        self.p = C.protocol(protokol)
        self.norm = C.normativy()
        self.tip = tip_proisshestviya
        self.s = Sostoyanie(nachalo_ms=int(time.time() * 1000))

    # ------------------------------------------------------------ наблюдение

    def observe(self, tekst: str, kto: str, t_ms: int | None = None) -> None:
        """Скармливаем реплику. Движок сам решает, что из неё следует."""
        t_ms = t_ms if t_ms is not None else self._proshlo_ms()
        self.s.repliki.append((kto, tekst, t_ms))
        low = tekst.lower()

        if kto == "operator":
            for v in self._vse_voprosy():
                if v.id not in self.s.zadannye and self._pohozh(low, v):
                    self.s.zadannye.add(v.id)

        if kto in ("caller", "zayavitel"):
            self._obnovit_kontekst(low)

    def zapolnit(self, field_key: str, value: Any) -> None:
        if value not in (None, "", []):
            self.s.zapolnennye[field_key] = value
            self._pereschitat_kontekst_po_polyam()

    def _pohozh(self, replika_low: str, v: Vopros) -> bool:
        """
        Прозвучал ли вопрос. Сейчас — по ключевым словам из текста вопроса.

        МЕСТО ПОД УЛУЧШЕНИЕ: здесь должен стоять мультимодальный
        классификатор (аудио + расшифровка), как в Corti MultiQT.
        Интерфейс менять не придётся — только тело метода.
        """
        slova = [w for w in re.findall(r"[а-яёa-z]{4,}", v.text.lower())
                 if w not in ("пожалуйста", "скажите", "какой", "какая", "какие")]
        if not slova:
            return False
        popal = sum(1 for w in slova if w[:5] in replika_low)
        return popal >= max(1, len(slova) // 3)

    def _obnovit_kontekst(self, low: str) -> None:
        """Флаги обстановки — включают условные ветки протокола."""
        pravila = {
            "net_tochnogo_adresa": [r"не знаю.{0,15}(адрес|улиц|дом|где)",
                                    r"не могу.{0,10}(сказать|назвать).{0,15}адрес",
                                    r"(какой|какая)-то (дом|улиц)", "без понятия",
                                    "не помню адрес", "адрес не знаю"],
            "perekrestok": ["перекрёст", "перекрест", "угол улиц", "пересечен"],
            "podval_ili_cherdak": ["подвал", "чердак", "цоколь"],
            "chastny_dom": ["частный дом", "свой дом", "коттедж", "дача", "барак"],
            "dtp_na_trasse": ["трасс", "шоссе", "автодорог", "километр"],
            "massovoe_prebyvanie": ["торговый центр", "школ", "детский сад",
                                    "больниц", "вокзал", "кинотеатр", "стадион",
                                    "много людей", "полно народу"],
            # Приложение № 11: повышенная возбуждённость или несвязная речь —
            # основание уточнить необходимость психологической поддержки.
            "vozbuzhdennost": ["!!!", "боже мой", "помогите помогите",
                               "скорее скорее", "быстрее быстрее", "умоляю"],
            "nesvyaznaya_rech": ["я не могу говорить", "не могу дышать",
                                 "мне плохо", r"\.{4,}", r"(а-а|о-о|ы-ы)"],
        }
        for flag, marks in pravila.items():
            for m in marks:
                # шаблоны с метасимволами трактуем как регулярные выражения
                if any(c in m for c in ".{}|()[]*+?"):
                    if re.search(m, low):
                        self.s.kontekst.add(flag); break
                elif m in low:
                    self.s.kontekst.add(flag); break

    def _pereschitat_kontekst_po_polyam(self) -> None:
        z = self.s.zapolnennye
        if z.get("uroven") in ("podval", "cherdak"):
            self.s.kontekst.add("podval_ili_cherdak")
        if z.get("doroga") or z.get("kilometr"):
            self.s.kontekst.add("dtp_na_trasse")
        if z.get("perekrestok"):
            self.s.kontekst.add("perekrestok")
        if z.get("yazyk") and z["yazyk"] != "ru":
            self.s.kontekst.add("inoy_yazyk")

    # ------------------------------------------------------------ вопросы

    def _blok(self, name: str) -> list[Vopros]:
        out = []
        for q in self.p.get(name, []) or []:
            out.append(Vopros(id=q["id"], text=q["text"], field=q.get("field"),
                              ves=q.get("ves", 2), opt=q.get("opt", False),
                              blok=name, obosnovanie=q.get("obosnovanie", "")))
        return out

    def _vse_voprosy(self) -> list[Vopros]:
        v = []
        vh = self.p.get("vhod", {}).get("pervy_vopros")
        if vh:
            v.append(Vopros(id=vh["id"], text=vh["text"],
                            field=vh.get("zapolnyaet"), ves=vh.get("ves", 3),
                            blok="vhod"))
        for b in ("adres", "zayavitel", "proisshestvie"):
            v += self._blok(b)
        v += self._uslovnye_vse()
        return v

    def _uslovnye_vse(self) -> list[Vopros]:
        out = []
        for u in self.p.get("usloviya", []) or []:
            out.append(Vopros(id=u["id"], text=u["text"], field=u.get("field"),
                              ves=u.get("ves", 2), blok="usloviya",
                              obosnovanie=u.get("obosnovanie", "")))
        return out

    def _uslovie_aktivno(self, vyrazhenie: str) -> bool:
        """Мини-интерпретатор выражений вида 'a or b' и 'tip in [X, Y]'."""
        e = vyrazhenie.strip()
        if " or " in e:
            return any(self._uslovie_aktivno(p) for p in e.split(" or "))
        if " and " in e:
            return all(self._uslovie_aktivno(p) for p in e.split(" and "))
        m = re.match(r"tip in \[(.+)\]", e)
        if m:
            return self.tip in [x.strip() for x in m.group(1).split(",")]
        if "!=" in e:
            left, right = [x.strip() for x in e.split("!=")]
            # Незаполненное поле — это НЕ «отличается от». Берём значение
            # по умолчанию из ukio.yaml, иначе правило про переводчика
            # срабатывало бы на каждом вызове с первой секунды.
            znach = self.s.zapolnennye.get(left)
            if znach in (None, ""):
                znach = self._default_polya(left)
            if znach in (None, ""):
                return False
            return str(znach) != right.strip("'\" ")
        return e in self.s.kontekst

    def _default_polya(self, key: str) -> Any:
        """Значение по умолчанию поля из ukio.yaml."""
        u = C.ukio()
        for sekciya in ("uchol", "obshchaya"):
            for group in u[sekciya].values():
                for f in group:
                    if f.get("key") == key:
                        return f.get("default")
        return None

    def nezadannye(self) -> list[Vopros]:
        """Обязательные вопросы, которые ещё не прозвучали."""
        out = []
        for v in self._vse_voprosy():
            if v.blok == "usloviya":
                continue
            if v.opt or v.id in self.s.zadannye:
                continue
            if v.field and self.s.zapolnennye.get(v.field):
                continue
            out.append(v)
        return sorted(out, key=lambda x: -x.ves)

    def uslovnye_aktivnye(self) -> list[Vopros]:
        """Условные вопросы, которые сработали по обстановке."""
        out = []
        for u in self.p.get("usloviya", []) or []:
            if not self._uslovie_aktivno(u.get("esli", "")):
                continue
            if u["id"] in self.s.zadannye:
                continue
            if u.get("field") and self.s.zapolnennye.get(u["field"]):
                continue
            out.append(Vopros(id=u["id"], text=u["text"], field=u.get("field"),
                              ves=u.get("ves", 3), blok="usloviya",
                              obosnovanie=u.get("obosnovanie", "")))
        return out

    # ------------------------------------------------------------ подсказки

    def _proshlo_ms(self) -> int:
        return int(time.time() * 1000) - self.s.nachalo_ms

    def podskazki(self, t_ms: int | None = None, limit: int = 5) -> list[Hint]:
        """
        Что показать оператору прямо сейчас.
        Порядок: правила по обстановке → условные вопросы → таймер → незаданные.
        """
        t_ms = t_ms if t_ms is not None else self._proshlo_ms()
        sec = t_ms / 1000
        norm = self.norm["opros_i_kartochka_sec"]
        por = self.norm["porogi_podskazok"]
        h: list[Hint] = []

        # 1. Переключения — приложение № 11, это правило, а не совет
        for per in self.p.get("perekluchenie", []) or []:
            if self._uslovie_aktivno(per.get("trigger", "")):
                h.append(Hint(
                    id=per["id"], kind="rule", ves=3,
                    text=(per.get("snachala_utochnit")
                          or f"Переключить на: {per['deystvie']}"),
                    obosnovanie=per.get("obosnovanie", "").strip(),
                    istochnik=self.p.get("istochnik", "")))

        # 2. Условные вопросы по обстановке
        for v in self.uslovnye_aktivnye():
            h.append(Hint(id=v.id, kind="question", text=v.text, ves=v.ves,
                          field=v.field, obosnovanie=v.obosnovanie,
                          istochnik=self.p.get("istochnik", "")))

        # 3. Таймер норматива
        if sec >= norm * por["prosrocheno"]:
            ost = [f["key"] for f in C.polya_karty()
                   if f.get("req") and not self.s.zapolnennye.get(f["key"])]
            h.append(Hint(id="t_prosrocheno", kind="timing", ves=3,
                          text=f"Норматив {norm} с превышен. Не заполнено: {len(ost)}",
                          istochnik="ПП РФ 1931 п.9 подп.р"))
        elif sec >= norm * por["trevoga"]:
            h.append(Hint(id="t_trevoga", kind="timing", ves=3,
                          text=f"До норматива {int(norm - sec)} с",
                          istochnik="ПП РФ 1931 п.9 подп.р"))
        elif sec >= norm * por["vnimanie"]:
            h.append(Hint(id="t_vnimanie", kind="timing", ves=1,
                          text=f"Прошло {int(sec)} с из {norm}",
                          istochnik="ПП РФ 1931 п.9 подп.р"))

        # 4. Незаданные обязательные
        for v in self.nezadannye()[:limit]:
            h.append(Hint(id=v.id, kind="question", text=v.text, ves=v.ves,
                          field=v.field, istochnik=self.p.get("istochnik", "")))

        h.sort(key=lambda x: -x.ves)
        return h[:limit]

    # ------------------------------------------------------------ готовность

    def gotovnost(self) -> dict:
        """Можно ли отправлять карточку в ДДС."""
        obyaz = C.obyazatelnye_polya()
        net = [k for k in obyaz if not self.s.zapolnennye.get(k)]
        vsego_v = [v for v in self._vse_voprosy() if not v.opt and v.blok != "usloviya"]
        return {
            "gotova": not net,
            "ne_zapolneno": net,
            "zapolneno_obyazatelnyh": len(obyaz) - len(net),
            "vsego_obyazatelnyh": len(obyaz),
            "zadano_voprosov": len(self.s.zadannye),
            "vsego_voprosov": len(vsego_v),
            "proshlo_sec": round(self._proshlo_ms() / 1000, 1),
            "normativ_sec": self.norm["opros_i_kartochka_sec"],
            "v_normativ": self._proshlo_ms() / 1000 <= self.norm["opros_i_kartochka_sec"],
        }

    def rekomendovannye_sluzhby(self) -> list[dict]:
        """Какие ДДС привлечь — по типу происшествия из конфигурации."""
        if not self.tip:
            return []
        rb = C.routes_by_kod()
        return [rb[k] for k in C.sluzhby_dlya_tipa(self.tip) if k in rb]
