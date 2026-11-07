const $ = s => document.querySelector(s);
const token = $('meta[name="searchtools-token"]')?.content || '';
let state = {}, project = null, page = 'overview', detail = null;
let projectId = localStorage.getItem('searchtools-project') || '', drafts = {};

const labels = {
  overview: '工作台',
  library: '文献管理',
  ask: '论文问答 (PaperQA2)',
  research: '方向调研',
  notes: '笔记与结果',
  tasks: '任务进度',
  models: '模型与设置'
};

const statuses = {
  queued: '排队中',
  waiting: '等待 GPU',
  running: '执行中',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消'
};

const kinds = {
  ask: '论文深度问答',
  evidence: '证据检索',
  research: '方向调研',
  index: '建立索引'
};

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

async function api(path, data) {
  const r = await fetch(path, {
    method: data ? 'POST' : 'GET',
    headers: data ? { 'Content-Type': 'application/json', 'X-SearchTools-Token': token } : {},
    body: data ? JSON.stringify(data) : undefined
  });
  const j = await r.json();
  if (!r.ok) throw Error(j.error || '请求失败');
  return j;
}

function toast(t) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = t;
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 5000);
}

function run(fn) {
  return async e => {
    try {
      await fn(e);
    } catch (err) {
      toast(err.message);
    }
  };
}

function saveDraft() {
  if ($('#question')) drafts[projectId + page] = $('#question').value;
  if ($('#askQuestion')) drafts[projectId + 'ask'] = $('#askQuestion').value;
  if ($('#noteBody')) drafts[projectId + 'note'] = $('#noteBody').value;
}

async function load(render = true) {
  state = await api('/api/state');
  if (!state.projects.some(p => p.id === projectId)) {
    projectId = state.projects[0]?.id || '';
  }
  $('#projects').innerHTML = state.projects.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
  $('#projects').value = projectId;
  $('#banner').textContent = state.gpu_busy
    ? `${state.gpu_message}。工作区任务自动等待；原生工具中的操作由你自行控制。`
    : '';

  if (projectId) {
    const requested = projectId;
    const p = await api('/api/project?id=' + projectId);
    if (requested !== projectId) return;
    project = p;
  } else {
    project = null;
  }

  const pendingCount = project?.jobs.filter(j => ['queued', 'waiting', 'running'].includes(j.status)).length || '';
  $('#pending').textContent = pendingCount;

  if (render) draw();
}

function go(p) {
  saveDraft();
  page = p;
  detail = null;
  draw();
}

function intro(title, desc) {
  return `<div class="intro"><div class="overline">RESEARCH WORKSPACE</div><h1>${esc(title)}</h1><p>${esc(desc)}</p></div>`;
}

function native(tool) {
  if (window.__SEARCHTOOLS_DESKTOP__) {
    location.href = 'searchtools://tool/' + tool;
  } else {
    window.open(tool === 'gpt' ? 'http://127.0.0.1:8002' : 'http://127.0.0.1:3782/chat', '_blank', 'noopener');
  }
}

function jobRows(jobs) {
  return jobs.map(j => `
    <div class="row">
      <div>
        <a href="#" data-job="${j.id}">${esc(j.prompt || '建立项目文献索引').slice(0, 140)}</a>
        <div class="quiet">${kinds[j.kind] || j.kind} · ${new Date(j.created * 1000).toLocaleString()}${['queued', 'waiting', 'running'].includes(j.status) && j.message ? ` · <span style="color:#2d5e44;font-weight:600">${esc(j.message)}</span>` : ''}</div>
      </div>
      <span class="badge ${j.status}">${statuses[j.status] || j.status}</span>
    </div>
  `).join('') || '<div class="empty">还没有任务。从一个研究问题开始。</div>';
}

