---
name: evidence-based-writing
description: |
  学术实证写作与文献证据链闭环工作流。
  在需要基于真实学术论文证据回答前沿研究问题、撰写开题报告、毕业设计论证、文献综述、实验设计对比、或需要严密可溯源引用（默认 GB/T 7714 格式与 Zotero 笔记同步）时激活。
  完整流程：检索复用已有证据卡 -> Zotero 本地检索与研究范围 (Scope) 绑定 -> 覆盖度检查 -> 外部文献检索与入库确认 -> PaperQA2 证据卡抽取与防短路核验 -> GB/T 7714 规范引用与 Zotero 子笔记自动写回 -> 双格式成果交付 (Markdown Artifact + Word/DOCX 规范排版导出)。
license: MIT
metadata:
  version: v3.2
  author: Antigravity Academic Pair
---

# 学术实证与可溯源写作工作流 (Evidence-Based Academic Writing for Antigravity)

当用户要求撰写学术开题报告、文献综述、研究方法设计、技术路线论证或回答需要文献实证支撑的学术问题时，**必须遵循本工作流**。

> [!IMPORTANT]
> **学术严谨性红线**：严禁凭记忆臆造文献、虚构作者/年份/DOI/页码或编造未经验证的实验数据。所有正文引用的事实结论与核心数据，必须来自 Zotero 本地文献库与 PaperQA2 抽取的**可溯源证据卡 (Evidence Cards v2)**，或明确标注为拟采用的理论设计。

---

## 核心工具集接入途径

在 Antigravity 中，你可以通过以下两种等价途径调用实证文献能力：
1. **MCP 工具途径**（MCP Servers: `zotero`, `paper-search`, `paperqa2-zotero`）：
   - `paperqa2-zotero`: `cards_search`, `scope_create`, `scope_status`, `coverage_check`, `gather_evidence`, `ask_literature`, `verify_claim`, `literature_acquire`, `cards_cite`, `cards_writeback_zotero`
   - `zotero`: `zotero_search_items`, `zotero_get_collections`, `zotero_get_item_metadata`, `zotero_get_full_text`, `zotero_add_note`
   - `paper-search`: `search_arxiv`, `search_semantic`, `search_openalex`, `search_crossref`
2. **本地 API / 脚本途径**（运行环境端口 `http://127.0.0.1:8765`，直接调用 `searchtools.core.Store` 与 `searchtools.zotero.Library`）：
   - 当需要批量处理或在脚本中高精度自动化时，可直接通过 `python3 -m searchtools...` 或 HTTP 请求与后台服务无缝通信。

---

## 阶段执行指南

### Step 1: 拆解论题并优先检索复用已有证据卡
1. 将学术写作任务拆解为 **2～4 个核心检索子问题 (Claims / Sub-questions)**。
2. 优先调用 `cards_search(query="关键词")` 检索本地历史积累的高分证据卡（评分 ≥ 7/10）。
   - **零成本优化**：若已检索到相关度极高的历史证据卡（如已标注 PDF 页码与引文），直接记录卡片 `id` 复用，避免重复重新解析全文 PDF 消耗时间。

### Step 2: 检索 Zotero 本地文献库并锁定研究范围 (Scope)
1. 调用 `zotero_search_items(query="关键词", limit=20)` 或查看分类 `zotero_get_collections()`。
2. 调用 `scope_create` 建立或绑定该课题的研究范围（如 `name="SRL_Quadruped_Proposal_Scope"`，绑定核心 `item_keys`）。
3. 检查范围索引状态 `scope_status(scope_id=...)`，确保相关 PDF 已完成向量索引。

### Step 3: 文献充足度核验与定向补充入库 (Coverage Check & Acquire)
1. 调用 `coverage_check(question="核心研究问题", scope_id=scope_id)` 评估当前文献是否充分覆盖所有子论题。
2. 若存在明显文献缺口（如缺少硬件映射、能耗评估或复杂避障），调用 `search_arxiv` / `search_semantic` 检索外部高质量预印本与期刊论文。
3. 将候选论文调用 `literature_acquire` 暂存入库（单次上限 20 篇，需用户在工作台确认或经用户指令同意）。

