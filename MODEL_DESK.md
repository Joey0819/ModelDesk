# ModelDesk 统一工作台

ModelDesk 2.1.0：一个页面使用已有 ChatGPT 与 Google / Antigravity 登录，运行并排聊天、Council、CAMEL、CrewAI 和 MultiBot Chat 式接力群聊，并添加本地项目进行真实代码审查。网页地址保持 http://localhost:5173/，当前电脑安装入口为 `/Applications/ModelDesk.app`，构建产物位于相邻目录 `../llm-tools/releases/ModelDesk.app`。

## 多智能体平台

首次打开进入「快速开始」，提供「开始协作」「添加项目用于代码审查」「打开最近成果」三个入口。常用模板和已保存提示词只填入输入框，补充材料并发送后才运行。知识、持久记忆、智能体/流程、工具/MCP、数据迁移、原版应用和原生 SDK 工作室继续位于「高级设置」或左侧「高级资源」中；完整 MultiBot 原版仍可在统一页显示。知识、项目快照与工具由用户明确选择。详见 [功能与验收表](docs/CAPABILITIES.md)。

## 使用

打开 `ModelDesk.app` 自动启动本机共享服务。也可运行相邻目录 `../llm-tools/start.command`，通过浏览器使用同一页面。

1. 从「快速开始」选择任务入口或模板，也可从左侧进入聊天、匿名评审、团队协作等模式。
2. 在「资料与工具」调整模型和必要资料；默认 `gpt-6.1-sol` 与 `gemini-3-flash`，已有的有效模型选择会恢复。未经实测的网关模型会单独标识。
3. 输入问题并发送。中间区域保留任务要求、各模型回复和运行过程；回答、主席结论、团队交付或实际发言会进入右侧「成果」面板。
4. 在成果面板选择具体输出，切换预览/文本，复制、下载 Markdown 或放大阅读。模型输出的 HTML 和脚本按源码展示，不在预览里执行。任务整体仍可导出 Markdown / JSON。
5. 继续输入下一步要求，或切换协作方式接续当前会话；需要完全独立的历史时，点击「新建独立会话」。运行中可停止，已收到的内容继续保留。

桌面宽屏采用「项目与最近会话 / 当前会话 / 成果」三栏。成果区有独立滚动，可拖动分隔线或用键盘左右键调整宽度，设置保存在本机；较窄窗口用可关闭的覆盖面板查看成果，保留会话输入空间。回复和代码块也提供直接复制入口。

## 当前会话的上下文

「会话记忆」默认开启，聊天、Council、CAMEL、CrewAI、Workforce、Flow 和群聊都会接续当前会话中同一项目范围的已完成内容。默认最多引用 12 轮，在「资料与工具 → 通用提示与会话上下文」调整为 1–40 轮；历史字符预算最多 16000，长输入和已选资料会进一步压缩可用预算。引用轮数、来源任务/模型、截断与排除原因保留在运行记录和导出中。

切换协作方式会保留当前会话，下一次发送使用新方式；各模型的真实输出带来源标签，其他模型意见作为引用材料，不冒充当前模型自己的历史。失败、停止、未完成的任务与原始框架日志不作为已完成交付传入下一轮。不同项目的历史不会混入，改变快照时旧成果会标记为旧快照引用，不能当作当前源码或工具授权。

「新建独立会话」重置本轮历史链；关闭「会话记忆」时下一次发送不引用历史。会话历史、每种协作方式的草稿和明确选择的资料保存在本机，刷新后可恢复；供应商凭据与私有运行令牌不会进入这些草稿。这里的当前会话上下文与「高级资源 → 记忆」中的长期条目分开管理，长期资料和工具仍需明确选择。

## Workforce 失败与重试

Workforce 规划依据各角色实际启用的工具与任务要求。角色缺少所需能力、工具拒绝访问或原生任务返回失败时，任务仍显示失败；空的最终答复不会被当作成功。已产生的步骤、部分输出和原生失败原因保留在历史中，刷新、服务重启以及 Markdown/JSON 导出可以继续复核；既有历史也可从保存的原生事件恢复失败信息。

工具阶段结束后，如果结果缺少有效的 `TaskResult` 格式，可由同一模型进行一次有范围限制的结果整理：只使用实际任务、近期对话与已返回的工具证据，关闭工具调用，保留证据截断标记。整理结果必须通过严格结构校验；格式仍无效、输出被截断或关键要求未满足时继续报告失败，并保留 `structured_output_invalid` 等诊断及原始输出。这个阶段整理已有证据，不重新执行研究任务。

临时传输问题由 SDK 最多重试两次，不重放已经执行的角色或工具流程。任务本身的失败不会因重试而改成成功。可查看各角色工具、编辑任务与配置后创建新任务；系统不会自动开启工具或扩大资料授权。工具可用性检查和失败提示帮助定位问题，不能保证所有任务或外部服务都能完成。

