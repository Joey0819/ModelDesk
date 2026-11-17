# 飞书内自助开通（Feishu Self-Service Bootstrap）

这份文档描述如何在不打开本机 ModelDesk UI 的情况下，直接在飞书私聊里完成「项目 → 团队 → 项目群 → 绑定」的全套开通。它解决的是冷启动死锁：绑定必须先有项目群，而项目群以前只能在本机 UI 创建。

## 三步结构

一次成功的自助开通按固定顺序创建四样东西：

1. **项目**（`POST /api/workbench/projects` 同源路径）：托管项目（无自定义 path，目录为 `workspaces/<uuid>`，`managed=true`）。
2. **团队**（`POST /api/harness/teams` 同源路径）：默认花名册取第一条**启用**模型连接的前两个文字模型，成员名「统筹」「执行」；也可用 `with 模型1,模型2` 自定义（1–4 个模型 id）。
3. **项目群**（`POST /api/groups` 同源路径）：`title = 项目名`，挂接 project_id 与 team_id，并快照当时的团队成员。
4. **绑定**：当前私聊的 `chat_id` 写入飞书配置的 bindings（`allow_native=false`、`natural_language_tasks=true`、发起人白名单 = 开通者本人）。

任一步失败会回滚已创建的项目与团队（团队归档、项目注销），不会留下半套资源。

## 两条命令

```
/modeldesk setup 项目名
/modeldesk setup 项目名 with 模型1,模型2
/modeldesk confirm 1234
/modeldesk unlink
```

- `setup` 只生成「将要创建什么」的计划和 4 位确认码，**不写任何东西**。
- `confirm` 在 5 分钟内有效、一次性使用、每会话同时只有一个待确认计划；执行后回传项目 ID、团队、项目群 ID 与可用命令示例。
- 同一条飞书消息重投（相同 message_id）不会创建两套（沿用 uuid5 消息键）。
- 频率限制：每会话每小时最多 3 次 `setup`。
- `unlink` 只删除当前会话的绑定，项目、团队、项目群全部保留。

## 权限模型（为什么这样设计）

自助开通是一扇从聊天软件直通本机写库的门，所以它的授权收敛到三个条件的**交集**：

1. **必须私聊（`chat_type == "p2p"`）**：群聊永不自助开通。否则任何人把机器人拉进一个群、发一句 setup 就完成了自我授权。
2. **发起人必须是配置里的 owner**：`owner_sender_ids`（根级配置，最多 10 个 `ou_` Open ID，去重）。这是唯一授权来源，**不是** per-binding 字段——授权先于绑定存在，这正是它要创建的东西。
3. **文本必须精确匹配 `^[/／]modeldesk\s+(setup|confirm)\b`**：其余任何未绑定消息保持现状——只写路由诊断（observed），正文不落库、不进日志。

再加上**两步确认**：计划本身不建任何东西，4 位确认码 5 分钟内一次性使用，防止误触和重放。计划数据（`receipts.sqlite3` 的 `plans` 表）只存 app_id、chat_id、sender_id、code、payload（项目名+模型 id）、时间戳，0600 权限，绝不含 app_secret。

`owner_sender_ids` 为空 = 自助开通完全关闭，行为与没有此功能时一致。

## 误绑自救

- `/modeldesk unlink`（owner 可用）解除当前会话绑定；项目/团队/群不动。
- `DELETE /api/groups/{identifier}`：仅当该房间**没有任何历史任务**（workbench job、群 Agent 任务、Muse 任务）时允许硬删，否则返回 409「该群已有历史任务，不能删除」。此端点要求本机管理（`require_local` + 拒绝 `X-ModelDesk-Remote`），与飞书配置面同级安全。

## 前端配置

飞书设置页新增「自助开通管理员」输入区（每行一个 `ou_` Open ID，去重，最多 10 个），文案说明：只有这些人的私聊可以用 `/modeldesk setup` 创建项目。

## 安全不变量

- app_secret 不出现在任何新代码路径、日志、回执或返回体中。
- 未绑定消息的正文绝不落库、绝不进日志（owner 的 setup/confirm 命令文本除外——那是命令本身，不是聊天正文）。
- 飞书 worker 仍只走 SDK 长连接，不开放入站 HTTP 端口；stdout 仍是固定 JSON `emit(state)` 协议。
- 所有新端点复用现有 `local_admin` / `require_local` 校验模式。
