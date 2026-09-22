/* Режим «Диспетчер ДДС». Экраны повторяют АРМ ГБУ «Система 112» по скриншотам заказчика. */
"use strict";

const DS = { sid: null, k: null, zapis: [], kont: [], st: [], statusy: [], call: null,
  t0: 0, tim: null, poll: null, ring: null, prinyata: false, sluzhby: null };

const POST_RU = { net: "нет", est: "есть", neizvestno: "нет данных" };

async function viewDdsList(v) {
  const [sc, sl] = await Promise.all([api("/api/scenarios"), DS.sluzhby || api("/api/dds/sluzhby")]);
  DS.sluzhby = sl;
  const now = new Date();
  $("#hdrRight").innerHTML = "";
  v.innerHTML = `
  <div class="arm-top" style="grid-template-columns:1fr 280px">
    <div class="arm-box"><div style="font-size:26px">Поиск происшествий</div>
      <div class="l">Роль: диспетчер ДДС. Карточку «поставляет» система, имитируя оператора 112.</div></div>
    <div class="arm-box arm-clock"><div class="l">${now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</div>
      <div class="v">${now.toTimeString().slice(0, 5)}</div></div>
  </div>
  <div class="card" style="margin-bottom:8px"><div class="row">
    <label style="margin:0">Ваша служба</label>
    <select id="ddsSl" style="max-width:520px"><option value="">— по карточке (назначит система) —</option>
      ${sl.map(s => `<option value="${esc(s.korotko)}">${esc(s.korotko)} · ${esc(s.nazvanie.slice(0, 60))}</option>`).join("")}</select>
    <input id="ddsF" placeholder="фильтр вызовов…" style="max-width:260px"></div></div>
  <div class="dds-list"><div style="color:#fff;font-weight:700;font-size:17px;margin:0 0 6px 6px">Список происшествий</div>
    <table><thead><tr><th>Номер</th><th>Тип</th><th>Сложн.</th><th>Адрес со слов заявителя</th><th>Статус службы</th><th></th></tr></thead>
    <tbody id="ddsBody"></tbody></table></div>`;
  const draw = f => {
    f = (f || "").toLowerCase();
    $("#ddsBody").innerHTML = sc.filter(s => !f || (s.situaciya + s.adres_vidimy + s.nazvanie).toLowerCase().includes(f)).slice(0, 120).map(s => `
      <tr class="row"><td>${esc(s.nazvanie)}</td><td>${esc((S.cfg.gruppy[s.gruppa] || s.gruppa).slice(0, 34))}</td>
        <td>${"●".repeat(s.slozhnost)}</td><td>${esc(s.adres_vidimy.slice(0, 70))}</td><td>Добавлена</td>
        <td><button class="btn btn-sm" onclick="ddsStart('${s.id}')">Открыть</button></td></tr>
      <tr class="desc"><td colspan="6">Описание: ${esc(s.situaciya.slice(0, 150))}</td></tr>`).join("");
  };
  draw(); $("#ddsF").oninput = e => draw(e.target.value);
}

async function ddsStart(scId, aid, sl) {
  try {
    const r = await api("/api/dds/session", { scenario_id: scId, assignment_id: aid || null,
      sluzhba: sl || ($("#ddsSl") ? $("#ddsSl").value || null : null) });
    Object.assign(DS, { sid: r.session_id, k: r.kartochka, zapis: r.zapis, kont: r.kontakty,
      statusy: r.statusy, st: [], call: null, t0: Date.now(), prinyata: false, zam: [] });
    ddsView();
    clearInterval(DS.tim); DS.tim = setInterval(ddsTick, 250);
    clearInterval(DS.poll); DS.poll = setInterval(ddsPoll, 1000);
  } catch (e) { toast(e.message, "bad"); }
}

function ddsTick() {
  const b = $("#ddsClock"); if (!b) return;
  const t = (Date.now() - DS.t0) / 1000, lim = S.cfg.dds.podtverzhdenie_sec;
  if (DS.prinyata) { b.className = "arm-box arm-clock ok"; return; }
  b.className = "arm-box arm-clock" + (t > lim ? " over" : "");
  b.innerHTML = `<div class="v">${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}</div>
    <div class="l">${t > lim ? "подтверждение просрочено" : "до подтверждения " + Math.ceil(lim - t) + " с"}</div>`;
}

