"""
Протокол разбора учебного вызова в PDF.

Зачем: аттестационная комиссия работает с бумагой, а нормативные требования
предполагают итоговую аттестацию с выводом результатов контроля в баллах.
Экран для этого не годится — нужен подписываемый документ.

Кириллица: встроенные шрифты reportlab её не содержат, поэтому регистрируем
DejaVu (ставится в контейнер пакетом fonts-dejavu-core).
"""
from __future__ import annotations

import io
import logging
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

log = logging.getLogger("report_pdf")

NORM_SEC = 75

INK = colors.HexColor("#16202B")
MUTED = colors.HexColor("#5E6E7F")
FAINT = colors.HexColor("#93A2B2")
LINE = colors.HexColor("#DCE3EA")
GREEN = colors.HexColor("#4E9E7E")
RED = colors.HexColor("#D6453F")
AMBER = colors.HexColor("#E0913C")

FONT, FONT_B, FONT_M = "Body", "BodyBold", "Mono"

# Технические коды полей карточки -> как это называется в документе.
# Коды соответствуют составу карточки по ПП РФ № 1931.
FIELD_LABELS = {
    "common.zayavitel_fio": "ФИО заявителя",
    "common.yazyk_obshcheniya": "Язык общения",
    "common.ulica": "Улица и дом",
    "common.dom": "Номер дома",
    "common.podezd": "Подъезд",
    "common.etazh": "Этаж",
    "common.kvartira": "Квартира и подъезд",
    "common.tip_proisshestviya": "Тип происшествия",
    "common.chislo_postradavshih": "Число пострадавших",
    "common.ugroza_lyudyam": "Угроза людям",
    "fire.harakter": "Характер происшествия",
    "fire.obstoyatelstva": "Обстоятельства и объект",
    "fire.etazhnost": "Этажность",
    "fire.obekt_gazificirovan": "Объект газифицирован",
    "fire.vozmozhnost_evakuacii": "Возможность эвакуации",
    "fire.podezdnye_puti": "Подъездные пути",
    "med.povod": "Повод к вызову",
    "med.vozrast": "Возраст пострадавшего",
    "med.sposobnost_k_peredvizheniyu": "Способность к передвижению",
}

_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
]

_fonts_ready = False


def _register_fonts() -> bool:
    """Ищет шрифт с кириллицей. Без него документ будет из пустых квадратов."""
    global _fonts_ready
    if _fonts_ready:
        return True
    for regular, bold, mono in _FONT_CANDIDATES:
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont(FONT, regular))
            pdfmetrics.registerFont(TTFont(FONT_B, bold if os.path.exists(bold) else regular))
            pdfmetrics.registerFont(TTFont(FONT_M, mono if os.path.exists(mono) else regular))
            _fonts_ready = True
            return True
    log.error("Шрифт с кириллицей не найден — установите fonts-dejavu-core")
    return False


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Normal"], fontName=FONT_B,
                                fontSize=15, leading=19, textColor=INK, alignment=TA_CENTER),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName=FONT,
                              fontSize=8.5, leading=12, textColor=MUTED, alignment=TA_CENTER),
        "h": ParagraphStyle("h", parent=base["Normal"], fontName=FONT_B,
                            fontSize=10.5, leading=14, textColor=INK,
                            spaceBefore=10, spaceAfter=5),
        "p": ParagraphStyle("p", parent=base["Normal"], fontName=FONT,
                            fontSize=8.5, leading=12, textColor=INK),
        "small": ParagraphStyle("sm", parent=base["Normal"], fontName=FONT,
                                fontSize=7.5, leading=10, textColor=FAINT),
    }


def _mmss(ms: int | None) -> str:
    if ms is None:
        return "—"
    s = ms / 1000.0
    return f"{int(s // 60)}:{int(s % 60):02d}"


def _table(data, widths, extra=None) -> Table:
    t = Table(data, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ]
    if extra:
        style += extra
    t.setStyle(TableStyle(style))
    return t


