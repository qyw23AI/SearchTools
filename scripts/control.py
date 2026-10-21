#!/usr/bin/env python3
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from searchtools.core import config,CONFIG,gpu_busy


def ready(url):
    try:
        with urlopen(url,timeout=2) as r:return r.status==200
    except Exception:return False


def start(cfg):
    services=[('searchtools-ollama.service','http://127.0.0.1:11434/api/version'),
              ('searchtools-deeptutor.service',cfg['deeptutor_url']),
              ('searchtools-gpt.service',cfg['gpt_url']),
              ('searchtools.service',f'http://127.0.0.1:{cfg["port"]}/')]
    for service,url in services:
        if not ready(url):
            subprocess.run(['systemctl','--user','start','--no-block',service],check=True)
    for _ in range(30):
        if ready(services[-1][1]):return
        time.sleep(1)
    raise RuntimeError('工作区服务未启动，请运行 ./scripts/searchtools doctor')


def run_engine(cfg,engine):
    env=os.environ.copy()
    if engine=='workspace':
        cmd=['/usr/bin/python3','-m','searchtools.server'];cwd=ROOT
    elif engine=='ollama':
        env.update(OLLAMA_HOST='127.0.0.1:11434',OLLAMA_MODELS=cfg['models'],OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_NUM_PARALLEL='1')
        cmd=[cfg['ollama_bin'],'serve'];cwd=ROOT
    elif engine=='gpt':
        cmd=[cfg['gpt_python'],'-m','uvicorn','main:app','--host','127.0.0.1','--port','8002'];cwd=cfg['gpt_root']
    else:
        env['DEEPTUTOR_HOME']=cfg['deeptutor_root']
        env['PATH']=cfg['node_bin']+':'+str(Path(cfg['deeptutor_bin']).parent)+':'+env['PATH']
        cmd=[cfg['deeptutor_bin'],'start','--home',cfg['deeptutor_root'],'--no-browser'];cwd='/tmp'
    os.chdir(cwd)
    os.execvpe(cmd[0],cmd,env)


def doctor(cfg):
    checks={}
    for name,url in [('workspace',f'http://127.0.0.1:{cfg["port"]}/'),('deeptutor',cfg['deeptutor_url']),('gpt',cfg['gpt_url']),('ollama','http://127.0.0.1:11434/api/tags')]:
        checks[name]=ready(url)
        print(('✓ ' if checks[name] else '✗ ')+name+' '+url)
    for name in ('gpt','paperqa'):
        r=subprocess.run([cfg[name+'_python'],'-c','import '+('gpt_researcher' if name=='gpt' else 'paperqa')],capture_output=True,text=True)
        print(('✓ ' if r.returncode==0 else '✗ ')+name+' Python adapter')
        checks[name+'_import']=r.returncode==0
    busy,reason=gpu_busy()
    print('GPU: '+reason)
    try:
        with urlopen('http://127.0.0.1:11434/api/tags',timeout=3) as response:
            models=[m['name'] for m in json.load(response)['models']]
        print('Models: '+', '.join(models))
        checks['models']=all(m in models for m in ('qwen3:8b','qwen3-embedding:4b'))
    except Exception:
        checks['models']=False
    print('Config: '+str(CONFIG))
    print('Data: '+cfg['data'])
    print('Logs: journalctl --user -u searchtools.service -n 80')
    return 0 if all(checks.values()) else 1


def main():
    parser=argparse.ArgumentParser(description='SearchTools 桌面科研工作区')
    parser.add_argument('action',nargs='?',default='open',choices=['open','start','doctor','download-models','run-engine'])
    parser.add_argument('engine',nargs='?',choices=['workspace','gpt','deeptutor','ollama'])
    args=parser.parse_args()
    cfg=config()
    if args.action=='run-engine':
        if not args.engine:parser.error('需要指定引擎')
        return run_engine(cfg,args.engine)
    if args.action=='doctor':return doctor(cfg)
    if args.action=='download-models':
        os.execv(sys.executable,[sys.executable,str(ROOT/'scripts/download_models.py')])
    start(cfg)
    if args.action=='open':
        os.execv('/usr/bin/python3',['/usr/bin/python3','-m','searchtools.desktop'])
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,RuntimeError) as e:
        print(f'SearchTools: {e}',file=sys.stderr)
        raise SystemExit(1)
