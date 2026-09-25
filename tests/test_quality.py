import os
import unittest
from pathlib import Path
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parents[1] / "data"))
from api import domain as D, quality as Q

class QualityTests(unittest.TestCase):
    def test_address_roles(self):
        for actual in ("Москва, ул. Тверская, дом 12 корпус 1", "дом 1 корпус 12",
                       "Москва, ул. Тверская, дом 1 корпус 12 строение 5", ""):
            self.assertFalse(D.sravnit_adres(actual, "Москва, ул. Тверская, дом 1 корпус 12")["sovpalo"])
        self.assertTrue(D.sravnit_adres("г. Москва, ул. Тверская, д. 1, корп. 12",
                                     "Москва, улица Тверская, дом 1 корпус 12")["sovpalo"])

    def test_critical_gate(self):
        facts = [D.Fakt(k,k,k!='adres',3,'test') for k in ['adres','tip','sluzhby','polnota','rech','normativ']]
        result = Q.result(facts,'ops112')
        self.assertGreater(result['ball'],70)
        self.assertFalse(result['passed'])

    def test_dds_gate(self):
        facts = [D.Fakt(k,k,k not in ('peredacha','zaversheno'),3,'test') for k in
                 ['podtverzhdenie','proverka','peredacha','po_faktu','zaversheno']]
        self.assertFalse(Q.result(facts,'dds')['passed'])

    def test_not_observed(self):
        r=Q.result([D.Fakt('rech','speech',None,2,'test')],'ops112')
        self.assertNotIn('rech',r['navyki'])
        self.assertEqual(r['fakty'][0]['status'],'not_observed')

    def test_expired_forecast(self):
        card=dict.fromkeys(['adres_polny','opisanie','zayavitel_fio','zayavitel_telefon','tip_kod'],'x')
        self.assertEqual(D.prognoz_v_vyzove({'kartochka':card},100)['veroyatnost'],0)

    def test_public_scenario(self):
        p=Q.public_scenario({'id':'x','adres_etalon':'secret','kod_etalon':'answer','_pravda':{}})
        self.assertNotIn('adres_etalon',p)
        self.assertNotIn('kod_etalon',p)

    def test_routes(self):
        self.assertEqual(Q.routes(None,{}),[])
        p={'sluzhby':['101'],'smp':{'est':True},'mvd_pri_pravonarushenii':True}
        self.assertEqual(Q.routes(p,{'postradavshie':'est','pravonarushenie':True}),['101','MVD','SMP'])

if __name__=='__main__': unittest.main()
