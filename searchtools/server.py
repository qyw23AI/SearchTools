from __future__ import annotations
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen
import time
import uuid
from .core import Store,Worker,config,gpu_busy,ROOT


class App:
    def __init__(self,cfg):
        self.cfg=cfg
        self.store=Store(cfg['data'])
        self.worker=Worker(self.store,cfg)
        self.token=secrets.token_urlsafe(32)
        self.gpu=(0,(True,'等待检查'))

    def status(self):
        if time.time()-self.gpu[0]>10:
            self.gpu=(time.time(),gpu_busy())
        services={}
        for name,url in [('deeptutor',self.cfg['deeptutor_url']),('gpt',self.cfg['gpt_url']),('ollama','http://127.0.0.1:11434/api/version')]:
            try:
                with urlopen(url,timeout=1) as r:
                    services[name]=r.status==200
            except Exception:
                services[name]=False
        return {'services':services,'gpu_busy':self.gpu[1][0],'gpu_message':self.gpu[1][1]}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def send(self,status,body,ctype='application/json; charset=utf-8',download=None):
            if not isinstance(body,bytes):
                body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if download:
                from urllib.parse import quote
                self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(download))
            self.end_headers()
            self.wfile.write(body)

        def trusted(self,write=False):
            expected=f'127.0.0.1:{app.cfg["port"]}'
            if self.headers.get('Host')!=expected:
                return False
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+expected:
                return False
            return not write or secrets.compare_digest(self.headers.get('X-SearchTools-Token',''),app.token)

        def do_GET(self):
            if not self.trusted():
                return self.send(403,{'error':'请求来源不受信任'})
            try:
                u=urlparse(self.path)
                q=parse_qs(u.query)
                path=u.path
                if path=='/api/state':
                    return self.send(200,{'projects':app.store.rows('SELECT * FROM projects ORDER BY created'),**app.status()})
                if path=='/api/project':
                    pid=q['id'][0]
                    project=app.store.project(pid)
                    return self.send(200,{'project':project,'folder':str(app.store.folder(pid)/'papers'),
                        'papers':app.store.rows('SELECT * FROM papers WHERE project=?',(pid,)),
                        'notes':app.store.rows('SELECT * FROM notes WHERE project=? ORDER BY created DESC',(pid,)),
                        'jobs':app.store.rows('SELECT * FROM jobs WHERE project=? ORDER BY created DESC',(pid,))})
                if path=='/api/job':
                    rows=app.store.rows('SELECT * FROM jobs WHERE id=?',(q['id'][0],))
                    if not rows:
                        raise ValueError('任务不存在')
                    job=rows[0]
                    folder=app.store.folder(job['project'])/'jobs'/job['id']
                    job['log']=(folder/'run.log').read_text(errors='replace')[-30000:] if (folder/'run.log').exists() else ''
                    job['result']=json.loads((folder/'result.json').read_text()) if (folder/'result.json').exists() else None
                    return self.send(200,job)
                if path=='/api/file':
                    rows=app.store.rows('SELECT * FROM papers WHERE id=?',(q['id'][0],))
                    if not rows:
                        raise ValueError('文献不存在')
                    paper=rows[0]
                    file=app.store.folder(paper['project'])/'papers'/paper['name']
                    return self.send(200,file.read_bytes(),mimetypes.guess_type(file)[0] or 'application/octet-stream',paper['name'])
                if path=='/api/export':
                    rows=app.store.rows('SELECT * FROM jobs WHERE id=?',(q['id'][0],))
                    if not rows or rows[0]['status']!='done':
                        raise ValueError('结果尚未生成')
                    job=rows[0]
                    file=app.store.folder(job['project'])/'results'/(job['id']+'.md')
                    return self.send(200,file.read_bytes(),'text/markdown; charset=utf-8','research-result.md')
                static={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
                if path in static:
                    file=ROOT/'searchtools/static'/static[path]
                    body=file.read_bytes().replace(b'__TOKEN__',app.token.encode())
                    return self.send(200,body,mimetypes.guess_type(file)[0]+'; charset=utf-8')
                return self.send(404,{'error':'未找到页面'})
            except (ValueError,KeyError,FileNotFoundError) as e:
                self.send(400,{'error':str(e)})

        def do_POST(self):
            if not self.trusted(write=True):
                return self.send(403,{'error':'请求来源不受信任，请重新打开页面'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                limit=100*1024*1024 if self.path=='/api/upload' else 1024*1024
                if not 0<length<=limit:
                    raise ValueError('文件过大或请求为空（单文件最大 100 MB）')
                raw=self.rfile.read(length)
                if self.path=='/api/upload':
                    pid=self.headers.get('X-Project','')
                    name=unquote(self.headers.get('X-Filename',''))
                    return self.send(200,{'id':app.store.import_paper(pid,name,raw)})
                data=json.loads(raw)
                if self.path=='/api/projects':
                    return self.send(200,{'id':app.store.create_project(data['name'])})
                if self.path=='/api/jobs':
                    return self.send(200,{'id':app.store.enqueue(data['project'],data['kind'],data.get('prompt',''),data.get('source','web'))})
                if self.path=='/api/cancel':
                    app.worker.cancel(data['id'])
                    return self.send(200,{'ok':True})
                if self.path=='/api/notes':
                    app.store.project(data['project'])
                    with app.store.db() as db:
                        db.execute('INSERT INTO notes VALUES(?,?,?,?,?)',(uuid.uuid4().hex,data['project'],str(data['title'])[:200],str(data['body'])[:200000],time.time()))
                    return self.send(200,{'ok':True})
                return self.send(404,{'error':'未知操作'})
            except (ValueError,KeyError) as e:
                self.send(400,{'error':str(e)})
    return Handler


def main():
    cfg=config()
    app=App(cfg)
    threading.Thread(target=app.worker.run,daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',cfg['port']),handler(app))
    print(f'SearchTools http://127.0.0.1:{cfg["port"]}',flush=True)
    try:
        server.serve_forever()
    finally:
        app.worker.stopped.set()
        if app.worker.current:
            app.worker.cancel(app.worker.current)
        server.server_close()

if __name__=='__main__':
    main()
