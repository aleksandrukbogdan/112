/* Evidence and analytics only. No changes to recording, ASR, TTS or phone controls. */
"use strict";

async function viewInsights(root, uid = 0, kind = "ops112") {
  root.innerHTML = "Загрузка аналитики…";
  try {
    const a = await api(`/api/insights/${Number(uid)}?kind=${encodeURIComponent(kind)}`);
    root.innerHTML = `<div class="card">
      <h2>Прогресс и персональный план</h2>
      <select id="programSelect"><option value="ops112">Оператор 112</option><option value="dds">Диспетчер ДДС</option></select>
      <p>${a.n} попыток по текущей рубрике · зачтено ${a.passed}. Архивных попыток отдельно: ${a.legacy_excluded}.</p>
      <p class="hint">${esc(a.bkt_note)} Балл, зачёт и ручная оценка — разные показатели.</p>
      <table class="t"><tr><th>Навык</th><th>Наблюдений</th><th>Успех</th><th>Интервал 95%</th><th>Последние 10</th></tr>
      ${a.skills.map(s => `<tr><td>${esc(s.name)}</td><td>${s.n}</td><td>${s.n ? pct(s.rate) : "не проверен"}</td>
        <td>${s.interval95 ? s.interval95.map(pct).join("–") : "—"}</td><td>${pct(s.recent_rate)}</td></tr>`).join("")}</table>
      <p class="hint">Интервалы Уилсона — ориентир при допущении независимости. Повторы одного человека зависимы; малое n не доказывает освоение.</p></div>
      <div class="card" style="margin-top:12px"><h2>Следующий шаг</h2>
      ${a.plan.map(p => `<section class="fact"><h3>${esc(p.name)} · ${esc(p.action)}</h3><p>${esc(p.reason)}</p>
        <p>${esc(p.criterion)}</p><div class="row">${p.scenarios.map(s => `<button class="btn btn-g planStart" data-scenario="${esc(s.id)}">${esc(s.name)}</button>`).join("")}</div>
        <p class="hint">${esc(p.selection)}</p></section>`).join("") || "Слабых навыков по текущей выборке не найдено. Нужна контрольная попытка и решение преподавателя."}</div>
      <div class="card" style="margin-top:12px"><h2>Проверка прогнозов</h2>
        <p>Попыток: ${a.forecasts.n} · Brier: ${a.forecasts.brier ?? "—"} (меньше — лучше) · точность: ${pct(a.forecasts.accuracy)}.</p>
        <p class="hint">Один первый прогноз до дедлайна на попытку. ${esc(a.forecasts.warning)}</p>
        <table class="t"><tr><th>Средний прогноз</th><th>Фактическая доля</th><th>n</th></tr>
        ${a.forecasts.calibration.map(x => `<tr><td>${pct(x.predicted)}</td><td>${pct(x.observed)}</td><td>${x.n}</td></tr>`).join("")}</table></div>
      <div class="card" style="margin-top:12px"><h2>Попытки</h2><table class="t"><tr><th>Дата</th><th>Сценарий</th><th>Балл / зачёт</th><th>Ручной балл</th><th></th></tr>
        ${a.attempts.slice().reverse().map(r => `<tr><td>${fmtT(r.finished)}</td><td>${esc(r.scenario)}</td>
        <td>${r.ball} / ${r.passed ? "зачёт" : "не зачтено"}</td><td>${r.manual_ball ?? "—"}</td>
        <td><button class="btn btn-g inspectAttempt" data-sid="${esc(r.id)}">Разбор</button></td></tr>`).join("")}</table></div>`;
    const select = root.querySelector("#programSelect"); select.value = kind;
    select.onchange = () => viewInsights(root, uid, select.value);
    root.querySelectorAll(".inspectAttempt").forEach(b => b.onclick = () => detailedRazbor(b.dataset.sid));
    root.querySelectorAll(".planStart").forEach(b => b.onclick = () => {
      closeModal(); return kind === "dds" ? ddsStart(b.dataset.scenario, null, null) : startFrom(b.dataset.scenario);
    });
  } catch (e) { root.textContent = `Ошибка аналитики: ${e.message}`; }
}

async function detailedRazbor(sid) {
  try {
    const r = await api(`/api/session/${encodeURIComponent(sid)}/razbor`), it = r.itog;
    modal(`<h2>Разбор · ${esc(r.session.scenario_id)}</h2>
      <p>Балл: ${it.ball} · ${esc(it.verdikt)} · критические ошибки: ${esc((it.critical_errors || []).join(", ") || "нет")}</p>
      <p>Ручной балл: ${r.session.override_ball ?? "—"}. Он не меняет доказательства и профиль навыков.</p>
      ${it.review ? `<p>Пересмотр критериев №${it.review.revision}: ${esc(it.review.reason)}</p>` : ""}
      ${factsHtml(it.fakty)}
      <details><summary>Версии и исходная оценка</summary><pre style="white-space:pre-wrap">${esc(JSON.stringify({versions:r.versions,origin:r.snapshot_origin,original:r.original_itog?.ball},null,2))}</pre></details>
      <h3>История действий</h3><p class="hint">События сохранения, а не запись звука. Для старых попыток история может отсутствовать.</p>
      ${(r.events || []).map(e => `<details id="ev-${esc(e.event_id)}"><summary>№${e.seq} · ${Math.max(0,e.ts-r.session.started).toFixed(1)} с · ${esc(e.type)}</summary>
        <pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(e.payload,null,2))}</pre></details>`).join("") || "Нет событий"}
      <h3>Разговоры</h3><pre style="white-space:pre-wrap">${esc(JSON.stringify(r.calls?.length ? r.calls : r.dialog,null,2))}</pre>
      <hr><div class="row"><button class="btn" id="closeEvidence">Закрыть</button>
      ${S.user.role === "teacher" ? '<button class="btn btn-g" id="reviewEvidence">Пересмотреть критерий</button>' : ""}</div>`);
    $("#closeEvidence").onclick = closeModal;
    if ($("#reviewEvidence")) $("#reviewEvidence").onclick = async () => {
      const code = prompt("Код критерия: " + it.fakty.map(f => f.kod).join(", "));
      if (!code) return;
      const value = prompt("true — выполнен, false — не выполнен, null — не проверен");
      if (!["true","false","null"].includes(value)) return toast("Неверное значение", "bad");
      const reason = prompt("Причина пересмотра (не менее 5 символов)");
      if (!reason) return;
      try {
        await api(`/api/session/${encodeURIComponent(sid)}/review`,{changes:{[code]:JSON.parse(value)},reason});
        await detailedRazbor(sid);
      } catch(e) { toast(e.message,"bad"); }
    };
    $("#modalBox").querySelectorAll("[data-evidence]").forEach(b => b.onclick = () => {
      const node = document.getElementById("ev-"+b.dataset.evidence);
      if(node) { node.open=true; node.scrollIntoView({block:"center",behavior:"smooth"}); }
    });
  } catch(e) { toast(e.message,"bad"); }
}
