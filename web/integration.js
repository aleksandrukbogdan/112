'use strict';
/* Final wiring runs after the supplied frontend and additive screens have loaded. */
TABS.trainee.unshift(['queue','Очередь']);
TABS.trainee.push(['materials','Материалы']);
TABS.teacher.splice(1,0,['lessons','Занятия']);
TABS.teacher.push(['quality','Качество ИИ'],['materials','Материалы'],['queue','Мои карточки']);
TABS.admin.push(['scopes','Доступ к группам'],['quality','Качество ИИ']);
Object.assign(VIEWS,{queue:viewQueue,lessons:viewLessons,materials:viewMaterials,quality:viewAIQuality,scopes:viewScopes,
  scen:viewScenarioStudio,monitor:viewMonitor,group:viewGroup,progress:root=>viewInsights(root)});
async function refreshVoiceHealth(){
  try{const h=await fetch('/health').then(r=>r.json());S.voice=!!h.voice?.asr;S.tts=!!h.voice?.tts;S.engine=h.voice?.engine;S.ttsEngine=h.voice?.tts_engine;
    const mic=$('#mic'),cmic=$('#cMic');if(mic){mic.disabled=!S.voice;if(S.voice)mic.onclick=toggleMic;}if(cmic){cmic.disabled=!S.voice;if(S.voice)cmic.onclick=()=>micTo(t=>ddsSay(t),cmic);}
  }catch{/* Text interaction remains available; voice diagnostics are shown in System. */}
}
window.addEventListener('t112-synchronized',()=>{refreshQueue(false);});
window.addEventListener('beforeunload',e=>{if(Transport.pending().length){e.preventDefault();e.returnValue='Есть неподтверждённые изменения';}});
const originalGo=go;
go=function(v){
  if(Workspace.mode==='ops112')flushFields().catch(e=>toast(e.message,'bad'));
  if(!['queue'].includes(v)){Workspace.mode=null;clearInterval(DS.poll);clearInterval(DS.tim);clearInterval(S.timer);document.querySelectorAll('.ring').forEach(x=>x.remove());}
  return originalGo(v);
};
overrideDlg=function(sid){return detailedRazbor(sid);};
boot();