async function draw() {
  $('#breadcrumb').textContent = (project?.project.name || '我的研究') + ' / ' + (labels[page] || page);
  document.querySelectorAll('[data-page]').forEach(b => b.classList.toggle('active', b.dataset.page === page));

  if (page === 'models') {
    $('#content').innerHTML = await ModelsPage.render();
    ModelsPage.bind();
    return;
  }

  if (page === 'library') {
    $('#content').innerHTML = await LibraryPage.render();
    LibraryPage.bind();
    return;
  }

  if (!project) {
    $('#content').innerHTML = intro('让研究，从这里开始。', '一个工作区，连接文献发现、证据问答与深入学习。') +
      '<div class="panel empty">点击左侧「新建研究项目」，开始整理你的第一项研究。</div>';
    return;
  }

  if (page === 'ask') {
    $('#content').innerHTML = await AskPage.render();
    AskPage.bind();
    return;
  }

  const p = project;
  let html = '';

  if (page === 'overview') {
    html = intro(p.project.name, '发现问题，整理证据，把理解积累成自己的研究。') + `
      <div class="stats">
        <div class="stat"><b>${p.papers.length}</b><span>篇项目文献</span></div>
        <div class="stat"><b>${p.jobs.filter(j => j.status === 'done').length}</b><span>项研究结果</span></div>
        <div class="stat"><b>${p.notes.length}</b><span>条研究笔记</span></div>
      </div>
      <div class="grid">
        <article class="card">
          <div class="icon">◎</div>
          <h3>发现研究方向</h3>
          <p>用 GPT Researcher 检索学术来源，形成调研报告。</p>
          <button data-go="research">开始调研　↗</button>
        </article>
        <article class="card">
          <div class="icon">⌕</div>
          <h3>向论文提问</h3>
          <p>用 PaperQA2 查找依据，对比项目中的论文。</p>
          <button data-go="ask">检索证据　↗</button>
        </article>
        <article class="card">
          <div class="icon">◇</div>
          <h3>深入理解</h3>
          <p>进入 DeepTutor 原生界面，精读、解题与学习。</p>
          <button data-native="deeptutor">打开 DeepTutor　↗</button>
        </article>
      </div>
      <div class="panel">
        <div class="section-title">
          <h2>最近研究</h2>
          <button class="subtle" data-go="tasks">查看全部 →</button>
        </div>
        ${jobRows(p.jobs.slice(0, 4))}
      </div>
      <div class="panel">
        <h3>工具状态</h3>
        <div class="status-grid">
          ${Object.entries(state.services).map(([k, v]) => `
            <span class="service ${v ? 'online' : ''}">● ${k === 'gpt' ? 'GPT Researcher' : k === 'deeptutor' ? 'DeepTutor' : 'Ollama'} · ${v ? '就绪' : '尚未就绪'}</span>
          `).join('')}
        </div>
      </div>
    `;
  }

  if (page === 'research') {
    html = intro('从一个问题，展开研究。', 'GPT Researcher 负责发现与综合。在线调研默认使用 OpenAlex 学术来源。') + `
      <div class="panel">
        <h3>你想研究什么？</h3>
        <textarea id="question" placeholder="调研一个方向的代表工作、争议与值得继续探索的问题。">${esc(drafts[projectId + 'research'] || '')}</textarea>
        <label class="quiet">资料范围 
          <select id="source">
            <option value="web">在线学术来源 · OpenAlex</option>
            <option value="local">仅本项目文献</option>
          </select>
        </label>
        <div class="actions">
          <button id="submitJob">生成调研报告</button>
          <span class="quiet">${state.gpu_busy ? 'GPU 忙碌时自动排队' : '任务在后台执行，可以继续浏览'}</span>
        </div>
      </div>
      <div class="panel">
        <h3>当前项目的调研任务</h3>
        ${jobRows(p.jobs.filter(j => j.kind === 'research'))}
      </div>
    `;
  }

  if (page === 'tasks') {
    html = intro('每一步，都有记录。', '任务串行执行。可取消当前任务，失败后保留日志并重新提交。') + `
      <div class="panel">${jobRows(p.jobs)}</div>
    `;
  }

  if (page === 'notes') {
    html = intro('把发现，留给下一步。', '研究结果与自己的判断保存在同一个项目里。') + `
      <div class="two-col">
        <div class="panel">
          <h3>记下一条研究笔记</h3>
          <input id="noteTitle" placeholder="笔记标题">
          <textarea id="noteBody" placeholder="记录想法、待验证的假设，或下一步实验。">${esc(drafts[projectId + 'note'] || '')}</textarea>
          <button id="saveNote">保存笔记</button>
        </div>
        <div class="panel">
          <h3>研究结果</h3>
          ${jobRows(p.jobs.filter(j => j.status === 'done'))}
        </div>
      </div>
      <div class="panel">
        <h3>项目笔记</h3>
        ${p.notes.map(n => `
          <details>
            <summary>${esc(n.title)}</summary>
            <div class="answer">${esc(n.body)}</div>
          </details>
        `).join('') || '<div class="empty">值得保留的想法，都可以从这里开始。</div>'}
      </div>
    `;
  }

  $('#content').innerHTML = html;
  bind();
}

