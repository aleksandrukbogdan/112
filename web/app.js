/* Тренажёр оператора ДДС · Москва — клиент для трёх ролей */
"use strict";

const S = {
  token: localStorage.getItem("t112_token"), user: null, cfg: null, voice: false,
  view: null, scen: [], sc: null, assign: null,
  sid: null, t0: 0, timer: null, dialog: [], card: {}, svc: [],
  treeTop: null, treeFull: {}, grp: null, p1: null, p2: null,
  prognoz: null, rec: null, liveTimer: null,
};

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtT = ts => ts ? new Date(ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const pct = v => v == null ? "—" : Math.round(v * 100) + "%";
const col = v => v >= .8 ? "var(--ok)" : v >= .5 ? "var(--warn)" : "var(--bad)";

async function api(path, body, method) {
  const opt = { headers: { Authorization: "Bearer " + (S.token || "") } };
  if (body !== undefined) {
    opt.method = method || "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  } else if (method) opt.method = method;
  const r = await fetch(path, opt);
  if (r.status === 401 && S.user) { logout(); throw new Error("сессия истекла"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || ("ошибка " + r.status));
  return j;
}
const dl = p => `${p}${p.includes("?") ? "&" : "?"}token=${encodeURIComponent(S.token)}`;

function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show " + kind;
  clearTimeout(t._h); t._h = setTimeout(() => t.className = "toast", 3600);
}
function modal(html) { $("#modalBox").innerHTML = html; $("#modal").classList.add("show"); }
function closeModal() { $("#modal").classList.remove("show"); }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });

/* ================================================== вход */

function viewLogin(err) {
  $("#hdr").style.display = "none";
  $("#view").innerHTML = `
  <div class="login"><div class="login-box">
    <div class="logo">112</div>
    <h1>Тренажёр оператора ДДС</h1>
    <div class="sub" style="margin-bottom:22px">Департамент ГОЧСиПБ города Москвы</div>
    <div class="fld"><label>Логин</label><input id="lg" autocomplete="username"></div>
    <div class="fld"><label>Пароль</label><input id="pw" type="password" autocomplete="current-password"></div>
    ${err ? `<div class="badge b-bad" style="margin-bottom:12px">${esc(err)}</div>` : ""}
    <button class="btn" style="width:100%" id="go">Войти</button>
    <div class="hint" style="margin-top:16px">Учётные записи первого запуска —
      в журнале контейнера: <span class="mono">make logs</span></div>
  </div></div>`;
  const go = async () => {
    try {
      const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: $("#lg").value, password: $("#pw").value }) });
      const j = await r.json();
      if (!r.ok) return viewLogin(j.detail || "ошибка входа");
      S.token = j.token; localStorage.setItem("t112_token", j.token); boot();
    } catch { viewLogin("сервер недоступен"); }
  };
  $("#go").onclick = go;
  $("#pw").onkeydown = e => { if (e.key === "Enter") go(); };
  $("#lg").focus();
}

function logout() {
  localStorage.removeItem("t112_token"); S.token = null; S.user = null;
  clearInterval(S.timer); clearInterval(S.liveTimer); viewLogin();
}

/* ================================================== каркас */

const TABS = {
  trainee: [["tasks", "Задания"], ["dds", "Диспетчер ДДС"], ["pick", "Приём вызова 112"], ["progress", "Мой прогресс"], ["spravka", "Справка"]],
  teacher: [["monitor", "Мониторинг"], ["scen", "Сценарии"], ["assign", "Назначения"],
            ["group", "Аналитика группы"], ["grades", "Занятия и оценки"], ["reports", "Отчёты"], ["dds", "Пробный ДДС"], ["pick", "Пробный вызов 112"]],
  admin: [["system", "Система"], ["users", "Пользователи"], ["audit", "Журнал аудита"],
          ["backup", "Резервные копии"], ["settings", "Настройки"], ["reports", "Отчёты"]],
};
const ROLE_RU = { admin: "администратор", teacher: "преподаватель", trainee: "обучающийся" };

async function boot() {
  if (!S.token) return viewLogin();
  try {
    S.user = await api("/api/me");
    S.cfg = await api("/api/config");
    const h = await fetch("/health").then(r => r.json());
    S.voice = !!h.voice?.asr; S.tts = !!h.voice?.tts; S.engine = h.voice?.engine;
  } catch { return viewLogin(); }
  $("#hdr").style.display = "";
  const c = S.cfg.svodka;
  $("#sub").textContent = `${c.zakazchik} · классификатор ${c.klassifikator.versiya} · ` +
    `${c.klassifikator.poziciy} позиций · ${c.bilety.vsego} вызовов` +
    (S.voice ? ` · речь: ${S.engine === "gigaam" ? "GigaAM (GPU)" : "Vosk (CPU)"}` : "");
  $("#me").innerHTML = `<span class="nm"><b>${esc(S.user.name)}</b></span>
    <span class="role">${ROLE_RU[S.user.role]}</span>
    <button class="btn btn-g btn-sm" onclick="pwDialog()">Пароль</button>
    <button class="btn btn-g btn-sm" onclick="logout()">Выйти</button>`;
  const tabs = TABS[S.user.role];
  $("#nav").innerHTML = tabs.map(([k, n]) => `<button data-v="${k}">${n}</button>`).join("");
  document.querySelectorAll("#nav button").forEach(b => b.onclick = () => go(b.dataset.v));
  document.addEventListener("keydown", hotkeys);
  go(tabs[0][0]);
}

