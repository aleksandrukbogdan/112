"""Resume, explainable semantic observations, instructor events and isolated replay."""
import copy
import hashlib
import json
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import db, access, ai_text, session_store as store, dds, dds_rules, facts, config as C
from .auth import current_user, role

router=APIRouter(prefix='/api/training',tags=['training'])


def public(s):
    kind=s.get('kind','ops112')
    answer={'session_id':s['id'],'kind':kind,'started':s['nachalo'],'server_time':time.time(),'revision':s.get('revision',0),
        'kartochka':dds._publichnaya(s['kartochka']),'field_versions':s.get('field_versions',{}),'helper':s.get('helper',{}),
        'lesson_id':s.get('lesson_id'),'mode':s.get('mode','practice'),'parent_session':s.get('parent_session'),
        'notices':s.get('visible_notices',[]),'helper_drafts':s.get('helper_drafts',{})}
    if kind=='dds':
        answer.update(zapis=s['zapis'],kontakty=s['kontakty'],status_history=s['statusy'],calls=s['zvonki'],
            statusy=[x for x in s.get('_rules',{}).get('dds',C.DDS)['statusy'] if not dds_rules.medical(s) or x['kod'] not in ('ne_prinyata','otkaz')],
            exercise_profile=s.get('exercise_profile','standard'),known_phase=dds.known_phase(s),
            podtverzhdenie_sec=s.get('_rules',{}).get('dds',C.DDS)['podtverzhdenie_sec'])
    else:
        answer.update(dialog=s['dialog'],normativ_sec=s.get('_rules',{}).get('norm',C.NORM)['kartochka_sec'],tayming_sec=s['tayming_sec'],
            scenario={k:s['bilet'].get(k) for k in ('id','situaciya','slozhnost','trebuet_utochneniya')})
    return answer


@router.get('/{sid}/resume')
def resume(sid:str,u=Depends(current_user)):
    return public(store.load(sid,u))


@router.get('/{sid}/observations')
def observations(sid:str,u=Depends(current_user)):
    s=store.load(sid,u)
    return {'notices':s.get('visible_notices',[]),'revision':s.get('revision',0)}


class Intervention(BaseModel):
    type:str
    text:str=Field(min_length=5,max_length=2000)


@router.post('/{sid}/intervene')
@store.command({},kind='intervention')
def intervene(sid:str,b:Intervention,u=Depends(role('teacher'))):
    access.session(u,sid);s=store.load(sid)
    if b.type not in ('notice','duplicate','ne_kompetenciya','otkaz','pribytie','raboty','zaversheno'):raise HTTPException(422,'Неизвестная вводная')
    if b.type!='notice' and s.get('kind')!='dds':raise HTTPException(422,'Вводная состояния применима к ДДС')
    phase=dds._faza(s) if s.get('kind')=='dds' else None
    valid={'pribytie':('vyezd',),'raboty':('pribytie',),'zaversheno':('raboty',)}
    if b.type in valid and phase not in valid[b.type]:raise HTTPException(409,'Недопустимый переход состояния бригады')
    if b.type in ('duplicate','ne_kompetenciya','otkaz') and dds_rules.medical(s):raise HTTPException(422,'Отказные ветки неприменимы к 103')
    if b.type=='otkaz' and (not s.get('t_vyezd') or phase in ('raboty','zaversheno')):raise HTTPException(409,'Отказ возможен после начала реагирования до работ')
    now=time.time();event={'id':uuid.uuid4().hex,'type':b.type,'text':b.text,'actor':u['id'],'ts':now}
    s.setdefault('interventions',[]).append(event);s.setdefault('visible_notices',[]).append(event)
    if b.type in valid:s['world_phase']=b.type
    if b.type!='notice':dds._osnovanie(s,b.type,'вводная преподавателя')
    if s.get('kind','ops112')=='ops112':s['dialog'].append({'id':event['id'],'kto':'teacher','tekst':b.text,'t_ms':int((now-s['nachalo'])*1000)})
    store.event(s,'teacher_intervention',event,u['id']);store.persist(s);db.audit(u,'teacher_intervention',{'session':sid,**event})
    return {'ok':True,'event':event}


class Replay(BaseModel):
    seq:int=Field(ge=0)


def shift_clock(s,delta):
    # Shift only timestamp fields, never durations, values or birth dates inside text.
    def walk(v):
        if isinstance(v,dict):
            for key,value in list(v.items()):
                if key in ('t','ts','nachalo','konec','t_vyezd','sozdana','source_time') and isinstance(value,(int,float)) and value>1000000000:
                    v[key]=value+delta
                else:walk(value)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(s)


