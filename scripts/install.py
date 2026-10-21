#!/usr/bin/env python3
"""Idempotent Ubuntu installer. Private configuration and research data stay outside Git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
HOME=Path.home()
CONF=Path(os.environ.get('SEARCHTOOLS_CONFIG',HOME/'.config/searchtools/config.json'))
RUNTIME=Path(os.environ.get('SEARCHTOOLS_RUNTIME',HOME/'SearchTools-runtime'))


def run(*args,**kwargs):
    print('→', ' '.join(str(a) for a in args),flush=True)
    subprocess.run([str(a) for a in args],check=True,**kwargs)


def download(url,dest,sha=None):
    dest=Path(dest)
    dest.parent.mkdir(parents=True,exist_ok=True)
    part=dest.with_name(dest.name+'.download')
    if not dest.exists():
        run('curl','-fL','--retry','5','--retry-all-errors','--connect-timeout','30','-C','-','-o',part,url)
        if sha and hashlib.sha256(part.read_bytes()).hexdigest()!=sha:
            part.unlink()
            raise RuntimeError('下载校验失败，请重试：'+url)
        part.replace(dest)
    if sha and hashlib.sha256(dest.read_bytes()).hexdigest()!=sha:
        dest.unlink()
        raise RuntimeError('缓存校验失败，已移除损坏缓存，请重试')
    return dest


def extract(archive,target,strip=True):
    target.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        for m in members:
            parts=Path(m.name).parts[1:] if strip else Path(m.name).parts
            if not parts:
                continue
            m.name=str(Path(*parts))
            if m.issym() or m.islnk():
                # Node distribution contains relative symlinks. Reject escapes.
                link=(target/m.name).parent/ m.linkname
                if not link.resolve().is_relative_to(target.resolve()):
                    raise RuntimeError('归档包含越界链接')
            if not (target/m.name).resolve().is_relative_to(target.resolve()):
                raise RuntimeError('归档包含越界路径')
            tar.extract(m,target)


def provision():
    installer=RUNTIME/'installer'
    if not (installer/'bin/python').exists():
        run(sys.executable,'-m','venv',installer)
    uv=installer/'bin/uv'
    if not uv.exists():
        run(installer/'bin/python','-m','pip','install','uv==0.9.5')
    run(uv,'python','install','3.12')
    deep=RUNTIME/'DeepTutor';gpt=RUNTIME/'GPT-Researcher';pqa=RUNTIME/'PaperQA2'
    for base in (deep,gpt,pqa):
        base.mkdir(parents=True,exist_ok=True)
        if not (base/'.venv/bin/python').exists():
            run(uv,'venv','--python','3.12',base/'.venv')
    marker=deep/'.installed-1.6.15'
    if not marker.exists():
        run(uv,'pip','install','--python',deep/'.venv/bin/python','deeptutor==1.6.15')
        marker.touch()
    revision='3582a6841fe7dc801e2a34e34ee408641aea9bce'
    if not (gpt/'main.py').exists():
        archive=download(f'https://codeload.github.com/assafelovic/gpt-researcher/tar.gz/{revision}',RUNTIME/'downloads/gpt-researcher.tar.gz','9a4a2aca4e0dd9d4d6df37afdc9d5a185bdceccba3a88d9866c5709eb63afdff')
        extract(archive,gpt)
    marker=gpt/'.installed-0.16.0'
    if not marker.exists():
        run(uv,'pip','install','--python',gpt/'.venv/bin/python','-e',gpt,'ddgs>=9','langchain-mcp-adapters')
        marker.touch()
    marker=pqa/'.installed-2026.8.12'
    if not marker.exists():
        run(uv,'pip','install','--python',pqa/'.venv/bin/python','paper-qa==2026.8.12')
        marker.touch()
    node=RUNTIME/'node'
    if not (node/'bin/node').exists():
        version='v22.23.3'
        fname=f'node-{version}-linux-x64.tar.xz'
        sums=download(f'https://nodejs.org/dist/{version}/SHASUMS256.txt',RUNTIME/'downloads/node-shasums.txt')
        sha=next(line.split()[0] for line in sums.read_text().splitlines() if line.endswith(' '+fname))
        archive=download(f'https://nodejs.org/dist/{version}/{fname}',RUNTIME/'downloads'/fname,sha)
        extract(archive,node)
    ollama=RUNTIME/'ollama'
    if not (ollama/'bin/ollama').exists():
        # Validate archive CRC through tar/zstd; model blobs are separately SHA-256 checked.
        archive=download('https://github.com/ollama/ollama/releases/download/v0.40.5/ollama-linux-amd64.tar.zst',RUNTIME/'downloads/ollama-linux-amd64.tar.zst')
        try:
            run('zstd','-t',archive)
        except subprocess.CalledProcessError:
            archive.unlink()
            raise RuntimeError('Ollama 归档损坏，已清除损坏缓存；请重新运行安装')
        ollama.mkdir(parents=True,exist_ok=True)
        run('tar','--zstd','-xf',archive,'-C',ollama)
    return {'deeptutor_root':str(deep),'deeptutor_python':str(deep/'.venv/bin/python'),
            'deeptutor_bin':str(deep/'.venv/bin/deeptutor'),'node_bin':str(node/'bin'),
            'gpt_root':str(gpt),'gpt_python':str(gpt/'.venv/bin/python'),
            'paperqa_root':str(pqa),'paperqa_python':str(pqa/'.venv/bin/python'),
            'ollama_bin':str(ollama/'bin/ollama'),'models':str(RUNTIME/'models')}


def reuse():
    paths={'deeptutor_root':HOME/'DeepTutor','deeptutor_python':HOME/'DeepTutor/.venv/bin/python',
           'deeptutor_bin':HOME/'DeepTutor/.venv/bin/deeptutor','node_bin':HOME/'DeepTutor/.tools/node/bin',
           'gpt_root':HOME/'GPT-Researcher','gpt_python':HOME/'GPT-Researcher/.conda/bin/python',
           'paperqa_root':HOME/'PaperQA2','paperqa_python':HOME/'PaperQA2/.conda/bin/python',
           'ollama_bin':HOME/'DeepTutor/.tools/ollama/bin/ollama','models':HOME/'DeepTutor/data/ollama/models'}
    for key,p in paths.items():
        if not p.exists():
            raise RuntimeError(f'已有安装缺少 {key}: {p}；新电脑请不加 --reuse-existing')
    return {k:str(v) for k,v in paths.items()}


def unit(name,engine):
    # systemd double-quoted arguments support paths containing spaces.
    def q(s):
        return '"'+str(s).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    text=f'''[Unit]
Description=SearchTools {engine}
After=network-online.target

[Service]
Type=simple
WorkingDirectory={str(ROOT).replace("%", "%%")}
ExecStart=/usr/bin/python3 {q(ROOT/'scripts/control.py')} run-engine {engine}
Environment={q('SEARCHTOOLS_CONFIG='+str(CONF))}
Restart=on-failure
RestartSec=5
TimeoutStopSec=25
KillMode=control-group

[Install]
WantedBy=default.target
'''
    path=HOME/'.config/systemd/user'/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text)


def main():
    parser=argparse.ArgumentParser(description='部署 SearchTools 和三个科研引擎')
    parser.add_argument('--reuse-existing',action='store_true',help='复用当前主目录内的已有三工具和模型')
    parser.add_argument('--skip-models',action='store_true',help='稍后通过 download-models 下载模型')
    parser.add_argument('--system-deps',action='store_true',help='使用 sudo 安装 Ubuntu 桌面依赖')
    parser.add_argument('--no-pin',action='store_true',help='不加入 GNOME 收藏夹')
    args=parser.parse_args()
    if platform.system()!='Linux' or platform.machine() not in ('x86_64','AMD64'):
        raise RuntimeError('当前安装器支持 Ubuntu x86_64；其他平台尚未验证')
    if args.system_deps:
        run('sudo','apt-get','update')
        run('sudo','apt-get','install','-y','python3','python3-venv','python3-gi','gir1.2-gtk-3.0','gir1.2-webkit2-4.1','curl','git','zstd','xz-utils','libgomp1','libgl1')
    run('/usr/bin/python3','-c',"import gi;gi.require_version('Gtk','3.0');from gi.repository import Gtk")
    # Confirm WebKit before expensive downloads.
    run('/usr/bin/python3','-c',"import gi\ntry: gi.require_version('WebKit2','4.1')\nexcept ValueError: gi.require_version('WebKit2','4.0')\nfrom gi.repository import WebKit2")
    if CONF.exists():
        cfg=json.loads(CONF.read_text())
        print('保留已有配置；重新注册应用与服务。',flush=True)
    else:
        paths=reuse() if args.reuse_existing else provision()
        cfg={**paths,'port':8765,'data':str(HOME/'.local/share/searchtools'),
             'deeptutor_url':'http://127.0.0.1:3782/chat','gpt_url':'http://127.0.0.1:8002',
             'paperqa_settings':str(CONF.parent/'paperqa.json')}
        CONF.parent.mkdir(parents=True,exist_ok=True)
        CONF.write_text(json.dumps(cfg,indent=2)+'\n');CONF.chmod(0o600)
    Path(cfg['data']).mkdir(parents=True,exist_ok=True)
    settings=Path(cfg['paperqa_settings'])
    if not settings.exists():
        shutil.copy2(ROOT/'config/paperqa-ollama.json',settings)
        settings.chmod(0o600)
    envfile=Path(cfg['gpt_root'])/'.env'
    if not envfile.exists():
        envfile.write_text((ROOT/'config/gpt.env.example').read_text()+f'\nDOC_PATH={cfg["data"]}/shared-papers\n')
        envfile.chmod(0o600)
    (Path(cfg['data'])/'shared-papers').mkdir(exist_ok=True)
    if not args.reuse_existing and not (Path(cfg['deeptutor_root'])/'data/user/settings/model_catalog.json').exists():
        run(cfg['deeptutor_bin'],'init','--home',cfg['deeptutor_root'],'--non-interactive',cwd='/tmp')
        configure_deeptutor(cfg)
    for name,engine in [('searchtools.service','workspace'),('searchtools-gpt.service','gpt'),('searchtools-deeptutor.service','deeptutor'),('searchtools-ollama.service','ollama')]:
        unit(name,engine)
    applications=HOME/'.local/share/applications'
    applications.mkdir(parents=True,exist_ok=True)
    desktop=applications/'io.github.qyw23AI.SearchTools.desktop'
    def dq(s):return str(s).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')
    desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=SearchTools
Name[zh_CN]=SearchTools 科研工作区
Comment=DeepTutor · GPT Researcher · PaperQA2
Exec="{dq(ROOT/'scripts/searchtools')}" open
Icon={ROOT/'assets/searchtools.svg'}
Terminal=false
Categories=Education;Science;
StartupNotify=true
StartupWMClass=io.github.qyw23AI.SearchTools
''')
    run('systemctl','--user','daemon-reload')
    run('systemctl','--user','enable','searchtools.service')
    run('systemctl','--user','restart','searchtools.service')
    run(sys.executable,ROOT/'scripts/control.py','start')
    if not args.no_pin and shutil.which('gsettings'):
        import ast
        result=subprocess.run(['gsettings','get','org.gnome.shell','favorite-apps'],capture_output=True,text=True)
        if result.returncode==0:
            values=ast.literal_eval(result.stdout.strip().removeprefix('@as '))
            appid=desktop.name
            if appid not in values:
                values.append(appid)
                run('gsettings','set','org.gnome.shell','favorite-apps',repr(values))
    if not args.skip_models:
        run(sys.executable,ROOT/'scripts/download_models.py')
    print('\n部署完成。运行 ./scripts/searchtools open 或从应用菜单打开 SearchTools。')


def configure_deeptutor(cfg):
    path=Path(cfg['deeptutor_root'])/'data/user/settings/model_catalog.json'
    data=json.loads(path.read_text()) if path.exists() else {'version':1,'connections':[],'services':{}}
    for service,model,base in [('llm','qwen3:8b','http://127.0.0.1:11434'),('embedding','qwen3-embedding:4b','http://127.0.0.1:11434/api/embed')]:
        mid=f'{service}-searchtools-model';pid=f'{service}-searchtools-profile'
        entry={'id':mid,'name':model,'model':model}
        if service=='embedding':entry['dimension']=2560
        data.setdefault('services',{})[service]={'active_profile_id':pid,'active_model_id':mid,'profiles':[{'id':pid,'name':'本地 Ollama','binding':'ollama','base_url':base,'api_key':'','models':[entry]}]}
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as e:
        print(f'部署未完成：{e}\n已完成下载会保留。修复网络或依赖后重新运行同一安装命令。',file=sys.stderr)
        sys.exit(1)
