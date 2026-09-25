import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from api import forecast_ml as ml
from scripts import ml_train

class MLTests(unittest.TestCase):
    def test_features_no_answer_key(self):
        s={"kartochka":{"adres_polny":"anything"},"bilet":{"adres_etalon":"SECRET","slozhnost":3}}
        x=ml.features(s,20,75)
        s["bilet"]["adres_etalon"]="OTHER";s["itog"]={"passed":True}
        self.assertEqual(x,ml.features(s,20,75))
        self.assertTrue(all(type(n) in (float,int) for n in x))

    def test_fail_closed_and_deadline(self):
        p={"veroyatnost":.3,"proverka_na_sec":75}
        with patch.dict(os.environ,{"ML_FORECAST_ENABLED":"true","ML_FORECAST_MODEL":"/nonexistent/model.json"}):
            self.assertEqual(ml.forecast({},20,p)["veroyatnost"],.3)
            self.assertEqual(ml.forecast({},80,p)["veroyatnost"],0.)
        with patch.dict(os.environ,{"ML_FORECAST_ENABLED":"false"}):
            self.assertEqual(ml.forecast({},20,p)["ml_status"],"disabled")

    def test_unapproved_model_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"model.json";path.write_text('{"approved":false}')
            with self.assertRaises(ValueError):ml.load_model(path)

    def test_group_time_split(self):
        rows=[{"attempt":str(i),"learner":str(i//2),"ts":float(i),"y":i%2,"x":[0]*5} for i in range(250)]
        a,b,c=ml_train.split(rows)
        for left,right in [(a,b),(b,c),(a,c)]:
            self.assertFalse({r["learner"] for r in left}&{r["learner"] for r in right})
            self.assertLess(max(r["ts"] for r in left),min(r["ts"] for r in right))
        with self.assertRaises(ValueError):ml_train.split(rows+rows[:1])
        with self.assertRaises(ValueError):ml_train.split([{**r,"learner":"same"} for r in rows])

    def test_synthetic_training_not_auto_approved(self):
        rows=[{"attempt":str(i),"learner":str(i//2),"ts":float(i),"y":i%2,
               "x":[.3,float(i%2),.2,.4,.1]} for i in range(250)]
        model=ml_train.train({"schema":ml.SCHEMA,"rows":rows})
        self.assertFalse(model["approved"])
        self.assertLess(model["validation"]["model"]["brier"],model["validation"]["constant_train_baseline"]["brier"])
        # This test establishes implementation sanity, NOT accuracy on real calls.