function go(v) {
  S.view = v;
  clearInterval(S.liveTimer);
  document.querySelectorAll("#nav button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  $("#hdrRight").innerHTML = "";
  const fn = VIEWS[v];
  if (fn) fn($("#view")).catch?.(e => toast(e.message, "bad"));
}

function pwDialog() {
  modal(`<h2>Смена пароля</h2>
    <div class="fld"><label>Текущий пароль</label><input id="po" type="password"></div>
    <div class="fld"><label>Новый пароль (от 6 символов)</label><input id="pn" type="password"></div>
    <div class="row"><button class="btn" onclick="pwSave()">Сохранить</button>
    <button class="btn btn-g" onclick="closeModal()">Отмена</button></div>`);
}
async function pwSave() {
  try { await api("/api/me/password", { old: $("#po").value, new: $("#pn").value });
    closeModal(); toast("Пароль изменён", "ok"); } catch (e) { toast(e.message, "bad"); }
}

/* ================================================== обучающийся: задания и выбор */

async function viewTasks(v) {
  const a = await api("/api/assignments");
  if (!a.length) {
    v.innerHTML = `<div class="empty"><div class="big">📋</div>Преподаватель ещё не назначил заданий.
      <br><br><button class="btn" onclick="go('pick')">Выбрать вызов самостоятельно</button></div>`;
    return;
  }
  v.innerHTML = `<div class="card"><h2>Мои задания <span class="badge b-dim">${a.length}</span></h2>
    <table class="t"><tr><th>Сценарий</th><th>Ситуация</th><th class="num">Лимит</th><th>Срок</th><th>Результат</th><th></th></tr>
    ${a.map(x => `<tr>
      <td><b>${esc(x.scenario?.nazvanie)}</b><br><span class="badge ${x.rezhim === "dds" ? "b-ac" : "b-dim"}">${x.rezhim === "dds" ? "диспетчер ДДС" + (x.dds_sluzhba ? " · " + esc(x.dds_sluzhba) : "") : "приём вызова 112"}</span></td>
      <td class="muted">${esc((x.scenario?.situaciya || "").slice(0, 80))}…</td>
      <td class="num">${x.tayming_sec || S.cfg.uchebny_tayming_sec} с</td>
      <td>${esc(x.deadline || "—")}</td>
      <td>${x.vypolneno ? `<span class="badge ${x.vypolneno.passed == null ? "b-dim" : x.vypolneno.passed ? "b-ok" : "b-bad"}">
        ${x.vypolneno.override_ball ?? x.vypolneno.ball} · ${x.vypolneno.passed == null ? "архив" : x.vypolneno.passed ? "зачёт" : "не зачтено"}</span>` : `<span class="badge b-dim">не выполнено</span>`}</td>
      <td><button class="btn btn-sm" onclick="${x.rezhim === "dds" ? `ddsStart('${x.scenario_id}',${x.id},${JSON.stringify(x.dds_sluzhba || "").replace(/"/g, "&quot;")})` : `startFrom('${x.scenario_id}',${x.id})`}">
        ${x.vypolneno ? "Ещё раз" : "Начать"}</button></td></tr>`).join("")}
    </table></div>`;
}

async function viewPick(v) {
  S.scen = await api("/api/scenarios" + (S.user.role !== "trainee" ? "?status=published" : ""));
  v.innerHTML = `
  <div class="grid g2">
    <div class="card">
      <h2>Вызовы <span class="badge b-dim">${S.scen.length}</span></h2>
      <input id="flt" placeholder="Поиск по ситуации или адресу…" style="margin-bottom:12px">
      <div class="list" id="blist"></div>
    </div>
    <div class="card"><h2>Вызов</h2><div id="prev">
      <div class="empty"><div class="big">🎧</div>Выберите вызов слева.<br>
      <span class="muted">Жёлтая метка — адрес придётся уточнять вопросом.</span></div></div></div>
  </div>`;
  const draw = f => {
    const L = $("#blist"); f = (f || "").toLowerCase();
    L.innerHTML = S.scen.filter(b => !f || (b.situaciya + b.adres_vidimy + b.nazvanie).toLowerCase().includes(f))
      .map(b => `<div class="bl" data-id="${b.id}">
        <div class="bl-h"><span class="bl-t">${esc(b.nazvanie)}</span>
          <span class="row" style="gap:6px">${b.trebuet_utochneniya ? '<span class="badge b-warn">адрес</span>' : ""}
          ${b.source === "gen" ? '<span class="badge b-ac">сгенерирован</span>' : ""}
          <span class="dots">${[1, 2, 3, 4, 5].map(i => `<span class="dot ${i <= b.slozhnost ? "f" : ""}"></span>`).join("")}</span></span></div>
        <div class="bl-s">${esc(b.situaciya.slice(0, 120))}${b.situaciya.length > 120 ? "…" : ""}</div></div>`).join("");
    L.querySelectorAll(".bl").forEach(n => n.onclick = () => {
      L.querySelectorAll(".bl").forEach(x => x.classList.remove("on")); n.classList.add("on");
      preview(S.scen.find(b => b.id === n.dataset.id));
    });
  };
  draw(); $("#flt").oninput = e => draw(e.target.value);
}

function preview(b) {
  $("#prev").innerHTML = `
    <h3>Ситуация</h3><div style="font-size:13.5px;line-height:1.6">${esc(b.situaciya)}</div>
    <h3>Заявитель говорит, что находится</h3><div class="muted" style="line-height:1.6">${esc(b.adres_vidimy)}</div>
    ${b.trebuet_utochneniya ? `<div class="fact no" style="margin-top:12px"><div class="fact-n">Адрес придётся уточнять</div>
      <div class="fact-src">Эталон скрыт — сверяется автоматически после отправки карточки</div></div>` : ""}
    <h3>Группа происшествия</h3><span class="badge b-ac">${esc(S.cfg.gruppy[b.gruppa] || b.gruppa || "Определите по ситуации")}</span>
    <h3>Нормативы</h3><div class="row"><span class="badge b-dim">карточка ${S.cfg.normativy.kartochka_sec} с</span>
      <span class="badge b-dim">учебный лимит ${S.cfg.uchebny_tayming_sec} с</span></div>
    <div class="hint">${esc(S.cfg.normativy.istochnik)}</div><hr>
    <button class="btn" style="width:100%" onclick="startFrom('${b.id}')">Принять вызов</button>`;
}

/* ================================================== вызов */

async function startFrom(scId, aid) {
  try {
    const r = await api("/api/session", { scenario_id: scId, assignment_id: aid || null });
    S.sid = r.session_id; S.t0 = Date.now(); S.tayming = r.tayming_sec; S.sc = r.scenario;
    S.dialog = [{ kto: "sys", tekst: r.privetstvie }];
    S.card = {}; S.svc = []; S.prognoz = null; S.grp = S.p1 = S.p2 = null;
    if (!S.treeTop) S.treeTop = await api("/api/tree");
    viewCall();
    clearInterval(S.timer); S.timer = setInterval(tick, 250);
    setTimeout(() => { if (S.sid) askPrognoz(true); }, 30000);
  } catch (e) { toast(e.message, "bad"); }
}

function tick() {
  const box = $("#timer"); if (!box) return;
  const t = (Date.now() - S.t0) / 1000, n = S.cfg.normativy.kartochka_sec;
  const p = Math.min(100, t / n * 100);
  const c = t > n ? "var(--bad)" : t > S.tayming ? "var(--warn)" : p > 60 ? "#eab308" : "var(--ok)";
  box.className = "timer" + (t > n ? " timer-over" : "");
  box.innerHTML = `<div class="timer-top"><span class="timer-v" style="color:${c}">${Math.floor(t)}<span class="timer-u">с</span></span>
    <span class="timer-n">лимит ${S.tayming} с · норматив ${n} с</span></div>
    <div class="timer-bar"><div class="timer-fill" style="width:${p}%;background:${c}"></div></div>
    <div class="timer-src">ПП РФ 1931, п. 9 подп. «р»</div>`;
}

function viewCall() {
  const v = $("#view");
  document.querySelectorAll("#nav button").forEach(b => b.classList.remove("on"));
  $("#hdrRight").innerHTML = `<div class="timer" id="timer"></div>`;
  v.innerHTML = `
  <div class="grid g3">
    <section class="card">
      <h2>Разговор <span class="badge b-dim">${esc(S.sc.id)}</span></h2>
      <div class="dlg" id="dlg"></div>
      <div class="dlg-in">
        <button class="mic" id="mic" title="${S.voice ? "Нажмите и говорите, нажмите ещё раз — отправить" : "Голосовой сервис не запущен"}"
          ${S.voice ? "" : "disabled"}>🎙</button>
        <input id="inp" placeholder="Реплика заявителю…" autocomplete="off">
        <button class="btn" onclick="send()">→</button>
      </div>
      <div class="hint">Точный адрес заявитель назовёт только на уточняющий вопрос:
        «уточните номер дома», «как проехать», «ближайший адрес».</div>
    </section>
    <section class="card">
      <h2>Карточка происшествия <span class="badge b-dim" id="cardState">0/5</span></h2>
      <div class="fld"><label class="req">Описание со слов заявителя</label>
        <textarea id="f_opisanie" oninput="setField('opisanie',this.value)"></textarea></div>
      <div class="fld"><label class="req">Адрес происшествия</label>
        <input id="f_adres" oninput="setField('adres_polny',this.value)" placeholder="Город, улица, дом, корпус, строение, км…"></div>
      <div class="row" style="gap:10px">
        <div class="fld" style="flex:1"><label class="req">ФИО заявителя</label>
          <input id="f_fio" oninput="setField('zayavitel_fio',this.value)"></div>
        <div class="fld" style="flex:1"><label class="req">Телефон</label>
          <input id="f_tel" oninput="setField('zayavitel_telefon',this.value)"></div>
      </div><hr>
      <h3>Что случилось? <span class="muted">быстрый выбор, как в АРМ</span></h3><div class="tagwrap" id="quick"></div>
      <h3>Тип происшествия — классификатор 0.46.24</h3><div id="tags"></div><hr>
      <h3>Признаки <span class="muted">влияют на состав служб</span></h3><div class="tagwrap" id="priz"></div><hr>
      <h3>Направить в ДДС <span class="muted">Alt + 1…6</span></h3><div class="svc" id="svc"></div><hr>
      <div class="row sp"><span class="muted" id="ready"></span>
        <div class="row"><button class="btn btn-g btn-sm" onclick="abandon()">Прервать</button>
        <button class="btn btn-ok" id="send" onclick="finish()" disabled>Отправить карточку</button></div></div>
    </section>
    <section class="card"><h2>Подсказки и прогноз</h2><div id="side"></div></section>
  </div>`;
  drawDlg(); drawQuick(); drawTags(); drawPriz(); drawSvc(); updReady(); drawSide(); tick();
  $("#inp").focus();
  $("#inp").onkeydown = e => { if (e.key === "Enter") send(); };
  if (S.voice) $("#mic").onclick = toggleMic;
}

function drawDlg() {
  const d = $("#dlg"); if (!d) return;
  d.innerHTML = S.dialog.map(m => m.kto === "sys" ? `<div class="msg m-sys">${esc(m.tekst)}</div>` :
    `<div class="msg ${m.kto === "operator" ? "m-op" : "m-cl"}"><b>${m.kto === "operator" ? "Оператор" : "Заявитель"}</b>${esc(m.tekst)}</div>`).join("");
  d.scrollTop = d.scrollHeight;
}

async function send(text) {
  const i = $("#inp"); const t = (text ?? i.value).trim();
  if (!t || !S.sid) return;
  i.value = "";
  S.dialog.push({ kto: "operator", tekst: t }); drawDlg();
  try {
    const r = await api(`/api/session/${S.sid}/replika`, { tekst: t });
    S.dialog.push({ kto: "caller", tekst: r.otvet }); drawDlg();
    if (r.narusheniya_rechi?.length) {
      const n = r.narusheniya_rechi[0];
      toast(`Речь: «${n.fragment}» — ${n.opisanie}` + (n.zamena ? `. Лучше: «${n.zamena}»` : ""), "bad");
    }
    if (r.podskazka) toast(r.podskazka, "ok");
    if (S.tts) new Audio(dl(`/api/voice/tts?text=${encodeURIComponent(r.otvet)}`)).play().catch(() => {});
  } catch (e) { toast(e.message, "bad"); }
}
function quick(q) { send(q); }

/* ---------- голос ---------- */
async function toggleMic() {
  const b = $("#mic");
  if (S.rec) { S.rec.stop(); return; }
  try {
    const st = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ch = []; const rec = new MediaRecorder(st); S.rec = rec;
    rec.ondataavailable = e => ch.push(e.data);
    rec.onstop = async () => {
      st.getTracks().forEach(t => t.stop()); S.rec = null; b.classList.remove("rec");
      const fd = new FormData(); fd.append("audio", new Blob(ch, { type: rec.mimeType }), "rec.webm");
      try {
        const r = await fetch("/api/voice/asr", { method: "POST", headers: { Authorization: "Bearer " + S.token }, body: fd });
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail);
        if (j.text) { send(j.text); if (j.latency_ms != null) toast(`${j.engine === "gigaam" || String(j.engine).startsWith("gigaam") ? "GigaAM" : "Vosk"} · ${j.latency_ms} мс`); }
        else toast("Речь не распознана", "bad");
      } catch (e) { toast("Голос: " + e.message, "bad"); }
    };
    rec.start(); b.classList.add("rec");
  } catch {
    toast("Нет доступа к микрофону. Откройте через http://localhost (туннель) — браузер разрешает микрофон только там или по https", "bad");
  }
}

/* ---------- «что случилось?» — быстрые кнопки АРМ ---------- */
const QUICK = { "ДТП": "2", "101": "1", "102": "15", "103": "22", "104": "13",
  "Человек в опасности": "17", "Ребенок в опасности": "18", "Смертельный исход": "19",
  "Угроза взрыва/террористического акта": "4", "Аварии и происшествия в городском хозяйстве": "14" };
function drawQuick() {
  const b = $("#quick"); if (!b) return;
  const names = Object.keys(QUICK).filter(n => (S.cfg.chto_sluchilos || []).includes(n) || /^10[1-4]$/.test(n));
  b.innerHTML = names.map(n => `<button class="tag ${S.grp === QUICK[n] ? "on" : ""}" data-qg="${QUICK[n]}">${esc(n)}</button>`).join("");
  b.querySelectorAll("[data-qg]").forEach(x => x.onclick = async () => { await pickG(x.dataset.qg); drawQuick(); });
}

/* ---------- теги классификатора ---------- */
function drawTags() {
  const box = $("#tags"); if (!box) return;
  let h = `<div class="tagwrap">` + Object.values(S.treeTop).map(g =>
    `<button class="tag grp ${S.grp === g.kod ? "on" : ""}" data-g="${g.kod}">${esc(g.nazvanie)}</button>`).join("") + `</div>`;
  const g = S.grp && S.treeFull[S.grp];
  if (g) {
    h += `<h3>Где / характер</h3><div class="tagwrap">` + Object.keys(g.p1).map((k, i) =>
      `<button class="tag ${S.p1 === k ? "on" : ""}" data-p1="${i}">${esc(k)}</button>`).join("") + `</div>`;
    if (S.p1 && g.p1[S.p1]) {
      h += `<h3>Уточнение</h3><div class="tagwrap">` + Object.keys(g.p1[S.p1].p2).map((k, i) =>
        `<button class="tag ${S.p2 === k ? "on" : ""}" data-p2="${i}">${esc(k)}</button>`).join("") + `</div>`;
      if (S.p2 && g.p1[S.p1].p2[S.p2]) {
        h += `<h3>Признак</h3><div class="tagwrap">` + Object.entries(g.p1[S.p1].p2[S.p2].p3).map(([k, x]) =>
          `<button class="tag ${S.card.tip_kod === x.kod ? "on" : ""}" data-p3="${x.kod}">${esc(k)}</button>`).join("") + `</div>`;
      }
    }
  }
  if (S.card.tip_itog) h += `<div class="fact ok" style="margin-top:11px"><div class="fact-n">${esc(S.card.tip_itog)}</div>
    <div class="fact-src">код ${S.card.tip_kod} · классификатор 0.46.24</div></div>`;
  box.innerHTML = h;
  box.querySelectorAll("[data-g]").forEach(b => b.onclick = () => pickG(b.dataset.g));
  if (g) {
    const k1 = Object.keys(g.p1);
    box.querySelectorAll("[data-p1]").forEach(b => b.onclick = () => { S.p1 = k1[+b.dataset.p1]; S.p2 = null; drawTags(); });
    if (S.p1) {
      const k2 = Object.keys(g.p1[S.p1].p2);
      box.querySelectorAll("[data-p2]").forEach(b => b.onclick = () => { S.p2 = k2[+b.dataset.p2]; drawTags(); });
    }
    box.querySelectorAll("[data-p3]").forEach(b => b.onclick = () => pickP3(b.dataset.p3));
  }
}
async function pickG(k) {
  S.grp = k; S.p1 = S.p2 = null;
  if (!S.treeFull[k]) S.treeFull[k] = await api(`/api/tree?gruppa=${k}`);
  drawTags();
}
async function pickP3(kod) {
  const node = S.treeFull[S.grp].p1[S.p1].p2[S.p2].p3;
  const it = Object.values(node).find(x => x.kod === kod);
  S.card.tip_kod = kod; S.card.tip_itog = it.itog;
  const r = await api(`/api/session/${S.sid}/pole`, { key: "tip_kod", value: kod });
  if (r.avto?.sluzhby) { S.svc = r.avto.sluzhby; toast(`Службы из классификатора: ${r.avto.sluzhby.join(", ") || "нет безусловных"}`, "ok"); }
  drawTags(); drawSvc(); updReady();
}

const POST = [{ v: "net", l: "Пострадавших нет" }, { v: "est", l: "Есть пострадавшие" }, { v: "ne_na_meste", l: "Пострадавшие не на месте" }];
function drawPriz() {
  const b = $("#priz"); if (!b) return;
  b.innerHTML = POST.map(p => `<button class="tag ${S.card.postradavshie === p.v ? "on" : ""}" data-pv="${p.v}">${p.l}</button>`).join("") +
    `<button class="tag ${S.card.pravonarushenie ? "on" : ""}" id="prv">Правонарушение</button>`;
  b.querySelectorAll("[data-pv]").forEach(x => x.onclick = () => setPriz("postradavshie", x.dataset.pv));
  $("#prv").onclick = () => setPriz("pravonarushenie", !S.card.pravonarushenie);
}
async function setPriz(k, v) {
  S.card[k] = S.card[k] === v ? null : v;
  const r = await api(`/api/session/${S.sid}/pole`, { key: k, value: S.card[k] });
  if (r.avto?.sluzhby) {
    S.svc = r.avto.sluzhby;
    toast("Службы пересчитаны: " + Object.entries(r.avto.prichiny || {}).map(([s, p]) => `${s} — ${p}`).join(" · "), "ok");
  } else if (!S.card.tip_kod) toast("Сначала выберите тип происшествия", "");
  drawPriz(); drawSvc(); updReady();
}

function drawSvc() {
  const b = $("#svc"); if (!b) return;
  b.innerHTML = S.cfg.routes.map(r => {
    const on = S.svc.includes(r.kod);
    return `<button class="${on ? "on" : ""}" data-k="${r.kod}"
      style="${on ? `background:${r.color};border-color:${r.color}` : `color:${r.color}`}">
      ${esc(r.korotko)}<span class="hk">⌥${r.hotkey}</span></button>`;
  }).join("");
  b.querySelectorAll("[data-k]").forEach(x => x.onclick = () => toggleSvc(x.dataset.k));
}
function toggleSvc(k) { S.svc = S.svc.includes(k) ? S.svc.filter(x => x !== k) : [...S.svc, k]; drawSvc(); updReady(); }
function hotkeys(e) {
  if (!S.sid || !e.altKey) return;
  const r = S.cfg.routes.find(x => x.hotkey === e.key);
  if (r) { e.preventDefault(); toggleSvc(r.kod); }
}

const _deb = {};
function setField(k, v) {
  S.card[k] = v; clearTimeout(_deb[k]);
  _deb[k] = setTimeout(() => api(`/api/session/${S.sid}/pole`, { key: k, value: v }).catch(() => {}), 400);
  updReady();
}
function updReady() {
  const need = ["opisanie", "adres_polny", "zayavitel_fio", "zayavitel_telefon", "tip_kod"];
  const done = need.filter(k => String(S.card[k] || "").trim()).length;
  const ok = done === need.length && S.svc.length > 0;
  if ($("#ready")) $("#ready").textContent = `Обязательных: ${done}/${need.length} · служб: ${S.svc.length}`;
  if ($("#send")) $("#send").disabled = !ok;
  const c = $("#cardState");
  if (c) { c.textContent = ok ? "готова" : `${done}/${need.length}`; c.className = "badge " + (ok ? "b-ok" : "b-dim"); }
}

async function askPrognoz(auto) {
  if (!S.sid) return;
  try {
    S.prognoz = await api(`/api/session/${S.sid}/prognoz`);
    drawSide();
    if (auto) toast(`${S.prognoz.label || "Прогноз"} на ${S.prognoz.t_prognoza_sec} с: уложится в норматив с вероятностью ${S.prognoz.veroyatnost}`);
  } catch {}
}
function drawSide() {
  const s = $("#side"); if (!s) return;
  let h = "";
  if (S.prognoz) {
    const p = S.prognoz, g = p.veroyatnost >= .5;
    h += `<div class="fact ${g ? "ok" : "no"}"><div class="fact-h"><span class="fact-n">Прогноз: уложится в норматив</span>
      <span class="badge ${g ? "b-ok" : "b-bad"}">${p.veroyatnost}</span></div>
      <div class="fact-src">${esc(p.label || "Прогноз")} · сделан на ${p.t_prognoza_sec} с · проверка на ${p.proverka_na_sec} с</div>
      <div class="fact-d">полей ${p.osnovanie.poley_gotovo}/${p.osnovanie.poley_vsego} · осталось ${p.osnovanie.ostalos_sec} с · нужно ≈${p.osnovanie.nuzhno_sec} с</div></div>`;
  } else h += `<div class="muted">Прогноз появится на 30-й секунде.</div>
    <button class="btn btn-g btn-sm" style="margin-top:9px" onclick="askPrognoz()">Запросить сейчас</button>`;
  h += `<h3>Быстрые вопросы</h3><div class="tagwrap">` +
    ["Что у вас случилось?", "Назовите точный адрес", "Уточните номер дома", "Как лучше проехать?",
     "Представьтесь, пожалуйста", "Номер телефона для связи?", "Есть пострадавшие?"].map(q =>
      `<button class="tag" data-q="${esc(q)}">${esc(q)}</button>`).join("") + `</div>
    <h3>Порядок</h3><ol class="muted" style="padding-left:18px;line-height:1.9;font-size:12.5px">
    <li>Что случилось</li><li>Точный адрес</li><li>Пострадавшие</li><li>ФИО и телефон</li>
    <li>Тип по классификатору</li><li>Проверить службы → отправить</li></ol>`;
  s.innerHTML = h;
  s.querySelectorAll("[data-q]").forEach(b => b.onclick = () => quick(b.dataset.q));
}

async function abandon() {
  if (!confirm("Прервать вызов? Результат не сохранится.")) return;
  clearInterval(S.timer); S.sid = null; go(TABS[S.user.role][0][0]);
}

async function finish() {
  clearInterval(S.timer);
  Object.values(_deb).forEach(clearTimeout);
  try {
    const r = await api(`/api/session/${S.sid}/otpravit`, { sluzhby: S.svc, kartochka: S.card });
    const sid = S.sid; S.sid = null;
    showRazbor(sid, r);
  } catch (e) { toast(e.message, "bad"); }
}

function factsHtml(fakty) {
  return fakty.map(f => `<div class="fact ${f.proyden == null ? "" : f.proyden ? "ok" : "no"}">
    <div class="fact-h"><span class="fact-n">${esc(f.nazvanie)}</span>
      <span class="badge ${f.proyden == null ? "b-dim" : f.proyden ? "b-ok" : "b-bad"}">${f.proyden == null ? "не проверено" : f.proyden ? "зачтено" : "не зачтено"} · вес ${f.ves}</span></div>
    <div class="fact-src">${esc(f.istochnik)}</div>
    <div class="fact-d">${f.proyden == null ? "Недостаточно наблюдений" : esc(detali(f))}</div>
    ${(f.evidence_event_ids || []).slice(-3).map(id => `<button class="btn btn-g btn-sm" data-evidence="${esc(id)}">Событие ${esc(id.slice(0,8))}</button>`).join("")}</div>`).join("");
}
function detali(f) {
  const d = f.detali || {};
  switch (f.kod) {
    case "adres": return `введено: «${d.vvod || ""}» · эталон: «${d.etalon || ""}» · совпадение ${pct(d.dolya)}` + (d.propushcheno?.length ? ` · не хватает: ${d.propushcheno.join(", ")}` : "");
    case "tip": return `выбрано: ${d.vybrano || "—"} ${d.itog ? "(" + d.itog + ")" : ""} · группа ${d.gruppa_vybrana || "—"}, эталон ${d.gruppa_etalon}`;
    case "sluzhby": return `нужно: ${(d.nado || []).join(", ") || "—"} · назначено: ${(d.naznacheno || []).join(", ") || "—"}` + (d.propushcheno?.length ? ` · пропущено: ${d.propushcheno.join(", ")}` : "");
    case "normativ": case "uchebny_tayming": return `факт ${d.fakt_sec} с · предел ${d.normativ_sec || d.limit_sec} с`;
    case "polnota": return d.ne_zapolneno?.length ? `не заполнено: ${d.ne_zapolneno.join(", ")}` : "все поля заполнены";
    case "rech": return d.spisok?.length ? d.spisok.map(n => `«${n.fragment}»` + (n.zamena ? ` → «${n.zamena}»` : "")).join(" · ") : "нарушений нет";
    case "grammatika": return d.spisok?.length ? d.spisok.map(g => `«${g.fragment}» — ${g.zamechanie}`).join(" · ") : "замечаний нет";
  }
  return JSON.stringify(d);
}

function showRazbor(sid, r) {
  const v = $("#view"); $("#hdrRight").innerHTML = "";
  const c = r.passed ? "var(--ok)" : r.ball >= 50 ? "var(--warn)" : "var(--bad)";
  const att = r.attestaciya;
  v.innerHTML = `
  <div class="grid g2">
    <div>
      <div class="kpis" style="margin-bottom:14px">
        <div class="kpi"><div class="v" style="color:${c}">${r.ball}</div><div class="l">Балл · ${r.verdikt}</div>
          <div class="n">${r.nabrano} из ${r.vsego} весов</div></div>
        <div class="kpi"><div class="v" style="color:${r.v_normativ ? "var(--ok)" : "var(--bad)"}">${r.t_sec}<span style="font-size:15px">с</span></div>
          <div class="l">Время карточки</div><div class="n">норматив ${S.cfg.normativy.kartochka_sec} с</div></div>
        ${att ? `<div class="kpi"><div class="v" style="color:var(--vio)">${att.vyzovov_do_attestacii ?? "—"}</div>
          <div class="l">Условных повторений навыка</div><div class="n">BKT · не допуск к работе</div></div>` : ""}
      </div>
      <button class="btn btn-g" onclick="openRazbor('${sid}')">История действий и версии</button>
      <div class="card"><h2>Граф доказательств <span class="badge b-dim">каждый балл со ссылкой на норму</span></h2>
        ${factsHtml(r.fakty)}</div>
    </div>
    <div>
      <div class="card" style="margin-bottom:14px"><h2>Проверка прогноза</h2><div id="pchk"></div></div>
      ${r.bkt && Object.keys(r.bkt).length ? `<div class="card" style="margin-bottom:14px"><h2>Освоение навыков · BKT</h2>
        <div class="bars">${Object.entries(r.bkt).map(([k, x]) => `<div class="bar-row">
          <span>${esc(S.cfg.skills.find(s => s.kod === k)?.nazvanie || k)}</span>
          <div class="bar"><i style="width:${x.p * 100}%;background:${x.osvoen ? "var(--ok)" : col(x.p)}"></i></div>
          <span class="num">${pct(x.p)}</span></div>`).join("")}</div>
        <div class="hint">Вероятность освоения. Порог — 95%. Обновляется после каждого вызова.</div></div>` : ""}
      <div class="card"><h2>Разговор</h2>
        <div class="dlg" style="height:auto;max-height:340px">${S.dialog.filter(m => m.kto !== "sys").map(m =>
          `<div class="msg ${m.kto === "operator" ? "m-op" : "m-cl"}"><b>${m.kto === "operator" ? "Оператор" : "Заявитель"}</b>${esc(m.tekst)}</div>`).join("")}</div>
        <hr><div class="row">
          <button class="btn" onclick="go('${S.user.role === "trainee" ? "tasks" : "pick"}')">Далее</button>
          <a class="btn btn-g" href="${dl(`/api/otchet/zanyatie/${sid}.pdf`)}">Отчёт PDF</a>
          ${S.user.role === "trainee" ? `<button class="btn btn-g" onclick="go('progress')">Мой прогресс</button>` : ""}
        </div></div>
    </div>
  </div>`;
  const p = $("#pchk");
  if (S.prognoz) {
    const ok = (S.prognoz.veroyatnost >= .5) === r.v_normativ;
    p.innerHTML = `<div class="fact ${ok ? "ok" : "no"}"><div class="fact-h"><span class="fact-n">${ok ? "Прогноз подтвердился" : "Прогноз не подтвердился"}</span>
      <span class="badge ${ok ? "b-ok" : "b-bad"}">${ok ? "верно" : "мимо"}</span></div>
      <div class="fact-d">на ${S.prognoz.t_prognoza_sec} с: вероятность ${S.prognoz.veroyatnost} · факт: ${r.v_normativ ? "уложился" : "не уложился"} (${r.t_sec} с)</div>
      <div class="fact-src">прогноз сохранён до факта — это и делает его проверяемым</div></div>`;
  } else p.innerHTML = `<div class="muted">В этом вызове прогноз не запрашивался.</div>`;
}

/* ================================================== аналитика: общие блоки */

function navBars(nav) {
  return `<div class="bars">${nav.map(n => `<div class="bar-row"><span>${esc(n.nazvanie)}</span>
    <div class="bar"><i style="width:${n.znachenie * 100}%;background:${col(n.znachenie)}"></i></div>
    <span class="num" style="color:${col(n.znachenie)}">${pct(n.znachenie)} <span class="muted">n=${n.n}</span></span></div>`).join("")}</div>`;
}
function heatHtml(heat, drillable) {
  if (!heat.length) return `<div class="muted">Нет данных.</div>`;
  const gr = [...new Set(heat.map(h => h.gruppa))].sort((a, b) => a - b);
  return `<div class="heat"><table><tr><th></th>${S.cfg.skills.map(s => `<th>${esc(s.nazvanie)}</th>`).join("")}</tr>
    ${gr.map(g => `<tr><th class="lbl">${esc(String(heat.find(h => h.gruppa === g).gruppa_nazvanie).slice(0, 38))}</th>` +
      S.cfg.skills.map(s => {
        const c = heat.find(h => h.gruppa === g && h.skill === s.kod);
        if (!c) return `<td style="background:#0d1119;color:var(--dim2)">—</td>`;
        const d = c.dolya_provalov;
        const bg = d === 0 ? "rgba(52,211,153,.22)" : d < .34 ? "rgba(251,191,36,.22)" : d < .67 ? "rgba(249,115,22,.3)" : "rgba(248,113,113,.34)";
        const click = drillable && c.sessii.length ? `class="clickable" onclick="drillCell('${c.sessii.join(",")}','${esc(s.nazvanie)}')"` : "";
        return `<td style="background:${bg}" title="n=${c.n}" ${click}>${Math.round(d * 100)}%<br><span style="font-size:9px;opacity:.6">n=${c.n}</span></td>`;
      }).join("") + `</tr>`).join("")}</table></div>
    <div class="hint">Доля провалов, в каждой клетке число наблюдений.${drillable ? " Нажмите на клетку — список вызовов." : ""}</div>`;
}
function dinamHtml(d) {
  if (!d.length) return `<div class="muted">Нет данных.</div>`;
  const W = 400, x = i => 30 + i * (360 / Math.max(1, d.length - 1)), y = b => 120 - b * 1.1;
  return `<svg viewBox="0 0 ${W} 150" style="width:100%;height:150px">
    <line x1="30" y1="120" x2="390" y2="120" stroke="var(--br)"/><line x1="30" y1="10" x2="30" y2="120" stroke="var(--br)"/>
    <line x1="30" y1="${y(70)}" x2="390" y2="${y(70)}" stroke="var(--ok)" stroke-dasharray="3 4" opacity=".5"/>
    <text x="4" y="16" fill="var(--dim2)" font-size="9">100</text><text x="10" y="123" fill="var(--dim2)" font-size="9">0</text>
    <text x="360" y="${y(70) - 3}" fill="var(--ok)" font-size="8" opacity=".7">зачёт</text>
    ${d.length > 1 ? `<polyline fill="none" stroke="var(--ac)" stroke-width="2" points="${d.map((p, i) => `${x(i)},${y(p.ball)}`).join(" ")}"/>` : ""}
    ${d.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.ball)}" r="3.5" fill="var(--ac)"/>`).join("")}</svg>
    <div class="hint">Ось Y от нуля. Точек: ${d.length}.${d.length < 5 ? " Для тренда данных недостаточно." : ""}</div>`;
}
function prognozTable(p, kind) {
  if (!p.vsego) return `<div class="muted">Прогнозы ещё не проверены фактом.</div>`;
  return `<table class="t"><tr><th>Прогноз</th><th>Факт</th><th></th></tr>${p.spisok.map(x => `<tr>
    <td>${kind === "call" ? `на ${x.data.t_prognoza_sec} с: ${x.data.veroyatnost}` : `${x.data.vyzovov_do_attestacii} вызовов`}</td>
    <td>${kind === "call" ? (x.fakt.uspel ? "уложился" : "не уложился") : `${x.fakt.vyzovov} вызовов`}</td>
    <td><span class="badge ${x.verno ? "b-ok" : "b-bad"}">${x.verno ? "верно" : "мимо"}</span></td></tr>`).join("")}</table>`;
}
async function drillCell(ids, nav) {
  const rows = await api(`/api/analytics/drill?ids=${ids}`);
  modal(`<h2>Провалы навыка «${esc(nav)}»</h2><table class="t"><tr><th>Обучающийся</th><th>Сценарий</th><th class="num">Балл</th><th class="num">Время</th><th></th></tr>
    ${rows.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.scenario_id)}</td><td class="num">${r.ball}</td><td class="num">${r.t_sec} с</td>
    <td><button class="btn btn-g btn-sm" onclick="openRazbor('${r.id}')">Разбор</button></td></tr>`).join("")}</table>
    <hr><button class="btn btn-g" onclick="closeModal()">Закрыть</button>`);
}
async function openRazbor(sid) { return detailedRazbor(sid); }