function bind() {
  document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(b.dataset.go));
  document.querySelectorAll('[data-native]').forEach(b => b.onclick = () => native(b.dataset.native));
  document.querySelectorAll('[data-job]').forEach(b => b.onclick = run(async e => {
    e.preventDefault();
    saveDraft();
    await showJob(b.dataset.job);
  }));

  if ($('#submitJob')) {
    $('#submitJob').onclick = run(async () => {
      saveDraft();
      const b = $('#submitJob');
      b.disabled = true;
      try {
        await api('/api/jobs', {
          project: projectId,
          kind: 'research',
          prompt: $('#question').value,
          source: $('#source')?.value || 'local'
        });
        toast('任务已加入队列');
        go('tasks');
        await load();
      } finally {
        b.disabled = false;
      }
    });
  }

  if ($('#saveNote')) {
    $('#saveNote').onclick = run(async () => {
      await api('/api/notes', {
        project: projectId,
        title: $('#noteTitle').value || '研究笔记',
        body: $('#noteBody').value
      });
      drafts[projectId + 'note'] = '';
      toast('笔记已保存');
      await load();
    });
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('已复制');
  } catch {
    const t = document.createElement('textarea');
    t.value = text;
    document.body.append(t);
    t.select();
    document.execCommand('copy');
    t.remove();
    toast('已复制');
  }
}

