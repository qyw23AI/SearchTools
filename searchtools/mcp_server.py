"""PaperQA2-Zotero v3 MCP Server for DeepTutor.

Implements the 12-tool traceable academic evidence & literature acquisition workflow:
1. scope_create: Create/update a research scope from Zotero item_keys, collection, or query.
2. scope_status: Inspect index status, PDF availability, and active jobs for a scope.
3. coverage_check: Fast vector/lexical coverage check returning sufficiency, missing_aspects, and suggested_queries.
4. gather_evidence: Extract high-relevance Evidence Cards v2 with Zotero item_key, citekey, and PDF page deep links.
5. ask_literature: Full academic synthesis with inline card references and bibliography.
6. verify_claim: Fact-check a specific draft statement against the scope's literature.
7. job_status: Poll or wait on a running PaperQA2 job without MCP timeout.
8. literature_acquire: Stage external candidates (max 20, default user confirmation in Workbench UI) or confirm import into Zotero.
9. scope_add_items: Add newly acquired Zotero item_keys into the scope and trigger incremental indexing.
10. cards_search: Search historical Evidence Cards v2 across scopes for zero-cost reuse.
11. cards_cite: Format used Evidence Cards into GB/T 7714 (default), Pandoc, APA, or BibTeX, and auto-writeback used cards to Zotero child notes.
12. cards_writeback_zotero: Explicitly write Evidence Cards back to Zotero item child notes with PDF page deep links.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from searchtools.core import Store, config
from searchtools.models import sync_model_from_deeptutor
from searchtools.acquire import resolve_identifier, MAX_ACQUIRE_BATCH


def _cfg():
    return config()


def _store() -> Store:
    c = _cfg()
    return Store(c.get('data') or Path.home() / 'ResearchWorkspace')


def _api(path: str, method: str = 'GET', payload: dict | None = None, timeout: float = 15.0) -> dict:
    port = _cfg().get('port', 8765)
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-SearchTools-Token': 'local-mcp',
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8', errors='replace')
        return json.loads(raw) if raw.strip() else {}


def _resolve_scope_id(store: Store, scope_id: str = '') -> tuple[str, dict]:
    projects = store.rows('SELECT * FROM projects ORDER BY created DESC')
    if scope_id:
        for p in projects:
            if p['id'] == scope_id.strip() or p['name'].casefold() == scope_id.strip().casefold():
                return p['id'], p
    if projects:
        return projects[0]['id'], projects[0]
    pid = store.create_project('学术实证研究')
    return pid, store.project(pid)


def _enqueue_and_wait(scope_id: str, kind: str, prompt: str, options: dict | None = None, wait_seconds: int = 85) -> dict:
    """Enqueue a task on the SearchTools :8765 worker queue and wait up to wait_seconds.
    Never exceeds DeepTutor's MCP tool_timeout; returns job_id + live progress if still running."""
    opts = dict(options or {})
    opts['use_deeptutor_model'] = True
    try:
        sync_model_from_deeptutor()
    except Exception:
        pass

    store = _store()
    try:
        res = _api('/api/jobs', method='POST', payload={
            'project': scope_id,
            'kind': kind,
            'prompt': prompt,
            'source': 'local',
            'options': opts,
        })
        jid = res['id']
    except Exception:
        jid = store.enqueue(scope_id, kind, prompt, 'local', options=opts)

    store.log_trace(scope_id, f"paperqa_{kind}", 'running', f"启动任务 [{jid[:8]}]: {prompt[:60]}", {'job_id': jid, 'kind': kind})
    return _wait_for_job(jid, wait_seconds=wait_seconds)