/* ================================================== обучающийся: прогресс и справка */

async function viewProgress(v) { return viewInsights(v); }

async function viewSpravka(v) {
  const s = await api("/api/spravka");
  v.innerHTML = `<div class="sect">${s.razdely.map(r => `<div class="card spravka"><h2>${esc(r.nazvanie)}</h2>
    <ul>${r.punkty.map(p => `<li>${esc(p)}</li>`).join("")}</ul></div>`).join("")}</div>
    <div class="card" style="margin-top:14px"><h2>Нормативная база</h2>
    <ul class="spravka">${s.istochniki.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div>`;
}

/* ================================================== преподаватель */

async function viewMonitor(v) {
  const draw = async () => {
    if (S.view !== "monitor") return;
    const L = await api("/api/live").catch(() => []);
    const n = S.cfg.normativy.kartochka_sec;
    v.innerHTML = `<div class="card"><h2>Идущие занятия <span class="badge ${L.length ? "b-ok" : "b-dim"}">${L.length}</span>
      <span class="muted">обновление каждые 2 с</span></h2>
      ${L.length ? `<div class="live">${L.map(x => x.rezhim === "ДДС" ? `<div class="live-c">
        <div class="row sp"><b>${esc(x.user)}</b><span class="badge b-ac">ДДС · ${esc(x.scenario)}</span></div>
        <div class="live-t">${Math.floor(x.t_sec)} с</div>
        <div class="muted" style="font-size:12px">статус: <b>${esc(x.status)}</b> · звонков ${x.zvonkov} · замечаний ${x.zamechaniy}</div>
        <div style="margin-top:6px"><span class="pill">${esc({ ne_vyehala: "бригада не направлена", vyezd: "бригада в пути", pribytie: "на месте", raboty: "идут работы", zaversheno: "работы завершены" }[x.faza] || x.faza)}</span>
          ${x.narusheniy ? `<span class="pill" style="color:var(--bad)">речь: ${x.narusheniy}</span>` : ""}</div></div>` : `<div class="live-c">
        <div class="row sp"><b>${esc(x.user)}</b><span class="badge b-dim">${esc(x.scenario)}</span></div>
        <div class="live-t" style="color:${x.t_sec > n ? "var(--bad)" : x.t_sec > n * .8 ? "var(--warn)" : "var(--ok)"}">${Math.floor(x.t_sec)} с</div>
        <div class="bar" style="margin:6px 0"><i style="width:${x.poley / x.vsego_poley * 100}%;background:var(--ac)"></i></div>
        <div class="muted" style="font-size:12px">полей ${x.poley}/${x.vsego_poley} · реплик ${x.replik}</div>
        <div style="margin-top:6px"><span class="pill">${x.adres_raskryt ? "адрес уточнён" : "адрес не уточнён"}</span>
          ${x.narusheniy ? `<span class="pill" style="color:var(--bad)">речь: ${x.narusheniy}</span>` : ""}
          ${x.sluzhby.map(s => `<span class="pill">${s}</span>`).join("")}</div>
        ${x.posledneye ? `<div class="hint">«${esc(x.posledneye)}»</div>` : ""}</div>`).join("")}</div>`
        : `<div class="empty"><div class="big">🎧</div>Сейчас никто не проходит вызов.</div>`}</div>`;
  };
  await draw(); S.liveTimer = setInterval(draw, 2000);
}

