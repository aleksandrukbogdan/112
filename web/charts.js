/* Local SVG charts. Data and denominators come exclusively from the analytics API. */
'use strict';
const Charts = (() => {
  const h = s => esc(s), num = (v,n=1) => v == null ? '—' : Number(v).toLocaleString('ru-RU',{maximumFractionDigits:n});
  const colors = ['#2e6896','#258467','#c65f39','#7864a0'];
  function table(headers,rows) { return `<div class="chart-table"><table class="t"><thead><tr>${headers.map(x=>`<th>${h(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${h(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`; }
  function panel(title,sub,visual,headers,rows,extra='') { return `<section class="card chart-card"><div class="chart-title"><h2>${h(title)}</h2>${extra}</div><p class="chart-sub">${h(sub)}</p>${rows.length?visual:'<div class="chart-empty">Нет наблюдений для выбранных условий</div>'}<details class="chart-details"><summary>Данные таблицей · ${rows.length}</summary>${table(headers,rows)}</details></section>`; }
  function svg(content,label,height=230) {return `<svg class="chart-svg" viewBox="0 0 700 ${height}" role="img" aria-label="${h(label)}">${content}</svg>`;}
  function axes(max=100,label='Баллы') {let s='';for(let v=0;v<=4;v++){const y=195-v*40;s+=`<line x1="45" y1="${y}" x2="670" y2="${y}" class="chart-grid"/><text x="35" y="${y+4}" text-anchor="end">${num(max*v/4)}</text>`;}return s+`<text x="45" y="16">${h(label)}</text>`;}
  function hit(ids,content,title) {return `<g tabindex="0" role="button" class="chart-hit" data-attempt-ids="${h(ids.join(','))}" aria-label="${h(title)}"><title>${h(title)}</title>${content}</g>`;}
  function trend(a) {
    const r=a.attempts, points=r.map((v,i)=>({x:45+i*625/Math.max(1,r.length-1),y:195-v.ball*1.6,v}));
    const comparable=new Set(r.map(x=>x.kind+':'+x.rubric)).size<=1;
    let s=axes();
    const thresholds=[...new Set(r.map(x=>x.pass_threshold??70))];
    if(thresholds.length===1){const value=thresholds[0],y=195-value*1.6;s+=`<line x1="45" y1="${y}" x2="670" y2="${y}" stroke="#b08036" stroke-dasharray="5 4"/><text x="665" y="${y-7}" text-anchor="end">${value} — порог балла</text>`;}
    if(comparable&&points.length>1)s+=`<polyline fill="none" stroke="#9fb6ca" stroke-width="2" points="${points.map(p=>p.x+','+p.y).join(' ')}"/>`;
    for(const p of points){const v=p.v,c=v.passed?'#258467':'#c14b49';const shape=v.critical_errors.length?`<path d="M${p.x},${p.y-6} l6,6 l-6,6 l-6,-6z" fill="${c}"/>`:`<circle cx="${p.x}" cy="${p.y}" r="5" fill="${c}"/>`;s+=hit([v.id],shape,`${v.name}: ${v.ball} баллов, ${v.passed?'зачёт':'не зачтено'}, ${fmtT(v.finished)}`);}
    if(r.length)s+=`<text x="45" y="220">${h(fmtT(r[0].finished))}</text><text x="670" y="220" text-anchor="end">${h(fmtT(r.at(-1).finished))}</text>`;
    return panel('Динамика результатов',`V01 · ${r.length} попыток · ● результат · ◆ критическая ошибка · зачёт зависит от критериев`,svg(s,'Баллы по попыткам'),['Дата','Ученик','Программа','Балл','Зачёт','Критические ошибки'],r.map(x=>[fmtT(x.finished),x.name,x.kind,x.ball,x.passed?'Да':'Нет',x.critical_errors.join(', ')]));
  }
  function skills(a) {
    const rows=a.skills;const visual=`<div class="skill-bars">${rows.map(x=>`<button class="skill-row" data-attempt-ids="${h(x.ids.join(','))}"><span>${h(x.name)} <small>${h(x.kind)}</small></span><span class="bar-track"><i style="width:${100*(x.rate||0)}%;background:${colors[0]}"></i>${x.interval95?`<b class="bar-interval" style="left:${x.interval95[0]*100}%;width:${(x.interval95[1]-x.interval95[0])*100}%"></b>`:''}</span><strong>${pct(x.rate)}</strong><small>n=${x.n}</small></button>`).join('')}</div>`;
    return panel('Профиль навыков','V02 · успешные применимые проверки / все применимые · чёрная линия: интервал Уилсона',visual,['Навык','Программа','Успешно','Применимо','Доля','Не оценено'],rows.map(x=>[x.name,x.kind,x.success,x.n,pct(x.rate),x.unknown]));
  }
  function heatmap(a,axis='learner') {
    const cells=a.heatmap.filter(x=>x.axis===axis), entities=[...new Map(cells.map(x=>[x.entity,x.label]))], columns=[...new Map(cells.map(x=>[x.kind+':'+x.skill,x.skill_name]))];
    const visual=`<div class="chart-table"><table class="t heat-table"><thead><tr><th>${axis==='learner'?'Ученик':'Тип происшествия'}</th>${columns.map(([k,n])=>`<th>${h(n)}<small>${h(k.split(':')[0])}</small></th>`).join('')}</tr></thead><tbody>${entities.map(([id,label])=>`<tr><th>${h(label)}</th>${columns.map(([key])=>{const c=cells.find(x=>x.entity===id&&x.kind+':'+x.skill===key);return `<td>${c?`<button data-attempt-ids="${h(c.ids.join(','))}" style="background:rgba(185,63,59,${.05+c.rate*.35})">${pct(c.rate)}<small>${c.errors}/${c.n}</small></button>`:'<span class="muted">нет данных</span>'}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div>`;
    return panel('Карта ошибок','V03 · доля ошибок по применимым проверкам · выберите ячейку для разбора',visual,['Объект','Навык','Ошибок','Применимых'],cells.map(x=>[x.label,x.skill_name,x.errors,x.n]),`<select aria-label="Ось тепловой карты" data-heat-axis><option value="learner" ${axis==='learner'?'selected':''}>Ученики</option><option value="incident" ${axis==='incident'?'selected':''}>Типы происшествий</option></select>`);
  }
  function errors(a) {
    const visual=`<div class="skill-bars">${a.errors.map(x=>`<button class="error-row" data-attempt-ids="${h(x.ids.join(','))}"><span>${x.critical?'◆ ':''}${h(x.name)}</span><span class="bar-track"><i style="width:${100*(x.rate||0)}%;background:#bb6251"></i></span><strong>${x.errors}/${x.n}</strong><small>${pct(x.rate)}</small></button>`).join('')}</div>`;
    return panel('Наиболее частые ошибки','V04 · ◆ критический критерий · у одной попытки может быть несколько ошибок',visual,['Критерий','Ошибки','Применимо','Доля'],a.errors.map(x=>[x.name,x.errors,x.n,pct(x.rate)]));
  }
  function timing(a,key='ack') {
    const keys=Object.keys(a.timings);if(!a.timings[key])key=keys[0]||key;
    const r=a.timings[key]||{points:[],n:0},names={ack:'Подтверждение ДДС',card:'Отправка 112',status:'Обновление статуса после сведений',process:'Полная длительность попытки'};
    const values=r.points.filter(x=>x.value!=null),max=Math.max(1,...values.flatMap(x=>[x.value,x.limit||0]));let s=axes(max,'Секунды');
    for(const [i,p] of values.entries()){const x=45+i*625/Math.max(1,values.length-1),y=195-p.value/max*160; if(p.limit!=null){const ly=195-p.limit/max*160;s+=`<path d="M${x-4},${ly} h8" stroke="#b08036" stroke-width="2"/>`;}s+=hit([p.id],`<circle cx="${x}" cy="${y}" r="4" fill="${p.limit!=null&&p.value>p.limit?'#bd4c4c':colors[0]}"/>`,`${num(p.value)} с; лимит ${p.limit??'не задан'}`);}
    return panel(names[key]||'Время',`V05 · n=${r.n||0} · без действия: ${r.missing||0} · медиана ${num(r.median)} с · p95 ${num(r.p95)} (от 20 наблюдений)`,svg(s,'Время действий и закреплённые лимиты'),['Попытка','Секунд','Лимит'],r.points.map(x=>[x.id,x.value??'нет действия',x.limit??'не задан']),`<select data-time-axis aria-label="Метрика времени">${keys.map(k=>`<option value="${k}" ${k===key?'selected':''}>${names[k]}</option>`).join('')}</select>`);
  }
  function comparison(a) {
    const r=a.comparison.groups;return panel('Самостоятельно и с помощником',`V08 · сопоставимых пар: ${a.comparison.paired_n} · ${a.comparison.note}`,
      `<div class="compare-grid">${r.map(x=>`<div><h3>${x.mode==='independent'?'Самостоятельно':'С помощником'}</h3><strong>${x.n}</strong><p>попыток</p><p>Медиана времени: <b>${num(x.median_sec)} с</b></p><p>Критические ошибки: <b>${pct(x.critical_rate)}</b></p><button class="btn btn-g" data-attempt-ids="${h(x.ids.join(','))}">Открыть попытки</button></div>`).join('')}</div>`,['Режим','n','Медиана, с','Доля с критическими ошибками'],r.map(x=>[x.mode,x.n,x.median_sec,pct(x.critical_rate)]));
  }
  function calibration(a,index=0) {
    const models=a.forecast.by_model||[],m=models[index]||{n:0,calibration:[]};let s=axes(1,'Фактическая доля');s+='<line x1="45" y1="195" x2="670" y2="35" stroke="#a6b2bd" stroke-dasharray="5 4"/><text x="520" y="220">Средний прогноз</text>';
    for(const x of m.calibration){const px=45+x.predicted*625,py=195-x.observed*160;s+=`<circle cx="${px}" cy="${py}" r="${Math.min(12,4+Math.sqrt(x.n))}" fill="${colors[0]}"><title>Прогноз ${pct(x.predicted)}, факт ${pct(x.observed)}, n=${x.n}</title></circle>`;}
    return panel('Прогноз против результата',`V09 · n=${m.n} · Brier ${num(m.brier,4)} · базовая частота ${num(m.baseline_brier,4)} · ${m.n<30?'недостаточно данных':'описательная проверка, без доказанной обобщаемости'}`,svg(s,'Калибровка прогноза'),['Средний прогноз','Фактическая доля','n'],m.calibration.map(x=>[pct(x.predicted),pct(x.observed),x.n]),`<select data-model-axis aria-label="Версия модели">${models.map((x,i)=>`<option value="${i}" ${i===index?'selected':''}>${h(x.model)}</option>`).join('')}</select>`);
  }
  function helper(a) {
    const r=a.helper,max=Math.max(1,...Object.values(r.decisions));const names={shown:'Без решения',accept:'Приняты',edit:'Исправлены',reject:'Отклонены'};
    const bars=Object.entries(r.decisions).map(([k,v])=>`<div class="error-row"><span>${names[k]}</span><span class="bar-track"><i style="width:${v/max*100}%;background:${colors[0]}"></i></span><strong>${v}</strong></div>`).join('');
    return panel('Качество предложений помощника',`V10 · ${r.note}`,`<div class="compare-grid"><div><h3>Проверено преподавателем</h3><strong>${r.reviewed}</strong><p>Верных: ${r.correct} · ошибочных: ${r.wrong}</p><p>Доля верных: ${pct(r.correct_rate)}</p><p>Ученик исправил / отклонил неверные: ${r.corrected_wrong}/${r.wrong||'—'}</p></div><div><h3>Решения учеников</h3>${bars}</div></div>`,['Показано','Проверено','Верных','Ошибочных','Исправлено неверных'],r.shown?[[r.shown,r.reviewed,r.correct,r.wrong,r.corrected_wrong]]:[]);
  }
  function load(a) {
    const max=Math.max(1,...a.load.map(x=>x.open));let s=axes(max,'Карточки');for(const [i,k] of ['open','waiting','overdue'].entries()){s+=`<polyline fill="none" stroke="${colors[i]}" stroke-width="2.5" points="${a.load.map((x,j)=>`${45+j*625/30},${195-x[k]/max*160}`).join(' ')}"/>`;}
    return panel('Нагрузка занятия','V07 · синий: открыты · зелёный: ожидают действия · оранжевый: просрочены',svg(s,'Динамика нагрузки'),['Время','Открыты','Ожидают','Просрочены'],a.load.map(x=>[fmtT(x.ts),x.open,x.waiting,x.overdue]));
  }
  function timeline(r) {
    const events=r.events||[],base=r.session.started,lanes=['dialog','statusy','field_changed','helper','teacher','other'],labels=['Диалог','Статусы','Карточка','Помощник','Преподаватель','Система'];
    const last=Math.max(1,...events.map(e=>e.ts-base));let s='';
    lanes.forEach((lane,i)=>{const y=35+i*35;s+=`<text x="5" y="${y+4}">${labels[i]}</text><line x1="120" y1="${y}" x2="680" y2="${y}" class="chart-grid"/>`;});
    for(const e of events){let lane=lanes.indexOf(e.type);if(e.type.startsWith('helper'))lane=3;else if(e.type.startsWith('teacher'))lane=4;else if(e.type==='calls_changed')lane=0;if(lane<0)lane=5;const x=120+(e.ts-base)/last*550,y=35+lane*35;s+=`<g tabindex="0" role="button" class="chart-hit" data-event-id="${h(e.event_id)}" aria-label="${h(e.type)}"><title>${h(e.type)} · ${num(e.ts-base)} с</title><circle cx="${x}" cy="${y}" r="5" fill="${colors[lane%4]}"/></g>`;}
    return panel('Ход попытки','V06 · выберите событие: время, действие и источник оценки',svg(s,'Шкала событий',235),['№','Секунд','Событие'],events.map(e=>[e.seq,num(e.ts-base),e.type]));
  }
  return {panel,table,trend,skills,heatmap,errors,timing,comparison,calibration,helper,load,timeline,num};
})();