def _wait_for_job(job_id: str, wait_seconds: int = 85) -> dict:
    store = _store()
    wait_seconds = max(5, min(int(wait_seconds or 85), 105))
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        rows = store.rows('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not rows:
            raise ValueError(f'任务不存在: {job_id}')
        job = rows[0]
        st = job['status']
        if st == 'done':
            folder = store.folder(job['project']) / 'jobs' / job_id
            res_file = folder / 'result.json'
            result = json.loads(res_file.read_text(encoding='utf-8')) if res_file.exists() else {}
            cards = store.list_evidence_cards(job['project'])
            job_cards = [c for c in cards if c.get('job_id') == job_id]
            if not job_cards and result.get('contexts'):
                job_cards = store.extract_and_save_evidence_from_result(
                    job['project'], job_id, result,
                    claim=job.get('prompt') if job.get('kind') == 'verify' else None
                )
            store.log_trace(job['project'], f"job_{job['kind']}", 'done',
                            f"任务完成 [{job_id[:8]}]: 提取 {len(job_cards)} 张证据卡", {'job_id': job_id, 'cards_count': len(job_cards)})
            return {'completed': True, 'job': job, 'result': result, 'cards': job_cards}
        if st in ('failed', 'cancelled'):
            store.log_trace(job['project'], f"job_{job['kind']}", st, f"任务失败/取消 [{job_id[:8]}]: {job.get('message')}", {'job_id': job_id})
            return {'completed': True, 'job': job, 'error': job.get('message') or '任务失败'}
        time.sleep(1.0)

    rows = store.rows('SELECT * FROM jobs WHERE id=?', (job_id,))
    job = rows[0] if rows else {'id': job_id, 'status': 'running', 'message': '正在执行中'}
    return {'completed': False, 'job': job}


def _format_card_markdown(c: dict, idx: int | None = None) -> str:
    src = c.get('source') or {}
    prefix = f"**[{c['id']}]**" if idx is None else f"**[{idx} · `{c['id']}`]**"
    authors = ', '.join((src.get('authors') or [])[:3]) or '未知作者'
    yr = src.get('year') or 'n.d.'
    pg = f" · p.{src['pages']}" if src.get('pages') else ""
    ck = src.get('citekey') or ''
    zlink = src.get('zotero_link') or ''
    link_md = f" · [在 Zotero 打开原文]({zlink})" if zlink else ""
    claim_line = f"- **支撑论点**: {c.get('claim')}\n" if c.get('claim') else ""
    return (
        f"{prefix} **{src.get('title') or c.get('title')}** ({authors}, {yr}{pg}) "
        f"[citekey: `{ck}` | Zotero: `{src.get('item_key')}` | 评分: {c.get('score')}/10]{link_md}\n"
        f"{claim_line}"
        f"- **论据提炼**: {c.get('summary')}\n"
        f"- **原文论据**: “{str(c.get('quote') or '').strip()}”"
    )


# ==============================================================================
# 12 Core v3 Tools
# ==============================================================================

def scope_create(
    name: str = "",
    item_keys: list[str] | None = None,
    collection: str = "",
    zotero_query: str = "",
    scope_id: str = "",
) -> str:
    """【1. 创建/更新研究范围 (Scope)】根据 Zotero 条目 key 列表 (`item_keys`)、Zotero 分类名称/key (`collection`) 或检索词 (`zotero_query`) 构建研究范围，同步本地 PDF 并绑定持久增量索引。"""
    try:
        store = _store()
        res = store.create_or_update_scope(
            name=name,
            item_keys=item_keys or [],
            collection=collection,
            zotero_query=zotero_query,
            scope_id=scope_id,
        )
        store.log_trace(res['scope_id'], 'scope_create', 'done',
                        f"创建/更新研究范围「{res['name']}」({res['with_pdf_count']} 篇有 PDF, {res['indexed_count']} 篇已建索引)", res)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'创建研究范围失败: {e}'}, ensure_ascii=False)


