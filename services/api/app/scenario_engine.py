"""
Движок сценария.

Ключевой принцип: заявитель раскрывает факт ТОЛЬКО когда оператор задал
нужный вопрос. Нераскрытые факты не попадают ни в промпт LLM, ни в ответ —
поэтому выдумать их невозможно.

Состояние живёт в памяти процесса (dict по session_id). События при этом
пишутся в Postgres, поэтому перезапуск теряет только активный звонок,
а всю историю и аналитику — нет.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any


def _emotion_for(stress: float) -> str:
    if stress >= 0.6:
        return "panic"
    if stress >= 0.3:
        return "stressed"
    return "calm"


@dataclass
class CallState:
    session_id: str
    scenario: dict
    started_at: float = field(default_factory=time.monotonic)

    stress: float = 0.5
    mood: str = "neutral"
    line_quality: float = 1.0
    dropped: bool = False
    confused_fields: set[str] = field(default_factory=set)

    revealed: set[str] = field(default_factory=set)      # fact_id
    intents_seen: dict[str, int] = field(default_factory=dict)  # intent -> t_ms первого раза
    card: dict[str, str] = field(default_factory=dict)   # заполненные поля карточки
    dispatched_services: list[str] = field(default_factory=list)
    dispatched_at_ms: int | None = None
    operator_turns: int = 0
    ended: bool = False

    def t_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


class ScenarioEngine:
    """Один экземпляр на процесс. Держит все активные звонки."""

    def __init__(self) -> None:
        self._calls: dict[str, CallState] = {}

    # ---------------------------------------------------------------- жизненный цикл

    def start(self, session_id: str, scenario: dict) -> CallState:
        st = CallState(
            session_id=session_id,
            scenario=scenario,
            stress=float(scenario.get("caller_profile", {}).get("baseline_stress", 0.5)),
        )
        self._calls[session_id] = st
        return st

    def get(self, session_id: str) -> CallState | None:
        return self._calls.get(session_id)

    def drop(self, session_id: str) -> None:
        self._calls.pop(session_id, None)

    # ---------------------------------------------------------------- реплика оператора

    def apply_intents(self, st: CallState, intents: list[str], t_ms: int) -> list[dict]:
        """
        Регистрирует интенты и возвращает факты, которые в результате раскрылись.
        Возвращает список dict факта (как в JSON сценария).
        """
        st.operator_turns += 1
        for i in intents:
            st.intents_seen.setdefault(i, t_ms)

        newly: list[dict] = []
        for fact in st.scenario.get("facts", []):
            if fact["id"] in st.revealed:
                continue
            if any(req in st.intents_seen for req in fact.get("reveal_if", [])):
                st.revealed.add(fact["id"])
                newly.append(fact)
        return newly

    def spontaneous(self, st: CallState, t_ms: int) -> list[dict]:
        """
        Факты, которые заявитель выкрикивает сам, если оператор не спросил вовремя.
        Спасает демо от зависания: сценарий двигает себя сам.
        """
        out: list[dict] = []
        for fact in st.scenario.get("facts", []):
            if fact["id"] in st.revealed:
                continue
            limit = fact.get("spontaneous_after_sec")
            if limit and t_ms >= limit * 1000:
                st.revealed.add(fact["id"])
                out.append(fact)
        return out

    # ---------------------------------------------------------------- реплика заявителя

    def caller_line(self, st: CallState, revealed: list[dict]) -> dict:
        """
        Собирает ответ заявителя.
        Если что-то раскрылось — озвучиваем факты. Иначе — заготовка по уровню стресса.
        Возвращает {"text", "emotion", "source", "fact_ids"}.
        """
        emotion = _emotion_for(st.stress)

        if revealed:
            parts, ids = [], []
            for fact in revealed:
                line = dict(fact.get("line") or {})
                text = line.get("text", "")
                # инъекция «путает адрес» портит значение при первой выдаче
                if fact.get("field") in st.confused_fields:
                    text = self._confuse(text)
                    st.confused_fields.discard(fact["field"])
                parts.append(text)
                ids.append(fact["id"])
            return {
                "text": " ".join(p for p in parts if p),
                "emotion": revealed[0].get("line", {}).get("emotion", emotion),
                "source": "scenario",
                "fact_ids": ids,
            }

        pool = st.scenario.get("fallback_lines", {}).get(emotion) or ["..."]
        if st.mood == "aggressive":
            pool = ["Вы вообще меня слушаете?! Сколько можно спрашивать!"] + pool
        return {
            "text": random.choice(pool),
            "emotion": emotion,
            "source": "fallback",
            "fact_ids": [],
        }

    @staticmethod
    def _confuse(text: str) -> str:
        return text.replace("двенадцать", "двадцать... нет, подождите, двенадцать")

    # ---------------------------------------------------------------- инъекции

    def inject(self, st: CallState, injection_id: str) -> dict | None:
        spec = next(
            (i for i in st.scenario.get("injections", []) if i["id"] == injection_id),
            None,
        )
        if not spec:
            return None
        eff: dict[str, Any] = spec.get("effect", {})

        if "stress" in eff:
            st.stress = min(1.0, st.stress + float(eff["stress"]))
        if "line_quality" in eff:
            st.line_quality = float(eff["line_quality"])
        if "mood" in eff:
            st.mood = str(eff["mood"])
        if "confuse_field" in eff:
            st.confused_fields.add(str(eff["confuse_field"]))
        if eff.get("drop"):
            st.dropped = True

        return spec

    # ---------------------------------------------------------------- карточка

    def set_card_field(self, st: CallState, field_name: str, value: str) -> bool:
        st.card[field_name] = value
        truth = st.scenario.get("card_truth", {}).get(field_name)
        if truth is None:
            return True
        return self._loose_equal(str(truth), str(value))

    @staticmethod
    def _loose_equal(a: str, b: str) -> bool:
        norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
        na, nb = norm(a), norm(b)
        return bool(na) and (na in nb or nb in na)

    def dispatch(self, st: CallState, services: list[str], t_ms: int) -> bool:
        st.dispatched_services = services
        st.dispatched_at_ms = t_ms
        return sorted(services) == sorted(st.scenario.get("services", []))

    # ---------------------------------------------------------------- живые метрики

    def live_metrics(self, st: CallState) -> dict:
        t = st.t_ms()
        timing = st.scenario.get("timing", {})
        norm = timing.get("time_to_dispatch_sec", 75) * 1000
        total_facts = len(st.scenario.get("facts", []))
        return {
            "t_ms": t,
            "norm_ms": norm,
            "within_norm": (st.dispatched_at_ms or t) <= norm,
            "facts_revealed": len(st.revealed),
            "facts_total": total_facts,
            "operator_turns": st.operator_turns,
            "info_efficiency": round(len(st.revealed) / st.operator_turns, 2)
            if st.operator_turns
            else 0.0,
            "stress": round(st.stress, 2),
            "ask_address_ms": st.intents_seen.get("asked_address"),
            "ask_victims_ms": st.intents_seen.get("asked_victims"),
            "dispatched_at_ms": st.dispatched_at_ms,
            "checklist": {
                c["id"]: c["id"] in st.intents_seen
                for c in st.scenario.get("checklist", [])
            },
        }


engine = ScenarioEngine()
