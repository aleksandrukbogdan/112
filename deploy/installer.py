#!/usr/bin/env python3
"""Atomic overlay installer. Python 3.10+, Docker Compose v2. No model download."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
PROTECTED={'.env','docker-compose.yml','compose.yml','requirements.txt'}

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def canonical(p):return hashlib.sha256(p.read_text(encoding='utf-8').replace('\r\n','\n').rstrip().encode()).hexdigest()
def write_json(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    p.chmod(0o600)
def command(args,cwd,capture=False):
    proc=subprocess.run([str(x) for x in args],cwd=cwd,text=True,stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else None)
    if proc.returncode:raise RuntimeError('Ошибка команды: '+str(args[0])+' (подробности в журнале Docker; секретные параметры не выводятся)')
    return proc.stdout.strip() if capture else ''
def protected(name):
    p=Path(name)
    return p.is_absolute() or '..' in p.parts or name in PROTECTED or p.parts[0] in ('state','models','tts','asr','voice') or p.name.startswith('.env')
def preflight(target,manifest,allow=False):
    conflicts=[]
    if not target.is_dir():raise RuntimeError('Каталог проекта не найден')
    for name,entry in manifest['files'].items():
        if protected(name):raise RuntimeError('Недопустимый путь в пакете: '+name)
        src=ROOT/'payload'/name;dst=target/name
        if not src.is_file() or digest(src)!=entry['sha256']:raise RuntimeError('Повреждён пакет: '+name)
        if dst.is_symlink() or any(p.is_symlink() for p in dst.parents if p!=target and target in p.parents):raise RuntimeError('Символьная ссылка в изменяемом пути: '+name)
        if dst.exists() and digest(dst)!=entry['sha256']:
            if not entry.get('baseline_canonical') or canonical(dst)!=entry['baseline_canonical']:conflicts.append(name)
    if conflicts and not allow:raise RuntimeError('Файлы отличаются от выгрузки 14: '+', '.join(conflicts)+' . Сверьте изменения. Для осознанной замены с копией есть --allow-local-changes.')
    return conflicts

def copy_payload(target,backup,manifest):
    before={}
    for name in manifest['files']:
        src=ROOT/'payload'/name;dst=target/name;old=backup/'files'/name
        before[name]=dst.exists()
        if dst.exists():old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dst,old)
    write_json(backup/'files-before.json',before)
    for name in manifest['files']:
        dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True)
        tmp=dst.with_name(dst.name+'.t112-new');shutil.copy2(ROOT/'payload'/name,tmp);os.replace(tmp,dst)

def restore_files(target,backup):
    for name,existed in json.loads((backup/'files-before.json').read_text()).items():
        if protected(name):raise RuntimeError('Недопустимый путь отката')
        dst=target/name
        if existed:shutil.copy2(backup/'files'/name,dst)
        else:dst.unlink(missing_ok=True)

def main():
    p=argparse.ArgumentParser(description='Обновление 112 / ДДС поверх выгрузки 14')
    p.add_argument('--target',type=Path,required=True);p.add_argument('--check',action='store_true')
    p.add_argument('--files-only',action='store_true',help='Только файлы; не мигрирует БД и не запускает сервисы')
    p.add_argument('--allow-local-changes',action='store_true');p.add_argument('--compose-file',action='append',type=Path)
    p.add_argument('--app-service',default='app');p.add_argument('--workers',type=int,choices=range(1,9),default=1)
    p.add_argument('--offline-image',help='Заранее загруженный образ этой поставки вместо сборки')
    p.add_argument('--rollback',type=Path,help='Путь к резервной копии кода; БД автоматически не откатывается')
    p.add_argument('--timeout',type=int,default=180)
    a=p.parse_args();target=a.target.resolve()
    if a.rollback:
        backup=a.rollback.resolve();meta=json.loads((backup/'deployment.json').read_text())
        if str(target)!=meta['target']:raise RuntimeError('Резервная копия относится к другому каталогу')
        compose=meta.get('compose',[])
        if compose:command(compose+['stop',meta['app_service']],target)
        restore_files(target,backup)
        if compose:write_json(target/'deploy/runtime.compose.json',json.loads((backup/'rollback.compose.json').read_text()))
        if compose:command(compose+['-f',str(backup/'rollback.compose.json'),'up','-d','--no-deps','--no-build',meta['app_service']],target)
        print('Код и прежний образ восстановлены. Данные БД сохранены; путь к копии БД в deployment.json.');return
    manifest=json.loads((ROOT/'manifest.json').read_text());conflicts=preflight(target,manifest,a.allow_local_changes)
    compose_files=a.compose_file or [next((target/x for x in ('docker-compose.yml','compose.yml','compose.yaml') if (target/x).is_file()),target/'docker-compose.yml')]
    compose=['docker','compose','--project-directory',str(target)]
    for f in compose_files:compose+=['-f',str(f.resolve())]
    if not a.files_only:
        if not all(x.is_file() for x in compose_files):raise RuntimeError('Не найден исходный Compose')
        command(['docker','compose','version'],target,True)
        cid=command(compose+['ps','-q',a.app_service],target,True)
        if not cid:raise RuntimeError('Приложение должно быть запущено: нужны фактические настройки, образ и резервная копия БД')
        inspected=json.loads(command(['docker','inspect',cid],target,True))[0]
        actual=dict(x.split('=',1) for x in inspected['Config'].get('Env',[]) if '=' in x)
    if a.check:
        print(f"Проверка пройдена: {len(manifest['files'])} файлов; локальных расхождений: {len(conflicts)}. Ничего не изменено.");return
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')
    backup=target/'state'/'upgrade-backups'/(stamp+'-'+manifest['release']);backup.mkdir(parents=True,exist_ok=False);backup.chmod(0o700)
    meta={'release':manifest['release'],'target':str(target),'app_service':a.app_service,'compose':[] if a.files_only else compose,'conflicts':conflicts,'database_backup':None}
    write_json(backup/'deployment.json',meta)
    if a.files_only:
        copy_payload(target,backup,manifest);print('Файлы обновлены. Контейнеры и БД не изменялись. Копия: '+str(backup));return
    # Retain actual environment and mounts, including separately configured ML and LLM.
    env={k:v for k,v in actual.items() if not k.startswith(('PYTHON_','PIP_')) and k not in ('PATH','LANG','HOME','HOSTNAME')}
    mounts=[]
    for m in inspected.get('Mounts',[]):
        if m['Type'] not in ('bind','volume'):continue
        mounts.append({'type':m['Type'],'source':m.get('Name') if m['Type']=='volume' else m['Source'],'target':m['Destination'],'read_only':not m['RW']})
    if any(m['type']=='volume' for m in mounts):
        raise RuntimeError('Использован именованный том. Укажите Compose с этим томом; данная автоматическая поставка ожидает bind-mount state/app. Файлы ещё не изменены.')
    oldtag='t112-before:'+stamp.lower();command(['docker','image','tag',inspected['Image'],oldtag],target)
    base={'environment':env,'volumes':mounts}
    old={'services':{a.app_service:{**base,'image':oldtag,'command':inspected['Config'].get('Cmd')}}}
    write_json(backup/'rollback.compose.json',old)
    destination=target/'deploy'/'runtime.compose.json'
    runtime={'services':{a.app_service:{**base,'image':a.offline_image or ('t112-release:'+manifest['release']),
        'command':['uvicorn','api.main:app','--host','0.0.0.0','--port','8080','--workers',str(a.workers)]}}}
    applied=False;stopped=False
    try:
        copy_payload(target,backup,manifest);applied=True
        # This generated file contains existing configuration; it is private and never distributed.
        write_json(destination,runtime)
        newcompose=compose+['-f',str(destination)]
        if a.offline_image:command(['docker','image','inspect',a.offline_image],target,True)
        else:command(newcompose+['build',a.app_service],target)
        command(compose+['stop',a.app_service],target);stopped=True
        oldcompose=compose+['-f',str(backup/'rollback.compose.json')]
        code="from api import db; print(db.backup_now())"
        meta['database_backup']=command(oldcompose+['run','--rm','--no-deps','-T',a.app_service,'python','-c',code],target,True).splitlines()[-1]
        write_json(backup/'deployment.json',meta)
        command(newcompose+['up','-d','--no-deps','--no-build',a.app_service],target)
        deadline=time.monotonic()+a.timeout
        while True:
            try:
                command(newcompose+['exec','-T',a.app_service,'python','-c',"import urllib.request,json; d=json.load(urllib.request.urlopen('http://127.0.0.1:8080/readyz',timeout=3)); assert d.get('ok') is True"],target,True)
                break
            except RuntimeError:
                if time.monotonic()>deadline:raise RuntimeError('Новая сборка не прошла проверку /readyz')
                time.sleep(2)
        meta['status']='ready';meta['runtime_compose']=str(destination);write_json(backup/'deployment.json',meta)
        print('Обновление запущено. Резервная копия: '+str(backup))
        print('Для последующих команд добавляйте -f deploy/runtime.compose.json к вашему Compose.')
        print('Проверка моделей: python3 deploy/doctor.py --url http://127.0.0.1:8080')
    except BaseException:
        if stopped:
            try:command(compose+['stop',a.app_service],target)
            except Exception:pass
        if applied:
            restore_files(target,backup)
            write_json(destination,old)
        if stopped:command(compose+['-f',str(backup/'rollback.compose.json'),'up','-d','--no-deps','--no-build',a.app_service],target)
        meta['status']='rolled_back';write_json(backup/'deployment.json',meta)
        print('Ошибка установки. Прежний код/образ восстановлены; БД сохранена. Копия: '+str(backup),file=sys.stderr)
        raise

if __name__=='__main__':
    try:main()
    except Exception as e:print('ОШИБКА: '+str(e),file=sys.stderr);sys.exit(1)
