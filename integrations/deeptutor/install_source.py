"""Install the local adapter and typed model-selection producer without replacing user edits."""
from pathlib import Path
import os
import secrets
import shutil
import time

REPO = Path(__file__).resolve().parents[2]
DT = Path('/home/qyw/DeepTutor')
WEB = DT / 'web.source-checkout'


def write_with_backup(path, content):
    if path.read_text() == content:
        return
    shutil.copy2(path, path.with_name(path.name + '.backup-searchtools-' + time.strftime('%Y%m%d-%H%M%S')))
    path.write_text(content)


def install():
    key = Path.home()/'.config/searchtools/deeptutor_gateway.key'
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(secrets.token_urlsafe(48))
    # The running service uses the installed wheel, while source changes are
    # retained for reproducible future source builds.
    for package in (DT/'deeptutor', DT/'.venv/lib/python3.11/site-packages/deeptutor'):
        target = package/'api/routers/searchtools_bridge.py'
        if target.exists():
            write_with_backup(target, (REPO/'integrations/deeptutor/evidence_router.py').read_text())
        else:
            shutil.copy2(REPO/'integrations/deeptutor/evidence_router.py', target)
        main = package/'api/main.py'
        content = main.read_text()
        anchor = '_auth = [Depends(require_learning_surface)]'
        if 'from deeptutor.api.routers import searchtools_bridge' not in content:
            content = content.replace(anchor, anchor + '\n\n# Local SearchTools adapter uses the same provider/auth runtime as conversation turns.\nfrom deeptutor.api.routers import searchtools_bridge\napp.include_router(searchtools_bridge.router, prefix="/api/evidence-llm/v1", dependencies=_auth)\n')
            write_with_backup(main, content)
    model = WEB/'components/chat/home/ModelSelector.tsx'
    content = model.read_text()
    if 'deeptutor:llm-selection' not in content:
        anchor = '  useOutsideClick(rootRef, open, () => {'
        producer = '''  // Publish the displayed session selection, including restoration and focus.
  // The companion persists only IDs and awaits a server acknowledgment.
  useEffect(() => {
    const effective = selectedSelection ?? activeDefault;
    if (!effective || loading || error) return;
    const publish = () => {
      if (document.visibilityState !== "visible" || !rootRef.current?.getClientRects().length) return;
      const detail = {selection: effective, session_id: window.location.pathname + window.location.search};
      (window as unknown as {__DEEPTUTOR_LLM_SELECTION__: typeof detail}).__DEEPTUTOR_LLM_SELECTION__ = detail;
      window.dispatchEvent(new CustomEvent("deeptutor:llm-selection", {detail}));
    };
    publish();
    window.addEventListener("focus", publish);
    return () => window.removeEventListener("focus", publish);
  }, [selectedKey, selectedSelection, activeDefault, loading, error]);

'''
        if anchor not in content:raise RuntimeError('ModelSelector anchor not found')
        content = content.replace(anchor, producer + anchor)
        write_with_backup(model, content)
    layout = WEB/'app/layout.tsx'
    content = layout.read_text()
    if 'deeptutor_companion.js' not in content:
        content = 'import Script from "next/script";\n' + content
        content = content.replace('        <AppShellProvider>', '        <Script src="http://127.0.0.1:8765/static/deeptutor_companion.js" strategy="afterInteractive" />\n        <AppShellProvider>')
        write_with_backup(layout, content)
    print('Installed local adapter and model event producer; existing changes preserved')


if __name__ == '__main__':
    install()