def scope_status(scope_id: str = "") -> str:
    """【2. 查询研究范围状态】查看指定 `scope_id` 的文献列表、PDF 完备度、向量索引进度与近期任务。"""
    try:
        store = _store()
        sid, _ = _resolve_scope_id(store, scope_id)
        st = store.get_scope_status(sid)
        return json.dumps(st, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'获取研究范围状态失败: {e}'}, ensure_ascii=False)


def coverage_check(question: str, scope_id: str = "", wait_seconds: int = 85) -> str:
    """【3. 文献覆盖度与充足性判定】对研究问题进行快速向量与关键词覆盖度检查（不调大模型生成），返回 `sufficiency` (sufficient / partial / insufficient)、命中篇数、缺口子问题 `missing_aspects` 与建议外部检索词 `suggested_queries`。"""
    try:
        store = _store()
        sid, proj = _resolve_scope_id(store, scope_id)
        papers = store.rows('SELECT id FROM papers WHERE project=?', (sid,))
        if not papers:
            empty_cov = {
                'scope_id': sid,
                'scope_name': proj['name'],
                'sufficiency': 'insufficient',
                'hit_papers_count': 0,
                'total_indexed_papers': 0,
                'max_score': 0.0,
                'missing_aspects': [question],
                'suggested_queries': [question],
                'message': '当前研究范围暂无 PDF 文献，请先调用 scope_create 导入 Zotero 文献或使用 paper-search 检索外部文献。',
            }
            store.log_trace(sid, 'coverage_check', 'done', f"覆盖度: insufficient (0 篇文献)", empty_cov)
            return json.dumps(empty_cov, ensure_ascii=False, indent=2)

        outcome = _enqueue_and_wait(sid, 'coverage', question, options={'preset': 'fast'}, wait_seconds=wait_seconds)
        if not outcome['completed']:
            job = outcome['job']
            return json.dumps({
                'status': 'running',
                'job_id': job['id'],
                'scope_id': sid,
                'progress_message': job.get('message') or '正在构建索引并计算覆盖度...',
                'next_action': f"请调用 job_status(job_id='{job['id']}') 继续等待结果。",
            }, ensure_ascii=False, indent=2)

        if outcome.get('error'):
            return json.dumps({'error': outcome['error'], 'scope_id': sid}, ensure_ascii=False)

        res = outcome['result']
        cov_payload = {
            'scope_id': sid,
            'scope_name': proj['name'],
            'sufficiency': res.get('sufficiency', 'insufficient'),
            'hit_papers_count': res.get('hit_papers_count', 0),
            'high_relevance_papers_count': res.get('high_relevance_papers_count', 0),
            'total_indexed_papers': res.get('total_indexed_papers', 0),
            'max_score': res.get('max_score', 0.0),
            'subquestions': res.get('subquestions', []),
            'missing_aspects': res.get('missing_aspects', []),
            'suggested_queries': res.get('suggested_queries', []),
            'top_chunks': res.get('top_chunks', [])[:5],
        }
        store.log_trace(
            sid,
            'coverage_check',
            'done',
            f"覆盖度判定: {cov_payload['sufficiency']} (高相关 {cov_payload['high_relevance_papers_count']} 篇, 最高相似度 {cov_payload['max_score']})",
            cov_payload
        )
        return json.dumps(cov_payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'覆盖度检查失败: {e}'}, ensure_ascii=False)


