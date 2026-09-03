"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Zap, CheckCheck, Radio } from "lucide-react";
import { API, api } from "../../lib/api";
import NormBar from "../../components/NormBar";

/* Ручной зачёт — страховка на случай, когда классификатор промахнулся.
   Без неё промах на демо не отыграть. */
const INTENTS = [
  ["greeting", "Представился"],
  ["asked_what_happened", "Что произошло"],
  ["asked_address", "Адрес"],
  ["asked_address_detail", "Квартира и подъезд"],
  ["asked_victims", "Пострадавшие"],
  ["asked_hazards", "Газификация"],
  ["asked_evacuation", "Эвакуация"],
  ["asked_access_routes", "Подъездные пути"],
  ["gave_instructions", "Инструкции"],
];

export default function Instructor() {
  const [sid, setSid] = useState("");
  const [connected, setConnected] = useState(false);
  const [scenario, setScenario] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [log, setLog] = useState([]);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);
  const logEnd = useRef(null);

  useEffect(() => () => wsRef.current?.close(), []);
  useEffect(() => { logEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [log]);

  function connect() {
    if (!sid.trim()) return;
    setError(null);
    wsRef.current?.close();

    const url = API.replace(/^http/, "ws") + `/ws/session/${sid.trim()}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ type: "ping" }));
    };
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setError("Не удалось подключиться. Проверьте идентификатор сессии.");
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.metrics) setMetrics(m.metrics);
      if (m.type === "caller_utterance") {
        setLog((l) => [...l, {
          who: "caller", text: m.caller.text,
          t: (m.metrics?.t_ms || 0) / 1000, revealed: m.revealed,
        }]);
      }
      if (m.type === "injection") {
        setLog((l) => [...l, {
          who: "system", text: `Инъекция: ${m.injection.label}`,
          t: (m.metrics?.t_ms || 0) / 1000,
        }]);
      }
    };

    api.scenarios().then(setScenario).catch(() => {});
  }

  const send = (msg) => wsRef.current?.readyState === 1 && wsRef.current.send(JSON.stringify(msg));

  const INJECTIONS = [
    ["escalate_panic", "Усилить панику"],
    ["child_crying", "Плач ребёнка"],
    ["worse_line", "Ухудшить связь"],
    ["wrong_address", "Путает адрес"],
    ["aggression", "Заявитель хамит"],
    ["line_drop", "Оборвать связь"],
  ];

  const nowSec = (metrics?.t_ms || 0) / 1000;
  const marks = metrics ? [
    metrics.ask_address_ms && { t: metrics.ask_address_ms / 1000, color: "#3E6BB5", label: "адрес" },
    metrics.ask_victims_ms && { t: metrics.ask_victims_ms / 1000, color: "#7A5EA8", label: "пострадавшие" },
    metrics.dispatched_at_ms && { t: metrics.dispatched_at_ms / 1000, color: "#4E9E7E", label: "отправка", big: true },
  ].filter(Boolean) : [];

  return (
    <main className="min-h-screen bg-paper">
      <header className="flex items-center gap-4 border-b border-line bg-surface px-5 py-3">
        <Link href="/" className="flex items-center gap-1.5 text-xs text-muted hover:text-ink">
          <ArrowLeft size={13} /> Меню
        </Link>
        <span className="text-sm font-semibold tracking-tight text-ink">
          Рабочее место преподавателя
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          <i className="inline-block h-2 w-2 rounded-full"
             style={{ background: connected ? "#4E9E7E" : "#93A2B2" }} />
          {connected ? "подключено к вызову" : "не подключено"}
        </span>
      </header>

      <div className="mx-auto max-w-5xl px-5 py-5">
        <div className="mb-4 rounded-md border border-line bg-surface p-4">
          <div className="mb-2 text-xs font-medium text-muted">Подключиться к вызову</div>
          <div className="flex gap-2">
            <input
              value={sid}
              onChange={(e) => setSid(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && connect()}
              placeholder="Идентификатор сессии из АРМ обучаемого"
              className="flex-1 rounded border border-line px-3 py-2 font-mono text-xs text-ink"
            />
            <button onClick={connect}
              className="rounded px-4 py-2 text-sm text-white" style={{ background: "#3E6BB5" }}>
              Подключиться
            </button>
          </div>
          {error && <div className="mt-2 text-xs" style={{ color: "#D6453F" }}>{error}</div>}
          <p className="mt-2 text-xs text-faint">
            Идентификатор выводится в консоли API при начале вызова, а также возвращается
            запросом <span className="font-mono">POST /api/sessions</span>.
          </p>
        </div>

        {connected && (
          <>
            <div className="mb-4 rounded-md border border-line bg-surface p-4">
              <div className="mb-3 flex items-baseline justify-between">
                <span className="text-sm font-semibold text-ink">Ход вызова</span>
                <span className="font-mono text-xl tabular-nums"
                  style={{ color: nowSec > 75 ? "#D6453F" : "#16202B" }}>
                  {String(Math.floor(nowSec / 60)).padStart(2, "0")}:
                  {String(Math.floor(nowSec % 60)).padStart(2, "0")}
                </span>
              </div>
              <NormBar nowSec={nowSec} marks={marks} showAxis height={28} />
              {metrics && (
                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {[["Сведений получено", `${metrics.facts_revealed}/${metrics.facts_total}`],
                    ["Реплик оператора", metrics.operator_turns],
                    ["Эффективность", metrics.info_efficiency],
                    ["Напряжение заявителя", metrics.stress]].map(([l, v]) => (
                    <div key={l}>
                      <div className="text-xs text-faint">{l}</div>
                      <div className="font-mono text-sm text-ink">{v}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <section className="rounded-md border border-line bg-surface">
                <div className="flex items-center gap-1.5 border-b border-lineSoft px-4 py-3">
                  <Zap size={13} className="text-amber" />
                  <span className="text-sm font-semibold text-ink">Усложнить обстановку</span>
                </div>
                <div className="flex flex-wrap gap-2 p-4">
                  {INJECTIONS.map(([id, label]) => (
                    <button
                      key={id}
                      onClick={() => send({ type: "injection", injection_id: id })}
                      className="rounded border border-line px-3 py-2 text-xs text-ink hover:border-amber"
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="px-4 pb-4 text-xs text-faint">
                  Действует немедленно: меняет поведение заявителя в текущем вызове.
                </p>
              </section>

              <section className="rounded-md border border-line bg-surface">
                <div className="flex items-center gap-1.5 border-b border-lineSoft px-4 py-3">
                  <CheckCheck size={13} style={{ color: "#4E9E7E" }} />
                  <span className="text-sm font-semibold text-ink">Засчитать вопрос вручную</span>
                </div>
                <div className="flex flex-wrap gap-2 p-4">
                  {INTENTS.map(([id, label]) => {
                    const done = metrics?.checklist?.[id];
                    return (
                      <button
                        key={id}
                        onClick={() => send({ type: "override_intent", intent: id })}
                        disabled={done}
                        className="rounded px-3 py-2 text-xs disabled:opacity-45"
                        style={{
                          border: `1px solid ${done ? "#4E9E7E" : "#DCE3EA"}`,
                          color: done ? "#4E9E7E" : "#16202B",
                        }}
                      >
                        {done ? "✓ " : ""}{label}
                      </button>
                    );
                  })}
                </div>
                <p className="px-4 pb-4 text-xs text-faint">
                  Если классификатор не распознал корректно заданный вопрос — засчитайте его сами.
                  Действие попадёт в разбор с пометкой «вручную».
                </p>
              </section>
            </div>

            <section className="mt-4 rounded-md border border-line bg-surface">
              <div className="flex items-center gap-1.5 border-b border-lineSoft px-4 py-3">
                <Radio size={13} className="text-amber" />
                <span className="text-sm font-semibold text-ink">Лента вызова</span>
              </div>
              <div className="overflow-y-auto p-4" style={{ maxHeight: 260 }}>
                {log.length === 0 && (
                  <div className="text-xs text-faint">
                    События появятся, как только обучаемый начнёт диалог.
                  </div>
                )}
                {log.map((m, i) => (
                  <div key={i} className="mb-2.5 flex gap-3">
                    <span className="font-mono text-xs text-faint" style={{ minWidth: 42 }}>
                      {Math.floor(m.t / 60)}:{String(Math.floor(m.t % 60)).padStart(2, "0")}
                    </span>
                    <span className="text-xs" style={{
                      color: m.who === "caller" ? "#E0913C" : "#4E9E7E", minWidth: 74,
                    }}>
                      {m.who === "caller" ? "Заявитель" : "Система"}
                    </span>
                    <span className="flex-1 text-xs text-ink">{m.text}</span>
                  </div>
                ))}
                <div ref={logEnd} />
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
