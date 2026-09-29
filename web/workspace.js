'use strict';
const Workspace={queue:[],queueTimer:null,mode:null,noticeIds:new Set(),helperBusy:false};
const FIELD_RU={adres_polny:'Адрес',opisanie:'Описание',zayavitel_fio:'Заявитель',zayavitel_telefon:'Телефон',postradavshie:'Пострадавшие',status:'Статус',comment:'Комментарий'};
async function refreshQueue(render=false){
  if(!S.user||S.user.role==='admin')return;
  try{const r=await api('/api/lessons/queue/mine');Workspace.queue=r.cards;
    if(render||document.querySelector('#queueStrip'))paintQueue();
    if(Workspace.mode==='ops112'&&S.sid){const sid=S.sid,obs=await api(`/api/training/${sid}/observations`);if(S.sid===sid&&Workspace.mode==='ops112')appendNotices(obs.notices);}
    const count=document.querySelector('[data-v="queue"] .badge');if(count)count.textContent=r.cards.length;
  }catch(e){if(document.querySelector('#queueStatus'))$('#queueStatus').textContent='Нет связи: '+e.message;}
}
function paintQueue(){
  let node=$('#queueStrip');if(!node){node=document.createElement('div');node.id='queueStrip';node.className='queue-strip';$('#view').prepend(node);}
  node.innerHTML=Workspace.queue.map(c=>`<button class="queue-card ${c.overdue?'overdue':''} ${(Workspace.mode==='dds'?DS.sid:S.sid)===c.id?'on':''}" data-resume="${esc(c.id)}"><b>${c.kind==='dds'?'ДДС':'112'} · ${esc(c.number)}</b><small>${esc(c.status)} · ${Math.round(c.elapsed)} с</small><small>${c.acknowledged?'Подтверждена':c.overdue?'Просрочено подтверждение':'До лимита '+Math.max(0,Math.ceil(c.limit_sec-c.elapsed))+' с'}</small><small>${esc(c.address||'Адрес уточняется')}</small></button>`).join('')||'<p class="hint">Активных карточек нет</p>';
  node.querySelectorAll('[data-resume]').forEach(b=>b.onclick=()=>resumeAttempt(b.dataset.resume));
}
async function viewQueue(root){
  Workspace.mode=null;clearInterval(DS.poll);clearInterval(DS.tim);clearInterval(S.timer);
  root.innerHTML='<div class="analysis-head"><div><h1>Поступившие карточки</h1><p>Время считается с момента выдачи сервером, независимо от открытой вкладки</p></div><button class="btn btn-g" onclick="go(\'queue\')">Обновить</button></div><div id="queueStatus" role="status"></div><div id="queueStrip" class="queue-strip"></div><div id="myLessons"></div>';
  await refreshQueue(true);const lessons=await api('/api/lessons');
  if(!root.isConnected)return;
  $('#myLessons').innerHTML=`<section class="card"><h2>Занятия группы</h2>${lessons.map(l=>`<div class="lesson-row"><b>${esc(l.config.name)}</b> · ${esc({draft:'Ожидает запуска',running:'Идёт занятие',stopped:'Завершено'}[l.state])}<p>${l.config.kind==='dds'?'Диспетчер ДДС':'Оператор 112'} · ${esc(l.config.service||'служба по сценарию')} · ${l.config.mode==='exam'?'Самостоятельный экзамен':'Учебное занятие'} · помощь ${l.config.helper_allowed?'разрешена':'выключена'}</p></div>`).join('')||'<p>Преподаватель ещё не создал занятие для вашей группы.</p>'}</section>`;
}
async function flushFields(){
  if(Workspace.mode!=='ops112'||!S.sid)return Transport.flush();
  const sid=S.sid,card={...S.card};
  const pending=Object.entries(_deb).filter(([k,v])=>v);for(const [k,t] of pending){clearTimeout(t);_deb[k]=null;await api(`/api/session/${sid}/pole`,{key:k,value:card[k]});}
  await Transport.flush();
}
async function resumeAttempt(sid){
  try{
    if(Workspace.mode==='ops112')await flushFields();else await Transport.flush();
    if(S.rec?.state==='recording'){toast('Остановите запись перед переключением карточки','bad');return;}
    const r=await api(`/api/training/${sid}/resume`);clearInterval(S.timer);clearInterval(DS.tim);clearInterval(DS.poll);
    document.querySelectorAll('.ring').forEach(x=>x.remove());Workspace.mode=r.kind;
    if(r.kind==='dds'){
      const active=(r.calls||[]).findLast(c=>!c.konec&&c.ishod==='otvetil');
      Object.assign(DS,{sid:r.session_id,k:r.kartochka,zapis:r.zapis,kont:r.kontakty,statusy:r.statusy,
        st:r.status_history.map(x=>({...x,t:x.t*1000})),call:active?{...active,id:active.id}:null,
        t0:Date.now()-(r.server_time-r.started)*1000,prinyata:r.status_history.some(x=>['prinyata','ne_prinyata'].includes(x.kod)),
        limit:r.podtverzhdenie_sec,exercise_profile:r.exercise_profile,zam:[],field_versions:r.field_versions});
      S.sid=null;ddsView();DS.tim=setInterval(ddsTick,250);DS.poll=setInterval(ddsPoll,1000);
      const check=$('#zPole')?.closest('.card');if(check&&r.exercise_profile!=='card_check')check.hidden=true;
    }else{
      DS.sid=null;Object.assign(S,{sid:r.session_id,sc:r.scenario,t0:Date.now()-(r.server_time-r.started)*1000,dialog:r.dialog,
        card:r.kartochka,svc:r.kartochka.sluzhby||[],tayming:r.tayming_sec,normativ:r.normativ_sec,prognoz:null,field_versions:r.field_versions});
      if(!S.treeTop)S.treeTop=await api("/api/tree");
      viewCall();restoreCardInputs();S.timer=setInterval(tick,250);
    }
    appendNotices(r.notices||[]);mountHelper(r.session_id,r.helper);paintQueue();
  }catch(e){toast(e.message,'bad');}
}
function restoreCardInputs(){
  for(const [field,id] of Object.entries({adres_polny:'f_adres',opisanie:'f_opisanie',zayavitel_fio:'f_fio',zayavitel_telefon:'f_tel'})){const el=document.getElementById(id);if(el)el.value=S.card[field]||'';}
  drawPriz();drawSvc();updReady();
}
function appendNotices(notices){
  if(!notices.length)return;let p=$('#visibleNotices');if(!p){p=document.createElement('div');p.id='visibleNotices';$('#view').prepend(p);}
  p.innerHTML=notices.map(x=>`<div class="notice-box"><b>Вводная преподавателя · ${esc(fmtT(x.ts))}</b><div>${esc(x.text)}</div></div>`).join('');
}
function mountHelper(sid,state){
  let node=$('#helperPanel');if(!node){node=document.createElement('section');node.id='helperPanel';node.className='helper-panel';const bottom=$('#view .dds-bottom');if(bottom)bottom.before(node);else $('#view').append(node);}
  node.innerHTML=`<header><h3>Помощник заполнения</h3><label><input type="checkbox" id="helperEnabled" style="width:auto" ${state?.enabled?'checked':''} ${state?.allowed?'':'disabled'}> Включён</label></header><p class="hint">${state?.allowed?'Предложения не меняют карточку до вашего подтверждения. Можно продолжить самостоятельно.':'Помощь запрещена условиями этого занятия.'}${state?.exposed?' Использование помощи уже отмечено в истории.':''}</p><button class="btn btn-g" id="helperSuggest" ${state?.enabled?'':'disabled'}>Предложить заполнение</button><div id="helperMessage" role="status"></div><div id="helperProposals"></div>`;
  $('#helperEnabled').onchange=async e=>{try{const h=await api(`/api/helper/${sid}/toggle`,{enabled:e.target.checked});mountHelper(sid,h);}catch(err){e.target.checked=!e.target.checked;toast(err.message,'bad');}};
  $('#helperSuggest').onclick=async()=>{
    if(Workspace.helperBusy)return;Workspace.helperBusy=true;$('#helperMessage').textContent='Ищем сведения в полученных сообщениях…';$('#helperSuggest').disabled=true;
    try{await flushFields();const r=await api(`/api/helper/${sid}/suggest`,{});if(!$('#helperProposals')||(Workspace.mode==='dds'?DS.sid:S.sid)!==sid)return;
      $('#helperMessage').textContent=r.message+(r.diagnostic.status==='unavailable'?' Модель недоступна; показаны предложения по явным сведениям.':'');
      $('#helperProposals').innerHTML=r.proposals.map(p=>`<article class="helper-proposal" data-proposal="${esc(p.id)}"><b>${esc(FIELD_RU[p.field]||p.field)}</b> · ${esc(({conflict:'Обнаружено расхождение',stale:'Предложение устарело',source_confirmed:'Есть источник'})[p.status])}<p>${esc(p.value)}</p><blockquote>${esc(p.quote)}<br><small>Источник ${esc(p.source_id)} · ${esc(fmtT(p.source_time))}</small></blockquote><input aria-label="Исправленное значение ${esc(FIELD_RU[p.field])}" value="${esc(p.value)}" ${p.field==='status'?'readonly':''}><label><input type="checkbox" style="width:auto" data-overwrite> Подтверждаю замену уже введённого значения</label><div class="row"><button class="btn btn-sm" data-decision="accept" ${p.status==='stale'?'disabled':''}>Принять</button><button class="btn btn-g btn-sm" data-decision="edit" ${p.status==='stale'?'disabled':''}>Принять с исправлением</button><button class="btn btn-g btn-sm" data-decision="reject">Отклонить</button></div></article>`).join('');
      $('#helperProposals').onclick=async e=>{const button=e.target.closest('[data-decision]');if(!button)return;const box=button.closest('[data-proposal]'),p=r.proposals.find(x=>x.id===box.dataset.proposal);
        try{if(button.dataset.decision!=='reject'&&p.field==='comment'&&$('#stKom')?.value.trim()&&!box.querySelector('[data-overwrite]').checked)throw new Error('Подтвердите замену уже введённого комментария');
          const d=await api(`/api/helper/${sid}/proposals/${p.id}`,{action:button.dataset.decision,value:box.querySelector('input').value,confirm_overwrite:box.querySelector('[data-overwrite]').checked,expected_version:p.base_version});
          if(d.state!=='reject'){if(d.draft_only){if(d.field==='comment'&&$('#stKom'))$('#stKom').value=d.value;if(d.field==='status')toast('Рекомендуемый статус: '+(DS.statusy.find(x=>x.kod===d.value)?.nazvanie||d.value)+'. Выберите его в панели статусов.','ok');}
            else{S.card=d.card;S.svc=d.card.sluzhby||[];restoreCardInputs();}}
          box.innerHTML+=`<p role="status">${d.state==='reject'?'Отклонено':'Решение сохранено'}</p>`;box.querySelectorAll('button,input').forEach(x=>x.disabled=true);
        }catch(err){toast(err.message,'bad');}};
    }catch(e){if($('#helperMessage'))$('#helperMessage').textContent='Не удалось получить предложения: '+e.message+'. Ручной ввод доступен.';}
    finally{Workspace.helperBusy=false;if($('#helperSuggest'))$('#helperSuggest').disabled=!$('#helperEnabled')?.checked;}
  };
}
async function viewMonitor(root){
  let groups=await api('/api/groups');root.innerHTML=`<div class="analysis-head"><div><h1>Занятие сейчас</h1><p>Очередь внимания, сроки и события учеников</p></div><select id="monitorGroup"><option value="">Все доступные группы</option>${groups.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select></div><div id="monitorStatus" class="analysis-state" role="status"></div><div id="monitorStats"></div><div id="monitorCards"></div><div id="monitorLoad" style="margin-top:16px"></div>`;
  let generation=0;
  const refresh=async()=>{const id=++generation,gid=$('#monitorGroup')?.value||'';try{const a=await api('/api/analysis/live'+(gid?'?group_id='+gid:''));if(id!==generation||!root.isConnected||!$('#monitorCards'))return;
    $('#monitorStatus').textContent='Обновлено '+new Date(a.server_time*1000).toLocaleTimeString('ru-RU');$('#monitorStatus').className='analysis-state';
    const n=a.cards.length,late=a.cards.filter(x=>x.overdue).length;
    $('#monitorStats').innerHTML=`<div class="metric-grid">${[['Карточек',n],['Просрочено',late],['С помощником',a.cards.filter(x=>x.assisted).length],['Технические проблемы',a.cards.filter(x=>x.technical_issue).length]].map(([n,v])=>`<div class="metric-tile"><small>${n}</small><strong>${v}</strong></div>`).join('')}</div>`;
    $('#monitorCards').innerHTML=`<section class="card"><h2>Очередь внимания</h2><div class="chart-table"><table class="t"><tr><th>Ученик</th><th>Режим / статус</th><th>Время</th><th>Помощь</th><th></th></tr>${a.cards.map(x=>`<tr><td>${esc(x.name)}${x.overdue?' <span class="badge b-bad">Просрочка</span>':''}</td><td>${esc(x.kind)} · ${esc(x.status)}</td><td>${x.elapsed} с / ${x.limit} с</td><td>${x.assisted?'Да':'Нет'}</td><td><button class="btn btn-g btn-sm" data-intervene="${esc(x.id)}">Вводная</button> <button class="btn btn-g btn-sm" data-watch="${esc(x.id)}">Наблюдать</button></td></tr>`).join('')||'<tr><td colspan="5">Активных карточек нет</td></tr>'}</table></div></section>`;
    $('#monitorLoad').innerHTML=Charts.load(a);
    root.querySelectorAll('[data-intervene]').forEach(b=>b.onclick=()=>interventionForm(b.dataset.intervene));root.querySelectorAll('[data-watch]').forEach(b=>b.onclick=()=>watchAttempt(b.dataset.watch));
  }catch(e){if($('#monitorStatus')){$('#monitorStatus').className='analysis-state error';$('#monitorStatus').textContent='Связь потеряна. Показаны последние данные: '+e.message;}}};
  $('#monitorGroup').onchange=refresh;await refresh();clearInterval(S.liveTimer);S.liveTimer=setInterval(refresh,3000);
}
async function watchAttempt(sid){const r=await api(`/api/session/${sid}/razbor`);modal(`<div class="row sp"><h2>Наблюдение за попыткой</h2><button class="btn btn-g" onclick="closeModal()">Закрыть</button></div><div class="timeline-events">${r.events.map(e=>`<div class="event-row"><small>${e.seq} · ${esc(fmtT(e.ts))}</small><p>${esc(eventText(e))}</p></div>`).join('')}</div><button class="btn btn-g" onclick="watchAttempt('${sid}')">Обновить</button>`);}
function interventionForm(sid){
  modal(`<h2>Вводная преподавателя</h2><form id="interventionForm"><label>Событие<select id="interventionType">${[['notice','Сообщение диспетчеру'],['duplicate','Повторная карточка'],['ne_kompetenciya','Вне компетенции'],['otkaz','Отмена реагирования'],['pribytie','Бригада прибыла'],['raboty','Начало работ'],['zaversheno','Работы завершены']].map(([v,n])=>`<option value="${v}">${n}</option>`).join('')}</select></label><label>Сообщение ученику<textarea id="interventionText" minlength="5" required></textarea></label><p class="hint">Событие, автор и время войдут в историю. Сервер проверяет допустимость перехода.</p><button class="btn">Передать вводную</button></form>`);
  $('#interventionForm').onsubmit=async e=>{e.preventDefault();try{await api(`/api/training/${sid}/intervene`,{type:$('#interventionType').value,text:$('#interventionText').value});closeModal();toast('Вводная передана','ok');}catch(err){toast(err.message,'bad');}};
}