def gather_evidence(
    question: str,
    scope_id: str = "",
    k: int = 8,
    min_score: int = 5,
    wait_seconds: int = 85,
    force_refresh: bool = False,
) -> str:
    """【4. 提取可溯源证据卡 (Evidence Cards v2)】使用 DeepTutor 当前模型驱动 PaperQA2，从指定 `scope_id` 中提取带原文引文、页码、citekey 与 Zotero 深度链接的证据卡。"""
    try:
        store = _store()
        sid, proj = _resolve_scope_id(store, scope_id)
        # Fast-path cache check: if not forced and the scope already has >= 3 high-scoring evidence cards matching the question
        # and no new papers are pending indexing, reuse cached cards immediately to save LLM token quota.
        scope_st = store.get_scope_status(sid)
        if not force_refresh:
            cached_cards = store.list_evidence_cards(pid=sid, q=question, min_score=max(min_score, 5))
            if len(cached_cards) >= 3 and scope_st.get('pending', 0) == 0:
                cards = cached_cards[:max(3, min(int(k or 6), 10))]
                store.log_trace(
                    sid,
                    'gather_evidence',
                    'done',
                    f"命中已抽取证据卡缓存: 复用 {len(cards)} 张高分证据卡 (零额外 LLM Token 消耗)",
                    {'scope_id': sid, 'reused_cards': [c['id'] for c in cards]},
                )
                lines = [
                    f"### 🗂️ PaperQA2 证据卡 v2 (研究范围: {proj['name']} · `scope_id={sid}` · 已命中本地证据卡索引)",
                    f"**检索问题**: {question} · **共返回 {len(cards)} 张证据卡**：\n",
                ]
                for idx, c in enumerate(cards, 1):
                    lines.append(_format_card_markdown(c, idx=idx))
                lines.append("\n💡 **写作引用提示**: 在撰写正文后，请将实际采纳的 `card_id` 列表传给 `cards_cite(card_ids=[...])`，系统将自动生成 GB/T 7714 参考文献并将已用证据卡写回 Zotero 子笔记。")
                return "\n\n".join(lines)

        opts = {
            'preset': 'fast',
            'evidence_k': max(3, min(int(k or 6), 10)),
            'evidence_relevance_score_cutoff': max(5, min(int(min_score or 5), 9)),
        }
        outcome = _enqueue_and_wait(sid, 'evidence', question, options=opts, wait_seconds=wait_seconds)
        if not outcome['completed']:
            job = outcome['job']
            return json.dumps({
                'status': 'running',
                'job_id': job['id'],
                'scope_id': sid,
                'progress_message': job.get('message') or '正在提取证据卡...',
                'next_action': f"任务仍在后台执行，请调用 job_status(job_id='{job['id']}') 获取最终证据卡。",
            }, ensure_ascii=False, indent=2)

        if outcome.get('error'):
            return f"❌ PaperQA2 证据提取失败: {outcome['error']}"

        cards = outcome.get('cards') or []
        if not cards:
            return f"在研究范围「{proj['name']}」中未检索到与『{question}』相关度达到 {min_score} 分的证据片段。"

        lines = [
            f"### 🗂️ PaperQA2 证据卡 v2 (研究范围: {proj['name']} · `scope_id={sid}`)",
            f"**检索问题**: {question} · **共提取 {len(cards)} 张证据卡**：\n",
        ]
        for idx, c in enumerate(cards, 1):
            lines.append(_format_card_markdown(c, idx=idx))
        lines.append("\n💡 **写作引用提示**: 在撰写正文后，请将实际采纳的 `card_id` 列表传给 `cards_cite(card_ids=[...])`，系统将自动生成 GB/T 7714 参考文献并将已用证据卡写回 Zotero 子笔记。")
        return "\n\n".join(lines)
    except Exception as e:
        return f"提取证据卡异常: {e}"


