# SearchTools 科研工作区

将 **DeepTutor、GPT Researcher、PaperQA2** 整合进一个本地桌面 App。

- **工作区**：项目文献、PaperQA2 问答、GPT Researcher 调研、笔记、结果与任务队列。
- **DeepTutor**：独立、持续保留状态的原生网页标签页，保留其学习、精读、解题、知识库与设置入口。
- **GPT Researcher**：同样保留原生网页，方便使用高级研究、历史记录、文件等功能。
- **协作**：调研结果可交给 PaperQA2 核对，也可复制问题、结果与文献路径交给 DeepTutor。

应用使用 GTK / WebKit 构成桌面窗口，不通过 iframe 嵌入，不改写上游网页。工作区采用轻量本地服务，三工具各自使用独立 Python 环境。

## 新电脑完整安装

当前支持 **Ubuntu 22.04 / 24.04、GNOME、x86_64**。本地模型建议 NVIDIA GPU 16 GB，并预留约 30 GB 磁盘空间。NVIDIA 驱动需由系统预先安装。其他桌面环境可使用应用菜单，GNOME 收藏夹固定步骤可能不适用。

```bash
git clone https://github.com/qyw23AI/SearchTools.git ~/SearchTools
cd ~/SearchTools
./scripts/install.sh --system-deps
./scripts/searchtools open
```

第一次 `--system-deps` 会请求系统 sudo 权限安装 GTK/WebKit、Python venv、curl、zstd 等依赖。后续安装不需要重复加此参数。

安装器将：

1. 通过隔离的 uv 安装 Python 3.12，为三个工具建立独立环境。
2. 安装 DeepTutor 1.6.15、GPT Researcher 固定源码提交、PaperQA2 2026.8.12，以及固定版本的 Node / Ollama。
3. 为新安装生成本地 Ollama 配置；复用已有安装时保留其模型和账号配置。
4. 下载 Qwen3 8B 与 **Qwen3 Embedding 4B**，按固定 manifest 逐个校验 SHA-256；后者是专用嵌入模型。
5. 注册用户服务、应用菜单入口，并追加到 GNOME 收藏夹。

下载中断后重新运行同一命令即可继续。已成功安装的环境、已下载内容与私人配置保留。安装器固定主要上游版本，传递依赖仍按上游兼容范围解析；完全离线安装暂不支持。

如想先打开界面，稍后再下载模型：

```bash
./scripts/install.sh --system-deps --skip-models
./scripts/searchtools download-models
```

模型下载使用官方注册表的固定 blobs，可续传，不调用 GPU。首次安装需要访问 GitHub、PyPI、Python / Node 分发站点与 Ollama 注册表；可按需为当前终端设置网络代理，不必修改系统 DNS。

## 复用这台电脑已有的安装

已有目录是 `~/DeepTutor`、`~/GPT-Researcher`、`~/PaperQA2` 时：

```bash
./scripts/install.sh --reuse-existing --skip-models
```

此模式复用现有环境、模型和 DeepTutor 登录状态，不重复下载、不覆盖账号配置。只有首次生成 SearchTools 配置时才选择安装位置；已有配置以 `~/.config/searchtools/config.json` 为准。

## 日常使用

从收藏夹打开 **SearchTools 科研工作区**。窗口顶部三个标签页随时切换，页面保持加载。桌面窗口提供最小化、最大化、还原与边缘拖拽调整大小。右上角 `− / 百分比 / ＋` 可缩放，支持 `Ctrl +`、`Ctrl −` 和 `Ctrl 0`；桌面三个标签页分别记住比例，浏览器工作区也会保留自己的比例。左侧工作区提供：

1. **新建项目** → **项目文献**导入 PDF / TXT / Markdown，相同内容自动去重。
2. **论文问答**：输入问题，PaperQA2 自动使用当前项目文献。也可以先手动建立索引。
3. **方向调研**：选择 OpenAlex 在线学术来源或当前项目文献，生成 GPT Researcher 报告。
4. **任务进度**查看、取消、重试；完成后查看回答、原始引用与证据结构、日志，导出 Markdown。
5. 结果可保存为项目笔记、交给 PaperQA2 核对，或复制给 DeepTutor 精读。

DeepTutor 需要在其自己的知识库界面导入所需论文；项目文献页可打开对应文件夹。工作区项目不会自动改变两个原生工具当前选择的资料范围。它们的知识库和历史记录独立管理，顶层原生标签页也不会随项目切换重置。