async function viewScen(v) {
  const all = await api("/api/scenarios");
  const drafts = all.filter(s => s.status === "draft");
  v.innerHTML = `
  <div class="grid g2">
    <div class="card"><h2>Генерация сценария</h2>
      <div class="hint" style="margin:0 0 12px">Модель придумывает обстоятельства и речь заявителя.
        Тип и службы — эталон из классификатора. К обучающимся сценарий попадёт только после вашего утверждения.</div>
      <div class="fld"><label>Тип происшествия — поиск по классификатору</label>
        <input id="gq" placeholder="например: пожар, ДТП, запах газа…"></div>
      <div id="gres" class="list" style="max-height:220px"></div>
      <div class="row" style="gap:10px;margin-top:10px">
        <div class="fld" style="flex:1"><label>Сложность</label><select id="gs">${[1, 2, 3, 4, 5].map(i => `<option ${i === 3 ? "selected" : ""}>${i}</option>`).join("")}</select></div>
        <div class="fld" style="flex:1"><label>Количество</label><select id="gn">${[1, 2, 3, 5].map(i => `<option>${i}</option>`).join("")}</select></div>
        <div class="fld" style="flex:2"><label>Адрес</label><select id="gu"><option value="1">ориентир, нужно уточнять</option><option value="0">назван сразу</option></select></div>
      </div>
      <button class="btn" id="gbtn" disabled>Сгенерировать</button></div>
    <div class="card"><h2>Ждут утверждения <span class="badge ${drafts.length ? "b-warn" : "b-dim"}">${drafts.length}</span></h2>
      ${drafts.length ? drafts.map(d => `<div class="bl"><div class="bl-h"><span class="bl-t">${esc(d.tip_etalon)}</span>
        <span class="badge b-dim">${esc(d.sgenerirovan?.istochnik || "")}</span></div>
        <div class="bl-s">${esc(d.situaciya)}</div>
        <div class="row" style="margin-top:8px"><button class="btn btn-sm" onclick="validDlg('${d.id}')">Проверить и утвердить</button></div></div>`).join("")
        : `<div class="muted">Черновиков нет.</div>`}</div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Все сценарии <span class="badge b-dim">${all.length}</span></h2>
    <table class="t"><tr><th>Название</th><th>Источник</th><th>Статус</th><th class="num">Сложн.</th><th>Адрес</th></tr>
    ${all.slice(0, 200).map(s => `<tr><td>${esc(s.nazvanie)}</td><td>${s.source === "bilet" ? "билеты ДГОЧСиПБ" : "генератор"}</td>
      <td><span class="badge ${s.status === "published" ? "b-ok" : s.status === "draft" ? "b-warn" : "b-bad"}">${{ published: "опубликован", draft: "черновик", rejected: "отклонён" }[s.status]}</span></td>
      <td class="num">${s.slozhnost}</td><td>${s.trebuet_utochneniya ? "уточнять" : "—"}</td></tr>`).join("")}</table></div>`;
  let pick = null, deb;
  $("#gq").oninput = e => { clearTimeout(deb); deb = setTimeout(async () => {
    const r = await api(`/api/classifier/search?q=${encodeURIComponent(e.target.value)}`);
    $("#gres").innerHTML = r.map(x => `<div class="bl" data-k="${x.kod}"><div class="bl-t">${esc(x.itog)}</div>
      <div class="bl-s">${x.kod} · ${esc(x.put)}</div></div>`).join("") || `<div class="muted">Ничего не найдено</div>`;
    $("#gres").querySelectorAll(".bl").forEach(n => n.onclick = () => {
      $("#gres").querySelectorAll(".bl").forEach(z => z.classList.remove("on")); n.classList.add("on");
      pick = n.dataset.k; $("#gbtn").disabled = false; });
  }, 250); };
  $("#gq").dispatchEvent(new Event("input"));
  $("#gbtn").onclick = async () => {
    $("#gbtn").disabled = true; $("#gbtn").textContent = "Генерация…";
    try { const r = await api("/api/scenarios/generate", { kod: pick, slozhnost: +$("#gs").value,
        kolichestvo: +$("#gn").value, utochnenie: $("#gu").value === "1" });
      toast(`Создано черновиков: ${r.length}`, "ok"); viewScen(v);
    } catch (e) { toast(e.message, "bad"); $("#gbtn").disabled = false; $("#gbtn").textContent = "Сгенерировать"; }
  };
}

