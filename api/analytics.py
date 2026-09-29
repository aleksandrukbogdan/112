"""One authorized, immutable data snapshot for charts, tables, PDF and Excel."""
import io
import json
import math
import statistics
import time
import uuid
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from . import db, access, insights, curriculum, quality as Q, session_store as store, config as C
from .auth import current_user, role

router=APIRouter(prefix='/api/analysis',tags=['analytics'])


def quantile(values,q):
    if not values:return None
    values=sorted(values);pos=(len(values)-1)*q;left=int(pos);part=pos-left
    return round(values[left]*(1-part)+values[min(left+1,len(values)-1)]*part,2)


def selected(u,filters):
    gids=access.groups(u)
    if filters.get('uid'):access.learner(u,filters['uid'])
    if filters.get('group_id'):access.group(u,filters['group_id'])
    sql="SELECT s.*,u.name,u.group_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.role='trainee'"
    args=[]
    if u['role']=='trainee':sql+=' AND s.user_id=?';args.append(u['id'])
    if filters.get('uid'):sql+=' AND s.user_id=?';args.append(filters['uid'])
    if filters.get('group_id'):sql+=' AND u.group_id=?';args.append(filters['group_id'])
    if filters.get('kind') in ('dds','ops112'):sql+=' AND s.kind=?';args.append(filters['kind'])
    if filters.get('from_ts'):sql+=' AND s.started>=?';args.append(filters['from_ts'])
    if filters.get('to_ts'):sql+=' AND s.started<=?';args.append(filters['to_ts'])
    rows=db.q(sql+' ORDER BY s.started,s.id',args);out=[];excluded=defaultdict(int)
    # Bulk latest reviews, not N review queries for N attempts.
    reviews={}
    for r in db.q('SELECT session_id,data FROM evidence_reviews ORDER BY revision'):
        reviews[r['session_id']]=json.loads(r['data'])
    for r in rows:
        if u['role']=='teacher' and r['group_id'] not in gids:continue
        s=json.loads(r['data']);r['state_data']=s
        r['result']=reviews.get(r['id'],json.loads(r['itog']) if r['itog'] else {})
        independent=store.independent(s);r['independent']=independent
        r['assisted']=bool(s.get('helper',{}).get('exposed'));r['replay']=bool(s.get('parent_session'))
        checks=[('rubric',r['result'].get('rubric_version',s.get('versions',{}).get('rubric','legacy'))),
                ('difficulty',s.get('bilet',{}).get('slozhnost')),('service',s.get('kartochka',{}).get('moya_sluzhba','')),
                ('mode',s.get('mode','practice'))]
        reason=next((k for k,v in checks if filters.get(k) not in (None,'','all') and str(filters[k])!=str(v)),None)
        if reason:excluded[reason]+=1;continue
        helper=filters.get('helper','all')
        if helper=='independent' and not independent:excluded['assistance_or_replay']+=1;continue
        if helper=='assisted' and not r['assisted']:excluded['without_assistance']+=1;continue
        out.append(r)
    return out,dict(excluded)


