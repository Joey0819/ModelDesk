# ModelDesk 五项目能力与接入边界

审计日期：2026-10-11。依据各项目当前官方仓库、官方文档和本机集成源码。本页定义功能覆盖与验收边界；框架已安装、模式可运行和整个框架所有 API 已映射到页面是不同的交付粒度。

## 五个项目各自解决的问题

| 项目 | 官方核心能力 | ModelDesk 接入基线 | 需要保留的能力边界 |
|---|---|---|---|
| ChatHub | 多机器人同时回答、模型对照、提示词库、本地历史、数据导入导出、Markdown 分享、网页访问及浏览器快捷入口。[官方仓库](https://github.com/chathub-dev/chathub#-features) | 统一页通过本机模型网关完成并行回答、上下文追问、模型选择、历史和 Markdown/JSON 导出；原 Chrome 扩展也已单独安装并验证两个模型回复。 | 统一页属于并行聊天适配入口。原扩展的提示词库、整库迁移、浏览器快捷入口和网页访问不自动继承；Chrome 扩展 API 也不直接变成 Electron API。 |
| LLM Council | 多模型独立作答、匿名互评排序、主席综合三个阶段。[官方流程](https://github.com/karpathy/llm-council#llm-council) | 复用实际 Council 阶段函数，提供成员与主席选择、排名/匿名映射、部分失败信息和持久化结果。 | 原项目每次问题独立评审；普通聊天上下文与 Council 的评审协议需明确区分。排名是模型评审意见，代码缺陷仍需对应源码证据和人工确认。 |
| CAMEL | 可配置角色的 `RolePlaying` 对话，任务说明/规划、critic、Agent 与工具；还包括 Workforce、记忆、RAG 和多模态工具。[RolePlaying 官方实现](https://github.com/camel-ai/camel/blob/master/camel/societies/role_playing.py)、[工具文档](https://docs.camel-ai.org/key_modules/tools) | 独立 CAMEL 环境实际运行双角色多轮对话。自定义角色名称、目标、背景、模型与轮次传入真实 RolePlaying/ChatAgent；可选原生 MathToolkit 与 FunctionTool 包装的文本统计，保存逐轮 transcript 和工具结果。 | 此入口为双角色对话，主输入提供任务；未接入 TaskSpecify/TaskPlanner/critic 开关、Workforce、全部 toolkits、训练、RAG 与长期记忆。配置可选和工具实际被模型调用分别验证。 |
| CrewAI | Agent 的角色/目标/背景/工具，Task 的描述/输出/上下文，Crew 的任务编排；还有 Flows、记忆、知识源、规划与 MCP。[Agents](https://docs.crewai.com/en/concepts/agents)、[Tasks](https://docs.crewai.com/en/concepts/tasks)、[Crews](https://docs.crewai.com/en/concepts/crews)、[Flows](https://docs.crewai.com/en/concepts/flows) | 独立 CrewAI 环境实际构造 1–4 个 Agent、1–6 个 Task，可编辑角色/目标/背景/模型、任务描述/预期输出/前序任务上下文，执行 sequential 或配置管理者模型的 hierarchical 流程。受限算术与文本统计注册为真实 CrewAI tool，保存分步及最终输出。 | 任务依赖只允许引用已定义的前序任务；不是任意图形化 Flow 设计器。未接入知识库、长期记忆、全部官方工具或第三方 MCP。分层委派是否发生、工具是否调用，以实际任务结果为准。 |
| MultiBot Chat | 多机器人对话、接力群聊、自定义机器人与全局/接力提示词、上下文数量、话题/历史、账户与配置同步、可扩展工具箱。[官方说明](https://github.com/gptzm/multibot-chat#multibot-chat) | 参照原版群聊流程实现本机接力协议：所选模型按顺序多轮发言，各自收到其他成员的引用记录；提供成员角色、全局/接力提示、上下文数量、话题隔离、历史与导出。调用既有本机网关；未导入上游 Streamlit 调度源码。 | 原版完整 Streamlit 界面、账户/云同步、机器人批量管理、六组工具与厂商专用适配未内嵌。“群聊可用”不代表这些原版功能都已接入；群聊当前也不附带项目快照。 |

表中“接入基线”对应已有运行路径；本轮新增项目、角色/任务编辑和群聊功能以最终验证记录为准，不据此假定尚未完成的路径已通过。

## 本轮核心验收项

1. 选择本机项目目录，显示项目文件与 Git 状态/差异；支持明确选取源码后发起审查。结果包含可核对的文件、行号、严重程度、说明和建议，并可从历史重开与导出。目录选择只是授权选择路径，文件范围和内容读取还由后端校验。
2. 项目代码评审支持四种引擎：并行聊天、Council、CAMEL、CrewAI；评审任务保存项目、文件/差异快照、模型和引擎，避免重开历史时把新代码当作原分析证据。普通角色/团队任务另存其自定义配置；MultiBot 群聊目前未接入项目快照。
3. CAMEL 自定义角色与轮次，CrewAI 自定义 Agent/Task/Process，MultiBot 自定义成员角色、系统提示、接力顺序和上下文。修改配置后，用真实引擎结果验证该配置生效。
4. 工具明确列出可用范围、依赖和执行结果。项目源码读取、数学/统计或网页工具要通过真实工具接口执行；提示模型“你可以用工具”不足以证明工具接入。
5. 任务取消、异常、刷新和服务重启后，保留已生成输出、任务状态和历史。失败的外部网页/搜索或未配置的知识/记忆能力显示具体状态。

## MultiBot 原版复用调查

`gptzm/multibot-chat` 是独立 Streamlit 应用。其 [OpenAI 引擎](https://github.com/gptzm/multibot-chat/blob/main/bot/chat_router.py)使用机器人配置中的 `base_url` 与 `model`；可以把这一原模块接到现有本机 OpenAI 兼容网关，不必向该项目复制 ChatGPT/Google OAuth 凭据。工具规划模型由 `MULTIBOT_BASE_LLM_MODEL`、`MULTIBOT_BASE_LLM_BASE_URL` 和 `MULTIBOT_BASE_LLM_API_KEY` 单独配置。[默认配置](https://github.com/gptzm/multibot-chat/blob/main/config.py)、[规划调用](https://github.com/gptzm/multibot-chat/blob/main/utils/base_llm.py)

原版提供六组工具：计算、统计、网页正文提取、AI 规划讨论、搜索、Markdown 转纯文本。[原版工具说明](https://github.com/gptzm/multibot-chat#群聊工具箱) 工具实现的共同入口为 `run(parameter, content, group_prompt, group_history)`，工具元信息来自各目录的 config.json；[ToolManager](https://github.com/gptzm/multibot-chat/blob/main/tools/tool_manager.py) 可以继续复用。

| 原版模块 | 复用方式与实际条件 |
|---|---|
| calculator / statistics / markdown_to_text | 主要是纯函数工具；可通过隔离运行环境调用原模块，并保留原许可证与来源。计算器存在 `eval`，但输入先经过算式提取，不能只凭出现 eval 就宣称它执行任意代码；适配时仍需限制输入规模与执行时长。[计算器源码](https://github.com/gptzm/multibot-chat/blob/main/tools/calculator/calculator.py) |
| web_extractor / web_search | 原模块依赖网页网络、readability 与 DuckDuckGo。源码中的网页请求没有显式 timeout，适配层需有取消/超时控制；不可把未实际连通的搜索显示为已验证。[网页模块](https://github.com/gptzm/multibot-chat/blob/main/tools/web_extractor/web_extractor.py)、[搜索模块](https://github.com/gptzm/multibot-chat/blob/main/tools/web_search/web_search.py) |
| chat_pilot | 依赖 Streamlit 的 `session_state.bot_manager` 与 `tool_manager`，还依赖基础模型的 function calling。单独导入文件不会自动获得完整工具规划与成员调度。[规划器源码](https://github.com/gptzm/multibot-chat/blob/main/tools/chat_pilot/chat_pilot.py) |
| 原版完整界面 | 调查过保留独立源码/虚拟环境/账户数据、运行本机 Streamlit 并嵌入统一页的方案；本轮未安装或启用。当前交付采用上表的本机群聊协议，不提供原版账户、配置同步或全部工具界面。 |

原 `app.py` 启动时默认进入登录页；`GUEST_USERNAMES` 是配置名单，空用户文件不会自动创建访客或免登录会话。原版独立账户需先初始化，其账户与供应商模型登录不是同一认证。[应用入口](https://github.com/gptzm/multibot-chat/blob/main/app.py)、[用户管理](https://github.com/gptzm/multibot-chat/blob/main/utils/user_manager.py) 如复用完整界面，本机账户文件、令牌/密钥、日志和机器人配置均保存在私有运行目录，不进入源码提交或桌面包。

原版 [requirements.txt](https://github.com/gptzm/multibot-chat/blob/main/requirements.txt) 使用 Streamlit 1.41.1 与 OpenAI 1.55.3 等固定旧版本；应使用独立环境验证其依赖组合。`captcha` 由 streamlit-authenticator 0.4.1 转依赖带入；`readability.Document` 的依赖需在运行前确认。此审计没有把原项目“可安装”视为运行成功，也没有安装或导入任何用户凭据。

## 来源与许可

ChatHub 和 MultiBot Chat 的官方仓库声明 GPL-3.0；CAMEL 为 Apache-2.0；CrewAI 为 MIT。应保留各上游源码、许可证与改动来源。原版作为独立运行项目接入时，也不能把其许可证省略或宣称许可义务自动消失。[ChatHub LICENSE](https://github.com/chathub-dev/chathub/blob/main/LICENSE)、[MultiBot LICENSE](https://github.com/gptzm/multibot-chat/blob/main/LICENSE)、[CAMEL LICENSE](https://github.com/camel-ai/camel/blob/master/LICENSE)、[CrewAI LICENSE](https://github.com/crewAIInc/crewAI/blob/main/LICENSE)

审计时 Karpathy LLM Council 的官方仓库文件树未见独立 LICENSE，GitHub API 的 license 字段也为 null，因此此文不把它标为 MIT 或其他已明确许可的项目。[官方仓库](https://github.com/karpathy/llm-council)、[仓库元数据](https://api.github.com/repos/karpathy/llm-council) 本机使用/开发继续按当前任务范围进行；若未来对外分发，应先明确相应许可和各上游的分发要求。
