# SearchTools & Evidence-Based Writing 缺陷与问题追踪表 (Issues Tracker)

本文档系统记录在科研实证写作平台、PaperQA2 本地化集成、Zotero 联动及证据工作台研发过程中发现的技术缺陷、根因排查（Root Cause Analysis, RCA）、修复方案与验证闭环。

---

## [Issue #001] 跨语言文献证据卡缺失、后台任务中断、缓存假命中与 Zotero 原文定位失效

- **状态**: ✅ **已解决 (Closed / Resolved)**
- **日期**: 2026-11-07
- **优先级**: 紧急 (Blocker)
- **影响模块**: `searchtools/core.py`, `searchtools/server.py`, `searchtools/desktop.py`, `searchtools/static/workbench.js`, `zotero.desktop`
- **关联功能**: 毕业论文开题报告自动化写作、PaperQA2 证据卡抽取、Zotero 原文页码跳转、桌面 WebKit 导航

---

### 一、 缺陷现象描述 (Symptoms)

1. **英文实证文献被静默跳过**：
   在执行开题报告写作任务时，研究范围（Scope）中包含 10 篇中文论文与 6 篇前沿英文论文（NeurIPS 2025、ICRA/RAL、FPGA SNN 等）。最终生成的开题报告参考文献列表中仅包含中文文献，且附录证据溯源表中完全缺失英文证据卡（未见 `xuProxyTargetBridging2025`, `raffinSmoothExplorationRobotic2020` 等关键成果）。
2. **证据工作台无法定位原文对应页面**：
   在证据工作台（Evidence Workbench）中，点击证据卡片底部的「📖 打开原文 p.X」按钮时，无法正常唤起 Zotero 并跳转至目标 PDF 对应页码：
   - 在独立浏览器或 DeepTutor 嵌入式抽屉（Iframe）中点击无效；
   - 在 Linux 终端通过系统调用执行 `gio open zotero://...` 报错 `gio: 不支持指定位置`；
   - 在 SearchTools WebKit 桌面应用中点击，即使唤起 Zotero 也仅停留在第一页，丢失了页码参数。

---

### 二、 根因深度剖析 (Root-Cause Analysis, RCA)

经过对底层服务、SQLite 数据库及前端代码的端到端单步排查，确认本问题由五个相互耦合的缺陷共同引发：

#### 1. 并发 Worker 任务被误杀 (Premature Job Abortion)
- **定位**: `searchtools/core.py` 中的 `Store.__init__`。
- **根因**: 初始化存储连接时硬编码了 `db.execute("UPDATE jobs SET status='failed', message='应用重启中断了任务，请重试' WHERE status='running'")`。当 MCP 工具或外部脚本并发实例化 `Store` 查询状态或新建任务时，后台正在运行的 PaperQA2 异步子进程被直接判定为中断，导致耗时较长的英文文献向量索引与 LLM 提取任务夭折。

#### 2. 分词宽松 `OR` 匹配导致缓存短路 (False-Positive Cache Hit)
- **定位**: `searchtools/core.py` 的 `list_evidence_cards` 与 `searchtools/mcp_server.py` 的 `gather_evidence`。
- **根因**: `list_evidence_cards` 将用户查询拆解为分词后，无停用词过滤地采用 SQL `' OR '` 连接检索字段。通用词汇（如 `control`, `robot`, `continuous`）轻易命中早期已存在的中文证据卡，使得 `len(cached_cards) >= 3` 快速缓存分支短路返回，彻底阻断了 PaperQA2 针对未处理英文文献的实际抽取调用。

#### 3. PaperQA2 引用 Slug 匹配失效 (Slug Mismatch)
- **定位**: `searchtools/core.py` 中的 `extract_and_save_evidence_from_result`。
- **根因**: PaperQA2 返回的文档片段标识常带有前缀或页码后缀（形如 `unknownauthorsunknownyearproxytargetbridgingpages5-6`）。原有字符串子串比对无法匹配 Zotero 中的标准 `citekey` 或文献标题，导致卡片元数据退化为 `anonndunknownautho`，丢失了 `paper_id`、`zotero_key` 与 `attachment_key`。

