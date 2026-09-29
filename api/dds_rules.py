"""DDS rubric v2: valid branches, medical service exceptions and applicable checks."""
import re
from . import config as C, domain as D, quality as Q, facts


def medical(s):
    return bool(re.search(r'103|СМП|скор',s.get('kartochka',{}).get('moya_sluzhba',''),re.I))


def status_reason(s, code):
    osn=s.get('osnovaniya',{})
    if code=='ne_prinyata':
        return osn.get('ne_kompetenciya') or osn.get('duplicate')
    cfg=next((x for x in s.get('_rules',{}).get('dds',C.DDS)['statusy'] if x['kod']==code),{})
    return osn.get(cfg.get('osnovanie',''))


def assess(s):
    cfg=s.get('_rules',{}).get('dds',C.DDS); statuses=s.get('statusy',[]); t0=s['nachalo']; F=[]
    critical={'podtverzhdenie','peredacha','po_faktu','zaversheno','etapy','kommentarii'}
    last=statuses[-1]['kod'] if statuses else None
    refusal=last=='ne_prinyata'; cancel=last=='otkaz'
    ismed=medical(s)
    def add(code,name,value,weight,details=None,source='Памятка ДДС; учебная рубрика v2'):
        F.append(D.Fakt(code,name,value,weight,source,details or {}))
    ack=next((x for x in statuses if x['kod'] in ('prinyata','ne_prinyata')),None)
    tp=round(ack['t']-t0,1) if ack else None
    ackvalid=bool(ack) and statuses[0] is ack and tp<=cfg['podtverzhdenie_sec']
    if ack and ack['kod']=='ne_prinyata':
        o=status_reason(s,'ne_prinyata'); ackvalid=ackvalid and not ismed and bool(o) and o['t']<=ack['t']
    add('podtverzhdenie','Получение карточки подтверждено в срок',bool(ackvalid),3,
        {'fakt_sec':tp,'normativ_sec':cfg['podtverzhdenie_sec'],'outcome':ack['kod'] if ack else None})
    if s.get('exercise_profile')=='card_check':
        osh=s['kartochka'].get('_oshibka'); zam=s.get('zamechaniya',[])
        hit=next((x for x in zam if osh and x['pole']==osh['pole']),None)
        ok=not zam if not osh else bool(hit) and (Q.compare_address(hit['verno'],osh['pravda'])['sovpalo'] if osh['pole']=='adres' else Q.normalized(hit['verno'])==Q.normalized(osh['pravda']))
        add('proverka','Проверка карточки в отдельном упражнении',bool(ok),3,{'error':osh,'correction':hit})
        critical.add('proverka')
    p=s.get('peredano') or {}
    required=s.get('bilet',{}).get('required_transmission',['adres','sut','telefon','postradavshie'])
    no_dispatch=refusal or (ismed and s.get('bilet',{}).get('no_dispatch'))
    if no_dispatch:critical.discard('peredacha')
    transfer = False if any(p.get(k) is False for k in required) or not p else (None if any(p.get(k) is None for k in required) else True)
    add('peredacha','Переданы сведения назначенному исполнителю',None if no_dispatch else transfer,3,
        {'required':required,'recipient':s.get('brigada'),**p})
    bad=[]; late=[]; missing=[]; delays=[]; comment_errors=[]
    previous=None
    for x in statuses:
        code=x['kod']; o=status_reason(s,code)
        invalid=not o or o['t']>x['t']
        if ismed and code in ('ne_prinyata','otkaz'):invalid=True
        if code=='otkaz' and (not any(y['kod']=='nachalo' and y['t']<=x['t'] for y in statuses) or any(y['kod']=='raboty' and y['t']<=x['t'] for y in statuses)):invalid=True
        order={'prinyata':0,'nachalo':1,'pribytie':2,'raboty':3,'zaversheno':4}
        if code in order and previous in order and order[code]<order[previous]:invalid=True
        if previous=='ne_prinyata' and code not in ('ne_prinyata','prinyata'):invalid=True
        if code=='ne_prinyata' and previous not in (None,'prinyata','ne_prinyata'):invalid=True
        if previous in ('zaversheno','otkaz') and code!=previous:invalid=True
        if code=='prinyata' and previous not in (None,'prinyata','ne_prinyata'):invalid=True
        if invalid:bad.append({'status':code,'t':x['t'],'reason':'Нет полученного основания или недопустимая ветка'})
        elif code not in ('prinyata','ne_prinyata'):
            delay=max(0,x['t']-o['t']);delays.append({'status':code,'delay_sec':round(delay,1),'limit_sec':cfg['svoevremenno_sec']})
            if delay>cfg['svoevremenno_sec']:late.append(code)
        if code in ('ne_prinyata','otkaz','zaversheno') and not facts.meaningful_comment(x.get('kommentariy',''),code):
            comment_errors.append(code)
        previous=code
    # Every stage actually reported must be reflected, including skipped reported stages.
    relevant=['nachalo','pribytie','raboty'] if not no_dispatch else []
    if cancel:relevant=['nachalo']
    for code in relevant:
        o=status_reason(s,code)
        if o and not any(x['kod']==code and x['t']>=o['t'] for x in statuses):missing.append(code)
    add('po_faktu','Статусы соответствуют полученным сведениям и ветке',bool(statuses) and not bad,3,{'invalid':bad})
    add('etapy','Полученные этапы отражены в статусах',not missing,2,{'missing':missing})
    add('svoevremenno','Своевременное обновление статусов',not late if delays else None,2,{'pozdno':late,'delays':delays,'normativ_sec':cfg['svoevremenno_sec']})
    terminal_ok=last=='zaversheno' or (refusal and not ismed) or (cancel and not ismed)
    add('zaversheno','Выбран обоснованный конечный исход',terminal_ok,2,{'poslednij':last})
    add('kommentarii','Причина отказа или результат работ описаны содержательно',not comment_errors,2,{'errors':comment_errors})
    nar=s.get('narusheniya_rechi',[]);spoke=any(m.get('kto')=='dispetcher' for c in s.get('zvonki',[]) for m in c.get('dialog',[]))
    add('rech','Профессиональная речь',not nar if spoke else None,1,{'spisok':nar})
    comments=' '.join(x.get('kommentariy','') for x in statuses).strip()
    gram=D.proverit_grammatiku(comments,s.get('_rules',{}).get('grammar'))
    add('grammatika','Грамматика комментариев',not gram if comments else None,1,{'spisok':gram})
    rubric=s.get('_rules',{}).get('grading',{})
    for f in F:f.ves=rubric.get('weights',{}).get(f.kod,f.ves)
    critical=set(rubric.get('critical',critical))
    if no_dispatch:critical.discard('peredacha')
    if s.get('exercise_profile')!='card_check':critical.discard('proverka')
    return {**Q.result(F,'dds',critical=critical,threshold=rubric.get('threshold',70)), 't_podtverzhdeniya':tp,
            'review_required':bool(p.get('review_required') and not no_dispatch),
            'ai_analysis':s.get('ai_analysis'), 'evaluation_method':'rules + source-linked extraction', 'branch':last}
