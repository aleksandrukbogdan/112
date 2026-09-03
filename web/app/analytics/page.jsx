"use client";

import React, { useState, useMemo, useEffect } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, LineChart, Line, Legend, Cell,
} from "recharts";
import {
  Filter, ArrowLeft, AlertTriangle, Clock, Target, TrendingUp, Radio,
} from "lucide-react";
import Link from "next/link";
import { api } from "../../lib/api";

/* ------------------------------------------------------------------ *
 *  ТОКЕНЫ
 *  Регистр «протокол»: аналитика — это документ, который печатают
 *  и подшивают в дело аттестации. Поэтому светлый, холодный, табличный.
 *  Регистр «пульт» (тёмный) живёт в АРМ обучаемого — здесь не используется.
 * ------------------------------------------------------------------ */
const T = {
  paper: "#F1F4F7",
  surface: "#FFFFFF",
  line: "#DCE3EA",
  lineSoft: "#EAEFF4",
  ink: "#16202B",
  muted: "#5E6E7F",
  faint: "#93A2B2",
  amber: "#E0913C",   // проблесковый маячок — акцент продукта
  red: "#D6453F",
  green: "#4E9E7E",
  blue: "#3E6BB5",
  violet: "#7A5EA8",
};

const NORM_SEC = 75; // ПП РФ 1931: опрос + заполнение обязательных полей

const FONT_UI = "'Golos Text', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
const FONT_MONO = "'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace";

/* ------------------------------------------------------------------ *
 *  МОК-ДАННЫЕ  (детерминированные — чтобы демо было воспроизводимым)
 * ------------------------------------------------------------------ */
let _seed = 20260824;
const rnd = () => {
  _seed = (_seed * 1103515245 + 12345) & 0x7fffffff;
  return _seed / 0x7fffffff;
};

const CHECKLIST = [
  { id: "greeting", label: "Представился" },
  { id: "asked_address", label: "Адрес" },
  { id: "asked_address_detail", label: "Кв./подъезд" },
  { id: "asked_victims", label: "Пострадавшие" },
  { id: "set_threat_flag", label: "Угроза людям" },
  { id: "asked_hazards", label: "Газификация" },
  { id: "asked_evacuation", label: "Эвакуация" },
  { id: "asked_access_routes", label: "Подъездные пути" },
  { id: "gave_instructions", label: "Инструкции" },
  { id: "routed_correctly", label: "Маршрутизация" },
];

const TRAINEES = [
  { id: "t1", name: "Абрамова Л.", group: "Поток 14-А", skill: 0.86 },
  { id: "t2", name: "Валеев Р.", group: "Поток 14-А", skill: 0.71 },
  { id: "t3", name: "Гущина М.", group: "Поток 14-А", skill: 0.58 },
  { id: "t4", name: "Дорохов С.", group: "Поток 15-Б", skill: 0.79 },
  { id: "t5", name: "Ерёмина О.", group: "Поток 15-Б", skill: 0.64 },
  { id: "t6", name: "Жарков П.", group: "Поток 15-Б", skill: 0.5 },
];

const SCENARIOS = [
  { id: "fire", label: "Пожар в квартире", difficulty: 2, services: ["01", "03"] },
  { id: "dtp", label: "ДТП с пострадавшими", difficulty: 3, services: ["01", "02", "03"] },
  { id: "gas", label: "Запах газа в подъезде", difficulty: 2, services: ["04"] },
  { id: "drop", label: "Обрыв связи", difficulty: 4, services: ["03"] },
];

// у каждого обучаемого — своя устойчивая слабость, это и должна ловить система
const WEAKNESS = {
  t1: "asked_access_routes",
  t2: "asked_hazards",
  t3: "asked_victims",
  t4: "asked_evacuation",
  t5: "asked_hazards",
  t6: "asked_victims",
};

