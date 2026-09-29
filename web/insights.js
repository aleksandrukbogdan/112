'use strict';
const Analysis = {epoch:0};
function analyticsFiltersHTML(f,groups,individual) {
  const sel=(k,values,label)=>`<label>${label}<select data-filter="${k}">${values.map(([v,n])=>`<option value="${esc(v)}" ${String(f[k]??'')===String(v)?'selected':''}>${esc(n)}</option>`).join('')}</select></label>`;
  return `<div class="card analytics-filters">
    ${!individual&&S.user.role!=='trainee'?sel('group_id',[['','Все доступные группы'],...groups.map(g=>[g.id,g.name])],'Группа'):''}
    ${sel('kind',[['dds','Диспетчер ДДС'],['ops112','Оператор 112'],['all','Обе программы отдельно']],'Программа')}
    ${sel('helper',[['all','Все условия'],['independent','Самостоятельно'],['assisted','С помощником']],'Помощь')}
    ${sel('difficulty',[['','Любая'],...[1,2,3,4,5].map(x=>[x,x])],'Сложность')}
    ${sel('rubric',[['112-quality-2','Текущая рубрика'],['112-quality-1','Архивная рубрика'],['all','Все рубрики отдельно']],'Критерии')}
    ${sel('mode',[['all','Любой'],['practice','Тренировка'],['exam','Экзамен'],['assisted_exam','Проверка с помощником'],['replay','Повтор после разбора']],'Режим')}
    <label>Служба<input data-filter="service" value="${esc(f.service||'')}" placeholder="Все службы"></label>
    <label>С даты<input type="date" data-filter="date_from" value="${esc(f.date_from||'')}"></label>
    <label>По дату<input type="date" data-filter="date_to" value="${esc(f.date_to||'')}"></label>
  </div>`;
}
async function renderAnalytics(root, uid=0, section='profile') {
  const epoch=++Analysis.epoch, key=`analysis:${section}:${uid}`, saved=JSON.parse(sessionStorage.getItem(key)||'{}');
  let groups=[];if(S.user.role!=='trainee')groups=await api('/api/groups');
  const f={kind:'dds',helper:'all',rubric:'112-quality-2',mode:'all',...saved};
  if(uid)f.uid=uid;
  root.innerHTML=`<div class="analysis-head"><div><h1>${section==='group'?'Аналитика группы':section==='quality'?'Качество ИИ и помощи':'Моё обучение'}</h1><p>Результаты, доказательства и следующий шаг</p></div><button class="btn btn-g" id="an-refresh">Обновить</button></div>${analyticsFiltersHTML(f,groups,!!uid)}<div id="analysisStatus" class="analysis-state" role="status">Рассчитываем показатели…</div><div id="analysisBody"></div>`;
  const body=root.querySelector('#analysisBody'), status=root.querySelector('#analysisStatus');let sequence=0, chosen=[];
  const fetchData=async()=>{
    const request=++sequence;
    root.querySelectorAll('[data-filter]').forEach(el=>f[el.dataset.filter]=el.value);
    sessionStorage.setItem(key,JSON.stringify(f));const query=new URLSearchParams();
    for(const [k,v] of Object.entries(f))if(v!==''&&v!=null&&!k.startsWith('date_'))query.set(k,v);
    if(f.date_from)query.set('from_ts',new Date(f.date_from+'T00:00:00').getTime()/1000);
    if(f.date_to)query.set('to_ts',new Date(f.date_to+'T23:59:59').getTime()/1000);
    status.textContent='Обновляем снимок…';status.className='analysis-state';
    try {
      const a=await api('/api/analysis/snapshot?'+query);
      if(request!==sequence||epoch!==Analysis.epoch||!root.isConnected)return;
      root._analytics=a;chosen=[];
      status.innerHTML=`Снимок от ${esc(fmtT(a.created))} · ${a.n} оценённых попыток · часовой пояс ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)} · <a href="${dl(`/api/analysis/export/${a.snapshot_id}.pdf`)}">PDF</a> · <a href="${dl(`/api/analysis/export/${a.snapshot_id}.xlsx`)}">Excel</a> · <a href="${dl(`/api/analysis/export/${a.snapshot_id}.json`)}">Данные</a>`;
      const s=a.summary;
      body.innerHTML=`<div class="metric-grid">
        ${[['Завершено',s.completed,`Остановлено преподавателем: ${s.stopped}`],['Доля зачётов',pct(s.pass_rate),`${s.passed} из ${s.completed}`],['Средний балл',s.mean_score??'—','Критические ошибки учитываются отдельно'],['С критическими ошибками',s.critical_attempts,`Активных карточек: ${s.live}`]].map(([n,v,t])=>`<div class="metric-tile"><small>${esc(n)}</small><strong>${esc(v)}</strong><small>${esc(t)}</small></div>`).join('')}</div>
        <div class="chart-grid-layout">
          ${section==='quality'?`<div>${Charts.calibration(a)}</div><div>${Charts.helper(a)}</div><div class="analytics-wide">${Charts.comparison(a)}</div>`:
            `<div>${Charts.trend(a)}</div><div>${Charts.skills(a)}</div>${section==='group'?`<div class="analytics-wide" id="chartHeat">${Charts.heatmap(a)}</div>`:''}<div>${Charts.errors(a)}</div><div id="chartTiming">${Charts.timing(a,f.kind==='ops112'?'card':'ack')}</div><div class="analytics-wide">${Charts.comparison(a)}</div>`}
        </div>
        ${a.recommendations?.length?`<section class="card" style="margin-top:16px"><h2>Следующие упражнения</h2>${a.recommendations.map(p=>`<div class="lesson-row"><h3>${esc(p.name)} · сложность ${p.difficulty}/5</h3><p>${esc(p.reason)}</p><p class="hint">${esc(p.criterion)}</p><div class="row">${p.scenarios.map(x=>`<button class="btn btn-g" data-practice="${esc(x.id)}" data-kind="${esc(f.kind)}">${esc(x.name)}</button>`).join('')}</div></div>`).join('')}</section>`:''}
        <section class="card" style="margin-top:16px"><div class="row sp"><h2>Попытки и основания</h2><button class="btn btn-g" id="resetAttemptFilter" hidden>Показать все</button></div><div id="attemptSelection"></div><div id="attemptTable"></div></section>
        <p class="hint">${esc(a.warnings.join(' '))} Исключено фильтрами: ${esc(Object.entries(a.excluded).map(([k,n])=>`${k}: ${n}`).join('; ')||'0')}.</p>`;
      const drawAttempts=()=>{
        const rows=chosen.length?a.attempts.filter(x=>chosen.includes(x.id)):a.attempts;
        body.querySelector('#attemptSelection').textContent=chosen.length?`Выбрано по диаграмме: ${rows.length} попыток`:'';
        body.querySelector('#resetAttemptFilter').hidden=!chosen.length;
        body.querySelector('#attemptTable').innerHTML=`<div class="chart-table"><table class="t"><thead><tr><th>Дата</th><th>Ученик</th><th>Сценарий</th><th>Балл / зачёт</th><th>Условия</th><th></th></tr></thead><tbody>${rows.slice().reverse().map(x=>`<tr><td>${esc(fmtT(x.finished))}</td><td>${esc(x.name)}</td><td>${esc(x.kind)} · ${esc(x.scenario)} · ${x.difficulty}/5</td><td><b>${x.ball}</b> · <span class="badge ${x.passed?'b-ok':'b-bad'}">${esc(x.verdict||(x.passed?'зачёт':'не зачтено'))}</span>${x.critical_errors.length?' ◆':''}${x.reviewed?' · пересмотрено':''}</td><td>${x.replay?'Повтор':x.assisted?'С помощником':x.independent?'Самостоятельно':'С вмешательством'}</td><td><button class="btn btn-g btn-sm" data-debrief="${esc(x.id)}">Разбор</button></td></tr>`).join('')||'<tr><td colspan="6">Нет попыток</td></tr>'}</tbody></table></div>`;
      };drawAttempts();
      body.querySelector('#resetAttemptFilter').onclick=()=>{chosen=[];drawAttempts();};
      const bindCharts=()=>{
        body.querySelector('[data-heat-axis]')?.addEventListener('change',e=>{body.querySelector('#chartHeat').innerHTML=Charts.heatmap(a,e.target.value);bindCharts();});
        body.querySelector('[data-time-axis]')?.addEventListener('change',e=>{body.querySelector('#chartTiming').innerHTML=Charts.timing(a,e.target.value);bindCharts();});
        body.querySelector('[data-model-axis]')?.addEventListener('change',e=>{e.target.closest('.chart-card').outerHTML=Charts.calibration(a,Number(e.target.value));bindCharts();});
      };bindCharts();
      body.onclick=e=>{
        const point=e.target.closest('[data-attempt-ids]');if(point){chosen=point.dataset.attemptIds.split(',').filter(Boolean);drawAttempts();body.querySelector('#attemptTable').scrollIntoView({behavior:'smooth',block:'center'});}
        const inspect=e.target.closest('[data-debrief]');if(inspect)detailedRazbor(inspect.dataset.debrief);
        const practice=e.target.closest('[data-practice]');if(practice){if(uid&&uid!==S.user.id)assignRecommended(uid,practice.dataset.practice,practice.dataset.kind);else practice.dataset.kind==='dds'?ddsStart(practice.dataset.practice):startFrom(practice.dataset.practice);}
      };
      body.onkeydown=e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-attempt-ids]')){e.preventDefault();e.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}};
    } catch(e){if(request===sequence&&epoch===Analysis.epoch){status.textContent='Ошибка обновления: '+e.message+'. Показанные ниже данные могут быть устаревшими.';status.className='analysis-state error';}}
  };
  root.querySelectorAll('[data-filter]').forEach(el=>el.onchange=fetchData);root.querySelector('#an-refresh').onclick=fetchData;
  await fetchData();
}
async function viewInsights(root,uid=0,kind='dds'){return renderAnalytics(root,uid,'profile');}
async function viewGroup(root){return renderAnalytics(root,0,'group');}
async function viewAIQuality(root){return renderAnalytics(root,0,'quality');}
async function assignRecommended(uid,sid,kind){
  const users=await api('/api/users'),u=users.find(x=>x.id===Number(uid));if(!u?.group_id)return toast('Ученик не включён в доступную группу','bad');
  if(!confirm('Назначить упражнение группе ученика? В конструкторе занятия можно выбрать полный набор.'))return;
  await api('/api/assignments',{group_id:u.group_id,scenario_ids:[sid],rezhim:kind});toast('Назначение сохранено','ok');
}
function eventText(e){
  const p=e.payload||{};
  if(e.type==='dialog')return p.tekst||'';
  if(e.type==='field_changed')return `${p.field}: ${JSON.stringify(p.before??'')} → ${JSON.stringify(p.after)}`;
  if(e.type==='statusy')return `${p.nazvanie||p.kod}: ${p.kommentariy||''}`;
  if(e.type==='calls_changed')return (p.calls||[]).map(c=>`${c.kontakt?.nazvanie}: ${(c.dialog||[]).map(m=>m.tekst).join(' / ')}`).join('\n');
  if(e.type==='teacher_intervention')return p.text||'';
  if(e.type==='helper_decision')return `${p.field}: ${p.action}, ${p.value??''}`;
  if(e.type==='session_completed')return `Балл: ${p.ball}; ${p.passed?'зачёт':'не зачтено'}`;
  if(e.type==='helper_shown')return `Показано предложений: ${p.proposal_ids?.length||0}; ${p.diagnostic?.status||''}`;
  if(e.type==='incoming_changed')return (p.incoming||[]).map(x=>`${x.kontakt?.nazvanie}: ${x.status}`).join('\n');
  return Object.entries(p).filter(([k,v])=>typeof v!=='object').map(([k,v])=>`${k}: ${v}`).join(' · ');
}
async function detailedRazbor(sid) {
  try {
    const r=await api(`/api/session/${encodeURIComponent(sid)}/razbor`), it=r.itog;
    if(!it)return toast('Попытка не оценена. Она могла быть остановлена преподавателем.','bad');
    const teacher=S.user.role==='teacher';
    modal(`<div class="debrief"><div class="row sp"><h1>Разбор попытки</h1><button class="btn btn-g" onclick="closeModal()">Закрыть</button></div>
      <p>${esc(r.session.scenario_id)} · ${esc(fmtT(r.session.started))} · <b>${it.ball} баллов</b> · <span class="badge ${it.passed?'b-ok':'b-bad'}">${esc(it.verdikt)}</span>${r.helper?.exposed?' · с помощником':''}${r.parent_session?' · повтор после разбора':''}</p>
      <p class="hint">Рубрика ${esc(it.rubric_version)}. Исходная оценка сохранена; пересмотров: ${r.reviews?.length||0}.</p>
      ${Charts.timeline(r)}<div id="eventFocus" class="notice-box" hidden></div>
      <div class="form-grid"><section><h2>Проверка критериев</h2>${factsHtml(it.fakty)}</section><section><h2>События</h2><div class="timeline-events">${r.events.map(e=>`<article class="event-row" data-event="${esc(e.event_id)}"><span class="event-chip">№ ${e.seq} · ${Math.max(0,e.ts-r.session.started).toFixed(1)} с · ${esc(e.type)}</span><p>${esc(eventText(e))}</p>${(r.snapshot_points||[]).includes(e.seq)?`<button class="btn btn-g btn-sm" data-replay-seq="${e.seq}">Повторить с этой точки</button>`:''}</article>`).join('')}</div></section></div>
      <h2>Нейросетевой анализ текста</h2><p class="hint">${esc(r.ai_analysis?.diagnostic?.status||'Анализ не запрашивался')} · замечания модели проверяет преподаватель, они не изменяют балл скрытым образом.</p>
      ${(r.ai_analysis?.observations||[]).map(x=>`<div class="lesson-row"><blockquote>${esc(x.quote)}</blockquote><p>${esc(x.explanation)}</p><p>${esc(x.suggestion)}</p><small>Источник: ${esc(x.source_id)}</small></div>`).join('')||'<p>Подтверждённых замечаний модели нет.</p>'}
      ${r.helper_proposals?.length?`<h2>Предложения помощника</h2>${r.helper_proposals.map(p=>`<div class="helper-proposal"><b>${esc(p.field)}: ${esc(p.data.value)}</b><blockquote>${esc(p.data.quote)}</blockquote><p>Решение: ${esc(p.state)}${p.reviewed?` · проверка: ${p.reviewed.correct?'верно':'ошибка'}`:' · правильность ещё не размечена'}</p>${teacher?`<button class="btn btn-g btn-sm" data-helper-review="${esc(p.id)}">Проверить предложение</button>`:''}</div>`).join('')}`:''}
      ${teacher?`<h2>Пересмотр критерия</h2><form class="review-form" id="reviewForm"><label>Критерий<select id="reviewCriterion">${it.fakty.map(f=>`<option value="${esc(f.kod)}">${esc(f.nazvanie)} · ${f.proyden==null?'не оценён':f.proyden?'выполнен':'ошибка'}</option>`).join('')}</select></label><label>Решение<select id="reviewValue"><option value="true">Выполнен</option><option value="false">Ошибка</option><option value="null">Не оценён / неприменим</option></select></label><label>Причина со ссылкой на событие<textarea id="reviewReason" required minlength="5" placeholder="Что подтверждает изменение оценки"></textarea></label><div class="row"><button type="button" class="btn btn-g" id="reviewPreview">Предпросмотр</button><button class="btn">Сохранить пересмотр</button></div><div id="reviewResult" role="status"></div></form>`:''}
      <div class="row" style="margin-top:18px"><a class="btn btn-g" href="${dl(`/api/otchet/zanyatie/${sid}.pdf`)}">Отчёт PDF</a><button class="btn btn-g" onclick="closeModal()">Вернуться</button></div></div>`);
    const box=$('#modalBox'),focus=eid=>{box.querySelectorAll('[data-event]').forEach(n=>n.classList.toggle('selected',n.dataset.event===eid));const node=[...box.querySelectorAll('[data-event]')].find(n=>n.dataset.event===eid);node?.scrollIntoView({behavior:'smooth',block:'center'});};
    box.onclick=async e=>{
      const ev=e.target.closest('[data-event-id],[data-evidence]');if(ev)focus(ev.dataset.eventId||ev.dataset.evidence);
      const re=e.target.closest('[data-replay-seq]');if(re){if(!confirm('Создать отдельную тренировку с выбранной точки? Исходная оценка сохранится.'))return;try{const p=await api(`/api/training/${sid}/replay`,{seq:Number(re.dataset.replaySeq)});closeModal();await resumeAttempt(p.session_id);}catch(err){toast(err.message,'bad');}}
      const hr=e.target.closest('[data-helper-review]');if(hr)helperReviewForm(sid,hr.dataset.helperReview);
    };
    box.onkeydown=e=>{if(e.key==='Enter'&&e.target.matches('[data-event-id]'))focus(e.target.dataset.eventId);};
    if(teacher){
      const payload=()=>({changes:{[$('#reviewCriterion').value]:JSON.parse($('#reviewValue').value)},reason:$('#reviewReason').value});
      $('#reviewPreview').onclick=async()=>{try{const p=await api(`/api/training/${sid}/review-preview`,payload());$('#reviewResult').textContent=`После пересмотра: ${p.ball} баллов, ${p.verdikt}; критические ошибки: ${p.critical_errors.join(', ')||'нет'}`;}catch(e){$('#reviewResult').textContent=e.message;}};
      $('#reviewForm').onsubmit=async e=>{e.preventDefault();try{await api(`/api/session/${sid}/review`,payload());await detailedRazbor(sid);}catch(err){toast(err.message,'bad');}};
    }
  } catch(e){toast('Не удалось открыть разбор: '+e.message,'bad');}
}
function helperReviewForm(sid,pid){
  modal(`<h2>Проверка предложения помощника</h2><form id="helperReviewForm"><label>Правильность<select id="helperCorrect"><option value="true">Верное предложение</option><option value="false">Ошибка помощника</option></select></label><label>Основание<textarea id="helperReason" minlength="5" required></textarea></label><div class="row"><button class="btn">Сохранить</button><button type="button" class="btn btn-g" id="helperBack">Назад</button></div></form>`);
  $('#helperBack').onclick=()=>detailedRazbor(sid);$('#helperReviewForm').onsubmit=async e=>{e.preventDefault();try{await api(`/api/helper/${sid}/proposals/${pid}/review`,{correct:$('#helperCorrect').value==='true',reason:$('#helperReason').value});detailedRazbor(sid);}catch(err){toast(err.message,'bad');}};
}
