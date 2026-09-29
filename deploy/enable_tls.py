#!/usr/bin/env python3
"""Creates a private local certificate and proxy configuration. Does not touch voice services."""
import argparse,ipaddress,json,os,re,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--target',type=Path,required=True);p.add_argument('--hostname',required=True);p.add_argument('--port',type=int,default=8443);a=p.parse_args();root=a.target.resolve()
if not re.fullmatch(r'[A-Za-z0-9.-]+',a.hostname) or not 1<=a.port<=65535:raise SystemExit('Недопустимый адрес/порт')
folder=root/'state/tls';folder.mkdir(parents=True,exist_ok=True);folder.chmod(0o700)
try:ipaddress.ip_address(a.hostname);san='IP:'+a.hostname
except ValueError:san='DNS:'+a.hostname
if not (folder/'server.key').exists():
 subprocess.run(['openssl','req','-x509','-newkey','rsa:3072','-sha256','-nodes','-days','365','-subj','/CN='+a.hostname,'-addext','subjectAltName='+san,'-keyout',str(folder/'server.key'),'-out',str(folder/'server.crt')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 (folder/'server.key').chmod(0o600)
conf=root/'deploy/nginx.release.conf'
conf.write_text('events {}\nhttp { server { listen 443 ssl; ssl_certificate /tls/server.crt; ssl_certificate_key /tls/server.key; ssl_protocols TLSv1.2 TLSv1.3; client_max_body_size 25m; location / { proxy_pass http://app:8080; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; proxy_read_timeout 250s; } } }\n')
compose={'services':{'tls':{'image':'nginx:1.28-alpine','restart':'unless-stopped','ports':[f'{a.port}:443'],'volumes':[str(folder)+':/tls:ro',str(conf)+':/etc/nginx/nginx.conf:ro'],'depends_on':['app']}}}
(root/'deploy/tls.compose.json').write_text(json.dumps(compose,indent=2))
print('Конфигурация готова. Запуск: docker compose -f docker-compose.yml -f deploy/runtime.compose.json -f deploy/tls.compose.json up -d --no-deps tls')
print('Установите сертификат state/tls/server.crt как доверенный на учебных АРМ или замените парой от вашего УЦ. Адрес: https://'+a.hostname+':'+str(a.port))
