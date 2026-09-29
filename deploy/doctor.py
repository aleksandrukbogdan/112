#!/usr/bin/env python3
"""Read-only installation diagnostics; never prints credentials."""
import argparse,json,sys,time,urllib.request
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8080');p.add_argument('--output',default='diagnostics-112.json');a=p.parse_args()
 results=[]
 for endpoint in ('/readyz','/health','/static/charts.js','/static/workspace.js'):
  start=time.monotonic()
  try:
   with urllib.request.urlopen(a.url.rstrip('/')+endpoint,timeout=20) as r:data=r.read();status=r.status
   content=json.loads(data) if endpoint in ('/readyz','/health') else {'bytes':len(data)}
   results.append({'path':endpoint,'ok':status==200,'seconds':round(time.monotonic()-start,3),'data':content})
  except Exception as exc:results.append({'path':endpoint,'ok':False,'error':type(exc).__name__})
 Path(a.output).write_text(json.dumps(results,ensure_ascii=False,indent=2))
 for row in results:print(('OK' if row['ok'] else 'FAIL')+' '+row['path'])
 health=next((x.get('data',{}) for x in results if x['path']=='/health'),{})
 if health:print('Компоненты: '+json.dumps(health,ensure_ascii=False))
 print('Протокол: '+a.output);return 0 if all(x['ok'] for x in results) else 1
if __name__=='__main__':sys.exit(main())