def ask_literature(question: str, scope_id: str = "", wait_seconds: int = 85) -> str:
    """【5. 深度文献综合问答】使用 DeepTutor 当前模型驱动 PaperQA2，执行证据检索、相关性打分与学术综合回答，返回带 `[card_id]` 支撑的回答及完整证据卡列表。"""
    try:
        store = _store()
        sid, proj = _resolve_scope_id(store, scope_id)
        opts = {'preset': 'balanced', 'answer_language': 'zh'}
        outcome = _enqueue_and_wait(sid, 'ask', question, options=opts, wait_seconds=wait_seconds)
        if not outcome['completed']:
            job = outcome['job']
            return json.dumps({
                'status': 'running',
                'job_id': job['id'],
                'scope_id': sid,
                'progress_message': job.get('message') or '正在综合研读文献...',
                'next_action': f"任务仍在后台执行，请调用 job_status(job_id='{job['id']}') 获取回答。",
            }, ensure_ascii=False, indent=2)

        if outcome.get('error'):
            return f"❌ PaperQA2 深度问答失败: {outcome['error']}"

        res = outcome['result']
        cards = outcome.get('cards') or []
        lines = [
            f"### 💡 PaperQA2 深度文献问答 (研究范围: {proj['name']} · `scope_id={sid}`)",
            f"**问题**: {question}\n",
            res.get('answer') or '问答完成',
            "\n### 🗂️ 支撑本回答的证据卡 (Evidence Cards v2):",
        ]
        for idx, c in enumerate(cards[:10], 1):
            lines.append(_format_card_markdown(c, idx=idx))
        if res.get('references'):
            lines.append(f"\n### 📚 PaperQA2 原始参考文献:\n{res['references']}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"深度文献问答异常: {e}"


def verify_claim(claim: str, scope_id: str = "", wait_seconds: int = 85) -> str:
    """【6. 学术论断核验】核验论文草稿中的某句具体论断 (`claim`) 是否有文献依据，返回 `supported / contradicted / insufficient` 判定以及支持/反驳的证据卡。"""
    try:
        store = _store()
        sid, proj = _resolve_scope_id(store, scope_id)
        verify_prompt = (
            f"请严格根据文献原文核验以下学术论断是否成立，指出是「支持 (supported)」、「矛盾/反驳 (contradicted)」还是「证据不足 (insufficient)」，并引用原文具体数据或结论：\n"
            f"待核验论断：{claim}"
        )
        opts = {'preset': 'fast', 'evidence_k': 6, 'answer_language': 'zh'}
        outcome = _enqueue_and_wait(sid, 'ask', verify_prompt, options=opts, wait_seconds=wait_seconds)
        if not outcome['completed']:
            job = outcome['job']
            return json.dumps({
                'status': 'running',
                'job_id': job['id'],
                'scope_id': sid,
                'progress_message': job.get('message') or '正在核验论断...',
                'next_action': f"请调用 job_status(job_id='{job['id']}') 获取核验结果。",
            }, ensure_ascii=False, indent=2)

        if outcome.get('error'):
            return json.dumps({'error': outcome['error']}, ensure_ascii=False)

        res = outcome['result']
        cards = outcome.get('cards') or []
        ans_text = str(res.get('answer') or '')
        verdict = 'insufficient'
        if any(w in ans_text.lower() for w in ('contradict', '矛盾', '反驳', '不一致', '相反')):
            verdict = 'contradicted'
        elif cards and max((c.get('score') or 0) for c in cards) >= 6 and not ('cannot answer' in ans_text.lower()):
            verdict = 'supported'

        payload = {
            'claim': claim,
            'scope_id': sid,
            'verdict': verdict,
            'analysis': ans_text,
            'cards': cards[:6],
        }
        store.log_trace(sid, 'verify_claim', 'done', f"论断核验 [{verdict}]: {claim[:50]}", {'verdict': verdict, 'cards_count': len(cards)})
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'论断核验失败: {e}'}, ensure_ascii=False)