async function validDlg(id) {
  const d = (await api("/api/scenarios?status=draft")).find(x => x.id === id);
  modal(`<h2>Проверка сценария</h2>
    <div class="badge b-ac" style="margin-bottom:12px">эталон: ${esc(d.tip_etalon)} · код ${d.kod_etalon}</div>
    <div class="fld"><label>Ситуация</label><textarea id="vs" rows="3">${esc(d.situaciya)}</textarea></div>
    <div class="row" style="gap:10px"><div class="fld" style="flex:1"><label>ФИО заявителя</label><input id="vf" value="${esc(d.zayavitel.fio)}"></div>
      <div class="fld" style="flex:1"><label>Телефон</label><input id="vt" value="${esc(d.zayavitel.telefon)}"></div></div>
    <div class="fld"><label>Как заявитель называет место</label><input id="vv" value="${esc(d.adres_vidimy)}"></div>
    <div class="fld"><label>Эталонный адрес (скрыт от обучающегося)</label><input id="ve" value="${esc(d.adres_etalon)}"></div>
    <div class="fld"><label>Комментарий</label><input id="vc" placeholder="необязательно"></div>
    <div class="row"><button class="btn btn-ok" onclick="validSave('${id}','published')">Утвердить и опубликовать</button>
      <button class="btn btn-g" onclick="validSave('${id}','rejected')">Отклонить</button>
      <button class="btn btn-g" onclick="closeModal()">Отмена</button></div>`);
}
async function validSave(id, st) {
  try { await api(`/api/scenarios/${id}/validate`, { status: st, comment: $("#vc").value,
      data: { situaciya: $("#vs").value, adres_vidimy: $("#vv").value, adres_etalon: $("#ve").value,
              zayavitel: { fio: $("#vf").value, telefon: $("#vt").value } } });
    closeModal(); toast(st === "published" ? "Опубликован" : "Отклонён", "ok"); go("scen");
  } catch (e) { toast(e.message, "bad"); }
}

