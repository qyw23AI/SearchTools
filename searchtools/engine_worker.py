"""Run inside each upstream tool's own Python environment, never the UI process."""
import asyncio
import json
import logging
import os
os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')
from pathlib import Path
import sys
import time


class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_live_logging():
    handler = FlushStreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(name)s: %(message)s', datefmt='%H:%M:%S'))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [handler]
    for name in ('paperqa', 'paperqa.agents', 'lmi', 'litellm'):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)


def stage(msg):
    print(f'[STAGE] {msg}', flush=True)


async def run_coverage_check(job, settings, emb_model):
    """Fast vector/lexical coverage check without full LLM generation.
    Computes sufficiency ('sufficient', 'partial', 'insufficient'), hit papers, top chunk scores,
    missing aspects, and bilingual suggested queries for external paper-search."""
    import re
    from paperqa.agents.search import get_directory_index
    from paperqa.docs import Docs

    stage(f'1/2 正在加载或增量构建向量索引 ({emb_model})...')
    index = await get_directory_index(settings=settings, build=True)
    index_files_map = await index.index_files
    valid_files = {k: v for k, v in (index_files_map or {}).items() if v != 'ERROR'}
    if not valid_files:
        return {
            'sufficiency': 'insufficient',
            'hit_papers_count': 0,
            'total_indexed_papers': 0,
            'max_score': 0.0,
            'subquestions': [job['prompt']],
            'missing_aspects': [job['prompt']],
            'suggested_queries': [job['prompt']],
            'top_chunks': [],
            'answer': '当前研究范围中没有已建索引的 PDF 文献。',
        }

    opts = job.get('options') or {}
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except Exception:
            opts = {}
    allowed_subset = set(opts.get('subset') or [])

    docs = Docs()
    embedding_model = settings.get_embedding_model()
    loaded_files = 0
    for file_loc, fhash in list(valid_files.items()):
        if allowed_subset and Path(file_loc).name not in allowed_subset and file_loc not in allowed_subset:
            continue
        saved_doc = await index.get_saved_object(file_loc)
        if saved_doc and getattr(saved_doc, 'docs', None) and getattr(saved_doc, 'texts', None):
            this_doc_details = next(iter(saved_doc.docs.values()))
            await docs.aadd_texts(
                texts=saved_doc.texts,
                doc=this_doc_details,
                settings=settings,
                embedding_model=embedding_model,
            )
            loaded_files += 1

    question = str(job['prompt'] or '').strip()
    # Decompose question into subquestions by punctuation / conjunctions
    raw_parts = [p.strip() for p in re.split(r'[？?；;。\n]|(?:以及|并且|同时|与|和|and)', question) if len(p.strip()) >= 4]
    subquestions = raw_parts[:4] if raw_parts else [question]

    stage(f'2/2 正在对 {loaded_files} 篇文献执行快速向量与关键词覆盖度检索...')
    all_texts = list(getattr(docs, 'texts', []) or [])
    if not all_texts:
        return {
            'sufficiency': 'insufficient',
            'hit_papers_count': 0,
            'total_indexed_papers': loaded_files,
            'max_score': 0.0,
            'subquestions': subquestions,
            'missing_aspects': subquestions,
            'suggested_queries': subquestions,
            'top_chunks': [],
            'answer': '未能从索引中加载到有效文献段落。',
        }

    import numpy as np
    q_embs = await embedding_model.embed_documents(subquestions)

    sub_covered = []
    missing_aspects = []
    chunk_best: dict[str, dict] = {}

    for sq, q_vec in zip(subquestions, q_embs):
        q_arr = np.array(q_vec, dtype=float)
        q_norm = np.linalg.norm(q_arr) or 1.0
        sq_tokens = [t.lower() for t in re.findall(r'[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}', sq)]
        best_for_sq = 0.0
        for t_obj in all_texts:
            txt = getattr(t_obj, 'text', '') or ''
            t_emb = getattr(t_obj, 'embedding', None)
            sim = 0.0
            if t_emb is not None:
                t_arr = np.array(t_emb, dtype=float)
                t_norm = np.linalg.norm(t_arr) or 1.0
                sim = float(np.dot(q_arr, t_arr) / (q_norm * t_norm))
            # Lexical boost for exact academic terms
            lex_hits = sum(1 for tok in sq_tokens if tok in txt.lower())
            lex_ratio = (lex_hits / len(sq_tokens)) if sq_tokens else 0.0
            combined = min(1.0, max(sim, sim * 0.8 + lex_ratio * 0.35))
            if combined > best_for_sq:
                best_for_sq = combined
            doc_obj = getattr(t_obj, 'doc', None)
            t_name = getattr(t_obj, 'name', '') or ''
            doc_key = getattr(doc_obj, 'docname', '') or t_name
            prev = chunk_best.get(t_name)
            if not prev or combined > prev['similarity']:
                page_m = re.search(r'pages?\s*(\d+(?:-\d+)?)', t_name, re.I)
                chunk_best[t_name] = {
                    'name': t_name,
                    'docname': doc_key,
                    'title': getattr(doc_obj, 'title', '') or getattr(doc_obj, 'citation', '') or doc_key,
                    'citation': getattr(doc_obj, 'citation', '') or doc_key,
                    'page': page_m.group(1) if page_m else '',
                    'similarity': round(combined, 4),
                    'snippet': txt[:320].strip(),
                }
        if best_for_sq >= 0.62:
            sub_covered.append({'subquestion': sq, 'max_similarity': round(best_for_sq, 3), 'covered': True})
        else:
            sub_covered.append({'subquestion': sq, 'max_similarity': round(best_for_sq, 3), 'covered': False})
            missing_aspects.append(sq)

    ranked_chunks = sorted(chunk_best.values(), key=lambda x: x['similarity'], reverse=True)
    high_papers = {c['title'] for c in ranked_chunks if c['similarity'] >= 0.72}
    med_papers = {c['title'] for c in ranked_chunks if c['similarity'] >= 0.58}
    max_sim = ranked_chunks[0]['similarity'] if ranked_chunks else 0.0

    if len(high_papers) >= 3 and not missing_aspects:
        sufficiency = 'sufficient'
    elif max_sim < 0.58 or len(med_papers) == 0:
        sufficiency = 'insufficient'
    else:
        sufficiency = 'partial'

    # Generate suggested search queries (Chinese + English academic keywords)
    suggested_queries = []
    for aspect in (missing_aspects or subquestions):
        suggested_queries.append(aspect)
        en_terms = re.findall(r'[A-Za-z][A-Za-z0-9\-]{2,}', aspect)
        if en_terms:
            suggested_queries.append(' '.join(en_terms))
    if not any(re.search(r'[A-Za-z]{4,}', q) for q in suggested_queries):
        suggested_queries.append(f"{question} review survey paper")

    return {
        'sufficiency': sufficiency,
        'hit_papers_count': len(high_papers) if high_papers else len(med_papers),
        'high_relevance_papers_count': len(high_papers),
        'total_indexed_papers': loaded_files,
        'max_score': round(max_sim, 3),
        'subquestions': sub_covered,
        'missing_aspects': missing_aspects,
        'suggested_queries': list(dict.fromkeys(suggested_queries))[:6],
        'top_chunks': ranked_chunks[:8],
        'answer': f"文献覆盖度判定: {sufficiency} (高相关文献 {len(high_papers)} 篇 / 已建索引 {loaded_files} 篇, 最高相似度 {round(max_sim, 3)})",
        'successful': True,
    }


