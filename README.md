# ModelDesk

**A local workspace where LLMs and executable Agents collaborate in project groups.**

把模型、执行 Agent、项目资料、代码审查和最终成果放在同一个工作区。你可以指定一个 LLM 担任 **统筹模型（Ruler）**，让它组织讨论、按需调用群内 Codex、Claude Code 或 Muse，再把实际返回内容整理成完整成果。

**当前桌面版本：2.8.0** · macOS / Apple Silicon · 中文界面 · 本机服务与私有手机入口

ModelDesk 基于 [Karpathy 的 LLM Council](https://github.com/karpathy/llm-council) 扩展，保留独立回答 → 匿名互评 → 主席综合的三阶段流程，并复用 CAMEL、CrewAI 和 LangGraph 的执行能力。原项目说明见 [上游 README](docs/UPSTREAM_COUNCIL.md)。

## 从项目群开始 / Quick start

1. **创建项目**：添加图片、文件，或关联本地文件夹。代码执行需要关联一个已有提交且工作区干净的 Git 仓库。
2. **设置团队**：打开「Agent 与连接 → 项目 Agent → 成员与分工」，选择统筹模型，为成员设置名称、类型与职责。需要 Grok 或其他模型时，先到「模型连接 / API Key」添加连接。
3. **描述成果**：保存协作组并新建项目群；修改已有群时，保存后点击「应用到当前群聊」。默认选择「自动完成」，发送你需要的完整文章、代码或其他交付要求。
4. **自动协作**：群内模型讨论后，统筹模型为必要的执行 Agent 分配任务；平台收集真实输出，再生成完整成果。时间线显示任务、失败与缺失项；右侧直接呈现最终正文。只想交流时切换「仅讨论」；也保留 @ 单个 Agent 的手动派发入口。
5. **审阅与交付**：在右侧成果栏预览、复制或下载实际内容。代码补丁仍需选择独立审查并通过人工审批卡合入本地项目。

每个协作组可保存 **2–4 名成员**，统筹模型单独选择；LLM 成员与统筹合计最多使用四个不同模型。群聊历史、草稿、成员职责和资料按项目保存，同一个模型也可以担任不同角色。

## Ruler 与执行 Agent

统筹模型与执行器分别配置。广播时，所选统筹 LLM 先给出计划，模型成员依次讨论。「自动完成」会进一步生成受限执行计划，调用已保存的实际 Agent，并单独整理完整交付；没有统筹模型时由所选模型成员负责整理。@ 指定模型不会扩大到其他执行成员。「仅讨论」不派发执行器；两种模式都不能自动批准代码合并。

| 成员类型 | 实际工作方式 | 配置与要求 |
| --- | --- | --- |
| **LLM 模型** | 群内发言、交叉讨论、只读审阅 | 已接入的模型、角色与职责；支持同模型多角色 |
| **Codex Agent** | 在隔离项目副本中编辑文件、运行可用测试、返回补丁 | 官方 CLI 现有登录；可单独指定执行模型 |
| **Claude Code Agent** | 通过受限 CLI 在隔离副本中读写文件 | 支持受限执行的 CLI 版本与可用登录；当前执行路径不提供终端测试 |
| **Muse 应用 Agent** | 操作本机 Muse，保存原生回执与实际读取的回复 | Mac 上安装并登录 Muse，原生桥接和辅助功能权限就绪 |

后续讨论可引用同群已完成 Agent 任务的有界输出，并保留任务 ID、执行器、输出与补丁指纹。计划、失败结果和不完整发言不会被当作完整交付。

操作细节见 [统筹模型与执行 Agent](docs/COORDINATOR_AND_AGENTS.md)。

「自动完成」的执行范围、失败处理和成果来源见 [自动交付说明](docs/AUTOMATIC_DELIVERY.md)。它按成员 ID 与类型派发，不把模型发言中的“已完成”当作执行回执。缺少必要图片、文件或运行记录时，仍保存可用正文，并明确标为部分交付。

## 模型连接 / Model connections

- **已有登录**：复用当前本机网关支持的 ChatGPT / Codex 与 Gemini 认证路径。订阅登录和通用 API Key 分开处理。
- **Grok / xAI**：在设置页填写 xAI API Key，读取账户模型列表，选择模型并测试实际调用。
- **其他兼容 API**：配置 OpenAI-compatible Chat Completions 地址、密钥与模型 ID；支持明确启用的本机模型服务，例如 Ollama。
- **凭据隔离**：API Key 只保存在本机后端私有文件中，不返回页面、不写入浏览器存储或任务导出。原生框架 Worker 使用临时、限定模型范围的调用授权。

读取到模型列表或发现登录凭据，不等于实际调用已经通过。界面单独记录调用测试状态。xAI API 接入的是 Grok 模型成员；已有独立 Grok Bot 的云端计算机、记忆和工具不会随 API Key 自动接入。

见 [模型连接与动态协作组](docs/MODEL_CONNECTIONS.md)。

## 一个工作区中的功能

| 功能 | 用途 |
| --- | --- |
| 项目与资料 | 普通项目、本地目录、图片和文件、明确选择的文件夹导入、源码快照 |
| 项目群聊 | 固定成员与职责、统筹模型、@ 指定成员、连续上下文、动态应用成员变更 |
| 独立代码 Agent | 隔离快照、真实进程输出、停止与中断记录、固定补丁的独立审阅 |
| 群内审批 | 绑定基准提交与补丁指纹，人工批准后 fast-forward 合入本地项目 |
| 协作任务 | LangGraph 步骤调度、检查点、人工确认、停止、明确继续与重试 |
| 单项协作 | 并排聊天、LLM Council、接力群聊、CAMEL、Workforce、CrewAI 与 Flow |
| 项目资源 | 提示词、角色模板、知识、记忆、工具和 MCP 连接 |
| 原生 SDK 工作室 | 在明确审查源码后运行 CAMEL / CrewAI Python 脚本 |
| 成果侧栏 | 预览实际返回内容、复制、Markdown / JSON 导出、来源和部分失败记录 |
| 手机控制 | Tailscale 私有 HTTPS、一次性配对、上传、群消息、任务状态与设备撤销 |
| macOS 桌面 | Electron 窗口、原生资料选择与保存、复用本机服务 |

ChatHub 和 MultiBot Chat 保留原版应用入口；它们自己的账户、设置与上下文独立管理。ModelDesk 并未将五个原项目的全部界面或所有上游功能完整内嵌。

## 手机访问与执行边界

Mac 与手机连接同一 Tailscale 网络后，在 Mac 的「手机连接」启动私有入口、生成一次性配对码。手机使用界面显示的 HTTPS 地址，不使用手机自身的 `localhost`。会话可从 Mac 撤销；Mac 需要保持开机、联网且服务运行。

原生 Muse 操作、API Key 管理、原生文件夹选择和任意 SDK Python 脚本执行需要在 Mac 操作。代码任务的命令网络关闭，原项目保持隔离；代码合入仍由人工审批。**本地审批合入不会自动推送或合并 GitHub PR。**

见 [Agent 群协作与手机操作](docs/AGENT_GROUPS_AND_PHONE.md) 和 [项目群聊与 Muse 指南](docs/PROJECT_GROUPS_AND_MUSE.md)。

## 本机启动与依赖 / Running locally

### 已配置的工作区

打开 `/Applications/ModelDesk.app`，或从本仓库根目录运行：

```bash
.venv/bin/python ../llm-tools/scripts/services.py start
```

| 服务 | 地址 |
| --- | --- |
| 工作台 | http://localhost:5173/ |
| API | http://127.0.0.1:8001 |
| 已有登录网关 | http://127.0.0.1:8317/v1 |

### 克隆本仓库前需要了解

此桌面包是当前本机工作区的启动入口，依赖相邻的 **`llm-tools`**、本机网关配置，以及单独安装的 CAMEL / CrewAI 运行环境。**克隆本仓库不会自动安装这些外部环境、取得模型登录或得到可独立分发的完整应用。**

基础开发需要 Python 3.12+、Node.js 20.19+（20.x）或 22.12+、npm 和 [uv](https://docs.astral.sh/uv/)。桌面打包需要 Apple Silicon Mac 与可用的 Xcode Command Line Tools（包括 Swift 编译器）。Codex / Claude Code 执行需要相应官方 CLI；Muse 需要原生 Mac 应用。具体已验证范围见 [功能与验收记录](docs/CAPABILITIES.md)。

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix desktop ci
```

本地开发分别启动 API 与前端：

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

上述命令安装和启动本仓库的开发环境，不安装外部网关或 CAMEL / CrewAI 环境。项目群推理依赖本机网关；独立 Council 流程默认使用 OpenRouter，可通过 `LLM_API_BASE`、`LLM_API_KEY` 配置兼容端点。API 和页面启动成功只说明本地服务可用；真实推理、框架任务与原生 Agent 仍需要各自的运行环境和登录。

## 验证、打包与已知限制

```bash
# 后端、前端组件与状态、lint、桌面策略
.venv/bin/python scripts/verify_modeldesk.py

# 合成资料的真实跨框架验收：需要已有服务、框架环境与模型登录
.venv/bin/python scripts/verify_harness.py
.venv/bin/python scripts/verify_project_teams.py

# 重建生产页面、实际 macOS 应用并校验签名
.venv/bin/python scripts/package_modeldesk.py
```

应用包输出到相邻目录 `llm-tools/releases/ModelDesk.app`，使用本机 ad-hoc 签名，未经 Apple 公证。凭据、用户项目资料、运行记录、缓存和生成的应用包不进入 Git；自动检查使用隔离测试数据。

2.8 的现场验收已验证 GPT / Gemini 讨论后的完整文章与实际下载、统筹模型自动派发 Codex 写文件与 Bash 测试，以及 Muse 新会话的实际送达、回复读取和最终整理。当前限制包括：

- 系统 Python 在隔离环境中未能取得开发者工具，Python 测试未运行；这与已通过的 Bash 用例分别记录。
- Claude Code 的受限执行环境尚未发现可用登录。
- Muse 需要 Mac 解锁且窗口可操作；锁屏、焦点变化或权限请求可能阻止操作。已读取真实回复仍不证明其中描述的外部动作或功能事实已经验证。失败不会用其他模型、固定模板或示例视频冒充成功。
- 手机连接、模型服务和原生应用的可用性需要在当前设备与当前登录下检查，历史通过记录不代表所有适配器始终可用。

## 文档导航

- [完整操作说明](MODEL_DESK.md)
- [统筹模型与执行 Agent](docs/COORDINATOR_AND_AGENTS.md)
- [模型连接与动态协作组](docs/MODEL_CONNECTIONS.md)
- [项目群聊与 Muse](docs/PROJECT_GROUPS_AND_MUSE.md)
- [手机访问、独立执行与审批](docs/AGENT_GROUPS_AND_PHONE.md)
- [Harness 架构](docs/HARNESS_ARCHITECTURE.md)
- [功能与验收边界](docs/CAPABILITIES.md)

## 来源与许可

- [LLM Council](https://github.com/karpathy/llm-council)：三阶段匿名评审与初始代码。
- [LangGraph](https://github.com/langchain-ai/langgraph)：图调度内核，MIT。
- [CAMEL](https://github.com/camel-ai/camel)：角色对话与 Workforce，Apache-2.0。
- [CrewAI](https://github.com/crewAIInc/crewAI)：团队任务与 Flow，MIT。
- [ChatHub](https://github.com/chathub-dev/chathub) 与 [MultiBot Chat](https://github.com/gptzm/multibot-chat)：原版应用入口，各自 GPL-3.0。
- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)：已有本机登录网关。

各来源保留自己的许可与运行要求；本仓库没有为上游未明确授权的代码另行授予许可。
