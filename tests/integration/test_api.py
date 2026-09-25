"""Integration tests. Always use a fresh temporary SQLite DB, never production state."""
import copy
import importlib.util
import json
import os
import secrets
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing=[m for m in ("fastapi","httpx","pydantic","multipart","openpyxl","reportlab") if importlib.util.find_spec(m) is None]
        if missing: raise unittest.SkipTest("Missing application dependencies: "+", ".join(missing))
        cls.tmp=tempfile.TemporaryDirectory()
        root=Path(__file__).resolve().parents[2]
        cls.env=patch.dict(os.environ,{"DATABASE_URL":"","DATA_DIR":str(root/"data"),
            "DB_PATH":cls.tmp.name+"/api.db","BACKUP_DIR":cls.tmp.name+"/backup",
            "SECRET_KEY":secrets.token_hex(32),"LLM_ENABLED":"false","ML_FORECAST_ENABLED":"false"})
        cls.env.start()
        from fastapi.testclient import TestClient
        from api import main,db,auth,config as C
        cls.main,cls.db,cls.auth,cls.C=main,db,auth,C
        if db._conn:db._conn.close()
        db._conn=None;db.PG=False
        db.DB_PATH=Path(cls.tmp.name)/"api.db";db.BACKUP_DIR=Path(cls.tmp.name)/"backup"
        C.DATA=root/"data";C.LLM_ON=False
        db.conn()
        for uid,role in [(1,"trainee"),(2,"trainee"),(3,"teacher"),(4,"admin")]:
            db.ex("INSERT INTO users(id,login,name,role,pw_hash,group_id) VALUES(?,?,?,?,?,?)",
                  (uid,"test"+str(uid),"Test user",role,"not-a-real-password",1))
        cls.bg=patch.object(db,"start_backup_thread");cls.bg.start()
        cls.client=TestClient(main.app);cls.client.__enter__()
        cls.sc=copy.deepcopy(C.load("bilety")[0]);cls.sc["id"]="patch-test-scenario"
        cls.code=next(k for k,v in C.load("index").items() if v["g"]==cls.sc["gruppa"])
        cls.sc["kod_etalon"]=cls.code;cls.sc.pop("pr1_etalon",None)
        db.ex("INSERT INTO scenarios(id,source,status,data) VALUES(?,?,?,?)",
              (cls.sc["id"],"test","published",json.dumps(cls.sc)))

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None);cls.bg.stop()
        cls.db._conn.close();cls.db._conn=None
        cls.env.stop();cls.tmp.cleanup()

    def request(self,method,path,body=None,uid=1):
        kw={"headers":{"Authorization":"Bearer "+self.auth.make_token(uid)}}
        if body is not None:kw["json"]=body
        return self.client.request(method,path,**kw)

    def start(self):
        r=self.request("POST","/api/session",{"scenario_id":self.sc["id"]})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()["session_id"]

    def card(self):
        from api.quality import routes
        c={"adres_polny":self.sc["adres_etalon"],"opisanie":"Произошло учебное событие по адресу",
           "zayavitel_fio":"Тест","zayavitel_telefon":"79991112233","tip_kod":self.code,"tip_itog":"UI display label",
           "postradavshie":"net","pravonarushenie":False}
        c["sluzhby"]=routes(self.C.load("index")[self.code],c)
        return c

    def test_no_live_answers_and_foreign_access(self):
        sid=self.start()
        self.assertEqual(self.request("GET",f"/api/session/{sid}/razbor").status_code,409)
        self.assertEqual(self.request("POST",f"/api/session/{sid}/otpravit",{},uid=2).status_code,403)
        for sc in self.request("GET","/api/scenarios").json():
            self.assertFalse({"adres_etalon","kod_etalon","zayavitel","podskazki"}&set(sc))

    def test_critical_wrong_address(self):
        sid=self.start();card=self.card();card["adres_polny"]="Чужая улица, дом 9999"
        r=self.request("POST",f"/api/session/{sid}/otpravit",{"kartochka":card})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()["passed"]);self.assertIn("adres",r.json()["critical_errors"])

    def test_parallel_completion_exactly_once(self):
        sid=self.start();body={"kartochka":self.card()}
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:self.request("POST",f"/api/session/{sid}/otpravit",body),range(4)))
        self.assertTrue(all(r.status_code==200 for r in responses),[r.text for r in responses])
        self.assertTrue(all(r.json()==responses[0].json() for r in responses))
        self.assertEqual(self.db.q1("SELECT COUNT(*) n FROM completion_receipts WHERE session_id=?",(sid,))["n"],1)
        self.assertEqual(self.db.q1("SELECT COUNT(*) n FROM session_events WHERE session_id=? AND type='session_completed'",(sid,))["n"],1)
        self.main.reload_live()
        self.assertEqual(self.request("POST",f"/api/session/{sid}/otpravit",body).json(),responses[0].json())

    def test_dds_accept_only_and_hidden_card(self):
        r=self.request("POST","/api/dds/session",{"scenario_id":self.sc["id"]})
        self.assertEqual(r.status_code,200,r.text)
        sid=r.json()["session_id"]
        self.assertNotIn("_pravda",r.json()["kartochka"])
        self.assertNotIn("_oshibka",r.json()["kartochka"])
        self.request("POST",f"/api/dds/{sid}/status",{"status":"prinyata"})
        r=self.request("POST",f"/api/dds/{sid}/finish",{})
        self.assertEqual(r.status_code,200,r.text)
        self.assertFalse(r.json()["passed"])
        self.assertIn("peredacha",r.json()["critical_errors"])
        self.assertEqual(self.request("POST",f"/api/dds/{sid}/finish",{}).json(),r.json())

    def test_review_permissions_and_immutable_original(self):
        sid=self.start();card=self.card();card["adres_polny"]="неверный адрес"
        original=self.request("POST",f"/api/session/{sid}/otpravit",{"kartochka":card}).json()
        body={"changes":{"adres":True},"reason":"Проверено преподавателем на учебном примере"}
        self.assertEqual(self.request("POST",f"/api/session/{sid}/review",body).status_code,403)
        self.assertEqual(self.request("POST",f"/api/session/{sid}/review",body,uid=4).status_code,403)
        r=self.request("POST",f"/api/session/{sid}/review",body,uid=3)
        self.assertEqual(r.status_code,200,r.text)
        after=self.request("GET",f"/api/session/{sid}/razbor").json()
        self.assertEqual(after["original_itog"],original)
        self.assertTrue(next(f for f in after["itog"]["fakty"] if f["kod"]=="adres")["proyden"])
        self.assertEqual(self.request("POST",f"/api/session/{sid}/otpravit",{}).json(),original)

    def test_invalid_fields_and_assignment(self):
        sid=self.start()
        self.assertEqual(self.request("POST",f"/api/session/{sid}/pole",{"key":"_pravda","value":"x"}).status_code,422)
        self.assertEqual(self.request("POST","/api/session",{"scenario_id":self.sc["id"],"assignment_id":999999}).status_code,403)

    def test_frozen_rules(self):
        sid=self.start()
        old=self.main.LIVE[sid]["_rules"]["norm"]["kartochka_sec"]
        with patch.dict(self.C.NORM,{"kartochka_sec":old+500}):
            r=self.request("GET",f"/api/session/{sid}/prognoz")
        self.assertEqual(r.json()["proverka_na_sec"],old)

    def test_report_and_script_smoke(self):
        sid=self.start();self.request("POST",f"/api/session/{sid}/otpravit",{"kartochka":self.card()})
        token=self.auth.make_token(1)
        r=self.client.get(f"/api/otchet/zanyatie/{sid}.pdf?token={token}")
        self.assertEqual(r.status_code,200,r.text[:100] if r.status_code!=200 else "")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertEqual(self.client.get("/static/insights.js").status_code,200)

if __name__=="__main__":unittest.main()