async def run_resilient_paperqa(job, settings, llm_model, emb_model):
    """Execute PaperQA2 pipeline with two key resilience enhancements over upstream fake_agent:
    1. All project papers in the index are pre-loaded into state.docs so even if LLM keyword
       search queries miss (e.g. Chinese vs English terms or transient search query failure),
       gather_evidence still evaluates the actual paper chunks rather than failing with 'no papers'.
    2. Upstream API errors (e.g. 503 Service Unavailable / 401 Auth Error) are surfaced clearly
       instead of being swallowed into 'I cannot answer this question due to having no papers.'
    """
    from paperqa.agents.env import PaperQAEnvironment
    from paperqa.agents.helpers import litellm_get_search_query
    from paperqa.agents.search import get_directory_index
    from paperqa.agents.tools import GatherEvidence, GenerateAnswer, PaperSearch
    from paperqa.docs import Docs
    from aviary.core import ToolCall, ToolRequestMessage

    stage(f'2/4 正在检查并构建文献向量索引 ({emb_model})...')
    index = await get_directory_index(settings=settings, build=True)
    index_files_map = await index.index_files
    if not index_files_map:
        raise RuntimeError('当前项目索引中没有任何可用文献，请确认已导入有效 PDF/Markdown 文献。')

    docs = Docs()
    env = PaperQAEnvironment(job['prompt'], settings, docs)
    obs, tools = await env.reset()
    settings.adjust_tools_for_agent_llm(tools)

    question = env.state.session.question
    search_tool = next(filter(lambda x: x.info.name == PaperSearch.TOOL_FN_NAME, tools))
    gather_evidence_tool = next(filter(lambda x: x.info.name == GatherEvidence.TOOL_FN_NAME, tools))
    generate_answer_tool = next(filter(lambda x: x.info.name == GenerateAnswer.TOOL_FN_NAME, tools))

    async def step(tcs):
        msg = tcs if isinstance(tcs, ToolRequestMessage) else ToolRequestMessage(tool_calls=tcs)
        await env.step(msg)

    llm_instance = settings.get_llm()

    # Step A: Try LLM-generated keyword searches against Tantivy index
    stage(f'3/4 正在使用 {llm_model} 生成检索词并匹配项目文献...')
    try:
        queries = await litellm_get_search_query(question, llm=llm_instance, count=3)
        for q_str in queries:
            await step([ToolCall.from_tool(search_tool, query=q_str, min_year=None, max_year=None)])
    except Exception as e:
        err_str = str(e)
        # If the LLM API itself is unreachable / 503 / 401 / 429, fail immediately with a clear message
        if any(code in err_str for code in ('503', 'UNAVAILABLE', 'ServiceUnavailable', 'AuthenticationError', '401', 'RateLimit', '429', 'Insufficient Balance', '402')):
            raise RuntimeError(f'模型服务调用失败 ({llm_model}): {err_str[:280]}') from e
        logging.warning(f'Keyword query generation failed, falling back to full project document load: {e}')

    # Step B: Guarantee all project papers (or user-selected subset) are loaded into env.state.docs!
    # In small/medium local project libraries, Tantivy BM25 keyword search can return 0 hits when
    # query terminology differs from paper language/phrasing. Pre-loading ensures gather_evidence
    # always performs vector similarity search across all project papers.
    opts = job.get('options') or {}
    if isinstance(opts, str):
        try:
            opts = json.loads(opts)
        except Exception:
            opts = {}
    allowed_subset = set(opts.get('subset') or [])

    if not env.state.docs.docs or len(index_files_map) <= 25:
        embedding_model = settings.get_embedding_model()
        for file_loc, fhash in list(index_files_map.items()):
            if fhash == 'ERROR':
                continue
            if allowed_subset and Path(file_loc).name not in allowed_subset and file_loc not in allowed_subset:
                continue
            saved_doc = await index.get_saved_object(file_loc)
            if saved_doc and getattr(saved_doc, 'docs', None) and getattr(saved_doc, 'texts', None):
                this_doc_details = next(iter(saved_doc.docs.values()))
                await env.state.docs.aadd_texts(
                    texts=saved_doc.texts,
                    doc=this_doc_details,
                    settings=settings,
                    embedding_model=embedding_model,
                )

    if not env.state.docs.docs:
        raise RuntimeError('未能从项目索引中加载到有效文献内容，请点击左侧「重新构建索引」后重试。')

    # Step C: Gather evidence chunks via vector similarity + summary_llm scoring
    stage(f'3/4 已加载 {len(env.state.docs.docs)} 篇项目文献，正在检索证据片段并相关性打分 ({llm_model})...')
    try:
        await step([ToolCall.from_tool(gather_evidence_tool, question=question)])
    except Exception as e:
        raise RuntimeError(f'提取文献证据时模型调用失败 ({llm_model}): {str(e)[:280]}') from e

    # Step D: Generate answer (skip if user only requested evidence cards)
    if job['kind'] != 'evidence':
        stage(f'4/4 已提取 {len(env.state.session.contexts)} 条原文证据，正在使用 {llm_model} 综合生成回答...')
        try:
            await step([ToolCall.from_tool(generate_answer_tool)])
        except Exception as e:
            raise RuntimeError(f'生成回答时模型调用失败 ({llm_model}): {str(e)[:280]}') from e
    else:
        stage(f'4/4 已完成 {len(env.state.session.contexts)} 条证据卡片检索！')

    return env.state.session


