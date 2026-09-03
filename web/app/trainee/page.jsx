"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { PhoneCall, PhoneOff, Send, ArrowLeft, Volume2, Mic, MicOff, FileDown, Keyboard } from "lucide-react";
import { api, DEMO } from "../../lib/api";
import NormBar, { NORM_SEC } from "../../components/NormBar";
import useRecorder from "../../lib/recorder";

/* Поля карточки — состав из ПП РФ № 1931, приложения 1 и 2.
   Обязательные помечены: их отсутствие видно в разборе. */
const CARD_FIELDS = [
  { group: "Заявитель", items: [
    { key: "common.zayavitel_fio", label: "ФИО заявителя" },
    { key: "common.yazyk_obshcheniya", label: "Язык общения" },
  ]},
  { group: "Место происшествия", items: [
    { key: "common.ulica", label: "Улица", req: true },
    { key: "common.dom", label: "Дом №", req: true },
    { key: "common.podezd", label: "Подъезд" },
    { key: "common.etazh", label: "Этаж" },
    { key: "common.kvartira", label: "Квартира" },
  ]},
  { group: "Происшествие", items: [
    { key: "common.tip_proisshestviya", label: "Тип происшествия", req: true },
    { key: "common.chislo_postradavshih", label: "Число пострадавших", req: true },
    { key: "common.ugroza_lyudyam", label: "Угроза людям" },
  ]},
  { group: "Специальная часть — пожарная охрана", items: [
    { key: "fire.obstoyatelstva", label: "Что горит" },
    { key: "fire.etazhnost", label: "Этажность" },
    { key: "fire.obekt_gazificirovan", label: "Объект газифицирован" },
    { key: "fire.vozmozhnost_evakuacii", label: "Возможность эвакуации" },
    { key: "fire.podezdnye_puti", label: "Подъездные пути" },
  ]},
];

const SERVICES = [
  { code: "01", label: "Пожарная охрана" },
  { code: "02", label: "Полиция" },
  { code: "03", label: "Скорая помощь" },
  { code: "04", label: "Аварийная газовая" },
  { code: "05", label: "ЖКХ" },
];