async function viewAssign(v) {
  if (!DS.sluzhby) DS.sluzhby = await api("/api/dds/sluzhby");
  const [g, a, sc] = await Promise.all([api("/api/groups"), api("/api/assignments"), api("/api/scenarios?status=published")]);
  v.innerHTML = `
  <div class="grid g2">
    <div class="card"><h2>Новое назначение</h2>
      <div class="fld"><label>Группа</label><select id="ag">${g.map(x => `<option value="${x.id}">${esc(x.name)} (${x.n})</option>`).join("")}</select></div>
      <div class="row" style="gap:10px"><div class="fld" style="flex:1"><label>Режим</label><select id="arz">
        <option value="dds">Диспетчер ДДС</option><option value="ops112">Приём вызова 112</option></select></div>
        <div class="fld" style="flex:2"><label>Служба обучаемого (для ДДС)</label><select id="asl"><option value="">— по карточке —</option>
        ${(DS.sluzhby || []).map(s => `<option>${esc(s.korotko)}</option>`).join("")}</select></div></div>
      <div class="row" style="gap:10px"><div class="fld" style="flex:1"><label>Лимит времени, с (ТЗ: по умолчанию 30)</label>
        <input id="at" type="number" value="${S.cfg.uchebny_tayming_sec}" min="10" max="600"></div>
        <div class="fld" style="flex:1"><label>Срок</label><input id="ad" type="date"></div></div>
      <div class="fld"><label>Сценарии <span class="muted" id="acnt">выбрано 0</span></label>
        <input id="af" placeholder="фильтр…" style="margin-bottom:6px">
        <div class="list" id="al" style="max-height:300px"></div></div>
      <button class="btn" onclick="assignSave()">Назначить</button></div>
    <div class="card"><h2>Назначено <span class="badge b-dim">${a.length}</span></h2>
      <table class="t"><tr><th>Группа</th><th>Сценарий</th><th class="num">Лимит</th><th>Срок</th><th></th></tr>
      ${a.map(x => `<tr><td>${esc(x.gruppa)}</td><td>${esc(x.scenario?.nazvanie)}</td><td class="num">${x.tayming_sec || "—"}</td>
        <td>${esc(x.deadline || "—")}</td><td><button class="btn btn-g btn-sm" onclick="assignDel(${x.id})">✕</button></td></tr>`).join("")}</table></div>
  </div>`;
  S.asel = new Set();
  const draw = f => { f = (f || "").toLowerCase();
    $("#al").innerHTML = sc.filter(s => !f || (s.nazvanie + s.situaciya).toLowerCase().includes(f)).map(s =>
      `<label class="bl" style="display:flex;gap:9px;align-items:flex-start;margin-bottom:6px">
        <input type="checkbox" data-id="${s.id}" ${S.asel.has(s.id) ? "checked" : ""} style="width:auto;margin-top:3px">
        <span><b style="font-size:12.5px">${esc(s.nazvanie)}</b><br><span class="bl-s">${esc(s.situaciya.slice(0, 90))}</span></span></label>`).join("");
    $("#al").querySelectorAll("input").forEach(c => c.onchange = () => {
      c.checked ? S.asel.add(c.dataset.id) : S.asel.delete(c.dataset.id); $("#acnt").textContent = `выбрано ${S.asel.size}`; });
  };
  draw(); $("#af").oninput = e => draw(e.target.value);
}
async function assignSave() {
  if (!S.asel.size) return toast("Выберите сценарии", "bad");
  try { const r = await api("/api/assignments", { group_id: +$("#ag").value, scenario_ids: [...S.asel],
      rezhim: $("#arz").value, dds_sluzhba: $("#asl").value || null,
      tayming_sec: +$("#at").value || null, deadline: $("#ad").value || null });
    toast(`Назначено: ${r.naznacheno}`, "ok"); go("assign");
  } catch (e) { toast(e.message, "bad"); }
}
async function assignDel(id) { await api(`/api/assignments/${id}`, undefined, "DELETE"); go("assign"); }

