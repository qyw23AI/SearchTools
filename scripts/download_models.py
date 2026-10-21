#!/usr/bin/env python3
"""Download pinned official Ollama blobs with resume and SHA-256 verification.
No inference is performed; this command does not allocate GPU memory.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from searchtools.core import config


def verify(path,digest,size):
    if not path.exists() or path.stat().st_size!=size:return False
    sha=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):sha.update(chunk)
    return sha.hexdigest()==digest


def main():
    root=Path(config()['models'])
    (root/'blobs').mkdir(parents=True,exist_ok=True)
    for name,tag in [('qwen3-embedding','4b'),('qwen3','8b')]:
        raw=(ROOT/'config/manifests'/f'{name}-{tag}.json').read_bytes()
        manifest=json.loads(raw)
        for item in [manifest['config'],*manifest['layers']]:
            digest=item['digest'].split(':')[1]
            target=root/'blobs'/('sha256-'+digest)
            if verify(target,digest,item['size']):
                print('已验证 '+digest[:12],flush=True)
                continue
            partial=target.with_name(target.name+'.download')
            if partial.exists() and partial.stat().st_size>item['size']:partial.unlink()
            if not verify(partial,digest,item['size']):
                if partial.exists() and partial.stat().st_size==item['size']:partial.unlink()
                subprocess.run(['curl','-fL','--retry','8','--retry-all-errors','--connect-timeout','30','--max-time','1800','-C','-','-o',str(partial),f'https://registry.ollama.ai/v2/library/{name}/blobs/{item["digest"]}'],check=True)
            if not verify(partial,digest,item['size']):
                raise RuntimeError('模型校验失败，重新执行会重试：'+digest)
            os.replace(partial,target)
        dest=root/'manifests/registry.ollama.ai/library'/name/tag
        dest.parent.mkdir(parents=True,exist_ok=True)
        tmp=dest.with_name(dest.name+'.download');tmp.write_bytes(raw);os.replace(tmp,dest)
        print(f'{name}:{tag} 下载并验证完成',flush=True)

if __name__=='__main__':
    main()