def aggregate(rows,filters):
    done=[r for r in rows if r['state']=='done' and r['result'].get('fakty')]
    attempts=[];skills=defaultdict(lambda:{'n':0,'success':0,'unknown':0,'ids':[],'failed_ids':[]})
    errors=defaultdict(lambda:{'n':0,'errors':0,'ids':[],'critical':False})
    heat=defaultdict(lambda:{'n':0,'errors':0,'ids':[]});time_sets=defaultdict(list)
    for r in done:
        s=r['state_data'];it=r['result'];critical=it.get('critical_errors',[])
        a={'id':r['id'],'user_id':r['user_id'],'name':r['name'],'kind':r['kind'],'scenario':r['scenario_id'],
            'started':r['started'],'finished':r['finished'],'ball':it['ball'],'passed':it.get('passed') is True,
            'critical_errors':critical,'t_sec':r['t_sec'],'assisted':r['assisted'],'independent':r['independent'],
            'replay':r['replay'],'mode':s.get('mode','practice'),'difficulty':s.get('bilet',{}).get('slozhnost'),
            'rubric':it.get('rubric_version','legacy'),'service':s.get('kartochka',{}).get('moya_sluzhba',''),
            'verdict':it.get('verdikt','не зачтено'),'pass_threshold':it.get('pass_threshold',70),'pending_criteria':it.get('pending_criteria',[]),'reviewed':bool(it.get('review')),'manual_ball':r.get('override_ball'),'facts':[]}
        for f in it['fakty']:
            code=f['kod'];skill=Q.FAKT_SKILL.get(code,code);value=f.get('proyden')
            a['facts'].append({'code':code,'name':f['nazvanie'],'value':value,'skill':skill,'critical':code in it.get('critical_codes',critical)})
            # Program names are part of the key; DDS/112 observations never silently collapse.
            rubric=a['rubric']
            key=r['kind']+':'+rubric+':'+skill
            bucket=skills[key];bucket['name']=insights.LABELS.get(skill,skill);bucket['code']=skill;bucket['kind']=r['kind'];bucket['rubric']=rubric
            if value is None:bucket['unknown']+=1;continue
            bucket['n']+=1;bucket['success']+=int(value);bucket['ids'].append(r['id'])
            if not value:bucket['failed_ids'].append(r['id'])
            e=errors[r['kind']+':'+rubric+':'+code];e['name']=f['nazvanie'];e['kind']=r['kind'];e['rubric']=rubric;e['n']+=1;e['errors']+=int(not value)
            e['critical']=e['critical'] or code in it.get('critical_codes',critical)
            if not value:e['ids'].append(r['id'])
            for axis,label in [('learner',r['name']),('incident',s.get('bilet',{}).get('gruppa','—'))]:
                entity=str(r['user_id']) if axis=='learner' else label
                cell=heat[(axis,entity,key)];cell.update(axis=axis,entity=entity,label=label,skill=skill,skill_name=bucket['name'],kind=r['kind'],rubric=rubric)
                cell['n']+=1;cell['errors']+=int(not value);cell['ids'].append(r['id'])
            if code in ('podtverzhdenie','normativ'):
                d=f.get('detali',{});a['reaction_sec']=d.get('fakt_sec');a['limit_sec']=d.get('normativ_sec')
                time_sets['ack' if r['kind']=='dds' else 'card'].append({'id':r['id'],'value':d.get('fakt_sec'),'limit':d.get('normativ_sec'),'kind':r['kind']})
            if code=='svoevremenno':
                for d in f.get('detali',{}).get('delays',[]):time_sets['status'].append({'id':r['id'],'value':d['delay_sec'],'limit':d['limit_sec'],'status':d['status']})
        time_sets['process'].append({'id':r['id'],'value':r['t_sec'],'limit':None,'kind':r['kind']})
        attempts.append(a)
    skill_out=[]
    for key,b in skills.items():
        n=b['n'];skill_out.append({**b,'key':key,'rate':b['success']/n if n else None,'interval95':insights.interval(b['success'],n),
                                  'ids':sorted(set(b['ids'])),'failed_ids':sorted(set(b['failed_ids']))})
    error_out=[{**e,'key':k,'rate':e['errors']/e['n'] if e['n'] else None,'ids':sorted(set(e['ids']))} for k,e in errors.items()]
    time_out={}
    for key,points in time_sets.items():
        values=[x['value'] for x in points if x['value'] is not None]
        time_out[key]={'points':points,'n':len(values),'missing':len(points)-len(values),'median':quantile(values,.5),
                       'p95':quantile(values,.95) if len(values)>=20 else None,'p95_minimum_n':20,'unit':'seconds'}
    n=len(attempts)
    forecast=insights.forecast_metrics({a['id'] for a in attempts})
    # Report calibration separately by method/version.
    forecast['validation_status']='descriptive_only';forecast['baseline_brier']=None
    obs=[]
    for row in db.q("SELECT subject,data,fakt,created FROM forecasts WHERE kind='call' AND fakt IS NOT NULL ORDER BY created,id"):
        if row['subject'] not in {a['id'] for a in attempts}:continue
        p=json.loads(row['data']);y=json.loads(row['fakt'])
        if p.get('t_prognoza_sec',0)<p.get('proverka_na_sec',75):obs.append((row['subject'],p,y))
    bymodel=defaultdict(dict)
    for sid,p,y in obs:
        model=p.get('model_version',p.get('method','legacy'))
        bymodel[str(model)].setdefault(sid,(float(p['veroyatnost']),int(bool(y['uspel']))))
    forecast['by_model']=[]
    for model,values in bymodel.items():
        values=list(values.values());nm=len(values);mean=sum(y for p,y in values)/nm
        bins=[]
        for i in range(5):
            b=[(p,y) for p,y in values if min(4,int(p*5))==i]
            if b:bins.append({'predicted':sum(p for p,y in b)/len(b),'observed':sum(y for p,y in b)/len(b),'n':len(b)})
        forecast['by_model'].append({'model':model,'n':nm,'brier':sum((p-y)**2 for p,y in values)/nm,
            'baseline_brier':sum((mean-y)**2 for p,y in values)/nm,'baseline_label':'Постоянная частота на этой выборке; описательное сравнение',
            'calibration':bins,'status':'insufficient_data' if nm<30 else 'descriptive_only'})
    ids={a['id'] for a in attempts};proposals=[]
    for r in db.q('SELECT * FROM helper_proposals'):
        if r['session_id'] in ids:proposals.append(r)
    reviews=[json.loads(x['reviewed']) for x in proposals if x['reviewed']]
    dispositions={k:sum(x['state']==k for x in proposals) for k in ('shown','accept','edit','reject')}
    corrected_wrong=sum(1 for x in proposals if x['reviewed'] and not json.loads(x['reviewed'])['correct'] and x['state'] in ('edit','reject'))
    wrong=sum(not x['correct'] for x in reviews)
    helper={'shown':len(proposals),'reviewed':len(reviews),'correct':sum(x['correct'] for x in reviews),'wrong':wrong,
            'correct_rate':sum(x['correct'] for x in reviews)/len(reviews) if reviews else None,'decisions':dispositions,
            'corrected_wrong':corrected_wrong,'detection_rate':corrected_wrong/wrong if wrong else None,
            'note':'Принятие предложения не является подтверждением его правильности; правильность размечает преподаватель'}
    modes=[]
    for key,predicate in [('independent',lambda a:a['independent']),('assisted',lambda a:a['assisted'])]:
        aa=[a for a in attempts if predicate(a)]
        modes.append({'mode':key,'n':len(aa),'median_sec':quantile([a['t_sec'] for a in aa if a['t_sec'] is not None],.5),
            'critical_rate':sum(bool(a['critical_errors']) for a in aa)/len(aa) if aa else None,'ids':[a['id'] for a in aa]})
    matching=defaultdict(dict)
    for a in attempts:
        if a['replay'] or not (a['independent'] or a['assisted']):continue
        key=(a['user_id'],a['scenario'],a['kind'],a['difficulty'],a['rubric'])
        matching[key]['assisted' if a['assisted'] else 'independent']=a
    pairs=[{'independent':v['independent']['id'],'assisted':v['assisted']['id'],
            'delta_sec':v['assisted']['t_sec']-v['independent']['t_sec']} for v in matching.values()
           if set(v)=={'independent','assisted'} and all(x['t_sec'] is not None for x in v.values())]
    return {'schema_version':'analytics-2','filters':filters,'created':time.time(),'timezone':'UTC (displayed in browser timezone)',
        'n':n,'unit':'completed_attempt; criterion denominators shown separately',
        'summary':{'completed':n,'passed':sum(a['passed'] for a in attempts),'pass_rate':sum(a['passed'] for a in attempts)/n if n else None,
                   'mean_score':round(sum(a['ball'] for a in attempts)/n,1) if n else None,'critical_attempts':sum(bool(a['critical_errors']) for a in attempts),
                   'stopped':sum(r['state']=='stopped' for r in rows),'live':sum(r['state']=='live' for r in rows)},
        'attempts':attempts,'skills':skill_out,'errors':sorted(error_out,key=lambda x:-x['errors']),
        'heatmap':[{**v,'rate':v['errors']/v['n'],'ids':sorted(set(v['ids']))} for v in heat.values()],
        'timings':time_out,'forecast':forecast,'helper':helper,'comparison':{'groups':modes,'pairs':pairs,'paired_n':len(pairs),
            'note':'Наблюдаемое сравнение; знакомство со сценарием и порядок прохождения могут влиять на результат'},
        'warnings':['Малые и зависимые выборки не доказывают освоение навыка или эффект помощника.']}


