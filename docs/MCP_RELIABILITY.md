# MCP 发现与成果传递

2026-11-01 的代码检查发现：分页目录只读第一页；独立 MCP 成员同时返回摘要和结构化数据时，后者会被丢弃；普通 MCP 工具的大结果会被切成不完整的 JSON。这些问题会让已配置的 Agent 不可见，或让后续成员只收到“已完成”，收不到实际统计与表格。

## 实际修复

- `backend/mcp_protocol.py` 复用官方 SDK 的 `PaginatedRequestParams`，读取同一连接中的全部工具页；目录检查、成员能力冻结、调用前检查和普通 MCP 工具调用共用该逻辑。游标按不透明字符串处理，不跨连接保存。重复名称、游标循环、超过 32 页、1024 个工具或 200 万字符时明确失败，不返回局部目录。
- 角色能力快照同时保存输入和输出 Schema 及摘要。调用前核对两者；结构化成果按服务声明的 JSON Schema 验证，使用空引用注册表，禁止通过 Schema 触发远程地址读取。旧任务的已完成记录仍直接复用；旧未发送任务遇到新增输出 Schema 时要求新建任务确认。
- 摘要文本和 `structuredContent` 同时保留。只有文本本身就是完全等价的 JSON 时才去重；数字 `1` 和布尔值 `true` 不混淆。独立成员的执行回执增加文本及结构化数据摘要，真实数据会随完整输出交给后续成员。
- 独立 MCP 成员保持 80000 字符完整输出上限；通用工具保持 32000 字符上限。超限明确报告，不能把切断的 JSON 当作成果。通用 MCP 超限返回固定 `MCP_RESULT_TOO_LARGE` 和 `retryable:false`；CAMEL/CrewAI 运行器保留“已执行、不要重复派发”的含义。
- 通用 MCP 派发后的超时、传输或输出 Schema 校验失败返回 `MCP_RESULT_UNCONFIRMED` 和 `retryable:false`。直接模型工具循环不再次发送同一服务器、工具和参数的请求；派发前目录或 Schema 错误不冒称任务已经执行。
- 项目范围、工具允许列表、调用前持久化、超时或崩溃后不自动重发保持原有规则。文本中的外部链接和非文本资源仍不是已验证的本地交付文件。

## 采用的开源依据

- [MCP 分页协议](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination)：工具列表支持分页，游标不透明且不能跨会话保存。
- [MCP 工具结果和输出 Schema](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#structured-content)：结构化数据与兼容文本可同时出现；客户端应验证声明的输出 Schema。
- [官方 Python SDK v1.30.0](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/session.py)，[MIT 许可](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/LICENSE)：项目当前安装的版本已提供分页消息与本地输出 Schema 验证。上游最新已发布 v2.3.0，但存在 API 和传输层变化，本次不进行未经迁移验证的大版本替换。
- [LangChain MCP Adapters](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/langchain_mcp_adapters/tools.py)，[MIT 许可](https://github.com/langchain-ai/langchain-mcp-adapters/blob/main/LICENSE)，[0.3.2 发布记录](https://github.com/langchain-ai/langchain-mcp-adapters/releases/tag/langchain-mcp-adapters%3D%3D0.3.2)：借鉴完整分页与保留结构化 artifact 的设计。ModelDesk 已直接使用官方 SDK，因此未增加另一套 Agent 运行框架；仅补充自身的权限、边界与交接适配。

## 验证

回归覆盖第二页工具、空中间页、不透明游标、循环和目录上限、文本与结构化数据共同保留、准确去重、输入/输出能力变化、旧记录兼容、非法输出、远程 Schema 引用、超限与不重复派发。

真实 localhost Streamable HTTP 验收使用官方 SDK 启动分页服务器：目录检查、冻结与执行分别读完两页，实际调用一次，重复进入复用原回执；下游文本同时包含原始摘要和结构化统计。记录位于 `data/acceptance-218/mcp-http-evidence.json`，复现脚本为同目录 `verify_mcp_http.py`。该验收证明 MCP 传输和交接，不表示新增了 Muse、Grok Bot 的专用接口或完成云端创作。
