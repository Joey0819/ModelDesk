# 自定义模型连接与动态项目协作组

ModelDesk 2.6.0 新增了外部模型连接管理（包括 Grok / xAI、兼容 OpenAI 的第三方模型服务以及本机 Ollama 服务），并在原生框架执行中引入短期 Bridge Token 凭据隔离机制；同时将项目协作组与项目群聊的成员配置深度打通，支持动态更新群成员与职责分工，全面完成桌面版 2.6.0 的构建、签名与安装验证。

---

## 1. 外部模型连接与凭据安全

### 1.1 支持的服务类型
- **Grok / xAI**：官方地址 `https://api.x.ai/v1`，直接填写 xAI API Key，保存后自动读取账户内可用模型（如 `grok-beta` 等）。
- **兼容 OpenAI 的 API**：适用于任意遵循 OpenAI Chat Completions 规范的云端或私有模型服务，支持输入自定义 Base URL 与 API Key。
- **本机模型服务（Ollama / 本地 LLM）**：勾选「连接本机模型服务」，允许连接 `http://127.0.0.1` / `http://localhost` 端口（如 Ollama `http://127.0.0.1:11434/v1`），支持免密钥或自定义密钥连接。

### 1.2 凭据安全与零泄露设计
- **仅本机安全存储**：API Key 仅保存在本机 Mac 磁盘文件（`data/model-connections/connections.json`），绝不随网络回传给浏览器，不存入 localStorage 或 Cookie。
- **界面密码框保护**：编辑连接时输入框为 `type="password"`，回显时仅提示「已保存 · 留空保留」状态，不向页面暴露已有密钥字符。修改 Base URL 时强制要求重新输入密钥，防止密钥被误发往第三方未知端点。
- **模型自动发现与测试**：保存连接后，可点击「读取模型列表」自动拉取服务商提供的模型清单，勾选后即可加入工作台模型目录；点击「测试模型调用」发送简短验证探测，确认连通性。

### 1.3 原生 SDK Worker 的受限 Bridge Token 隔离
- 原生框架进程（如 CAMEL、CrewAI）调用通过自定义连接接入的模型时，系统**不会**将真实 API Key 写入进程环境变量或工作区输入文件。
- 后端为本次运行生成短期的 `bridge token`（`MODELDESK_BRIDGE_TOKEN`）并启动本地受限代理路由（`grant_worker`）。原生 Worker 进程仅持有该临时 Token，并在任务结束或取消时立即撤销（`revoke_worker`）。

---

## 2. 动态项目群团队管理（Agent 与连接）

### 2.1 全新「Agent 与连接」工作台视图
- 侧边栏及设置新增统一「Agent 与连接」入口，聚合三大功能区：
  - **项目 Agent**：按项目管理协作组，为每个成员设定明确名称、模型与职责分工。
  - **模型连接 / API Key**：集中管理 Grok、自定义 API 和本机模型连接。
  - **Muse 应用**：检查本机 Muse Mac 窗口桥接与辅助功能权限。

### 2.2 项目群动态成员更新
- 群聊创建后，用户可随时点击群顶部的「管理成员」为当前群应用已保存的协作组配置（`PUT /api/groups/{identifier}/team`）。
- **乐观并发版本控制**：接口通过 `expected_revision` 与群当前的 `team_revision` 进行校验，避免多窗口并发修改产生冲突。
- **安全变更守卫**：群内有活跃或排队中的讨论任务、代码执行任务时，系统阻止变更群成员，要求等待任务完成或停止后再应用。

### 2.3 历史记录不可变性与单模型多角色
- **不可变历史快照**：历史消息与已交付任务牢固保留当时的成员名单、角色快照与模型归属，后续的成员调整仅作用于新发送的讨论和任务。
- **单模型多角色独立性**：一个协作组内可包含多个使用相同底层模型（例如两个 Gemini 或两个 GPT）但承担不同职责的角色（如「架构师」与「测试专家」）。发言派发按成员 ID 独立注入职责提示，避免旧版按模型名称覆盖角色说明的问题。

---

## 3. 架构与测试覆盖

- `backend/model_connections.py`：连接存储、端点校验、密钥擦除、模型发现/测试接口、受限 Worker Bridge Token 调度。
- `backend/groups.py`：`RoomTeamRequest` 团队更新接口、`team_revision` 乐观锁、群成员派发角色隔离。
- `frontend/src/model-connection-api.js` & `model-connection-state.js`：连接 API 客户端、表单校验、端点安全规则与状态判定。
- `frontend/src/components/AgentSettings.jsx`：连接配置表单、发现/测试弹窗、Agent 团队配置。
- `backend/test_model_connections.py`：端点防护、CSRF 限制、密钥擦除、模型测试及 Worker 授权撤销回归测试。

---

## 4. 2.6.0 验收结果

- **自动测试**：
  - 后端测试通过：`208` 项
  - 前端测试通过：`101` 项
  - 桌面测试通过：`18` 项
  - ESLint 代码风格检查通过
- **桌面构建与安装**：
  - 前端生产构建通过（`vite build`）
  - Electron 桌面包打包为 2.6.0 并通过 macOS Ad-hoc 严格签名校验（`codesign --verify --deep --strict`）
  - `/Applications/ModelDesk.app` 已安装就绪，旧版备份至 `llm-tools/releases/backups/ModelDesk-2.5.0-*.app`
  - 界面实测截图存档于 `data/validation-images/modeldesk-2.6-connection-test.png`
