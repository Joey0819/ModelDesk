# ModelDesk

本机多模型、多智能体协作工作台。围绕同一个项目，把资料、模型讨论、团队执行、评审和最终成果放在一个工作区。

ModelDesk 基于 [Karpathy 的 LLM Council](https://github.com/karpathy/llm-council) 扩展，保留三阶段匿名评审，并接入 CAMEL、CrewAI、接力群聊、原生 SDK 和本地项目资料。原项目说明保留在 [上游 README](docs/UPSTREAM_COUNCIL.md)。

## 从一个目标开始

1. 创建项目，添加需要的图片、文件，或从关联文件夹中选择资料。
2. 打开「协作任务」，选一个流程模板，写下目标。可直接选择模型，也可新建项目协作组，保存成员名称、模型和分工。
3. 启动任务，查看步骤进度。需要人工把关的步骤可设置为完成后等待确认。
4. 在右侧查看、复制或导出实际成果。中断后可查看已完成步骤，再明确选择继续或重试。

「单项协作」保留直接聊天、Council、CAMEL、CrewAI、群聊、Workforce 和 Flow。代码评审使用明确预览的源码快照；SDK 工作室保留原生框架接口。

## 协作与执行

| 层 | 职责 |
| --- | --- |
| 项目与资料 | 项目范围、文件夹导入、图片/文本资料、不可变任务输入与来源记录 |
| 项目协作组 | 每项目保存 2–4 个不同模型的成员与角色，启动时冻结本次成员配置 |
| 协作任务 | LangGraph 顺序调度、步骤检查点、人工确认、停止、明确重试与运行预算 |
| 原生执行器 | 复用 Council、CAMEL、CrewAI、并排聊天和模型群聊的实际执行路径 |
| 成果与证据 | 真实子任务、模型来源、历史尝试、部分失败、Markdown/JSON 导出 |
| 本地桌面 | Electron 原生选择/保存窗口，复用本机服务与现有模型登录 |

详细设计和开源选型见 [Harness 架构](docs/HARNESS_ARCHITECTURE.md)，完整操作见 [ModelDesk 使用说明](MODEL_DESK.md)，验收和功能边界见 [功能表](docs/CAPABILITIES.md)。

## 当前电脑运行

已配置环境可打开 `/Applications/ModelDesk.app`，或从仓库根目录执行：

```bash
python3 ../llm-tools/scripts/services.py start
```

页面位于 http://localhost:5173/，API 位于 http://127.0.0.1:8001。模型通过本机网关调用，认证信息保留在本机私有目录。

此桌面包目前依赖相邻的 `llm-tools`、独立 CAMEL/CrewAI 环境和本机网关配置。克隆本仓库可取得工作台源码，不能自动取得这些运行环境或账户登录。

## 开发与验证

Python 3.12+，Node/npm，以及 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix desktop ci

# 后端、前端组件与状态、lint、桌面策略
.venv/bin/python scripts/verify_modeldesk.py

# 使用自建合成资料的真实跨框架验收（需要已有本地服务与登录）
.venv/bin/python scripts/verify_harness.py
.venv/bin/python scripts/verify_project_teams.py

# 重建生产页面和实际 macOS 应用
.venv/bin/python scripts/package_modeldesk.py
```

本地开发 API 和页面分别运行：

```bash
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

凭据、用户项目资料、运行记录、构建缓存和应用包不进入 Git。自动检查使用隔离测试数据；真实模型验收使用脚本生成的合成材料。

## 来源

- [LLM Council](https://github.com/karpathy/llm-council)：三阶段匿名评审与初始代码。
- [LangGraph](https://github.com/langchain-ai/langgraph)：图调度内核，MIT。
- [CAMEL](https://github.com/camel-ai/camel)：角色对话与 Workforce，Apache-2.0。
- [CrewAI](https://github.com/crewAIInc/crewAI)：团队任务与 Flow，MIT。
- [ChatHub](https://github.com/chathub-dev/chathub) 与 [MultiBot Chat](https://github.com/gptzm/multibot-chat)：保留原版应用入口，各自 GPL-3.0。
- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)：现有本机登录网关。

各来源保留自己的许可与运行要求，详见功能表。本仓库没有为上游未明确授权的代码另行授予许可。
