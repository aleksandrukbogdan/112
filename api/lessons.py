"""Durable class lifecycle and card delivery. Server timestamps define all deadlines."""
import contextvars
import copy
import json
import threading
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import db, access, curriculum, session_store as store, config as C
from .auth import current_user, role

router=APIRouter(prefix='/api/lessons',tags=['lessons'])
dispatching=contextvars.ContextVar('lesson_dispatch',default=False)
dispatch_item=contextvars.ContextVar('lesson_item',default=None)


class LessonSpec(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    group_id:int
    kind:str='dds'
    scenario_ids:list[str]=Field(default_factory=list,max_length=200)
    service:str|None=None
    source:str='system'
    student_session_ids:list[str]=Field(default_factory=list,max_length=200)
    interval_sec:int=Field(default=60,ge=5,le=3600)
    max_active:int=Field(default=3,ge=1,le=20)
    mode:str='practice'
    helper_allowed:bool=False
    helper_toggle:bool=True
    ack_sec:int=Field(default=30,ge=5,le=3600)
    status_sec:int=Field(default=60,ge=5,le=3600)
    card_sec:int=Field(default=75,ge=5,le=3600)
    grading:dict=Field(default_factory=lambda:{'threshold':70,'weights':{}})
    material_ids:list[str]=Field(default_factory=list,max_length=30)


def validate_policy(p):
    if p['kind'] not in ('ops112','dds') or p['source'] not in ('system','student','mixed') or p['mode'] not in ('practice','exam','assisted_exam'):
        raise HTTPException(422,'Недопустимый режим или источник')
    if p['mode']=='exam' and p['helper_allowed']:
        raise HTTPException(422,'Самостоятельный экзамен проводится без помощника')
    if p['source']!='system' and p['kind']!='dds':
        raise HTTPException(422,'Заполненные карточки используются в режиме ДДС')
    g=p['grading']
    if type(g.get('threshold',70)) is not int or not 0<=g.get('threshold',70)<=100:
        raise HTTPException(422,'Порог оценки от 0 до 100')
    allowed={'adres','tip','sluzhby','normativ','uchebny_tayming','polnota','rech','grammatika','podtverzhdenie','peredacha','po_faktu','etapy','svoevremenno','zaversheno','kommentarii','proverka'}
    if any(k not in allowed or type(v) is not int or not 0<=v<=10 for k,v in g.get('weights',{}).items()):
        raise HTTPException(422,'Вес известного критерия: целое число 0–10')
    if 'critical' in g and (not isinstance(g['critical'],list) or set(g['critical'])-allowed):
        raise HTTPException(422,'Неизвестные критические критерии')


@router.post('')
def create(b:LessonSpec,u=Depends(role('teacher'))):
    access.group(u,b.group_id);p=b.model_dump();validate_policy(p)
    if not p['scenario_ids'] and not p['student_session_ids']:raise HTTPException(422,'Выберите сценарии или карточки учеников')
    items=[]
    if p['source'] in ('system','mixed'):
        for sid in p['scenario_ids']:
            row=db.q1("SELECT * FROM scenarios WHERE id=? AND status='published'",(sid,))
            if not row:raise HTTPException(422,'Сценарий не опубликован: '+sid)
            sc=curriculum.enrich(json.loads(row['data']))
            if p['service'] and p['service'] not in sc['service_profiles'] and not sc.get('outside_competence'):
                raise HTTPException(422,'Сценарий не соответствует профилю службы: '+sid)
            items.append({'scenario_id':sid,'data':sc,'source':'system'})
    if p['source'] in ('student','mixed'):
        for sid in p['student_session_ids']:
            row=access.session(u,sid,completed=True)
            if row['kind']!='ops112':raise HTTPException(422,'Источник должен быть заполненной карточкой 112')
            s=json.loads(row['data']);k=s['kartochka'];sc=copy.deepcopy(s['bilet'])
            sc.update(adres_etalon=k.get('adres_polny',''),adres_vidimy=k.get('adres_polny',''),situaciya=k.get('opisanie',''),
                zayavitel={'fio':k.get('zayavitel_fio',''),'telefon':k.get('zayavitel_telefon','')},victims=k.get('postradavshie','neizvestno'),trebuet_utochneniya=False)
            if any(not sc[x] for x in ('adres_etalon','situaciya')):raise HTTPException(422,'Исходная карточка неполна: '+sid)
            items.append({'scenario_id':row['scenario_id'],'data':sc,'source':'student','source_session':sid})
    if not items:raise HTTPException(422,'Для выбранного источника нет карточек')
    for mid in p['material_ids']:
        row=db.q1('SELECT owner_id FROM materials WHERE id=?',(mid,))
        if not row or row['owner_id']!=u['id']:raise HTTPException(403,'Материал не принадлежит преподавателю')
    p['items']=items; lid=uuid.uuid4().hex
    db.ex('INSERT INTO lessons(id,owner_id,group_id,state,created,config) VALUES(?,?,?,?,?,?)',(lid,u['id'],b.group_id,'draft',time.time(),store.dumps(p)))
    db.audit(u,'lesson_created',{'lesson':lid})
    return {'id':lid,'state':'draft'}


def get(lid,u,owner=False):
    row=db.q1('SELECT * FROM lessons WHERE id=?',(lid,))
    if not row:raise HTTPException(404,'Занятие не найдено')
    access.group(u,row['group_id'])
    if owner and row['owner_id']!=u['id']:raise HTTPException(403,'Управлять занятием может его преподаватель')
    return row


@router.get('')
def listing(u=Depends(current_user)):
    gids=access.groups(u);out=[]
    for row in db.q('SELECT * FROM lessons ORDER BY created DESC'):
        if row['group_id'] not in gids:continue
        p=json.loads(row['config']);p.pop('items',None)
        out.append({**row,'config':p})
    return out


@router.post('/{lid}/start')
def start(lid:str,u=Depends(role('teacher'))):
    get(lid,u,True)
    with db.tx():
        row=db.lock_row('lessons','id',lid)
        if row['state']=='running':return {'ok':True,'id':lid,'started':row['started']}
        if row['state']!='draft':raise HTTPException(409,'Завершённое занятие нельзя перезапустить')
        db.lock_row('groups','id',row['group_id'])
        if db.q1("SELECT id FROM lessons WHERE group_id=? AND state='running'",(row['group_id'],)):
            raise HTTPException(409,'У группы уже идёт занятие')
        now=time.time()
        db.ex("UPDATE lessons SET state='running',started=? WHERE id=?",(now,lid))
        for member in db.q("SELECT id FROM users WHERE group_id=? AND role='trainee' AND active=1",(row['group_id'],)):
            db.ex('INSERT INTO lesson_members(lesson_id,user_id,data) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                  (lid,member['id'],store.dumps({'next':0,'next_at':now})))
        db.audit(u,'lesson_started',{'lesson':lid})
    tick(lid)
    return {'ok':True,'id':lid,'started':now}


@router.post('/{lid}/stop')
def stop(lid:str,u=Depends(role('teacher'))):
    get(lid,u,True)
    from . import dds,main
    with db.tx():
        row=db.lock_row('lessons','id',lid)
        if row['state']=='stopped':return {'ok':True}
        now=time.time();db.ex("UPDATE lessons SET state='stopped',stopped=? WHERE id=?",(now,lid))
        for r in db.q("SELECT * FROM sessions WHERE state='live'"):
            s=json.loads(r['data'])
            if s.get('lesson_id')!=lid:continue
            db.lock_row('sessions','id',r['id']);s=store.load(r['id'])
            s['interrupted']={'reason':'Преподаватель завершил занятие','ts':now,'actor':u['id']}
            store.event(s,'lesson_stopped',s['interrupted'],u['id']);store.persist(s)
            db.ex("UPDATE sessions SET state='stopped',finished=? WHERE id=?",(now,s['id']))
            dds.LIVE.pop(s['id'],None);main.LIVE.pop(s['id'],None)
        db.audit(u,'lesson_stopped',{'lesson':lid})
    return {'ok':True}


def start_policy(u,lid,scenario,kind):
    if lid:
        row=db.q1("SELECT * FROM lessons WHERE id=? AND state='running'",(lid,))
        if not row or row['group_id']!=u.get('group_id'):raise HTTPException(403,'Нет доступа к действующему занятию')
        p=json.loads(row['config'])
        if p['kind']!=kind or scenario not in [x['scenario_id'] for x in p['items']]:raise HTTPException(403,'Сценарий не назначен на этом занятии')
        if not dispatching.get():raise HTTPException(403,'Карточки занятия выдаёт сервер автоматически')
        return {k:v for k,v in p.items() if k!='items'}
    if u['role']=='trainee' and db.q1("SELECT id FROM lessons WHERE group_id=? AND state='running'",(u.get('group_id'),)):
        raise HTTPException(403,'Во время занятия используйте назначенную очередь карточек')
    return {'mode':'practice','helper_allowed':True,'helper_toggle':True}


def tick(lid=None):
    from . import dds,main
    rows=db.q("SELECT id FROM lessons WHERE state='running'" + (' AND id=?' if lid else ''),(lid,) if lid else ())
    for item in rows:
        with db.tx():
            lesson=db.lock_row('lessons','id',item['id'])
            if lesson['state']!='running':continue
            p=json.loads(lesson['config']);now=time.time()
            members=db.q('SELECT * FROM lesson_members WHERE lesson_id=?',(lesson['id'],))
            for member in members:
                progress=json.loads(member['data'])
                if progress['next']>=len(p['items']) or progress['next_at']>now:continue
                active=sum(json.loads(x['data']).get('lesson_id')==lesson['id'] for x in db.q("SELECT data FROM sessions WHERE user_id=? AND state='live'",(member['user_id'],)))
                if active>=p['max_active']:continue
                u=db.q1('SELECT id,login,name,role,group_id,active FROM users WHERE id=?',(member['user_id'],))
                if not u or not u['active']:continue
                sc=p['items'][progress['next']]
                token=dispatching.set(True)
                item_token=dispatch_item.set(sc)
                # Internal delivery must not inherit an HTTP command idempotency key.
                ctx=store.request_command.set(None)
                try:
                    if p['kind']=='dds':
                        r=dds.start(dds.Start(scenario_id=sc['scenario_id'],sluzhba=p.get('service'),lesson_id=lesson['id']),u)
                        s=dds.LIVE[r['session_id']]
                    else:
                        r=main.start(main.Start(scenario_id=sc['scenario_id'],lesson_id=lesson['id']),u);s=main.LIVE[r['session_id']]
                        s['bilet']=copy.deepcopy(sc['data'])
                    s['source_kind']=sc['source'];s['source_session']=sc.get('source_session')
                    s['versions']['scenario']=store.content('scenario',s['bilet']);store.persist(s)
                    progress['next']+=1;progress['next_at']=now+p['interval_sec']
                    db.ex('UPDATE lesson_members SET data=? WHERE lesson_id=? AND user_id=?',(store.dumps(progress),lesson['id'],member['user_id']))
                finally:
                    dispatching.reset(token);dispatch_item.reset(item_token);store.request_command.reset(ctx)


def active(user):
    from . import dds
    out=[]
    for row in db.q("SELECT * FROM sessions WHERE user_id=? AND state='live' ORDER BY started",(user['id'],)):
        s=json.loads(row['data']);now=time.time();kind=row['kind']
        ack=next((x for x in s.get('statusy',[]) if x['kod'] in ('prinyata','ne_prinyata')),None)
        limit=s.get('_rules',{}).get('dds',C.DDS)['podtverzhdenie_sec'] if kind=='dds' else s.get('tayming_sec',75)
        out.append({'id':row['id'],'kind':kind,'scenario_id':row['scenario_id'],'started':row['started'],'elapsed':round(now-row['started'],1),
            'server_time':now,'limit_sec':limit,'acknowledged':bool(ack),'overdue':not ack and now-row['started']>limit,
            'lesson_id':s.get('lesson_id'),'number':s['kartochka'].get('nomer',row['id'][:8]),'address':s['kartochka'].get('adres',s['kartochka'].get('adres_polny','')),
            'status':s.get('statusy',[{'nazvanie':'Добавлена'}])[-1]['nazvanie'] if s.get('statusy') else 'Добавлена'})
    return out


@router.get('/queue/mine')
def queue(u=Depends(current_user)):
    return {'server_time':time.time(),'cards':active(u)}


def start_scheduler():
    def loop():
        while True:
            try:tick()
            except Exception as exc:
                # No secrets or model payloads in diagnostic logs.
                db.set_setting('scheduler_error',{'type':type(exc).__name__,'ts':time.time()})
            time.sleep(1)
    threading.Thread(target=loop,daemon=True,name='lesson-delivery').start()
