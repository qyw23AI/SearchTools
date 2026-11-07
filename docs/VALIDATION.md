# 本机部署与验证记录

## 已实际完成

- 复用已有 DeepTutor / GPT Researcher / PaperQA2 与两种 Qwen 模型，未重新安装上游依赖。
- 安装 SearchTools 用户服务和桌面入口，并追加到 GNOME 收藏夹。
- 重复执行安装器，确认保留配置、项目和已保存笔记。
- 桌面 WebKit 实际加载工作区、DeepTutor `/chat`、GPT Researcher 首页，读取到三个页面的标题及正文。
- 浏览器中验证项目创建、工作台显示、笔记保存及页面布局。
- 页面默认字号调整，新增缩放按钮与快捷键；浏览器中验证放大至 120% 后刷新仍保留比例。桌面三个标签页独立使用 WebKit 页面缩放。
- HTTP 与引擎导入诊断通过，Ollama 返回 `qwen3:8b` 与 `qwen3-embedding:4b`。
- 对两个模型的全部 manifest/config/layer 文件重新逐项验证大小与 SHA-256，结果通过。
- 七项自动测试通过：内容去重与项目隔离、任务输入快照、空资料范围拒绝与取消、GPU 忙碌时不启动引擎、接口来源/令牌校验、路径与内容处理、真实独立子进程的结果保存和取消。
- Python 编译检查、前端 JavaScript 语法检查通过。

## 验证限制

本机早期有 GPU 训练占用约 11 GB 显存，因此当时未执行 8B 生成、真实 PDF 索引与完整调研报告。2026-11-05 已补做真实 PDF 索引、8B 与 14B 的 PaperQA2 问答；自动测试中的引擎子进程仍使用确定性测试程序，不等同于真实研究质量测试。先前专用 4B 嵌入接口已有成功验证记录。

## PaperQA2 本地兼容修复（2026-11-05）

- `paper-qa 2026.8.12` 运行时报 `LiteLLMModel has no attribute get_router`：环境中的 `fhlmi 1.0.7` 已移除该方法；固定 `fhlmi==0.45.0`（并固定已知可用的 `litellm==1.84.1`）后恢复。
- 含图片 PDF 索引时报 `No module named 'PIL'`：在 PaperQA2 环境安装 `pillow`。
- 索引时默认使用 `parsing.enrichment_llm = gpt-4o-2024-11-20`，本地没有 OpenAI key 会失败：改为 Ollama，并设置 `parsing.multimodal=false` 暂时关闭图像增强。
- Qwen3 默认开启 thinking，ToolSelector 返回 `{}` 而不是工具调用：在 `litellm_params` 中设置 `think: false` 后恢复。
- 重建项目索引后复跑“脉冲强化学习的算法种类”成功：1 篇论文、5 条证据、有引用回答。
- 已下载 `qwen2.5:14b-instruct`（Q4_K_M，9.0 GB），Ollama 以 8192 上下文加载；14B 占 10.43 GB VRAM，加 4B 嵌入模型共约 14.2 GB，16 GB 显存可运行。
- 14B + `ToolSelector` 对整句中文问题检索 0 篇，并出现 `gen_answer` 参数错误；切换 `agent_type=fake` 后，同一问题检索 1 篇、5 条证据、回答成功。fake Agent 会自动生成带空格的中文关键词/英文关键词，更适合本地中文检索。
- 安装器与 `config/paperqa-ollama.json` 已同步加入 `fhlmi==0.45.0`、`litellm==1.84.1`、`pillow` 以及 `parsing`、`think`、`num_ctx`、`agent_type=fake` 配置，避免新环境重复踩坑。

全新空白 Ubuntu 的大体积依赖下载与完整安装未在第二台设备执行。安装器已提供固定主版本、隔离环境、官方下载、续传与校验、默认配置和服务注册；本机实际验证的是复用安装和重复部署路径。

上游原生页面主要界面已加载验证，所有插件、特殊媒体权限、弹窗与高级工作流未逐一测试。

## 跨语言实证文献链与 Zotero 原文精准定位验证（2026-11-07）

- **缺陷追踪**: 详见 [docs/ISSUES.md](file:///home/qyw/文档/ChatGPT/科研学习相关工具搭建研究/docs/ISSUES.md) `[Issue #001]`。
- **并发任务保护**: 移除了 `Store.__init__` 中将 running 任务误置为 failed 的逻辑，保证并发 Worker 与状态轮询安全。
- **多语言检索防短路**: 在 `list_evidence_cards` 中引入停用词过滤与严格 `AND` 匹配，阻断早期缓存假命中。
- **元数据模糊 Slug 匹配**: 修复 PaperQA2 文献引用 Slug 与 Zotero 标准条目映射，提取到 6 篇英文论文共 22 张高分证据卡。
- **Zotero 原文页码跳转**:
  - `desktop.py` 策略决策保留 `?page=...` 参数，并向所有视图开放；
  - `server.py` 新增 `POST /api/open-zotero` 接口，支持系统级唤起；
  - `~/.local/share/applications/zotero.desktop` 注册 `x-scheme-handler/zotero;`；
  - 证据工作台 `workbench.js` 改用双重保障按钮唤起。
- **双格式产出**: 新增 `scripts/md_to_docx.py`，支持将学术 Markdown 自动转换为符合中国学术规范的 Word 文档（宋体/Times New Roman、三线表、KaTeX 符号映射）。开题报告已完成 `.md` 与 `.docx` 同步输出验证。
