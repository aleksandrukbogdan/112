#!/usr/bin/env python3
"""Scripted demonstration attempts through the public 112/DDS API.

Run from /data/112/deploy/demo_attempts.py. The bank is used as a fixture, so
the resulting learner scores are synthetic examples, not independent evidence.
"""
import argparse
import getpass
import json
import os
import random
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class APIError(Exception):
    pass


class Client:
    def __init__(self, base):
        self.base = base.rstrip('/')
        self.token = ''

    def call(self, method, path, body=None, timeout=45):
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode('utf-8', errors='replace')
            raise APIError(f'{method} {path}: HTTP {exc.code}: {detail}') from None
        except urllib.error.URLError as exc:
            raise APIError(f'{method} {path}: соединение недоступно: {exc.reason}') from None

    def get(self, path):
        return self.call('GET', path)

    def post(self, path, body):
        return self.call('POST', path, body)


def load_bank(root):
    data = root / 'data'
    tickets = {b['id']: b for b in json.loads((data / 'bilety.json').read_text(encoding='utf-8'))}
    index = json.loads((data / 'index.json').read_text(encoding='utf-8'))
    return tickets, index


def victim_sentence(value):
    return {'net': 'Пострадавших нет.', 'est': 'Есть пострадавшие.',
            'neizvestno': 'Точных сведений о пострадавших нет.'}.get(value, 'Точных сведений о пострадавших нет.')


def classify(ticket, index):
    candidates = [(code, row) for code, row in index.items() if row.get('g') == ticket['gruppa']]
    if ticket.get('kod_etalon'):
        candidates = [(c, x) for c, x in candidates if c == ticket['kod_etalon']]
    elif ticket.get('pr1_etalon'):
        candidates = [(c, x) for c, x in candidates if x.get('pr1') == ticket['pr1_etalon']]
    if not candidates:
        raise ValueError(f"Нет позиции классификатора для {ticket['id']}")
    # These are scripted fixture values; a real student must establish them from the call.
    code, row = candidates[0]
    return code, row


def proposal_correct(proposal, ticket, root):
    """Conservative fixture review; ambiguous values remain unreviewed."""
    field, value = proposal['field'], proposal['value']
    if field == 'adres_polny':
        sys.path.insert(0, str(root))
        from api.quality import compare_address
        return bool(compare_address(value, ticket['adres_etalon'], spoken=True)['sovpalo'])
    if field == 'zayavitel_telefon':
        digits = lambda x: ''.join(c for c in str(x) if c.isdigit())[-10:]
        return digits(value) == digits(ticket['zayavitel']['telefon'])
    if field == 'postradavshie':
        return value == ticket['victims']
    if field == 'zayavitel_fio':
        return str(value).lower().rstrip('.') == ticket['zayavitel']['fio'].lower().rstrip('.')
    # A short fragment can be true yet incomplete; do not assign a false label.
    return None


def use_helper(api, sid, ticket, sequence, root):
    api.post(f'/api/helper/{sid}/toggle', {'enabled': True})
    suggestions = api.post(f'/api/helper/{sid}/suggest', {})['proposals']
    if not suggestions:
        raise APIError(f'Помощник не нашёл подтверждённых предложений в попытке {sid}')
    selected = [p for p in suggestions if p['field'] in
                ('zayavitel_telefon', 'adres_polny', 'postradavshie', 'zayavitel_fio')]
    selected.sort(key=lambda p: proposal_correct(p, ticket, root) is not False)
    if not selected:
        raise APIError(f'Помощник не предложил проверяемого поля в попытке {sid}')
    actions = ('accept', 'edit', 'reject')
    decisions = []
    for i, p in enumerate(selected[:3]):
        correctness = proposal_correct(p, ticket, root)
        action = ('edit' if sequence % 2 else 'reject') if correctness is False else actions[(sequence + i) % len(actions)]
        edit = {'zayavitel_telefon': ticket['zayavitel']['telefon'],
                'adres_polny': ticket['adres_etalon'], 'postradavshie': ticket['victims'],
                'zayavitel_fio': ticket['zayavitel']['fio']}
        body = {'action': action, 'expected_version': p['base_version'],
                'confirm_overwrite': True}
        if action == 'edit':
            body['value'] = edit[p['field']]
        api.post(f"/api/helper/{sid}/proposals/{p['id']}", body)
        decisions.append((p, correctness))
    print(f'  Помощник: {len(suggestions)} предложений, {len(decisions)} решений', flush=True)
    return decisions


def review_proposals(teacher, sid, decisions):
    if teacher is None:
        return 0
    reviewed = 0
    for proposal, correct in decisions:
        if correct is None:
            continue
        teacher.post(f"/api/helper/{sid}/proposals/{proposal['id']}/review",
                     {'correct': correct, 'reason': 'Синтетическая сверка предложения с эталоном учебного билета.'})
        reviewed += 1
    return reviewed


