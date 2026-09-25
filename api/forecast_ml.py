"""Optional CPU logistic model, JSON only; no pickle, model download or voice dependency.

Default: off. A missing, malformed or unapproved model falls back to the heuristic.
Features describe information available when a forecast is made, never the answer key.
"""
import hashlib
import json
import math
import os
from pathlib import Path

SCHEMA="call-deadline-v1"
FEATURES=["elapsed_ratio","filled_ratio","dialog_turns","difficulty","edits"]
NEEDED=("adres_polny","opisanie","zayavitel_fio","zayavitel_telefon","tip_kod")

def features(s,t,norm):
    return [min(2,max(0,t/max(1,norm))),
            sum(bool(str(s.get("kartochka",{}).get(k,"")).strip()) for k in NEEDED)/len(NEEDED),
            min(1,sum(m.get("kto")=="operator" for m in s.get("dialog",[]))/10),
            min(1,max(0,float(s.get("bilet",{}).get("slozhnost",1)))/5),
            min(1,max(0,s.get("_field_edits",0))/30)]

def sigmoid(z):
    return 1/(1+math.exp(-max(-40,min(40,z))))

def raw_logit(model,x):
    return model["bias"]+sum(w*v for w,v in zip(model["weights"],x))

def predict(model,x):
    a,b=model.get("calibration",[1.,0.])
    return sigmoid(a*raw_logit(model,x)+b)

def load_model(path):
    path=Path(path)
    if path.stat().st_size>1_000_000: raise ValueError("Model file too large")
    raw=path.read_bytes(); m=json.loads(raw)
    if (m.get("schema")!=SCHEMA or m.get("features")!=FEATURES or
        m.get("approved") is not True or m.get("eligible") is not True or
        len(m.get("weights",[]))!=len(FEATURES) or len(m.get("calibration",[]))!=2):
        raise ValueError("Unapproved or incompatible model")
    numbers=m["weights"]+[m["bias"]]+m["calibration"]
    if any(type(n) not in (int,float) or not math.isfinite(n) for n in numbers):
        raise ValueError("Non-finite model parameters")
    return m,hashlib.sha256(raw).hexdigest()

def forecast(s,t,heuristic):
    p=dict(heuristic)
    x=features(s,t,p["proverka_na_sec"])
    p.update(feature_schema=SCHEMA,features=x,feature_names=FEATURES,
             ml_status="disabled",model_id="heuristic-v2")
    if os.environ.get("ML_FORECAST_ENABLED","false").lower()!="true": return p
    if t>=p["proverka_na_sec"]:
        p.update(veroyatnost=0.,ml_status="deadline_elapsed")
        return p
    try:
        model,key=load_model(os.environ.get("ML_FORECAST_MODEL","/app/state/ml/call-model.json"))
        p.update(veroyatnost=round(predict(model,x),6),method="logistic-calibrated-v1",
                 model_id=key,ml_status="active",calibrated=True,
                 label="ML-прогноз срока отправки; не оценка правильности и не допуск")
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        p["ml_status"]="fallback_missing_invalid_or_unapproved"
    return p
