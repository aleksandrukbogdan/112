"""Export numeric snapshots from the configured DB. No mutation of attempt data.

python -m scripts.ml_export /safe/path/dataset.json
Run inside the app environment. Different exports use different pseudonyms.
"""
import argparse
import hashlib
import json
import secrets
from pathlib import Path
from api import db
from api.forecast_ml import SCHEMA

def export_rows():
    salt=secrets.token_bytes(32); selected={}
    for row in db.q("SELECT f.*,s.user_id,s.kind session_kind FROM forecasts f JOIN sessions s ON s.id=f.subject "
                    "WHERE f.kind='call' AND f.fakt IS NOT NULL AND s.state='done' ORDER BY f.created,f.id"):
        if row["subject"] in selected or row["session_kind"]!="ops112": continue
        p=json.loads(row["data"]);fact=json.loads(row["fakt"])
        if p.get("feature_schema")!=SCHEMA or p["t_prognoza_sec"]>=p["proverka_na_sec"]: continue
        learner=hashlib.sha256(salt+str(row["user_id"]).encode()).hexdigest()[:24]
        attempt=hashlib.sha256(salt+row["subject"].encode()).hexdigest()[:24]
        selected[row["subject"]]={"learner":learner,"attempt":attempt,"ts":row["created"],
                                   "x":p["features"],"y":int(bool(fact["uspel"]))}
    return {"schema":SCHEMA,"selection":"first_predeadline_per_attempt","rows":list(selected.values())}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("output",type=Path)
    args=parser.parse_args();data=export_rows()
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Exported {len(data['rows'])} attempts; pseudonymous data still needs access control.")

if __name__=="__main__":main()
