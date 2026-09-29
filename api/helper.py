"""Opt-in assistance with visible sources, explicit decisions and optimistic field versions."""
import copy
import json
import re
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import db, facts, quality as Q, session_store as store, ai_text, access
from .auth import current_user, role

router=APIRouter(prefix='/api/helper',tags=['helper'])


def allowed(s):
    h=s.get('helper',{})
    if not h.get('allowed') or not h.get('enabled') or s.get('mode')=='exam':
        raise HTTPException(403,'Помощник выключен или запрещён условиями занятия')


class Toggle(BaseModel):
    enabled:bool


@router.get('/{sid}')
def state(sid:str,u=Depends(current_user)):
    s=store.load(sid,u)
    return {**s.get('helper',{}),'field_versions':s.get('field_versions',{}),'revision':s.get('revision',0)}


@router.post('/{sid}/toggle')
@store.command({},kind='helper')
def toggle(sid:str,b:Toggle,u=Depends(current_user)):
    s=store.load(sid,u);h=s.setdefault('helper',{'allowed':False,'enabled':False,'exposed':False})
    if b.enabled and (not h['allowed'] or s.get('mode')=='exam'):raise HTTPException(403,'Помощь не разрешена преподавателем')
    if b.enabled and not s.get('lesson_policy',{}).get('helper_toggle',True):raise HTTPException(403,'Переключение закреплено преподавателем')
    h['enabled']=b.enabled
    store.event(s,'helper_toggled',{'enabled':b.enabled,'exposed':h['exposed']});store.persist(s)
    return h


def extract(messages,kind):
    found=[]
    for source in messages:
        t=source['tekst'];low=t.lower()
        def add(field,value,quote=None):
            found.append({'field':field,'value':value,'source_id':source['id'],'quote':quote or t,'status':'source_confirmed','engine':'rules-v2'})
        if kind=='dds':continue
        phone=re.search(r'(?<!\d)(?:\+7|8)?[\s(-]*9\d{2}[\s)-]*\d{3}[ -]*\d{2}[ -]*\d{2}(?!\d)',t)
        if phone:add('zayavitel_telefon',phone.group(0).strip(),phone.group(0))
        if re.search(r'\b(?:ул\.|улица|шоссе|проспект|мкад|переулок|бульвар|набережная|проезд|дом\s+\d)',low):
            value=re.sub(r'^(?:сейчас[.…\s]*|точнее это\s*|я же говорил:\s*)','',t,flags=re.I).strip().rstrip('.')
            add('adres_polny',value,value)
        v=facts.victims(t)
        if v:add('postradavshie',v)
        match=re.search(r'(?:меня зовут|я —|я -)\s*([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){0,2})',t)
        if match:add('zayavitel_fio',match.group(1),match.group(1))
        elif re.fullmatch(r'[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2}[.]?',t.strip()):add('zayavitel_fio',t.strip().rstrip('.'))
        if any(re.search(rx,t,re.I) for _,rx in facts.EVENTS):add('opisanie',t)
    return found


def validate_model(data,messages,kind):
    sources={m['id']:m for m in messages};out=[]
    fields={'adres_polny','opisanie','zayavitel_fio','zayavitel_telefon','postradavshie'} if kind=='ops112' else set()
    entries=data.get('proposals',[])
    if not isinstance(entries,list):return []
    for item in entries[:30]:
        if not isinstance(item,dict) or item.get('field') not in fields:continue
        source=sources.get(item.get('source_id'));quote=item.get('quote');value=item.get('value')
        if not source or not isinstance(quote,str) or not quote.strip() or quote not in source['tekst']:continue
        if item['field']=='postradavshie':
            if value not in ('net','est','neizvestno') or facts.victims(quote)!=value:continue
        elif not isinstance(value,str) or Q.normalized(value) not in Q.normalized(quote):continue
        out.append({**item,'engine':'llm-extractive-v1','status':'source_confirmed'})
    return out