def _timeline_bar(marks: list[dict], dispatch_sec: float | None) -> Table:
    """
    Полоса 75 секунд в бумажном виде: та же подпись, что на экранах.
    Рисуется таблицей из ячеек — reportlab так надёжнее, чем рисованием.
    """
    span = max(NORM_SEC * 1.35, (dispatch_sec or 0) * 1.1, 1)
    cells = 54
    width = 170 * mm

    row = [""] * cells
    colors_row = []
    norm_cell = int(NORM_SEC / span * cells)

    for i in range(cells):
        if i < norm_cell:
            colors_row.append(colors.HexColor("#E4F0EB"))
        else:
            colors_row.append(colors.HexColor("#FBECEB"))

    for m in marks:
        idx = int(m["t"] / span * cells)
        if 0 <= idx < cells:
            colors_row[idx] = m["color"]

    t = Table([row], colWidths=[width / cells] * cells, rowHeights=[7 * mm])
    style = [("GRID", (0, 0), (-1, -1), 0, colors.white),
             ("TOPPADDING", (0, 0), (-1, -1), 0),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]
    for i, c in enumerate(colors_row):
        style.append(("BACKGROUND", (i, 0), (i, 0), c))
    if 0 <= norm_cell < cells:
        style.append(("LINEBEFORE", (norm_cell, 0), (norm_cell, 0), 1.2, GREEN))
    t.setStyle(TableStyle(style))
    return t


