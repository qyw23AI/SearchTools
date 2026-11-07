// Model catalog management UI supporting both DeepTutor Sync Mode and Custom Mode
window.ModelsPage = (function() {
  let catalog = null;
  let activeConnId = null;
  let activeService = 'llm';
  let isEditingAdvanced = false;
  let testStatus = null;
  let dtTestStatus = null;

  async function loadCatalog() {
    catalog = await api('/api/models');
    if (!activeConnId && catalog.connections.length > 0) {
      const currentSvcConn = catalog.services[activeService]?.connection;
      activeConnId = currentSvcConn || catalog.connections[0].id;
    }
  }

  function render() {
    if (!catalog) return '<div class="empty">加载模型配置中...</div>';

    const p = catalog;
    const mode = p.mode || 'deeptutor';
    const conns = p.connections || [];
    const activeConn = conns.find(c => c.id === activeConnId) || conns[0] || null;
    const providerDef = activeConn ? p.providers[activeConn.provider] : null;
    const dtSync = p.deeptutor_sync || {};
    const dtInfo = dtSync.info || {};

    let html = `
      <div class="intro">
        <div class="overline">MODEL CONFIGURATION</div>
        <h1>模型与服务商设置</h1>
        <p>提供「DeepTutor 实时同步模式」与「独立配置模式」，让 PaperQA2 既能零配置直接复用 DeepTutor 当前模型，也可独立设置专属端点。</p>
      </div>

      <!-- Mode Selector Panel -->
      <div class="mode-selector-panel">
        <div class="mode-header">
          <h3>运行模式选择 (Operation Mode)</h3>
          <span class="tag" style="background:${mode === 'deeptutor' ? '#e0f2fe;color:#0284c7;font-weight:700' : '#f1f5f9;color:#475569'}">
            当前：${mode === 'deeptutor' ? '🔗 DeepTutor 同步模式' : '⚙️ 独立配置模式'}
          </span>
        </div>
        <div class="mode-options-grid">
          <div class="mode-card ${mode === 'deeptutor' ? 'selected' : ''}" data-mode-btn="deeptutor">
            <div class="mode-radio">${mode === 'deeptutor' ? '●' : '○'}</div>
            <div class="mode-info">
              <h4>DeepTutor 同步模式 (实时同步 · 推荐)</h4>
              <p>实时检测并继承 DeepTutor 当前选中的生成大模型与向量模型。DeepTutor 切换模型，PaperQA2 Web 问答与 MCP 检索立刻生效，无需重复配 Key。</p>
              ${dtSync.available ? `
                <div class="sync-preview">
                  <div><b>当前检测 LLM</b>: ${esc(dtInfo.llm_provider || 'Cloud').toUpperCase()} · ${esc(dtInfo.llm_model || 'active')}</div>
                  <div><b>当前检测 Embedding</b>: ${esc(dtInfo.emb_provider || 'Local').toUpperCase()} · ${esc(dtInfo.emb_model || 'active')}</div>
                </div>
              ` : `
                <div class="sync-preview" style="color:#b23528;background:#fef2f2">
                  ⚠️ 未检测到 DeepTutor 模型配置文件
                </div>
              `}
            </div>
          </div>

          <div class="mode-card ${mode === 'custom' ? 'selected' : ''}" data-mode-btn="custom">
            <div class="mode-radio">${mode === 'custom' ? '●' : '○'}</div>
            <div class="mode-info">
              <h4>自动 / 独立配置模式</h4>
              <p>在 SearchTools 中独立管理服务商连接、API Key 与模型分配。不受 DeepTutor 模型切换影响，适合为本地文献检索指定专属的本地或云端端点。</p>
              <div style="font-size:11.5px;color:#64748b;margin-top:6px">
                可自由添加 OpenAI、Gemini、DeepSeek、Ollama 等任意连接。
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="catalog-grid">
        <!-- Sidebar: Service Assignments -->
        <div class="catalog-sidebar">
          <div class="panel" style="margin-top:0">
            <div class="section-title">
              <h3>服务分配</h3>
              ${mode === 'deeptutor' ? '<span class="tag" style="background:#e0f2fe;color:#0369a1">同步托管</span>' : ''}
            </div>
            <div class="service-list">
              ${Object.entries(p.service_labels).map(([key, label]) => {
                const sel = p.services[key] || {};
                const conn = conns.find(c => c.id === sel.connection);
                const isCur = key === activeService;
                return `
                  <div class="service-item ${isCur ? 'active' : ''}" data-service="${key}">
                    <div>
                      <div class="svc-name">${esc(label)}</div>
                      <div class="svc-val">${conn ? `${esc(conn.name)} · ${esc(sel.model || '未选')}` : '<span style="color:#a84435">未分配</span>'}</div>
                    </div>
                    ${isCur ? '<span>●</span>' : ''}
                  </div>
                `;
              }).join('')}
            </div>
          </div>

          ${mode === 'custom' ? `
            <div class="panel">
              <div class="section-title">
                <h3>自定义连接列表</h3>
                <button class="subtle" id="addConnBtn">＋ 新建连接</button>
              </div>
              <div class="conn-list">
                ${conns.filter(c => !c.is_deeptutor_sync).map(c => {
                  const isActive = c.id === activeConnId;
                  const prov = p.providers[c.provider] || {};
                  return `
                    <div class="conn-item ${isActive ? 'active' : ''}" data-conn="${c.id}">
                      <div>
                        <div style="font-size:13px;font-weight:600">${esc(c.name)}</div>
                        <div class="quiet">${esc(prov.name || c.provider)}</div>
                      </div>
                      <span class="tag">${c.api_key_set ? '已配 Key' : '免 Key'}</span>
                    </div>
                  `;
                }).join('')}
              </div>
            </div>
          ` : `
            <div class="panel">
              <h3>DeepTutor 联动说明</h3>
              <p style="font-size:12px;color:#64748b;line-height:1.6;margin:0">
                当前运行在 <b>DeepTutor 同步模式</b> 下。PaperQA2 的任务执行器与 MCP 工具服务均已自动绑定 DeepTutor。<br><br>
                若需自定义单独的 API Key 或更换服务商，可点击上方切换为「独立配置模式」。
              </p>
            </div>
          `}

          <div class="panel" style="font-size:11px;color:var(--muted)">
            当前索引特征码: <code>${esc(p.index_name || '')}</code>
            <br>更换向量模型时 PaperQA2 会自动基于新特征码创建独立索引，不混淆向量。
          </div>
        </div>

        <!-- Main Workspace -->
        <div class="catalog-main">
          ${mode === 'deeptutor' ? renderDeepTutorSyncView(dtInfo) : (activeConn ? renderConnEditor(activeConn, providerDef) : '<div class="empty">请添加或选择一个连接</div>')}
        </div>
      </div>
    `;

    return html;
  }

  function renderDeepTutorSyncView(info) {
    const p = catalog;
    const llmName = `${(info.llm_provider || 'Cloud').toUpperCase()} · ${info.llm_model || 'active'}`;
    const embName = `${(info.emb_provider || 'Local').toUpperCase()} · ${info.emb_model || 'active'}`;

    return `
      <div class="dt-sync-banner">
        <div class="dt-sync-status">
          <span class="status-indicator live"></span>
          <h3>DeepTutor 模型实时同步工作台 (已激活)</h3>
        </div>
        <p style="margin:10px 0 16px;color:#475569;font-size:13px;line-height:1.6">
          已成功连通 DeepTutor 运行时配置。无论您在 DeepTutor 的主聊天窗口下拉列表切换模型，还是在 DeepTutor 全局配置中修改服务商，SearchTools 的 <b>论文问答工作台 (Web)</b> 与 <b>DeepTutor MCP 实证工具</b> 均会自动直接复用当前模型。
        </p>

        <div class="dt-sync-details-grid">
          <div class="sync-detail-card">
            <div class="sync-k">当前继承生成模型 (LLM)</div>
            <div class="sync-v" style="color:#0284c7">${esc(llmName)}</div>
            <div class="sync-sub">服务商绑定: <code>${esc(info.llm_binding || 'active')}</code> | 授权: 跟随 DeepTutor</div>
          </div>
          <div class="sync-detail-card">
            <div class="sync-k">当前继承向量模型 (Embedding)</div>
            <div class="sync-v" style="color:#059669">${esc(embName)}</div>
            <div class="sync-sub">端点: <code>${esc(info.emb_base_url || '本地 Ollama')}</code> | 状态: 正常</div>
          </div>
        </div>

        <div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap">
          <button id="refreshDtBtn" class="primary">🔄 重新检测 DeepTutor 最新模型</button>
          <button type="button" class="secondary" id="testDtModelBtn">⚡ 测试当前 DeepTutor 模型连通性</button>
          <button type="button" class="secondary" id="jumpDtTabBtn">📖 切到 DeepTutor 界面</button>
        </div>

        ${dtTestStatus ? `
          <div class="test-box ${dtTestStatus.ok ? 'success' : 'error'}" style="margin-top:14px">
            ${dtTestStatus.ok
              ? `✓ 测试成功！延迟 ${dtTestStatus.latency_ms} ms (模型: ${esc(dtTestStatus.model)})`
              : `✗ 连接失败: ${esc(dtTestStatus.error)}`
            }
          </div>
        ` : ''}
      </div>

      <!-- Advanced retrieval parameters -->
      <details style="margin-top:24px" open>
        <summary style="font-weight:600;font-size:13px;color:#334155;cursor:pointer">PaperQA2 检索与切片参数 (可自主调节)</summary>
        <div style="padding-top:14px">
          <div class="form-row">
            <div class="form-group">
              <label>Temperature (回答发散度: 0.0–2.0)</label>
              <input type="number" step="0.1" id="paramTemp" value="${p.params.temperature}">
            </div>
            <div class="form-group">
              <label>回答语言倾向</label>
              <select id="paramLang">
                <option value="auto" ${p.params.answer_language === 'auto' ? 'selected' : ''}>自动 (跟随提问语言)</option>
                <option value="zh" ${p.params.answer_language === 'zh' ? 'selected' : ''}>强制中文 (Simplified Chinese)</option>
                <option value="en" ${p.params.answer_language === 'en' ? 'selected' : ''}>强制英文 (English)</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>分块字符上限 (Chunk chars)</label>
              <input type="number" id="indexChunk" value="${p.index.chunk_chars}">
            </div>
            <div class="form-group">
              <label>分块重叠字数 (Overlap)</label>
              <input type="number" id="indexOverlap" value="${p.index.overlap}">
            </div>
          </div>
          <div style="margin-top:12px">
            <button id="saveSyncParamsBtn" class="secondary">保存检索切片参数</button>
          </div>
        </div>
      </details>
    `;
  }

  function renderConnEditor(conn, prov) {
    const p = catalog;
    const currentServiceLabel = p.service_labels[activeService];
    const isAssigned = p.services[activeService]?.connection === conn.id;

    return `
      <div class="section-title">
        <h2>编辑连接：${esc(conn.name)}</h2>
        <button class="secondary" id="deleteConnBtn" style="color:#b23624" ${conn.id === 'conn-ollama' ? 'disabled' : ''}>删除此连接</button>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>服务商类型</label>
          <select id="editProvider">
            ${Object.entries(p.providers).map(([k, v]) => `
              <option value="${k}" ${k === conn.provider ? 'selected' : ''}>${esc(v.name)}</option>
            `).join('')}
          </select>
        </div>
        <div class="form-group">
          <label>连接名称 (自定义标识)</label>
          <input id="editName" value="${esc(conn.name)}" placeholder="如：我的 Gemini 账号">
        </div>
      </div>

      ${prov && prov.hint ? `<div class="quiet" style="margin-bottom:14px;background:#f8f9f6;padding:8px 12px;border-radius:8px">💡 ${esc(prov.hint)}</div>` : ''}

      <div class="form-group">
        <label>API Base URL ${prov?.needs_key ? '(可选/非官方中转时填写)' : '(本地或指定服务地址)'}</label>
        <input id="editBaseUrl" value="${esc(conn.base_url || '')}" placeholder="${esc(prov?.base_url || 'https://...')}">
      </div>

      ${prov?.needs_key ? `
        <div class="form-group">
          <label>API Key ${conn.api_key_set ? `<span class="quiet">(${esc(conn.api_key_hint)})</span>` : ''}</label>
          <div class="secret-wrap">
            <input type="password" id="editApiKey" placeholder="${conn.api_key_set ? '留空保持原有 Key 不变' : '填入密钥，如 AIzaSy... / sk-...'}">
            <button type="button" class="secondary" id="toggleKeyVis">👁</button>
          </div>
        </div>
      ` : ''}

      <div class="form-group">
        <label>可用模型列表 (回车或点击候选添加)</label>
        <div class="tag-input-wrap" id="modelsTagWrap">
          ${(conn.models || []).map(m => `
            <span class="tag-chip" data-model="${esc(m)}">${esc(m)} <span class="remove">&times;</span></span>
          `).join('')}
          <input id="newModelInput" style="border:0;outline:0;padding:4px;flex:1;min-width:140px" placeholder="+ 输入模型名称">
        </div>
        ${prov && (prov.llm?.length || prov.embedding?.length) ? `
          <div class="quiet" style="margin-top:8px">
            常用推荐: ${[...(prov.llm || []), ...(prov.embedding || [])].map(m => `
              <a href="#" class="suggest-model" data-m="${esc(m)}" style="margin-right:8px;text-decoration:underline">${esc(m)}</a>
            `).join('')}
          </div>
        ` : ''}
      </div>

      <div style="background:#f4f7f4;border-radius:10px;padding:16px;margin:20px 0">
        <div style="font-weight:600;font-size:13px;margin-bottom:8px">分配给当前服务：「${esc(currentServiceLabel)}」</div>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <select id="assignModelSelect" style="max-width:240px">
            ${(conn.models || []).map(m => `
              <option value="${esc(m)}" ${p.services[activeService]?.model === m && isAssigned ? 'selected' : ''}>${esc(m)}</option>
            `).join('')}
          </select>
          <button id="assignBtn" class="${isAssigned ? 'secondary' : ''}">
            ${isAssigned ? '✓ 当前正在使用' : `设为「${esc(currentServiceLabel)}」`}
          </button>
        </div>
      </div>

      <div style="border-top:1px solid #edf1eb;padding-top:18px;margin-top:20px">
        <div class="actions" style="margin-top:0">
          <button id="saveCatalogBtn">保存所有设置</button>
          <button type="button" class="secondary" id="testConnBtn">⚡ 测试当前连接</button>
        </div>
        ${testStatus ? `
          <div class="test-box ${testStatus.ok ? 'success' : 'error'}">
            ${testStatus.ok
              ? `✓ 测试成功！延迟 ${testStatus.latency_ms} ms (模型: ${esc(testStatus.model)})`
              : `✗ 连接失败: ${esc(testStatus.error)}`
            }
          </div>
        ` : ''}
      </div>

      <details style="margin-top:24px">
        <summary>高级模型生成与切片参数 (Temperature / Token / Chunk)</summary>
        <div style="padding-top:14px">
          <div class="form-row">
            <div class="form-group">
              <label>Temperature (多样性: 0.0–2.0)</label>
              <input type="number" step="0.1" id="paramTemp" value="${p.params.temperature}">
            </div>
            <div class="form-group">
              <label>回答语言倾向</label>
              <select id="paramLang">
                <option value="auto" ${p.params.answer_language === 'auto' ? 'selected' : ''}>自动 (跟随提问语言)</option>
                <option value="zh" ${p.params.answer_language === 'zh' ? 'selected' : ''}>强制中文 (Simplified Chinese)</option>
                <option value="en" ${p.params.answer_language === 'en' ? 'selected' : ''}>强制英文 (English)</option>
              </select>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>分块字符上限 (Chunk chars)</label>
              <input type="number" id="indexChunk" value="${p.index.chunk_chars}">
            </div>
            <div class="form-group">
              <label>分块重叠字数 (Overlap)</label>
              <input type="number" id="indexOverlap" value="${p.index.overlap}">
            </div>
          </div>
        </div>
      </details>
    `;
  }

  function bind() {
    // Mode switcher buttons
    document.querySelectorAll('[data-mode-btn]').forEach(el => {
      el.onclick = run(async () => {
        const targetMode = el.dataset.modeBtn;
        if (targetMode === catalog.mode) return;
        catalog = await api('/api/models/mode', { mode: targetMode });
        toast(targetMode === 'deeptutor' ? '已切换至 DeepTutor 同步模式' : '已切换至独立配置模式');
        updateUI();
      });
    });

    // DeepTutor Sync View Buttons
    const refreshDtBtn = $('#refreshDtBtn');
    if (refreshDtBtn) {
      refreshDtBtn.onclick = run(async () => {
        refreshDtBtn.disabled = true;
        refreshDtBtn.textContent = '检测中...';
        try {
          await loadCatalog();
          toast('已更新 DeepTutor 模型同步状态');
          updateUI();
        } finally {
          refreshDtBtn.disabled = false;
        }
      });
    }

    const testDtModelBtn = $('#testDtModelBtn');
    if (testDtModelBtn) {
      testDtModelBtn.onclick = run(async () => {
        testDtModelBtn.disabled = true;
        testDtModelBtn.textContent = '测试中...';
        try {
          const dtInfo = catalog.deeptutor_sync?.info || {};
          const payload = {
            connection: {
              id: 'conn-dt-synced-llm',
              is_deeptutor_sync: true,
              provider: dtInfo.llm_provider || 'gemini',
              model: dtInfo.llm_model || 'gemini-3.6-flash',
              base_url: dtInfo.llm_base_url || '',
            },
            service: 'llm'
          };
          const res = await api('/api/models/test', payload);
          dtTestStatus = res;
        } catch (err) {
          dtTestStatus = { ok: false, error: err.message };
        } finally {
          testDtModelBtn.disabled = false;
          testDtModelBtn.textContent = '⚡ 测试当前 DeepTutor 模型连通性';
          updateUI();
        }
      });
    }

    const jumpDtTabBtn = $('#jumpDtTabBtn');
    if (jumpDtTabBtn) {
      jumpDtTabBtn.onclick = () => {
        native('deeptutor');
      };
    }

    const saveSyncParamsBtn = $('#saveSyncParamsBtn');
    if (saveSyncParamsBtn) {
      saveSyncParamsBtn.onclick = run(async () => {
        if ($('#paramTemp')) catalog.params.temperature = parseFloat($('#paramTemp').value);
        if ($('#paramLang')) catalog.params.answer_language = $('#paramLang').value;
        if ($('#indexChunk')) catalog.index.chunk_chars = parseInt($('#indexChunk').value);
        if ($('#indexOverlap')) catalog.index.overlap = parseInt($('#indexOverlap').value);
        catalog = await api('/api/models', {
          params: catalog.params,
          index: catalog.index
        });
        toast('检索切片参数已保存');
        updateUI();
      });
    }

    // Service selector clicks
    document.querySelectorAll('[data-service]').forEach(el => {
      el.onclick = () => {
        activeService = el.dataset.service;
        const selConn = catalog.services[activeService]?.connection;
        if (selConn) activeConnId = selConn;
        testStatus = null;
        updateUI();
      };
    });

    // Connection item clicks
    document.querySelectorAll('[data-conn]').forEach(el => {
      el.onclick = () => {
        activeConnId = el.dataset.conn;
        testStatus = null;
        updateUI();
      };
    });

    // Add connection
    const addBtn = $('#addConnBtn');
    if (addBtn) addBtn.onclick = () => {
      const newId = 'conn-' + Math.random().toString(36).slice(2, 9);
      catalog.connections.push({
        id: newId,
        name: '新建 Gemini 连接',
        provider: 'gemini',
        base_url: '',
        api_key: '',
        models: ['gemini-2.5-flash', 'gemini-2.5-pro']
      });
      activeConnId = newId;
      testStatus = null;
      updateUI();
    };

    // Provider select change
    const provSelect = $('#editProvider');
    if (provSelect) provSelect.onchange = (e) => {
      const cur = catalog.connections.find(c => c.id === activeConnId);
      if (cur) {
        cur.provider = e.target.value;
        const tpl = catalog.providers[cur.provider];
        if (tpl) {
          cur.base_url = tpl.base_url || '';
          cur.models = [...(tpl.llm || []), ...(tpl.embedding || [])];
        }
        updateUI();
      }
    };

    // Name change
    const nameInput = $('#editName');
    if (nameInput) nameInput.oninput = (e) => {
      const cur = catalog.connections.find(c => c.id === activeConnId);
      if (cur) cur.name = e.target.value;
    };

    // Base URL change
    const baseInput = $('#editBaseUrl');
    if (baseInput) baseInput.oninput = (e) => {
      const cur = catalog.connections.find(c => c.id === activeConnId);
      if (cur) cur.base_url = e.target.value;
    };

    // Toggle API Key visibility
    const toggleKey = $('#toggleKeyVis');
    if (toggleKey) toggleKey.onclick = () => {
      const inp = $('#editApiKey');
      if (inp) inp.type = inp.type === 'password' ? 'text' : 'password';
    };

    // Remove model tag
    document.querySelectorAll('.tag-chip .remove').forEach(btn => {
      btn.onclick = (e) => {
        const chip = e.target.closest('.tag-chip');
        const m = chip.dataset.model;
        const cur = catalog.connections.find(c => c.id === activeConnId);
        if (cur) {
          cur.models = cur.models.filter(x => x !== m);
          updateUI();
        }
      };
    });

    // Add suggested model
    document.querySelectorAll('.suggest-model').forEach(a => {
      a.onclick = (e) => {
        e.preventDefault();
        const m = a.dataset.m;
        const cur = catalog.connections.find(c => c.id === activeConnId);
        if (cur && !cur.models.includes(m)) {
          cur.models.push(m);
          updateUI();
        }
      };
    });

    // Add model by Enter
    const newModelInput = $('#newModelInput');
    if (newModelInput) {
      newModelInput.onkeydown = (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const val = newModelInput.value.trim();
          const cur = catalog.connections.find(c => c.id === activeConnId);
          if (val && cur && !cur.models.includes(val)) {
            cur.models.push(val);
            newModelInput.value = '';
            updateUI();
          }
        }
      };
    }

    // Delete connection
    const delBtn = $('#deleteConnBtn');
    if (delBtn) delBtn.onclick = () => {
      if (!confirm('确定要删除此连接吗？')) return;
      catalog.connections = catalog.connections.filter(c => c.id !== activeConnId);
      activeConnId = catalog.connections[0]?.id || null;
      updateUI();
    };

    // Assign to active service
    const assignBtn = $('#assignBtn');
    if (assignBtn) assignBtn.onclick = () => {
      const selModel = $('#assignModelSelect')?.value;
      if (!selModel) {
        toast('请先在模型列表中添加模型');
        return;
      }
      catalog.services[activeService] = {
        connection: activeConnId,
        model: selModel
      };
      toast(`已成功指定为「${catalog.service_labels[activeService]}」`);
      updateUI();
    };

    // Test connection
    const testBtn = $('#testConnBtn');
    if (testBtn) testBtn.onclick = run(async () => {
      testBtn.disabled = true;
      testBtn.textContent = '测试连接中...';
      const cur = catalog.connections.find(c => c.id === activeConnId);
      const testModel = $('#assignModelSelect')?.value || cur?.models[0];
      const keyVal = $('#editApiKey')?.value;

      try {
        const payload = {
          connection: {
            ...cur,
            api_key: keyVal || undefined,
            model: testModel
          },
          service: activeService === 'embedding' ? 'embedding' : 'llm'
        };
        const res = await api('/api/models/test', payload);
        testStatus = res;
      } catch (err) {
        testStatus = { ok: false, error: err.message };
      } finally {
        testBtn.disabled = false;
        testBtn.textContent = '⚡ 测试当前连接';
        updateUI();
      }
    });

    // Save all settings in custom mode
    const saveBtn = $('#saveCatalogBtn');
    if (saveBtn) saveBtn.onclick = run(async () => {
      const cur = catalog.connections.find(c => c.id === activeConnId);
      if (cur) {
        const keyInput = $('#editApiKey');
        if (keyInput && keyInput.value) {
          cur.api_key = keyInput.value.trim();
        }
      }
      if ($('#paramTemp')) catalog.params.temperature = parseFloat($('#paramTemp').value);
      if ($('#paramLang')) catalog.params.answer_language = $('#paramLang').value;
      if ($('#indexChunk')) catalog.index.chunk_chars = parseInt($('#indexChunk').value);
      if ($('#indexOverlap')) catalog.index.overlap = parseInt($('#indexOverlap').value);

      saveBtn.disabled = true;
      try {
        catalog = await api('/api/models', catalog);
        toast('模型配置与参数已成功保存！');
        updateUI();
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  function updateUI() {
    $('#content').innerHTML = render();
    bind();
  }

  async function refreshIfChanged() {
    if (page !== 'models') return;
    // Do not interrupt if user is actively typing in an input field in custom mode
    const activeEl = document.activeElement;
    if (activeEl && ['INPUT', 'TEXTAREA', 'SELECT'].includes(activeEl.tagName)) return;
    try {
      const latest = await api('/api/models');
      const oldModel = catalog?.deeptutor_sync?.info?.llm_model || '';
      const newModel = latest?.deeptutor_sync?.info?.llm_model || '';
      const oldProv = catalog?.deeptutor_sync?.info?.llm_provider || '';
      const newProv = latest?.deeptutor_sync?.info?.llm_provider || '';
      const oldMode = catalog?.mode || '';
      const newMode = latest?.mode || '';
      if (oldModel !== newModel || oldProv !== newProv || oldMode !== newMode) {
        catalog = latest;
        updateUI();
      }
    } catch {}
  }

  return {
    render: async () => {
      await loadCatalog();
      return render();
    },
    bind: bind,
    refreshIfChanged: refreshIfChanged
  };
})();