#### 4. Desktop WebKit 抛弃 URL Query 参数 (Query Stripping)
- **定位**: `searchtools/desktop.py` 第 161–165 行。
- **根因**: 在拦截 WebKit 导航事件处理 `zotero://` 自定义协议时，代码使用 `f'zotero://{parts[0]}/library/items/{parts[3]}'` 重构了目标 URI，**完全抛弃了 `u.query`（即 `?page=X`）**，且限制仅允许 `workspace` 视图唤起，使得来自 `deeptutor` 嵌入式工作台的请求被直接忽略。

#### 5. Linux 系统桌面协议关联缺失 (Missing Scheme Handler)
- **定位**: `~/.local/share/applications/zotero.desktop`。
- **根因**: 桌面入口文件的 `MimeType` 字段未注册 `x-scheme-handler/zotero;`，且系统未设置默认 URI 处理程序。导致操作系统抛出 `gio: 不支持指定位置` 错误，外部浏览器无法拉起本地 Zotero 客户端。

---

### 三、 修复方案与代码改动 (Fixes & Implementations)

| 涉及模块 | 修改位置 | 核心改进方案 |
| :--- | :--- | :--- |
| `searchtools/core.py` | `Store.__init__` | 移除无条件重置 `running` 任务的语句，避免单例或多实例初始化时误杀正在执行的 Worker。 |
| `searchtools/core.py` | `list_evidence_cards` | 引入中英文停用词表；对 $\le 4$ 个有意义词的检索短语强制采用 `AND` 逻辑，杜绝泛词假阳性缓存命中。 |
| `searchtools/core.py` | `extract_and_save_evidence_from_result` | 增加非字母数字字符剥离与 PaperQA2 固有前后缀清洗，实现强健的模糊 Slug 命中，精准关联 `zotero_item` 与 `attachment_key`。 |
| `searchtools/core.py` | `_enrich_card_v2` & `get_evidence_card` | 优化深层链接计算逻辑：若卡片拥有 `attachment_key` 与 `page`，优先生成高精度 `zotero://open-pdf/library/items/{att}?page={page}` 跳转链接；新增单卡精准查询接口。 |
| `searchtools/server.py` | POST 路由 | 新增 `POST /api/open-zotero` 接口，支持通过后台系统调用 `xdg-open` 稳定拉起 Zotero 并传递定位参数。 |
| `searchtools/desktop.py` | WebKit 策略决策 | 移除单一视图限制，并在重组 `zotero://` URI 时完整保留 `?page=...` 查询参数。 |
| `searchtools/static/workbench.js` | 渲染与事件绑定 | 将原本普通的 `<a>` 标签升级为带有 `data-open-zotero` 的交互按钮，绑定点击事件双重保障（先向后台接口派发唤起请求，再兜底触发前端定位）。 |
| `zotero.desktop` | 桌面与 MIME 数据库 | 在 `MimeType` 中追加 `x-scheme-handler/zotero;`，并执行 `update-desktop-database` 和 `xdg-mime default zotero.desktop x-scheme-handler/zotero`。 |

---

### 四、 验证结果与闭环指标 (Verification)

1. **英文文献实证抽取全覆盖**：
   重新执行抽取后，Scope 中的 6 篇英文论文全部完成深度抽取，共新增 **22 张高置信度英文证据卡**（涵盖 Xu et al. NeurIPS 2025、Raffin et al. gSDE、Gautam et al. NeuroCoreX FPGA、Zhang et al. SpikingNav 等）。
2. **Zotero 原文定位毫秒级响应**：
   - 终端调用测试：`gio open "zotero://open-pdf/library/items/8TEGCNID?page=5"` 成功拉起 Zotero 并精准翻到第 5 页。
   - 服务接口测试：`curl -X POST http://127.0.0.1:8765/api/open-zotero -d '{"card_id":"ev_8dfe928f2b"}'` 返回 `{"ok": true, "uri": "zotero://open-pdf/library/items/QF757IAF?page=5"}` 并即时激活桌面 Zotero 阅读器至对应页。
   - 数据库清洗：执行数据迁移脚本，将历史遗留的 14 张浅层链接或空链接卡片全量修复为带精确页码的 `open-pdf` 深层协议链接。
3. **成果物双格式输出**：
   - Markdown 交互版：`docs/thesis_proposal_snn_quadruped.md` (40 KB)
   - Word 学术规范版：`docs/thesis_proposal_snn_quadruped.docx` (62 KB)