def build(report: dict, meta: dict | None = None) -> bytes:
    """
    report — ответ GET /api/sessions/{id}/report
    meta   — {"trainee", "scenario", "attempt", "date", "instructor"}
    """
    if not _register_fonts():
        raise RuntimeError("Шрифт с кириллицей не найден в контейнере")

    meta = meta or {}
    st = _styles()
    score = report.get("score") or {}
    timeline = report.get("timeline") or []
    breakdown = score.get("breakdown") or {}
    stats = breakdown.get("stats") or {}

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Протокол разбора учебного вызова",
    )
    story = []

    # ---------------------------------------------------------------- шапка
    story.append(Paragraph("ПРОТОКОЛ РАЗБОРА УЧЕБНОГО ВЫЗОВА", st["title"]))
    story.append(Paragraph(
        "Тренажёр приёма экстренного вызова по единому номеру «112»", st["sub"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.8, color=INK, spaceAfter=8))

    sc = report.get("scenario") or {}
    head = [
        ["Обучаемый", meta.get("trainee", "—"), "Дата", meta.get("date", datetime.now().strftime("%d.%m.%Y"))],
        ["Сценарий", sc.get("title", "—"), "Попытка", str(meta.get("attempt", "—"))],
        ["Преподаватель", meta.get("instructor", "—"), "Сессия", report.get("session_id", "—")[:8]],
    ]
    story.append(_table(head, [26 * mm, 74 * mm, 22 * mm, 48 * mm], [
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
        ("FONTNAME", (1, 0), (1, -1), FONT_B),
    ]))

    # ---------------------------------------------------------------- итог
    story.append(Paragraph("Результат", st["h"]))

    passed = bool(score.get("passed"))
    stop = score.get("stop_factors") or []
    total = score.get("total", 0)

    res = [[
        "Протокол", "Скорость", "Манера", "ИТОГ", "Заключение",
    ], [
        f"{score.get('protocol', 0):.0f}",
        f"{score.get('speed', 0):.0f}",
        f"{score.get('composure', 0):.0f}",
        f"{total:.0f}",
        "ЗАЧТЕНО" if passed else "НЕ ЗАЧТЕНО",
    ]]
    story.append(_table(res, [30 * mm, 30 * mm, 30 * mm, 30 * mm, 50 * mm], [
        ("FONTNAME", (0, 0), (-1, 0), FONT),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), FAINT),
        ("FONTNAME", (0, 1), (-1, 1), FONT_M),
        ("FONTSIZE", (0, 1), (-1, 1), 14),
        ("TEXTCOLOR", (3, 1), (3, 1), GREEN if passed else RED),
        ("TEXTCOLOR", (4, 1), (4, 1), GREEN if passed else RED),
        ("FONTSIZE", (4, 1), (4, 1), 10),
        ("FONTNAME", (4, 1), (4, 1), FONT_B),
    ]))

    w = breakdown.get("weights", {})
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        f"Веса осей: протокол {w.get('protocol', 0.6):.0%}, скорость "
        f"{w.get('speed', 0.25):.0%}, манера {w.get('composure', 0.15):.0%}. "
        "Признаки извлекаются автоматически, баллы начисляются по правилам чек-листа.",
        st["small"]))

    if stop:
        story.append(Spacer(1, 5))
        story.append(_table(
            [["СТОП-ФАКТОРЫ: " + "; ".join(stop)]], [170 * mm],
            [("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBECEB")),
             ("TEXTCOLOR", (0, 0), (-1, -1), RED),
             ("FONTNAME", (0, 0), (-1, -1), FONT_B),
             ("LINEBELOW", (0, 0), (-1, -1), 0, colors.white)]))
        story.append(Spacer(1, 2))
        story.append(Paragraph(
            "При наличии стоп-фактора сценарий не засчитывается независимо от суммы баллов.",
            st["small"]))

    # ---------------------------------------------------------------- полоса
    story.append(Paragraph("Ход вызова относительно норматива", st["h"]))

    speed_d = breakdown.get("speed", {})
    marks = []
    for key, color in (("ask_address", colors.HexColor("#3E6BB5")),
                       ("ask_victims", colors.HexColor("#7A5EA8")),
                       ("dispatch", GREEN)):
        sec = (speed_d.get(key) or {}).get("actual_sec")
        if sec:
            marks.append({"t": sec, "color": color})

    story.append(_timeline_bar(marks, stats.get("dispatch_sec")))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        f"Норматив опроса и заполнения обязательных полей карточки — {NORM_SEC} с "
        "(ПП РФ № 1931, п. 9 подп. р). Зелёная зона — норматив, отметки — ключевые действия оператора.",
        st["small"]))

    # ---------------------------------------------------------------- время
    story.append(Paragraph("Соблюдение временных параметров", st["h"]))
    rows = [["Показатель", "Факт", "Цель", "Оценка"]]
    labels = {
        "ask_address": "Вопрос об адресе задан",
        "ask_victims": "Вопрос о пострадавших задан",
        "dispatch": "Карточка направлена в ДДС",
    }
    for key, label in labels.items():
        d = speed_d.get(key) or {}
        a = d.get("actual_sec")
        rows.append([
            label,
            f"{a:.0f} с" if a is not None else "не выполнено",
            f"{d.get('target_sec', '—')} с",
            f"{d.get('score', 0):.0f}%",
        ])
    story.append(_table(rows, [80 * mm, 30 * mm, 30 * mm, 30 * mm], [
        ("FONTNAME", (0, 0), (-1, 0), FONT_B),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTNAME", (1, 1), (-1, -1), FONT_M),
    ]))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Оценивается момент, когда оператор задал вопрос, а не момент получения ответа: "
        "задержка со стороны заявителя не является ошибкой оператора.", st["small"]))

    # ---------------------------------------------------------------- чек-лист
    story.append(Paragraph("Полнота опроса", st["h"]))
    rows = [["", "Пункт", "Время", "Баллы"]]
    styles_extra = [
        ("FONTNAME", (0, 0), (-1, 0), FONT_B),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTNAME", (2, 1), (-1, -1), FONT_M),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
    ]
    for i, (k, v) in enumerate((breakdown.get("checklist") or {}).items(), start=1):
        hit = bool(v.get("hit"))
        rows.append([
            "+" if hit else "—",
            v.get("label", k),
            _mmss(v.get("at_ms")),
            f"{v.get('points', 0):+d}" if isinstance(v.get("points"), int) else str(v.get("points")),
        ])
        styles_extra.append(("TEXTCOLOR", (0, i), (0, i), GREEN if hit else RED))
        styles_extra.append(("FONTNAME", (0, i), (0, i), FONT_B))
        if not hit:
            styles_extra.append(("TEXTCOLOR", (1, i), (1, i), MUTED))
    story.append(_table(rows, [8 * mm, 112 * mm, 20 * mm, 30 * mm], styles_extra))

    # ---------------------------------------------------------------- манера
    comp = breakdown.get("composure") or {}
    story.append(Paragraph("Устойчивость и манера ведения", st["h"]))
    rows = [
        ["Паузы на своём ходу", str(comp.get("pauses_own_turn", 0))],
        ["Слова-паразиты", str(comp.get("fillers", 0))],
        ["Средний темп речи", f"{comp.get('avg_wpm')} сл/мин" if comp.get("avg_wpm") else "—"],
        ["Реплик оператора", str(stats.get("operator_turns", 0))],
        ["Сведений получено", str(stats.get("facts_revealed", 0))],
        ["Информационная эффективность", str(stats.get("info_efficiency", 0))],
    ]
    story.append(_table(rows, [80 * mm, 90 * mm], [
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("FONTNAME", (1, 0), (1, -1), FONT_M),
    ]))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Акустические признаки имеют вспомогательный характер и не являются "
        "единственным основанием для оценки.", st["small"]))

    # ---------------------------------------------------------------- лента
    story.append(Paragraph("Лента вызова", st["h"]))
    rows = [["Время", "Событие", "Содержание"]]
    shown = {
        "call_start": "Вызов принят",
        "operator_utterance": "Оператор",
        "caller_utterance": "Заявитель",
        "fact_revealed": "Получено сведение",
        "forbidden_phrase": "Запрещённая фраза",
        "injection_applied": "Усложнение обстановки",
        "card_dispatched": "Карточка направлена",
        "line_dropped": "Обрыв связи",
        "call_end": "Вызов завершён",
    }
    idx = 0
    extra = [("FONTNAME", (0, 0), (-1, 0), FONT_B),
             ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
             ("FONTNAME", (0, 1), (0, -1), FONT_M)]
    for e in timeline:
        if e["kind"] not in shown:
            continue
        idx += 1
        p = e.get("payload") or {}
        if e["kind"] == "fact_revealed":
            text = FIELD_LABELS.get(p.get("field", ""), p.get("field") or p.get("fact_id", ""))
        else:
            text = (p.get("text") or p.get("label")
                    or (", ".join(p.get("services", [])) if p.get("services") else ""))
        rows.append([_mmss(e["t_ms"]), shown[e["kind"]], (text or "")[:70]])
        if e["kind"] == "forbidden_phrase":
            extra.append(("TEXTCOLOR", (1, idx), (-1, idx), RED))
        elif e["kind"] == "fact_revealed":
            extra.append(("TEXTCOLOR", (1, idx), (1, idx), GREEN))
        elif e["kind"] == "injection_applied":
            extra.append(("TEXTCOLOR", (1, idx), (1, idx), AMBER))
    story.append(_table(rows, [18 * mm, 52 * mm, 100 * mm], extra))

    # ---------------------------------------------------------------- подписи
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=LINE, spaceAfter=8))
    story.append(_table(
        [["Преподаватель  ____________________", "Обучаемый  ____________________"]],
        [85 * mm, 85 * mm],
        [("TEXTCOLOR", (0, 0), (-1, -1), MUTED),
         ("LINEBELOW", (0, 0), (-1, -1), 0, colors.white)]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Документ сформирован автоматически {datetime.now().strftime('%d.%m.%Y %H:%M')}. "
        "Пороговые значения подлежат согласованию с методистами обучающей организации.",
        st["small"]))

    doc.build(story)
    return buf.getvalue()