async function showJob(id) {
  const j = await api('/api/job?id=' + id);
  detail = id;
  detailStatus = j.status;
  const result = j.result;
  const isRunning = ['queued', 'waiting', 'running'].includes(j.status);
  $('#content').innerHTML = intro(kinds[j.kind] || j.kind, j.message) + `
    <button class="subtle" id="back">← 返回${labels[page] || '页面'}</button>
    <div class="panel">
      <div class="result-question">
        ${esc(j.prompt || '建立索引')}<br>
        范围：${j.kind === 'research' && j.source === 'web' ? '在线学术来源' : '任务创建时的项目文献快照'}
      </div>
      <span class="badge ${j.status}" id="detailJobBadge">${statuses[j.status] || j.status}</span>
      ${isRunning ? `
        <div style="margin:14px 0;padding:12px 14px;background:#f5f8f4;border-radius:8px;border:1px solid #d7e4d8;color:#2d5e44;font-size:13px;font-weight:600;display:flex;justify-content:space-between;align-items:center">
          <span id="detailJobStageMsg">⏳ ${esc(j.message || '正在执行任务...')}</span>
          <span class="quiet" id="detailJobElapsed" style="font-size:11.5px;font-weight:400">已耗时 ${Math.max(1, Math.round(Date.now() / 1000 - (j.created || Date.now() / 1000)))} 秒 · 实时更新中</span>
        </div>
      ` : ''}
      ${result ? `
        <div class="answer">${esc(result.answer)}</div>
        ${result.successful === false ? '<p>引擎未形成充分支持的答案，请检查证据或补充文献。</p>' : ''}
        ${result.references ? `
          <div class="references-box">
            <b>参考文献与出处:</b><br>${esc(result.references)}
          </div>
        ` : ''}
        <div class="actions">
          <a href="/api/export?id=${id}">导出 Markdown ↓</a>
          <button id="toNote" class="secondary">保存为笔记</button>
          <button id="handoff">复制并交给 DeepTutor</button>
          <button id="verify" class="secondary">交给 PaperQA 核对</button>
        </div>
        <details>
          <summary>引用、证据与输入清单（原始结构）</summary>
          <pre class="log">${esc(JSON.stringify(result, null, 2))}</pre>
        </details>
      ` : (!isRunning ? '<div class="empty">结果生成后将显示在这里。</div>' : '')}
      <div class="actions">
        ${isRunning ? '<button id="cancel" class="secondary">取消任务</button>' : ''}
        ${['failed', 'cancelled'].includes(j.status) ? '<button id="retry">重新提交</button>' : ''}
        ${!isRunning ? '<button id="deleteJobDetail" class="secondary" style="color:#b23528">删除此记录</button>' : ''}
        <button id="refreshJob" class="subtle">刷新状态</button>
      </div>
      <details ${isRunning ? 'open' : ''}>
        <summary>执行日志 ${isRunning ? '(实时刷新)' : ''}</summary>
        <pre class="log" id="detailJobLog" style="max-height:300px;overflow-y:auto">${esc(j.log || '正在启动执行引擎...')}</pre>
      </details>
    </div>
  `;

  $('#back').onclick = () => { detail = null; draw(); };
  $('#refreshJob').onclick = run(() => showJob(id));
  if ($('#cancel')) $('#cancel').onclick = run(async () => { await api('/api/cancel', { id }); await showJob(id); });
  if ($('#retry')) $('#retry').onclick = run(async () => {
    await api('/api/jobs', { project: j.project, kind: j.kind, prompt: j.prompt, source: j.source });
    detail = null;
    await load();
  });
  if ($('#deleteJobDetail')) $('#deleteJobDetail').onclick = run(async () => {
    if (!confirm('确定要删除这条任务记录及其结果吗？')) return;
    await api('/api/job/remove', { project: j.project, id: j.id });
    toast('任务记录已删除');
    detail = null;
    await load();
    draw();
  });

  if (result) {
    $('#toNote').onclick = run(async () => {
      await api('/api/notes', { project: j.project, title: j.prompt || '研究结果', body: result.answer });
      toast('已保存到项目笔记');
    });
    $('#handoff').onclick = run(async () => {
      await copy(`请帮助我精读并理解以下研究材料。以下内容是工具生成结果，请核对原论文，不要视为已验证事实。\n研究问题：${j.prompt}\n\n${result.answer}\n\n文献文件夹：${project.folder}`);
      native('deeptutor');
      toast('已复制交接内容，在 DeepTutor 中粘贴；论文需导入其知识库。');
    });
    $('#verify').onclick = () => {
      drafts[projectId + 'ask'] = `请根据本项目论文核对以下内容，区分支持、反对和证据不足，并引用原文依据：\n\n${result.answer}`;
      go('ask');
    };
  }
}

$('#projects').onchange = run(async e => {
  saveDraft();
  projectId = e.target.value;
  localStorage.setItem('searchtools-project', projectId);
  detail = null;
  await load();
});

$('#newProject').onclick = () => $('#dialog').showModal();
$('#closeDialog').onclick = () => $('#dialog').close();
$('#projectForm').onsubmit = run(async e => {
  e.preventDefault();
  const p = await api('/api/projects', { name: $('#projectName').value });
  projectId = p.id;
  localStorage.setItem('searchtools-project', projectId);
  $('#dialog').close();
  $('#projectName').value = '';
  go('overview');
  await load();
});

document.querySelectorAll('[data-page]').forEach(b => b.onclick = () => go(b.dataset.page));
document.querySelectorAll('[data-tool]').forEach(a => a.onclick = e => {
  e.preventDefault();
  native(a.dataset.tool);
});

let detailStatus = null;

load().catch(e => toast(e.message));

