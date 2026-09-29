"""Structured generation and correction. Every correction creates an unpublished copy."""
import copy
import json
import time
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from . import db, curriculum, generator, ai_text, access, session_store as store
from .auth import current_user, role

router=APIRouter(prefix='/api/scenario-tools',tags=['scenarios'])


class Generate(BaseModel):
    kod:str
    difficulty:int=Field(default=3,ge=1,le=5)
    location:str=Field(default='',max_length=1000)
    goal:str=Field(default='',max_length=2000)
    kind:str='dds'
    service:str|None=None
    material_ids:list[str]=Field(default_factory=list,max_length=20)
    clarification:bool=True
    count:int=Field(default=1,ge=1,le=5)


def sources(ids,u):
    out=[]
    for mid in ids:
        row=db.q1('SELECT * FROM materials WHERE id=? AND owner_id=?',(mid,u['id']))
        if not row:raise HTTPException(403,'Источник не принадлежит преподавателю')
        out.append({'id':mid,'hash':row['hash'],'title':row['title'],'text':row['content'][:20000]})
    return out


def save(s,u,comment='',parent=None):
    errors=curriculum.validate(s)
    if errors:raise HTTPException(422,errors)
    with db.tx():
        db.ex('INSERT INTO scenarios(id,source,status,data,created_by,created,comment) VALUES(?,?,?,?,?,?,?)',
              (s['id'],'gen','draft',store.dumps(s),u['id'],time.time(),comment))
        db.ex('INSERT INTO scenario_revisions(scenario_id,revision,actor,data,comment,created) VALUES(?,?,?,?,?,?)',
              (s['id'],1,u['id'],store.dumps(s),comment,time.time()))
        db.audit(u,'scenario_version',{'id':s['id'],'parent':parent,'comment':comment})
    return {'scenario':s,'status':'draft','validation_errors':[]}


@router.post('/generate')
async def generate(b:Generate,u=Depends(role('teacher'))):
    if b.kind not in ('dds','ops112'):raise HTTPException(422,'Неизвестная программа')
    refs=sources(b.material_ids,u);out=[]
    for _ in range(b.count):
        try:s=await generator.sgenerirovat(b.kod,b.difficulty,b.clarification,u['id'])
        except ValueError as exc:raise HTTPException(422,str(exc))
        if b.goal or b.location or refs:
            model,diagnostic=await ai_text.structured(
                'Доработай учебный сценарий под цель и место. Сохрани id, код классификатора и группу. '
                'Верни {"situaciya":"...", "adres_vidimy":"...", "adres_etalon":"...", "training_focus":"..."}. '
                'Не добавляй непроверенные нормативы. Не приписывай источникам отсутствующие сведения.',
                {'scenario':s,'goal':b.goal,'location':b.location,'sources':refs})
            if model:
                for key in ('situaciya','adres_vidimy','adres_etalon','training_focus'):
                    if isinstance(model.get(key),str) and model[key].strip():s[key]=model[key][:5000]
            elif b.location:
                s['adres_vidimy']=s['adres_etalon']=b.location
            s['generation_diagnostic']=diagnostic
        s=curriculum.enrich(s);s['training_goal']=b.goal;s['target_program']=b.kind
        if b.service:s['service_profiles']=[b.service]
        s['source_materials']=[{k:x[k] for k in ('id','hash','title')} for x in refs]
        s['trebuet_utochneniya']=s['adres_vidimy'].strip()!=s['adres_etalon'].strip()
        out.append(save(s,u))
    return out


class Correct(BaseModel):
    comment:str=Field(min_length=5,max_length=5000)
    edits:dict=Field(default_factory=dict)


@router.post('/{sid}/correct')
async def correct(sid:str,b:Correct,u=Depends(role('teacher'))):
    row=db.q1('SELECT * FROM scenarios WHERE id=?',(sid,))
    access.scenario(u,row)
    old=json.loads(row['data']);s=copy.deepcopy(old)
    allowed={'situaciya','adres_vidimy','adres_etalon','zayavitel','slozhnost','target_skills','service_profiles',
             'training_goal','outside_competence','duplicate','cancel_allowed','no_dispatch','events','required_transmission','victims'}
    if set(b.edits)-allowed:raise HTTPException(422,'Неизвестные редактируемые поля')
    diagnostic={'status':'manual_edits'}
    if not b.edits:
        model,diagnostic=await ai_text.structured(
            'Исправь сценарий по замечанию преподавателя, не меняй код классификатора. '
            'Верни {"edits":{"situaciya":"...","adres_vidimy":"...","adres_etalon":"...","training_goal":"..."},"explanation":"..."}. '
            'Возвращай только изменённые поля.',{'scenario':s,'comment':b.comment})
        if not model or not isinstance(model.get('edits'),dict):
            raise HTTPException(503,'Модель не исправила сценарий. Исходник сохранён; внесите правку вручную и повторите.')
        edits={k:v for k,v in model['edits'].items() if k in allowed}
    else:edits=b.edits
    s.update(edits);s['id']='gen_'+uuid.uuid4().hex[:12];s['parent_scenario']=sid
    errors=curriculum.validate(s)
    if errors:raise HTTPException(422,errors)
    s['trebuet_utochneniya']=s['adres_vidimy'].strip()!=s['adres_etalon'].strip()
    changes=[{'field':k,'before':old.get(k),'after':s.get(k)} for k in edits if old.get(k)!=s.get(k)]
    if not changes:raise HTTPException(422,'Исправления не изменяют сценарий; уточните замечание')
    s['correction']={'comment':b.comment,'changes':changes,'diagnostic':diagnostic}
    return save(s,u,b.comment,parent=sid)


@router.get('/{sid}/preview')
def preview(sid:str,u=Depends(role('teacher'))):
    row=db.q1('SELECT * FROM scenarios WHERE id=?',(sid,));access.scenario(u,row)
    s=json.loads(row['data']);return {'scenario':s,'status':row['status'],'validation_errors':curriculum.validate(s)}