def job_status(job_id: str, wait_seconds: int = 60) -> str:
    """【7. 查询/等待后台任务进度】当 `gather_evidence`、`ask_literature`、`coverage_check` 返回 `job_id` 时，调用本工具继续等待并获取最终结果，彻底避免超时。"""
    try:
        outcome = _wait_for_job(job_id.strip(), wait_seconds=wait_seconds)
        if not outcome['completed']:
            job = outcome['job']
            return json.dumps({
                'status': job.get('status', 'running'),
                'job_id': job['id'],
                'progress_message': job.get('message') or '正在执行中...',
                'next_action': f"任务仍在执行 ({job.get('message')})，请再次调用 job_status(job_id='{job['id']}')。",
            }, ensure_ascii=False, indent=2)

        job = outcome['job']
        if outcome.get('error'):
            return json.dumps({'status': job.get('status'), 'job_id': job['id'], 'error': outcome['error']}, ensure_ascii=False)

        res = outcome.get('result') or {}
        cards = outcome.get('cards') or []
        return json.dumps({
            'status': 'done',
            'job_id': job['id'],
            'kind': job.get('kind'),
            'answer': res.get('answer'),
            'sufficiency': res.get('sufficiency'),
            'missing_aspects': res.get('missing_aspects'),
            'suggested_queries': res.get('suggested_queries'),
            'cards_count': len(cards),
            'cards': cards[:12],
            'references': res.get('references', ''),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'查询任务状态失败: {e}'}, ensure_ascii=False)


def literature_acquire(
    candidates: list[dict] | None = None,
    identifiers: list[str] | None = None,
    topic: str = "",
    query: str = "",
    scope_id: str = "",
    require_confirmation: bool = True,
    batch_id_to_confirm: str = "",
) -> str:
    """【8. 外部文献补充与候选暂存 (上限 20 篇，默认需人工确认)】
    - 当通过 `paper-search` 检索到外部候选文献后，传入 `candidates`（每项含 `title`, `authors`, `year`, `doi`, `arxiv_id`, `pdf_url`, `abstract`）或 `identifiers`（DOI / arXiv ID 列表，最多 20 条）。
    - 默认 `require_confirmation=True`：将候选文献（最多 20 篇）推送至右侧「证据工作台 -> 补充文献」面板，自动标记 Zotero 库内去重状态与 OA PDF 可用性，等待用户勾选确认入库。
    - 若用户在对话中明确指令「直接帮我确认入库批次 `batch_id`」或设置 `require_confirmation=False`，则立即下载开放获取 PDF 并入库至 Zotero `AI 补充文献/<课题>` 分类。"""
    try:
        store = _store()
        sid, proj = _resolve_scope_id(store, scope_id)
        topic_name = (topic or proj.get('name') or '默认课题').strip()

        if batch_id_to_confirm:
            res = _api('/api/candidates/confirm', method='POST', payload={
                'batch_id': batch_id_to_confirm.strip(),
                'scope_id': sid,
                'topic': topic_name,
                'fetch_pdf': True,
            }, timeout=90.0)
            return json.dumps(res, ensure_ascii=False, indent=2)

        raw_list: list[dict] = []
        for c in (candidates or []):
            if isinstance(c, dict):
                raw_list.append(c)
            elif isinstance(c, str):
                raw_list.append({'identifier': c})
        for ident in (identifiers or []):
            if ident and str(ident).strip():
                raw_list.append({'identifier': str(ident).strip()})

        if not raw_list:
            return json.dumps({'error': '请提供 candidates 列表或 identifiers (DOI / arXiv ID) 列表。'}, ensure_ascii=False)

        # Enforce hard limit of 20 items per supplementary acquisition batch
        capped_list = raw_list[:MAX_ACQUIRE_BATCH]
        resolved_candidates = [
            resolve_identifier(item.get('identifier') or item.get('doi') or item.get('arxiv_id') or '', hint=item)
            for item in capped_list
        ]

        if require_confirmation:
            staged = store.save_candidate_batch(scope_id=sid, topic=topic_name, query=query, candidates=resolved_candidates)
            store.log_trace(
                sid,
                'literature_acquire',
                'waiting',
                f"已暂存 {staged['total_staged']} 篇候选文献 (批次 `{staged['batch_id']}`，单次上限 20 篇)，等待用户在侧栏勾选确认入库",
                staged
            )
            return json.dumps({
                'status': 'pending_user_confirmation',
                'message': f"已整理并去重 {staged['total_staged']} 篇候选文献（单次上限 20 篇），已推送至右侧「证据工作台 -> 补充文献」面板。请提示用户在侧栏勾选需要的文献并点击「导入 Zotero 并建立索引」，或在对话中确认入库批次 `{staged['batch_id']}`。",
                **staged,
            }, ensure_ascii=False, indent=2)
        else:
            res = _api('/api/candidates/confirm', method='POST', payload={
                'candidates': resolved_candidates,
                'scope_id': sid,
                'topic': topic_name,
                'fetch_pdf': True,
            }, timeout=90.0)
            return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'文献获取与暂存失败: {e}'}, ensure_ascii=False)


