from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get('SEARCHTOOLS_CONFIG', Path.home()/'.config/searchtools/config.json'))


def config():
    return json.loads(CONFIG.read_text())


def gpu_busy():
    """Fail closed if GPU occupancy cannot be determined; never kill GPU jobs."""
    try:
        p = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader'], capture_output=True, text=True, timeout=8)
        if p.returncode:
            return True, '暂时无法确认 GPU 状态，任务等待中'
        others = [line for line in p.stdout.splitlines() if line.strip() and 'ollama' not in line.lower()]
        return bool(others), '其他程序正在使用 GPU，本地任务等待中' if others else 'GPU 可用于本地任务'
    except (OSError, subprocess.TimeoutExpired):
        return True, '无法读取 GPU 状态，任务等待中'


class Store:
    def __init__(self, data):
        self.data = Path(data).expanduser().resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.data.chmod(0o700)
        self.dbpath = self.data/'workspace.sqlite3'
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY,project TEXT NOT NULL,name TEXT NOT NULL,sha TEXT NOT NULL,size INTEGER,UNIQUE(project,sha));
            CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,project TEXT NOT NULL,title TEXT,body TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT NOT NULL,kind TEXT NOT NULL,prompt TEXT,source TEXT,status TEXT,message TEXT,created REAL);
            ''')
            db.execute("UPDATE jobs SET status='failed',message='应用重启中断了任务，请重试' WHERE status='running'")

    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def rows(self, sql, params=()):
        with self.db() as db:
            return [dict(r) for r in db.execute(sql, params)]

    def project(self, pid):
        rows = self.rows('SELECT * FROM projects WHERE id=?', (pid,))
        if not rows:
            raise ValueError('项目不存在')
        return rows[0]

    def folder(self, pid):
        self.project(pid)
        return self.data/'projects'/pid

    def create_project(self, name):
        name = str(name).strip()[:100]
        if not name:
            raise ValueError('请输入项目名称')
        pid = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO projects VALUES(?,?,?)', (pid,name,time.time()))
        for sub in ('papers','results','index','jobs'):
            (self.folder(pid)/sub).mkdir(parents=True, exist_ok=True)
        return pid

    def import_paper(self, pid, name, body):
        folder = self.folder(pid)/'papers'
        name = Path(name).name
        if Path(name).suffix.lower() not in ('.pdf','.txt','.md') or not body:
            raise ValueError('支持非空 PDF、TXT、Markdown 文件')
        if name.lower().endswith('.pdf') and not body.startswith(b'%PDF-'):
            raise ValueError('文件不是有效的 PDF')
        sha = hashlib.sha256(body).hexdigest()
        found = self.rows('SELECT * FROM papers WHERE project=? AND sha=?',(pid,sha))
        if found:
            return found[0]['id']
        paperid = uuid.uuid4().hex
        safe = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', name)[:150]
        filename = sha[:12]+'_'+safe
        target = folder/filename
        target.write_bytes(body)
        with self.db() as db:
            db.execute('INSERT INTO papers VALUES(?,?,?,?,?)',(paperid,pid,filename,sha,len(body)))
        return paperid

    def enqueue(self,pid,kind,prompt,source='web'):
        self.project(pid)
        if kind not in ('ask','index','research') or source not in ('local','web'):
            raise ValueError('无效任务类型')
        if kind != 'index' and not str(prompt).strip():
            raise ValueError('请输入研究问题')
        if kind in ('ask','index') or source == 'local':
            if not self.rows('SELECT id FROM papers WHERE project=?',(pid,)):
                raise ValueError('请先导入项目文献')
        jid=uuid.uuid4().hex
        work=self.folder(pid)/'jobs'/jid
        papers=work/'papers'
        papers.mkdir(parents=True)
        manifest=self.rows('SELECT * FROM papers WHERE project=?',(pid,))
        import shutil
        for paper in manifest:
            shutil.copy2(self.folder(pid)/'papers'/paper['name'],papers/paper['name'])
        (work/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False))
        with self.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(jid,pid,kind,str(prompt)[:20000],source,'queued','等待执行',time.time()))
        return jid

    def update(self,jid,status,message):
        with self.db() as db:
            db.execute('UPDATE jobs SET status=?,message=? WHERE id=?',(status,message,jid))


class Worker:
    def __init__(self,store,cfg,guard=gpu_busy):
        self.store,self.cfg,self.guard=store,cfg,guard
        self.lock=threading.Lock()
        self.process=None
        self.current=None
        self.stopped=threading.Event()

    def cancel(self,jid):
        with self.lock:
            if self.current == jid and self.process and self.process.poll() is None:
                os.killpg(self.process.pid,signal.SIGTERM)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET status='cancelled',message='已取消' WHERE id=? AND status IN ('queued','waiting','running')",(jid,))

    def run(self):
        while not self.stopped.wait(3):
            jobs=self.store.rows("SELECT * FROM jobs WHERE status IN ('queued','waiting') ORDER BY created LIMIT 1")
            if not jobs:
                continue
            job=jobs[0]
            busy,reason=self.guard()
            if busy:
                with self.lock:
                    if self.store.rows('SELECT status FROM jobs WHERE id=?',(job['id'],))[0]['status'] != 'cancelled':
                        self.store.update(job['id'],'waiting',reason)
                continue
            self.execute(job)

    def execute(self,job):
        jid,pid=job['id'],job['project']
        folder=self.store.folder(pid)
        work=folder/'jobs'/jid
        work.mkdir(exist_ok=True)
        # Freeze input files and record hashes so results retain their exact source scope.
        papers=work/'papers'
        papers.mkdir(exist_ok=True)
        manifest=json.loads((work/'manifest.json').read_text())
        payload={**job,'papers':str(papers),'output':str(work/'result.json'),'manifest':manifest,
                 'index':str(folder/'index'),'settings':self.cfg['paperqa_settings']}
        (work/'input.json').write_text(json.dumps(payload,ensure_ascii=False))
        engine='gpt' if job['kind']=='research' else 'paperqa'
        try:
            with self.lock:
                if self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']=='cancelled':
                    return
                self.current=jid
                self.store.update(jid,'running','正在执行；可随时取消')
                log=(work/'run.log').open('w')
                self.process=subprocess.Popen([self.cfg[engine+'_python'],str(ROOT/'searchtools/engine_worker.py'),str(work/'input.json')],cwd=self.cfg[engine+'_root'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                deadline=time.monotonic()+7200
                cancelled_at=None
                while self.process.poll() is None:
                    if self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']=='cancelled':
                        cancelled_at=cancelled_at or time.monotonic()
                        if time.monotonic()-cancelled_at>10:
                            try:os.killpg(self.process.pid,signal.SIGKILL)
                            except ProcessLookupError:pass
                    if time.monotonic()>deadline:
                        raise subprocess.TimeoutExpired(self.process.args,7200)
                    time.sleep(0.5)
                code=self.process.returncode
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid,signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid,signal.SIGKILL)
                    self.process.wait()
                raise RuntimeError('任务超过两小时，请缩小范围后重试')
            finally:
                log.close()
            with self.lock:
                state=self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']
                if state!='cancelled':
                    if code != 0 or not (work/'result.json').exists():
                        raise RuntimeError('引擎执行失败，请查看任务日志')
                    result=json.loads((work/'result.json').read_text())
                    (folder/'results'/f'{jid}.md').write_text(result.get('answer','任务完成'))
                    self.store.update(jid,'done','已完成')
        except Exception as e:
            if self.store.rows('SELECT status FROM jobs WHERE id=?',(jid,))[0]['status']!='cancelled':
                self.store.update(jid,'failed',str(e))
        finally:
            with self.lock:
                self.process=None
                self.current=None
