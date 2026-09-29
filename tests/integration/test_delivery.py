"""Acceptance paths for the full update; always uses an isolated temporary database."""
import asyncio
import copy
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from test_api import APITests

class DeliveryTests(unittest.TestCase):
    setUpClass=classmethod(APITests.setUpClass.__func__)
    tearDownClass=classmethod(APITests.tearDownClass.__func__)
    request=APITests.request;start=APITests.start;card=APITests.card

    def setUp(self):
        for gid in (1,2):self.db.ex('INSERT INTO groups(id,name) VALUES(?,?) ON CONFLICT DO NOTHING',(gid,'Group '+str(gid)))
        self.db.ex("INSERT INTO users(id,login,name,role,pw_hash) VALUES(5,'otherteacher','Other teacher','teacher','x') ON CONFLICT DO NOTHING")
        self.db.ex('INSERT INTO teacher_groups(teacher_id,group_id) VALUES(5,2) ON CONFLICT DO NOTHING')
        self.db.ex("UPDATE lessons SET state='stopped' WHERE state='running'")
        self.flow=copy.deepcopy(self.sc);self.flow.update(id='flow',situaciya='Горит квартира, есть пострадавшие.',adres_etalon='Москва, улица Тверская, дом 12',adres_vidimy='Москва, улица Тверская, дом 12',victims='est',trebuet_utochneniya=False,service_profiles=['Служба 101','Служба 103'])
        self.flow['zayavitel']={'fio':'Иванов Иван','telefon':'916-123-45-67'};self.save_flow()

    def save_flow(self):self.db.ex("INSERT INTO scenarios(id,source,status,data) VALUES('flow','test','published',?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",(json.dumps(self.flow),))
    def dds(self,**kw):
        r=self.request('POST','/api/dds/session',{'scenario_id':'flow',**kw});self.assertEqual(r.status_code,200,r.text);return r.json()['session_id']
    def call(self,sid,contact='br1'):
        with patch('api.dds.random.random',return_value=.5):r=self.request('POST',f'/api/dds/{sid}/call',{'kontakt':contact})
        self.assertEqual(r.status_code,200,r.text);return r.json()['call_id']
    def say(self,sid,cid,text):return self.request('POST',f'/api/dds/{sid}/call/{cid}/say',{'tekst':text})
    def state(self,sid):return json.loads(self.db.q1('SELECT data FROM sessions WHERE id=?',(sid,))['data'])
    def status(self,sid,code,comment=''):return self.request('POST',f'/api/dds/{sid}/status',{'status':code,'kommentariy':comment})

    def test_01_partial_transmission_96_cases(self):
        from api import dds
        for b in self.C.load('bilety'):
            p=dds._peredano(b['adres_etalon']+'. Пострадавших нет.',{'adres':b['adres_etalon'],'opisanie':b['situaciya'],'telefon':b['zayavitel']['telefon'],'postradavshie':b['victims']})
            self.assertFalse(p['complete'],b['id'])

    def test_02_accumulated_transmission_and_corrections(self):
        sid=self.dds();cid=self.call(sid);self.say(sid,cid,self.flow['adres_etalon'])
        self.say(sid,cid,'Горит квартира. Пострадавших нет. Телефон 916-123-45-67.')
        self.assertFalse(self.state(sid)['peredano']['complete'])
        self.say(sid,cid,'Уточняю: есть пострадавшие.');self.assertTrue(self.state(sid)['peredano']['complete'],self.state(sid)['peredano'])
        self.say(sid,cid,'Уточняю адрес: Москва, улица Тверская, дом 99.');self.assertFalse(self.state(sid)['peredano']['adres'])

    def test_03_valid_refusal(self):
        self.flow['outside_competence']=True;self.save_flow();sid=self.dds();cid=self.call(sid,'ruk')
        self.say(sid,cid,'Уточните компетенцию службы.');self.status(sid,'ne_prinyata','Не относится к компетенции службы, информация передана дежурному 112.')
        r=self.request('POST',f'/api/dds/{sid}/finish',{});self.assertTrue(r.json()['passed'],r.text)
        self.assertNotIn('proverka',r.json()['critical_codes']);self.assertIsNone(next(x['proyden'] for x in r.json()['fakty'] if x['kod']=='peredacha'))

    def test_04_medical_service(self):
        self.flow['no_dispatch']=True;self.flow['service_profiles']=['Служба 103'];self.save_flow();sid=self.dds(sluzhba='Служба 103')
        self.assertEqual(self.status(sid,'ne_prinyata').status_code,422);self.status(sid,'prinyata')
        self.say(sid,self.call(sid,'ruk'),'Уточните результат обработки обращения.')
        self.status(sid,'zaversheno','Обращение обработано, помощь оказана без выезда бригады.')
        r=self.request('POST',f'/api/dds/{sid}/finish',{});self.assertTrue(r.json()['passed'],r.text)

    def test_05_hidden_phase(self):
        sid=self.dds();self.say(sid,self.call(sid),self.flow['adres_etalon'])
        with patch('api.dds._now',return_value=time.time()+46):r=self.request('GET',f'/api/dds/{sid}/events').json()
        self.assertEqual(r['faza'],'vyezd');self.assertNotIn('faza',r['vhodyashchie'][0])

    def test_06_helper_access_gold_and_staleness(self):
        sid=self.start();self.assertEqual(self.request('POST',f'/api/helper/{sid}/suggest',{}).status_code,403)
        self.request('POST',f'/api/helper/{sid}/toggle',{'enabled':True})
        self.assertEqual(self.request('POST',f'/api/helper/{sid}/suggest',{}).json()['proposals'],[])
        self.request('POST',f'/api/session/{sid}/replika',{'tekst':'Номер телефона для связи?'})
        ps=self.request('POST',f'/api/helper/{sid}/suggest',{}).json()['proposals'];p=next(x for x in ps if x['field']=='zayavitel_telefon')
        self.request('POST',f'/api/session/{sid}/pole',{'key':'zayavitel_telefon','value':'Ручной ввод'})
        r=self.request('POST',f"/api/helper/{sid}/proposals/{p['id']}",{'action':'accept','expected_version':p['base_version'],'confirm_overwrite':True});self.assertEqual(r.status_code,409,r.text)
        self.request('POST',f'/api/helper/{sid}/toggle',{'enabled':False});h=self.request('GET',f'/api/helper/{sid}').json()
        self.assertTrue(h['exposed']);self.assertFalse(h['enabled']);self.assertEqual(self.state(sid)['kartochka']['zayavitel_telefon'],'Ручной ввод')

    def test_07_helper_explicit_application(self):
        sid=self.start();self.request('POST',f'/api/session/{sid}/replika',{'tekst':'Номер телефона для связи?'})
        self.request('POST',f'/api/helper/{sid}/toggle',{'enabled':True})
        p=next(x for x in self.request('POST',f'/api/helper/{sid}/suggest',{}).json()['proposals'] if x['field']=='zayavitel_telefon')
        self.assertNotIn('zayavitel_telefon',self.state(sid)['kartochka'])
        r=self.request('POST',f"/api/helper/{sid}/proposals/{p['id']}",{'action':'edit','value':'79991112233','expected_version':p['base_version']})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.state(sid)['kartochka']['zayavitel_telefon'],'79991112233')

    def test_08_command_deduplication(self):
        sid=self.start();headers={'Authorization':'Bearer '+self.auth.make_token(1),'Idempotency-Key':'same-command'}
        path=f'/api/session/{sid}/pole';body={'key':'opisanie','value':'Тестовая информация для карточки.'}
        r=self.client.post(path,json=body,headers=headers);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.post(path,json=body,headers=headers).json(),r.json())
        self.assertEqual(self.client.post(path,json={**body,'value':'Другая команда'},headers=headers).status_code,409)
        self.assertEqual(self.request('GET',f'/api/training/{sid}/resume',uid=2).status_code,403)

    def test_09_lesson_queue_exam_and_stop(self):
        r=self.request('POST','/api/lessons',{'name':'Приёмочное занятие','group_id':1,'scenario_ids':['flow','flow'],'interval_sec':5,'max_active':2,'mode':'exam'},uid=3)
        self.assertEqual(r.status_code,200,r.text);lid=r.json()['id'];r=self.request('POST',f'/api/lessons/{lid}/start',{},uid=3);self.assertEqual(r.status_code,200,r.text)
        cards=[x for x in self.request('GET','/api/lessons/queue/mine').json()['cards'] if x['lesson_id']==lid];self.assertEqual(len(cards),1);sid=cards[0]['id']
        self.assertEqual(self.request('POST',f'/api/helper/{sid}/toggle',{'enabled':True}).status_code,403)
        self.assertEqual(self.request('POST','/api/dds/session',{'scenario_id':'flow'}).status_code,403)
        from api import lessons
        self.db.ex('UPDATE lesson_members SET data=? WHERE lesson_id=?',(json.dumps({'next':1,'next_at':0}),lid));lessons.tick(lid)
        self.assertEqual(len([x for x in self.request('GET','/api/lessons/queue/mine').json()['cards'] if x['lesson_id']==lid]),2)
        self.main.reload_live();self.assertEqual(self.request('GET',f'/api/training/{sid}/resume').status_code,200)
        self.request('POST',f'/api/lessons/{lid}/stop',{},uid=3);self.assertEqual(self.db.q1('SELECT state FROM sessions WHERE id=?',(sid,))['state'],'stopped')

    def test_10_analytics_exports_and_scope(self):
        sid=self.start();self.request('POST',f'/api/session/{sid}/otpravit',{'kartochka':self.card()})
        r=self.request('GET','/api/analysis/snapshot?kind=ops112&uid=1');self.assertEqual(r.status_code,200,r.text);a=r.json();self.assertIn(sid,[x['id'] for x in a['attempts']])
        path=f"/api/analysis/export/{a['snapshot_id']}";token=self.auth.make_token(1)
        self.assertEqual(self.client.get(path+'.json?token='+token).json(),a)
        for fmt,magic in [('xlsx',b'PK'),('pdf',b'%PDF')]:
            r=self.client.get(path+'.'+fmt+'?token='+token);self.assertEqual(r.status_code,200,r.text[:200] if r.status_code!=200 else '');self.assertTrue(r.content.startswith(magic))
        self.assertEqual(self.request('GET','/api/analysis/snapshot?uid=1',uid=5).status_code,403)
        self.assertEqual(self.request('GET',f'/api/session/{sid}/razbor',uid=5).status_code,403)

    def test_11_replay_does_not_mutate_original(self):
        sid=self.start();self.request('POST',f'/api/session/{sid}/pole',{'key':'opisanie','value':'Первое описание события.'})
        seq=self.db.q1('SELECT MAX(seq) n FROM session_snapshots WHERE session_id=?',(sid,))['n']
        self.request('POST',f'/api/session/{sid}/pole',{'key':'opisanie','value':'Позднейшая информация.'})
        original=self.request('POST',f'/api/session/{sid}/otpravit',{'kartochka':self.card()}).json()
        r=self.request('POST',f'/api/training/{sid}/replay',{'seq':seq});self.assertEqual(r.status_code,200,r.text)
        self.assertNotEqual(r.json()['session_id'],sid);self.assertEqual(r.json()['kartochka']['opisanie'],'Первое описание события.')
        self.assertEqual(self.request('GET',f'/api/session/{sid}/razbor').json()['original_itog'],original)

    def test_12_teacher_event_permissions(self):
        sid=self.dds();path=f'/api/training/{sid}/intervene'
        self.assertEqual(self.request('POST',path,{'type':'zaversheno','text':'Работы завершены по сценарию.'},uid=3).status_code,409)
        self.assertEqual(self.request('POST',path,{'type':'notice','text':'Получены дополнительные сведения.'},uid=5).status_code,403)
        self.assertEqual(self.request('POST',path,{'type':'notice','text':'Получены дополнительные сведения.'},uid=3).status_code,200)
        self.assertIn('Получены',self.request('GET',f'/api/training/{sid}/resume').json()['notices'][0]['text'])

    def test_13_material_upload_correction_and_publish(self):
        r=self.client.post('/api/materials',headers={'Authorization':'Bearer '+self.auth.make_token(3)},data={'title':'Памятка'},files={'file':('test.txt','Учебная памятка. Статусы по полученным сведениям.'.encode(),'text/plain')})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(self.request('GET','/api/materials/'+r.json()['id']).status_code,403)
        r=self.request('POST','/api/scenario-tools/flow/correct',{'comment':'Снизить сложность для тренировки','edits':{'slozhnost':1}},uid=3)
        self.assertEqual(r.status_code,200,r.text);sid=r.json()['scenario']['id']
        self.assertEqual(self.request('POST','/api/dds/session',{'scenario_id':sid}).status_code,404)
        self.assertEqual(self.request('POST',f'/api/scenarios/{sid}/validate',{'status':'published'},uid=3).status_code,200)
        self.assertEqual(self.request('POST','/api/dds/session',{'scenario_id':sid}).status_code,200)

    def test_14_slow_model_and_concurrent_field(self):
        sid=self.start();other=self.dds()
        async def delayed(*args,**kwargs):await asyncio.sleep(.35);return 'Принял информацию.'
        with patch('api.caller.otvet',side_effect=delayed):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future=pool.submit(self.request,'POST',f'/api/session/{sid}/replika',{'tekst':'Продолжайте рассказ.'})
                time.sleep(.06);t=time.monotonic();r=self.status(other,'prinyata');elapsed=time.monotonic()-t
                self.request('POST',f'/api/session/{sid}/pole',{'key':'opisanie','value':'Ручной ввод во время ответа модели.'})
                self.assertEqual(future.result().status_code,200)
        self.assertEqual(r.status_code,200);self.assertLess(elapsed,.25)
        self.assertEqual(self.state(sid)['kartochka']['opisanie'],'Ручной ввод во время ответа модели.')

if __name__=='__main__':unittest.main()