def scope_add_items(item_keys: list[str], scope_id: str = "", wait_for_index: bool = True, wait_seconds: int = 85) -> str:
    """【9. 将新入库文献加入研究范围并增量建索引】当补充文献入库 Zotero 后，传入其 `item_keys` 加入当前 `scope_id` 并触发增量向量索引。"""
    try:
        store = _store()
        sid, _ = _resolve_scope_id(store, scope_id)
        res = store.create_or_update_scope(scope_id=sid, item_keys=item_keys)
        if res.get('with_pdf_count', 0) > 0 and res.get('pending_index_count', 0) > 0:
            if wait_for_index:
                outcome = _enqueue_and_wait(sid, 'index', '补充文献增量索引', options={'preset': 'fast'}, wait_seconds=wait_seconds)
                res['index_completed'] = outcome['completed']
                res['index_job_id'] = outcome['job']['id']
            else:
                jid = store.enqueue(sid, 'index', '补充文献增量索引', 'local', options={'use_deeptutor_model': True})
                res['index_job_id'] = jid
        store.log_trace(sid, 'scope_add_items', 'done', f"已将 {len(item_keys)} 篇文献加入研究范围并更新索引", res)
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'加入研究范围失败: {e}'}, ensure_ascii=False)


def cards_search(
    query: str = "",
    scope_id: str = "",
    tag: str = "",
    min_score: float = 0.0,
    pinned_only: bool = False,
) -> str:
    """【10. 检索历史证据卡 (跨对话零成本复用)】在证据库中按关键词、标签、最低评分检索已沉淀的 Evidence Cards v2。"""
    try:
        store = _store()
        sid = ''
        if scope_id:
            sid, _ = _resolve_scope_id(store, scope_id)
        cards = store.list_evidence_cards(
            pid=sid or None,
            q=query or None,
            tag=tag or None,
            min_score=min_score if min_score > 0 else None,
            pinned_only=pinned_only,
        )
        if not cards:
            return "证据库中暂无匹配的证据卡记录。"
        lines = [f"### 🗂️ 检索到 {len(cards)} 张历史证据卡:"]
        for idx, c in enumerate(cards[:20], 1):
            lines.append(_format_card_markdown(c, idx=idx))
        return "\n\n".join(lines)
    except Exception as e:
        return f"检索证据库失败: {e}"


def cards_cite(
    card_ids: list[str],
    style: str = "gbt7714",
    doc_ref: str = "deeptutor:writing",
    anchor: str = "",
    auto_writeback_zotero: bool = True,
) -> str:
    """【11. 生成规范引用与 GB/T 7714 参考文献（并自动将已用证据卡写回 Zotero 子笔记）】
    - 默认 `style="gbt7714"`：生成符合国家标准 GB/T 7714-2015 的文内数字引注 `[1, p.5]` 与文末参考文献列表，同时附带 BibTeX。
    - 默认 `auto_writeback_zotero=True`：凡是大模型在写作中实际调用 `cards_cite` 采纳的证据卡，自动写回对应 Zotero 文献条目的子笔记（含页码深链）并记录 `used_in` 溯源锚点。"""
    try:
        store = _store()
        formatted = store.format_evidence_cards_citation(
            card_ids,
            style=style or 'gbt7714',
            auto_writeback=auto_writeback_zotero,
            doc_ref=doc_ref,
            anchor=anchor,
        )
        store.log_trace(
            '',
            'cards_cite',
            'done',
            f"生成 {style or 'gbt7714'} 引用 ({len(card_ids)} 张证据卡)" + (" 并已自动写回 Zotero 子笔记" if auto_writeback_zotero else ""),
            {'card_ids': card_ids, 'style': style, 'auto_writeback': auto_writeback_zotero}
        )
        return formatted
    except Exception as e:
        return json.dumps({'error': f'生成引用失败: {e}'}, ensure_ascii=False)


