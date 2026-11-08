/**
 * DeepTutor Companion Shell v3 (~70 lines)
 * Embeds the standalone SearchTools Evidence Workbench (:8765/workbench) into a resizable
 * side panel, syncs light/dark theme via postMessage, and handles citation insertion into
 * Co-Writer / Chat editors without any DOM-scraping model hacks.
 */
(function () {
  if (window.__SEARCHTOOLS_COMPANION_INITIALIZED__) return;
  window.__SEARCHTOOLS_COMPANION_INITIALIZED__ = true;

  const WORKBENCH_ORIGIN = 'http://127.0.0.1:8765';
  const modelClientId = crypto.randomUUID();
  let modelSequence = 0;
  let modelState = null;
  let modelSync = Promise.resolve();
  let isOpen = false;
  let panelWidth = Number(localStorage.getItem('st_wb_width') || 430);

  function currentTheme() {
    const html = document.documentElement;
    if (html.classList.contains('dark') || html.getAttribute('data-theme') === 'dark') return 'dark';
    return 'light';
  }

  function insertIntoDeepTutor(text) {
    const editor = document.querySelector('.ProseMirror[contenteditable="true"], [contenteditable="true"], textarea');
    if (!editor) {
      navigator.clipboard.writeText(text);
      return;
    }
    if (editor.tagName === 'TEXTAREA' || editor.tagName === 'INPUT') {
      const start = editor.selectionStart || 0;
      const end = editor.selectionEnd || 0;
      const val = editor.value || '';
      editor.value = val.slice(0, start) + text + val.slice(end);
      editor.selectionStart = editor.selectionEnd = start + text.length;
      editor.dispatchEvent(new Event('input', { bubbles: true }));
    } else if (editor.isContentEditable) {
      editor.focus();
      document.execCommand('insertText', false, text);
    }
    editor.focus();
  }

  const style = document.createElement('style');
  style.textContent = `
    #st-wb-toggle {
      position: fixed; right: 0; top: 46%; transform: translateY(-50%);
      z-index: 999998; cursor: pointer; user-select: none;
      writing-mode: vertical-rl; letter-spacing: 2px; font-size: 12.5px; font-weight: 600;
      padding: 12px 8px; border-radius: 10px 0 0 10px;
      background: #2563eb; color: #fff; border: none;
      box-shadow: -2px 4px 14px rgba(37,99,235,0.32); transition: right 0.2s ease;
    }
    #st-wb-drawer {
      position: fixed; top: 0; right: 0; height: 100vh; width: ${panelWidth}px;
      z-index: 999999; background: var(--background, #fff);
      border-left: 1px solid rgba(148,163,184,0.28);
      box-shadow: -8px 0 28px rgba(15,23,42,0.18);
      transform: translateX(100%); transition: transform 0.2s ease;
      display: flex; flex-direction: column;
    }
    #st-wb-drawer.open { transform: translateX(0); }
    #st-wb-resizer {
      position: absolute; left: -4px; top: 0; width: 8px; height: 100%;
      cursor: ew-resize; z-index: 2;
    }
    #st-wb-iframe { width: 100%; height: 100%; border: none; flex: 1; }
  `;
  document.head.appendChild(style);

  const btn = document.createElement('button');
  btn.id = 'st-wb-toggle';
  btn.textContent = '⚖️ 证据工作台';

  const drawer = document.createElement('aside');
  drawer.id = 'st-wb-drawer';
  drawer.innerHTML = `<div id="st-wb-resizer" title="拖拽调整宽度"></div><iframe id="st-wb-iframe" src="${WORKBENCH_ORIGIN}/workbench?theme=${currentTheme()}&client_id=${encodeURIComponent(modelClientId)}"></iframe>`;

  document.body.appendChild(btn);
  document.body.appendChild(drawer);

  const iframe = drawer.querySelector('#st-wb-iframe');
  function publishModel(detail) {
    if (!detail?.selection?.profile_id || !detail?.selection?.model_id) return;
    const sequence = ++modelSequence;
    const startedAt = performance.now();
    iframe.contentWindow?.postMessage({type: 'st-model-pending'}, WORKBENCH_ORIGIN);
    modelSync = modelSync.catch(() => {}).then(async () => {
      const response = await fetch(WORKBENCH_ORIGIN + '/api/deeptutor/sync-selection', {
        method: 'POST', headers: {'Content-Type':'application/json','X-SearchTools-Token':'local-mcp'},
        body: JSON.stringify({client_id:modelClientId, sequence, selection:detail.selection,
          session_id:detail.session_id || location.pathname + location.search})
      });
      const state = await response.json();
      if (!response.ok || !state.ok) throw new Error(state.error || '模型同步失败');
      if (sequence === modelSequence) {
        modelState = state;
        modelState.client_elapsed_ms = performance.now() - startedAt;
        iframe.contentWindow?.postMessage({type:'st-model-confirmed', state:modelState}, WORKBENCH_ORIGIN);
        btn.title = '已同步：' + state.model.llm_model;
        window.dispatchEvent(new CustomEvent('searchtools:model-confirmed', {detail:modelState}));
      }
      return state;
    });
    modelSync.catch(error => {
      if (sequence !== modelSequence) return;
      modelState = null;
      btn.title = '模型同步失败：' + error.message;
      iframe.contentWindow?.postMessage({type:'st-model-error', error:error.message}, WORKBENCH_ORIGIN);
    });
  }
  window.addEventListener('deeptutor:llm-selection', event => publishModel(event.detail));
  if (window.__DEEPTUTOR_LLM_SELECTION__) publishModel(window.__DEEPTUTOR_LLM_SELECTION__);
  iframe.addEventListener('load', () => {
    if (modelState) iframe.contentWindow?.postMessage({type:'st-model-confirmed',state:modelState}, WORKBENCH_ORIGIN);
  });
  window.addEventListener('message', async event => {
    if (event.origin !== WORKBENCH_ORIGIN || event.source !== iframe.contentWindow || event.data?.type !== 'st-model-context-request') return;
    try {
      await modelSync;
      if (!modelState) throw new Error('DeepTutor 当前会话尚未确认模型');
      iframe.contentWindow.postMessage({type:'st-model-context-response', request_id:event.data.request_id, state:modelState}, WORKBENCH_ORIGIN);
    } catch (error) {
      iframe.contentWindow.postMessage({type:'st-model-context-response', request_id:event.data.request_id, error:error.message}, WORKBENCH_ORIGIN);
    }
  });

  function toggleDrawer(force) {
    isOpen = typeof force === 'boolean' ? force : !isOpen;
    drawer.classList.toggle('open', isOpen);
    btn.style.right = isOpen ? `${panelWidth}px` : '0';
    btn.textContent = isOpen ? '✕ 收起工作台' : '⚖️ 证据工作台';
    if (isOpen && iframe.contentWindow) {
      iframe.contentWindow.postMessage({ type: 'st-theme', theme: currentTheme() }, '*');
      iframe.contentWindow.postMessage({ type: 'st-refresh', projectId: localStorage.getItem('searchtools-project') || '' }, '*');
    }
  }
  btn.addEventListener('click', () => toggleDrawer());

  window.__stSyncDrawerNow = function() {
    if (isOpen && iframe && iframe.contentWindow) {
      iframe.contentWindow.postMessage({ type: 'st-refresh', projectId: localStorage.getItem('searchtools-project') || '' }, '*');
    }
  };

  // Drag resize
  const resizer = drawer.querySelector('#st-wb-resizer');
  resizer.addEventListener('mousedown', (e) => {
    e.preventDefault();
    iframe.style.pointerEvents = 'none';
    const onMove = (ev) => {
      panelWidth = Math.max(340, Math.min(780, window.innerWidth - ev.clientX));
      drawer.style.width = `${panelWidth}px`;
      if (isOpen) btn.style.right = `${panelWidth}px`;
    };
    const onUp = () => {
      iframe.style.pointerEvents = 'auto';
      localStorage.setItem('st_wb_width', String(panelWidth));
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });

  // Listen to messages from Workbench iframe
  window.addEventListener('message', (ev) => {
    if (ev.source !== iframe.contentWindow || ev.origin !== WORKBENCH_ORIGIN || !ev.data || typeof ev.data !== 'object') return;
    if (ev.data.type === 'st-insert-text' && ev.data.text) {
      insertIntoDeepTutor(ev.data.text);
    }
  });

  // Observe host theme changes
  new MutationObserver(() => {
    if (iframe.contentWindow) {
      iframe.contentWindow.postMessage({ type: 'st-theme', theme: currentTheme() }, '*');
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
})();