// Fast 1.5s real-time polling for active jobs and model synchronization
setInterval(async () => {
  try {
    if (detail) {
      if (['queued', 'waiting', 'running'].includes(detailStatus)) {
        const j = await api('/api/job?id=' + detail);
        detailStatus = j.status;
        if (['queued', 'waiting', 'running'].includes(j.status)) {
          const badge = $('#detailJobBadge');
          if (badge) {
            badge.className = `badge ${j.status}`;
            badge.textContent = statuses[j.status] || j.status;
          }
          const stageEl = $('#detailJobStageMsg');
          if (stageEl) stageEl.textContent = `⏳ ${j.message || '正在执行任务...'}`;
          const elapsedEl = $('#detailJobElapsed');
          if (elapsedEl) {
            const secs = Math.max(1, Math.round(Date.now() / 1000 - (j.created || Date.now() / 1000)));
            elapsedEl.textContent = `已耗时 ${secs} 秒 · 实时更新中`;
          }
          const logEl = $('#detailJobLog');
          if (logEl) {
            const newLog = j.log || '正在启动执行引擎...';
            if (logEl.textContent !== newLog) {
              logEl.textContent = newLog;
              logEl.scrollTop = logEl.scrollHeight;
            }
          }
        } else {
          await load(false);
          await showJob(detail);
        }
      }
      return;
    }

    if (page === 'ask' && window.AskPage) {
      await window.AskPage.pollRunningJob();
    }

    const hasRunning = project?.jobs?.some(j => ['queued', 'waiting', 'running'].includes(j.status));
    if (hasRunning || ['tasks', 'overview'].includes(page)) {
      const prevSig = JSON.stringify((project?.jobs || []).map(j => [j.id, j.status, j.message]));
      await load(false);
      const nextSig = JSON.stringify((project?.jobs || []).map(j => [j.id, j.status, j.message]));
      if (prevSig !== nextSig && ['tasks', 'overview', 'research'].includes(page)) {
        saveDraft();
        draw();
      }
    }
  } catch {}
}, 1500);

// Periodic & visibility-driven model sync check (every 2.5s)
async function syncModelsOnForeground() {
  try {
    if (page === 'models' && window.ModelsPage?.refreshIfChanged) {
      await window.ModelsPage.refreshIfChanged();
    } else if (page === 'ask' && window.AskPage?.refreshModelBadge) {
      await window.AskPage.refreshModelBadge();
    }
  } catch {}
}

setInterval(syncModelsOnForeground, 2500);
window.searchtoolsOnTabVisible = () => {
  syncModelsOnForeground();
  if (page === 'ask' && window.AskPage?.pollRunningJob) {
    window.AskPage.pollRunningJob();
  }
};
window.addEventListener('focus', window.searchtoolsOnTabVisible);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) window.searchtoolsOnTabVisible();
});

// Zoom controls
let uiZoom = Number(localStorage.getItem('searchtools-zoom') || 1);
window.searchtoolsSetZoom = z => {
  uiZoom = Math.max(.6, Math.min(2, Number(z) || 1));
  $('#zoomReset').textContent = Math.round(uiZoom * 100) + '%';
  if (!window.__SEARCHTOOLS_ZOOM_NATIVE__) {
    document.documentElement.style.setProperty('--ui-zoom', String(uiZoom));
    localStorage.setItem('searchtools-zoom', String(uiZoom));
  }
};

function zoomBy(action) {
  if (window.__SEARCHTOOLS_ZOOM_NATIVE__) {
    location.href = 'searchtools://zoom/' + action;
    return;
  }
  window.searchtoolsSetZoom(action === 'reset' ? 1 : Math.round((uiZoom + (action === 'in' ? .1 : -.1)) * 10) / 10);
}

$('#zoomIn').onclick = () => zoomBy('in');
$('#zoomOut').onclick = () => zoomBy('out');
$('#zoomReset').onclick = () => zoomBy('reset');
window.searchtoolsSetZoom(window.__SEARCHTOOLS_ZOOM_NATIVE__ ? 1 : uiZoom);

if (!window.__SEARCHTOOLS_ZOOM_NATIVE__) {
  document.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (['+', '=', '-', '0'].includes(e.key)) {
      e.preventDefault();
      zoomBy(e.key === '0' ? 'reset' : e.key === '-' ? 'out' : 'in');
    }
  });
}