@router.post('/{sid}/replay')
@store.command({},kind='replay')
def replay(sid:str,b:Replay,u=Depends(role('trainee','teacher'))):
    original=access.session(u,sid,completed=True)
    if u['role']=='trainee' and db.q1("SELECT id FROM lessons WHERE group_id=? AND state='running'",(u.get('group_id'),)):
        raise HTTPException(403,'Повтор доступен после завершения текущего занятия')
    row=db.q1('SELECT * FROM session_snapshots WHERE session_id=? AND seq<=? ORDER BY seq DESC LIMIT 1',(sid,b.seq))
    if not row:raise HTTPException(409,'Для этой архивной точки нет снимка; выберите более позднюю точку')
    s=json.loads(row['data']);new=uuid.uuid4().hex;now=time.time();shift_clock(s,now-row['ts'])
    s.update(id=new,user_id=u['id'],parent_session=sid,replay_from_seq=row['seq'],mode='replay',lesson_id=None,assignment_id=None)
    s['helper']={'allowed':True,'enabled':False,'exposed':False};s['lesson_policy']={'helper_toggle':True}
    s.pop('pending_reply',None);s.pop('interrupted',None);s.pop('ai_analysis',None)
    db.ex('INSERT INTO sessions(id,user_id,scenario_id,kind,started,state,data) VALUES(?,?,?,?,?,?,?)',
          (new,u['id'],original['scenario_id'],original['kind'],s['nachalo'],'live',store.dumps(s)))
    store.event(s,'replay_started',{'parent':sid,'seq':row['seq'],'modelled_alternative':True});store.snapshot(s)
    return public(s)


def analysis_input(s):
    # This is the assessor view. It never goes to the helper adapter or a live trainee response.
    return {'messages':[{'id':f'dialog:{i}','text':m['tekst']} for i,m in enumerate(s.get('dialog',[]))]
        + [{'id':f"{c['id']}:{i}",'text':m['tekst']} for c in s.get('zvonki',[]) for i,m in enumerate(c['dialog'])],
        'comments':[{'id':x.get('id',str(i)),'text':x.get('kommentariy',''),'status':x['kod']} for i,x in enumerate(s.get('statusy',[]))],
        'description':s['kartochka'].get('opisanie','')}


@router.post('/{sid}/analyze')
async def analyze(sid:str,u=Depends(current_user)):
    row=access.session(u,sid,completed=True)
    s=json.loads(row['data']);payload=analysis_input(s);signature=hashlib.sha256(store.dumps(payload).encode()).hexdigest()
    data,diagnostic=await ai_text.structured(
        'Проверь текст учебного диалога и комментариев диспетчера: содержательность, противоречия, грамматика. '
        'Не ставь балл. Верни {"observations":[{"source_id":"id сообщения или комментария", "quote":"точная цитата",'
        '"category":"grammar|contradiction|comment", "explanation":"объяснение", "suggestion":"исправление"}]}. '
        'Пустой список допустим. Не превращай отсутствие сведений в отсутствие пострадавших.',payload)
    sources={x['id']:x['text'] for x in payload['messages']+payload['comments']};sources['description']=payload['description'];valid=[]
    if data and isinstance(data.get('observations'),list):
        for x in data['observations'][:30]:
            if not isinstance(x,dict):continue
            if x.get('category') not in ('grammar','contradiction','comment'):continue
            if not isinstance(x.get('quote'),str) or not x['quote'].strip() or x['quote'] not in sources.get(x.get('source_id'),''):continue
            if not isinstance(x.get('explanation'),str):continue
            valid.append({k:str(x.get(k,''))[:2000] for k in ('source_id','quote','category','explanation','suggestion')})
    with db.tx():
        row=db.lock_row('sessions','id',sid);access.session(u,sid,completed=True)
        current=json.loads(row['data'])
        if hashlib.sha256(store.dumps(analysis_input(current)).encode()).hexdigest()!=signature:
            raise HTTPException(409,'Диалог изменился во время анализа. Повторите анализ после последнего действия.')
        current['ai_analysis']={'diagnostic':diagnostic,'observations':valid,'source_hash':signature,'ts':time.time(),
            'requires_teacher_review':bool(valid),'automatic_grade_authority':False}
        store.event(current,'semantic_analysis',{'status':diagnostic['status'],'observations':len(valid)})
        store.persist(current)
    # No semantic advice during an exam; it is shown in the completed debrief.
    return {'status':diagnostic['status'],'saved':True}

class ReviewPreview(BaseModel):
    changes:dict
    reason:str='Предпросмотр'

@router.post('/{sid}/review-preview')
def review_preview(sid:str,b:ReviewPreview,u=Depends(role('teacher'))):
    from . import insights,domain as D,quality as Q
    row=access.session(u,sid,completed=True);it=insights.effective(row);codes={f['kod'] for f in it['fakty']}
    if set(b.changes)-codes or any(v is not None and type(v) is not bool for v in b.changes.values()):raise HTTPException(422,'Неизвестный критерий или решение')
    fs=[D.Fakt(f['kod'],f['nazvanie'],b.changes.get(f['kod'],f['proyden']),f['ves'],f['istochnik'],f['detali']) for f in it['fakty']]
    return Q.result(fs,row['kind'],critical=it.get('critical_codes'),version=it.get('rubric_version'),threshold=it.get('pass_threshold',70))

@router.post('/{sid}/stop')
@store.command({},kind='stop')
def stop_attempt(sid:str,u=Depends(current_user)):
    s=store.load(sid,u)
    if s.get('lesson_id'):raise HTTPException(403,'Остановить попытку занятия может преподаватель')
    s['interrupted']={'reason':'Прервано обучаемым','ts':time.time(),'actor':u['id']}
    store.event(s,'attempt_stopped',s['interrupted']);store.persist(s)
    db.ex("UPDATE sessions SET state='stopped',finished=? WHERE id=?",(time.time(),sid))
    return {'ok':True,'state':'stopped'}