def ops_attempt(api, ticket, index, profile='correct', assisted=False, sequence=0, teacher=None, root=None,
                false_notice=False):
    sid = api.post('/api/session', {'scenario_id': ticket['id']})['session_id']
    print(f"112 {ticket['id']}: попытка {sid}", flush=True)
    try:
        for question in ('Служба 112, что случилось?',
                         'Назовите точный адрес, улицу, дом и строение.',
                         'Есть ли пострадавшие? Как вас зовут?',
                         'Назовите номер телефона для связи.'):
            reply = api.post(f'/api/session/{sid}/replika', {'tekst': question})
            print('  Заявитель:', str(reply.get('otvet', ''))[:180], flush=True)
        if false_notice and teacher:
            teacher.post(f'/api/training/{sid}/intervene',
                         {'type': 'notice', 'text': 'Учебная проверочная вводная (может быть ошибочной): '
                          'адрес Москва, улица Ошибочная, дом 999.'})
        decisions = use_helper(api, sid, ticket, sequence, root) if assisted else []
        code, position = classify(ticket, index)
        services = set(position.get('sluzhby', []))
        if position.get('smp', {}).get(ticket['victims']):
            services.add('SMP')
        card = {'adres_polny': ticket['adres_etalon'], 'opisanie': 'Учебная демонстрация. ' + ticket['situaciya'],
                'zayavitel_fio': ticket['zayavitel']['fio'], 'zayavitel_telefon': ticket['zayavitel']['telefon'],
                'tip_kod': code, 'tip_itog': position['itog'], 'postradavshie': ticket['victims'],
                'pravonarushenie': False, 'sluzhby': sorted(services)}
        if profile == 'wrong_address':
            card['adres_polny'] = 'Москва, улица Ошибочная, дом 999'
        elif profile == 'wrong_type':
            other = next((c for c, row in index.items() if row.get('g') != ticket['gruppa']), None)
            if not other:
                raise ValueError('Нет другого типа для демонстрации ошибки классификации')
            card['tip_kod'] = other
            card['tip_itog'] = index[other]['itog']
        elif profile == 'wrong_services':
            # An empty route can be correct for some classifier positions.
            extra = next(x for x in ('MVD', 'SMP', '101', '102', '103', '104') if x not in services)
            card['sluzhby'] = sorted(services | {extra})
        elif profile == 'incomplete':
            card['opisanie'] = ''
        elif profile != 'correct':
            raise ValueError('Неизвестный демонстрационный профиль: ' + profile)
        try:
            api.get(f'/api/session/{sid}/prognoz')
        except APIError as exc:
            print('  Прогноз недоступен:', exc, flush=True)
        result = api.post(f'/api/session/{sid}/otpravit', {'kartochka': card})
        if decisions:
            print('  Предложений проверено преподавателем:', review_proposals(teacher, sid, decisions), flush=True)
        return sid, result
    except Exception:
        print(f'  Попытка {sid} осталась открытой; её можно продолжить в интерфейсе.', flush=True)
        raise


def status(api, sid, code, comment=''):
    response = api.post(f'/api/dds/{sid}/status', {'status': code, 'kommentariy': comment})
    if response.get('preduprezhdenie'):
        raise APIError('Статус ' + code + ': ' + response['preduprezhdenie'])


def call_crew(api, sid):
    for crew in ('br1', 'br2', 'br3'):
        response = api.post(f'/api/dds/{sid}/call', {'kontakt': crew})
        if response['ishod'] == 'otvetil':
            return response['call_id']
    raise APIError('Ни одна из трёх бригад не ответила; попытка оставлена открытой')


