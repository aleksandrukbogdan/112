#!/usr/bin/env python3
"""Проверка целостности config/ до запуска контейнеров."""
import sys, glob, os
from pathlib import Path
try:
    import yaml
except ImportError:
    sys.exit("нужен pyyaml: pip install pyyaml")

ROOT = Path(__file__).parent.parent / "config"
ok = True

def err(m):
    global ok; ok = False; print(f"  ОШИБКА: {m}")

files = sorted(glob.glob(str(ROOT / "**" / "*.yaml"), recursive=True))
print(f"config/: {len(files)} файлов")
data = {}
for f in files:
    rel = os.path.relpath(f, ROOT)
    try:
        data[rel] = yaml.safe_load(open(f, encoding="utf-8"))
    except Exception as e:
        err(f"{rel}: {e}")

need = ["normativy.yaml", "routes.yaml", "ukio.yaml", "rechevye_pravila.yaml",
        "protocols/_base.yaml", "regions/so.yaml"]
for n in need:
    if n not in data: err(f"нет обязательного файла {n}")

if "routes.yaml" in data:
    r = data["routes.yaml"]["routes"]
    print(f"  маршрутов: {len(r)}, со спецчастью УКИО: {sum(1 for x in r if 'ukio_chast' in x)}")
    if "ukio.yaml" in data:
        spec = set(data["ukio.yaml"]["special"])
        for x in r:
            if x.get("ukio_chast") and x["ukio_chast"] not in spec:
                err(f"маршрут {x['kod']} ссылается на несуществующую спецчасть {x['ukio_chast']}")

# классификаторы, на которые ссылаются поля УКИО
if "ukio.yaml" in data:
    u = data["ukio.yaml"]; refs = set()
    for sec in ("uchol", "obshchaya"):
        for g in u[sec].values():
            for f in g:
                if f.get("classifier"): refs.add(f["classifier"])
    for s in u["special"].values():
        for f in s["polya"]:
            if f.get("classifier"): refs.add(f["classifier"])
        for sp in s.get("spiski", {}).values():
            for f in sp["polya"]:
                if f.get("classifier"): refs.add(f["classifier"])
    have = {Path(k).stem for k in data if k.startswith("classifiers/")}
    miss = refs - have
    print(f"  классификаторов: есть {len(have)}, нужно {len(refs)}")
    if miss: print(f"  НЕТ ФАЙЛОВ (поля будут без списка): {', '.join(sorted(miss))}")

zg = [k for k, v in data.items() if isinstance(v, dict)
      and str(v.get("status", "")).upper() == "ZAGLUSHKA"]
if zg:
    print(f"  ЗАГЛУШКИ ({len(zg)}): {', '.join(zg)}")
    for k in zg:
        print(f"    → заменить на: {data[k].get('zamenit_na', '?')}")

print("ОК" if ok else "ЕСТЬ ОШИБКИ")
sys.exit(0 if ok else 1)
