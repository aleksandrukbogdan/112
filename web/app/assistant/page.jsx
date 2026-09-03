"use client";

/**
 * Режим ПОДДЕРЖКИ — боевое рабочее место оператора.
 *
 * Отличия от тренажёра:
 *   — заявитель настоящий, реплики приходят из ASR
 *   — оценок в эфире НЕ показываем, только подсказки
 *   — каждая подсказка и реакция на неё логируются
 *
 * Принцип: система обрабатывает вызов параллельно, но с заявителем
 * напрямую не разговаривает. Решение всегда за оператором.
 */

import { useState, useEffect, useRef, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:21121";

/* ---------------------------------------------------------------- утилиты */

const cx = (...a) => a.filter(Boolean).join(" ");

async function post(path, body) {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

/* ---------------------------------------------------------------- таймер */

function Timer({ sec, norm }) {
  const pct = Math.min(100, (sec / norm) * 100);
  const state = sec > norm ? "over" : pct > 85 ? "warn" : pct > 60 ? "note" : "ok";
  const color = { ok: "#22c55e", note: "#eab308", warn: "#f97316", over: "#ef4444" }[state];

  return (
    <div className="timer">
      <div className="timer-head">
        <span className="timer-val" style={{ color }}>
          {Math.floor(sec)}<span className="timer-unit">с</span>
        </span>
        <span className="timer-norm">норматив {norm} с</span>
      </div>
      <div className="timer-bar">
        <div className="timer-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="timer-src">ПП РФ 1931, п.9 подп.«р»</div>
    </div>
  );
}

/* ---------------------------------------------------------------- подсказка */

const KIND_META = {
  rule:     { label: "правило",  color: "#a78bfa" },
  question: { label: "вопрос",   color: "#5eead4" },
  timing:   { label: "время",    color: "#fbbf24" },
  risk:     { label: "риск",     color: "#f87171" },
  field:    { label: "поле",     color: "#93c5fd" },
  draft:    { label: "черновик", color: "#94a3b8" },
};

function HintCard({ h, onReact }) {
  const [open, setOpen] = useState(false);
  const meta = KIND_META[h.kind] || KIND_META.field;
  return (
    <div className={cx("hint", `hint-v${h.ves}`)}>
      <div className="hint-top">
        <span className="hint-kind" style={{ color: meta.color, borderColor: meta.color }}>
          {meta.label}
        </span>
        {h.obosnovanie && (
          <button className="hint-why" onClick={() => setOpen(!open)}>
            почему
          </button>
        )}
      </div>
      <div className="hint-text">{h.text}</div>
      {open && (
        <div className="hint-obosn">
          {h.obosnovanie}
          {h.istochnik && <div className="hint-src">{h.istochnik}</div>}
        </div>
      )}
      <div className="hint-acts">
        <button onClick={() => onReact(h.id, "accepted")}>задал</button>
        <button onClick={() => onReact(h.id, "dismissed")}>не нужно</button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- поле УКИО */

function Field({ f, value, onChange, classifiers }) {
  const opts = f.classifier ? classifiers[f.classifier] : f.options;
  const common = {
    value: value ?? "",
    onChange: (e) => onChange(f.key, e.target.value),
    disabled: f.auto,
    className: cx("fld", f.req && !value && "fld-req", f.auto && "fld-auto"),
  };
  return (
    <label className="field">
      <span className="field-lbl">
        {f.label}
        {f.req && <b className="req">*</b>}
        {f.auto && <span className="auto">авто</span>}
      </span>
      {f.type === "textarea" ? (
        <textarea rows={2} {...common} />
      ) : f.type === "bool" ? (
        <input type="checkbox" checked={!!value}
               onChange={(e) => onChange(f.key, e.target.checked)}
               className="fld-chk" />
      ) : opts ? (
        <select {...common}>
          <option value="">—</option>
          {opts.map((o) => (
            <option key={o.v ?? o.kod} value={o.v ?? o.kod}>
              {o.l ?? o.nazvanie}
            </option>
          ))}
        </select>
      ) : (
        <input type={f.type === "int" ? "number" : "text"} {...common} />
      )}
      {f.hint && <span className="field-hint">{f.hint}</span>}
    </label>
  );
}

/* ------------------------------------------------- списки спец.частей
 * Подозреваемые, больные, транспортные средства.
 * В УКИО это повторяющиеся блоки: у одного происшествия может быть
 * несколько пострадавших или несколько ТС.
 */

function SpisokBlok({ name, spisok, rows, onChange, classifiers }) {
  const add = () => onChange(name, [...rows, {}]);
  const del = (i) => onChange(name, rows.filter((_, k) => k !== i));
  const setCell = (i, key, val) =>
    onChange(name, rows.map((r, k) => (k === i ? { ...r, [key]: val } : r)));

  return (
    <div className="spisok">
      <div className="spisok-h">
        <h3>{spisok.nazvanie}</h3>
        <button className="spisok-add" onClick={add}>+ добавить</button>
      </div>
      {rows.length === 0 && <div className="spisok-empty">Записей нет</div>}
      {rows.map((row, i) => (
        <div key={i} className="spisok-row">
          <div className="spisok-num">
            {spisok.nazvanie.replace(/ы$|и$/, "")} {i + 1}
            <button className="spisok-del" onClick={() => del(i)}>убрать</button>
          </div>
          <div className="grp-fields">
            {spisok.polya.map((f) => (
              <Field key={f.key} f={f} value={row[f.key]}
                     onChange={(k, v) => setCell(i, k, v)}
                     classifiers={classifiers} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- страница */

export default function AssistantPage() {
  const [cfg, setCfg] = useState(null);
  const [classifiers, setClassifiers] = useState({});
  const [sid, setSid] = useState(null);
  const [sec, setSec] = useState(0);
  const [hints, setHints] = useState([]);
  const [card, setCard] = useState({});
  const [dialog, setDialog] = useState([]);
  const [ready, setReady] = useState(null);
  const [sluzhby, setSluzhby] = useState([]);
  const [tab, setTab] = useState("obshchaya");
  const [input, setInput] = useState("");
  const [sent, setSent] = useState(null);
  const [spiski, setSpiski] = useState({});   // подозреваемые, больные, ТС
  const tick = useRef(null);

  /* --- загрузка конфигурации --- */
  useEffect(() => {
    fetch(`${API}/api/v2/config`).then((r) => r.json()).then(async (c) => {
      setCfg(c);
      const names = new Set();
      const walk = (groups) =>
        Object.values(groups).forEach((g) =>
          g.forEach((f) => f.classifier && names.add(f.classifier)));
      walk(c.ukio.obshchaya);
      walk(c.ukio.uchol);
      Object.values(c.ukio.special).forEach((s) =>
        s.polya.forEach((f) => f.classifier && names.add(f.classifier)));
      const out = {};
      for (const n of names) {
        try {
          const d = await fetch(`${API}/api/v2/config/classifier/${n}`).then((r) => r.json());
          out[n] = d.items || [];
        } catch { out[n] = []; }
      }
      setClassifiers(out);
    }).catch(() => {});
  }, []);

  /* --- тикающий таймер + опрос подсказок --- */
  useEffect(() => {
    if (!sid) return;
    tick.current = setInterval(async () => {
      setSec((s) => s + 1);
      try {
        const d = await fetch(`${API}/api/v2/session/${sid}/podskazki`).then((r) => r.json());
        setHints(d.podskazki); setReady(d.gotovnost);
      } catch {}
    }, 1000);
    return () => clearInterval(tick.current);
  }, [sid]);

  /* --- горячие клавиши Alt+1..6 на службы --- */
  useEffect(() => {
    if (!cfg) return;
    const h = (e) => {
      if (!e.altKey) return;
      const r = cfg.routes.find((x) => x.hotkey === `Alt+${e.key}`);
      if (r) {
        e.preventDefault();
        setSluzhby((s) => s.includes(r.kod) ? s.filter((k) => k !== r.kod) : [...s, r.kod]);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [cfg]);

  const start = async () => {
    const r = await post("/api/v2/session", { rezhim: "assist" });
    setSid(r.session_id); setSec(0); setHints(r.podskazki);
    setDialog([{ kto: "system", tekst: r.privetstvie }]);
    setCard({}); setSluzhby([]); setSent(null); setSpiski({});
  };

  const send = async (kto) => {
    if (!input.trim() || !sid) return;
    setDialog((d) => [...d, { kto, tekst: input }]);
    const r = await post(`/api/v2/session/${sid}/replika`,
      { kto, tekst: input, t_ms: sec * 1000 });
    setHints(r.podskazki); setReady(r.gotovnost); setInput("");
  };

  const setField = useCallback(async (key, value) => {
    setCard((c) => ({ ...c, [key]: value }));
    if (!sid) return;
    const r = await post(`/api/v2/session/${sid}/pole`, { key, value });
    setHints(r.podskazki); setReady(r.gotovnost);
    if (r.avto?.uroven_chs) setCard((c) => ({ ...c, uroven_chs: r.avto.uroven_chs.value }));
    if (r.avto?.sluzhby) setSluzhby(r.avto.sluzhby.map((s) => s.kod));
  }, [sid]);

  const setSpisok = useCallback((name, rows) => {
    setSpiski((s) => ({ ...s, [name]: rows }));
    if (sid) post(`/api/v2/session/${sid}/pole`, { key: name, value: rows });
  }, [sid]);

  const react = (hid, reaction) => {
    if (sid) post(`/api/v2/session/${sid}/hint`, { hint_id: hid, reaction });
    setHints((h) => h.filter((x) => x.id !== hid));
  };

  const otpravit = async () => {
    const r = await post(`/api/v2/session/${sid}/otpravit`, { sluzhby });
    setSent(r);
  };

  if (!cfg) return <div className="wrap"><div className="load">Загрузка конфигурации…</div></div>;

  const norm = cfg.normativy.opros_i_kartochka_sec;
  const spec = cfg.ukio.special;
  const activeSpec = sluzhby
    .map((k) => cfg.routes.find((r) => r.kod === k))
    .filter((r) => r?.ukio_chast);

  return (
    <div className="wrap">
      {/* ------------------------------------------------ шапка */}
      <header className="hdr">
        <div>
          <h1>Поддержка оператора 112</h1>
          <div className="sub">
            {cfg.region.nazvanie} · {cfg.region.operator_112.korotko}
          </div>
        </div>
        <div className="hdr-r">
          {cfg.zaglushki.length > 0 && (
            <span className="zagl" title={cfg.zaglushki.map((z) => z.fayl).join(", ")}>
              заглушек в конфигурации: {cfg.zaglushki.length}
            </span>
          )}
          {!sid ? (
            <button className="btn-main" onClick={start}>Принять вызов</button>
          ) : (
            <Timer sec={sec} norm={norm} />
          )}
        </div>
      </header>

      {!sid ? (
        <div className="empty">
          <p>Режим поддержки. Заявитель настоящий, оценки в эфире не показываются.</p>
          <p className="empty-sub">
            Каждая подсказка и реакция на неё записываются в журнал.
            Окончательное решение всегда за оператором.
          </p>
        </div>
      ) : (
        <div className="grid">
          {/* -------------------------------------------- диалог */}
          <section className="col col-dialog">
            <h2>Разговор</h2>
            <div className="dlg">
              {dialog.map((d, i) => (
                <div key={i} className={cx("msg", `msg-${d.kto}`)}>
                  <span className="msg-who">
                    {d.kto === "operator" ? "Оператор" : d.kto === "caller" ? "Заявитель" : ""}
                  </span>
                  {d.tekst}
                </div>
              ))}
            </div>
            <div className="dlg-in">
              <input value={input} onChange={(e) => setInput(e.target.value)}
                     placeholder="Текст реплики"
                     onKeyDown={(e) => e.key === "Enter" && send("operator")} />
              <button onClick={() => send("operator")}>Оператор</button>
              <button className="alt" onClick={() => send("caller")}>Заявитель</button>
            </div>
            <div className="note">
              В боевом режиме реплики заявителя приходят из ASR автоматически.
            </div>
          </section>

          {/* -------------------------------------------- карточка */}
          <section className="col col-card">
            <div className="tabs">
              <button className={cx(tab === "obshchaya" && "on")}
                      onClick={() => setTab("obshchaya")}>Общая часть</button>
              {activeSpec.map((r) => (
                <button key={r.kod} className={cx(tab === r.ukio_chast && "on")}
                        onClick={() => setTab(r.ukio_chast)}>
                  {r.korotko}
                </button>
              ))}
            </div>

            <div className="card-body">
              {tab === "obshchaya"
                ? Object.entries(cfg.ukio.obshchaya).map(([g, fields]) => (
                    <div key={g} className="grp">
                      <h3>{{ sluzhebnoe: "Служебное", mesto: "Место происшествия",
                              proisshestvie: "Происшествие", ischod: "Исход" }[g] || g}</h3>
                      <div className="grp-fields">
                        {fields.map((f) => (
                          <Field key={f.key} f={f} value={card[f.key]}
                                 onChange={setField} classifiers={classifiers} />
                        ))}
                      </div>
                    </div>
                  ))
                : spec[tab] && (
                    <>
                      <div className="grp">
                        <h3>{spec[tab].nazvanie}</h3>
                        <div className="grp-fields">
                          {spec[tab].polya.map((f) => (
                            <Field key={f.key} f={f} value={card[f.key]}
                                   onChange={setField} classifiers={classifiers} />
                          ))}
                        </div>
                      </div>
                      {Object.entries(spec[tab].spiski || {}).map(([nm, sp]) => (
                        <SpisokBlok key={nm} name={`${tab}_${nm}`} spisok={sp}
                                    rows={spiski[`${tab}_${nm}`] || []}
                                    onChange={setSpisok} classifiers={classifiers} />
                      ))}
                    </>
                  )}
            </div>

            {/* службы */}
            <div className="routes">
              <div className="routes-h">Направить в ДДС <span>Alt+1…6</span></div>
              <div className="routes-b">
                {cfg.routes.map((r) => (
                  <button key={r.kod}
                          className={cx("rt", sluzhby.includes(r.kod) && "on")}
                          onClick={() => setSluzhby((s) =>
                            s.includes(r.kod) ? s.filter((k) => k !== r.kod) : [...s, r.kod])}>
                    {r.korotko}
                    {r.hotkey && <span className="rt-hk">{r.hotkey.replace("Alt+", "⌥")}</span>}
                  </button>
                ))}
              </div>
            </div>

            {ready && (
              <div className="ready">
                <span>Обязательных: {ready.zapolneno_obyazatelnyh}/{ready.vsego_obyazatelnyh}</span>
                <button className="btn-send" disabled={!ready.gotova || !sluzhby.length}
                        onClick={otpravit}>
                  Отправить карточку
                </button>
              </div>
            )}
            {sent && (
              <div className={cx("sent", sent.ok ? (sent.v_normativ ? "ok" : "late") : "err")}>
                {sent.ok
                  ? `Отправлено за ${sent.t_sec} с ${sent.v_normativ ? "— в норматив" : `— норматив ${sent.normativ_sec} с превышен`}`
                  : `Не заполнено: ${sent.ne_zapolneno?.join(", ")}`}
              </div>
            )}
          </section>

          {/* -------------------------------------------- подсказки */}
          <section className="col col-hints">
            <h2>Подсказки</h2>
            {hints.length === 0 && <div className="no-hints">Нет активных подсказок</div>}
            {hints.map((h) => <HintCard key={h.id} h={h} onReact={react} />)}
            <div className="note">
              Подсказки не заменяют решение. Всё показанное записывается в журнал.
            </div>
          </section>
        </div>
      )}

      <style jsx>{`
        .wrap { min-height:100vh; background:#0f1216; color:#e6e8ec;
                font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif; padding:16px 20px; }
        h1 { font-size:18px; font-weight:600; margin:0; }
        h2 { font-size:11px; text-transform:uppercase; letter-spacing:.07em;
             color:#8b93a1; margin:0 0 10px; font-weight:600; }
        h3 { font-size:11px; text-transform:uppercase; letter-spacing:.05em;
             color:#8b93a1; margin:14px 0 8px; font-weight:600; }
        .sub { color:#8b93a1; font-size:12px; margin-top:2px; }
        .hdr { display:flex; justify-content:space-between; align-items:flex-start;
               padding-bottom:14px; border-bottom:1px solid #232936; margin-bottom:16px; }
        .hdr-r { display:flex; gap:14px; align-items:center; }
        .zagl { font-size:11px; color:#fbbf24; border:1px solid #3a3020;
                background:#1f1a10; padding:4px 9px; border-radius:6px; }
        .btn-main { background:#5eead4; color:#0d1117; border:none; border-radius:8px;
                    padding:10px 20px; font-weight:600; font-size:14px; cursor:pointer;
                    font-family:inherit; }
        .empty { text-align:center; padding:70px 20px; color:#8b93a1; }
        .empty-sub { font-size:12px; max-width:440px; margin:10px auto 0; }
        .load { padding:60px; text-align:center; color:#8b93a1; }

        .timer { min-width:190px; }
        .timer-head { display:flex; justify-content:space-between; align-items:baseline; }
        .timer-val { font-size:26px; font-weight:600; font-variant-numeric:tabular-nums; }
        .timer-unit { font-size:14px; margin-left:2px; }
        .timer-norm { font-size:11px; color:#8b93a1; }
        .timer-bar { height:4px; background:#232936; border-radius:2px; margin-top:4px;
                     overflow:hidden; }
        .timer-fill { height:100%; transition:width .3s, background .3s; }
        .timer-src { font-size:10px; color:#5b6472; margin-top:3px; }

        .grid { display:grid; grid-template-columns: 1fr 1.5fr 1fr; gap:14px;
                align-items:start; }
        .col { background:#171b22; border:1px solid #232936; border-radius:12px; padding:14px; }
        .col-dialog { display:flex; flex-direction:column; height:calc(100vh - 130px); }
        .col-card { max-height:calc(100vh - 130px); overflow-y:auto; }
        .col-hints { max-height:calc(100vh - 130px); overflow-y:auto; }

        .dlg { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:8px; }
        .msg { padding:8px 11px; border-radius:9px; font-size:13px; max-width:92%; }
        .msg-operator { background:#1e3a34; align-self:flex-end; }
        .msg-caller { background:#232936; align-self:flex-start; }
        .msg-system { background:transparent; color:#8b93a1; font-size:12px;
                      align-self:center; text-align:center; }
        .msg-who { display:block; font-size:10px; color:#8b93a1; margin-bottom:2px; }
        .dlg-in { display:flex; gap:6px; margin-top:10px; }
        .dlg-in input { flex:1; background:#0f1216; border:1px solid #232936; color:#e6e8ec;
                        border-radius:7px; padding:8px 10px; font-family:inherit; font-size:13px; }
        .dlg-in button { background:#1e3a34; color:#5eead4; border:1px solid #2a4d45;
                         border-radius:7px; padding:8px 12px; cursor:pointer; font-size:12px;
                         font-family:inherit; }
        .dlg-in button.alt { background:#232936; color:#93c5fd; border-color:#2f3a4d; }
        .note { font-size:11px; color:#5b6472; margin-top:10px; }

        .tabs { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:12px; }
        .tabs button { background:#0f1216; border:1px solid #232936; color:#8b93a1;
                       border-radius:7px; padding:6px 11px; cursor:pointer; font-size:12px;
                       font-family:inherit; }
        .tabs button.on { background:#1e3a34; color:#5eead4; border-color:#2a4d45; }
        .grp-fields { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
        .field { display:flex; flex-direction:column; gap:3px; }
        .field-lbl { font-size:11px; color:#8b93a1; display:flex; gap:5px; align-items:center; }
        .req { color:#f87171; }
        .auto { font-size:9px; background:#232936; color:#5b6472; padding:1px 5px;
                border-radius:3px; }
        .field-hint { font-size:10px; color:#5b6472; }
        .fld { background:#0f1216; border:1px solid #232936; color:#e6e8ec;
               border-radius:6px; padding:6px 8px; font-size:13px; font-family:inherit;
               width:100%; }
        .fld-req { border-color:#4a2530; }
        .fld-auto { opacity:.55; }
        .fld-chk { width:16px; height:16px; }

        .spisok { margin-top:14px; border-top:1px solid #232936; padding-top:10px; }
        .spisok-h { display:flex; justify-content:space-between; align-items:center; }
        .spisok-add { background:#1e3a34; color:#5eead4; border:1px solid #2a4d45;
                      border-radius:6px; padding:3px 10px; font-size:11px;
                      cursor:pointer; font-family:inherit; }
        .spisok-empty { color:#5b6472; font-size:12px; padding:8px 0; }
        .spisok-row { background:#0f1216; border:1px solid #232936; border-radius:8px;
                      padding:10px; margin-top:8px; }
        .spisok-num { font-size:11px; color:#8b93a1; margin-bottom:7px;
                      display:flex; justify-content:space-between; }
        .spisok-del { background:none; border:none; color:#f87171; font-size:11px;
                      cursor:pointer; font-family:inherit; }

        .routes { margin-top:16px; border-top:1px solid #232936; padding-top:12px; }
        .routes-h { font-size:11px; text-transform:uppercase; letter-spacing:.05em;
                    color:#8b93a1; margin-bottom:8px; display:flex; justify-content:space-between; }
        .routes-h span { color:#5b6472; text-transform:none; letter-spacing:0; }
        .routes-b { display:flex; flex-wrap:wrap; gap:5px; }
        .rt { background:#0f1216; border:1px solid #232936; color:#8b93a1; border-radius:7px;
              padding:5px 10px; cursor:pointer; font-size:12px; font-family:inherit;
              display:flex; gap:5px; align-items:center; }
        .rt.on { background:#1e3a34; color:#5eead4; border-color:#5eead4; }
        .rt-hk { font-size:10px; opacity:.6; }

        .ready { display:flex; justify-content:space-between; align-items:center;
                 margin-top:14px; padding-top:12px; border-top:1px solid #232936;
                 font-size:12px; color:#8b93a1; }
        .btn-send { background:#5eead4; color:#0d1117; border:none; border-radius:7px;
                    padding:8px 16px; font-weight:600; cursor:pointer; font-family:inherit; }
        .btn-send:disabled { background:#232936; color:#5b6472; cursor:not-allowed; }
        .sent { margin-top:10px; padding:9px 12px; border-radius:8px; font-size:12px; }
        .sent.ok { background:#12271d; color:#4ade80; }
        .sent.late { background:#2a1f10; color:#fbbf24; }
        .sent.err { background:#2a1518; color:#f87171; }

        .hint { background:#0f1216; border:1px solid #232936; border-radius:9px;
                padding:10px; margin-bottom:8px; }
        .hint-v3 { border-left:3px solid #5eead4; }
        .hint-top { display:flex; justify-content:space-between; align-items:center;
                    margin-bottom:5px; }
        .hint-kind { font-size:10px; border:1px solid; padding:1px 6px; border-radius:4px; }
        .hint-why { background:none; border:none; color:#5b6472; font-size:11px;
                    cursor:pointer; text-decoration:underline; font-family:inherit; }
        .hint-text { font-size:13px; }
        .hint-obosn { margin-top:7px; padding:7px; background:#171b22; border-radius:6px;
                      font-size:11px; color:#8b93a1; }
        .hint-src { margin-top:4px; color:#5b6472; font-size:10px; }
        .hint-acts { display:flex; gap:5px; margin-top:8px; }
        .hint-acts button { background:#171b22; border:1px solid #232936; color:#8b93a1;
                            border-radius:5px; padding:3px 9px; font-size:11px; cursor:pointer;
                            font-family:inherit; }
        .no-hints { color:#5b6472; font-size:12px; padding:16px 0; text-align:center; }

        @media (max-width:1200px) { .grid { grid-template-columns:1fr; }
          .col-dialog,.col-card,.col-hints { height:auto; max-height:none; } }
      `}</style>
    </div>
  );
}