def cards_writeback_zotero(card_ids: list[str]) -> str:
    """【12. 显式将证据卡写回 Zotero 子笔记】将指定的 `card_ids` 按所属文献聚合，写入或更新对应 Zotero 条目下的「PaperQA2 实证卡片与引用溯源」子笔记（含一键打开 PDF 指定页码的 `zotero://open-pdf` 链接）。"""
    try:
        store = _store()
        res = store.writeback_cards_to_zotero(card_ids)
        store.log_trace('', 'cards_writeback_zotero', 'done', f"已将 {len(card_ids)} 张证据卡写回 Zotero 子笔记", {'results': res})
        return json.dumps({'ok': True, 'writeback_results': res}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({'error': f'写回 Zotero 失败: {e}'}, ensure_ascii=False)


# ==============================================================================
# Server Entrypoint
# ==============================================================================

ALL_TOOLS = [
    scope_create,
    scope_status,
    coverage_check,
    gather_evidence,
    ask_literature,
    verify_claim,
    job_status,
    literature_acquire,
    scope_add_items,
    cards_search,
    cards_cite,
    cards_writeback_zotero,
]


def zotero_list_collections_and_papers(collection: str = "") -> str:
    """Legacy compatibility helper."""
    return scope_create(collection=collection)


def saved_evidence_cards_query(project_name: str = "", query: str = "", min_score: float = 0.0) -> str:
    """Legacy compatibility helper."""
    res = cards_search(query=query, scope_id=project_name, min_score=min_score)
    return f"【{project_name}】检索结果:\n{res}"


def main():
    try:
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("paperqa2-zotero")
        for fn in ALL_TOOLS:
            mcp.tool()(fn)
        mcp.run()
    except ImportError:
        _run_stdio_fallback()


def _run_stdio_fallback():
    """Standard library JSON-RPC 2.0 stdio fallback."""
    tool_map = {fn.__name__: fn for fn in ALL_TOOLS}
    tools_schema = [
        {
            'name': fn.__name__,
            'description': (fn.__doc__ or '').strip(),
            'inputSchema': {'type': 'object', 'properties': {}}
        }
        for fn in ALL_TOOLS
    ]
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            mid = msg.get('id')
            method = msg.get('method')
            if method == 'initialize':
                resp = {
                    'jsonrpc': '2.0', 'id': mid,
                    'result': {
                        'protocolVersion': '2024-11-05',
                        'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'paperqa2-zotero', 'version': '3.0.0'}
                    }
                }
            elif method == 'tools/list':
                resp = {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': tools_schema}}
            elif method == 'tools/call':
                name = msg.get('params', {}).get('name')
                args = msg.get('params', {}).get('arguments', {}) or {}
                func = tool_map.get(name)
                if func:
                    text_out = func(**args)
                    resp = {'jsonrpc': '2.0', 'id': mid, 'result': {'content': [{'type': 'text', 'text': str(text_out)}]}}
                else:
                    resp = {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'Tool {name} not found'}}
            elif method == 'ping':
                resp = {'jsonrpc': '2.0', 'id': mid, 'result': {}}
            else:
                resp = None
            if resp:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + '\n')
                sys.stdout.flush()
        except Exception as e:
            err_resp = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32000, 'message': str(e)}}
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