**训练优先**：工作区提交的本地任务在检测到其他 GPU 程序时自动等待，串行执行；不能确认 GPU 状态时也等待。不会终止训练。原生 DeepTutor / GPT Researcher 中直接启动的任务由上游独立执行，不经过这个队列。

本地 Qwen3 8B 是初始可用配置，复杂研究的质量需自行验证。16 GB 显存可手动升级到 `qwen2.5:14b-instruct`（Q4_K_M，约 9 GB）；本地中文 PaperQA2 建议使用 `agent_type=fake`，避免整句中文检索不到和工具参数错误。调研和问答结果保留引擎返回的引用，应用不将其标记为已人工核实。OpenAlex 默认面向学术发现，不能等同于全网检索。

## 更新与重复部署

```bash
cd ~/SearchTools
./scripts/update.sh
```

脚本只进行 `git pull --ff-only` 并重新注册/重启工作区，私人数据和配置位于仓库外。更新时运行中的工作区任务会被中断，应先等待完成或取消。上游工具版本不会被更新脚本自动升级；需要升级时应修改安装清单并单独验证兼容性。

如果修改了仓库位置，再运行 `./scripts/install.sh --skip-models`，服务和桌面入口会写入新路径。若修改了私有引擎配置，重启对应 `searchtools-*.service`；复用模式下已有的外部服务仍由原来的管理器管理。

## 数据、配置与端口

| 内容 | 默认位置 |
|---|---|
| 工作区私有配置 | `~/.config/searchtools/config.json` |
| PaperQA2 私有模型设置 | `~/.config/searchtools/paperqa.json` |
| 项目论文、任务快照、日志、结果、SQLite | `~/.local/share/searchtools/` |
| 新安装引擎、Python、Node、Ollama 和模型 | `~/SearchTools-runtime/` |
| 复用安装时的模型 | `~/DeepTutor/data/ollama/models/` |
| 桌面入口 | `~/.local/share/applications/io.github.qyw23AI.SearchTools.desktop` |

工作区 `127.0.0.1:8765`，GPT Researcher `127.0.0.1:8002`，DeepTutor 前端 `3782` / 后端 `8001`，Ollama `11434`。请确保端口未被其他应用占用。DeepTutor 的绑定行为由其自身设置控制；SearchTools 工作区仅监听本机。

每个项目独立保存索引。任务提交时保留一份文献快照及哈希，后续导入不会改变已有任务的输入。多任务会占用额外磁盘空间，暂未提供自动清理。

备份时先等待任务结束并停止 `searchtools.service`，复制 `~/.local/share/searchtools` 和 `~/.config/searchtools`，然后重新启动服务。迁移到不同用户名/目录时，需修正配置路径；项目 PaperQA 索引可能包含绝对路径，建议重建索引。DeepTutor 与 GPT Researcher 的原生历史还需分别备份各自运行目录。

凭据、OAuth 状态、模型、论文、日志和数据库均不应提交到 Git。新电脑需要在对应工具中自行重新授权；DeepTutor 的现有授权不会被复制给其他工具。

## 诊断与开发验证

```bash
./scripts/searchtools doctor
journalctl --user -u searchtools.service -n 80
journalctl --user -u searchtools-gpt.service -n 80
journalctl --user -u searchtools-deeptutor.service -n 80
python3 -m unittest discover -s tests -v
```

工作区 Python 后端仅使用标准库；桌面外壳使用系统 Python 的 GTK / WebKit。网页无需 Node 构建步骤，Node 仅供 DeepTutor 上游前端运行。服务代码和接口见 [架构与边界](docs/ARCHITECTURE.md)，安装与验证记录见 [部署验收](docs/VALIDATION.md)。文献管理工具的选型与 Zotero 集成方案见 [选型与集成方案](docs/文献管理工具选型与集成方案.md)，只读脑图原型见 [zotero_mindmap.py](scripts/zotero_mindmap.py)。

关闭窗口不会中止后台工作区任务。工作区服务随用户登录启动；原生引擎在打开 App 时按需启动。

## 上游项目与许可

- [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor)：Apache-2.0。
- [assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher)：MIT。
- [Future-House/paper-qa](https://github.com/Future-House/paper-qa)：Apache-2.0。
- [Ollama](https://github.com/ollama/ollama)：MIT；模型权利与使用条件见对应上游模型说明。

本仓库是集成层，通过安装器获取上游软件，不包含其完整源代码或用户授权。SearchTools 自有代码采用 MIT 许可。