async function viewGroup(v) {
  const g = await api("/api/groups");
  if (!g.length) { v.innerHTML = `<div class="empty">Групп нет.</div>`; return; }
  S.gid = S.gid || g[0].id;
  const a = await api(`/api/analytics/group/${S.gid}`);
  v.innerHTML = `
  <div class="row sp" style="margin-bottom:14px"><select id="gsel" style="max-width:320px">${g.map(x =>
    `<option value="${x.id}" ${x.id === S.gid ? "selected" : ""}>${esc(x.name)} · ${x.n} чел.</option>`).join("")}</select>
    <a class="btn btn-g btn-sm" href="${dl(`/api/otchet/export.xlsx?group_id=${S.gid}`)}">Excel</a></div>
  <div class="kpis" style="margin-bottom:14px">
    <div class="kpi"><div class="v">${a.uchastnikov}</div><div class="l">Обучающихся</div></div>
    <div class="kpi"><div class="v">${a.n}</div><div class="l">Вызовов</div></div>
    <div class="kpi"><div class="v" style="color:var(--ac)">${a.sredniy_ball ?? "—"}</div><div class="l">Средний балл</div><div class="n">n=${a.n}</div></div>
    <div class="kpi"><div class="v" style="color:var(--ok)">${a.n ? Math.round(a.v_normativ / a.n * 100) + "%" : "—"}</div><div class="l">В норматив</div></div>
    <div class="kpi"><div class="v" style="color:var(--vio)">${a.prognozy_vyzov.tochnost != null ? pct(a.prognozy_vyzov.tochnost) : "—"}</div><div class="l">Точность прогнозов</div><div class="n">n=${a.prognozy_vyzov.vsego}</div></div>
  </div>
  ${a.tipichnye_oshibki.length ? `<div class="card" style="margin-bottom:14px"><h2>Типичные ошибки группы</h2>
    ${a.tipichnye_oshibki.map(t => `<div class="fact no"><div class="fact-n">${esc(t)}</div></div>`).join("")}</div>` : ""}
  <div class="card" style="margin-bottom:14px"><h2>Обучающиеся <span class="badge b-dim">нажмите строку — подробно</span></h2>
    <table class="t"><tr><th>Имя</th><th class="num">Вызовов</th><th class="num">Балл</th><th class="num">В норматив</th><th>Слабый навык</th><th>До освоения (BKT)</th></tr>
    ${a.obuchayushchiesya.map(t => `<tr class="clickable" onclick="userDetail(${t.id})"><td><b>${esc(t.name)}</b></td>
      <td class="num">${t.n}</td><td class="num">${t.ball ?? "—"}</td><td class="num">${t.v_norm}/${t.n}</td>
      <td>${esc(t.slabyy || "—")}</td><td>${t.gotov ? '<span class="badge b-ok">освоено</span>' : `<span class="badge ${t.do_attestacii > 10 ? "b-bad" : "b-warn"}">${t.do_attestacii ?? "—"} повторений</span>`}</td></tr>`).join("")}</table></div>
  <div class="grid g2">
    <div class="card"><h2>Навыки группы</h2>${a.navyki.length ? navBars(a.navyki) : '<div class="muted">Нет данных.</div>'}</div>
    <div class="card"><h2>Динамика группы</h2>${dinamHtml(a.dinamika)}</div>
  </div>
  <div class="card" style="margin-top:14px"><h2>Тепловая карта: группа происшествий × навык</h2>${heatHtml(a.heatmap, true)}</div>`;
  $("#gsel").onchange = e => { S.gid = +e.target.value; viewGroup(v); };
}
async function userDetail(uid) {
  modal('<div id="userInsights"></div>');
  return viewInsights($("#userInsights"), uid);
}
async function legacyUserDetail(uid) {
  const a = await api(`/api/analytics/user/${uid}`);
  modal(`<h2>${esc(a.user.name)}</h2>
    <div class="row" style="margin-bottom:12px"><span class="badge b-dim">вызовов ${a.n}</span>
      <span class="badge b-ac">балл ${a.sredniy_ball ?? "—"}</span>
      <span class="badge b-warn">до освоения ${a.attestaciya.vyzovov_do_attestacii}</span></div>
    <h3>Освоение · BKT</h3><div class="bars">${Object.entries(a.bkt).map(([k, x]) => `<div class="bar-row">
      <span>${esc(S.cfg.skills.find(s => s.kod === k)?.nazvanie || k)}</span><div class="bar"><i style="width:${x.p * 100}%;background:${col(x.p)}"></i></div>
      <span class="num">${pct(x.p)}</span></div>`).join("")}</div>
    <h3>Динамика</h3>${dinamHtml(a.dinamika)}
    <h3>Рекомендации</h3>${a.rekomendacii.map(r => `<div class="fact no"><div class="fact-n">${esc(r.tekst)}</div>
      <div class="fact-src">${r.bilety.map(b => esc(b.nazvanie)).join(" · ")}</div></div>`).join("") || '<div class="muted">Нет.</div>'}
    <h3>Вызовы</h3><table class="t">${a.dinamika.slice().reverse().map(d => `<tr class="clickable" onclick="openRazbor('${d.id}')">
      <td>${esc(d.scenario)}</td><td class="num">${d.ball}</td><td class="num">${d.t_sec} с</td></tr>`).join("")}</table>
    <hr><div class="row"><a class="btn btn-g" href="${dl(`/api/otchet/sertifikat/${uid}.pdf`)}">Сертификат</a>
    <button class="btn btn-g" onclick="closeModal()">Закрыть</button></div>`);
}

async function viewGrades(v) {
  const s = await api("/api/sessions");
  v.innerHTML = `<div class="card"><h2>Завершённые занятия <span class="badge b-dim">${s.length}</span>
    <a class="btn btn-g btn-sm" href="${dl("/api/otchet/export.xlsx")}">Excel</a></h2>
    <table class="t"><tr><th>Дата</th><th>Обучающийся</th><th>Сценарий</th><th class="num">Балл</th><th class="num">Время</th><th></th></tr>
    ${s.map(x => `<tr><td>${fmtT(x.started)}</td><td>${esc(x.name)}</td><td>${esc(x.scenario_id)}</td>
      <td class="num">${x.override_ball != null ? `<s class="muted">${x.ball}</s> ${x.override_ball}` : x.ball}</td>
      <td class="num">${x.t_sec} с</td><td class="row" style="gap:5px">
      <button class="btn btn-g btn-sm" onclick="openRazbor('${x.id}')">Разбор</button>
      <button class="btn btn-g btn-sm" onclick="overrideDlg('${x.id}',${x.override_ball ?? x.ball})">Оценка</button></td></tr>`).join("")}</table>
    <div class="hint">Изменение оценки фиксируется в журнале аудита с причиной (требование ТЗ).</div></div>`;
}
function overrideDlg(sid, ball) {
  modal(`<h2>Изменение оценки</h2>
    <div class="fld"><label>Новый балл (0–100)</label><input id="ob" type="number" min="0" max="100" value="${ball}"></div>
    <div class="fld"><label>Причина (обязательно, попадёт в журнал аудита и отчёт)</label><textarea id="or"></textarea></div>
    <div class="row"><button class="btn" onclick="overrideSave('${sid}')">Сохранить</button>
    <button class="btn btn-g" onclick="closeModal()">Отмена</button></div>`);
}
async function overrideSave(sid) {
  try { await api(`/api/session/${sid}/override`, { ball: +$("#ob").value, reason: $("#or").value });
    closeModal(); toast("Оценка изменена", "ok"); if (S.view === "grades") go("grades");
  } catch (e) { toast(e.message, "bad"); }
}

async function viewReports(v) {
  const [f1, f2] = await Promise.all([api("/api/otchet/forma1"), api("/api/otchet/forma2")]);
  v.innerHTML = `<div class="grid g2">
    <div class="card"><h2>Форма 1/112 <span class="badge b-ac">приказ МЧС № 192</span></h2>
      <div class="muted" style="margin-bottom:10px">${esc(f1.istochnik)} · ${esc(f1.subekt)} · вызовов ${f1.vyzovov}</div>
      <table class="t"><tr><th>№</th><th>Наименование</th><th class="num">Требование</th><th class="num">Факт</th></tr>
      ${f1.stroki.map(s => `<tr><td class="muted">${s.n}</td><td>${esc(s.nazvanie)}</td><td class="num">${s.trebovanie}</td>
        <td class="num" style="color:${s.fakt == null ? "var(--dim2)" : s.fakt <= s.trebovanie ? "var(--ok)" : "var(--bad)"}">${s.fakt ?? "—"}</td></tr>`).join("")}</table>
      <div class="hint">Прочерк — параметр не моделируется в учебном контуре.</div></div>
    <div class="card"><h2>Форма 2/112 <span class="badge b-ac">приказ МЧС № 192</span></h2>
      <div class="muted" style="margin-bottom:10px">${esc(f2.istochnik)} · всего ${f2.vsego}</div>
      <table class="t"><tr><th>Направление</th><th class="num">Количество</th></tr>
      ${f2.po_napravleniyam.map(r => `<tr><td>${esc(r.nazvanie)}</td><td class="num">${r.kolichestvo}</td></tr>`).join("")}</table>
      <hr><a class="btn btn-g" href="${dl("/api/otchet/export.xlsx")}">Выгрузить все занятия в Excel</a></div></div>`;
}

/* ================================================== администратор */