def main():
    job = json.loads(Path(sys.argv[1]).read_text())

    # Fast model connection test
    if job.get('kind') == 'test':
        import litellm
        litellm.num_retries = 1
        t0 = time.monotonic()
        svc = job.get('service', 'llm')
        params = dict(job.get('params') or {})
        params.setdefault('timeout', 15)
        try:
            if svc == 'embedding':
                params.setdefault('input', ['SearchTools connection test'])
                litellm.embedding(**params)
            else:
                params.setdefault('messages', [{'role': 'user', 'content': 'hi'}])
                params['max_tokens'] = 2
                litellm.completion(**params)
            latency = round((time.monotonic() - t0) * 1000)
            res = {'ok': True, 'latency_ms': latency, 'model': params.get('model')}
        except Exception as e:
            res = {'ok': False, 'error': str(e), 'model': params.get('model')}
        Path(job['output']).write_text(json.dumps(res, ensure_ascii=False, indent=2))
        return

    setup_live_logging()

    if job['kind'] == 'research':
        stage('1/3 正在初始化 GPT Researcher 调研环境...')
        from dotenv import load_dotenv
        load_dotenv(Path.cwd() / '.env')
        os.environ['DOC_PATH'] = job['papers']
        os.environ['REPORT_SOURCE'] = job['source']
        from gpt_researcher import GPTResearcher

        async def research():
            agent = GPTResearcher(query=job['prompt'], report_type='research_report', report_source=job['source'])
            stage('2/3 正在检索与阅读相关学术文献来源...')
            await agent.conduct_research()
            stage('3/3 正在综合整理并撰写调研报告...')
            answer = await agent.write_report()
            return {'answer': answer, 'sources': agent.get_research_sources()}

        result = asyncio.run(research())
    else:
        stage('1/4 正在加载 PaperQA2 引擎与模型配置...')
        from paperqa import Settings
        from paperqa.agents import ask, build_index

        cfg = json.loads(Path(job['settings']).read_text())
        llm_model = cfg.get('llm', 'LLM')
        emb_model = cfg.get('embedding', 'Embedding')
        stage(f'1/4 已加载模型配置 (LLM: {llm_model}, Embedding: {emb_model})')

        index_name = cfg.get('agent', {}).get('index', {}).get('name') or ('project-' + job['project'])
        cfg.setdefault('agent', {}).setdefault('index', {}).update({
            'name': index_name,
            'paper_directory': job['papers'],
            'index_directory': job['index'],
        })

        # Attach metadata manifest if SearchTools generated one from Zotero/user titles
        manifest_csv = Path(job['papers']).parent / 'paperqa_manifest.csv'
        if manifest_csv.exists():
            cfg['agent']['index']['manifest_file'] = str(manifest_csv)

        settings = Settings(**cfg)

        if job['kind'] == 'index':
            stage(f'2/2 正在解析 PDF 文献并构建向量索引 ({emb_model})...')
            build_index(settings=settings)
            stage('索引构建完成！')
            result = {'answer': '项目文献索引已完成。', 'successful': True}
        elif job['kind'] == 'coverage':
            result = asyncio.run(run_coverage_check(job, settings, emb_model))
        else:
            import re
            session = asyncio.run(run_resilient_paperqa(job, settings, llm_model, emb_model))

            contexts = []
            for c in (getattr(session, 'contexts', None) or []):
                doc = getattr(getattr(c, 'text', None), 'doc', None)
                t_name = getattr(getattr(c, 'text', None), 'name', '') or ''
                page_m = re.search(r'pages?\s*(\d+(?:-\d+)?)', t_name, re.I)
                page_val = page_m.group(1) if page_m else ''
                contexts.append({
                    'id': getattr(c, 'id', ''),
                    'summary': getattr(c, 'context', ''),
                    'text': getattr(getattr(c, 'text', None), 'text', '') or getattr(c, 'context', ''),
                    'score': getattr(c, 'score', 0),
                    'name': t_name,
                    'page': page_val,
                    'citation': getattr(doc, 'citation', '') or t_name,
                    'title': getattr(doc, 'title', '') or t_name,
                    'docname': getattr(doc, 'docname', ''),
                })

            formatted = getattr(session, 'formatted_answer', '') or getattr(session, 'answer', '')
            if job['kind'] == 'evidence':
                ans = f"已检索出 {len(contexts)} 条相关证据片段，可在右侧证据面板查看。"
            else:
                ans = formatted

            result = {
                'answer': ans,
                'raw_answer': getattr(session, 'answer', ''),
                'successful': getattr(session, 'has_successful_answer', True),
                'references': getattr(session, 'references', ''),
                'cost': getattr(session, 'cost', 0.0),
                'token_counts': getattr(session, 'token_counts', {}),
                'contexts': contexts,
            }

    result['input_manifest'] = job.get('manifest')
    result['question'] = job.get('prompt')
    Path(job['output']).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print('SearchTools task finished', flush=True)


if __name__ == '__main__':
    main()