async function ddsPoll() {
  if (!DS.sid) return;
  try {
    const e = await api(`/api/dds/${DS.sid}/events`);
    const r = e.vhodyashchie[0];
    const box = $("#ring");
    if (r && !DS.call) {
      if (!box || box.dataset.id !== r.id) {
        document.querySelectorAll(".ring").forEach(x => x.remove());
        document.body.insertAdjacentHTML("beforeend", `<div class="ring" id="ring" data-id="${r.id}">
          <div style="font-size:11px;opacity:.7">ВХОДЯЩИЙ ЗВОНОК · IP-телефон</div>
          <div style="font-weight:700;margin-top:3px">${esc(r.ot)}</div>
          <button class="btn btn-ok" onclick="ddsAnswer('${r.id}')">Ответить</button></div>`);
      }
    } else if (box && (!r || DS.call)) box.remove();
    if ($("#ddsFaza")) $("#ddsFaza").textContent = { ne_vyehala: "бригада не направлена", vyezd: "бригада в пути",
      pribytie: "бригада на месте", raboty: "идут работы", zaversheno: "работы завершены" }[e.faza] +
      (e.propushcheno ? ` · пропущено входящих: ${e.propushcheno}` : "");
  } catch {}
}

function ddsView() {
  const k = DS.k, v = $("#view");
  document.querySelectorAll("#nav button").forEach(b => b.classList.remove("on"));
  $("#hdrRight").innerHTML = "";
  const sozd = new Date(k.sozdana * 1000).toLocaleString("ru-RU");
  v.innerHTML = `
  <div class="arm-top">
    <div class="arm-box"><div>📞 Отключение</div><button class="btn btn-g btn-sm" style="margin-top:5px" onclick="ddsZapis()">записи звонков</button></div>
    <div class="arm-box"><div class="l">АОН</div><div class="v">${esc(k.aon)}</div></div>
    <div class="arm-box"><div class="l">предоставленный</div><div class="v">—</div></div>
    <div class="arm-box"><div class="l">телефон на место</div><div class="v">—</div></div>
    <div class="arm-box arm-no"><b>Происшествие ${esc(k.nomer)}</b><div>Сохр. ${sozd}</div><div>${esc(k.operator_112)}</div></div>
    <div class="arm-box arm-clock" id="ddsClock"></div>
  </div>
  <div class="arm-row">
    <div class="arm-box" style="min-height:0"><div class="l">ФИО заявителя</div><b>${esc(k.zayavitel_fio)}</b></div>
    <div class="arm-box" style="min-height:0">Пострадавшие: <b>${POST_RU[k.postradavshie]}</b> ·
      <span class="muted">служба: <b>${esc(k.moya_sluzhba)}</b></span> · <span class="muted" id="ddsFaza"></span></div>
  </div>
  <div class="dds-grid">
    <div>
      <div class="arm-box" style="margin-bottom:6px"><b>${esc(k.adres)}</b></div>
      <div class="arm-box" style="min-height:120px"><b>${sozd} · оператор 112</b><div style="margin-top:4px">${esc(k.opisanie)}</div></div>
      <div class="arm-bar" style="margin-top:6px">Происшествие · ${esc(k.tip)}</div>
      <div class="arm-line">Класс.: <b>${esc(k.tip)}</b> · пострадавшие: ${POST_RU[k.postradavshie]}</div>
      <div class="card" style="margin-top:8px"><h2>Проверка карточки <span class="muted">сверьте с записью разговора 112</span></h2>
        <div class="row" style="gap:8px;align-items:flex-end">
          <div style="flex:1"><label>Поле с ошибкой</label><select id="zPole">
            <option value="adres">Адрес</option><option value="telefon">Телефон (АОН)</option>
            <option value="postradavshie">Пострадавшие</option></select></div>
          <div style="flex:2"><label>Верное значение (по записи)</label><input id="zVer" placeholder="например: …, дом 12"></div>
          <button class="btn btn-g" onclick="ddsZam()">Отметить расхождение</button></div>
        <div id="zList" class="hint">Расхождений не отмечено. Если ошибок нет — ничего не отмечайте.</div></div>
    </div>
    <div>
      <div class="card zap" style="margin-bottom:8px"><h2>Запись разговора 112 <span class="badge b-dim">доступна ДДС</span></h2>
        <div id="zapBody" style="max-height:230px;overflow:auto"></div></div>
      <div class="card"><h2>IP-телефон</h2><div id="phone"></div></div>
    </div>
  </div>
  <div class="card" style="margin-top:8px" id="stPanel"></div>
  <div class="dds-bottom"><span class="lab">Службы:</span><span id="chips" class="row" style="gap:4px"></span>
    <span style="margin-left:auto"></span>
    <button class="btn btn-g" onclick="ddsFinish()">Закрыть карточку и получить разбор</button></div>`;
  $("#zapBody").innerHTML = DS.zapis.map((z, i) => `<div class="ln"><b>${z.t_sec} с · ${z.kto === "operator" ? "Оператор 112" : "Заявитель"}</b>
    <span>${esc(z.tekst)}</span>${S.tts ? `<button onclick="ddsPlay(${i})" title="прослушать">▶</button>` : ""}</div>`).join("");
  ddsChips(); ddsPhone(); ddsStPanel(); ddsTick();
}

