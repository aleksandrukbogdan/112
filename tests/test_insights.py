import json
import time
from api import insights, quality as Q, domain as D, db
from support import DatabaseCase


class InsightsTests(DatabaseCase):
    # Shared temporary SQLite fixture; these tests do not contact a running service.
    def add_attempt(self,sid,kind,facts,rubric=None):
        result=Q.result(facts,kind)
        if rubric: result["rubric_version"]=rubric
        db.ex("INSERT INTO sessions(id,user_id,scenario_id,kind,state,data,itog,finished) VALUES(?,?,?,?,?,?,?,?)",
              (sid,1,"example",kind,"done",'{}',json.dumps(result),time.time()))

    def test_profile_separates_legacy_and_modes(self):
        self.add_attempt("a","ops112",[D.Fakt("adres","Адрес",False,3,"test")])
        self.add_attempt("b","dds",[D.Fakt("rech","Речь",True,1,"test")])
        self.add_attempt("c","ops112",[D.Fakt("adres","Адрес",True,3,"test")],"legacy")
        a=insights.profile(1,"ops112")
        self.assertEqual((a["n"],a["legacy_excluded"]),(1,1))
        self.assertEqual(next(s for s in a["skills"] if s["code"]=="rech")["n"],0)
        self.assertEqual(a["passed"],0)

    def test_one_forecast_per_attempt(self):
        for t,p in [(20,.2),(30,.9),(80,1.)]:
            db.ex("INSERT INTO forecasts(kind,subject,created,data,fakt) VALUES(?,?,?,?,?)",
                  ("call","a",t,json.dumps({"veroyatnost":p,"t_prognoza_sec":t,"proverka_na_sec":75}),'{"uspel":true}'))
        metric=insights.forecast_metrics({"a"})
        self.assertEqual(metric["n"],1)
        self.assertEqual(metric["brier"],.64)

    def test_wilson_bounds(self):
        self.assertIsNone(insights.interval(0,0))
        self.assertLess(insights.interval(3,3)[0],.8)
