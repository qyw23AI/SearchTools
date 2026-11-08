"""Deploy the validated standalone frontend, preserving the packaged runtime marker."""
from pathlib import Path
import json
import shutil
import subprocess
import time
from urllib.request import urlopen

DT = Path('/home/qyw/DeepTutor')
source = DT/'web.source-checkout/.next-searchtools-sync/standalone'
runtime = DT/'data/user/runtime/web'
staged = runtime.with_name('web.searchtools-staged-' + time.strftime('%Y%m%d-%H%M%S'))
if not (source/'server.js').is_file():raise SystemExit('Frontend build not ready')
shutil.copytree(source, staged)
# Launcher normally recopies the packaged distribution when settings change.
# Preserve that exact contract; a future upstream upgrade will replace this patch.
marker = runtime/'.deeptutor-web-runtime.json'
shutil.copy2(marker, staged/marker.name)
(staged/'.searchtools-sync-build.json').write_text(json.dumps({'source':str(source),'deployed_at':time.time()}))
subprocess.run(['systemctl','--user','stop','searchtools-deeptutor.service'],check=True)
backup = runtime.with_name('web.backup-searchtools-' + time.strftime('%Y%m%d-%H%M%S'))
runtime.rename(backup)
staged.rename(runtime)
subprocess.run(['systemctl','--user','restart','searchtools.service'],check=True)
subprocess.run(['systemctl','--user','start','searchtools-deeptutor.service'],check=True)
for _ in range(110):
    try:
        with urlopen('http://127.0.0.1:8001/health/live',timeout=1) as api:
            alive=json.load(api).get('status')=='alive'
        with urlopen('http://127.0.0.1:3782/chat',timeout=2) as frontend:
            if alive and frontend.status==200:
                print('DeepTutor backend and event-enabled frontend ready; previous runtime preserved at '+str(backup))
                break
    except Exception:pass
    time.sleep(.5)
else:raise SystemExit('Deployment did not become ready; inspect service logs')
