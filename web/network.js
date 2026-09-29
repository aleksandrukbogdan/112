'use strict';
/* Durable, bounded retries only for server-idempotent session commands. */
const Transport = (()=>{
  const storage='t112_pending_commands_v2', inflight=new Map();let replaying=false;
  const get=()=>{try{return JSON.parse(localStorage.getItem(storage)||'[]');}catch{return [];}};
  const save=xs=>localStorage.setItem(storage,JSON.stringify(xs));
  const remove=id=>save(get().filter(x=>x.id!==id));
  function banner(message){let b=document.getElementById('syncBanner');if(!b){b=document.createElement('div');b.id='syncBanner';b.className='sync-banner';b.setAttribute('role','status');document.body.prepend(b);}b.textContent=message;b.classList.toggle('visible',!!message);}
  function pending(){return get().filter(x=>typeof S!=='undefined'&&x.uid===S.user?.id);}
  async function request(path,body,method){
    method=method||(body===undefined?'GET':'POST');
    const safe=method==='POST'&&(/^\/api\/session\/[\w-]+\/(pole|otpravit|replika)$/.test(path)||/^\/api\/dds\/([\w-]+)\/(status|call(?:\/[\w-]+\/(say|end))?|incoming\/[\w-]+\/answer|finish|zamechanie)$/.test(path)||/^\/api\/helper\/[^/]+\/(toggle|proposals\/[^/]+)$/.test(path)||/^\/api\/training\/[^/]+\/(replay|intervene)$/.test(path));
    if(/\/pole$/.test(path)&&body){let client=localStorage.getItem('t112_client_id');if(!client){client=crypto.randomUUID();localStorage.setItem('t112_client_id',client);}const seq=Number(localStorage.getItem('t112_field_sequence')||0)+1;localStorage.setItem('t112_field_sequence',String(seq));body={...body,client_id:client,client_seq:seq};}
    const entry={id:crypto.randomUUID(),uid:S.user?.id,path,method,body,created:Date.now()};
    if(safe)save([...get(),entry]);
    return execute(entry,safe);
  }
  async function execute(entry,durable){
    if(inflight.has(entry.id))return inflight.get(entry.id);
    const work=(async()=>{
      const deadline=Date.now()+30000;let last;
      while(true){
        const controller=new AbortController(),slow=/generate|correct|analyze|suggest|replika/.test(entry.path);
        const timer=setTimeout(()=>controller.abort(),slow?240000:12000);
        try{
          const response=await fetch(entry.path,{method:entry.method,headers:{Authorization:'Bearer '+(S.token||''),...(entry.body!==undefined?{'Content-Type':'application/json'}:{}),...(durable?{'Idempotency-Key':entry.id}:{})},body:entry.body===undefined?undefined:JSON.stringify(entry.body),signal:controller.signal});
          const j=await response.json().catch(()=>({}));
          if(!response.ok){
            if(response.status===401){if(durable)remove(entry.id);throw Object.assign(new Error('Сессия истекла. Войдите заново.'),{permanent:true});}
            if(response.status===409&&String(j.detail).includes('ещё обрабатывается'))throw new Error(j.detail);
            if(durable)remove(entry.id);
            throw Object.assign(new Error(typeof j.detail==='string'?j.detail:JSON.stringify(j.detail)||`Ошибка ${response.status}`),{permanent:true,status:response.status});
          }
          if(durable)remove(entry.id);if(!pending().length)banner('');return j;
        }catch(e){last=e;if(e.permanent||!durable)throw e;banner('Соединение прервано. Изменения сохранены в очереди и ожидают подтверждения сервера.');}
        finally{clearTimeout(timer);}
        if(Date.now()>=deadline)throw new Error('Сервер пока недоступен. Команда сохранена; отправка продолжится при восстановлении связи.');
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    })();inflight.set(entry.id,work);try{return await work;}finally{inflight.delete(entry.id);}
  }
  async function replay(){
    if(replaying||typeof S==='undefined'||!S.user||!navigator.onLine)return;replaying=true;let changed=false;
    try{for(const entry of pending()){if(inflight.has(entry.id))continue;try{await execute(entry,true);changed=true;}catch(e){if(e.permanent)banner('Команда не применена: '+e.message);else break;}}}finally{replaying=false;}
    if(changed)window.dispatchEvent(new Event('t112-synchronized'));
  }
  async function flush(){
    const all=[...inflight.values()];if(all.length)await Promise.all(all);if(pending().length){await replay();if(pending().length)throw new Error('Есть неподтверждённые команды. Дождитесь восстановления связи.');}
  }
  window.addEventListener('online',replay);setInterval(replay,4000);
  return {request,pending,flush,banner};
})();
