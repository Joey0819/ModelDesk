# ModelDesk 2.0 多智能体平台：功能入口与验收

更新日期：2026-10-12。平台把统一工作流、完整原版界面和原生 SDK 工作室放在同一个导航中。下表区分实际运行过的路径与需要用户配置的外部服务；原生 SDK 可访问完整已安装框架，不把一个固定示例当作整个框架。

| 项目/模块 | 统一页面可用功能 | 完整入口与验收依据 |
|---|---|---|
| ChatHub | 并排模型、连续上下文、角色/系统提示、联网和工具、提示词库、历史、Markdown/JSON、数据导入导出、浅色/深色模式 | 保留已安装的原 Chrome 扩展，固定入口打开完整浏览器版；扩展的快捷键和网页厂商登录继续由 Chrome 执行。ChatHub 格式导入接受提示词和历史，不复制原导出中的账号密钥。 |
| LLM Council | 成员/主席选择、独立回答、匿名互评和排名、综合结果、项目快照与知识上下文、第一阶段模型工具调用 | 复用实际三阶段协议；运行过程、匿名映射、部分失败和来源均保存。原三阶段与代码审查已真实模型验证。 |
| CAMEL | 自定义角色/模型/工具、角色多轮、任务细化、任务规划、Critic、原生 Workforce 自动分解及配置任务管线 | 真实 `RolePlaying`/`Workforce`；任务细化/规划/selector、自动分解与明确任务依赖均已实测。更广的 toolkit、记忆/RAG/多模态/研究 API 在原生 SDK 工作室编写和运行；依赖按模块实际要求配置。 |
| CrewAI | Agent/Task/Crew、角色与工具、前序上下文、顺序/分层、管理者、规划、原生 Memory、原生 Flow 条件路由 | 真实 `Crew`、`Memory` 与 `Flow.start/router/listen`；分支执行与跳过、实际规划及本机持久记忆已实测。其他原生 API、知识源、训练和 MCP DSL 可通过 SDK 工作室配置。 |
| MultiBot Chat | 统一接力讨论、成员角色、共享话题、轮次、上下文、工具、项目/知识/记忆绑定 | **完整原版 Streamlit 界面已内嵌**：机器人管理、话题、账户、配置导入/同步以及六组工具。真实 GPT/Gemini、原规划器、计算、统计、网页正文、搜索和 Markdown 转换全部通过原版路径验证。 |
| 项目 | 添加/编辑/解除登记，Git 工作区/暂存/提交/分支/指定文件，固定快照预览、证据位置审查、历史与导出；快照可用于普通团队和群聊 | 路径、凭据/二进制排除、内容指纹、过期检测、引用行范围核查。解除登记不删除代码。代码审查默认不执行项目代码或测试。 |
| 知识库 | 文本/PDF/DOCX/Markdown/CSV/JSON/源码上传，编辑、归档/恢复、中文检索、文档指纹和行范围、任务绑定 | `jieba` 分词 + `rank_bm25.BM25Plus`；真实模型通过检索工具读取明确所选文档。属于词项 RAG，不将其称作语义 embedding；扫描 PDF 需要先 OCR。 |
| 记忆 | 本机持久条目、命名空间、来源、显式选择/保存、归档/恢复；CrewAI 原生持久记忆 | 平台记忆与任务来源持久化；CrewAI 使用本机 LanceDB 与确定性词法哈希向量，无需供应商 embedding key。不同命名空间与任务工具授权已验证。 |
| 工具/MCP | 11 种实际工具、参数表单/JSON、调用结果，MCP HTTP/SSE 发现、工具白名单、手动或智能体调用 | 官方 MCP Python SDK；附带持续运行的本机分析 MCP 服务（8371），完整 initialize/list_tools/call_tool 和模型调用已实测。外部服务器按其账号、权限和网络条件配置。 |
| 智能体/流程库 | 自定义智能体、团队模板、任务依赖、条件分支、编辑/归档/恢复、重复运行、实时输出/取消/历史 | 配置进入真实框架。默认不继承模板中隐含的知识/项目授权，运行时明确选择资料。 |
| SDK 工作室 | 原生 CAMEL/CrewAI 模板、代码编辑、模型与任务变量、RESULT 输出、明确执行确认、错误/运行记录/导出 | 两个原生 SDK 模板已通过真实模型调用。脚本以本机 Python 权限执行，修改代码/模板后确认会重置；不由智能体隐式调用。未配置的外部模型、服务或硬件会返回实际错误。 |
| 数据迁移 | 平台资源与已结束任务历史备份，增量导入，跳过或重命名冲突，保留资源 UUID 引用、拒绝损坏历史 | 整包导出再导入实测通过；只保留任务历史，不启动导入任务。OAuth 凭据、私有运行令牌和原 ChatHub 供应商设置不迁移。原版 MultiBot 配置/账户由其原界面管理。 |

## 2.0.1：Workforce 失败处理

规划器接收每个角色实际启用的工具与能力清单，用于约束任务分工；未启用的工具仍不可调用。原生任务失败或最终答复为空时保留失败状态，并保存已生成的步骤、部分输出和原生失败原因。刷新、服务重启及 Markdown/JSON 导出复原这些记录；既有任务也可从其持久事件恢复原生失败信息。

原始研究输出不是有效 `TaskResult` 时，允许同一模型进行一次关闭工具的结构化整理，输入仅为实际任务、近期对话及已执行工具返回的证据。对话证据最多保留 64000 字符、单条最多 12000 字符，截断会标记；整理阶段最多一次模型迭代，输出按严格 JSON/布尔字段与完成标记校验。无效结构或输出截断记为 `structured_output_invalid`；要求没有完成则仍失败，原始文本及格式诊断继续保留，不重放工具或任务步骤。

