"""Execute the real pure DDS evaluator AST without importing the FastAPI router.

Not an API test: dependency injection, HTTP and voice are checked separately.
"""
import ast
import copy
import re
import unittest
from pathlib import Path
from api import config as C, domain as D

tree=ast.parse((Path(__file__).resolve().parents[1]/"api/dds.py").read_text())
function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="ocenit")
function.decorator_list=[]
scope={"D":D,"C":C,"re":re,"POST_RU":{"net":"нет","est":"есть","ne_na_meste":"не на месте"}}
exec(compile(ast.Module(body=[function],type_ignores=[]),"dds-scoring-extraction","exec"),scope)

class DDSRulesTests(unittest.TestCase):
    def state(self):
        return {"kartochka":{"_oshibka":None},"nachalo":0.,
            "statusy":[{"kod":"prinyata","t":1.,"nazvanie":"Принята","kommentariy":""}],
            "zamechaniya":[],"osnovaniya":{"postuplenie":{"t":0.}},"narusheniya_rechi":[],"zvonki":[]}

    def test_accept_only_not_passed(self):
        result=scope["ocenit"](self.state())
        self.assertFalse(result["passed"])
        self.assertIn("peredacha",result["critical_errors"])
        self.assertIn("zaversheno",result["critical_errors"])
        self.assertIsNone(next(f for f in result["fakty"] if f["kod"]=="rech")["proyden"])

    def test_false_status_without_evidence(self):
        s=self.state();s["statusy"].append({"kod":"zaversheno","t":2,"nazvanie":"Завершено","kommentariy":"готово"})
        result=scope["ocenit"](s)
        self.assertIn("po_faktu",result["critical_errors"])

    def test_scoring_uses_frozen_time_limit(self):
        s=self.state();s["statusy"][0]["t"]=31.
        s["_rules"]={"dds":copy.deepcopy(C.DDS)}
        s["_rules"]["dds"]["podtverzhdenie_sec"]=40
        result=scope["ocenit"](s)
        self.assertNotIn("podtverzhdenie",result["critical_errors"])
