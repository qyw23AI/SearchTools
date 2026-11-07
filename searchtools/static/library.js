// Zotero & Local Library management UI
window.LibraryPage = (function() {
  let zoteroStatus = null;
  let collections = [];
  let currentNav = 'all'; // 'all', 'project', 'col:KEY', 'search:KEY', 'tag:TAG'
  let currentSearch = '';
  let currentMode = 'titleCreatorYear';
  let onlyPdf = false;
  let items = [];
  let selectedItemKeys = new Set();
  let inspectedItem = null;

  async function loadData() {
    try {
      zoteroStatus = await api('/api/library/status');
    } catch {
      zoteroStatus = { connected: false };
    }
    if (zoteroStatus.connected) {
      try {
        collections = await api('/api/library/collections');
      } catch {
        collections = [];
      }
    }
    await loadItems();
  }

  async function loadItems() {
    if (currentNav === 'project') {
      items = (project?.papers || []).map(p => ({
        key: p.zotero_item || p.id,
        title: p.title || p.name.replace(/^[a-f0-9]{12}_/, ''),
        year: '',
        creators: [],
        has_pdf: p.name.toLowerCase().endsWith('.pdf'),
        doi: p.doi || '',
        origin: p.origin || 'upload',
        id: p.id,
        name: p.name,
        size: p.size
      }));
      return;
    }

    if (!zoteroStatus.connected) {
      items = [];
      return;
    }

    let col = '', search = '', tag = '';
    if (currentNav.startsWith('col:')) col = currentNav.slice(4);
    if (currentNav.startsWith('search:')) search = currentNav.slice(7);
    if (currentNav.startsWith('tag:')) tag = currentNav.slice(4);

    try {
      const query = new URLSearchParams({
        collection: col,
        search: search,
        tag: tag,
        q: currentSearch,
        mode: currentMode,
        limit: '150'
      });
      items = await api('/api/library/items?' + query.toString());
      if (onlyPdf) {
        items = items.filter(x => x.has_pdf);
      }
    } catch (err) {
      items = [];
      toast(err.message);
    }
  }

  function render() {
    const isConn = zoteroStatus?.connected;

    return `
      <div class="intro">
        <div class="overline">RESEARCH LIBRARY</div>
        <h1>文献管理与 Zotero 接入</h1>
        <p>无缝连接本地 Zotero 知识库，支持层级分类、高亮标注、笔记萃取与全文证据回溯。</p>
      </div>

      <div class="lib-grid">
        <!-- Left: Navigator -->
        <div class="lib-nav">
          <div style="padding-bottom:10px;border-bottom:1px solid #edf1eb">
            <div style="font-size:12px;font-weight:600;margin-bottom:6px">Zotero 状态</div>
            <div style="display:flex;align-items:center;gap:6px;font-size:12px">
              <span class="dot" style="background:${isConn ? '#36855b' : '#c47d2b'}"></span>
              <span>${isConn ? `已连接 (v${esc(zoteroStatus.version)})` : '未连接本地 Zotero'}</span>
            </div>
            ${!isConn ? `
              <div class="quiet" style="font-size:11px;margin-top:6px;line-height:1.4">
                提示：请打开 Zotero 桌面端，在「编辑/设置 → 高级」中勾选“允许此计算机上的其他应用程序与 Zotero 通信”。
              </div>
            ` : ''}
          </div>

          <div>
            <div class="eyebrow">文献来源</div>
            <div class="tree-node ${currentNav === 'project' ? 'active' : ''}" data-nav="project">
              <span>📁 本项目文献</span>
              <span class="tag">${project?.papers?.length || 0}</span>
            </div>
            ${isConn ? `
              <div class="tree-node ${currentNav === 'all' ? 'active' : ''}" data-nav="all">
                <span>📚 Zotero 全部文献</span>
              </div>
            ` : ''}
          </div>

          ${isConn && collections.length > 0 ? `
            <div>
              <div class="eyebrow">Zotero 分类 (Collections)</div>
              <div style="display:flex;flex-direction:column;gap:3px">
                ${collections.map(c => `
                  <div class="tree-node ${currentNav === 'col:' + c.key ? 'active' : ''}" data-nav="col:${c.key}">
                    <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.name)}</span>
                    <span class="quiet" style="font-size:11px">${c.count}</span>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          <div style="margin-top:auto;border-top:1px solid #edf1eb;padding-top:12px">
            <label class="file-input" style="padding:12px;font-size:12px">
              ＋ 手动上传本地文件
              <input id="libUpload" type="file" multiple accept=".pdf,.txt,.md">
            </label>
            <button id="libIndexBtn" class="secondary" style="width:100%;margin-top:8px;font-size:12px">
              为本项目建立索引
            </button>
          </div>
        </div>

        <!-- Center: Item List -->
        <div class="lib-items">
          <div style="display:flex;gap:10px;margin-bottom:14px;align-items:center;flex-wrap:wrap">
            <input id="libSearchInput" style="flex:1;min-width:200px" placeholder="搜索论文标题、作者、年份或标注..." value="${esc(currentSearch)}">
            <select id="libSearchMode" style="width:130px">
              <option value="titleCreatorYear" ${currentMode === 'titleCreatorYear' ? 'selected' : ''}>标题/作者</option>
              <option value="everything" ${currentMode === 'everything' ? 'selected' : ''}>全文内容</option>
            </select>
            <label style="font-size:12px;display:flex;align-items:center;gap:4px;cursor:pointer">
              <input type="checkbox" id="onlyPdfCheck" ${onlyPdf ? 'checked' : ''}> 仅带 PDF
            </label>
          </div>

          ${selectedItemKeys.size > 0 ? `
            <div style="background:#eaf2ec;padding:8px 14px;border-radius:8px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:center">
              <span style="font-size:13px;color:#285a3f">已选 <b>${selectedItemKeys.size}</b> 篇文献</span>
              <div style="display:flex;gap:8px">
                <button id="batchImportBtn" style="padding:6px 14px;font-size:12px">加入当前项目</button>
                <button id="batchMindmapBtn" class="secondary" style="padding:6px 14px;font-size:12px">生成思维导图</button>
              </div>
            </div>
          ` : ''}

          <div class="paper-list">
            ${items.map(item => {
              const isSelected = selectedItemKeys.has(item.key);
              const isInspected = inspectedItem?.key === item.key;
              const inProject = (project?.papers || []).some(p => p.zotero_item === item.key || p.name.includes(item.key));

              return `
                <div class="paper-row ${isInspected ? 'selected' : ''}" data-key="${item.key}">
                  <input type="checkbox" class="paper-check" data-k="${item.key}" ${isSelected ? 'checked' : ''} style="margin-top:4px">
                  <div style="flex:1;min-width:0">
                    <div style="font-weight:600;font-size:14px;color:#203029">${esc(item.title)}</div>
                    <div class="paper-meta">
                      ${item.year ? `<span>📅 ${esc(item.year)}</span>` : ''}
                      ${item.creators?.length ? `<span>👤 ${esc(item.creators.slice(0, 3).join(', '))}${item.creators.length > 3 ? ' 等' : ''}</span>` : ''}
                      ${item.publication ? `<span>📖 ${esc(item.publication)}</span>` : ''}
                    </div>
                  </div>
                  <div style="display:flex;flex-direction:column;gap:4px;align-items:flex-end">
                    ${item.has_pdf ? '<span class="badge">PDF</span>' : ''}
                    ${inProject ? '<span class="badge" style="background:#e0ede3;color:#2b6848">已在项目</span>' : ''}
                  </div>
                </div>
              `;
            }).join('') || '<div class="empty">没有检索到符合条件的文献</div>'}
          </div>
        </div>

        <!-- Right: Inspector -->
        <div class="lib-detail">
          ${inspectedItem ? renderInspector(inspectedItem) : '<div class="empty">点击文献条目查看摘要、高亮批注与笔记详情</div>'}
        </div>
      </div>
    `;
  }

  function renderInspector(detail) {
    const isLocalProject = currentNav === 'project';
    const inProject = (project?.papers || []).some(p => p.zotero_item === detail.key);

    return `
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <h2 style="font-size:16px;line-height:1.4">${esc(detail.title)}</h2>
      </div>

      <div class="paper-meta" style="margin:10px 0 16px">
        ${detail.year ? `<span>年份: ${esc(detail.year)}</span>` : ''}
        ${detail.doi ? `<span>DOI: <a href="https://doi.org/${esc(detail.doi)}" target="_blank">${esc(detail.doi)}</a></span>` : ''}
      </div>

      <div style="display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap">
        ${!inProject ? `<button id="importSingleBtn" style="font-size:12px;padding:6px 12px">导入到当前项目</button>` : '<span class="badge" style="background:#edf4ec;color:#286847">✓ 已在当前项目中</span>'}
        ${detail.key && !isLocalProject ? `
          <a href="zotero://select/library/items/${detail.key}" class="secondary pill" style="display:inline-block">在 Zotero 中打开 ↗</a>
        ` : ''}
        ${detail.annotations?.length || detail.notes?.length ? `
          <button id="importNotesBtn" class="secondary" style="font-size:12px;padding:6px 12px">生成《阅读笔记.md》</button>
        ` : ''}
        ${isLocalProject ? `
          <button id="removePaperBtn" class="secondary" style="color:#b23928;font-size:12px;padding:6px 12px">从项目中移除</button>
        ` : ''}
      </div>

      ${detail.abstract ? `
        <div style="margin-bottom:20px">
          <div style="font-size:12px;font-weight:600;margin-bottom:6px;color:#45554d">摘要 (Abstract)</div>
          <div style="font-size:13px;line-height:1.6;color:#35443d;background:#fafbf9;padding:12px;border-radius:8px">
            ${esc(detail.abstract)}
          </div>
        </div>
      ` : ''}

      ${detail.annotations?.length ? `
        <div style="margin-bottom:20px">
          <div style="font-size:12px;font-weight:600;margin-bottom:8px;color:#45554d">
            Zotero 高亮与批注 (${detail.annotations.length})
          </div>
          <div>
            ${detail.annotations.map(a => `
              <div class="annotation-box" style="border-left-color:${a.color || '#488c6b'}">
                <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-bottom:4px">
                  <span>${esc(a.label || '批注')}</span>
                  <span>${a.page ? `p.${esc(a.page)}` : ''}</span>
                </div>
                ${a.text ? `<div style="font-style:italic;margin-bottom:4px">“${esc(a.text)}”</div>` : ''}
                ${a.comment ? `<div style="color:#2b5e43;font-weight:500">批注: ${esc(a.comment)}</div>` : ''}
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}

      ${detail.notes?.length ? `
        <div style="margin-bottom:20px">
          <div style="font-size:12px;font-weight:600;margin-bottom:8px;color:#45554d">条目笔记 (${detail.notes.length})</div>
          ${detail.notes.map(n => `
            <div style="background:#fafbf8;border:1px solid #edf1eb;border-radius:8px;padding:10px;font-size:13px;line-height:1.6;margin-bottom:8px">
              ${esc(n.text)}
            </div>
          `).join('')}
        </div>
      ` : ''}
    `;
  }

  function bind() {
    // Navigation items
    document.querySelectorAll('[data-nav]').forEach(el => {
      el.onclick = async () => {
        currentNav = el.dataset.nav;
        inspectedItem = null;
        selectedItemKeys.clear();
        await loadItems();
        updateUI();
      };
    });

    // Search input
    const sInput = $('#libSearchInput');
    if (sInput) {
      sInput.onkeydown = async (e) => {
        if (e.key === 'Enter') {
          currentSearch = sInput.value.trim();
          await loadItems();
          updateUI();
        }
      };
    }

    const sMode = $('#libSearchMode');
    if (sMode) {
      sMode.onchange = async (e) => {
        currentMode = e.target.value;
        await loadItems();
        updateUI();
      };
    }

    const pdfCheck = $('#onlyPdfCheck');
    if (pdfCheck) {
      pdfCheck.onchange = async (e) => {
        onlyPdf = e.target.checked;
        await loadItems();
        updateUI();
      };
    }

    // Checkbox select
    document.querySelectorAll('.paper-check').forEach(cb => {
      cb.onchange = (e) => {
        e.stopPropagation();
        const k = cb.dataset.k;
        if (cb.checked) selectedItemKeys.add(k);
        else selectedItemKeys.delete(k);
        updateUI();
      };
    });

    // Paper row click to inspect
    document.querySelectorAll('.paper-row').forEach(row => {
      row.onclick = run(async (e) => {
        if (e.target.type === 'checkbox') return;
        const k = row.dataset.key;
        if (currentNav === 'project') {
          inspectedItem = items.find(x => x.key === k) || null;
        } else {
          inspectedItem = await api('/api/library/item?id=' + k);
        }
        updateUI();
      });
    });

    // Import single item
    const importSingle = $('#importSingleBtn');
    if (importSingle) {
      importSingle.onclick = run(async () => {
        if (!inspectedItem) return;
        await api('/api/library/import', { project: projectId, keys: [inspectedItem.key] });
        toast('文献已成功导入当前项目！');
        await load(false);
        updateUI();
      });
    }

    // Import annotations as reading notes
    const importNotes = $('#importNotesBtn');
    if (importNotes) {
      importNotes.onclick = run(async () => {
        if (!inspectedItem) return;
        await api('/api/library/notes', { project: projectId, key: inspectedItem.key });
        toast('《阅读笔记.md》已成功生成并加入项目文献！PaperQA2 可直接检索。');
        await load(false);
        updateUI();
      });
    }

    // Remove paper from project
    const removeBtn = $('#removePaperBtn');
    if (removeBtn) {
      removeBtn.onclick = run(async () => {
        if (!confirm('确定要从当前项目中移除此文献吗？')) return;
        await api('/api/paper/remove', { project: projectId, id: inspectedItem.id });
        toast('文献已从项目中移除');
        inspectedItem = null;
        await load(false);
        await loadItems();
        updateUI();
      });
    }

    // Batch import
    const batchImport = $('#batchImportBtn');
    if (batchImport) {
      batchImport.onclick = run(async () => {
        const keys = Array.from(selectedItemKeys);
        await api('/api/library/import', { project: projectId, keys: keys });
        toast(`已成功将 ${keys.length} 篇文献导入当前项目！`);
        selectedItemKeys.clear();
        await load(false);
        updateUI();
      });
    }

    // Batch mindmap
    const batchMM = $('#batchMindmapBtn');
    if (batchMM) {
      batchMM.onclick = () => {
        const keys = Array.from(selectedItemKeys).join(',');
        window.open(`/api/mindmap?items=${encodeURIComponent(keys)}&format=md`, '_blank');
      };
    }

    // Manual upload
    const up = $('#libUpload');
    if (up) {
      up.onchange = run(async (e) => {
        for (const f of e.target.files) {
          const r = await fetch('/api/upload', {
            method: 'POST',
            headers: {
              'X-SearchTools-Token': token,
              'X-Project': projectId,
              'X-Filename': encodeURIComponent(f.name)
            },
            body: f
          });
          const j = await r.json();
          if (!r.ok) throw Error(j.error);
        }
        toast('文献已成功导入');
        await load(false);
        await loadItems();
        updateUI();
      });
    }

    // Index button
    const idxBtn = $('#libIndexBtn');
    if (idxBtn) {
      idxBtn.onclick = run(async () => {
        await api('/api/jobs', { project: projectId, kind: 'index' });
        toast('索引任务已加入执行队列');
        go('tasks');
        await load();
      });
    }
  }

  function updateUI() {
    $('#content').innerHTML = render();
    bind();
  }

  return {
    render: async () => {
      await loadData();
      return render();
    },
    bind: bind
  };
})();