function ddsZapis() { $("#zapBody").scrollIntoView({ behavior: "smooth", block: "center" }); }
function ddsPlay(i) { new Audio(dl(`/api/voice/tts?text=${encodeURIComponent(DS.zapis[i].tekst)}`)).play().catch(() => {}); }

function ddsChips() {
  const last = DS.st.length ? DS.st[DS.st.length - 1] : null;
  $("#chips").innerHTML = DS.k.sluzhby.map(s => `<button class="svc-chip ${s.moya ? "moya" : ""}" ${s.moya ? 'onclick="ddsStPanel(true)"' : ""}>
    <b>${esc(s.korotko)}</b><span>${s.moya && last ? new Date(last.t).toTimeString().slice(0, 5) + " " + esc(last.nazvanie) : "Добавлена"}</span></button>`).join("");
}

function ddsStPanel(scroll) {
  const p = $("#stPanel");
  p.innerHTML = `<h2>Статус службы «${esc(DS.k.moya_sluzhba)}» <span class="muted">${esc(S.cfg.dds.istochnik_statusov)}</span></h2>
    <div class="tagwrap">${DS.statusy.map(s => `<button class="tag" onclick="ddsStatus('${s.kod}')">${esc(s.nazvanie)}${s.kommentariy ? " *" : ""}</button>`).join("")}</div>
    <div class="row" style="margin-top:8px"><input id="stKom" placeholder="Комментарий (обязателен для статусов со звёздочкой): например, «Отправлен сантехник для перекрытия воды»"></div>
    <h3>История</h3>${DS.st.length ? DS.st.map(x => `<div class="arm-line">${new Date(x.t).toLocaleTimeString("ru-RU")} · <b>${esc(x.nazvanie)}</b>
      ${x.kommentariy ? " — " + esc(x.kommentariy) : ""}${x.warn ? ` <span class="badge b-warn">без основания</span>` : ""}</div>`).join("") : '<div class="muted">Статусов нет.</div>'}`;
  if (scroll) p.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function ddsStatus(kod) {
  const kom = $("#stKom").value;
  try {
    const r = await api(`/api/dds/${DS.sid}/status`, { status: kod, kommentariy: kom });
    const cfg = DS.statusy.find(s => s.kod === kod);
    DS.st.push({ kod, nazvanie: cfg.nazvanie, kommentariy: kom, t: Date.now(), warn: !r.osnovanie_bylo });
    if (kod === "prinyata" || kod === "ne_prinyata") DS.prinyata = true;
    if (r.preduprezhdenie) toast(r.preduprezhdenie, "bad"); else toast(`Статус «${cfg.nazvanie}» установлен`, "ok");
    ddsChips(); ddsStPanel();
  } catch (e) { toast(e.message, "bad"); }
}

async function ddsZam() {
  const pole = $("#zPole").value, ver = $("#zVer").value.trim();
  if (!ver) return toast("Укажите верное значение по записи", "bad");
  await api(`/api/dds/${DS.sid}/zamechanie`, { pole, verno: ver });
  DS.zam.push({ pole, ver });
  $("#zVer").value = "";
  $("#zList").innerHTML = DS.zam.map(z => `<div class="arm-line">⚠ ${({ adres: "Адрес", telefon: "Телефон", postradavshie: "Пострадавшие" })[z.pole]}: верно — <b>${esc(z.ver)}</b></div>`).join("");
  toast("Расхождение отмечено", "ok");
}

function ddsPhone() {
  const p = $("#phone");
  if (DS.call) {
    p.innerHTML = `<div class="phone"><div class="ct"><b>☎ ${esc(DS.call.kontakt.nazvanie)}</b>
      <button class="btn btn-sm" style="background:var(--bad)" onclick="ddsHang()">Положить трубку</button></div>
      <div class="dlg" id="cDlg" style="height:210px;padding:8px"></div>
      <div class="dlg-in" style="padding:0 8px 8px">
        <button class="mic" id="cMic" ${S.voice ? "" : "disabled"}>🎙</button>
        <input id="cInp" placeholder="Говорите: адрес, что случилось, пострадавшие, телефон заявителя…">
        <button class="btn" onclick="ddsSay()">→</button></div></div>`;
    ddsCallDlg();
    $("#cInp").onkeydown = e => { if (e.key === "Enter") ddsSay(); };
    if (S.voice) $("#cMic").onclick = () => micTo(t => ddsSay(t), $("#cMic"));
    $("#cInp").focus();
    return;
  }
  p.innerHTML = `<div class="phone">${DS.kont.map(k => `<div class="ct"><span>${esc(k.nazvanie)}<br><span class="muted">${esc(k.telefon)}</span></span>
    <button class="btn btn-g btn-sm" onclick="ddsCall('${k.id}')">📞 Позвонить</button></div>`).join("")}</div>
    <div class="hint">Бригаду выбирают вручную. Выезд — только после того, как старшему передан адрес.
      Об этапах старший сообщит сам, или позвоните ему и уточните обстановку.</div>`;
}
function ddsCallDlg() {
  const d = $("#cDlg"); if (!d) return;
  d.innerHTML = DS.call.dialog.map(m => `<div class="msg ${m.kto === "dispetcher" ? "m-op" : "m-cl"}">
    <b>${m.kto === "dispetcher" ? "Диспетчер" : esc(DS.call.kontakt.nazvanie.split("—")[0])}</b>${esc(m.tekst)}</div>`).join("");
  d.scrollTop = d.scrollHeight;
}
function speak(t) { if (S.tts) new Audio(dl(`/api/voice/tts?text=${encodeURIComponent(t)}`)).play().catch(() => {}); }

async function ddsCall(id) {
  const k = DS.kont.find(x => x.id === id);
  toast(`Вызов: ${k.nazvanie}…`);
  const r = await api(`/api/dds/${DS.sid}/call`, { kontakt: id });
  if (r.ishod !== "otvetil") { toast("Абонент не отвечает. Перезвоните.", "bad"); return; }
  DS.call = { id: r.call_id, kontakt: k, dialog: r.dialog };
  ddsPhone(); r.dialog.forEach(m => speak(m.tekst));
}
async function ddsAnswer(eid) {
  document.querySelectorAll(".ring").forEach(x => x.remove());
  const r = await api(`/api/dds/${DS.sid}/incoming/${eid}/answer`, {});
  const k = DS.kont.find(x => x.kto === "brigada") || { nazvanie: "Старший бригады" };
  DS.call = { id: r.call_id, kontakt: { ...k, nazvanie: "Старший бригады (входящий)" }, dialog: [{ kto: "abonent", tekst: r.tekst }] };
  ddsPhone(); speak(r.tekst);
}
async function ddsSay(text) {
  const i = $("#cInp"); const t = (text ?? i.value).trim(); if (!t || !DS.call) return;
  i.value = "";
  DS.call.dialog.push({ kto: "dispetcher", tekst: t }); ddsCallDlg();
  const r = await api(`/api/dds/${DS.sid}/call/${DS.call.id}/say`, { tekst: t });
  DS.call.dialog.push({ kto: "abonent", tekst: r.otvet }); ddsCallDlg(); speak(r.otvet);
  if (r.narusheniya_rechi?.length) toast(`Речь: «${r.narusheniya_rechi[0].fragment}» — ${r.narusheniya_rechi[0].opisanie}`, "bad");
}
async function ddsHang() {
  await api(`/api/dds/${DS.sid}/call/${DS.call.id}/end`, {}); DS.call = null; ddsPhone();
}

async function ddsFinish() {
  if (!confirm("Закрыть карточку и получить разбор?")) return;
  clearInterval(DS.tim); clearInterval(DS.poll); document.querySelectorAll(".ring").forEach(x => x.remove());
  const r = await api(`/api/dds/${DS.sid}/finish`, {});
  DS.sid = null;
  const c = r.ball >= 70 ? "var(--ok)" : r.ball >= 50 ? "var(--warn)" : "var(--bad)";
  const o = r.oshibka_bylo;
  $("#view").innerHTML = `<div class="grid g2"><div>
    <div class="kpis" style="margin-bottom:10px">
      <div class="kpi"><div class="v" style="color:${c}">${r.ball}</div><div class="l">Балл · ${r.verdikt}</div></div>
      <div class="kpi"><div class="v">${r.t_podtverzhdeniya ?? "—"}<span style="font-size:14px"> с</span></div><div class="l">Подтверждение приёма</div><div class="n">норматив ${S.cfg.dds.podtverzhdenie_sec} с</div></div>
    </div>
    <div class="card"><h2>Граф доказательств · диспетчер ДДС</h2>${r.fakty.map(f => `<div class="fact ${f.proyden ? "ok" : "no"}">
      <div class="fact-h"><span class="fact-n">${esc(f.nazvanie)}</span><span class="badge ${f.proyden ? "b-ok" : "b-bad"}">${f.proyden ? "зачтено" : "не зачтено"} · вес ${f.ves}</span></div>
      <div class="fact-src">${esc(f.istochnik)}</div><div class="fact-d">${esc(JSON.stringify(f.detali).slice(1, -1).replace(/","/g, '" · "'))}</div></div>`).join("")}</div></div>
    <div><div class="card" style="margin-bottom:10px"><h2>Ошибка оператора 112 в карточке</h2>
      ${o ? `<div class="fact"><div class="fact-n">Поле «${esc(o.pole)}»</div><div class="fact-d">в карточке: ${esc(POST_RU[o.v_kartochke] || o.v_kartochke)}\nпо записи: ${esc(POST_RU[o.pravda] || o.pravda)}</div></div>`
        : `<div class="muted">В этой карточке ошибок не было.</div>`}</div>
      <div class="card"><div class="row"><button class="btn" onclick="go('dds')">К списку происшествий</button>
      <a class="btn btn-g" href="${dl(`/api/otchet/zanyatie/${r.session_id}.pdf`)}">Отчёт PDF</a></div></div></div></div>`;
}

/* общий микрофон для телефона ДДС */
async function micTo(cb, btn) {
  if (S.rec) { S.rec.stop(); return; }
  try {
    const st = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ch = []; const rec = new MediaRecorder(st); S.rec = rec;
    rec.ondataavailable = e => ch.push(e.data);
    rec.onstop = async () => {
      st.getTracks().forEach(t => t.stop()); S.rec = null; btn.classList.remove("rec");
      const fd = new FormData(); fd.append("audio", new Blob(ch, { type: rec.mimeType }), "rec.webm");
      const r = await fetch("/api/voice/asr", { method: "POST", headers: { Authorization: "Bearer " + S.token }, body: fd });
      const j = await r.json(); if (j.text) cb(j.text); else toast("Речь не распознана", "bad");
    };
    rec.start(); btn.classList.add("rec");
  } catch { toast("Нет доступа к микрофону — откройте через туннель на localhost", "bad"); }
}
