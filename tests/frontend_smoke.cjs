/* Template/logic smoke tests in a fake DOM. These are explicitly NOT browser E2E. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
function extract(file,name){
  const text=fs.readFileSync(path.join(root,file),'utf8');
  const starts=[...text.matchAll(/^(?:async )?function (\w+)\(/gm)];
  const i=starts.findIndex(m=>m[1]===name);assert(i>=0,`Missing ${name}`);
  return text.slice(starts[i].index,i+1<starts.length?starts[i+1].index:text.length);
}
function node(){return {innerHTML:'',textContent:'',value:'',querySelector:()=>node(),querySelectorAll:()=>[],scrollIntoView(){}};}
const elements=new Map();
const get=s=>{if(!elements.has(s))elements.set(s,node());return elements.get(s);};
const ctx=vm.createContext({console,Date,Math,JSON,Number,String,Set,Map,
  clearInterval(){},confirm:()=>true,document:{querySelectorAll:()=>[],getElementById:()=>node()},
  $:get,esc:s=>String(s??'').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c])),
  pct:n=>n==null?'—':Math.round(n*100)+'%',fmtT:()=>'',dl:s=>s,
  modal:html=>get('#modalBox').innerHTML=html,closeModal(){},toast(message){throw new Error(message);},
  S:{user:{role:'trainee'},cfg:{gruppy:{},dds:{podtverzhdenie_sec:30}},dialog:[]},
  DS:{sid:'a',sluzhby:[]},POST_RU:{net:'нет'}});
for(const name of ['factsHtml','detali'])vm.runInContext(extract('web/app.js',name),ctx);
for(const name of ['viewDdsList','ddsFinish'])vm.runInContext(extract('web/dds.js',name),ctx);
vm.runInContext(fs.readFileSync(path.join(root,'web/insights.js'),'utf8'),ctx);

(async()=>{
  const fact={kod:'rech',nazvanie:'<script>alert(1)</script>',proyden:null,ves:1,istochnik:'test',detali:{}};
  const html=ctx.factsHtml([fact]);assert(html.includes('не проверено'));assert(!html.includes('<script>'));
  ctx.api=async()=>[{id:'a',nazvanie:'A',situaciya:'Scenario',adres_vidimy:'Address',slozhnost:1}];
  await ctx.viewDdsList(node());assert(get('#ddsBody').innerHTML.includes('Указан в карточке'));
  ctx.api=async()=>({ball:74,passed:false,verdikt:'не зачтено',fakty:[fact],session_id:'a',t_podtverzhdeniya:1});
  await ctx.ddsFinish();assert(get('#view').innerHTML.includes('не зачтено'));
  assert(get('#view').innerHTML.includes('</div></div>'));assert(!get('#view').innerHTML.includes('}/div>'));
  const target=node();
  ctx.api=async()=>({n:0,passed:0,legacy_excluded:0,bkt_note:'baseline',skills:[{name:'Адрес',n:0}],plan:[],
    forecasts:{n:0,brier:null,accuracy:null,calibration:[],warning:'small sample'},attempts:[]});
  await ctx.viewInsights(target);assert(target.innerHTML.includes('не проверен'));assert(!target.textContent.startsWith('Ошибка'));
  ctx.api=async()=>({session:{scenario_id:'a',started:1},itog:{ball:0,verdikt:'не зачтено',fakty:[fact]},
    events:[{seq:1,event_id:'a',ts:2,type:'dialog',payload:{text:'<img src=x onerror=alert(1)>'}}],dialog:[]});
  await ctx.detailedRazbor('a');assert(!get('#modalBox').innerHTML.includes('<img src=x'));
  console.log('Frontend smoke: 5 scenarios passed (fake DOM, no audio/browser)');
})().catch(e=>{console.error(e);process.exitCode=1;});