def dds_attempt(api, ticket, max_wait, profile='correct', assisted=False, teacher=None):
    sid = api.post('/api/dds/session', {'scenario_id': ticket['id'], 'sluzhba': ticket['service_profiles'][0]})['session_id']
    print(f"ДДС {ticket['id']}: попытка {sid}", flush=True)
    try:
        status(api, sid, 'prinyata', 'Карточка получена и принята в работу.')
        cid = call_crew(api, sid)
        if profile == 'incomplete_transfer':
            api.post(f'/api/dds/{sid}/call/{cid}/say',
                     {'tekst': 'Получил карточку. Адрес и детали происшествия пока уточняются.'})
            api.post(f'/api/dds/{sid}/call/{cid}/end', {})
            return sid, api.post(f'/api/dds/{sid}/finish', {})
        transfer = (f"Уточнённый адрес: {ticket['adres_etalon']}. {ticket['situaciya']}. "
                    f"{victim_sentence(ticket['victims'])} Телефон заявителя {ticket['zayavitel']['telefon']}.")
        reply = api.post(f'/api/dds/{sid}/call/{cid}/say', {'tekst': transfer})
        if not reply.get('vyezd'):
            raise APIError('Бригада не выехала после передачи адреса; завершение не выполняется')
        api.post(f'/api/dds/{sid}/call/{cid}/end', {})
        dds_decisions = []
        if assisted:
            api.post(f'/api/helper/{sid}/toggle', {'enabled': True})
            proposals = api.post(f'/api/helper/{sid}/suggest', {})['proposals']
            item = next((p for p in proposals if p['field'] == 'status' and p['value'] == 'nachalo'), None)
            if not item:
                raise APIError('Нет предложения статуса после полученного сообщения бригады')
            api.post(f"/api/helper/{sid}/proposals/{item['id']}",
                     {'action': 'accept', 'expected_version': item['base_version']})
            dds_decisions.append((item, True))
        status(api, sid, 'nachalo', 'Сведения переданы бригаде, она выехала.')
        phases = {'pribytie': 'Прибытие', 'raboty': 'Работы начаты',
                  'zaversheno': 'Работы завершены, результат передан и зафиксирован.'}
        done = set()
        deadline = time.monotonic() + max_wait
        while len(done) < len(phases) and time.monotonic() < deadline:
            events = api.get(f'/api/dds/{sid}/events')
            for event in events.get('vhodyashchie', []):
                answer = api.post(f"/api/dds/{sid}/incoming/{event['id']}/answer", {})
                phase = answer['faza']
                if phase in phases and phase not in done:
                    status(api, sid, phase, phases[phase])
                    done.add(phase)
                    print(f'  Этап {phase}: входящий звонок принят, статус обновлён', flush=True)
                api.post(f"/api/dds/{sid}/call/{answer['call_id']}/end", {})
            if len(done) < len(phases):
                time.sleep(1)
        if len(done) != len(phases):
            raise APIError(f'Истекло ожидание этапов ДДС; получены {sorted(done)}. Попытка {sid} остаётся открытой')
        result = api.post(f'/api/dds/{sid}/finish', {})
        if dds_decisions:
            print('  Статус, предложенный помощником, проверен преподавателем:',
                  review_proposals(teacher, sid, dds_decisions), flush=True)
        return sid, result
    except Exception:
        print(f'  Попытка {sid} осталась открытой; её можно продолжить в интерфейсе.', flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description='Демонстрационные прохождения 112 и ДДС с записью в аналитику')
    parser.add_argument('--url', default='http://127.0.0.1:21121')
    parser.add_argument('--login', default='trainee')
    parser.add_argument('--scenario', default='b01_v1', help='ID билета из data/bilety.json')
    parser.add_argument('--ops-count', type=int, default=3)
    parser.add_argument('--dds-count', type=int, default=1)
    parser.add_argument('--max-wait', type=int, default=180)
    parser.add_argument('--varied', action='store_true', help='Парные вызовы 112 и разные исходы ДДС')
    parser.add_argument('--varied-count', type=int, default=6, help='Число ПАР 112, 1–10; каждая пара состоит из самостоятельной и с помощником')
    parser.add_argument('--varied-dds', type=int, default=3, help='Попытки ДДС: самостоятельная, с помощником, ошибка передачи (0–3)')
    parser.add_argument('--teacher-login', default='teacher', help='Преподаватель для разметки предложений, только --varied')
    parser.add_argument('--seed', type=int, help='Повторить выбор билетов; без него каждый запуск выбирает новую комбинацию')
    parser.add_argument('--confirm-demo', action='store_true', help='Подтвердить запись синтетических попыток в результаты ученика')
    args = parser.parse_args()
    if not args.confirm_demo:
        parser.error('Добавьте --confirm-demo: попытки навсегда попадут в учебную аналитику выбранного пользователя')
    if not 0 <= args.ops_count <= 20 or not 0 <= args.dds_count <= 5 or args.ops_count + args.dds_count == 0:
        parser.error('Укажите от 1 до 20 попыток 112 и от 0 до 5 попыток ДДС')
    if args.varied and not (1 <= args.varied_count <= 10 and 0 <= args.varied_dds <= 3):
        parser.error('Для --varied нужно 1–10 пар 112 и 0–3 попытки ДДС')
    host = urllib.parse.urlparse(args.url).hostname
    if host not in ('127.0.0.1', 'localhost'):
        parser.error('Для защиты пароля запускайте скрипт на сервере через http://127.0.0.1:21121')
    root = Path(__file__).resolve().parent.parent
    tickets, index = load_bank(root)
    ticket = tickets.get(args.scenario)
    if not ticket:
        parser.error('Сценарий не найден в локальном банке')
    if not ticket.get('service_profiles'):
        parser.error('Сценарий не содержит профиля службы ДДС')
    password = os.environ.get('DEMO_TRAINEE_PASSWORD') or getpass.getpass('Пароль обучаемого: ')
    api = Client(args.url)
    api.token = api.post('/api/login', {'login': args.login, 'password': password})['token']
    password = None
    who = api.get('/api/me')
    if who['role'] != 'trainee':
        parser.error('Нужна учётная запись обучаемого')
    visible = {x['id'] for x in api.get('/api/scenarios')}
    if ticket['id'] not in visible:
        parser.error('Билет не опубликован для обучаемого')
    teacher = None
    if args.varied:
        teacher_password = os.environ.get('DEMO_TEACHER_PASSWORD') or getpass.getpass('Пароль преподавателя для разметки подсказок: ')
        teacher = Client(args.url)
        teacher.token = teacher.post('/api/login', {'login': args.teacher_login, 'password': teacher_password})['token']
        teacher_password = None
        if teacher.get('/api/me')['role'] != 'teacher':
            parser.error('Для разметки предложений нужна учётная запись преподавателя')
    print('ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ: использован локальный эталон билета; результаты не отражают навык человека.', flush=True)
    finished = []
    expected = []
    try:
        if args.varied:
            # Pick distinct incident groups; random seed rotates tickets on repeated runs.
            choices = []
            seen = set()
            seed = args.seed if args.seed is not None else secrets.randbits(48)
            rng = random.Random(seed)
            candidates = list(tickets.values())
            rng.shuffle(candidates)
            print('Выбор билетов, seed:', seed, flush=True)
            for candidate in candidates:
                if candidate['id'] in visible and candidate['gruppa'] not in seen:
                    try:
                        classify(candidate, index)
                    except ValueError:
                        continue
                    choices.append(candidate)
                    seen.add(candidate['gruppa'])
            if not choices:
                raise ValueError('Нет опубликованных билетов с классификацией')
            profiles = (('wrong_address','correct'), ('correct','correct'),
                        ('wrong_type','correct'), ('wrong_services','wrong_address'),
                        ('incomplete','correct'), ('correct','incomplete'))
            ops_jobs = []
            for i in range(args.varied_count):
                case = choices[i % len(choices)]
                independent, assisted = profiles[i % len(profiles)]
                ops_jobs.extend([(case, independent, False), (case, assisted, True)])
            dds_jobs = [(ticket, profile, assisted) for profile, assisted in
                        [('correct',False),('correct',True),('incomplete_transfer',False)][:args.varied_dds]]
        else:
            ops_jobs = [(ticket, 'correct', False)] * args.ops_count
            dds_jobs = [(ticket, 'correct', False)] * args.dds_count
        for sequence, (case, profile, assisted) in enumerate(ops_jobs):
            print('  Профиль 112:', profile, 'с помощником' if assisted else 'самостоятельно', flush=True)
            sid, result = ops_attempt(api, case, index, profile, assisted, sequence, teacher, root,
                                      false_notice=args.varied and sequence == 1)
            finished.append(('ops112', sid, result))
            expected.append(profile == 'correct')
            print(f"  Итог: {result['ball']}, {result['verdikt']}; ошибки: {result.get('critical_errors', [])}", flush=True)
        for case, profile, assisted in dds_jobs:
            print('  Профиль ДДС:', profile, 'с помощником' if assisted else 'самостоятельно', flush=True)
            sid, result = dds_attempt(api, case, args.max_wait, profile, assisted, teacher)
            finished.append(('dds', sid, result))
            expected.append(profile == 'correct')
            print(f"  Итог: {result['ball']}, {result['verdikt']}; ошибки: {result.get('critical_errors', [])}", flush=True)
    finally:
        for kind in ('ops112', 'dds'):
            ids = [sid for k, sid, _ in finished if k == kind]
            if ids:
                query = urllib.parse.urlencode({'kind': kind, 'rubric': '112-quality-2'})
                try:
                    analysis = api.get('/api/analysis/snapshot?' + query)
                    found = {x['id'] for x in analysis['attempts']}
                    print(f'Аналитика {kind}: {len(found & set(ids))}/{len(ids)} новых попыток; '
                          f'всего завершено по фильтру {analysis["summary"]["completed"]}; '
                          f'пары {analysis["comparison"]["paired_n"]}; '
                          f'помощник показал {analysis["helper"]["shown"]}, '
                          f'проверено {analysis["helper"]["reviewed"]}', flush=True)
                except APIError as exc:
                    print('Проверка аналитики:', exc, flush=True)
    unexpected = [(sid, want, result.get('passed')) for (_, sid, result), want in zip(finished, expected)
                  if bool(result.get('passed')) != want]
    if unexpected:
        print('Неожиданные исходы:', unexpected, 'Проверьте их в «Разборе».', flush=True)
        return 2
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (APIError, OSError, ValueError, KeyError) as exc:
        print('ОШИБКА:', exc, file=sys.stderr)
        sys.exit(1)
