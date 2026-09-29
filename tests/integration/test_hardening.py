"""Regression cases for timing, stale delivery, rubric versions and review uncertainty."""
import asyncio,copy,json,time,unittest
from unittest.mock import patch
import test_delivery as delivery

class HardeningTests(delivery.DeliveryTests):
    # Inherits the full acceptance paths; additional cases below are explicit.
    def test_20_stale_manual_sequence(self):
        sid=self.start();path=f'/api/session/{sid}/pole'
        for seq,value in [(2,'Новый адрес'),(1,'Старый адрес'),(2,'Дубликат')]:
            r=self.request('POST',path,{'key':'adres_polny','value':value,'client_id':'test-browser','client_seq':seq});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.state(sid)['kartochka']['adres_polny'],'Новый адрес')

    def test_21_analysis_runs_after_seal(self):
        sid=self.start();self.assertEqual(self.request('POST',f'/api/training/{sid}/analyze',{}).status_code,409)
        original=self.request('POST',f'/api/session/{sid}/otpravit',{'kartochka':self.card()}).json()
        before=self.db.q1('SELECT finished,t_sec,itog FROM sessions WHERE id=?',(sid,))
        async def slow(*args,**kwargs):
            await asyncio.sleep(.05)
            return {'observations':[]},{'status':'ok','model':'test'}
        with patch('api.ai_text.structured',slow):self.assertEqual(self.request('POST',f'/api/training/{sid}/analyze',{}).status_code,200)
        self.assertEqual(before,self.db.q1('SELECT finished,t_sec,itog FROM sessions WHERE id=?',(sid,)))
        self.assertEqual(original,self.request('POST',f'/api/session/{sid}/otpravit',{}).json())

    def test_22_unknown_not_labelled_error(self):
        from api import quality as Q,domain as D
        r=Q.result([D.Fakt('adres','Адрес',None,3,'test',{}),D.Fakt('tip','Тип',True,1,'test',{})],'ops112',critical={'adres'})
        self.assertFalse(r['passed']);self.assertEqual(r['critical_errors'],[]);self.assertEqual(r['pending_criteria'],['adres'])
        self.assertEqual(r['verdikt'],'требуется проверка')

    def test_23_regression_of_reported_stages(self):
        from api import dds,dds_rules
        sid=self.dds();s=self.state(sid);now=time.time()
        s['osnovaniya'].update({k:{'t':now-1,'istochnik':'test'} for k in ('vyezd','pribytie','raboty','zaversheno')})
        s['statusy']=[{'id':str(i),'kod':k,'nazvanie':k,'t':now+i*.01,'kommentariy':'Пожар потушен, помощь пострадавшим оказана.'} for i,k in enumerate(('prinyata','nachalo','pribytie','raboty','pribytie','zaversheno'))]
        it=dds_rules.assess(s);self.assertFalse(next(f['proyden'] for f in it['fakty'] if f['kod']=='po_faktu'))

    def test_24_legacy_evaluator_is_separate(self):
        from api import domain,legacy_domain,session_store as store
        sid=self.start();s=self.state(sid);s['kartochka']=self.card();s['konec']=time.time();s['versions']['rubric']='112-quality-1'
        with patch('api.quality.compare_address',side_effect=AssertionError('new evaluator called')):
            self.assertEqual(domain.ocenit(s),legacy_domain.ocenit(s))
        row=self.db.q1('SELECT data FROM sessions WHERE id=?',(sid,));self.main.reload_live()
        self.assertEqual(row,self.db.q1('SELECT data FROM sessions WHERE id=?',(sid,)))

    def test_25_backup_and_restore_full_state(self):
        from api import db
        sid=self.start();self.request('POST',f'/api/session/{sid}/pole',{'key':'opisanie','value':'До резервирования'})
        backup=db.backup_now();self.request('POST',f'/api/session/{sid}/pole',{'key':'opisanie','value':'После резервирования'})
        db.restore(backup);self.main.reload_live()
        self.assertEqual(self.state(sid)['kartochka']['opisanie'],'До резервирования')
        self.assertTrue(db.q('SELECT * FROM schema_migrations'))

    def test_26_malformed_sources_and_model_safe(self):
        from api import helper,curriculum
        self.assertEqual(helper.validate_model({'proposals':None},[],'ops112'),[])
        bad=copy.deepcopy(self.flow);bad.update(zayavitel=[],events=['broken'],service_profiles=1)
        self.assertGreaterEqual(len(curriculum.validate(bad)),3)
        r=self.request('POST','/api/scenario-tools/flow/correct',{'comment':'Некорректная структура','edits':{'adres_etalon':None}},uid=3)
        self.assertEqual(r.status_code,422,r.text)

    def test_27_frozen_threshold_in_analytics(self):
        sid=self.start();s=self.state(sid);s['_rules']['grading']['threshold']=93
        self.db.ex('UPDATE sessions SET data=? WHERE id=?',(json.dumps(s),sid))
        self.request('POST',f'/api/session/{sid}/otpravit',{'kartochka':self.card()})
        a=self.request('GET','/api/analysis/snapshot?kind=ops112').json()
        row=next(x for x in a['attempts'] if x['id']==sid);self.assertEqual(row['pass_threshold'],93)

    def test_28_adaptive_different_targets(self):
        from api import curriculum
        obs=[{'code':'adres','name':'Адрес','n':1,'rate':0},{'code':'sluzhby','name':'Службы','n':1,'rate':0}]
        rec=curriculum.recommendations(2,'ops112',obs)
        self.assertEqual(len(rec),2);self.assertNotEqual(rec[0]['scenarios'],rec[1]['scenarios'])

if __name__=='__main__':unittest.main()
