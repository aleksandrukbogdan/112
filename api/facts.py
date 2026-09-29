"""Evidence extraction: explicit polarity, recipient history and conservative unknowns."""
import re
from . import quality as Q

UNKNOWN = re.compile(r'не\s*(?:извест|знаю|установ)|нет\s+(?:точн\w*\s+)?(?:данных|информац|сведен)|точн\w*\s+информац\w*\s+нет|пострадавш\w*\s+не\s+вид|не\s+вид\w*\s+пострадав', re.I)
ABSENT = re.compile(r'пострадавш\w*(?:\s+людей)?\s+нет|без\s+пострадавш|б\s*/\s*п\b', re.I)
INJURY = re.compile(r'пострадал|\d+\s+пострадавш|есть\s+пострадавш|(?:без|потер\w*)\s+сознани|травм|кровотеч|ожог|ранен|в\s+крови|не\s+дыш|задыха|судорог|плохо|сильн\w*\s+головн|отек\s+рук|отошли\s+воды', re.I)


def victims(text):
    # Unknown and absence of visible injuries must not suppress explicit unconsciousness.
    t = Q.normalized(text)
    if re.search(r'(?:без|потер\w*)\s+сознани|не\s+дыш', t):
        return 'est'
    if UNKNOWN.search(t):
        return 'neizvestno'
    if ABSENT.search(t):
        return 'net'
    if INJURY.search(t):
        return 'est'
    return None


EVENTS = [
 ('fire',r'горит|горят|горел|пожар|возгоран|плам|задым|дым\b'),
 ('crash',r'дтп|столкнул|столкнов|наезд|сбил|авари|падени\w*\s+автомашин|бревно'),
 ('fight',r'дерут|драк|напал|изби|бьют|угрожа|угроз|убий|взорва'),
 ('medical',r'плохо|бол[ьи]|сознани|отек|судорог|отрав|упал|роды|отошли воды|инсульт'),
 ('rescue',r'тонет|льдин|плывут|упал|паден|заблок|открыть дверь|не открывает|просит о помощи|застрял'),
 ('missing',r'заблуд|потерял|пропал|не вернул|ушел|ушёл'),
 ('gas',r'газ|свист.*труб'),
 ('crime',r'угон|завладе|украл|краж|подозритель|коробк|провод|бомб|взрыв'),
 ('utility',r'освещен|вод[аы]|прорыв|трещин|плитк|табло|креплен|электр|отоплен|фонар'),
 ('disturbance',r'музык|поругал|ремонт|стоит у домофона|куртке|нарушен|шум'),
 ('death',r'труп|скончал|умер'),
]


def essence(text, expected):
    """Address/victim/telephone tokens never count as incident description."""
    expected = re.split(r'[,.;]\s*(?:пострадав|о пострадав)', expected, maxsplit=1, flags=re.I)[0]
    labels = [label for label,rx in EVENTS if re.search(rx, expected, re.I)]
    if not labels:
        return None
    return any(re.search(rx, text, re.I) and not re.search(r'\bне\s+(?:'+rx+r')', text, re.I)
               for label,rx in EVENTS if label in labels)


def latest_claim(messages, check):
    """Last explicit mention wins. Corrections replace earlier facts, never concatenate numbers."""
    found = None
    for i,m in enumerate(messages):
        text = m.get('tekst','')
        value = check(text)
        if value is not None:
            found = {'value': value, 'message': m.get('id',str(i)), 'quote': text, 't': m.get('t',m.get('t_ms'))}
    return found


def transmission(messages, truth):
    def addr(t):
        if not re.search(r'адрес|улиц|дом|проезд|шоссе|переул|проспект|км|мкад|област|площад|бульвар|станци|набереж|корп', t, re.I):
            return None
        t = re.split(r'(?:уточняю|исправляю|правильный адрес|верный адрес)\s*[:—-]?\s*',t,flags=re.I)[-1]
        return Q.compare_address(t, truth['adres'], spoken=True)['sovpalo']
    def telephone(t):
        parts = re.findall(r'(?<!\d)(?:\+?7|8)?[\s(-]*9\d{2}[\s)-]*\d{3}[ -]*\d{2}[ -]*\d{2}(?!\d)',t)
        if not parts:return None
        actual = re.sub(r'\D','',parts[-1])[-10:]
        return actual == re.sub(r'\D','',truth.get('telefon',''))[-10:]
    a=latest_claim(messages,addr); tel=latest_claim(messages,telephone)
    post=latest_claim(messages, victims)
    # Essence can arrive after departure or in a subsequent call to the SAME crew.
    description=latest_claim(messages,lambda t: essence(t,truth.get('opisanie','')) if any(re.search(rx,t,re.I) for _,rx in EVENTS) else None)
    claims={'adres':a,'telefon':tel,'postradavshie':post,'sut':description}
    values={'adres':bool(a and a['value']), 'telefon':bool(tel and tel['value']),
            'sut':bool(description and description['value']),
            'postradavshie':bool(post and post['value']==truth.get('postradavshie','neizvestno'))}
    return {**values, 'adres_dolya':float(values['adres']), 'evidence':claims,
            'victims_actual':post['value'] if post else None,
            'complete':all(values.values()), 'review_required':description is None}


def meaningful_comment(text, code):
    t=Q.normalized(text)
    if len(re.findall(r'[а-яa-z]{2,}',t)) < 3:
        return False
    if code=='ne_prinyata':
        return bool(re.search(r'компетенц|территор|дубликат|повторн|передан|ошибочн\w*\s+направ',t))
    if code=='otkaz':
        return bool(re.search(r'отказ|отмен|ложн|не подтверд|не требуется|устранен|устранён',t))
    if code=='zaversheno':
        return bool(re.search(r'заверш|выполн|устран|ликвидир|оказан|передан|освобожд|работ',t))
    return True


def visible_messages(s):
    """Only the trainee's current observations. NEVER scenario gold or future event lists."""
    out=[]
    if s.get('kind','ops112')=='dds':
        k=s['kartochka']
        for key in ('adres','opisanie','aon','zayavitel_fio','postradavshie'):
            out.append({'id':'card:'+key,'tekst':str(k.get(key,'')),'field':key,'t':s['nachalo']})
        for call in s.get('zvonki',[]):
            for i,m in enumerate(call.get('dialog',[])):
                if m.get('kto')=='abonent':
                    out.append({'id':call['id']+':'+str(i),'tekst':m['tekst'],'t':m.get('t'), 'from':call['kontakt']['kto']})
        for e in s.get('visible_notices',[]):
            out.append({'id':e['id'],'tekst':e['text'],'t':e['ts'],'from':'teacher'})
    else:
        for i,m in enumerate(s.get('dialog',[])):
            if m.get('kto') in ('caller','teacher'):
                out.append({'id':'dialog:'+str(i),'tekst':m['tekst'],'t':s['nachalo']+m.get('t_ms',0)/1000})
    return out