@router.get('/snapshot')
def snapshot(kind:str='dds',group_id:int|None=None,uid:int|None=None,helper:str='all',difficulty:int|None=None,
             rubric:str=Q.RUBRIC_VERSION,mode:str='all',service:str='',from_ts:float|None=None,to_ts:float|None=None,u=Depends(current_user)):
    if kind not in ('dds','ops112','all') or helper not in ('all','independent','assisted'):raise HTTPException(422,'Неизвестный фильтр')
    if difficulty is not None and not 1<=difficulty<=5:raise HTTPException(422,'Сложность 1–5')
    if from_ts and to_ts and from_ts>to_ts:raise HTTPException(422,'Начало периода позже окончания')
    filters=dict(kind=kind,group_id=group_id,uid=uid,helper=helper,difficulty=difficulty,rubric=rubric,mode=mode,service=service,from_ts=from_ts,to_ts=to_ts)
    with db.tx():
        rows,excluded=selected(u,filters);data=aggregate(rows,filters);data['excluded']=excluded
        target=uid or (u['id'] if u['role']=='trainee' else None)
        if target and kind in Q.PROGRAMS:
            data['recommendations']=curriculum.recommendations(target,kind,[x for x in data['skills'] if x['kind']==kind])
        else:data['recommendations']=[]
        data['snapshot_id']=uuid.uuid4().hex
        db.ex('INSERT INTO analytics_snapshots(id,owner_id,data,created) VALUES(?,?,?,?)',(data['snapshot_id'],u['id'],store.dumps(data),time.time()))
        # Snapshots only; attempts and security audit are not purged.
        db.ex('DELETE FROM analytics_snapshots WHERE created<?',(time.time()-7*86400,))
    return data