@router.post('/{sid}/suggest')
async def suggest(sid:str,u=Depends(current_user)):
    with db.tx():
        db.lock_row('sessions','id',sid);s=store.load(sid,u);allowed(s)
        messages=facts.visible_messages(s);base=copy.deepcopy(s.get('field_versions',{}));kind=s.get('kind','ops112')
    started=time.monotonic();proposals=extract(messages,kind)
    model,diagnostic=await ai_text.structured(
        'Извлеки только явно названные значения из сообщений. Не достраивай адрес. '
        'Верни {"proposals":[{"field":"adres_polny|opisanie|zayavitel_fio|zayavitel_telefon|postradavshie",'
        '"value":"дословное значение или net/est/neizvestno","source_id":"id сообщения","quote":"точная цитата"}]}.',
        {'messages':messages},timeout=20) if kind=='ops112' else (None,{'status':'rules_only'})
    if model:proposals+=validate_model(model,messages,kind)
    if kind=='dds':
        # The already received information is an explicit source, never a future world phase.
        from .dds_rules import status_reason
        for code in ('prinyata','ne_prinyata','nachalo','pribytie','raboty','zaversheno','otkaz'):
            o=status_reason(s,code)
            if o and not any(x['kod']==code for x in s.get('statusy',[])):
                if code=='prinyata':continue  # Mere delivery does not need a disguised auto-acknowledgement.
                source=next((m for m in reversed(messages) if m.get('t',0)>=o['t']-1 and m.get('from') in ('brigada','rukovoditel','teacher')),None)
                if source:
                    proposals.append({'field':'status','value':code,'source_id':source['id'],'quote':source['tekst'],'engine':'received-state-v2','status':'source_confirmed'})
                    proposals.append({'field':'comment','value':source['tekst'],'source_id':source['id'],'quote':source['tekst'],'engine':'received-state-v2','status':'source_confirmed'})
    # Latest source per field wins, but conflict is visible and requires confirmation.
    source_order={m['id']:i for i,m in enumerate(messages)}
    proposals.sort(key=lambda x:source_order.get(x['source_id'],-1))
    selected={}
    for x in proposals:
        old=selected.get(x['field'])
        if old and old['value']!=x['value']:x['status']='conflict';x['previous_value']=old['value']
        selected[x['field']]=x
    with db.tx():
        db.lock_row('sessions','id',sid);current=store.load(sid,u);allowed(current)
        available={m['id']:m['tekst'] for m in facts.visible_messages(current)}
        result=[]
        for x in selected.values():
            if x['source_id'] not in available or x['quote'] not in available[x['source_id']]:continue
            field=x['field'];pid=uuid.uuid4().hex
            x.update(id=pid,base_version=base.get(field,0),source_time=next((m.get('t') for m in messages if m['id']==x['source_id']),None),
                     manual_value=current['kartochka'].get(field),needs_confirmation=True)
            if current.get('field_versions',{}).get(field,0)!=base.get(field,0):x['status']='stale'
            db.ex('INSERT INTO helper_proposals(id,session_id,field,base_version,data,state,created) VALUES(?,?,?,?,?,?,?)',
                  (pid,sid,field,base.get(field,0),store.dumps(x),'shown',time.time()))
            result.append(x)
        if result:current['helper']['exposed']=True
        store.event(current,'helper_shown',{'proposal_ids':[x['id'] for x in result],'diagnostic':diagnostic,'latency_ms':round((time.monotonic()-started)*1000)})
        store.persist(current)
        return {'proposals':result,'diagnostic':diagnostic,'message':'Предложения требуют вашей проверки' if result else 'Нет подтверждённых предложений. Продолжайте вручную.'}


class Decision(BaseModel):
    action:str
    value:object=None
    confirm_overwrite:bool=False
    expected_version:int


@router.post('/{sid}/proposals/{pid}')
@store.command({},kind='helper')
def decide(sid:str,pid:str,b:Decision,u=Depends(current_user)):
    s=store.load(sid,u);allowed(s)
    row=db.q1('SELECT * FROM helper_proposals WHERE id=? AND session_id=?',(pid,sid))
    if not row:raise HTTPException(404,'Предложение не найдено')
    if row['state']!='shown':return {'ok':True,'state':row['state'],'decision':json.loads(row['decision'])}
    if b.action not in ('accept','edit','reject'):raise HTTPException(422,'Неизвестное действие')
    proposal=json.loads(row['data']);field=row['field'];current_version=s.get('field_versions',{}).get(field,0)
    if b.action!='reject':
        if b.expected_version!=row['base_version'] or current_version!=row['base_version']:
            raise HTTPException(409,'Предложение устарело. Ручной ввод сохранён, запросите новое предложение.')
        if field not in ('status','comment') and s['kartochka'].get(field) not in (None,'',[]) and not b.confirm_overwrite:
            raise HTTPException(409,'Подтвердите замену уже введённого значения')
    value=proposal['value'] if b.action=='accept' else b.value
    if b.action!='reject' and field not in ('status','comment'):
        Q.validate_card({field:value});s['kartochka'][field]=value
        s.setdefault('field_provenance',{})[field]={'proposal_id':pid,'action':b.action,'source_id':proposal['source_id'],'value':value}
        if field=='adres_polny' and s.get('t_adres_ms') is None:s['t_adres_ms']=int((time.time()-s['nachalo'])*1000)
        if field in ('postradavshie','tip_kod'):
            from .main import _pereschitat
            _pereschitat(s)
    # DDS result is a draft. The status endpoint remains the only way to change a status.
    if b.action!='reject' and field in ('status','comment'):
        s.setdefault('helper_drafts',{})[field]=value
        s.setdefault('field_versions',{})[field]=current_version+1
    decision={'action':b.action,'value':value if b.action!='reject' else None,'ts':time.time(),'actor':u['id']}
    db.ex('UPDATE helper_proposals SET state=?,decision=? WHERE id=?',(b.action,store.dumps(decision),pid))
    store.event(s,'helper_decision',{'proposal_id':pid,'field':field,**decision});store.persist(s)
    return {'ok':True,'state':b.action,'field':field,'value':value,'card':{k:v for k,v in s['kartochka'].items() if not k.startswith('_')},
            'field_versions':s.get('field_versions',{}),'draft_only':field in ('status','comment')}


class Review(BaseModel):
    correct:bool
    reason:str=Field(min_length=5,max_length=2000)


@router.post('/{sid}/proposals/{pid}/review')
def review(sid:str,pid:str,b:Review,u=Depends(role('teacher'))):
    access.session(u,sid)
    row=db.q1('SELECT * FROM helper_proposals WHERE id=? AND session_id=?',(pid,sid))
    if not row:raise HTTPException(404,'Предложение не найдено')
    result={'correct':b.correct,'reason':b.reason,'actor':u['id'],'ts':time.time()}
    db.ex('UPDATE helper_proposals SET reviewed=? WHERE id=?',(store.dumps(result),pid))
    db.audit(u,'helper_review',{'session':sid,'proposal':pid,**result})
    return result
