# ModelDesk 统一工作台

ModelDesk 2.0.1：一个页面使用已有 ChatGPT 与 Google / Antigravity 登录，运行并排聊天、Council、CAMEL、CrewAI 和 MultiBot Chat 式接力群聊，并添加本地项目进行真实代码审查。网页地址保持 http://localhost:5173/，当前电脑安装入口为 `/Applications/ModelDesk.app`，构建产物位于相邻目录 `../llm-tools/releases/ModelDesk.app`。

## 多智能体平台

左侧切换「平台资源」，可管理提示词、知识、记忆、智能体、流程、工具/MCP、数据迁移与原版应用。「智能体与流程」提供原生 Workforce/Flow 和 SDK 工作室；完整 MultiBot 原版直接在统一页显示。知识与项目上下文由每个任务明确选择。详见 [功能与验收表](docs/CAPABILITIES.md)。

## 使用

打开 `ModelDesk.app` 自动启动本机共享服务。也可运行相邻目录 `../llm-tools/start.command`，通过浏览器使用同一页面。

1. 左侧选择多模型聊天、匿名评审、角色对话、任务协作或模型群聊。
2. 选择模型；默认 `gpt-6.1-sol` 与 `gemini-3-flash`。未经实测的网关模型会单独标识。
3. 输入问题并发送。聊天并排显示流式回答；Council 显示独立回答、匿名排名和主席综合；CAMEL 显示自定义双角色多轮讨论；CrewAI 可设置角色、任务、前序上下文和顺序/分层流程；群聊让各成员依次读取共享讨论并接话。
4. 运行中可停止任务，从历史打开或导出 Markdown / JSON。中断的框架任务可展开运行记录，完整结果是否生成会明确显示。

多模型聊天和群聊延续同一话题上下文，可设置角色、系统提示与上下文数量；Council、CAMEL 与 CrewAI 每次输入启动一项独立任务。框架角色可启用数学计算、文本统计工具，运行记录显示实际调用结果；模板保存在本机。更广泛的框架 API 覆盖情况见 [五项目能力边界](docs/CAPABILITIES.md)。

## Workforce 失败与重试

Workforce 规划依据各角色实际启用的工具与任务要求。角色缺少所需能力、工具拒绝访问或原生任务返回失败时，任务仍显示失败；空的最终答复不会被当作成功。已产生的步骤、部分输出和原生失败原因保留在历史中，刷新、服务重启以及 Markdown/JSON 导出可以继续复核；既有历史也可从保存的原生事件恢复失败信息。

工具阶段结束后，如果结果缺少有效的 `TaskResult` 格式，可由同一模型进行一次有范围限制的结果整理：只使用实际任务、近期对话与已返回的工具证据，关闭工具调用，保留证据截断标记。整理结果必须通过严格结构校验；格式仍无效、输出被截断或关键要求未满足时继续报告失败，并保留 `structured_output_invalid` 等诊断及原始输出。这个阶段整理已有证据，不重新执行研究任务。

临时传输问题由 SDK 最多重试两次，不重放已经执行的角色或工具流程。任务本身的失败不会因重试而改成成功。可查看各角色工具、编辑任务与配置后创建新任务；系统不会自动开启工具或扩大资料授权。工具可用性检查和失败提示帮助定位问题，不能保证所有任务或外部服务都能完成。

网页工具兼容代理把公开域名解析为 `198.18.0.0/15` 假地址的情况：仅当域名的本机解析结果全部落入该范围时，才通过固定 Cloudflare DoH 地址重新查询 A/AAAA，并要求返回地址全部为公网地址。实际网页连接固定到已检查的 IP，同时保留原域名 Host、TLS SNI 和证书验证，每次重定向重新校验；本机代理设置保持原状。[Cloudflare DoH JSON](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)、[HTTPX SNI 扩展](https://www.python-httpx.org/advanced/extensions/)

当前已通过 13 项工作进程测试、31 项界面状态与配置测试及 14 项公开网页目标测试（含 5 项新增回归）。原始 Reddit 来源核查在 15 次实际网页调用后仍未找到符合所述日期与评分的原帖证据；Reddit 的 JavaScript/登录限制也影响访问。页面以 `source_unverified` 诊断展示该任务，原始失败状态、完整输出及工具记录已保存，其结构化结果已成功解析，失败不再被误述为工具权限或结果格式问题。

另一个明确要求「原创教学示例、不得声称已核实 Reddit 原帖」的原生管线已完成：GPT `gpt-6.1-sol` 提纲/起草，Gemini `gemini-3.8-flash-high` 审稿，三个步骤均通过严格 `TaskResult` 校验，最终正文约 3300 字符。这个成功结果证明教学生成与审稿流程可运行；原始 Reddit 来源仍未核实。两项实际验收分别保存在 `data/workforce-recovery-validation.json` 与 `data/workforce-recovery-validation-source-unverified.json`。

2.0.1 应用包已重建并安装，签名校验通过；最终发布流程再次重建生产页面和应用包，并核对安装包与发布包的一致性。

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