仅临时传输问题使用 SDK 最多两次重试，任务本身的失败继续报告为失败；不会重放已经执行的角色或工具步骤。页面提供角色工具摘要、运行前检查和编辑后创建新任务的入口，修正配置仍由用户明确选择，不自动启用工具或扩大来源范围。此处理不保证所有工具、任务或外部服务都能成功。

网页工具补充代理假地址兼容：仅非 IP 域名的本机解析结果全部为 `198.18.0.0/15` 时，使用固定 `1.1.1.1` 的 TLS 验证 DoH 查询 A/AAAA，检查所有返回地址均为公网。网页请求固定到验证后的 IP，保留原域名 Host/TLS SNI，关闭该请求对环境代理的隐式继承，并逐次检查重定向；没有修改系统代理设置。[Cloudflare 官方接口](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)、[HTTPX 官方扩展](https://www.python-httpx.org/advanced/extensions/) 本轮已实测读取 [Claude Code 官方概览](https://code.claude.com/docs/en/overview)，HTTP 200、提取 9239 字符。该证据验证公开网页路径，不代表所有网站、原始任务或界面操作都已成功。

最新回归通过：工作进程 13 项、界面状态与配置测试 31 项、公开网页目标 14 项（含 5 项新增）。两项真实模型验收的任务要求与结论分别记录：

- 原始 Reddit 来源核查 `cac775b7-1b0e-4167-8924-68000df5ef0c`：15 次实际网页调用后仍未找到符合所述日期/评分的原帖证据，Reddit 的 JavaScript/登录限制也影响访问。严格 `TaskResult` 已成功解析、模型结束原因为 stop，完整原始内容保留；任务仍为 FAILED，页面诊断为 `source_unverified`，没有把来源缺口改成成功或误报为工具权限问题。
- 独立原创教学管线 `70f2f8d5-b744-4b9e-be8d-77115c73f41c`：明确要求原创示例并注明未核实 Reddit 来源；GPT `gpt-6.1-sol` 提纲/起草，Gemini `gemini-3.8-flash-high` 审稿，三个原生步骤均严格解析并正常结束。结果为 COMPLETE/DONE、partial=false，完整管线输出约 3300 字符。这个成功结果验证生成与审稿管线，不能替代原始来源核查。

2.0.1 本机应用包已重建、安装并通过签名检查；最终发布流程再次构建页面/应用包并复核安装与发布产物一致性。上表的 2.0.0 调用记录、回归测试和原创教学成功，均不代表原始 Reddit 来源已经核实。

## 核心操作

1. 在「平台资源」管理智能体、流程、知识、记忆和提示词。
2. 在协作任务或流程运行前选择知识文档、记忆空间、项目快照和允许的工具；未选择的资料不会自动进入模型上下文。
3. 「智能体与流程 → 流程库」配置任务依赖、分支、规划和记忆；「原生 SDK 工作室」使用没有专用表单的原生框架 API。
4. 「原版应用」连接并显示完整 MultiBot；ChatHub 保留完整浏览器扩展入口。
5. 从任务记录查看实际模型、工具结果、分支/跳过原因与引用来源，可停止并导出。

## 实际验收文件

- `data/advanced-validation.json`：CAMEL 高级角色、Workforce、Crew 规划/原生记忆、Flow 的真实模型调用。
- `data/platform-framework-validation.json`：知识/记忆/项目快照工具的实际 Workforce 和 Flow 调用；越权/结束后令牌撤销测试。
- `data/super-platform-validation.json`：中文知识检索、记忆、真实 MCP、模型工具调用、两种原生 SDK、数据往返迁移与群聊工具。
- `data/project-validation.json`：四种代码审查引擎的源码证据。
- `data/workforce-recovery-validation.json`：独立原创教学管线的提纲、起草、审稿成功记录。
- `data/workforce-recovery-validation-source-unverified.json`：原始 Reddit 来源仍未核实的失败记录；报告另导出至 `../llm-tools/reports/Reddit-案例核查-未核实.md`。
- 本机运行报告和账户数据位于被忽略目录，不进入源码提交或桌面包。

## 认证、部署与来源

现有 ChatGPT 与 Google/Antigravity 登录通过本机网关使用。Claude、其他模型厂商、外部 MCP、云同步后端、训练硬件和各 toolkit 的专用依赖，按对应服务要求配置；平台不会伪造这些外部条件已具备。ChatHub 原扩展可使用其原有网页登录方式。

当前桌面包针对本机，依赖相邻 `llm-tools` 中的隔离环境、原版源码、网关和本机数据；不包含 OAuth 凭据。服务均监听 loopback：8317 网关、8001 API、5173 页面、8371 本机 MCP、8501 原版 MultiBot。

原始项目：[ChatHub](https://github.com/chathub-dev/chathub)、[LLM Council](https://github.com/karpathy/llm-council)、[CAMEL](https://github.com/camel-ai/camel)、[CrewAI](https://github.com/crewAIInc/crewAI)、[MultiBot Chat](https://github.com/gptzm/multibot-chat)。ChatHub/MultiBot 为 GPL-3.0，CAMEL 为 Apache-2.0，CrewAI/MCP SDK 为 MIT，保留相应源码与许可。Council 仓库未声明独立 LICENSE；未来对外分发应先明确许可。

MultiBot 原版基线 commit `38e000df1462927eb2b5fec12b4de5a9b82b41be` 保持无修改；可追踪兼容入口修复 readability 字节解码和旧搜索客户端，并使用原版 UI、规划器、调度与其他工具。其独立本机账户/配置存私有目录，没有复制供应商 OAuth 文件。
