/**
 * Academic Evidence & Literature Workbench v3 (Standalone + Embedded Iframe)
 */
(function () {
  const TOKEN = window.__SEARCHTOOLS_TOKEN__ || 'local-mcp';
  let currentScopeId = '';
  let scopesList = [];
  let selectedCardIds = new Set();
  let cachedCards = [];
  let activeRunningJobId = '';

  // Theme initialization from URL query or parent postMessage
  const params = new URLSearchParams(window.location.search);
  if (params.get('theme') === 'dark') {
    document.documentElement.classList.add('dark');
  }
  window.addEventListener('message', (ev) => {
    if (!ev.data || typeof ev.data !== 'object') return;
    if (ev.data.type === 'st-theme') {
      document.documentElement.classList.toggle('dark', ev.data.theme === 'dark');
    } else if (ev.data.type === 'st-refresh') {
      refreshAll();
    }
  });

  function showToast(msg) {
    const el = document.getElementById('wb-toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.remove('hidden');
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.add('hidden'), 2800);
  }

  function esc(str) {
    const d = document.createElement('div');
    d.textContent = str == null ? '' : String(str);
    return d.innerHTML;
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-SearchTools-Token': TOKEN,
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
  }

  function insertTextToHost(text) {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'st-insert-text', text }, '*');
      showToast('已发送引用至 DeepTutor 编辑器');
    } else {
      navigator.clipboard.writeText(text).then(() => showToast('已复制引用到剪贴板'));
    }
  }

  // Tab switching
  document.querySelectorAll('.wb-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll('.wb-tab').forEach((b) => b.classList.toggle('active', b === btn));
      document.querySelectorAll('.wb-panel').forEach((p) => p.classList.toggle('active', p.id === `wb-panel-${tab}`));
    });
  });

  // Scope selection & Creation
  const scopeSelect = document.getElementById('wb-scope-select');

  async function promptCreateScope() {
    const name = window.prompt('请输入新研究范围名称（如：具身智能避障、四足越障控制等）：');
    if (!name || !name.trim()) {
      if (scopeSelect) scopeSelect.value = currentScopeId;
      return;
    }
    try {
      showToast('正在创建新研究范围...');
      const res = await api('/api/scope/create', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
      currentScopeId = res.scope_id;
      showToast(`已创建研究范围：${name.trim()}`);
      await refreshAll();
    } catch (e) {
      showToast('创建失败: ' + e.message);
      if (scopeSelect) scopeSelect.value = currentScopeId;
    }
  }

  const quickNewScopeBtn = document.getElementById('wb-btn-quick-new-scope');
  if (quickNewScopeBtn) {
    quickNewScopeBtn.addEventListener('click', promptCreateScope);
  }

  scopeSelect.addEventListener('change', async () => {
    if (scopeSelect.value === '__NEW__') {
      await promptCreateScope();
      return;
    }
    currentScopeId = scopeSelect.value;
    selectedCardIds.clear();
    updateBatchBar();
    loadCards();
    loadScopeDetail();
    loadCandidateBatches();
    loadTraces();
  });

  document.getElementById('wb-btn-refresh').addEventListener('click', () => {
    refreshAll();
    showToast('已刷新工作台状态');
  });

  async function loadHeaderStatus() {
    try {
      const [stState, zotSt, dtSt] = await Promise.all([
        api('/api/state'),
        api('/api/library/status').catch(() => ({ connected: false })),
        api('/api/deeptutor/status').catch(() => ({})),
      ]);

      scopesList = stState.projects || [];
      if (!currentScopeId && scopesList.length > 0) {
        currentScopeId = scopesList[scopesList.length - 1].id;
      }
      scopeSelect.innerHTML =
        scopesList
          .map((p) => `<option value="${esc(p.id)}" ${p.id === currentScopeId ? 'selected' : ''}>${esc(p.name)}</option>`)
          .join('') + `<option value="__NEW__">➕ 新建研究范围...</option>`;

      const zotEl = document.getElementById('wb-dot-zotero');
      if (zotSt.connected) {
        zotEl.innerHTML = `<i class="dot ok"></i> Zotero (${zotSt.total ?? 0})`;
      } else {
        zotEl.innerHTML = `<i class="dot err"></i> Zotero 离线`;
      }

      const modLabel = document.getElementById('wb-model-label');
      const syncInfo = (dtSt && dtSt.model_sync) || {};
      modLabel.textContent = syncInfo.llm_model ? `${syncInfo.llm_model}` : '已同步模型';
    } catch (e) {
      console.warn('loadHeaderStatus error:', e);
    }
  }

  // ==================== Tab 1: Live Trace ====================
  function renderTraces(traces, activeJobs) {
    const badge = document.getElementById('wb-badge-trace');
    badge.textContent = String((traces || []).length);

    // Active running job banner
    const jobBanner = document.getElementById('wb-active-job-bar');
    const jobMsg = document.getElementById('wb-job-msg');
    if (activeJobs && activeJobs.length > 0) {
      const j = activeJobs[0];
      activeRunningJobId = j.id;
      jobMsg.textContent = `[${j.kind}] ${j.message || '执行中...'} (${(j.prompt || '').slice(0, 32)})`;
      jobBanner.classList.remove('hidden');
    } else {
      activeRunningJobId = '';
      jobBanner.classList.add('hidden');
    }

    const listEl = document.getElementById('wb-trace-list');
    if (!traces || traces.length === 0) {
      listEl.innerHTML = `<div class="wb-card"><div class="wb-hint">暂无工具调用记录。当大模型在对话中调用 Zotero 检索、覆盖度判定、PaperQA2 取证或补充文献时，此处将实时显示完整溯源时间线。</div></div>`;
      return;
    }

    listEl.innerHTML = traces
      .map((t) => {
        const dt = new Date((t.created || 0) * 1000);
        const timeStr = dt.toTimeString().slice(0, 8);
        const st = t.status || 'done';
        const badgeCls = st === 'done' ? 'ok' : st === 'waiting' || st === 'running' ? 'warn' : 'err';
        const detail = t.detail || {};
        let extraHtml = '';
        if (detail.sufficiency) {
          const suf = detail.sufficiency;
          const sufCls = suf === 'sufficient' ? 'ok' : suf === 'partial' ? 'warn' : 'err';
          const missing = (detail.missing_aspects || []).join('；');
          extraHtml += `<div style="margin-top:6px;"><span class="wb-badge ${sufCls}">覆盖度: ${esc(suf)}</span> ${
            missing ? `<span class="wb-hint">缺口: ${esc(missing)}</span>` : ''
          }</div>`;
        }
        if (detail.batch_id) {
          extraHtml += `<div style="margin-top:6px;"><button class="wb-btn wb-btn-primary wb-btn-xs" data-goto-acquire="1">前往「补充文献」勾选确认 (${esc(detail.total_staged || 0)} 篇)</button></div>`;
        }
        return `
          <div class="wb-card">
            <div class="wb-card-header">
              <span class="wb-card-title"><code>${esc(t.tool)}</code></span>
              <span class="wb-badge ${badgeCls}">${esc(timeStr)} · ${esc(st)}</span>
            </div>
            <div class="wb-summary">${esc(t.summary)}</div>
            ${extraHtml}
          </div>
        `;
      })
      .join('');

    listEl.querySelectorAll('[data-goto-acquire]').forEach((b) => {
      b.addEventListener('click', () => {
        document.querySelector('.wb-tab[data-tab="acquire"]').click();
      });
    });
  }

  async function loadTraces() {
    try {
      const res = await api(`/api/traces?scope_id=${encodeURIComponent(currentScopeId)}`);
      renderTraces(res.traces || [], res.active_jobs || []);
    } catch (e) {
      console.warn('loadTraces error:', e);
    }
  }

  document.getElementById('wb-job-cancel').addEventListener('click', async () => {
    if (!activeRunningJobId) return;
    try {
      await api('/api/cancel', { method: 'POST', body: JSON.stringify({ id: activeRunningJobId }) });
      showToast('已发送取消任务指令');
      loadTraces();
    } catch (e) {
      showToast('取消失败: ' + e.message);
    }
  });

  // ==================== Tab 2: Evidence Cards v2 ====================
  function updateBatchBar() {
    const cnt = selectedCardIds.size;
    const style = document.getElementById('wb-cite-style').value.toUpperCase();
    document.getElementById('wb-selected-cards-count').textContent = `已选 ${cnt} 张`;
    const btnCite = document.getElementById('wb-btn-cite-selected');
    const btnWb = document.getElementById('wb-btn-writeback-selected');
    btnCite.disabled = cnt === 0;
    btnWb.disabled = cnt === 0;
    btnCite.textContent = `插入选中引用 (${style})`;
  }

  document.getElementById('wb-cite-style').addEventListener('change', updateBatchBar);
  document.getElementById('wb-card-search').addEventListener('input', () => loadCards());
  document.getElementById('wb-card-score').addEventListener('change', () => loadCards());
  document.getElementById('wb-card-pinned').addEventListener('change', () => loadCards());

  async function loadCards() {
    const q = document.getElementById('wb-card-search').value.trim();
    const minScore = document.getElementById('wb-card-score').value;
    const pinnedOnly = document.getElementById('wb-card-pinned').checked ? '1' : '0';
    try {
      const res = await api(
        `/api/evidence-cards?project=${encodeURIComponent(currentScopeId)}&q=${encodeURIComponent(q)}&min_score=${encodeURIComponent(minScore)}&pinned_only=${pinnedOnly}`
      );
      cachedCards = res.cards || [];
      document.getElementById('wb-badge-cards').textContent = String(cachedCards.length);
      renderCardsList();
    } catch (e) {
      console.warn('loadCards error:', e);
    }
  }

  function renderCardsList() {
    const el = document.getElementById('wb-cards-list');
    if (cachedCards.length === 0) {
      el.innerHTML = `<div class="wb-card"><div class="wb-hint">当前筛选条件下暂无证据卡。可通过对话或「研究范围 -> 提取证据卡」生成。</div></div>`;
      return;
    }

    el.innerHTML = cachedCards
      .map((c) => {
        const src = c.source || {};
        const checked = selectedCardIds.has(c.id) ? 'checked' : '';
        const score = Number(c.score || 0);
        const badgeCls = score >= 7 ? 'ok' : score >= 5 ? 'warn' : 'err';
        const authors = (src.authors || []).slice(0, 2).join(', ') || '未知作者';
        const pg = src.pages ? ` · p.${src.pages}` : '';
        const usedCnt = (c.used_in || []).length;
        const zlink = src.zotero_link || '';
        return `
          <div class="wb-card" data-cid="${esc(c.id)}">
            <div class="wb-card-header">
              <label class="wb-checkbox-label" style="align-items:flex-start;">
                <input type="checkbox" class="wb-card-check" data-id="${esc(c.id)}" ${checked} style="margin-top:3px;">
                <span class="wb-card-title">${esc(src.title || c.title)}</span>
              </label>
              <span class="wb-badge ${badgeCls}">${score}/10</span>
            </div>
            <div class="wb-card-meta">
              <code>${esc(c.id)}</code> · ${esc(authors)} (${esc(src.year || 'n.d.')})${esc(pg)} · citekey: <code>${esc(src.citekey || '')}</code>
              ${c.locate && c.locate.status === 'exact' ? ` · <span class="wb-badge ok" title="已精确检索到原文段落与高亮区域">🎯 精确定位 p.${esc(c.locate.page_label)}</span>` : (c.locate && c.locate.status === 'page_only' ? ` · <span class="wb-badge warn" title="已定位到对应页码">📄 页面定位 p.${esc(c.locate.page_label)}</span>` : '')}
              ${usedCnt > 0 ? ` · <span class="wb-badge ok">已引用 ${usedCnt} 次</span>` : ''}
            </div>
            <div class="wb-summary"><strong>要点：</strong>${esc(c.summary)}</div>
            <div class="wb-quote">“${esc((c.quote || '').slice(0, 360))}”</div>
            <div class="wb-btn-group" style="margin-top:8px;">
              <button class="wb-btn wb-btn-primary wb-btn-xs" data-cite-one="${esc(c.id)}">插入引用</button>
              <button class="wb-btn wb-btn-secondary wb-btn-xs" data-prompt-one="${esc(c.id)}">插入证据块</button>
              ${
                zlink || src.item_key
                  ? `<button class="wb-btn wb-btn-secondary wb-btn-xs" data-open-zotero="${esc(zlink)}" data-cid="${esc(c.id)}">📖 打开原文与高亮${c.locate && c.locate.page_label ? ' p.' + esc(c.locate.page_label) : (src.pages ? ' p.' + esc(src.pages) : '')}</button>`
                  : ''
              }
              <button class="wb-btn wb-btn-secondary wb-btn-xs" data-pin-one="${esc(c.id)}">${c.pinned ? '⭐ 已星标' : '☆ 星标'}</button>
              <button class="wb-btn wb-btn-secondary wb-btn-xs" data-wb-one="${esc(c.id)}">📝 写回 Zotero</button>
            </div>
          </div>
        `;
      })
      .join('');

    el.querySelectorAll('.wb-card-check').forEach((chk) => {
      chk.addEventListener('change', () => {
        if (chk.checked) selectedCardIds.add(chk.dataset.id);
        else selectedCardIds.delete(chk.dataset.id);
        updateBatchBar();
      });
    });

    el.querySelectorAll('[data-cite-one]').forEach((btn) => {
      btn.addEventListener('click', () => citeCards([btn.dataset.citeOne]));
    });
    el.querySelectorAll('[data-prompt-one]').forEach((btn) => {
      btn.addEventListener('click', () => citeCards([btn.dataset.promptOne], 'prompt'));
    });
    el.querySelectorAll('[data-pin-one]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await api('/api/evidence-cards/pin', {
          method: 'POST',
          body: JSON.stringify({ project: currentScopeId, id: btn.dataset.pinOne }),
        });
        loadCards();
      });
    });
    el.querySelectorAll('[data-wb-one]').forEach((btn) => {
      btn.addEventListener('click', () => writebackCards([btn.dataset.wbOne]));
    });
    el.querySelectorAll('[data-open-zotero]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault();
        const uri = btn.dataset.openZotero;
        const cid = btn.dataset.cid;
        showToast('正在打开 Zotero 定位原文...');
        try {
          const res = await api('/api/open-zotero', {
            method: 'POST',
            body: JSON.stringify({ uri, card_id: cid }),
          });
          if (res && res.ok) {
            showToast('已在 Zotero 中定位原文与对应页面');
            return;
          }
        } catch (err) {
          console.warn('Backend /api/open-zotero invocation failed, trying client navigation', err);
        }
        if (uri) {
          window.location.href = uri;
        }
      });
    });
  }

  async function citeCards(cardIds, overrideStyle) {
    const style = overrideStyle || document.getElementById('wb-cite-style').value || 'gbt7714';
    try {
      const res = await api('/api/evidence-cards/format', {
        method: 'POST',
        body: JSON.stringify({
          card_ids: cardIds,
          style,
          auto_writeback: true,
          doc_ref: 'deeptutor:workbench',
        }),
      });
      let textToInsert = res.formatted || '';
      try {
        const parsed = JSON.parse(res.formatted);
        if (parsed && parsed.inline) {
          textToInsert = `${parsed.inline}\n\n参考文献:\n${parsed.bibliography}`;
        }
      } catch (_) {}
      insertTextToHost(textToInsert);
      loadCards();
    } catch (e) {
      showToast('生成引用失败: ' + e.message);
    }
  }

  async function writebackCards(cardIds) {
    try {
      const res = await api('/api/evidence-cards/writeback', {
        method: 'POST',
        body: JSON.stringify({ card_ids: cardIds, scope_id: currentScopeId }),
      });
      showToast(`已将 ${cardIds.length} 张证据卡写回 Zotero 子笔记`);
      return res;
    } catch (e) {
      showToast('写回 Zotero 失败: ' + e.message);
    }
  }

  document.getElementById('wb-btn-cite-selected').addEventListener('click', () => {
    if (selectedCardIds.size > 0) citeCards(Array.from(selectedCardIds));
  });
  document.getElementById('wb-btn-writeback-selected').addEventListener('click', () => {
    if (selectedCardIds.size > 0) writebackCards(Array.from(selectedCardIds));
  });

  // ==================== Tab 3: Research Scope ====================
  async function loadZoteroCollectionsDropdown() {
    try {
      const colls = await api('/api/library/collections');
      const sel = document.getElementById('wb-zot-collection');
      sel.innerHTML =
        `<option value="">-- 选择 Zotero 分类 --</option>` +
        (colls || []).map((c) => `<option value="${esc(c.key)}">${esc(c.name)} (${c.count})</option>`).join('');
    } catch (_) {}
  }

  async function loadScopeDetail() {
    if (!currentScopeId) return;
    try {
      const st = await api(`/api/scope/status?scope_id=${encodeURIComponent(currentScopeId)}`);
      document.getElementById('wb-badge-scope').textContent = String(st.total_papers || 0);
      document.getElementById('wb-scope-metrics').innerHTML = `
        <div class="wb-metric"><div class="wb-metric-val">${st.total_papers || 0}</div><div class="wb-metric-lbl">有 PDF 文献</div></div>
        <div class="wb-metric"><div class="wb-metric-val">${st.indexed || 0}</div><div class="wb-metric-lbl">已建向量索引</div></div>
        <div class="wb-metric"><div class="wb-metric-val">${st.pending || 0}</div><div class="wb-metric-lbl">待建索引</div></div>
      `;

      const papersEl = document.getElementById('wb-scope-papers');
      const papers = st.papers || [];
      if (papers.length === 0) {
        papersEl.innerHTML = `<div class="wb-card"><div class="wb-hint">当前研究范围尚无文献，请在上方选择 Zotero 分类点击「同步分类并建索引」。</div></div>`;
        return;
      }
      papersEl.innerHTML = papers
        .map((p) => {
          const idxSt = p.index_status || 'pending';
          const badgeCls = idxSt === 'indexed' ? 'ok' : idxSt === 'pending' ? 'warn' : 'err';
          const badgeTxt = idxSt === 'indexed' ? '✅ 已索引' : idxSt === 'pending' ? '⏳ 待索引' : '❌ 解析异常';
          return `
            <div class="wb-card">
              <div class="wb-card-header">
                <span class="wb-card-title">${esc(p.title || p.name)}</span>
                <span class="wb-badge ${badgeCls}">${badgeTxt}</span>
              </div>
              <div class="wb-card-meta">
                citekey: <code>${esc(p.citekey || '-')}</code> · Zotero: <code>${esc(p.zotero_item || '-')}</code>
                ${p.year ? ` · ${esc(p.year)}` : ''}
              </div>
            </div>
          `;
        })
        .join('');
    } catch (e) {
      console.warn('loadScopeDetail error:', e);
    }
  }

  document.getElementById('wb-btn-sync-collection').addEventListener('click', async () => {
    const colKey = document.getElementById('wb-zot-collection').value;
    if (!colKey) {
      showToast('请先选择一个 Zotero 分类');
      return;
    }
    try {
      showToast('正在从 Zotero 同步文献...');
      const res = await api('/api/scope/create', {
        method: 'POST',
        body: JSON.stringify({ scope_id: currentScopeId, collection: colKey }),
      });
      if (res.pending_index_count > 0 && res.with_pdf_count > 0) {
        await api('/api/jobs', {
          method: 'POST',
          body: JSON.stringify({ project: res.scope_id, kind: 'index', prompt: '构建分类文献向量索引', source: 'local' }),
        });
      }
      showToast(`已同步 ${res.with_pdf_count} 篇 PDF 文献`);
      await refreshAll();
    } catch (e) {
      showToast('同步失败: ' + e.message);
    }
  });

  document.getElementById('wb-btn-create-scope').addEventListener('click', async () => {
    const name = document.getElementById('wb-new-scope-name').value.trim();
    if (!name) {
      showToast('请输入新研究范围名称');
      return;
    }
    try {
      const res = await api('/api/scope/create', { method: 'POST', body: JSON.stringify({ name }) });
      currentScopeId = res.scope_id;
      document.getElementById('wb-new-scope-name').value = '';
      showToast(`已创建研究范围：${name}`);
      await refreshAll();
    } catch (e) {
      showToast('创建失败: ' + e.message);
    }
  });

  async function runManualScopeTask(kind) {
    const q = document.getElementById('wb-manual-question').value.trim();
    if (!q) {
      showToast('请输入学术问题或待核验论断');
      return;
    }
    const outEl = document.getElementById('wb-manual-result');
    outEl.innerHTML = `<div class="wb-hint">⏳ 已提交任务，正在后台执行...</div>`;
    try {
      const res = await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          project: currentScopeId,
          kind,
          prompt: q,
          source: 'local',
          options: { use_deeptutor_model: true, preset: 'fast' },
        }),
      });
      const jid = res.id;
      const poll = setInterval(async () => {
        try {
          const j = await api(`/api/job?id=${encodeURIComponent(jid)}`);
          if (j.status === 'done') {
            clearInterval(poll);
            const r = j.result || {};
            if (kind === 'coverage') {
              outEl.innerHTML = `<div class="wb-quote"><strong>覆盖度: ${esc(r.sufficiency)}</strong> (高相关 ${r.high_relevance_papers_count || 0} 篇, 最高相似度 ${r.max_score || 0})<br>建议检索词: ${esc((r.suggested_queries || []).join(' / '))}</div>`;
            } else {
              outEl.innerHTML = `<div class="wb-quote">${esc((r.answer || '已完成').slice(0, 600))}</div>`;
            }
            loadCards();
            loadTraces();
          } else if (j.status === 'failed' || j.status === 'cancelled') {
            clearInterval(poll);
            outEl.innerHTML = `<div class="wb-badge err">执行失败: ${esc(j.message)}</div>`;
          } else {
            outEl.innerHTML = `<div class="wb-hint">⏳ ${esc(j.message || '执行中...')}</div>`;
          }
        } catch (_) {}
      }, 1200);
    } catch (e) {
      outEl.innerHTML = `<div class="wb-badge err">提交失败: ${esc(e.message)}</div>`;
    }
  }

  document.getElementById('wb-btn-manual-coverage').addEventListener('click', () => runManualScopeTask('coverage'));
  document.getElementById('wb-btn-manual-evidence').addEventListener('click', () => runManualScopeTask('evidence'));
  document.getElementById('wb-btn-manual-ask').addEventListener('click', () => runManualScopeTask('ask'));

  // ==================== Tab 4: Supplementary Literature Confirmation ====================
  async function loadCandidateBatches() {
    try {
      const res = await api(`/api/candidates?scope_id=${encodeURIComponent(currentScopeId)}`);
      const batches = res.batches || [];
      const pendingCnt = batches
        .filter((b) => b.status === 'pending_confirmation')
        .reduce((acc, b) => acc + (b.candidates || []).length, 0);
      document.getElementById('wb-badge-acquire').textContent = String(pendingCnt);

      const container = document.getElementById('wb-acquire-batches');
      if (batches.length === 0) {
        container.innerHTML = `<div class="wb-card"><div class="wb-hint">暂无待确认的补充文献批次。当大模型检测到文献覆盖度不足并调用外部检索时，候选文献将在此列出（单次上限 20 篇），供您勾选后入库 Zotero。</div></div>`;
        return;
      }

      container.innerHTML = batches
        .map((b) => {
          const isPending = b.status === 'pending_confirmation';
          const cands = b.candidates || [];
          return `
            <div class="wb-card" data-batch="${esc(b.id)}">
              <div class="wb-card-header">
                <span class="wb-card-title">课题：${esc(b.topic || '补充文献')} (${cands.length}/20 篇)</span>
                <span class="wb-badge ${isPending ? 'warn' : 'ok'}">${isPending ? '待人工确认' : '已导入 Zotero'}</span>
              </div>
              ${b.query ? `<div class="wb-card-meta">检索词: ${esc(b.query)}</div>` : ''}
              <div class="wb-list" style="margin:8px 0;">
                ${cands
                  .map(
                    (c) => `
                  <label class="wb-checkbox-label" style="align-items:flex-start; padding:6px; background:var(--wb-surface-alt); border-radius:6px;">
                    <input type="checkbox" class="wb-cand-check" data-batch="${esc(b.id)}" data-idx="${c.index}"
                      ${c.selected ? 'checked' : ''} ${!isPending ? 'disabled' : ''} style="margin-top:3px;">
                    <div style="flex:1;">
                      <div style="font-weight:600; font-size:12.5px;">${esc(c.title)}</div>
                      <div class="wb-card-meta" style="margin:2px 0 0;">
                        ${esc((c.authors || []).slice(0, 3).join(', '))} (${esc(c.year || 'n.d.')})
                        ${c.doi ? ` · DOI: <code>${esc(c.doi)}</code>` : ''}
                        ${c.in_zotero ? ` · <span class="wb-badge ok">已在库 (${esc(c.existing_key)})</span>` : ''}
                        ${c.has_oa_pdf ? ` · <span class="wb-badge ok">有 OA PDF</span>` : ` · <span class="wb-badge warn">仅元数据</span>`}
                      </div>
                    </div>
                  </label>
                `
                  )
                  .join('')}
              </div>
              ${
                isPending
                  ? `<div class="wb-btn-group">
                      <button class="wb-btn wb-btn-primary wb-btn-sm" data-confirm-batch="${esc(b.id)}">✅ 导入选中文献到 Zotero 并建立索引</button>
                    </div>`
                  : ''
              }
            </div>
          `;
        })
        .join('');

      container.querySelectorAll('[data-confirm-batch]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const bid = btn.dataset.confirmBatch;
          const checks = container.querySelectorAll(`.wb-cand-check[data-batch="${bid}"]:checked`);
          const selectedIndices = Array.from(checks).map((c) => Number(c.dataset.idx));
          if (selectedIndices.length === 0) {
            showToast('请至少勾选一篇候选文献');
            return;
          }
          btn.disabled = true;
          btn.textContent = '⏳ 正在下载 OA PDF 并导入 Zotero...';
          try {
            const res = await api('/api/candidates/confirm', {
              method: 'POST',
              body: JSON.stringify({
                batch_id: bid,
                scope_id: currentScopeId,
                selected_indices: selectedIndices,
                fetch_pdf: true,
              }),
            });
            showToast(`成功入库 ${(res.imported || []).length} 篇文献至 Zotero「${res.collection_path}」`);
            await refreshAll();
          } catch (e) {
            showToast('导入失败: ' + e.message);
            btn.disabled = false;
            btn.textContent = '✅ 导入选中文献到 Zotero 并建立索引';
          }
        });
      });
    } catch (e) {
      console.warn('loadCandidateBatches error:', e);
    }
  }

  document.getElementById('wb-btn-stage-identifiers').addEventListener('click', async () => {
    const raw = document.getElementById('wb-manual-identifiers').value.trim();
    if (!raw) {
      showToast('请输入 DOI 或 arXiv ID');
      return;
    }
    const ids = raw
      .split(/[\n,;，；]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 20);
    try {
      showToast('正在解析文献元数据...');
      await api('/api/candidates/stage', {
        method: 'POST',
        body: JSON.stringify({
          scope_id: currentScopeId,
          topic: scopeSelect.options[scopeSelect.selectedIndex]?.text || '手动补充',
          query: '手动输入标识符',
          candidates: ids.map((id) => ({ identifier: id, title: id })),
        }),
      });
      document.getElementById('wb-manual-identifiers').value = '';
      await loadCandidateBatches();
      showToast(`已添加 ${ids.length} 条候选记录，请核对后点击导入`);
    } catch (e) {
      showToast('解析失败: ' + e.message);
    }
  });

  const btnClean = document.getElementById('wb-btn-clean-highlights');
  if (btnClean) {
    btnClean.addEventListener('click', async () => {
      showToast('正在向 Zotero 派发清理临时高亮指令...');
      try {
        await api('/api/open-zotero', {
          method: 'POST',
          body: JSON.stringify({ action: 'cleanup' }),
        });
        showToast('已向 Zotero 派发一键清理临时高亮指令！');
      } catch (err) {
        showToast('清理请求派发失败: ' + err.message);
      }
    });
  }

  // SSE stream for real-time trace & job updates
  function connectEventStream() {
    try {
      const es = new EventSource(`/api/events?scope_id=${encodeURIComponent(currentScopeId)}`);
      es.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          renderTraces(data.traces || [], data.active_jobs || []);
          if (data.pending_batches) {
            const cnt = data.pending_batches.reduce((a, b) => a + (b.candidates || []).length, 0);
            document.getElementById('wb-badge-acquire').textContent = String(cnt);
          }
        } catch (_) {}
      };
      es.onerror = () => {
        es.close();
        setTimeout(connectEventStream, 5000);
      };
    } catch (_) {}
  }

  async function checkZoteroPluginStatus() {
    const el = document.getElementById('wb-zotero-status');
    if (!el) return;
    try {
      const res = await api('/api/zotero/actions?status=1');
      if (res && res.connected) {
        el.className = 'wb-badge ok';
        el.textContent = '🟢 Zotero 在线';
        el.title = 'Zotero 插件已就绪：点击卡片可直接翻至对应页码并高亮论据！';
      } else {
        el.className = 'wb-badge warn';
        el.textContent = '⚪ Zotero 离线';
        el.title = 'Zotero 未运行或插件未启用，点击卡片将尝试协议直连。';
      }
    } catch (_) {
      el.className = 'wb-badge warn';
      el.textContent = '⚪ 插件离线';
    }
  }

  async function refreshAll() {
    await loadHeaderStatus();
    await checkZoteroPluginStatus();
    await Promise.all([loadTraces(), loadCards(), loadScopeDetail(), loadCandidateBatches(), loadZoteroCollectionsDropdown()]);
  }

  refreshAll().then(() => {
    connectEventStream();
    setInterval(checkZoteroPluginStatus, 3000);
  });
})();