function buildSessions() {
  const out = [];
  let n = 0;
  for (const tr of TRAINEES) {
    for (let attempt = 1; attempt <= 6; attempt++) {
      const sc = SCENARIOS[Math.floor(rnd() * SCENARIOS.length)];
      // навык растёт с попытками
      const skill = Math.min(0.97, tr.skill + attempt * 0.028 + (rnd() - 0.5) * 0.09);

      const checklist = {};
      for (const item of CHECKLIST) {
        let p = skill;
        if (item.id === WEAKNESS[tr.id]) p -= 0.45;          // устойчивая слабость
        if (item.id === "asked_access_routes") p -= 0.25;     // редкий вопрос у всех
        if (item.id === "asked_address") p += 0.3;            // базовое почти всегда
        checklist[item.id] = rnd() < Math.max(0.05, Math.min(0.98, p));
      }

      const askAddress = 8 + (1 - skill) * 42 + rnd() * 8;
      const gotAddress = askAddress + 4 + rnd() * 16;          // сопротивление заявителя
      const askVictims = checklist.asked_victims
        ? askAddress + 10 + (1 - skill) * 30 + rnd() * 10
        : null;
      const dispatch = 40 + (1 - skill) * 70 + rnd() * 22;

      const forbidden = rnd() < (1 - skill) * 0.45;
      const pauses = Math.round((1 - skill) * 6 + rnd() * 2);
      const injections = Math.round(rnd() * 3);

      const protocol = Math.round(
        (Object.values(checklist).filter(Boolean).length / CHECKLIST.length) * 100
      );
      const speed = Math.round(Math.max(0, Math.min(100, 130 - dispatch)));
      const composure = Math.round(
        Math.max(0, Math.min(100, 100 - pauses * 7 - (forbidden ? 22 : 0) + rnd() * 8))
      );
      const total = Math.round(protocol * 0.6 + speed * 0.25 + composure * 0.15);

      const stop = [];
      if (!checklist.asked_address) stop.push("Адрес не запрошен");
      if (!checklist.routed_correctly) stop.push("Неверная маршрутизация");
      if (forbidden) stop.push("Запрещённая формулировка");

      const day = new Date(2026, 6, 6 + n * 0.7);
      out.push({
        id: `s${++n}`,
        trainee: tr,
        scenario: sc,
        attempt,
        date: day,
        askAddress, gotAddress, askVictims, dispatch,
        forbidden, pauses, injections,
        facts: Object.values(checklist).filter(Boolean).length,
        turns: 6 + Math.round((1 - skill) * 9),
        checklist,
        score: { protocol, speed, composure, total },
        stop,
        withinNorm: dispatch <= NORM_SEC,
      });
    }
  }
  return out;
}
const SYNTHETIC = buildSessions();


/* ------------------------------------------------------------------ *
 *  РЕАЛЬНЫЕ ДАННЫЕ
 *  Пока проведённых вызовов мало, панель показывает синтетику —
 *  иначе на ней нечего разрабатывать и нечего показывать.
 * ------------------------------------------------------------------ */
function fromApi(rows) {
  return rows
    .filter((r) => r.dispatch_ms != null)
    .map((r, i) => {
      const checklist = {};
      const bc = (r.breakdown && r.breakdown.checklist) || {};
      for (const c of CHECKLIST) checklist[c.id] = !!(bc[c.id] && bc[c.id].hit);
      return {
        id: r.session_id,
        real: true,
        trainee: { id: r.trainee, name: r.trainee, group: r.group_name },
        scenario: { id: r.scenario_slug, label: r.scenario_title, difficulty: r.difficulty,
                    services: [] },
        attempt: r.attempt,
        date: r.started_at ? new Date(r.started_at) : new Date(),
        askAddress: (r.ask_address_ms ?? 0) / 1000,
        gotAddress: (r.got_address_ms ?? r.ask_address_ms ?? 0) / 1000,
        askVictims: r.ask_victims_ms != null ? r.ask_victims_ms / 1000 : null,
        dispatch: r.dispatch_ms / 1000,
        forbidden: (r.forbidden_count || 0) > 0,
        pauses: r.operator_pauses || 0,
        injections: r.injections_count || 0,
        facts: 0,
        turns: 0,
        checklist,
        score: {
          protocol: Math.round(r.protocol_score ?? 0),
          speed: Math.round(r.speed_score ?? 0),
          composure: Math.round(r.composure_score ?? 0),
          total: Math.round(r.total_score ?? 0),
        },
        stop: r.stop_factors || [],
        withinNorm: r.dispatch_ms <= NORM_SEC * 1000,
      };
    });
}

/* ------------------------------------------------------------------ *
 *  ПОДПИСЬ ПРОДУКТА: полоса 75 секунд
 *  Один примитив на трёх масштабах — в АРМ бежит вживую,
 *  в отчёте застывает, в аналитике складывается в стопку.
 * ------------------------------------------------------------------ */
