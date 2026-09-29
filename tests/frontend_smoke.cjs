/* Real DOM + real isolated HTTP API. Does not certify browser layout, audio or GPU. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),base=process.env.TEST_BASE_URL||'http://127.0.0.1:18080';
const errors=[];
async function wait(fn,msg){const end=Date.now()+10000;while(Date.now()<end){if(fn())return;await new Promise(r=>setTimeout(r,30));}throw Error(msg);}
async function main(){
 const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
 const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace(/<script[^>]*src=[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:base,runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:vc});const w=dom.window;
 w.fetch=(url,opts)=>fetch(new URL(url,base),opts);w.AbortController=AbortController;w.confirm=()=>true;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
 for(const file of ['network','dds','app','charts','insights','workspace','integration']){const tag=w.document.createElement('script');tag.textContent=fs.readFileSync(path.join(root,'web',file+'.js'),'utf8');w.document.body.append(tag);}
 const $=s=>w.document.querySelector(s);
 async function login(role){w.logout();await wait(()=>$('#lg'),'login form');$('#lg').value=role;$('#pw').value=role+'112';$('#go').click();await wait(()=>$('#nav [data-v]')&&w.eval('S.user?.role')===role,'login '+role);await new Promise(r=>setTimeout(r,100));}
 console.log('UI login trainee');await login('trainee');await wait(()=>$('#queueStrip'),'queue');assert($('#myLessons'));
 const start=await w.api('/api/session',{scenario_id:'b01_v1'}),sid=start.session_id;
 console.log('UI resume',sid);await w.resumeAttempt(sid);assert($('#helperPanel'));assert.equal($('#helperEnabled').checked,false);assert($('#f_adres'));
 $('#f_adres').value='Москва, улица Тестовая, дом 10';$('#f_adres').dispatchEvent(new w.Event('input',{bubbles:true}));await w.flushFields();
 await w.resumeAttempt(sid);assert.equal($('#f_adres').value,'Москва, улица Тестовая, дом 10');
 await w.api(`/api/helper/${sid}/toggle`,{enabled:true});await w.resumeAttempt(sid);assert($('#helperEnabled').checked);
 await w.api(`/api/session/${sid}/otpravit`,{kartochka:w.eval('S.card')});
 await w.api(`/api/training/${sid}/analyze`,{});await w.detailedRazbor(sid);assert($('#modalBox').textContent.includes('Разбор'));w.closeModal();
 w.go('progress');await wait(()=>$('#attemptTable'),'profile charts');assert(w.document.querySelectorAll('.chart-card').length>=5);
 console.log('UI login teacher');await login('teacher');w.go('group');await wait(()=>$('#chartHeat'),'group heatmap');
 const f=$('[data-filter="kind"]');f.value='ops112';f.dispatchEvent(new w.Event('change'));await wait(()=>$('#attemptTable').textContent.includes('b01_v1'),'filter completed attempt');
 const point=$('[data-attempt-ids]');assert(point);point.dispatchEvent(new w.MouseEvent('click',{bubbles:true}));assert($('#attemptSelection').textContent.includes('Выбрано'));
 w.go('quality');await wait(()=>$('#view').textContent.includes('V09'),'quality');
 w.go('lessons');await wait(()=>$('#lessonForm'),'lesson form');assert($('#lessonSource').options.length===3);assert($('#lessonScenarios'));
 w.go('materials');await wait(()=>$('#view').textContent.includes('Материал'),'materials');
 w.go('scen');await wait(()=>$('#view').textContent.includes('Сценарии'),'scenario studio');
 await login('admin');w.go('scopes');await wait(()=>$('#view').textContent.includes('Доступ'),'scopes');
 assert.deepEqual(errors,[]);dom.window.close();console.log('PASS: login 3 roles, queue, 112 resume/manual field/helper, completed debrief, analytics filters/drilldown, lessons, sources, quality, materials, scenarios, scopes (JSDOM + HTTP; no visual/browser certification)');
}
main().then(()=>process.exit(0)).catch(e=>{console.error(e,errors);process.exit(1)});
