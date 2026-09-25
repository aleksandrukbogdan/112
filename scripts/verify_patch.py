"""Single verification entry point. An explicitly requested skipped suite is NOT success."""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def contracts(source):
    hashes={}
    for file,names in {"api/main.py":["v_asr","v_tts","voice_health"],
                       "api/caller.py":None,"web/app.js":["toggleMic","send"],
                       "web/dds.js":["micTo","speak","ddsPlay"]}.items():
        text=source(file)
        if names is None:hashes[file]=hashlib.sha256(text.encode()).hexdigest();continue
        if file.endswith(".py"):
            nodes={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(text).body
                   if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        else:
            starts=list(re.finditer(r"^(?:async )?function (\w+)\(",text,re.M))
            nodes={m.group(1):text[m.start():starts[i+1].start() if i+1<len(starts) else len(text)].strip()
                   for i,m in enumerate(starts)}
        for name in names:
            if name not in nodes:raise ValueError(f"Missing voice boundary {file}:{name}")
            hashes[file+":"+name]=hashlib.sha256(nodes[name].encode()).hexdigest()
    return hashes

def run_suite(directory,pattern="test*.py",required=False):
    suite=unittest.TestLoader().discover(str(ROOT/directory),pattern=pattern,top_level_dir=str(ROOT/directory))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful() and (not required or result.testsRun>0 and not result.skipped)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--integration",action="store_true")
    p.add_argument("--postgres",action="store_true")
    p.add_argument("--skip-js",action="store_true",help="Explicitly omit Node checks, e.g. inside Python-only app image")
    p.add_argument("--write-contract",metavar="GIT_REF",help="Maintainer only: record original voice boundaries")
    args=p.parse_args()
    if args.write_contract:
        def read(file):return subprocess.check_output(["git","show",f"{args.write_contract}:{file}"],cwd=ROOT,text=True)
        (ROOT/"tests/voice_contract.json").write_text(json.dumps(contracts(read),indent=2)+"\n")
        return
    os.chdir(ROOT);sys.path.insert(0,str(ROOT))
    ok=run_suite("tests")
    for folder in ("api","scripts","tests"):
        for path in (ROOT/folder).rglob("*.py"):
            try:ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
            except SyntaxError as e:print(e);ok=False
    expected=json.loads((ROOT/"tests/voice_contract.json").read_text())
    actual=contracts(lambda file:(ROOT/file).read_text())
    changed=[k for k in expected if expected[k]!=actual.get(k)]
    if changed:
        print("Voice boundaries differ (may be colleague's intended changes; review manually):",changed)
        ok=False
    else:print("Voice boundary hashes: unchanged")
    node=shutil.which("node") or os.environ.get("CODEX_PRIMARY_RUNTIME_NODE")
    if args.skip_js:
        print("JS checks intentionally omitted; run frontend_smoke.cjs and node --check on the workstation.")
    elif node:
        for file in ("app.js","dds.js","insights.js"):
            if subprocess.run([node,"--check",str(ROOT/"web"/file)]).returncode:ok=False
        if subprocess.run([node,str(ROOT/"tests/frontend_smoke.cjs")]).returncode:ok=False
        print("JavaScript syntax check completed; this is NOT a browser test.")
    else:print("JS SYNTAX NOT CHECKED: install Node.js on the test workstation");ok=False
    if args.integration:ok=run_suite("tests/integration","test_api.py",required=True) and ok
    if args.postgres:ok=run_suite("tests/integration","test_postgres.py",required=True) and ok
    print("PASS (selected checks only)" if ok else "NOT ACCEPTED: failed or unavailable requested checks")
    raise SystemExit(0 if ok else 1)

if __name__=="__main__":main()
