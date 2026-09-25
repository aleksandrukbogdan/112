"""python -m scripts.ml_train dataset.json model.json [--approve]

Standard-library baseline. Strict chronological split + unseen learners in holdouts.
If returning learners consume the holdout, collect a new cohort; never switch to a
random row split. JSON contains no text, names, phone numbers or answer keys.
"""
import argparse
import collections
import hashlib
import json
import math
import random
from pathlib import Path
from api.forecast_ml import FEATURES,SCHEMA,sigmoid,predict,raw_logit

def split(rows):
    rows=sorted(rows,key=lambda r:(r["ts"],r["attempt"]))
    if len({r["attempt"] for r in rows})!=len(rows):
        raise ValueError("Exactly one snapshot per attempt required")
    if len(rows)<200: raise ValueError("Need at least 200 completed attempts")
    t1,t2=rows[int(len(rows)*.6)]["ts"],rows[int(len(rows)*.8)]["ts"]
    train=[r for r in rows if r["ts"]<t1]
    seen={r["learner"] for r in train}
    calibration=[r for r in rows if t1<=r["ts"]<t2 and r["learner"] not in seen]
    seen|={r["learner"] for r in calibration}
    test=[r for r in rows if r["ts"]>=t2 and r["learner"] not in seen]
    for name,part in [("train",train),("calibration",calibration),("test",test)]:
        if len(part)<30 or len({r["learner"] for r in part})<10 or {r["y"] for r in part}!={0,1}:
            raise ValueError(f"Insufficient {name}: need 30 attempts, 10 distinct new learners and both outcomes")
    return train,calibration,test

def fit(rows,iterations=1000):
    width=len(rows[0]["x"]); w=[0.]*width; b=0.
    for _ in range(iterations):
        dw=[0.]*width; db=0.
        for row in rows:
            error=sigmoid(b+sum(a*v for a,v in zip(w,row["x"])))-row["y"]
            db+=error
            for j,v in enumerate(row["x"]): dw[j]+=error*v
        b-=.3*db/len(rows)
        w=[a-.3*(d/len(rows)+.001*a) for a,d in zip(w,dw)]
    return {"weights":w,"bias":b}

def metrics(pairs):
    n=len(pairs)
    return {"n":n,"brier":sum((p-y)**2 for p,y in pairs)/n,
            "log_loss":-sum(y*math.log(max(1e-9,p))+(1-y)*math.log(max(1e-9,1-p)) for p,y in pairs)/n,
            "accuracy":sum((p>=.5)==bool(y) for p,y in pairs)/n}

def bootstrap_delta(test,model,base):
    groups=collections.defaultdict(list)
    for r in test: groups[r["learner"]].append((predict(model,r["x"])-r["y"])**2-(base-r["y"])**2)
    keys=sorted(groups); rnd=random.Random(112); differences=[]
    for _ in range(500):
        sample=[v for k in rnd.choices(keys,k=len(keys)) for v in groups[k]]
        differences.append(sum(sample)/len(sample))
    differences.sort()
    return [differences[12],differences[487]]

def train(data,approve=False):
    if data.get("schema")!=SCHEMA: raise ValueError("Incompatible dataset schema")
    rows=data["rows"]
    for r in rows:
        if (r["y"] not in (0,1) or len(r["x"])!=len(FEATURES) or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in r["x"]) or
            not math.isfinite(r["ts"])): raise ValueError("Invalid row")
    learning,calibration,test=split(rows)
    model=fit(learning)
    calibrator=fit([{**r,"x":[raw_logit(model,r["x"])]} for r in calibration])
    model["calibration"]=[calibrator["weights"][0],calibrator["bias"]]
    base=sum(r["y"] for r in learning)/len(learning)
    actual=metrics([(predict(model,r["x"]),r["y"]) for r in test])
    baseline=metrics([(base,r["y"]) for r in test])
    delta=bootstrap_delta(test,model,base)
    eligible=actual["brier"]<baseline["brier"] and delta[1]<0
    model.update(schema=SCHEMA,features=FEATURES,algorithm="logistic-l2+platt-v1",
                 eligible=eligible,approved=bool(approve and eligible),
                 validation={"policy":"time_ordered_new_learners_60_20_20",
                    "train":len(learning),"calibration":len(calibration),"test":len(test),
                    "excluded":len(rows)-len(learning)-len(calibration)-len(test),
                    "model":actual,"constant_train_baseline":baseline,"delta_brier_ci95":delta,
                    "warning":"Exploratory bootstrap on one holdout. Repeated model selection needs a fresh test cohort."})
    return model

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset",type=Path);parser.add_argument("model",type=Path)
    parser.add_argument("--approve",action="store_true",help="Manual approval, only if statistical gate passes")
    args=parser.parse_args()
    raw=args.dataset.read_bytes(); data=json.loads(raw)
    model=train(data,args.approve)
    model["dataset_sha256"]=hashlib.sha256(raw).hexdigest()
    args.model.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.model.with_suffix(".tmp")
    tmp.write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(args.model)
    print(json.dumps({"approved":model["approved"],"eligible":model["eligible"],"validation":model["validation"]},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
