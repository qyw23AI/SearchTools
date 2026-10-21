# 架构与实现边界

```text
GTK 桌面窗口
├─ 持久 WebView：本地工作区（项目、PaperQA2、任务、结果）
├─ 持久 WebView：DeepTutor 原生网页
└─ 持久 WebView：GPT Researcher 原生网页
             │
127.0.0.1:8765 标准库 HTTP 服务
├─ SQLite 项目 / 文件身份 / 任务 / 笔记
├─ 文献快照与结果文件
└─ 串行执行队列 → GPU 占用检查
   ├─ GPT 独立 Python 子进程 → GPTResearcher
   └─ PaperQA 独立 Python 子进程 → build_index / ask
```

## 已实现

- 保留原生页面及切换状态，不覆盖上游导航、主题或模型设置。
- 本地项目、导入和按内容去重、笔记、任务进度、取消/重试、日志、Markdown 导出。
- PaperQA2 本项目索引与带引用回答，保留 AnswerResponse 完整 JSON。
- GPT Researcher 在线 / 本项目报告，保留研究来源列表。
- 结果交接到 PaperQA2 的可编辑问题；复制给 DeepTutor，并提示自行导入文献。
- 创建任务时冻结输入文件，记录哈希与原文件身份；不同项目使用独立索引。
- 本地 HTTP 来源和 Host 检查，写接口需页面随机令牌；内容按文本展示，拒绝跨站写入。
- 官方模型 blobs 的续传、大小和 SHA-256 校验；可重复执行的安装与更新。

## 尚未承诺的能力

- DeepTutor 知识库自动同步：该版本 connect-folder API 是挂载已经存在的 RAG 索引，不能直接挂载原始 PDF。当前提供文件夹交接和原生导入，避免冒用错误接口。
- 不共享不同引擎的向量索引；也不自动切换原生页面的项目范围。
- 尚无完全自动的多轮三工具协同、全文自动获取、页内证据高亮和人工核验流程。
- GPU 队列管理只约束工作区发起的任务，无法拦截用户在原生工具启动的调用，也不会抢占其他程序。
- GTK WebKit 与 Chromium 的功能存在差异；主要页面已检查，所有上游插件、视频/麦克风、特殊弹窗或浏览器扩展并未逐项验证。
- 前端当前将研究回答作为安全文本显示，保留 Markdown 原文供导出；复杂表格/公式由原生工具或导出后的阅读器处理。

## 任务状态与恢复

`queued → waiting（GPU 忙碌）→ running → done / failed / cancelled`。

每次只执行一个工作区引擎子进程。取消只向该任务独立进程组发信号。工作区服务重启会终止自己的子进程，将旧 running 任务标记失败；排队任务保留。独立的训练、Ollama 服务和上游原生任务不在此进程组。

## 配置和版本

主版本：DeepTutor 1.6.15、GPT Researcher 源码提交 `3582a6841fe7dc801e2a34e34ee408641aea9bce`（0.16.0）、PaperQA2 2026.8.12、Node 22.23.3、Ollama 0.40.5、uv 0.9.5。GPT 源码 tarball、Node 下载有 SHA-256 校验；Ollama 发行归档经 zstd CRC 校验，模型逐 blob SHA-256 校验。

环境位置写入用户配置，应用不依赖固定用户名。部署时自动生成服务文件与桌面快捷方式，支持仓库路径包含空格和中文。三工具的 Python 库均在隔离子进程中调用，不导入到桌面进程或工作区 HTTP 服务。

本地接口使用 `/api/projects`、`/api/project`、`/api/upload`、`/api/jobs`、`/api/job`、`/api/cancel`、`/api/notes`、`/api/file`、`/api/export`。它们仅服务本地界面，不是面向公网的用户鉴权 API。
