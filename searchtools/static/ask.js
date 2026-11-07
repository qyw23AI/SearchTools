// PaperQA2 Workbench UI: 3-column interactive evidence & Q&A
window.AskPage = (function() {
  let indexStatus = null;
  let activeJob = null;
  let activePreset = 'balanced';
  let activeKind = 'ask'; // 'ask' or 'evidence'
  let selectedPapersScope = 'all'; // 'all' or specific paper ID
  let activeContextId = null;
  let modelsCatalog = null;

  async function loadData() {
    if (projectId) {
      try {
        indexStatus = await api('/api/index/status?project=' + projectId);
      } catch {
        indexStatus = null;
      }
    }
    try {
      modelsCatalog = await api('/api/models');
    } catch {
      modelsCatalog = null;
    }
    // Auto-attach to currently running job if activeJob is not set
    if (!activeJob && project?.jobs) {
      const running = project.jobs.find(j => ['ask', 'evidence', 'index'].includes(j.kind) && ['queued', 'waiting', 'running'].includes(j.status));
      if (running) {
        try {
          activeJob = await api('/api/job?id=' + running.id);
        } catch {}
      }
    }
  }

  function render() {
    const p = project;
    if (!p) return '<div class="empty">请先在左侧选择或新建一个研究项目</div>';

    const jobs = p.jobs.filter(j => ['ask', 'evidence', 'index'].includes(j.kind));
    const curResult = activeJob?.result;
    const isRunning = activeJob && ['queued', 'waiting', 'running'].includes(activeJob.status);

    return `
      <div class="intro">
        <div class="overline">PAPERQA2 WORKBENCH</div>
        <h1>论文证据检索与问答工作台</h1>
        <p>基于 PaperQA2 严谨的学术检索管线。答案严格依托文献原文证据，支持证据溯源、相关度打分与引用核验。</p>
      </div>

      <div class="workbench-3col">
        <!-- Left: Question History & Index Status -->
        <div class="wb-col">
          <div class="wb-sidebar">
            <div style="font-size:12px;font-weight:600;margin-bottom:8px">索引健康度</div>
            ${indexStatus ? `
              <div style="font-size:13px;line-height:1.7;margin-bottom:10px">
                <div>已建立索引: <b>${indexStatus.indexed}</b> / ${indexStatus.total} 篇</div>
                ${indexStatus.failed > 0 ? `<div style="color:#b23b2b">解析失败: <b>${indexStatus.failed}</b> 篇</div>` : ''}
                ${indexStatus.pending > 0 ? `<div class="quiet">待建立索引: ${indexStatus.pending} 篇</div>` : ''}
              </div>
              <button id="rebuildIndexBtn" class="secondary" style="width:100%;font-size:12px">
                ${indexStatus.ready ? '重新构建索引' : '⚡ 建立 / 更新索引'}
              </button>
            ` : '<div class="quiet">暂无索引状态</div>'}
          </div>

          <div class="wb-sidebar" style="flex:1">
            <div class="section-title">
              <div style="font-size:12px;font-weight:600">问答记录</div>
              <button class="subtle" id="newAskBtn">＋ 新提问</button>
            </div>
            <div style="display:flex;flex-direction:column;gap:6px">
              ${jobs.map(j => {
                const isActive = activeJob?.id === j.id;
                const canDel = !['queued', 'waiting', 'running'].includes(j.status);
                return `
                  <div class="tree-node ${isActive ? 'active' : ''}" data-job-id="${j.id}" style="display:flex;justify-content:space-between;align-items:center">
                    <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;flex:1;margin-right:6px">
                      ${esc(j.prompt || '问答')}
                    </div>
                    <div style="display:flex;align-items:center;gap:4px">
                      <span class="badge ${j.status}" style="font-size:10px;padding:2px 6px">${statuses[j.status] || j.status}</span>
                      ${canDel ? `<button class="subtle" data-del-job="${j.id}" title="删除此记录" style="padding:0 3px;font-size:16px;color:#b23928;line-height:1;border:0">&times;</button>` : ''}
                    </div>
                  </div>
                `;
              }).join('') || '<div class="empty" style="padding:15px 0">暂无问答记录</div>'}
            </div>
          </div>
        </div>

        <!-- Center: Ask Form & Answer View -->
        <div class="wb-main">
          <div class="panel" style="margin-top:0">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
              <div class="mode-toggle">
                <button type="button" class="${activeKind === 'ask' ? 'active' : ''}" data-kind="ask">论文深度问答</button>
                <button type="button" class="${activeKind === 'evidence' ? 'active' : ''}" data-kind="evidence">仅检索证据卡</button>
              </div>

              <div class="preset-pills" style="margin:0">
                <span class="quiet" style="font-size:11px">预设：</span>
                <span class="pill ${activePreset === 'fast' ? 'active' : ''}" data-preset="fast">快速 (k=5)</span>
                <span class="pill ${activePreset === 'balanced' ? 'active' : ''}" data-preset="balanced">平衡 (k=10)</span>
                <span class="pill ${activePreset === 'deep' ? 'active' : ''}" data-preset="deep">深入 (k=20)</span>
              </div>
            </div>

            <textarea id="askQuestion" placeholder="${activeKind === 'ask'
              ? '例如：比较这几篇论文的训练设置、模型结构与实验主要结论，并标明依据。'
              : '输入关键词或假设，如：ANN-SNN转换中时间步长与精度的折中关系'
            }">${esc(drafts[projectId + 'ask'] || '')}</textarea>

            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;font-size:11.5px;color:#475569">
              <span>🤖 执行模型:
                <b id="askModelBadge" style="color:#0284c7">
                  ${modelsCatalog?.mode === 'deeptutor'
                    ? `🔗 DeepTutor 同步 (${esc((modelsCatalog.deeptutor_sync?.info?.llm_provider || 'DeepTutor').toUpperCase())} · ${esc(modelsCatalog.deeptutor_sync?.info?.llm_model || 'active')})`
                    : `⚙️ 自定义 (${esc(modelsCatalog?.services?.llm?.model || '已分配')})`
                  }
                </b>
              </span>
              <a href="#models" id="jumpModelsLink" style="color:#0284c7;text-decoration:none;font-weight:600">切换模式 / 设置 →</a>
            </div>

            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px;flex-wrap:wrap;gap:12px">
              <div style="display:flex;align-items:center;gap:8px">
                <span class="quiet" style="font-size:12px">检索范围:</span>
                <select id="paperScope" style="font-size:12px;padding:6px 10px;max-width:200px">
                  <option value="all">本项目全部文献 (${p.papers.length} 篇)</option>
                  ${p.papers.map(f => `
                    <option value="${f.id}" ${selectedPapersScope === f.id ? 'selected' : ''}>${esc(f.name.replace(/^[a-f0-9]{12}_/, ''))}</option>
                  `).join('')}
                </select>
              </div>

              <div class="actions" style="margin:0">
                <button id="runAskBtn">${activeKind === 'ask' ? '开始向文献提问' : '检索支撑证据'}</button>
              </div>
            </div>
          </div>

          <!-- Answer & Result Display -->
          ${activeJob ? `
            <div class="panel">
              <div style="display:flex;justify-content:space-between;align-items:flex-start">
                <div>
                  <div style="font-weight:600;font-size:15px;color:#22332a">${esc(activeJob.prompt || '建立项目文献索引')}</div>
                  <div class="quiet" style="margin-top:4px">
                    ${new Date(activeJob.created * 1000).toLocaleString()} · ${kinds[activeJob.kind] || activeJob.kind}
                  </div>
                </div>
                <span class="badge ${activeJob.status}" id="activeJobStatusBadge">${statuses[activeJob.status] || activeJob.status}</span>
              </div>

              ${isRunning ? `
                <div style="margin:20px 0;padding:16px;background:#f5f8f4;border-radius:10px;border:1px solid #d7e4d8">
                  <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;color:#2d5e44;font-size:13px;font-weight:600">
                    <div style="display:flex;align-items:center;gap:10px">
                      <span class="dot" style="background:#2d5e44"></span>
                      <span id="activeJobStageMsg">${esc(activeJob.message || '正在执行 PaperQA2 检索与推理计算...')}</span>
                    </div>
                    <span class="quiet" id="activeJobElapsed" style="font-size:11.5px;font-weight:400">已耗时 ${Math.max(1, Math.round(Date.now() / 1000 - (activeJob.created || Date.now() / 1000)))} 秒 · 实时更新中</span>
                  </div>
                  <details style="margin-top:12px" open>
                    <summary style="cursor:pointer;font-size:12px;color:#394c42;font-weight:600">实时执行日志 (自动流式刷新)</summary>
                    <pre class="log" id="activeJobLiveLog" style="max-height:240px;overflow-y:auto">${esc(activeJob.log || '正在启动 Python 引擎子进程...')}</pre>
                  </details>
                  <div style="margin-top:14px">
                    <button id="cancelJobBtn" class="secondary" style="color:#b23528">取消任务</button>
                    <button id="refreshJobBtn" class="subtle" style="margin-left:10px">立即刷新</button>
                  </div>
                </div>
              ` : ''}

              ${curResult ? `
                <div style="margin-top:18px">
                  <div class="answer" id="formattedAnswerBody">${formatAnswerWithChips(curResult.answer)}</div>

                  ${curResult.references ? `
                    <div class="references-box">
                      <div style="font-weight:600;margin-bottom:6px;color:#394c42">参考文献与来源 (References)</div>
                      <div style="white-space:pre-wrap">${esc(curResult.references)}</div>
                    </div>
                  ` : ''}

                  <div class="meta-bar">
                    ${curResult.cost ? `<span>估算费用: \$${curResult.cost.toFixed(4)}</span>` : ''}
                    ${curResult.token_counts?.total_tokens ? `<span>Tokens: ${curResult.token_counts.total_tokens}</span>` : ''}
                    <span>证据卡片: <b>${curResult.contexts?.length || 0}</b> 条</span>
                  </div>

                  <div class="actions" style="margin-top:20px;flex-wrap:wrap;gap:8px">
                    <button id="pushToDtDocBtn" class="primary">📝 在 DeepTutor 写作台打开此综述</button>
                    <button id="saveAsNoteBtn" class="secondary">存为项目笔记</button>
                    <button id="handoffDtBtn" class="secondary">复制交接内容</button>
                    <a href="/api/export?id=${activeJob.id}" class="secondary pill" style="display:inline-block">导出 Markdown ↓</a>
                    <button id="deleteActiveJobBtn" class="secondary" style="color:#b23528">删除此记录</button>
                  </div>
                </div>
              ` : (!isRunning ? `
                <div class="empty">未生成结果或执行异常，请查看日志</div>
                <div class="actions" style="margin-top:12px">
                  <button id="deleteActiveJobBtn" class="secondary" style="color:#b23528">删除此记录</button>
                </div>
              ` : '')}

              ${!isRunning && activeJob.log ? `
                <details style="margin-top:20px">
                  <summary>引擎执行日志</summary>
                  <pre class="log">${esc(activeJob.log)}</pre>
                </details>
              ` : ''}
            </div>
          ` : ''}
        </div>

        <!-- Right: Evidence Cards -->
        <div class="wb-evidence">
          <div style="font-size:14px;font-weight:600;color:#283a31;margin-bottom:12px">
            支撑证据卡片 ${curResult?.contexts ? `(${curResult.contexts.length})` : ''}
          </div>

          ${curResult?.contexts?.length ? `
            <div>
              ${curResult.contexts.map((ctx, idx) => {
                const isSelected = activeContextId === ctx.id;
                return `
                  <div class="evidence-card ${isSelected ? 'active' : ''}" id="ctx-${ctx.id}" data-ctx-id="${ctx.id}">
                    <div class="ev-header">
                      <span class="ev-score">相关度 ★ ${ctx.score || '高'}</span>
                      <span class="ev-doc" title="${esc(ctx.citation || ctx.name)}">${esc(ctx.name || `来源 ${idx + 1}`)}</span>
                    </div>
                    <div class="ev-summary">${esc(ctx.summary)}</div>
                    ${ctx.text && ctx.text !== ctx.summary ? `
                      <details style="margin-top:6px">
                        <summary style="font-size:11px">查看原始摘录片段</summary>
                        <div class="ev-text">${esc(ctx.text)}</div>
                      </details>
                    ` : ''}
                    <div class="ev-card-actions" style="display:flex;gap:6px;margin-top:8px">
                      <button class="secondary pill btn-save-card" data-ctx-id="${ctx.id}" style="font-size:11px;padding:3px 8px">⭐ 收藏到证据库</button>
                      <button class="secondary pill btn-cite-dt" data-ctx-id="${ctx.id}" style="font-size:11px;padding:3px 8px">📎 复制引用</button>
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          ` : `
            <div class="empty" style="padding:40px 10px;text-align:center;font-size:12px;line-height:1.6">
              提问完成后，PaperQA2 从各论文中切片并打分的原文证据将在这里呈现。
              <br><br>
              在回答中点击引用标签可直接定位到对应卡片。
            </div>
          `}
        </div>
      </div>
    `;
  }

  function formatAnswerWithChips(rawText) {
    if (!rawText) return '';
    // Format citations like (Chen2023 pages 1-2) or (Author2024) into interactive chips
    const safe = esc(rawText);
    return safe.replace(/\(([^()\n]+?\d{4}[^()\n]*?)\)/g, (match, inner) => {
      return `<span class="citation-chip" data-cite="${inner}">${match}</span>`;
    });
  }

  function bind() {
    // Mode toggles
    document.querySelectorAll('.mode-toggle button').forEach(btn => {
      btn.onclick = () => {
        activeKind = btn.dataset.kind;
        updateUI();
      };
    });

    // Preset pills
    document.querySelectorAll('.preset-pills .pill').forEach(pill => {
      pill.onclick = () => {
        activePreset = pill.dataset.preset;
        updateUI();
      };
    });

    // Job history selection
    document.querySelectorAll('[data-job-id]').forEach(el => {
      el.onclick = run(async () => {
        const id = el.dataset.jobId;
        activeJob = await api('/api/job?id=' + id);
        updateUI();
      });
    });

    // New ask button
    const newAsk = $('#newAskBtn');
    if (newAsk) {
      newAsk.onclick = () => {
        activeJob = null;
        updateUI();
      };
    }

    // Rebuild index button
    const rebuildBtn = $('#rebuildIndexBtn');
    if (rebuildBtn) {
      rebuildBtn.onclick = run(async () => {
        rebuildBtn.disabled = true;
        try {
          const res = await api('/api/jobs', { project: projectId, kind: 'index' });
          toast('索引构建任务已启动，正在实时显示进度');
          await load(false);
          activeJob = await api('/api/job?id=' + res.id);
          updateUI();
        } finally {
          rebuildBtn.disabled = false;
        }
      });
    }

    const jumpModelsLink = $('#jumpModelsLink');
    if (jumpModelsLink) {
      jumpModelsLink.onclick = (e) => {
        e.preventDefault();
        go('models');
      };
    }

    // Run ask button
    const runBtn = $('#runAskBtn');
    if (runBtn) {
      runBtn.onclick = run(async () => {
        const q = $('#askQuestion')?.value.trim();
        if (!q) {
          toast('请输入研究问题');
          return;
        }
        drafts[projectId + 'ask'] = q;
        const scope = $('#paperScope')?.value;
        const options = {
          preset: activePreset,
          papers: scope && scope !== 'all' ? [scope] : undefined
        };

        runBtn.disabled = true;
        try {
          const res = await api('/api/jobs', {
            project: projectId,
            kind: activeKind,
            prompt: q,
            source: 'local',
            options: options
          });
          toast('问答任务已启动并在后台执行');
          await load(false);
          activeJob = await api('/api/job?id=' + res.id);
          updateUI();
        } finally {
          runBtn.disabled = false;
        }
      });
    }

    // Citation chips click to focus evidence card
    document.querySelectorAll('.citation-chip').forEach(chip => {
      chip.onclick = () => {
        const cite = chip.dataset.cite.toLowerCase();
        // Look for matching evidence card
        const cards = document.querySelectorAll('.evidence-card');
        let matched = null;
        cards.forEach(c => {
          if (c.textContent.toLowerCase().includes(cite.slice(0, 8))) {
            matched = c;
          }
        });
        if (matched) {
          matched.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          cards.forEach(c => c.classList.remove('active'));
          matched.classList.add('active');
        }
      };
    });

    // Delete job from sidebar
    document.querySelectorAll('[data-del-job]').forEach(btn => {
      btn.onclick = run(async (e) => {
        e.stopPropagation();
        const id = btn.dataset.delJob;
        if (!confirm('确定要删除这条任务记录及其结果吗？')) return;
        await api('/api/job/remove', { project: projectId, id: id });
        if (activeJob?.id === id) activeJob = null;
        toast('任务记录已删除');
        await load(false);
        updateUI();
      });
    });

    // Delete active job button
    const delActive = $('#deleteActiveJobBtn');
    if (delActive && activeJob) {
      delActive.onclick = run(async () => {
        if (!confirm('确定要删除这条任务记录及其结果吗？')) return;
        await api('/api/job/remove', { project: projectId, id: activeJob.id });
        activeJob = null;
        toast('任务记录已删除');
        await load(false);
        updateUI();
      });
    }

    // Cancel job
    const cancelBtn = $('#cancelJobBtn');
    if (cancelBtn && activeJob) {
      cancelBtn.onclick = run(async () => {
        await api('/api/cancel', { id: activeJob.id });
        activeJob = await api('/api/job?id=' + activeJob.id);
        updateUI();
      });
    }

    // Refresh job
    const refreshBtn = $('#refreshJobBtn');
    if (refreshBtn && activeJob) {
      refreshBtn.onclick = run(async () => {
        activeJob = await api('/api/job?id=' + activeJob.id);
        updateUI();
      });
    }

    // Save as note
    const toNote = $('#saveAsNoteBtn');
    if (toNote && activeJob?.result) {
      toNote.onclick = run(async () => {
        await api('/api/notes', {
          project: projectId,
          title: activeJob.prompt || '问答结果',
          body: activeJob.result.answer
        });
        toast('已成功保存为项目笔记！');
      });
    }

    // DeepTutor handoff
    const handoff = $('#handoffDtBtn');
    if (handoff && activeJob?.result) {
      handoff.onclick = run(async () => {
        const text = `请帮助我精读并理解以下研究材料。以下内容是工具生成结果，请核对原论文，不要视为已验证事实。\n研究问题：${activeJob.prompt}\n\n${activeJob.result.answer}\n\n文献文件夹：${project.folder}`;
        await copy(text);
        native('deeptutor');
        toast('已复制交接内容，在 DeepTutor 中粘贴；论文需导入其知识库。');
      });
    }

    // Push to DeepTutor Document (Co-Writer)
    const pushDt = $('#pushToDtDocBtn');
    if (pushDt && activeJob?.result) {
      pushDt.onclick = run(async () => {
        const curResult = activeJob.result;
        const title = '学术综述草稿: ' + (activeJob.prompt || '论文问答').slice(0, 35);
        const mdContent = `# ${activeJob.prompt}\n\n## 综述与实证结论\n${curResult.answer}\n\n## 关键文献证据卡片\n${(curResult.contexts || []).map((c, i) => `### 证据 ${i + 1}: ${c.citation || c.name} (评分: ${c.score}/10)\n> **论据摘要**: ${c.summary}\n> **原文片段**: ${c.text}\n`).join('\n')}\n\n## 参考文献\n${curResult.references || ''}`;
        try {
          await api('/api/deeptutor/push-writing', {
            target: 'document',
            title: title,
            content: mdContent
          });
          toast('✅ 已在 DeepTutor 协同写作台创建草稿！正在跳转...');
          native('deeptutor');
        } catch (e) {
          toast('推送到 DeepTutor 失败: ' + e.message);
        }
      });
    }

    // Evidence card actions
    document.querySelectorAll('.btn-save-card').forEach(b => {
      b.onclick = run(async (e) => {
        e.stopPropagation();
        const ctxId = b.dataset.ctxId;
        const ctx = activeJob?.result?.contexts?.find(x => x.id === ctxId);
        if (!ctx) return;
        await api('/api/evidence-cards/save', {
          project: projectId,
          card: {
            job_id: activeJob.id,
            citation: ctx.citation || ctx.name,
            title: ctx.title || ctx.name,
            summary: ctx.summary,
            quote: ctx.text,
            score: ctx.score,
            pinned: 1
          }
        });
        b.textContent = '⭐ 已收藏';
        toast('已收藏到课题精选证据库');
      });
    });

    document.querySelectorAll('.btn-cite-dt').forEach(b => {
      b.onclick = (e) => {
        e.stopPropagation();
        const ctxId = b.dataset.ctxId;
        const ctx = activeJob?.result?.contexts?.find(x => x.id === ctxId);
        if (!ctx) return;
        const clip = `> [证据: ${ctx.citation || ctx.name} | 评分: ${ctx.score}]\n> 论点: ${ctx.summary}\n> 原文: ${ctx.text}\n\n`;
        copy(clip);
        toast('已复制证据引用块到剪贴板');
      };
    });
  }

  function updateUI() {
    saveDraft();
    $('#content').innerHTML = render();
    bind();
  }

  async function refreshModelBadge() {
    try {
      const latest = await api('/api/models');
      modelsCatalog = latest;
      const badge = $('#askModelBadge');
      if (badge && latest) {
        badge.innerHTML = latest.mode === 'deeptutor'
          ? `🔗 DeepTutor 同步 (${esc((latest.deeptutor_sync?.info?.llm_provider || 'DeepTutor').toUpperCase())} · ${esc(latest.deeptutor_sync?.info?.llm_model || 'active')})`
          : `⚙️ 自定义 (${esc(latest.services?.llm?.model || '已分配')})`;
      }
    } catch {}
  }

  async function pollRunningJob() {
    if (page !== 'ask') return;
    // Also check if a job started externally (e.g. from MCP or Companion Drawer)
    if (!activeJob && project?.jobs) {
      const running = project.jobs.find(j => ['ask', 'evidence', 'index'].includes(j.kind) && ['queued', 'waiting', 'running'].includes(j.status));
      if (running) {
        try {
          activeJob = await api('/api/job?id=' + running.id);
          updateUI();
        } catch {}
        return;
      }
    }
    if (!activeJob) return;
    const wasRunning = ['queued', 'waiting', 'running'].includes(activeJob.status);
    if (!wasRunning) return;

    try {
      const updated = await api('/api/job?id=' + activeJob.id);
      activeJob = updated;
      const nowRunning = ['queued', 'waiting', 'running'].includes(updated.status);
      if (nowRunning) {
        // Update status badge, stage message, elapsed time, and live log in-place without losing focus!
        const badge = $('#activeJobStatusBadge');
        if (badge) {
          badge.className = `badge ${updated.status}`;
          badge.textContent = statuses[updated.status] || updated.status;
        }
        const sideBadge = document.querySelector(`[data-job-id="${updated.id}"] .badge`);
        if (sideBadge) {
          sideBadge.className = `badge ${updated.status}`;
          sideBadge.textContent = statuses[updated.status] || updated.status;
        }
        const stageEl = $('#activeJobStageMsg');
        if (stageEl) {
          stageEl.textContent = updated.message || '正在执行 PaperQA2 检索与推理计算...';
        }
        const elapsedEl = $('#activeJobElapsed');
        if (elapsedEl) {
          const secs = Math.max(1, Math.round(Date.now() / 1000 - (updated.created || Date.now() / 1000)));
          elapsedEl.textContent = `已耗时 ${secs} 秒 · 实时更新中`;
        }
        const logEl = $('#activeJobLiveLog');
        if (logEl) {
          const newLog = updated.log || '正在启动 Python 引擎子进程...';
          if (logEl.textContent !== newLog) {
            logEl.textContent = newLog;
            logEl.scrollTop = logEl.scrollHeight;
          }
        }
      } else {
        // Job just finished (done / failed / cancelled)! Reload project state and index status, then render full result
        await load(false);
        await loadData();
        updateUI();
      }
    } catch {}
  }

  return {
    render: async () => {
      await loadData();
      return render();
    },
    bind: bind,
    pollRunningJob: pollRunningJob,
    refreshModelBadge: refreshModelBadge
  };
})();