### Step 4: PaperQA2 深度证据抽取 (Gather Evidence)
1. 若已有证据卡未能完全覆盖特定实证细节（如门级数值能耗模型、动力学微分方程参数），调用 `gather_evidence(question="具体技术问题", scope_id=scope_id, k=8, min_score=3)`。
2. 等待解析完成，提取包含 `quote`（原文摘录）、`page`（PDF 真实页码）、`summary`（提炼结论）的高置信度实证卡片。
3. **跨语言与新增文献避坑机制 (Critical Guard)**：
   - 当研究范围（Scope）中包含跨语言文献（如既有中文又有英文论文）或近期新补充入库了文献时，需高度警惕分词假缓存命中。若发现某些关键文献未被覆盖，**必须显式传入 `force_refresh=True`** 绕过缓存短路，强制后台 Worker 完整运行 PaperQA2 提取。
   - **原文精准溯源核验**：每张提取的有效卡片必须正确映射到具体的 `zotero_item` 与 `attachment_key`，并携带真实起始页码，生成形如 `zotero://open-pdf/library/items/{attachment_key}?page={page}` 的深层定位链接，杜绝 `anonndunknownautho` 无归属卡片。

### Step 5: 规范引用生成与 Zotero 笔记自动写回 (Cite & Writeback)
1. 在输出学术正文前，**必须调用 `cards_cite`**：
   ```json
   {
     "card_ids": ["ev_...", "ec-..."],
     "style": "gbt7714",
     "doc_ref": "antigravity:thesis_proposal",
     "anchor": "课题论文名称",
     "auto_writeback_zotero": true
   }
   ```
2. 系统将返回符合 **GB/T 7714-2015 顺序编码制** 的文末参考文献列表，并自动将所有采用的证据卡作为格式化富文本子笔记写回对应的 Zotero 文献条目中。

### Step 6: 撰写高质量学术成果与 Artifact 呈现
1. 正文引用采用规范角标 `[1]`, `[2]` 或 `[1, p.2-5]`。
2. 充分发挥 Antigravity 的长上下文与数学渲染优势：
   - 使用 KaTeX 公式（行内 `$...$`, 独占行 `$$...$$`）严谨呈现动力学方程、状态转移、奖励函数与能耗公式；
   - 使用 Markdown 表格详尽列出状态/动作向量定义表、超参数配置表、4 组对照实验设计表；
   - 在文末附录包含完整的 **PaperQA2 实证卡片与 Zotero 原文溯源核验表**（包含真实页码及 `zotero://open-pdf/` 深层跳转链接）；
3. **输出为 Artifact**：将完整的毕业论文开题报告、综述或正文直接保存为 Artifact 文档，方便用户在右侧面板随时预览、对照与答辩使用。

### Step 7: 双格式成果输出 (Dual-Format Delivery: Markdown + Word/DOCX)
学术报告或论文必须**同时提供 Markdown 交互版与 Word 规范排版版**，严禁仅输出单一格式：
1. **Markdown (.md)**：作为交互式 Artifact 保存在 `<appDataDir>/brain/<conversation-id>/`，并同步镜像至项目 `docs/` 目录；
2. **Word (.docx)**：调用转换工具 `scripts/md_to_docx.py` 生成标准学术版 Word 文档：
   ```bash
   /home/qyw/DeepTutor/.venv/bin/python /home/qyw/文档/ChatGPT/科研学习相关工具搭建研究/scripts/md_to_docx.py \
     <path_to_markdown.md> <path_to_output.docx>
   ```
   - **学术排版规范要求**：
     - 中文字体统一采用 **宋体 (SimSun)**，西文与数字采用 **Times New Roman**，正文字号 **小四 (12pt)**，行间距 **1.25倍**，中文正文段落首行缩进 2 字符；
     - 各级标题采用 **黑体 (SimHei)**（一级标题三号/16pt、二级标题四号/14pt、三级标题小四/12pt，加粗并开启孤行控制）；
     - 表格自动应用学术标准 **三线表 (Three-Line Table)**：顶线与底线加粗 (1.5pt)，表头线细实线 (0.75pt)，无坚线，表头居中加粗并设浅灰背景；
     - 数学公式自动解析 LaTeX 语法并映射为高质量 Unicode 数学表达；
     - 生成后将 `.docx` 文档同步保存至 Artifact 目录及工作区 `docs/` 目录，并在最终回复中向用户提供可直接下载打开的完整路径。
