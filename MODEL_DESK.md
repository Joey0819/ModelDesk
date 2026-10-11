# ModelDesk 统一工作台

ModelDesk 2.0.0：一个页面使用已有 ChatGPT 与 Google / Antigravity 登录，运行并排聊天、Council、CAMEL、CrewAI 和 MultiBot Chat 式接力群聊，并添加本地项目进行真实代码审查。网页地址保持 http://localhost:5173/，当前电脑安装入口为 `/Applications/ModelDesk.app`，构建产物位于相邻目录 `../llm-tools/releases/ModelDesk.app`。

## 多智能体平台

左侧切换「平台资源」，可管理提示词、知识、记忆、智能体、流程、工具/MCP、数据迁移与原版应用。「智能体与流程」提供原生 Workforce/Flow 和 SDK 工作室；完整 MultiBot 原版直接在统一页显示。知识与项目上下文由每个任务明确选择。详见 [功能与验收表](docs/CAPABILITIES.md)。

## 使用

打开 `ModelDesk.app` 自动启动本机共享服务。也可运行相邻目录 `../llm-tools/start.command`，通过浏览器使用同一页面。

1. 左侧选择多模型聊天、匿名评审、角色对话、任务协作或模型群聊。
2. 选择模型；默认 `gpt-6.1-sol` 与 `gemini-3-flash`。未经实测的网关模型会单独标识。
3. 输入问题并发送。聊天并排显示流式回答；Council 显示独立回答、匿名排名和主席综合；CAMEL 显示自定义双角色多轮讨论；CrewAI 可设置角色、任务、前序上下文和顺序/分层流程；群聊让各成员依次读取共享讨论并接话。
4. 运行中可停止任务，从历史打开或导出 Markdown / JSON。中断的框架任务可展开运行记录，完整结果是否生成会明确显示。

多模型聊天和群聊延续同一话题上下文，可设置角色、系统提示与上下文数量；Council、CAMEL 与 CrewAI 每次输入启动一项独立任务。框架角色可启用数学计算、文本统计工具，运行记录显示实际调用结果；模板保存在本机。更广泛的框架 API 覆盖情况见 [五项目能力边界](docs/CAPABILITIES.md)。

## 添加项目与审查代码

1. 左侧打开「项目与代码评审」→「添加项目」。桌面应用可选择文件夹，浏览器填写绝对路径；已有项目无需迁移，普通文件夹也可添加。
2. 选择工作区、暂存区、最近提交、分支差异或指定文件，点击「生成上下文预览」，检查纳入/排除/截断内容。大型范围会明确显示覆盖限制。
3. 选择独立审查、Council 匿名互评、CAMEL 角色讨论或 CrewAI 分工审查，再开始。报告展示文件与行号、严重程度、原始代码证据、建议及模型来源；引用位置或证据不匹配的意见单独列为未确认。

审查使用固定源码快照，后续源码变化需要重新预览；历史报告继续关联原快照。源码作为不可信材料处理，流程不执行项目代码、测试或 Git 写入。位置与证据核验不能替代缺陷的人工确认。移除项目只解除登记，不删除源文件。

关闭桌面窗口保留共享服务，Chrome ChatHub 仍可使用同一登录网关。停止全部服务请运行 `python3 ../llm-tools/scripts/services.py stop`。

## 安装和认证范围

这份 macOS 应用面向当前电脑，复用本机已安装的 Python 环境、模型网关和认证文件；移动到另一台电脑需要安装这些依赖。应用包不包含登录凭据。

浏览器仅接收模型名称、任务和公开的认证状态。OAuth 凭据保留在 `../llm-tools/.private/auth/`；API 不输出 token 或账号邮箱。Claude 订阅 OAuth 未接入本工作台，可继续使用官方客户端。

任务保存至本仓库被忽略的 `data/workbench/`，项目登记与快照保存至 `data/projects/`。服务仅绑定 loopback。任务通过固定参数调用已有框架，停止操作仅终止对应任务的自有进程。服务重启会恢复已完成历史，并标记未完成任务中断。

## 开发与验证

```bash
# 现有后端和框架环境已安装；首次安装桌面构建依赖
npm --prefix desktop ci

# 后端生命周期、流式恢复、导出、前端与桌面导航检查，不调用外部模型
.venv/bin/python scripts/verify_modeldesk.py

# 四种模式真实调用、取消、SSE 重放和上下文核查
.venv/bin/python scripts/verify_workbench.py

# 自定义框架角色、真实工具、任务依赖、群聊与话题上下文
.venv/bin/python scripts/verify_collaboration.py

# 不执行项目源码，使用专用缺陷示例核查四种审查引擎
.venv/bin/python scripts/verify_project_workflows.py --workers 2

# 平台、原生 SDK 和模型→工具真实链路
.venv/bin/python scripts/verify_super_platform.py
.venv/bin/python scripts/verify_platform_frameworks.py

# 重建生产页面和实际 macOS 应用包
.venv/bin/python scripts/package_modeldesk.py
```

构建使用 Electron，启用 sandbox、context isolation，并限制应用窗口导航到本地工作台。桌面包使用本机 ad hoc 签名；没有申请开发者公证。

## 复用的项目

[LLM Council](https://github.com/karpathy/llm-council) 提供三阶段匿名评审；[CAMEL](https://github.com/camel-ai/camel) 提供角色对话；[CrewAI](https://github.com/crewAIInc/crewAI) 提供任务协作；[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 提供本机 OAuth 模型网关。并排聊天接入同一网关，原 ChatHub 扩展继续可用。

界面的统一切换方式参考用户指定的 [CC Switch](https://github.com/farion1231/cc-switch)。