def snapshot_access(sid,u):
    row=db.q1('SELECT * FROM analytics_snapshots WHERE id=? AND owner_id=?',(sid,u['id']))
    if not row:raise HTTPException(404,'Снимок отчёта не найден; обновите аналитику')
    data=json.loads(row['data']);filters=data['filters']
    if filters.get('group_id'):access.group(u,filters['group_id'])
    if filters.get('uid'):access.learner(u,filters['uid'])
    for uid in {x['user_id'] for x in data['attempts']}:access.learner(u,uid)
    return data


@router.get('/export/{sid}.{fmt}')
def export(sid:str,fmt:str,token:str=''):
    from .main import _auth_q
    from . import report_analytics
    u=_auth_q(token);data=snapshot_access(sid,u)
    if fmt=='xlsx':body=report_analytics.excel(data);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif fmt=='pdf':body=report_analytics.pdf(data);mime='application/pdf'
    elif fmt=='json':body=store.dumps(data).encode();mime='application/json'
    else:raise HTTPException(422,'Формат PDF, XLSX или JSON')
    return Response(body,media_type=mime,headers={'Content-Disposition':f'attachment; filename="analytics-{sid[:8]}.{fmt}"'})


@router.get('/live')
def monitor(group_id:int|None=None,u=Depends(role('teacher'))):
    if group_id:access.group(u,group_id)
    gids=access.groups(u);rows=[];now=time.time()
    for r in db.q("SELECT s.*,u.name,u.group_id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.started>?",(now-86400,)):
        if r['group_id'] not in gids or (group_id and r['group_id']!=group_id):continue
        s=json.loads(r['data']);r['s']=s;rows.append(r)
    cards=[]
    for r in rows:
        if r['state']!='live':continue
        s=r['s'];ack=next((x for x in s.get('statusy',[]) if x['kod'] in ('prinyata','ne_prinyata')),None)
        limit=s.get('_rules',{}).get('dds',C.DDS)['podtverzhdenie_sec'] if r['kind']=='dds' else s.get('tayming_sec',75)
        cards.append({'id':r['id'],'name':r['name'],'kind':r['kind'],'elapsed':round(now-r['started'],1),'acknowledged':bool(ack),
            'overdue':not ack and now-r['started']>limit,'limit':limit,'lesson_id':s.get('lesson_id'),
            'status':s.get('statusy',[])[-1]['nazvanie'] if s.get('statusy') else 'Открыта',
            'assisted':bool(s.get('helper',{}).get('exposed')),'interventions':len(s.get('interventions',[])),
            'review_required':bool(s.get('ai_analysis',{}).get('requires_teacher_review')),
            'technical_issue':s.get('ai_analysis',{}).get('diagnostic',{}).get('status')=='unavailable'})
    points=[];start=max(now-3600,min((r['started'] for r in rows),default=now))
    for i in range(31):
        ts=start+(now-start)*i/30;opened=waiting=overdue=0
        for r in rows:
            if r['started']>ts or (r['finished'] and r['finished']<=ts):continue
            opened+=1;s=r['s'];ack=next((x for x in s.get('statusy',[]) if x['kod'] in ('prinyata','ne_prinyata') and x['t']<=ts),None)
            if not ack:
                waiting+=1;limit=s.get('_rules',{}).get('dds',C.DDS)['podtverzhdenie_sec'] if r['kind']=='dds' else s.get('tayming_sec',75)
                if ts-r['started']>limit:overdue+=1
        points.append({'ts':ts,'open':opened,'waiting':waiting,'overdue':overdue})
    return {'server_time':now,'cards':sorted(cards,key=lambda x:(not x['overdue'],-x['elapsed'])),'load':points,'scheduler':db.setting('scheduler_error')}