async function viewLessons(root){
  const [groups,scenarios,services,lessons,materials,sessions]=await Promise.all([api('/api/groups'),api('/api/scenarios?status=published'),api('/api/dds/sluzhby'),api('/api/lessons'),api('/api/materials'),api('/api/sessions')]);
  root.innerHTML=`<div class="analysis-head"><div><h1>Учебные занятия</h1><p>Состав, условия, поток карточек и запуск всей группы</p></div></div><div class="grid2"><section class="card"><h2>Создать занятие</h2><form id="lessonForm" class="form-grid">
    <label class="full">Название<input id="lessonName" required minlength="2" value="Практическое занятие ДДС"></label>
    <label>Группа<select id="lessonGroup">${groups.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></label>
    <label>Программа<select id="lessonKind"><option value="dds">Диспетчер ДДС</option><option value="ops112">Оператор 112</option></select></label>
    <label>Профиль службы<select id="lessonService"><option value="">По сценарию</option>${services.map(x=>`<option value="${esc(x.korotko)}">${esc(x.korotko)}</option>`).join('')}</select></label>
    <label>Источник карточек<select id="lessonSource"><option value="system">Системные сценарии</option><option value="student">Карточки учеников 112</option><option value="mixed">Смешанный поток</option></select></label>
    <label>Режим<select id="lessonMode"><option value="practice">Тренировка</option><option value="exam">Самостоятельный экзамен</option><option value="assisted_exam">Проверка работы с помощником</option></select></label>
    <label>Помощник<select id="lessonHelp"><option value="false">Выключен</option><option value="true">Ученик может включить</option></select></label>
    <label>Интервал выдачи, с<input id="lessonInterval" type="number" min="5" max="3600" value="60" required></label>
    <label>Одновременно карточек<input id="lessonMax" type="number" min="1" max="20" value="3" required></label>
    <label>Подтверждение ДДС, с<input id="lessonAck" type="number" min="5" value="30" required></label>
    <label>Статус после сведений, с<input id="lessonStatus" type="number" min="5" value="60" required></label>
    <label>Заполнение карточки 112, с<input id="lessonCard" type="number" min="5" value="75" required></label>
    <label>Порог балла<input id="lessonThreshold" type="number" min="0" max="100" value="70" required></label>
    <details class="full"><summary>Веса критериев</summary><p class="hint">Оставьте пустым для стандартных весов. Новые настройки закрепятся в новых попытках.</p><div class="form-grid">${[['podtverzhdenie','Подтверждение'],['peredacha','Передача'],['po_faktu','Статусы по факту'],['etapy','Этапы'],['kommentarii','Комментарии'],['grammatika','Грамматика'],['adres','Адрес 112'],['tip','Классификация']].map(([k,n])=>`<label>${n}<input type="number" min="0" max="10" data-weight="${k}" placeholder="Стандарт"></label>`).join('')}</div></details>
    <div class="full"><label>Системные сценарии</label><input id="lessonScenarioSearch" placeholder="Поиск сценария"><div class="list" id="lessonScenarios" style="max-height:220px"></div></div>
    <details class="full"><summary>Карточки, заполненные учениками 112</summary><div class="list" style="max-height:160px">${sessions.filter(x=>x.kind==='ops112').map(x=>`<label><input type="checkbox" data-source-session="${esc(x.id)}" style="width:auto"> ${esc(x.name)} · ${esc(x.scenario_id)} · ${esc(fmtT(x.started))}</label>`).join('')||'Нет завершённых карточек'}</div></details>
    <details class="full"><summary>Учебные материалы</summary>${materials.map(x=>`<label><input type="checkbox" data-lesson-material="${esc(x.id)}" style="width:auto"> ${esc(x.title)}</label>`).join('')||'Материалы пока не загружены'}</details>
    <p class="hint full">Лимиты и веса — настройки преподавателя. Остановка занятия сохраняет незавершённые попытки отдельно от оценённых.</p><button class="btn full" ${groups.length?'':'disabled'}>Сохранить занятие</button><div id="lessonFormStatus" class="full" role="status"></div></form></section>
    <section class="card"><h2>Занятия</h2><div class="lesson-list">${lessons.map(l=>`<div class="lesson-row"><h3>${esc(l.config.name)}</h3><p>${esc(groups.find(g=>g.id===l.group_id)?.name||'Группа')} · ${esc(l.config.kind)} · ${esc(l.state)}</p><p class="hint">Интервал ${l.config.interval_sec} с · максимум ${l.config.max_active} карточек · помощь ${l.config.helper_allowed?'разрешена':'выключена'}</p>${l.owner_id===S.user.id?`<button class="btn ${l.state==='running'?'btn-g':''}" data-lesson-action="${l.state==='draft'?'start':'stop'}" data-lid="${esc(l.id)}" ${l.state==='stopped'?'disabled':''}>${l.state==='draft'?'Запустить группу':l.state==='running'?'Завершить занятие':'Завершено'}</button>`:''}</div>`).join('')||'<p>Занятий пока нет.</p>'}</div></section></div>`;
  const selected=new Set(),drawScenarios=()=>{const q=$('#lessonScenarioSearch').value.toLowerCase();$('#lessonScenarios').innerHTML=scenarios.filter(x=>!q||(x.nazvanie+' '+x.situaciya).toLowerCase().includes(q)).map(x=>`<label class="arm-line"><input type="checkbox" data-scenario-choice="${esc(x.id)}" style="width:auto" ${selected.has(x.id)?'checked':''}> ${esc(x.nazvanie)} · ${x.slozhnost}/5</label>`).join('');$('#lessonScenarios').querySelectorAll('[data-scenario-choice]').forEach(x=>x.onchange=()=>x.checked?selected.add(x.dataset.scenarioChoice):selected.delete(x.dataset.scenarioChoice));};
  drawScenarios();$('#lessonScenarioSearch').oninput=drawScenarios;
  $('#lessonMode').onchange=()=>{if($('#lessonMode').value==='exam')$('#lessonHelp').value='false';$('#lessonHelp').disabled=$('#lessonMode').value==='exam';};
  $('#lessonForm').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button[type=submit],button.btn.full');button.disabled=true;
    try{const weights={};root.querySelectorAll('[data-weight]').forEach(el=>{if(el.value!=='')weights[el.dataset.weight]=Number(el.value);});
      await api('/api/lessons',{name:$('#lessonName').value,group_id:Number($('#lessonGroup').value),kind:$('#lessonKind').value,scenario_ids:[...selected],service:$('#lessonService').value||null,source:$('#lessonSource').value,
        student_session_ids:[...root.querySelectorAll('[data-source-session]:checked')].map(x=>x.dataset.sourceSession),mode:$('#lessonMode').value,helper_allowed:$('#lessonHelp').value==='true',
        interval_sec:Number($('#lessonInterval').value),max_active:Number($('#lessonMax').value),ack_sec:Number($('#lessonAck').value),status_sec:Number($('#lessonStatus').value),card_sec:Number($('#lessonCard').value),
        grading:{threshold:Number($('#lessonThreshold').value),weights},material_ids:[...root.querySelectorAll('[data-lesson-material]:checked')].map(x=>x.dataset.lessonMaterial)});
      go('lessons');toast('Занятие подготовлено. Нажмите «Запустить группу».','ok');
    }catch(err){$('#lessonFormStatus').textContent=err.message;button.disabled=false;}};
  root.querySelectorAll('[data-lesson-action]').forEach(b=>b.onclick=async()=>{if(b.dataset.lessonAction==='stop'&&!confirm('Завершить занятие? Незаконченные попытки сохранятся как остановленные.'))return;b.disabled=true;try{await api(`/api/lessons/${b.dataset.lid}/${b.dataset.lessonAction}`,{});go('lessons');}catch(e){b.disabled=false;toast(e.message,'bad');}});
}
async function viewMaterials(root){
  const rows=await api('/api/materials'),teacher=S.user.role==='teacher';
  root.innerHTML=`<div class="analysis-head"><div><h1>Учебные материалы</h1><p>Назначенные документы и версии источников</p></div></div>${teacher?`<section class="card"><form id="materialUpload" class="form-grid"><label>Название<input id="materialTitle" placeholder="Памятка ДДС"></label><label>Документ<input id="materialFile" type="file" accept=".txt,.md,.pdf,.docx" required></label><p class="hint full">До 20 МБ. PDF должен содержать текст; для скана загрузите результат OCR.</p><button class="btn">Загрузить</button><span id="materialStatus" role="status"></span></form></section>`:''}<section class="card" style="margin-top:15px"><h2>Документы</h2>${rows.map(x=>`<div class="lesson-row"><button class="btn btn-g" data-material="${esc(x.id)}">${esc(x.title)}</button><p class="hint">${esc(x.filename)} · ${x.meta.chars} символов · версия ${esc(x.hash.slice(0,12))} · ${esc(fmtT(x.created))}</p></div>`).join('')||'<p>Материалов пока нет.</p>'}</section>`;
  root.querySelectorAll('[data-material]').forEach(b=>b.onclick=async()=>{const m=await api('/api/materials/'+b.dataset.material);modal(`<div class="row sp"><h2>${esc(m.title)}</h2><button class="btn btn-g" onclick="closeModal()">Закрыть</button></div><div class="material-text">${esc(m.content)}</div>`);});
  if(teacher)$('#materialUpload').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{const fd=new FormData();fd.append('file',$('#materialFile').files[0]);fd.append('title',$('#materialTitle').value);const r=await fetch('/api/materials',{method:'POST',headers:{Authorization:'Bearer '+S.token},body:fd}),j=await r.json();if(!r.ok)throw new Error(j.detail);go('materials');}catch(err){$('#materialStatus').textContent=err.message;button.disabled=false;}};
}
async function viewScenarioStudio(root){
  const [all,mats]=await Promise.all([api('/api/scenarios'),api('/api/materials')]);let code='';
  root.innerHTML=`<div class="analysis-head"><div><h1>Сценарии и учебные цели</h1><p>Генерация → проверка → исправление → утверждение</p></div></div><div class="grid2"><section class="card"><h2>Новый сценарий</h2><form id="studioForm" class="form-grid"><label class="full">Тип по классификатору<input id="studioSearch" placeholder="Пожар, ДТП, помощь…" required><div id="studioResults" class="list" style="max-height:150px"></div><small id="studioCode"></small></label><label>Программа<select id="studioKind"><option value="dds">ДДС</option><option value="ops112">Оператор 112</option></select></label><label>Сложность<select id="studioLevel">${[1,2,3,4,5].map(n=>`<option ${n===3?'selected':''}>${n}</option>`).join('')}</select></label><label class="full">Учебная цель<textarea id="studioGoal" placeholder="Например: передать уточнённый адрес и неизвестность о пострадавших"></textarea></label><label class="full">Место<input id="studioLocation" placeholder="Оставьте пустым для генерации"></label><label>Уточнение адреса<select id="studioClarify"><option value="true">Нужно уточнить</option><option value="false">Назван сразу</option></select></label><label>Количество<input id="studioCount" type="number" min="1" max="5" value="1"></label><details class="full"><summary>Источники</summary>${mats.map(x=>`<label><input style="width:auto" type="checkbox" data-studio-material="${esc(x.id)}"> ${esc(x.title)}</label>`).join('')}</details><button class="btn full" id="studioGenerate" disabled>Сгенерировать черновик</button><p class="hint full" id="studioStatus" role="status"></p></form></section><section class="card"><h2>Банк сценариев</h2><input id="studioFilter" placeholder="Поиск по названию и ситуации"><div id="studioList" class="list" style="max-height:650px"></div></section></div>`;
  let searchSeq=0;$('#studioSearch').oninput=async e=>{const seq=++searchSeq;code='';$('#studioGenerate').disabled=true;const q=e.target.value;if(q.length<2)return;const r=await api('/api/classifier/search?q='+encodeURIComponent(q));if(seq!==searchSeq)return;$('#studioResults').innerHTML=r.slice(0,15).map(x=>`<button type="button" class="btn btn-g btn-sm" data-code="${esc(x.kod)}">${esc(x.itog)} · ${esc(x.kod)}</button>`).join('');$('#studioResults').querySelectorAll('[data-code]').forEach(b=>b.onclick=()=>{code=b.dataset.code;$('#studioCode').textContent=b.textContent;$('#studioResults').innerHTML='';$('#studioGenerate').disabled=false;});};
  const draw=()=>{const q=$('#studioFilter').value.toLowerCase();$('#studioList').innerHTML=all.filter(x=>!q||(x.nazvanie+' '+x.situaciya).toLowerCase().includes(q)).map(x=>`<div class="lesson-row"><span class="badge ${x.status==='published'?'b-ok':'b-warn'}">${esc(x.status)}</span> <b>${esc(x.nazvanie)}</b><p>${esc(x.situaciya.slice(0,180))}</p><button class="btn btn-g btn-sm" data-scenario-edit="${esc(x.id)}">Проверить / исправить</button></div>`).join('');$('#studioList').querySelectorAll('[data-scenario-edit]').forEach(b=>b.onclick=()=>scenarioEditor(b.dataset.scenarioEdit));};draw();$('#studioFilter').oninput=draw;
  $('#studioForm').onsubmit=async e=>{e.preventDefault();$('#studioGenerate').disabled=true;$('#studioStatus').textContent='Генерация выполняется. Остальные ученики могут продолжать работу.';try{const result=await api('/api/scenario-tools/generate',{kod:code,difficulty:Number($('#studioLevel').value),kind:$('#studioKind').value,goal:$('#studioGoal').value,location:$('#studioLocation').value,clarification:$('#studioClarify').value==='true',count:Number($('#studioCount').value),material_ids:[...root.querySelectorAll('[data-studio-material]:checked')].map(x=>x.dataset.studioMaterial)});await viewScenarioStudio(root);scenarioEditor(result[0].scenario.id);}catch(err){$('#studioStatus').textContent=err.message;$('#studioGenerate').disabled=false;}};
}
async function scenarioEditor(sid){
  const r=await api(`/api/scenario-tools/${sid}/preview`),s=r.scenario;
  modal(`<div class="row sp"><h2>Проверка сценария</h2><button class="btn btn-g" onclick="closeModal()">Закрыть</button></div><p>${esc(sid)} · ${esc(r.status)}</p><div class="form-grid"><label class="full">Ситуация<textarea id="editSituation">${esc(s.situaciya)}</textarea></label><label class="full">Адрес со слов заявителя<input id="editVisible" value="${esc(s.adres_vidimy)}"></label><label class="full">Точный адрес<input id="editGold" value="${esc(s.adres_etalon)}"></label><label>ФИО<input id="editName" value="${esc(s.zayavitel.fio)}"></label><label>Телефон<input id="editPhone" value="${esc(s.zayavitel.telefon)}"></label><label>Сложность<input id="editLevel" type="number" min="1" max="5" value="${s.slozhnost}"></label><label>Ветка ДДС<select id="editBranch"><option value="standard">Обычное реагирование</option><option value="outside_competence" ${s.outside_competence?'selected':''}>Вне компетенции</option><option value="duplicate" ${s.duplicate?'selected':''}>Дубликат</option><option value="cancel_allowed" ${s.cancel_allowed?'selected':''}>Отмена после выезда</option><option value="no_dispatch" ${s.no_dispatch?'selected':''}>103 без выезда</option></select></label><label class="full">Замечание преподавателя<textarea id="editComment" placeholder="Что изменить в новой версии"></textarea></label><div class="full row"><button class="btn btn-g" id="editAI">Исправить по замечанию через ИИ</button><button class="btn btn-g" id="editManual">Сохранить ручные правки новой версией</button><button class="btn" id="editPublish">Утвердить текущую версию</button></div><p id="editStatus" class="full hint" role="status"></p></div>${s.correction?`<h3>Изменения этой версии</h3>${s.correction.changes.map(x=>`<div class="lesson-row"><b>${esc(x.field)}</b><p>Было: ${esc(typeof x.before==='object'?JSON.stringify(x.before):x.before)}</p><p>Стало: ${esc(typeof x.after==='object'?JSON.stringify(x.after):x.after)}</p></div>`).join('')}`:''}`);
  const corrections=()=>{const branch=$('#editBranch').value;return {situaciya:$('#editSituation').value,adres_vidimy:$('#editVisible').value,adres_etalon:$('#editGold').value,zayavitel:{fio:$('#editName').value,telefon:$('#editPhone').value},slozhnost:Number($('#editLevel').value),outside_competence:branch==='outside_competence',duplicate:branch==='duplicate',cancel_allowed:branch==='cancel_allowed',no_dispatch:branch==='no_dispatch',...(branch==='no_dispatch'?{service_profiles:['Служба 103']}: {})};};
  const correct=async manual=>{const message=$('#editComment').value;if(message.trim().length<5)return toast('Опишите причину правки, не менее 5 символов','bad');$('#editStatus').textContent='Готовим новую версию…';try{const n=await api(`/api/scenario-tools/${sid}/correct`,{comment:message,edits:manual?corrections():{}});scenarioEditor(n.scenario.id);}catch(e){$('#editStatus').textContent=e.message;}};
  $('#editAI').onclick=()=>correct(false);$('#editManual').onclick=()=>correct(true);
  $('#editPublish').onclick=async()=>{try{const edits=corrections();if(['situaciya','adres_vidimy','adres_etalon','slozhnost'].some(k=>edits[k]!==s[k])||edits.zayavitel.fio!==s.zayavitel.fio||edits.zayavitel.telefon!==s.zayavitel.telefon)return toast('Сначала сохраните правки новой версией','bad');await api(`/api/scenarios/${sid}/validate`,{status:'published',comment:'Проверено преподавателем'});closeModal();go('scen');}catch(e){$('#editStatus').textContent=e.message;}};
}
async function viewScopes(root){
  const [users,groups,grants]=await Promise.all([api('/api/users'),api('/api/groups'),api('/api/admin/scopes')]),teachers=users.filter(x=>x.role==='teacher');
  root.innerHTML=`<div class="analysis-head"><div><h1>Преподаватели и группы</h1><p>Доступ к занятиям, оценкам, аналитике и материалам учеников</p></div></div><section class="card"><table class="t"><tr><th>Преподаватель</th>${groups.map(g=>`<th>${esc(g.name)}</th>`).join('')}</tr>${teachers.map(t=>`<tr><th>${esc(t.name)}</th>${groups.map(g=>`<td><input type="checkbox" style="width:auto" data-teacher="${t.id}" data-group="${g.id}" ${grants.some(x=>x.teacher_id===t.id&&x.group_id===g.id)?'checked':''} aria-label="${esc(t.name+' — '+g.name)}"></td>`).join('')}</tr>`).join('')}</table></section>`;
  root.querySelectorAll('[data-teacher]').forEach(x=>x.onchange=async()=>{try{await api('/api/admin/scopes',{teacher_id:Number(x.dataset.teacher),group_id:Number(x.dataset.group),allowed:x.checked});toast('Доступ сохранён','ok');}catch(e){x.checked=!x.checked;toast(e.message,'bad');}});
}
