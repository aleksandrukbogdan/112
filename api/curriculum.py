"""Scenario metadata, frozen lesson policies and explainable practice selection."""
import copy
import hashlib
import json
import re
import time
from . import config as C, db, quality as Q, facts


def skills(s, kind):
    explicit=s.get('target_skills',{}); explicit=explicit.get(kind,[]) if isinstance(explicit,dict) else explicit
    if explicit:return [x for x in explicit if x in Q.PROGRAMS[kind]]
    if kind=='dds':
        return ['podtverzhdenie','peredacha','statusy'] + (['rech'] if s.get('slozhnost',1)>=3 else [])
    result=['tip','polnota','tayming']
    if s.get('trebuet_utochneniya'):result.append('adres')
    if s.get('gruppa') in ('1','2','4','12','17'):result.append('sluzhby')
    if s.get('slozhnost',1)>=4:result.append('rech')
    return result


def validate(s):
    errors=[]
    for key in ('id','situaciya','adres_vidimy','adres_etalon','gruppa'):
        if not isinstance(s.get(key),str) or not s[key].strip():errors.append('Пустое поле: '+key)
    if type(s.get('slozhnost')) is not int or not 1<=s['slozhnost']<=5:errors.append('Сложность: целое число от 1 до 5')
    caller=s.get('zayavitel')
    if not isinstance(caller,dict):errors.append('Заявитель должен быть объектом');caller={}
    for key in ('fio','telefon'):
        if not isinstance(caller.get(key),str) or not caller[key].strip():errors.append('Нет контакта заявителя: '+key)
    if s.get('gruppa') not in C.load('tree'):errors.append('Неизвестная группа классификатора')
    if s.get('kod_etalon') and s['kod_etalon'] not in C.load('index'):errors.append('Неизвестный код классификатора')
    elif s.get('kod_etalon') and C.load('index')[s['kod_etalon']]['g']!=s.get('gruppa'):errors.append('Код и группа противоречат друг другу')
    profiles=s.get('service_profiles',[])
    if not isinstance(profiles,list) or not all(isinstance(v,str) for v in profiles):errors.append('Профили служб должны быть списком строк');profiles=[]
    if s.get('no_dispatch') and not any('103' in v for v in profiles):errors.append('Завершение без бригады задаётся только профилю 103')
    events=s.get('events',[])
    if not isinstance(events,list):errors.append('Вводные должны быть списком');events=[]
    for e in events:
        if not isinstance(e,dict) or e.get('type') not in ('notice','duplicate','ne_kompetenciya','otkaz','pribytie','raboty','zaversheno'):errors.append('Неизвестная вводная')
    return errors


def rules_for(s, base):
    settings=db.setting('rubric_policy',{})
    policy=s.get('lesson_policy',settings)
    out=copy.deepcopy(base)
    for key in ('ack_sec','status_sec','card_sec'):
        if key in policy:
            target,field={'ack_sec':('dds','podtverzhdenie_sec'),'status_sec':('dds','svoevremenno_sec'),'card_sec':('norm','kartochka_sec')}[key]
            out[target][field]=int(policy[key])
    out['grading']=copy.deepcopy(policy.get('grading',{'threshold':70,'weights':{}}))
    out['limits_source']='Настройки преподавателя, закреплённые при выдаче попытки'
    out['dds']['veroyatnost_oshibki']=.7 if s.get('exercise_profile')=='card_check' else 0
    return out


def enrich(s):
    s=copy.deepcopy(s)
    s['victims']=s.get('victims') or facts.victims(s['situaciya']) or 'neizvestno'
    s['target_skills']={k:skills(s,k) for k in ('ops112','dds')}
    s.setdefault('service_profiles',C.GRUPPA_SLUZHBY.get(s['gruppa'],['Служба 102']))
    s.setdefault('required_transmission',['adres','sut','telefon','postradavshie'])
    s.setdefault('source_ref',{'document':'S11','ticket':s.get('bilet'),'call':s.get('vyzov'),'transcription':'provided bank; semantic metadata v2'})
    return s


def migrate_bank():
    """Only unchanged supplied bank rows are replaced. Teacher edits and live snapshots survive."""
    hashes=C.load('baseline_bilety_hashes')
    report={'updated':[],'preserved_custom':[]}
    with db.tx():
        for item in C.load('bilety'):
            row=db.q1('SELECT * FROM scenarios WHERE id=?',(item['id'],))
            if not row:continue
            existing=json.loads(row['data'])
            oldhash=hashlib.sha256(json.dumps(existing,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            if existing==item:continue
            if row['source']=='bilet' and oldhash==hashes.get(item['id']):
                db.ex('UPDATE scenarios SET data=? WHERE id=?',(json.dumps(item,ensure_ascii=False),item['id']))
                report['updated'].append(item['id'])
            else:report['preserved_custom'].append(item['id'])
        db.set_setting('bank_upgrade_report',report)
    return report


def recommendations(uid, kind, observations):
    rows=db.q("SELECT * FROM sessions WHERE user_id=? AND kind=? AND state='done' ORDER BY finished DESC",(uid,kind))
    done={r['scenario_id'] for r in rows[:8]}
    independent=[]
    from .session_store import independent as is_independent
    from .insights import effective
    for row in rows:
        s=json.loads(row['data'])
        if is_independent(s) and effective(row).get('rubric_version')==Q.RUBRIC_VERSION:
            independent.append((s,effective(row)))
    recent=independent[:3]
    base=recent[0][0].get('bilet',{}).get('slozhnost',2) if recent else 2
    if len(recent)>=3 and all(r.get('passed') for _,r in recent):base+=1
    elif any(r.get('critical_errors') for _,r in recent):base-=1
    level=max(1,min(5,base))
    pool=[json.loads(r['data']) for r in db.q("SELECT data FROM scenarios WHERE status='published'")]
    out=[]
    for skill in sorted(observations,key=lambda x:(x['n']==0,x.get('rate') if x.get('rate') is not None else -1)):
        if skill['n'] and skill.get('rate',0)>=.85:continue
        candidates=[x for x in pool if skill['code'] in skills(x,kind)]
        candidates.sort(key=lambda x:(x['id'] in done,abs(x.get('slozhnost',1)-level),x['id']))
        out.append({'skill':skill['code'],'name':skill['name'],'difficulty':level,
            'reason':('Недостаточно наблюдений' if not skill['n'] else f"Успешно {round(skill['rate']*skill['n'])} из {skill['n']} применимых проверок") + f'. Рекомендуемая сложность {level}/5.',
            'action':'Целевая тренировка','criterion':'Три самостоятельных успешных применения на разных сценариях',
            'selection':'Навык → сложность по последним самостоятельным результатам → новый сценарий',
            'scenarios':[{'id':x['id'],'name':x.get('nazvanie') or f"Билет {x.get('bilet','—')}, вызов {x.get('vyzov','—')}",'difficulty':x.get('slozhnost',1)} for x in candidates[:3]]})
        if len(out)==3:break
    return out
