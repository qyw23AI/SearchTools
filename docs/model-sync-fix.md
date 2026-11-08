# DeepTutor 当前会话模型的事件同步与 GPT‑5.6 接入

日期：2026-11-08。最终实测型号：**gpt-5.6-sol**。

此前“通过旧实时文件、聊天数据库时间戳或 5 秒轮询即可同步当前会话”的结论不成立。本文件记录取代先前延时同步方案。

## 已部署链路

1. DeepTutor 的模型选择器在实际会话选择发生变化、恢复或重新聚焦时发布 `deeptutor:llm-selection` 事件，携带模型/profile ID、会话位置与推理强度选择。
2. 前端桥接器分配页面 client ID 和递增序号，立即提交事件。后端严格解析该模型，原子更新 PaperQA2 通用配置与会话事件状态，之后才确认成功。
3. 工作台通过父页面确认消息及 SSE 事件立即更新名称；不存在按 2.5/5 秒检查模型的定时器。SSE 的保活不是模型查询。
4. 任务提交前等待当前选择的同步确认；提交时记录 client、revision、selection 与模型快照。每个任务的 `paperqa_settings.json` 在入队时冻结，排队期间再切模型不会更换该任务的模型。
5. 多个页面的 client 状态独立，迟到序号不能覆盖新状态；带过期 revision 的任务拒绝提交，不静默使用旧模型。

## GPT‑5.6 的实际调用

DeepTutor 当前会话选择映射为 `gpt-5.6-sol`，binding 为 `openai_codex`。这不是普通 OpenAI API key 模型连接。

新增的本机适配路由 `/api/evidence-llm/v1/chat/completions` 调用 DeepTutor **同一运行进程**内的模型解析器、LLMFactory 与 OpenAICodexProvider，沿用其已有登录、续期、账户校验和并发控制。OAuth 凭据不传给浏览器、SearchTools 或 PaperQA2。

PaperQA2 使用 `openai/gpt-5.6-sol` 调用本机适配器，以仅限本机的随机凭据认证，并携带冻结的选择 ID。路由拒绝非回环请求、错误凭据和请求模型与选择不一致的情况。适配器兼容普通和聚合流式响应；没有在本轮实现逐 token 的透传流。

已修复 PaperQA2/LMI 自动传入 `temperature=0` 导致 GPT‑5.6 客户端拒绝的问题。适配调用交由 DeepTutor 的提供商处理受支持参数，客户端丢弃不支持的生成参数，不更换模型。

## 实际验收结果

| 验收 | 结果 |
| --- | --- |
| DeepTutor 界面切到 GPT‑5.6 | 原生选择器和工作台模型名一致；事件到后台确认曾测得 6–8 ms |
| 切到 deepseek-flash | 工作台立即更新；包含 PaperQA2 配置导出的确认耗时 **6.2 ms**；通用配置为 `deepseek/deepseek-flash` |
| 切回 gpt-5.6-sol | 工作台立即更新；包含配置导出的确认耗时 **8.6 ms**；通用配置为 `openai/gpt-5.6-sol` |
| DeepTutor 登录通道真实请求 | HTTP 200，响应模型 `gpt-5.6-sol`，固定测试输出 `MODEL_SYNC_OK` |
| PaperQA2 自身 Settings/get_llm 调用链 | 实际输出 `PAPERQA_GPT56_OK`，约 2.15 秒 |
| 完整检索与证据抽取任务 | 任务 `cb79d134e7bd4562b222502ce9eca0f5` 完成；保存卡 `ev_cfc53f9cd4`，定位为 `exact` |
| 回归 | **57 项 Python 测试通过**，包括网关认证、模型一致性、流式格式、事件唤醒、旧事件拒绝、任务快照及文件指纹；前端生产构建通过 |

上述毫秒数是浏览器发布选择事件到后台确认的实测值，不是推理耗时，也不是跨机器延迟保证。界面切换实测未等待定时刷新。模型测试只使用固定测试文字与明确标为虚构的测试 PDF，没有发送用户私有论文。

完整任务还发现：PaperQA2 可能生成缩短的文献名，导致正确片段没有保存。已增加输入快照内容指纹到文献 ID 的精确映射，保留原子摘录的出处，并对已有片段却不能落库的情况报告失败。独立模型验收项目 ID 为 `608e5d3239af438cad9df2fed8515345`。

- [界面实测截图](acceptance/2026-11-08/model-sync-proof.png)
- [结构化验收结果](acceptance/2026-11-08/realtime-model-sync-results.json)

## 部署及重现

维护文件位于 `integrations/deeptutor/`：

- `evidence_router.py`：本机适配器，安装到 DeepTutor 源码和实际运行的 Python 包。
- `install_source.py`：保留备份，接入路由与源码模型事件生产者；不改全局默认模型，不复制 OAuth 凭据。
- `deploy_frontend.py`：部署已构建的独立前端，备份旧运行目录并重新加载服务。

源码前端位于 `/home/qyw/DeepTutor/web.source-checkout`；以 `DEEPTUTOR_NEXT_DIST_DIR=.next-searchtools-sync NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 NEXT_PUBLIC_AUTH_ENABLED=false npm run build` 构建。当前服务使用的安装包与源码目录不同，两处适配器已同步部署。

当前部署和实测页面已生效。部署前已打开的旧 DeepTutor 页面需刷新一次加载新前端；之后切换模型不需要刷新。已运行任务保留启动配置，新任务绑定已确认选择。

本机缓存前端与已安装 Python 包的修改在上游升级时可能被替换；上游升级后应重跑本地集成安装、构建与部署。原包、前端运行目录和源文件备份均保留。

官方参考：[Codex app-server 授权与模型访问验证](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)。本轮的模型可用性结论来自实际完成的推理请求，不仅来自模型目录。