function NormBar({ s, height = 22, showAxis = false, onClick, dense }) {
  const span = Math.max(NORM_SEC * 1.45, s.dispatch * 1.12);
  const pct = (v) => `${Math.min(100, (v / span) * 100)}%`;

  const marks = [
    { t: s.askAddress, color: T.blue, label: "спросил адрес" },
    { t: s.gotAddress, color: T.faint, label: "получил адрес" },
    s.askVictims && { t: s.askVictims, color: T.violet, label: "спросил пострадавших" },
    { t: s.dispatch, color: s.withinNorm ? T.green : T.red, label: "карточка отправлена", big: true },
  ].filter(Boolean);

  return (
    <div className="w-full">
      <div
        onClick={onClick}
        className={`relative w-full rounded-sm overflow-hidden ${onClick ? "cursor-pointer" : ""}`}
        style={{ height, background: T.lineSoft }}
      >
        {/* зона норматива */}
        <div
          className="absolute inset-y-0 left-0"
          style={{ width: pct(NORM_SEC), background: "rgba(78,158,126,0.14)" }}
        />
        {/* граница норматива */}
        <div
          className="absolute inset-y-0"
          style={{ left: pct(NORM_SEC), width: 2, background: T.green, opacity: 0.75 }}
        />
        {/* прожитое время */}
        <div
          className="absolute"
          style={{
            left: 0, width: pct(s.dispatch), top: height / 2 - 2, height: 4,
            background: s.withinNorm ? T.green : T.red, opacity: 0.5,
          }}
        />
        {/* разрыв «спросил → получил» */}
        <div
          className="absolute"
          style={{
            left: pct(s.askAddress),
            width: `${((s.gotAddress - s.askAddress) / span) * 100}%`,
            top: height / 2 - 6, height: 12,
            background: "rgba(224,145,60,0.28)",
          }}
          title="Разрыв: спросил → получил. Не штрафуется."
        />
        {marks.map((m, i) => (
          <div
            key={i}
            className="absolute"
            style={{
              left: pct(m.t), width: m.big ? 3 : 2, top: m.big ? 2 : 4,
              bottom: m.big ? 2 : 4, background: m.color,
            }}
            title={`${m.label} — ${m.t.toFixed(0)} с`}
          />
        ))}
        {s.forbidden && (
          <div
            className="absolute"
            style={{
              left: pct(s.askAddress + 12), top: 0, bottom: 0, width: 3,
              background: T.red,
            }}
            title="Запрещённая формулировка"
          />
        )}
      </div>
      {showAxis && (
        <div
          className="relative w-full mt-1"
          style={{ height: 14, fontFamily: FONT_MONO, fontSize: 10, color: T.faint }}
        >
          {[0, 25, 45, NORM_SEC].map((t) => (
            <span key={t} className="absolute" style={{ left: pct(t) }}>
              {t}с
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 *  Мелкие элементы
 * ------------------------------------------------------------------ */
const Card = ({ title, hint, children, className = "" }) => (
  <section
    className={`rounded-md ${className}`}
    style={{ background: T.surface, border: `1px solid ${T.line}` }}
  >
    {title && (
      <header
        className="px-4 py-3 flex items-baseline justify-between"
        style={{ borderBottom: `1px solid ${T.lineSoft}` }}
      >
        <h2
          className="text-sm font-semibold tracking-tight"
          style={{ color: T.ink, letterSpacing: "-0.01em" }}
        >
          {title}
        </h2>
        {hint && (
          <span className="text-xs" style={{ color: T.faint }}>
            {hint}
          </span>
        )}
      </header>
    )}
    <div className="p-4">{children}</div>
  </section>
);

const Kpi = ({ icon: Icon, label, value, unit, sub, tone = T.ink }) => (
  <div
    className="rounded-md p-4"
    style={{ background: T.surface, border: `1px solid ${T.line}` }}
  >
    <div className="flex items-center gap-2 mb-2">
      <Icon size={13} style={{ color: T.faint }} />
      <span className="text-xs" style={{ color: T.muted }}>{label}</span>
    </div>
    <div className="flex items-baseline gap-1">
      <span style={{ fontFamily: FONT_MONO, fontSize: 26, fontWeight: 600, color: tone, lineHeight: 1 }}>
        {value}
      </span>
      {unit && <span className="text-xs" style={{ color: T.faint }}>{unit}</span>}
    </div>
    {sub && <div className="text-xs mt-1.5" style={{ color: T.faint }}>{sub}</div>}
  </div>
);

const Select = ({ label, value, onChange, options }) => (
  <label className="flex flex-col gap-1">
    <span className="text-xs" style={{ color: T.faint }}>{label}</span>
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="text-xs rounded px-2 py-1.5 outline-none"
      style={{ background: T.surface, border: `1px solid ${T.line}`, color: T.ink, minWidth: 130 }}
    >
      {options.map((o) => (
        <option key={o.v} value={o.v}>{o.l}</option>
      ))}
    </select>
  </label>
);

/* ------------------------------------------------------------------ *
 *  МИКРО: один звонок
 * ------------------------------------------------------------------ */
function MicroView({ s, onBack }) {
  const events = [
    { t: 0, label: "Вызов принят", kind: "ok" },
    s.checklist.greeting
      ? { t: 3.5, label: "Представился по регламенту", kind: "ok", pts: "+5" }
      : { t: 3.5, label: "Не представился", kind: "miss", pts: "0" },
    { t: s.askAddress, label: "Запросил адрес", kind: "ok", pts: "+20",
      note: `на ${s.askAddress.toFixed(0)} с при цели 25 с` },
    { t: s.gotAddress, label: "Заявитель назвал улицу и дом", kind: "info",
      note: `разрыв ${(s.gotAddress - s.askAddress).toFixed(0)} с — паника, не штрафуется` },
    s.checklist.asked_address_detail
      ? { t: s.gotAddress + 6, label: "Уточнил квартиру и подъезд", kind: "ok", pts: "+15" }
      : { t: s.gotAddress + 6, label: "Квартира и подъезд не уточнены", kind: "miss", pts: "0" },
    s.forbidden && { t: s.askAddress + 12, label: "«Успокойтесь» — запрещённая формулировка",
      kind: "bad", pts: "−10" },
    s.askVictims
      ? { t: s.askVictims, label: "Спросил про пострадавших", kind: "ok", pts: "+20" }
      : { t: s.dispatch - 8, label: "Вопрос о пострадавших не задан", kind: "bad", pts: "−20" },
    s.checklist.asked_hazards
      ? { t: s.dispatch - 12, label: "Уточнил газификацию объекта", kind: "ok", pts: "+15" }
      : { t: s.dispatch - 12, label: "Поле «Объект газифицирован» не заполнено", kind: "miss", pts: "0" },
    { t: s.dispatch, label: `Карточка отправлена в ${s.scenario.services.join(", ")}`,
      kind: s.withinNorm ? "ok" : "bad",
      pts: s.withinNorm ? "+20" : "+20",
      note: s.withinNorm ? "в нормативе 75 с" : `норматив 75 с превышен на ${(s.dispatch - NORM_SEC).toFixed(0)} с` },
  ].filter(Boolean).sort((a, b) => a.t - b.t);

  const tone = { ok: T.green, miss: T.amber, bad: T.red, info: T.faint };
  const glyph = { ok: "✓", miss: "○", bad: "✕", info: "→" };

  const confidence = Array.from({ length: 26 }, (_, i) => {
    const t = (i / 25) * s.dispatch;
    let v = 62 + s.score.composure * 0.3;
    if (Math.abs(t - s.askAddress) < 8) v -= 14;
    if (s.forbidden && Math.abs(t - (s.askAddress + 12)) < 6) v -= 22;
    v += Math.sin(i * 0.9) * 6;
    return { t: Math.round(t), v: Math.max(12, Math.min(96, Math.round(v))) };
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-xs"
          style={{ color: T.blue }}
        >
          <ArrowLeft size={13} /> Вернуться к когорте
        </button>
        {s.real && (
          <a
            href={api.reportPdfUrl(s.id)}
            className="ml-auto rounded px-3 py-1.5 text-xs"
            style={{ background: T.amber, color: "#16202B" }}
          >
            Скачать протокол PDF
          </a>
        )}
      </div>

      <Card
        title={`${s.trainee.name} — ${s.scenario.label}`}
        hint={`попытка ${s.attempt} · ${s.date.toLocaleDateString("ru-RU")} · ${s.trainee.group}`}
      >
        <NormBar s={s} height={34} showAxis />
        <div className="flex flex-wrap gap-x-5 gap-y-1 mt-4 text-xs" style={{ color: T.muted }}>
          {[["Спросил адрес", T.blue], ["Получил адрес", T.faint],
            ["Спросил пострадавших", T.violet], ["Отправка карточки", s.withinNorm ? T.green : T.red],
          ].map(([l, c]) => (
            <span key={l} className="flex items-center gap-1.5">
              <i style={{ width: 2, height: 11, background: c, display: "inline-block" }} />
              {l}
            </span>
          ))}
          <span className="flex items-center gap-1.5">
            <i style={{ width: 14, height: 11, background: "rgba(224,145,60,0.28)", display: "inline-block" }} />
            разрыв вопрос → ответ
          </span>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3">
          <Card title="Разбор вызова" hint="таймкоды от начала">
            <ol className="space-y-0">
              {events.map((e, i) => (
                <li
                  key={i}
                  className="flex gap-3 py-2"
                  style={{ borderTop: i ? `1px solid ${T.lineSoft}` : "none" }}
                >
                  <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: T.faint, minWidth: 44 }}>
                    {Math.floor(e.t / 60)}:{String(Math.round(e.t % 60)).padStart(2, "0")}
                  </span>
                  <span style={{ color: tone[e.kind], fontSize: 12, minWidth: 12 }}>{glyph[e.kind]}</span>
                  <span className="flex-1">
                    <span className="text-xs" style={{ color: T.ink }}>{e.label}</span>
                    {e.note && (
                      <span className="block text-xs mt-0.5" style={{ color: T.faint }}>{e.note}</span>
                    )}
                  </span>
                  {e.pts && (
                    <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: tone[e.kind] }}>{e.pts}</span>
                  )}
                </li>
              ))}
            </ol>
          </Card>
        </div>

        <div className="lg:col-span-2 space-y-4">
          <Card title="Оценка">
            {[["Протокол", s.score.protocol, 0.6], ["Скорость", s.score.speed, 0.25],
              ["Манера", s.score.composure, 0.15]].map(([l, v, w]) => (
              <div key={l} className="mb-3">
                <div className="flex justify-between items-baseline mb-1">
                  <span className="text-xs" style={{ color: T.muted }}>
                    {l} <span style={{ color: T.faint }}>· вес {Math.round(w * 100)}%</span>
                  </span>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 12, color: T.ink }}>{v}</span>
                </div>
                <div style={{ height: 5, background: T.lineSoft, borderRadius: 2 }}>
                  <div style={{ width: `${v}%`, height: 5, background: T.blue, borderRadius: 2 }} />
                </div>
              </div>
            ))}
            <div className="flex items-baseline justify-between pt-3" style={{ borderTop: `1px solid ${T.line}` }}>
              <span className="text-xs" style={{ color: T.muted }}>Итог</span>
              <span style={{ fontFamily: FONT_MONO, fontSize: 24, fontWeight: 600,
                color: s.stop.length ? T.red : T.ink }}>
                {s.score.total}
              </span>
            </div>
            {s.stop.length > 0 ? (
              <div className="mt-3 rounded p-2.5" style={{ background: "rgba(214,69,63,0.07)" }}>
                <div className="flex items-center gap-1.5 mb-1">
                  <AlertTriangle size={12} style={{ color: T.red }} />
                  <span className="text-xs font-semibold" style={{ color: T.red }}>
                    Сценарий не зачтён
                  </span>
                </div>
                {s.stop.map((f) => (
                  <div key={f} className="text-xs" style={{ color: T.muted }}>— {f}</div>
                ))}
              </div>
            ) : (
              <div className="mt-3 text-xs" style={{ color: T.green }}>Стоп-факторов нет</div>
            )}
          </Card>

          <Card title="Уверенность по ходу вызова" hint="вспомогательный сигнал">
            <ResponsiveContainer width="100%" height={120}>
              <LineChart data={confidence} margin={{ top: 4, right: 4, bottom: 0, left: -28 }}>
                <CartesianGrid stroke={T.lineSoft} vertical={false} />
                <XAxis dataKey="t" tick={{ fontSize: 9, fill: T.faint }} tickLine={false} axisLine={false} unit="с" />
                <YAxis domain={[0, 100]} tick={{ fontSize: 9, fill: T.faint }} tickLine={false} axisLine={false} />
                <Line type="monotone" dataKey="v" stroke={T.amber} strokeWidth={1.75} dot={false} />
              </LineChart>
            </ResponsiveContainer>
            <p className="text-xs mt-1" style={{ color: T.faint }}>
              Не влияет на балл напрямую. Показывает преподавателю, где обучаемый терялся.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 *  ГЛАВНЫЙ КОМПОНЕНТ
 * ------------------------------------------------------------------ */
export default function Analytics() {
  const [group, setGroup] = useState("all");
  const [scenario, setScenario] = useState("all");
  const [difficulty, setDifficulty] = useState("all");
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [selected, setSelected] = useState(null);
  const [source, setSource] = useState("synthetic");
  const [all, setAll] = useState(SYNTHETIC);

  useEffect(() => {
    api.analytics()
      .then((res) => {
        const real = fromApi(res.rows || []);
        if (real.length >= 3) { setAll(real); setSource("real"); }
      })
      .catch(() => { /* API недоступен — остаёмся на синтетике */ });
  }, []);

  const data = useMemo(
    () =>
      all.filter(
        (s) =>
          (group === "all" || s.trainee.group === group) &&
          (scenario === "all" || s.scenario.id === scenario) &&
          (difficulty === "all" || String(s.scenario.difficulty) === difficulty) &&
          (!onlyFailed || !s.withinNorm)
      ),
    [all, group, scenario, difficulty, onlyFailed]
  );

  const kpi = useMemo(() => {
    if (!data.length) return null;
    const times = data.map((s) => s.dispatch).sort((a, b) => a - b);
    return {
      median: times[Math.floor(times.length / 2)].toFixed(0),
      norm: Math.round((data.filter((s) => s.withinNorm).length / data.length) * 100),
      score: Math.round(data.reduce((a, s) => a + s.score.total, 0) / data.length),
      stops: Math.round((data.filter((s) => s.stop.length).length / data.length) * 100),
    };
  }, [data]);

  const histogram = useMemo(() => {
    const bins = Array.from({ length: 11 }, (_, i) => ({
      bin: i * 15,
      label: `${i * 15}`,
      n: 0,
    }));
    data.forEach((s) => {
      const i = Math.min(10, Math.floor(s.dispatch / 15));
      bins[i].n++;
    });
    return bins;
  }, [data]);

  const heat = useMemo(() => {
    const names = [...new Set(data.map((s) => s.trainee.id))];
    return names.map((id) => {
      const rows = data.filter((s) => s.trainee.id === id);
      return {
        name: rows[0].trainee.name,
        group: rows[0].trainee.group,
        cells: CHECKLIST.map((c) => ({
          id: c.id,
          rate: Math.round((rows.filter((s) => s.checklist[c.id]).length / rows.length) * 100),
        })),
      };
    });
  }, [data]);

  const curve = useMemo(() => {
    return Array.from({ length: 6 }, (_, i) => {
      const rows = data.filter((s) => s.attempt === i + 1);
      return {
        attempt: i + 1,
        score: rows.length ? Math.round(rows.reduce((a, s) => a + s.score.total, 0) / rows.length) : null,
        sec: rows.length ? Math.round(rows.reduce((a, s) => a + s.dispatch, 0) / rows.length) : null,
      };
    });
  }, [data]);

  const missed = useMemo(() => {
    return CHECKLIST.map((c) => ({
      label: c.label,
      miss: data.length
        ? Math.round((data.filter((s) => !s.checklist[c.id]).length / data.length) * 100)
        : 0,
    }))
      .sort((a, b) => b.miss - a.miss)
      .slice(0, 6);
  }, [data]);

  const heatColor = (r) =>
    r >= 85 ? "rgba(78,158,126,0.85)"
      : r >= 65 ? "rgba(78,158,126,0.45)"
      : r >= 45 ? "rgba(224,145,60,0.55)"
      : r >= 25 ? "rgba(224,145,60,0.85)"
      : "rgba(214,69,63,0.8)";

  return (
    <div className="min-h-screen w-full" style={{ background: T.paper, fontFamily: FONT_UI }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');`}</style>

      {/* Шапка */}
      <header
        className="px-5 py-3 flex flex-wrap items-center gap-x-6 gap-y-2"
        style={{ background: T.surface, borderBottom: `1px solid ${T.line}` }}
      >
        <div className="flex items-center gap-2.5">
          <Radio size={16} style={{ color: T.amber }} />
          <div>
            <div className="text-sm font-semibold tracking-tight" style={{ color: T.ink }}>
              Подготовка операторов
            </div>
            <div className="text-xs" style={{ color: T.faint }}>
              Тренажёр приёма экстренного вызова
            </div>
          </div>
        </div>
        <Link href="/" className="text-xs" style={{ color: T.blue }}>Меню</Link>
        <span className="text-xs rounded px-2 py-1"
          style={{ background: source === "real" ? "rgba(78,158,126,0.12)" : "rgba(224,145,60,0.12)",
                   color: source === "real" ? T.green : T.amber }}>
          {source === "real" ? "реальные вызовы" : "демонстрационные данные"}
        </span>
        <div className="ml-auto text-xs" style={{ color: T.faint }}>
          Норматив опроса —{" "}
          <span style={{ fontFamily: FONT_MONO, color: T.ink }}>75 с</span>{" "}
          · ПП РФ № 1931
        </div>
      </header>

      {selected ? (
        <main className="p-5 max-w-6xl mx-auto">
          <MicroView s={selected} onBack={() => setSelected(null)} />
        </main>
      ) : (
        <main className="p-5 max-w-6xl mx-auto space-y-4">
          {/* Фильтры */}
          <div
            className="rounded-md px-4 py-3 flex flex-wrap items-end gap-4"
            style={{ background: T.surface, border: `1px solid ${T.line}` }}
          >
            <div className="flex items-center gap-1.5 pb-1.5">
              <Filter size={13} style={{ color: T.faint }} />
              <span className="text-xs font-medium" style={{ color: T.muted }}>Отбор</span>
            </div>
            <Select label="Группа" value={group} onChange={setGroup}
              options={[{ v: "all", l: "Все группы" },
                ...[...new Set(TRAINEES.map((t) => t.group))].map((g) => ({ v: g, l: g }))]} />
            <Select label="Сценарий" value={scenario} onChange={setScenario}
              options={[{ v: "all", l: "Все сценарии" },
                ...SCENARIOS.map((s) => ({ v: s.id, l: s.label }))]} />
            <Select label="Сложность" value={difficulty} onChange={setDifficulty}
              options={[{ v: "all", l: "Любая" }, { v: "2", l: "2 — базовая" },
                { v: "3", l: "3 — средняя" }, { v: "4", l: "4 — высокая" }]} />
            <label className="flex items-center gap-2 pb-1.5 cursor-pointer">
              <input type="checkbox" checked={onlyFailed}
                onChange={(e) => setOnlyFailed(e.target.checked)} />
              <span className="text-xs" style={{ color: T.muted }}>Только вне норматива</span>
            </label>
            <span className="ml-auto text-xs pb-1.5" style={{ color: T.faint }}>
              {data.length} из {all.length} вызовов
            </span>
          </div>

          {kpi && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <Kpi icon={Clock} label="Медиана до отправки карточки" value={kpi.median} unit="с"
                sub={`норматив ${NORM_SEC} с`}
                tone={Number(kpi.median) <= NORM_SEC ? T.green : T.red} />
              <Kpi icon={Target} label="Уложились в норматив" value={kpi.norm} unit="%"
                sub="доля вызовов"
                tone={kpi.norm >= 70 ? T.green : kpi.norm >= 50 ? T.amber : T.red} />
              <Kpi icon={TrendingUp} label="Средний балл" value={kpi.score} unit="из 100" />
              <Kpi icon={AlertTriangle} label="Со стоп-фактором" value={kpi.stops} unit="%"
                sub="незачёт независимо от баллов"
                tone={kpi.stops > 25 ? T.red : T.ink} />
            </div>
          )}

          {/* ПОДПИСЬ: стопка полос */}
          <Card
            title="Полосы вызовов"
            hint="каждая строка — один вызов · нажмите, чтобы открыть разбор"
          >
            <div
              className="flex items-center gap-3 mb-3 pb-2 text-xs"
              style={{ color: T.faint, borderBottom: `1px solid ${T.lineSoft}` }}
            >
              <span style={{ minWidth: 108 }}>Обучаемый</span>
              <span className="flex-1">
                Зелёная зона — норматив 75 секунд. Янтарная полоса — разрыв между
                вопросом оператора и ответом заявителя.
              </span>
            </div>
            <div className="space-y-1.5 overflow-y-auto" style={{ maxHeight: 340 }}>
              {[...data]
                .sort((a, b) => a.dispatch - b.dispatch)
                .map((s) => (
                  <div key={s.id} className="flex items-center gap-3">
                    <span className="text-xs truncate" style={{ color: T.muted, minWidth: 108 }}>
                      {s.trainee.name}
                    </span>
                    <div className="flex-1">
                      <NormBar s={s} onClick={() => setSelected(s)} />
                    </div>
                    <span style={{ fontFamily: FONT_MONO, fontSize: 11, minWidth: 34,
                      textAlign: "right", color: s.withinNorm ? T.green : T.red }}>
                      {s.dispatch.toFixed(0)}с
                    </span>
                  </div>
                ))}
            </div>
          </Card>

          {/* Тепловая карта */}
          <Card
            title="Что систематически пропускают"
            hint="доля вызовов, где пункт выполнен"
          >
            <div className="overflow-x-auto">
              <table className="w-full" style={{ borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th className="text-left text-xs font-medium pb-2 pr-3"
                      style={{ color: T.faint, minWidth: 108 }}>Обучаемый</th>
                    {CHECKLIST.map((c) => (
                      <th key={c.id} className="pb-2 px-0.5"
                        style={{ width: 62, verticalAlign: "bottom" }}>
                        <div className="text-xs leading-tight" style={{ color: T.faint, fontSize: 10 }}>
                          {c.label}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {heat.map((row) => (
                    <tr key={row.name}>
                      <td className="text-xs py-0.5 pr-3" style={{ color: T.ink }}>
                        {row.name}
                        <span className="block" style={{ color: T.faint, fontSize: 10 }}>{row.group}</span>
                      </td>
                      {row.cells.map((c) => (
                        <td key={c.id} className="px-0.5 py-0.5">
                          <div
                            className="rounded-sm flex items-center justify-center"
                            style={{ height: 30, background: heatColor(c.rate),
                              fontFamily: FONT_MONO, fontSize: 10,
                              color: c.rate >= 45 ? "#fff" : "#fff" }}
                            title={`${c.rate}%`}
                          >
                            {c.rate}
                          </div>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs mt-3" style={{ color: T.faint }}>
              Красная клетка — адрес для следующей тренировки. Система подбирает сценарии,
              нагружающие именно эти пункты.
            </p>
          </Card>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Время до отправки карточки" hint="распределение по вызовам">
              <ResponsiveContainer width="100%" height={190}>
                <BarChart data={histogram} margin={{ top: 6, right: 8, bottom: 0, left: -26 }}>
                  <CartesianGrid stroke={T.lineSoft} vertical={false} />
                  <XAxis dataKey="label" tick={{ fontSize: 10, fill: T.faint }}
                    tickLine={false} axisLine={{ stroke: T.line }} unit="с" />
                  <YAxis tick={{ fontSize: 10, fill: T.faint }} tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ fontSize: 11, borderRadius: 4, border: `1px solid ${T.line}` }}
                    formatter={(v) => [`${v} вызовов`, ""]}
                    labelFormatter={(l) => `${l}–${Number(l) + 15} с`}
                  />
                  <ReferenceLine x="75" stroke={T.green} strokeWidth={2}
                    label={{ value: "норматив", fontSize: 10, fill: T.green, position: "top" }} />
                  <Bar dataKey="n" radius={[2, 2, 0, 0]}>
                    {histogram.map((b, i) => (
                      <Cell key={i} fill={b.bin < NORM_SEC ? T.green : T.red}
                        fillOpacity={b.bin < NORM_SEC ? 0.72 : 0.62} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Card>

            <Card title="Кривая обучения" hint="среднее по отобранным вызовам">
              <ResponsiveContainer width="100%" height={190}>
                <LineChart data={curve} margin={{ top: 6, right: 8, bottom: 0, left: -26 }}>
                  <CartesianGrid stroke={T.lineSoft} vertical={false} />
                  <XAxis dataKey="attempt" tick={{ fontSize: 10, fill: T.faint }}
                    tickLine={false} axisLine={{ stroke: T.line }} />
                  <YAxis tick={{ fontSize: 10, fill: T.faint }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ fontSize: 11, borderRadius: 4, border: `1px solid ${T.line}` }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <ReferenceLine y={NORM_SEC} stroke={T.green} strokeDasharray="3 3" />
                  <Line name="Балл" type="monotone" dataKey="score" stroke={T.blue}
                    strokeWidth={2} dot={{ r: 2.5 }} connectNulls />
                  <Line name="Секунд до отправки" type="monotone" dataKey="sec" stroke={T.amber}
                    strokeWidth={2} dot={{ r: 2.5 }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
              <p className="text-xs mt-1" style={{ color: T.faint }}>
                Номер попытки, а не дата: так видно рост навыка, а не расписание занятий.
              </p>
            </Card>
          </div>

          <Card title="Чаще всего пропускают" hint="доля вызовов без этого пункта">
            <div className="space-y-2">
              {missed.map((m) => (
                <div key={m.label} className="flex items-center gap-3">
                  <span className="text-xs" style={{ color: T.ink, minWidth: 130 }}>{m.label}</span>
                  <div className="flex-1 rounded-sm" style={{ height: 16, background: T.lineSoft }}>
                    <div style={{ width: `${m.miss}%`, height: 16, background: T.amber,
                      opacity: 0.75, borderRadius: 2 }} />
                  </div>
                  <span style={{ fontFamily: FONT_MONO, fontSize: 11, color: T.muted, minWidth: 32,
                    textAlign: "right" }}>{m.miss}%</span>
                </div>
              ))}
            </div>
          </Card>
        </main>
      )}
    </div>
  );
}
