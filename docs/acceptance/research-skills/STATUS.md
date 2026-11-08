# 研究技能安装与真实文库验收记录

## 已安装的执行入口

两个技能安装在 ~/.codex/skills/paper-direction-discovery 和 ~/.codex/skills/paper-atomic-evidence，源包在 dist/research-skills。MCP服务新增 research_call、cards_locate、cards_open、cards_action_status。当前已打开聊天未重新加载MCP清单时，技能可使用 python3 -m searchtools.research_cli 的JSON标准输入入口。

研究接口提供持久运行记录、父子运行、原子句、候选绑定、真实PDF分页读取、Zotero检索、结构化方向/阅读/实验记录、审查包、上层意见、论点汇总、审核状态与JSON导出。方向撰写、实验比较、拆句和二审由运行技能的agent完成，底层提供数据与校验。独立命名的完整research_*工具族、跨源批量去重/预算控制/恢复调度仍使用原有工具加agent流程，尚未全部实现为专用后端操作。

## 本次实际验收

- 用真实库内 Proxy Target 和 SRL-CPG 两篇论文完成小样本定向阅读、两个候选方向与实验矩阵，见 directions.md 和 experiments.csv。未将其计为大规模查新测试。
- 单独建立Skill 2验收运行，核验部署机制句，并拒绝将84.6%能耗降幅归给Proxy Target真机实验。
- 建立Skill 1父运行及Skill 2子运行，完成选题交接、原子句与原文绑定的最小链路。见 results.json。
- 真正调用PaperQA2当前gpt-5.6-sol进行检索与抽取。首次因DeepTutor服务未运行而连接失败，启动服务后重新执行，任务8d4acb87fcd14772a5f0c7ccdd583eb7完成并返回4张卡。
- 上层当前Codex模型读取原文并对两个目标句的全部8个绑定逐项复核。部署句接受1项，真机84.6%句全部拒绝，见 lower-upper-review.json。下层分数10不能绕过上层门槛。
- 发现跨页卡混入页码24。上层拒绝该卡用于部署句，理由落库。定位层现已排除底部边距中的纯数字页脚；包含页码的旧摘录无法继续获得精确定位，需要重新抽取。
- 工作台真实按钮点击显示“阅读器已定位”。插件1.2.4及1.2.5返回附件QF757IAF、page_index=6、目标批注载入、页面渲染与选中确认。保存于 lower-upper-review.json。该证据为Reader状态回执，未取得Zotero像素截图。
- 插件原有高亮逻辑复用，并新增论点和二审状态显示。服务端by-item接口返回相同审核记录。原生插件面板按钮的实际鼠标点击尚未独立验收。

## 修复和边界

新增按run与atom校验的引用门槛，防止一张卡对某句审核通过后被挪用于错误句。原句/摘录/定位上下文变化时旧意见失效。论点汇总在全部候选已审后提交；未解决反证不能汇总为supported。新增工作台“研究论点与上层二审”区使用同一后端，待审原文仍可打开供核对。

当前保存的reviewer标识来自调用通道约定，未增加可认证的上下层身份隔离。独立命名的完整工具族、全量分页外部查新、OCR/复杂表格、真实手工反向建卡绑定atom以及大规模研究质量标注验收仍未完成。因此本次记录为可运行的第一阶段交付，完整v1.1计划仍有待办。

1.2.4重复打开曾出现Reader确认超时。1.2.5优先采用本次Reader.open返回的阅读器，并在延迟返回时选择最近初始化实例，同时移除泛临时标签导致的跨卡批注复用。真实同段连续两次打开均返回目标页渲染、批注载入与选中成功，回执见 repeated-open.json。

完整Python回归58项执行成功，其中7项跳过。Reader桥接契约测试通过。两份技能格式校验、JavaScript语法检查和变更格式检查通过。

最新补充：1.2.5存在间歇性渲染未完成，即page_index正确、annotation_in_reader=true、selected_annotation=true但page_rendered=false。已安装1.2.6将窗口恢复/激活提前，等待重启后复测。不能将既有两次成功记录解释为该问题已完全解决。

1.2.6重启后同段连续三次打开全部返回done且page_rendered/annotation_in_reader/selected_annotation均为true，详见 repeated-open-126.json。所述渲染问题在本次三次重复样本中未再出现。
