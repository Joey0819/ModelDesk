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
- 本机运行报告和账户数据位于被忽略目录，不进入源码提交或桌面包。

## 认证、部署与来源

现有 ChatGPT 与 Google/Antigravity 登录通过本机网关使用。Claude、其他模型厂商、外部 MCP、云同步后端、训练硬件和各 toolkit 的专用依赖，按对应服务要求配置；平台不会伪造这些外部条件已具备。ChatHub 原扩展可使用其原有网页登录方式。

当前桌面包针对本机，依赖相邻 `llm-tools` 中的隔离环境、原版源码、网关和本机数据；不包含 OAuth 凭据。服务均监听 loopback：8317 网关、8001 API、5173 页面、8371 本机 MCP、8501 原版 MultiBot。

原始项目：[ChatHub](https://github.com/chathub-dev/chathub)、[LLM Council](https://github.com/karpathy/llm-council)、[CAMEL](https://github.com/camel-ai/camel)、[CrewAI](https://github.com/crewAIInc/crewAI)、[MultiBot Chat](https://github.com/gptzm/multibot-chat)。ChatHub/MultiBot 为 GPL-3.0，CAMEL 为 Apache-2.0，CrewAI/MCP SDK 为 MIT，保留相应源码与许可。Council 仓库未声明独立 LICENSE；未来对外分发应先明确许可。

MultiBot 原版基线 commit `38e000df1462927eb2b5fec12b4de5a9b82b41be` 保持无修改；可追踪兼容入口修复 readability 字节解码和旧搜索客户端，并使用原版 UI、规划器、调度与其他工具。其独立本机账户/配置存私有目录，没有复制供应商 OAuth 文件。
