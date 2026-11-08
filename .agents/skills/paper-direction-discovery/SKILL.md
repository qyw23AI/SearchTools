---
name: paper-direction-discovery
description: 从研究主题或初步思路出发，检索 Zotero 并用 paper-search 补充文献，分层阅读摘要、结论和实验，比较已有方法，产出有具体文献与实验依据的研究方向、论文大纲和可执行实验方案。适用于尚未确定论文方向的调研选题。
---

# 文献调研与研究选题


## 调用范围

单独请求选题时，交付候选方向、大纲、实验矩阵和来源后结束。用户要求连用时，先完成这些交付，再将其指定的方向和对应大纲交给 Skill 2。用户要求处理全部方向时分别建档。如果有多个候选且用户没有指定选择规则或方向，列出各方向 ID 供选择。仅有一个方向或已给出选择规则时直接交接。

## 开始

读取用户主题、已有思路和可用的算力/时间/数据约束。缺失但不阻塞的内容标为假设；影响方向可执行性的约束需要澄清。发现实际工具能力，不把本文件提及的拟建工具当成已安装。

建立独立 run 和明确 scope。使用已有授权；当前课题已授权自动补库则在预算内分批导入，否则暂存候选后取得批次授权。不得从本技能推断修改无关文库数据的授权。

## 检索与阅读

1. 建立任务、方法、指标、基线、条件等检索维度和中英文同义词。先发现 Zotero 标签和分类，再将标题、标签、摘要命中取并集；避免只按标签漏掉未标记论文。
2. 使用实际 Zotero 工具参数（当前搜索参数为 `q`），记录来源。按维度检查缺口，需要时用 paper-search 多源补充；记录查询式、时间、版本、排除理由、平台限制。
3. DOI/arXiv/元数据去重，并关联预印本与正式版。每批最多20篇入库，等待附件与索引状态，不把元数据入库当成已读全文。
4. 按发现→摘要筛选→结论/实验定向阅读→核心来源逐段核验推进。默认发现上限300条、摘要约100篇、定向约40篇、核心约15篇，按领域和预算调整；禁止用数量宣称充分。
5. 保存每篇实际阅读的章节、缺失部分和锚点。核心方向来源必须读到相关实验、设置和限制；全文不可达时只报告摘要层信息。
6. 用实验级矩阵比较：任务/划分、基线、预算、方法差异、指标/单位/数值/误差、消融、表图和证据锚点。不可比条件分组，不把不同硬件或数据集的数字混排。

## 推导方向

为每个候选方向建立“已验证观察→局限/差距→研究者推导→可证伪假设→拟设计实验”的链。作者指出的局限与 agent 推导明确区分。引用来源不仅给文章名，还要给对应实验/消融/表图/段落及证据卡。

对核心事实和数值使用 PaperQA2 + 原文回验创建卡片；高相关分数或检索摘要不能替代原文核验。反证与不一致结果同样保留。

围绕每个候选方向做最近邻和重复性检索，记录已检索范围；不能因未搜到就声称从未有人研究。

默认给3个方向，每个包含：题目、问题、创新假设、来源表、最近邻差异、方法模块、论文大纲、基线/变量/消融/指标/统计方案、最小可行实验、资源成本、成功/失败判据和风险。预期改善是待检验假设，不写成已知结果。

若有依据的方向少于3个，减少正式方向数量并说明缺口。不要为凑数输出无来源方向。

## 交付与交接

输出 `directions.md`、`experiments.csv`、`handoff.json` 与阅读/检索账本。handoff 至少包含 schema_version、run/scope及revision、source manifest、direction IDs、方向来源绑定、大纲、实验和未解决缺口，使第二个技能可续用。

报告分别统计发现、去重、摘要已读、定向已读和核心已核验篇数。达到预算或定向补缺趋于饱和时交付受限结论、剩余缺口和续跑点。

已有工具可完成的部分直接执行；缺少结构化比较或研究服务时可以保存同等结构的本地产物，但必须披露降级，不能声称已自动持久化至尚不存在的接口。新工具补齐前不能把整链路标为验收通过。

## 当前实现的调用入口

使用 paperqa2-zotero 的 research_call(operation, arguments)，先调用 capabilities。详细设计见 [工具规格](references/TOOLS.md) 和 [流程](references/PLAN.md)。research_call 提供持久化 run、atoms、bindings、review_packets、submit_review、audit、read、record、search、export。拟议的专用 research_* 接口目前通过统一入口调用，方向生成与实验比较由当前 agent 阅读原文后执行并用 record 保存，尚未实现后台自动生成。

run 的 action=create，需要 scope_id 和 topic，返回的 id 即后续 run_id。atoms 的 action=create 需要 text，可含 type、original_sentence、outline_node。bindings 的 action=assign 需要 atom_id、card_id。review_packets 返回原句、原文段落、定位和 packet_revision。submit_review 传 binding_id、packet_revision、decision、relation、reason、span_ids，当前 agent 必须自己阅读并提交。record 通过 kind=direction/reading/experiment/search_log/atom_summary 保存结构化 data。read 接受 attachment_key 与分页 cursor。export 返回结构化交接数据，agent 保存 Markdown/CSV/JSON。所有后续调用都携带 run_id。

工具未加载时可在项目目录用 python3 -m searchtools.research_cli 读取标准输入 JSON，格式为 {"operation":"...","arguments":{...}}。引用目前仅开放当前版本上层审核接受的 supports 卡，限定或反驳材料先在报告列出关系及原文，不通过此引用格式化入口。

从任意目录调用时，运行本技能包内 scripts/research.py 并通过标准输入传入上述JSON。该适配器使用已安装的本地 SearchTools 项目；迁移机器后可设置 SEARCHTOOLS_ROOT。