网页工具兼容代理把公开域名解析为 `198.18.0.0/15` 假地址的情况：仅当域名的本机解析结果全部落入该范围时，才通过固定 Cloudflare DoH 地址重新查询 A/AAAA，并要求返回地址全部为公网地址。实际网页连接固定到已检查的 IP，同时保留原域名 Host、TLS SNI 和证书验证，每次重定向重新校验；本机代理设置保持原状。[Cloudflare DoH JSON](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)、[HTTPX SNI 扩展](https://www.python-httpx.org/advanced/extensions/)

当前已通过 13 项工作进程测试、31 项界面状态与配置测试及 14 项公开网页目标测试（含 5 项新增回归）。原始 Reddit 来源核查在 15 次实际网页调用后仍未找到符合所述日期与评分的原帖证据；Reddit 的 JavaScript/登录限制也影响访问。页面以 `source_unverified` 诊断展示该任务，原始失败状态、完整输出及工具记录已保存，其结构化结果已成功解析，失败不再被误述为工具权限或结果格式问题。

另一个明确要求「原创教学示例、不得声称已核实 Reddit 原帖」的原生管线已完成：GPT `gpt-6.1-sol` 提纲/起草，Gemini `gemini-3.8-flash-high` 审稿，三个步骤均通过严格 `TaskResult` 校验，最终正文约 3300 字符。这个成功结果证明教学生成与审稿流程可运行；原始 Reddit 来源仍未核实。两项实际验收分别保存在 `data/workforce-recovery-validation.json` 与 `data/workforce-recovery-validation-source-unverified.json`。

2.0.1 应用包已重建、安装并通过签名检查。这是此前故障修复版本的验收，不代表新的 2.1.0 界面发布流程已经完成。

## 添加项目与审查代码

1. 左侧打开「项目与代码评审」→「添加项目」。桌面应用可选择文件夹，浏览器填写绝对路径；已有项目无需迁移，普通文件夹也可添加。
2. 可按名称或路径搜索已登记项目，完整名称与路径可悬停查看。选择工作区、暂存区、最近提交、分支差异或指定文件，点击「预览要审查的代码」，展开检查纳入/排除/截断内容。大型范围会明确显示覆盖限制。
3. 预览完成后出现「开始代码评审」；可填写审查重点，默认使用当前匿名互评配置，其他方式、参与模型与主席设置在「调整审查方式与模型」中。报告展示文件与行号、严重程度、原始代码证据、建议及模型来源；引用位置或证据不匹配的意见单独列为未确认。历史评审可通过「打开成果」进入右侧面板。

审查使用固定源码快照，后续源码变化需要重新预览；历史报告继续关联原快照。源码作为不可信材料处理，流程不执行项目代码、测试或 Git 写入。位置与证据核验不能替代缺陷的人工确认。移除项目只解除登记，不删除源文件。

关闭桌面窗口保留共享服务，Chrome ChatHub 仍可使用同一登录网关。停止全部服务请运行 `python3 ../llm-tools/scripts/services.py stop`。

## 安装和认证范围

这份 macOS 应用面向当前电脑，复用本机已安装的 Python 环境、模型网关和认证文件；移动到另一台电脑需要安装这些依赖。应用包不包含登录凭据。

浏览器仅接收模型名称、任务和公开的认证状态。OAuth 凭据保留在 `../llm-tools/.private/auth/`；API 不输出 token 或账号邮箱。Claude 订阅 OAuth 未接入本工作台，可继续使用官方客户端。

任务保存至本仓库被忽略的 `data/workbench/`，项目登记与快照保存至 `data/projects/`。服务仅绑定 loopback。任务通过固定参数调用已有框架，停止操作仅终止对应任务的自有进程。服务重启会恢复已完成历史，并标记未完成任务中断。

## 2.1.0 验证状态

自动检查覆盖后端 70 项、前端状态与真实组件渲染 49 项、桌面导航/下载策略 11 项，另检查 lint 与生产构建。浏览器真实验收中，GPT `gpt-6.1-sol` 与 Gemini `gemini-3.8-flash-high` 从聊天接续到 CrewAI 与 Council，均在未重发代号的后续任务中正确沿用“杉桥7392”；Council 全部三个阶段完成且没有部分失败。新建独立会话的两模型均回复“没有历史代号”。项目评审实际引用同项目已完成记录，新快照将旧快照标注为已变化，返回两条带准确行号的发现。复制经过剪贴板回读，单份成果下载端点返回正确附件正文；设置刷新、模板预填、项目搜索、成果放大和窄窗口布局已通过实际界面验证。证据见 `data/ux-validation.json`、`data/ux-council-final.json` 与相邻 `llm-tools/reports/modeldesk-2.1-*.jpg`。

2.1.0 实际应用包已重建并安装到 `/Applications/ModelDesk.app`，版本、签名与 ASAR 一致性检查通过。当前 Mac 锁屏阻止原生窗口及保存对话框操作，这两项尚未实测；不能把端点附件响应当作原生文件已保存。Council 曾遇到上游 TLS 超时，现已提供有限传输重试与各模型诊断；一次成功重试不表示上游网络问题已永久消除。

## 界面参考

布局参考官方公开的使用方式：Codex 示例中的 Projects / Recents 整理入口（[官方功能页](https://learn.chatgpt.com/docs/features)）；Claude 以模板开始、成果与会话并排并可再次打开（[Artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them)）；Cursor 统一跨项目工作区和差异审查（[Agents Window](https://cursor.com/docs/agent/agents-window)）；Dify 从输入、配置到明确 Test Run 的快速开始路径（[官方教程](https://docs.dify.ai/en/quick-start)）。ModelDesk 据此采用任务入口、最近会话、可预览代码与独立成果面板；这些是交互参考，不表示完整复制上述产品能力。

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