async function viewSystem(v) {
  const s = await api("/api/admin/system");
  const ok = x => `<span class="badge ${x ? "b-ok" : x === null ? "b-dim" : "b-bad"}">${x ? "работает" : x === null ? "выключено" : "недоступно"}</span>`;
  v.innerHTML = `<div class="kpis" style="margin-bottom:14px">
    <div class="kpi"><div class="v">${s.users}</div><div class="l">Пользователей</div></div>
    <div class="kpi"><div class="v">${s.zanyatiy_idyot}</div><div class="l">Занятий идёт</div></div>
    <div class="kpi"><div class="v">${s.zaversheno}</div><div class="l">Завершено</div></div>
    <div class="kpi"><div class="v">${(s.db_size / 1e6).toFixed(1)}<span style="font-size:14px">МБ</span></div><div class="l">База</div><div class="n">свободно ${s.disk_free_gb} ГБ</div></div>
  </div>
  <div class="grid g2"><div class="card"><h2>Компоненты</h2><table class="t">
    <tr><td>API и интерфейс</td><td>${ok(true)}</td></tr>
    <tr><td>Локальная модель (${esc(s.svodka.llm.model)})</td><td>${ok(s.llm.ok)}</td></tr>
    ${s.llm.note ? `<tr><td colspan="2" class="muted">${esc(s.llm.note)}</td></tr>` : ""}
    <tr><td>Распознавание: GigaAM v3 (GPU)${s.voice.gigaam?.gpu ? " · " + esc(s.voice.gigaam.gpu) : ""}</td><td>${ok(!!s.voice.gigaam?.ok)}</td></tr>
    <tr><td>Распознавание: Vosk (CPU, запасной)</td><td>${ok(!!s.voice.vosk)}</td></tr>
    <tr><td>Синтез речи: F5-TTS</td><td>${ok(!!s.voice.tts)}</td></tr>
    <tr><td>Сейчас распознаёт</td><td><b>${s.voice.engine === "gigaam" ? "GigaAM" : s.voice.engine === "vosk" ? "Vosk" : "—"}</b></td></tr>
    ${s.voice.note ? `<tr><td colspan="2" class="muted">${esc(s.voice.note)}</td></tr>` : ""}
    <tr><td>Резервных копий</td><td>${s.backups}</td></tr></table></div>
  <div class="card"><h2>Данные</h2><table class="t">
    <tr><td>Классификатор</td><td class="mono">v${s.svodka.klassifikator.versiya} · ${s.svodka.klassifikator.poziciy} поз.</td></tr>
    <tr><td>SHA-256 источника</td><td class="mono">${s.svodka.klassifikator.sha256}…</td></tr>
    ${s.scenarios.map(x => `<tr><td>Сценарии: ${x.status}</td><td>${x.n}</td></tr>`).join("")}
    <tr><td>Учебный лимит</td><td>${s.tayming} с</td></tr></table></div></div>`;
}

async function viewUsers(v) {
  const [u, g] = await Promise.all([api("/api/users"), api("/api/groups")]);
  const gopt = sel => `<option value="">—</option>` + g.map(x => `<option value="${x.id}" ${x.id === sel ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  v.innerHTML = `<div class="grid g2">
    <div class="card"><h2>Новый пользователь</h2>
      <div class="row" style="gap:10px"><div class="fld" style="flex:1"><label>Логин</label><input id="nl"></div>
        <div class="fld" style="flex:1"><label>Пароль (от 6)</label><input id="np"></div></div>
      <div class="fld"><label>ФИО</label><input id="nn"></div>
      <div class="row" style="gap:10px"><div class="fld" style="flex:1"><label>Роль</label><select id="nr">
        <option value="trainee">обучающийся</option><option value="teacher">преподаватель</option><option value="admin">администратор</option></select></div>
        <div class="fld" style="flex:1"><label>Группа</label><select id="ng">${gopt(g[0]?.id)}</select></div></div>
      <button class="btn" onclick="userAdd()">Создать</button>
      <hr><h3>Новая группа</h3><div class="row"><input id="gname" placeholder="Название" style="flex:1">
        <button class="btn btn-g" onclick="groupAdd()">Добавить</button></div></div>
    <div class="card"><h2>Пользователи <span class="badge b-dim">${u.length}</span></h2>
      <table class="t"><tr><th>Имя</th><th>Логин</th><th>Роль</th><th>Группа</th><th></th></tr>
      ${u.map(x => `<tr style="${x.active ? "" : "opacity:.45"}"><td>${esc(x.name)}</td><td class="mono">${esc(x.login)}</td>
        <td>${ROLE_RU[x.role]}</td><td>${x.role === "trainee" ? `<select onchange="userEdit(${x.id},{group_id:+this.value||0})" style="padding:4px">${gopt(x.group_id)}</select>` : "—"}</td>
        <td class="row" style="gap:4px"><button class="btn btn-g btn-sm" onclick="userPw(${x.id})">Пароль</button>
        <button class="btn btn-g btn-sm" onclick="userEdit(${x.id},{active:${!x.active}})">${x.active ? "Откл." : "Вкл."}</button></td></tr>`).join("")}</table></div></div>`;
}
async function userAdd() {
  try { await api("/api/users", { login: $("#nl").value, password: $("#np").value, name: $("#nn").value,
      role: $("#nr").value, group_id: +$("#ng").value || null });
    toast("Создан", "ok"); go("users"); } catch (e) { toast(e.message, "bad"); }
}
async function groupAdd() { try { await api("/api/groups", { name: $("#gname").value }); go("users"); } catch (e) { toast(e.message, "bad"); } }
async function userEdit(id, b) { try { await api(`/api/users/${id}`, b); toast("Сохранено", "ok"); go("users"); } catch (e) { toast(e.message, "bad"); } }
function userPw(id) {
  modal(`<h2>Сброс пароля</h2><div class="fld"><label>Новый пароль (от 6 символов)</label><input id="up"></div>
    <div class="row"><button class="btn" onclick="userEdit(${id},{password:$('#up').value});closeModal()">Сохранить</button>
    <button class="btn btn-g" onclick="closeModal()">Отмена</button></div>`);
}

async function viewAudit(v) {
  const a = await api("/api/admin/audit");
  const RU = { login: "вход", login_fail: "неудачный вход", grade_override: "изменение оценки", scenario_generate: "генерация сценария",
    scenario_published: "сценарий утверждён", scenario_rejected: "сценарий отклонён", assign: "назначение", assign_delete: "снятие назначения",
    user_create: "создан пользователь", user_edit: "изменён пользователь", group_create: "создана группа", backup: "резервная копия",
    settings: "настройки", password_change: "смена пароля" };
  v.innerHTML = `<div class="card"><h2>Журнал аудита <span class="badge b-dim">${a.length}</span></h2>
    <table class="t"><tr><th>Время</th><th>Пользователь</th><th>Действие</th><th>Подробности</th></tr>
    ${a.map(x => `<tr><td>${fmtT(x.ts)}</td><td class="mono">${esc(x.login)}</td>
      <td><span class="badge ${x.action.includes("fail") || x.action.includes("override") ? "b-warn" : "b-dim"}">${esc(RU[x.action] || x.action)}</span></td>
      <td class="muted mono" style="font-size:11px">${esc(x.details)}</td></tr>`).join("")}</table></div>`;
}

async function viewBackup(v) {
  const b = await api("/api/admin/backups");
  v.innerHTML = `<div class="card"><h2>Резервные копии <span class="badge b-dim">автоматически раз в сутки · хранится 14</span></h2>
    <button class="btn" onclick="backupNow()">Создать сейчас</button><hr>
    <table class="t"><tr><th>Файл</th><th>Дата</th><th class="num">Размер</th><th></th></tr>
    ${b.map(x => `<tr><td class="mono">${esc(x.name)}</td><td>${fmtT(x.ts)}</td><td class="num">${(x.size / 1024).toFixed(0)} КБ</td>
      <td class="row" style="gap:4px"><a class="btn btn-g btn-sm" href="${dl(`/api/admin/backup/${x.name}`)}">Скачать</a>
      <button class="btn btn-g btn-sm" onclick="backupRestore('${x.name}')">Восстановить</button></td></tr>`).join("") || `<tr><td colspan="4" class="muted">Копий пока нет</td></tr>`}</table>
    <div class="hint">Перед восстановлением автоматически создаётся страховочная копия текущего состояния.</div></div>`;
}
async function backupRestore(n) {
  if (!confirm(`Восстановить базу из ${n}? Текущие данные будут заменены (страховочная копия создастся).`)) return;
  try { const r = await api(`/api/admin/restore/${n}`, {}); toast(`Восстановлено. Страховочная копия: ${r.strahovka}`, "ok"); go("backup"); }
  catch (e) { toast(e.message, "bad"); }
}
async function backupNow() { await api("/api/admin/backup", {}); toast("Копия создана", "ok"); go("backup"); }

async function viewSettings(v) {
  v.innerHTML = `<div class="card" style="max-width:560px"><h2>Настройки обучения</h2>
    <div class="fld"><label>Учебный лимит времени по умолчанию, с</label>
      <input id="st" type="number" min="10" max="600" value="${S.cfg.uchebny_tayming_sec}"></div>
    <div class="hint" style="margin:0 0 12px">ТЗ ДГОЧСиПБ: по умолчанию 30 с. Норматив ПП РФ 1931 (${S.cfg.normativy.kartochka_sec} с) не меняется.
      Преподаватель может задать свой лимит в каждом назначении.</div>
    <button class="btn" onclick="settingsSave()">Сохранить</button></div>`;
}
async function settingsSave() {
  try { await api("/api/admin/settings", { uchebny_tayming_sec: +$("#st").value });
    S.cfg = await api("/api/config"); toast("Сохранено", "ok"); } catch (e) { toast(e.message, "bad"); }
}

const VIEWS = {
  tasks: viewTasks, pick: viewPick, dds: viewDdsList, progress: viewProgress, spravka: viewSpravka,
  monitor: viewMonitor, scen: viewScen, assign: viewAssign, group: viewGroup, grades: viewGrades, reports: viewReports,
  system: viewSystem, users: viewUsers, audit: viewAudit, backup: viewBackup, settings: viewSettings,
};

boot();
