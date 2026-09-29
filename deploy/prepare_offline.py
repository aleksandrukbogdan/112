#!/usr/bin/env python3
"""Prepare this application image on a connected host. Existing GPU images/models stay on target."""
import argparse,subprocess,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--target',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
tag='t112-release:2026.09.28-plan';a.output.parent.mkdir(parents=True,exist_ok=True)
subprocess.run(['docker','build','-t',tag,'-f',str(a.target/'Dockerfile'),str(a.target)],check=True)
subprocess.run(['docker','save','-o',str(a.output),tag],check=True)
print('На целевом сервере: docker load -i '+str(a.output));print('Затем install.py --target /data/112 --offline-image '+tag)