export default function Trainee() {
  const [scenarios, setScenarios] = useState([]);
  const [slug, setSlug] = useState("");
  const [trainee, setTrainee] = useState(DEMO.trainees[0].id);

  const [session, setSession] = useState(null);
  const [log, setLog] = useState([]);
  const [input, setInput] = useState("");
  const [metrics, setMetrics] = useState(null);
  const [card, setCard] = useState({});
  const [fieldState, setFieldState] = useState({});
  const [services, setServices] = useState([]);
  const [dispatched, setDispatched] = useState(false);
  const [now, setNow] = useState(0);
  const [busy, setBusy] = useState(false);
  const [inputMode, setInputMode] = useState("text"); // text | voice
  const [recMode, setRecMode] = useState("auto");     // auto | push
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const startedAt = useRef(null);
  const logEnd = useRef(null);
  const audioRef = useRef(null);

  useEffect(() => {
    api.scenarios()
      .then((s) => { setScenarios(s); if (s[0]) setSlug(s[0].slug); })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!session || result) return;
    const id = setInterval(() => {
      setNow((Date.now() - startedAt.current) / 1000);
    }, 200);
    return () => clearInterval(id);
  }, [session, result]);

  useEffect(() => { logEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [log]);

  async function begin() {
    setError(null); setBusy(true);
    try {
      const s = await api.startSession({ trainee_id: trainee, scenario_slug: slug });
      startedAt.current = Date.now();
      setSession(s);
      setLog([{ who: "caller", text: s.opening.text, t: 0 }]);
      setCard({}); setFieldState({}); setServices([]);
      setDispatched(false); setResult(null); setNow(0);
      play(s.opening.text, s.opening.emotion);
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  function play(text, emotion) {
    if (!audioRef.current) return;
    audioRef.current.src = api.ttsUrl(text, emotion || "neutral");
    audioRef.current.play().catch(() => {/* TTS выключен — молча пропускаем */});
  }

  // Голосовой ход: WAV с микрофона -> ASR -> та же логика, что у текста
  const onUtterance = useCallback(async (blob, info) => {
    if (!session || busy) return;
    setBusy(true);
    try {
      const r = await api.utterance(session.session_id, blob, info);
      setMetrics(r.metrics);
      setLog((l) => [
        ...l,
        { who: "operator", text: r.recognized || "(распознано)", t: r.metrics.t_ms / 1000, voice: true },
        { who: "caller", text: r.caller.text, t: r.metrics.t_ms / 1000, revealed: r.revealed },
      ]);
      play(r.caller.text, r.caller.emotion);
    } catch (e) {
      // «Речь не распознана» — обычная ситуация при кашле или шуме, не ошибка
      if (!String(e.message).includes("422")) setError(e.message);
    }
    setBusy(false);
  }, [session, busy]);

  const rec = useRecorder({ onUtterance, mode: recMode });

  useEffect(() => {
    if (inputMode !== "voice" || !session || result) { rec.active && rec.stop(); return; }
    rec.start();
    return () => rec.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputMode, session, result]);

  async function send() {
    const text = input.trim();
    if (!text || !session || busy) return;
    setBusy(true); setInput("");
    const t = (Date.now() - startedAt.current) / 1000;
    setLog((l) => [...l, { who: "operator", text, t }]);
    try {
      const r = await api.say(session.session_id, text);
      setMetrics(r.metrics);
      setLog((l) => [...l, {
        who: "caller", text: r.caller.text,
        t: r.metrics.t_ms / 1000, revealed: r.revealed,
      }]);
      play(r.caller.text, r.caller.emotion);
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  async function saveField(key, value) {
    if (!session || !value) return;
    try {
      const r = await api.card(session.session_id, key, value);
      setFieldState((s) => ({ ...s, [key]: r.is_correct }));
      setMetrics(r.metrics);
    } catch (e) { setError(e.message); }
  }

  async function doDispatch() {
    if (!session || !services.length) return;
    try {
      const r = await api.dispatch(session.session_id, services);
      setDispatched(true);
      setMetrics(r.metrics);
      setLog((l) => [...l, {
        who: "system",
        text: `Карточка направлена: ${services.join(", ")}`,
        t: r.metrics.t_ms / 1000,
      }]);
    } catch (e) { setError(e.message); }
  }

  async function end() {
    if (!session) return;
    setBusy(true);
    try { setResult(await api.finish(session.session_id)); }
    catch (e) { setError(e.message); }
    setBusy(false);
  }

  const marks = metrics ? [
    metrics.ask_address_ms && { t: metrics.ask_address_ms / 1000, color: "#3E6BB5", label: "спросил адрес" },
    metrics.ask_victims_ms && { t: metrics.ask_victims_ms / 1000, color: "#7A5EA8", label: "спросил пострадавших" },
    metrics.dispatched_at_ms && { t: metrics.dispatched_at_ms / 1000, color: "#4E9E7E", label: "карточка отправлена", big: true },
  ].filter(Boolean) : [];

  const overNorm = now > NORM_SEC && !dispatched;

  return (
    <main className="min-h-screen bg-pult text-pultText">
      <audio ref={audioRef} className="hidden" />

      <header className="flex items-center gap-4 border-b border-pultLine px-5 py-3">
        <Link href="/" className="flex items-center gap-1.5 text-xs text-pultMuted hover:text-pultText">
          <ArrowLeft size={13} /> Меню
        </Link>
        <span className="text-sm font-semibold tracking-tight">Рабочее место оператора</span>
        {session && (
          <span className="ml-auto flex items-baseline gap-2">
            <span className="text-xs text-pultMuted">на линии</span>
            <span
              className="font-mono text-2xl font-semibold tabular-nums"
              style={{ color: overNorm ? "#D6453F" : "#C9D4E2" }}
            >
              {String(Math.floor(now / 60)).padStart(2, "0")}:
              {String(Math.floor(now % 60)).padStart(2, "0")}
            </span>
          </span>
        )}
      </header>

      {error && (
        <div className="border-b border-pultLine px-5 py-2 text-xs" style={{ color: "#D6453F" }}>
          {error}
        </div>
      )}

      {!session ? (
        /* ---------- до вызова ---------- */
        <div className="mx-auto max-w-lg px-5 py-16">
          <h1 className="mb-1 text-lg font-semibold">Учебный вызов</h1>
          <p className="mb-6 text-xs text-pultMuted">
            Вызов начнётся сразу. Заявитель сообщает сведения только в ответ на заданный вопрос.
          </p>

          <label className="mb-4 block">
            <span className="mb-1 block text-xs text-pultMuted">Обучаемый</span>
            <select
              value={trainee}
              onChange={(e) => setTrainee(e.target.value)}
              className="w-full rounded border border-pultLine bg-pultUp px-3 py-2 text-sm text-pultText"
            >
              {DEMO.trainees.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>

          <label className="mb-6 block">
            <span className="mb-1 block text-xs text-pultMuted">Сценарий</span>
            <select
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              className="w-full rounded border border-pultLine bg-pultUp px-3 py-2 text-sm text-pultText"
            >
              {scenarios.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.title} · сложность {s.difficulty}
                </option>
              ))}
            </select>
          </label>

          <button
            onClick={begin}
            disabled={!slug || busy}
            className="flex w-full items-center justify-center gap-2 rounded px-4 py-3 text-sm font-medium disabled:opacity-40"
            style={{ background: "#4E9E7E", color: "#0F141B" }}
          >
            <PhoneCall size={15} /> Принять вызов
          </button>
        </div>
      ) : result ? (
        /* ---------- после вызова ---------- */
        <div className="mx-auto max-w-2xl px-5 py-10">
          <h1 className="mb-5 text-lg font-semibold">Разбор тренировки</h1>
          <div className="mb-5 grid grid-cols-4 gap-3">
            {[["Протокол", result.protocol], ["Скорость", result.speed],
              ["Манера", result.composure], ["Итог", result.total]].map(([l, v], i) => (
              <div key={l} className="rounded border border-pultLine bg-pultUp p-3">
                <div className="mb-1 text-xs text-pultMuted">{l}</div>
                <div
                  className="font-mono text-xl font-semibold"
                  style={{ color: i === 3 ? (result.passed ? "#4E9E7E" : "#D6453F") : "#C9D4E2" }}
                >
                  {Math.round(v)}
                </div>
              </div>
            ))}
          </div>

          {result.stop_factors.length > 0 ? (
            <div className="mb-5 rounded border p-3" style={{ borderColor: "#D6453F" }}>
              <div className="mb-1 text-sm font-semibold" style={{ color: "#D6453F" }}>
                Сценарий не зачтён
              </div>
              {result.stop_factors.map((f) => (
                <div key={f} className="text-xs text-pultMuted">— {f}</div>
              ))}
            </div>
          ) : (
            <div className="mb-5 text-sm" style={{ color: "#4E9E7E" }}>Стоп-факторов нет</div>
          )}

          <div className="mb-5 rounded border border-pultLine bg-pultUp">
            <div className="border-b border-pultLine px-3 py-2 text-xs text-pultMuted">
              Чек-лист опроса
            </div>
            <ul>
              {Object.entries(result.breakdown.checklist).map(([k, v]) => (
                <li key={k} className="flex items-center gap-3 border-b border-pultLine px-3 py-2 last:border-0">
                  <span style={{ color: v.hit ? "#4E9E7E" : "#D6453F", width: 12 }}>
                    {v.hit ? "✓" : "✕"}
                  </span>
                  <span className="flex-1 text-xs">{v.label}</span>
                  {v.at_ms != null && (
                    <span className="font-mono text-xs text-pultMuted">
                      {(v.at_ms / 1000).toFixed(0)}с
                    </span>
                  )}
                  <span className="font-mono text-xs" style={{ color: v.points >= 0 ? "#4E9E7E" : "#D6453F" }}>
                    {v.points > 0 ? "+" : ""}{v.points}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex gap-3">
            <button
              onClick={() => { setSession(null); setResult(null); setLog([]); setMetrics(null); }}
              className="rounded border border-pultLine px-4 py-2 text-sm"
            >
              Ещё вызов
            </button>
            <a
              href={api.reportPdfUrl(session.session_id)}
              className="flex items-center gap-1.5 rounded px-4 py-2 text-sm"
              style={{ background: "#E0913C", color: "#0F141B" }}
            >
              <FileDown size={14} /> Скачать протокол
            </a>
            <Link href="/analytics" className="rounded border border-pultLine px-4 py-2 text-sm">
              Аналитика
            </Link>
          </div>
        </div>
      ) : (
        /* ---------- во время вызова ---------- */
        <div className="grid gap-4 p-5 lg:grid-cols-[1fr_400px]">
          <section className="flex flex-col">
            <div className="mb-4 rounded border border-pultLine bg-pultUp p-3">
              <NormBar nowSec={now} marks={marks} dark showAxis height={28} />
              {metrics && (
                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs text-pultMuted">
                  <span>фактов {metrics.facts_revealed}/{metrics.facts_total}</span>
                  <span>реплик {metrics.operator_turns}</span>
                  <span>эффективность {metrics.info_efficiency}</span>
                </div>
              )}
            </div>

            <div
              className="mb-3 flex-1 overflow-y-auto rounded border border-pultLine bg-pultUp p-3"
              style={{ minHeight: 300, maxHeight: 420 }}
            >
              {log.map((m, i) => (
                <div key={i} className="mb-3 flex gap-3">
                  <span className="font-mono text-xs text-pultMuted" style={{ minWidth: 42 }}>
                    {Math.floor(m.t / 60)}:{String(Math.floor(m.t % 60)).padStart(2, "0")}
                  </span>
                  <div className="flex-1">
                    <div className="mb-0.5 text-xs" style={{
                      color: m.who === "caller" ? "#E0913C"
                        : m.who === "system" ? "#4E9E7E" : "#7A8A9E",
                    }}>
                      {m.who === "caller" ? "Заявитель" : m.who === "system" ? "Система" : "Вы"}
                    </div>
                    <div className="text-sm leading-relaxed">{m.text}</div>
                    {m.revealed?.length > 0 && (
                      <div className="mt-1 font-mono text-xs" style={{ color: "#4E9E7E" }}>
                        получено сведений: {m.revealed.length}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={logEnd} />
            </div>

            <div className="mb-2 flex items-center gap-2">
              <button
                onClick={() => setInputMode(inputMode === "text" ? "voice" : "text")}
                className="flex items-center gap-1.5 rounded border border-pultLine px-2.5 py-1.5 text-xs"
              >
                {inputMode === "text"
                  ? (<><Mic size={12} /> Перейти на голос</>)
                  : (<><Keyboard size={12} /> Перейти на текст</>)}
              </button>

              {inputMode === "voice" && (
                <>
                  <button
                    onClick={() => setRecMode(recMode === "auto" ? "push" : "auto")}
                    className="rounded border border-pultLine px-2.5 py-1.5 text-xs text-pultMuted"
                  >
                    {recMode === "auto" ? "нарезка по паузе" : "удержание кнопки"}
                  </button>
                  <div className="flex flex-1 items-center gap-2">
                    <span className="text-xs" style={{ color: rec.speaking ? "#4E9E7E" : "#7A8A9E" }}>
                      {rec.error ? "микрофон недоступен" : rec.speaking ? "говорите" : "тишина"}
                    </span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded" style={{ background: "#2A3644" }}>
                      <div
                        className="h-full rounded"
                        style={{
                          width: `${Math.round(rec.level * 100)}%`,
                          background: rec.speaking ? "#4E9E7E" : "#7A8A9E",
                          transition: "width 80ms linear",
                        }}
                      />
                    </div>
                  </div>
                </>
              )}
            </div>

            {rec.error && (
              <div className="mb-2 text-xs" style={{ color: "#D6453F" }}>{rec.error}</div>
            )}

            {inputMode === "voice" && recMode === "push" && (
              <button
                onMouseDown={rec.holdStart} onMouseUp={rec.holdEnd}
                onMouseLeave={rec.holdEnd}
                onTouchStart={rec.holdStart} onTouchEnd={rec.holdEnd}
                className="mb-2 w-full rounded py-3 text-sm font-medium"
                style={{ background: rec.speaking ? "#4E9E7E" : "#2A3644",
                         color: rec.speaking ? "#0F141B" : "#C9D4E2" }}
              >
                {rec.speaking ? "Идёт запись — отпустите, чтобы отправить" : "Удерживайте, чтобы говорить"}
              </button>
            )}

            <div className="flex gap-2" style={{ display: inputMode === "voice" ? "none" : "flex" }}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder="Ваша реплика заявителю…"
                disabled={busy}
                className="flex-1 rounded border border-pultLine bg-pultUp px-3 py-2.5 text-sm text-pultText placeholder:text-pultMuted"
              />
              <button
                onClick={send}
                disabled={busy || !input.trim()}
                className="rounded px-4 disabled:opacity-40"
                style={{ background: "#3E6BB5", color: "#fff" }}
                aria-label="Отправить реплику"
              >
                <Send size={15} />
              </button>
              <button
                onClick={end}
                className="flex items-center gap-1.5 rounded px-4 text-sm"
                style={{ background: "#D6453F", color: "#fff" }}
              >
                <PhoneOff size={15} /> Завершить
              </button>
            </div>
            {inputMode === "voice" && (
              <div className="flex gap-2">
                <button
                  onClick={end}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded py-2.5 text-sm"
                  style={{ background: "#D6453F", color: "#fff" }}
                >
                  <PhoneOff size={15} /> Завершить вызов
                </button>
              </div>
            )}

            <p className="mt-2 flex items-center gap-1.5 text-xs text-pultMuted">
              {inputMode === "voice"
                ? (<><Mic size={11} /> Реплика отправляется сама после паузы. Нужен включённый профиль speech.</>)
                : (<><Volume2 size={11} /> Голос заявителя звучит, если включён синтез речи.</>)}
            </p>
          </section>

          {/* ---- карточка происшествия ---- */}
          <aside className="overflow-y-auto rounded border border-pultLine bg-pultUp" style={{ maxHeight: "78vh" }}>
            <div className="border-b border-pultLine px-3 py-2 text-xs font-medium">
              Карточка информационного обмена
            </div>

            {CARD_FIELDS.map((g) => (
              <div key={g.group}>
                <div className="border-b border-pultLine bg-pult px-3 py-1.5 text-xs text-pultMuted">
                  {g.group}
                </div>
                {g.items.map((f) => (
                  <label key={f.key} className="flex items-center gap-2 border-b border-pultLine px-3 py-1.5">
                    <span className="text-xs text-pultMuted" style={{ minWidth: 132 }}>
                      {f.label}
                      {f.req && <span style={{ color: "#E0913C" }}> *</span>}
                    </span>
                    <input
                      value={card[f.key] || ""}
                      onChange={(e) => setCard((c) => ({ ...c, [f.key]: e.target.value }))}
                      onBlur={(e) => saveField(f.key, e.target.value)}
                      className="flex-1 rounded bg-pult px-2 py-1 font-mono text-xs text-pultText"
                      style={{
                        border: `1px solid ${
                          fieldState[f.key] === true ? "#4E9E7E"
                            : fieldState[f.key] === false ? "#D6453F" : "#2A3644"
                        }`,
                      }}
                    />
                  </label>
                ))}
              </div>
            ))}

            <div className="border-b border-pultLine bg-pult px-3 py-1.5 text-xs text-pultMuted">
              Службы реагирования
            </div>
            <div className="p-3">
              <div className="mb-3 flex flex-wrap gap-1.5">
                {SERVICES.map((s) => {
                  const on = services.includes(s.code);
                  return (
                    <button
                      key={s.code}
                      onClick={() =>
                        setServices((v) => on ? v.filter((x) => x !== s.code) : [...v, s.code])
                      }
                      disabled={dispatched}
                      className="rounded px-2.5 py-1.5 text-xs disabled:opacity-50"
                      style={{
                        background: on ? "#3E6BB5" : "transparent",
                        border: `1px solid ${on ? "#3E6BB5" : "#2A3644"}`,
                        color: on ? "#fff" : "#7A8A9E",
                      }}
                    >
                      <span className="font-mono">{s.code}</span> {s.label}
                    </button>
                  );
                })}
              </div>
              <button
                onClick={doDispatch}
                disabled={dispatched || !services.length}
                className="w-full rounded py-2 text-sm font-medium disabled:opacity-40"
                style={{ background: dispatched ? "#2A3644" : "#4E9E7E",
                         color: dispatched ? "#7A8A9E" : "#0F141B" }}
              >
                {dispatched ? "Карточка направлена" : "Направить карточку"}
              </button>
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
