# ModelDesk 2.29：目标驱动与自改进的项目群自动化闭环

ModelDesk 2.29 在 2.28 的项目群例程基础上，将「从最终目标出发 → 梳理步骤与隐性判断 → 边界案例预演 → 持续指标与反馈 → 人类把关的自改进」升级为一等能力，让项目群不仅能按时跑起来，还能越跑越准、不靠人工逐次盯守。

## 核心方法与能力升级

### 1. 目标优先与隐性判断显式化（`RoutineSpec`）
- **明确区分目标与过程**：例程结构扩展为 **预期成果（目标）**、**执行步骤（过程）**、**决策判断与求助边界（`judgment`）**、**验收标准（`checks`）**、**真实案例与反例（`examples`）**，并附带 6 项自动化就绪度指示。
- **跨来源关联编号（`correlation_field` / `correlation_key`）**：支持为事项指定唯一关联编号（如客户邮箱、订单号、需求编号）。
  - 同一关联编号处于运行中或标记为 **等待补充信息（`waiting_details`）** 时，自动拦截重复触发，避免重复建单或重复执行。
  - 显式续办（`resume_case`）时，自动把同编号的历史运行结果与反馈冻结进本次上下文，不从零开始。

### 2. AI 访谈设计助手与无副作用案例演练（`backend/routine_designer.py`）
- **先对齐方案、不急于执行（`POST /routines/design`, `mode="interview"`）**：支持从常见目标起点、大白话描述或浏览器语音口述开始。助手先判断用户是否把“步骤”误当成“目标”，用白话复述当前理解，再**一次只追问一个最关键的盲区**（信息冲突以谁为准、什么算真正完成、何时停下来问人、待补状态如何跟进、触发节奏等）。
- **对照目标优化流程（`mode="optimize"`）**：审视现有步骤、判断规则与验收标准，指出冗余环节或更可靠的达成路径。
- **上线前边界案例演练（`POST /routines/walkthrough`）**：在不调用任何本机 Agent、不产生外部副作用的前提下，用 2–3 个冲突/缺信息/历史易错场景推演当前配置的表现（直接完成 / 记录待补信息 / 停下来求助人工），并支持将发现的盲区一键采纳为判断规则与案例。

### 3. 更贴合实际节奏的触发器
- **固定间隔定时（`interval`）**：支持每 1–720 小时循环（如每 6 小时、每 2 天/隔天、每 3 天），按固定槽位去重，服务中断恢复后最多补跑最近一次，不会连发风暴。
- **看板状态列触发（`board_status`）**：当本群看板手动卡片新建或拖入指定列（如 `待审核`、`待办`、`受阻`）时自动触发，可选按标签过滤；自动同步的镜像卡片不会循环触发，手机触发的卡片仍无法激活 `allow_native=True` 的本机权限例程。

### 4. 运行指标、反馈与人类把关的自改进闭环
- **结构化运行指标（`telemetry`）**：在 SQLite 中精确记录每次运行的创建、派发（`dispatched_at`）与结束（`finished_at`）时间，自动汇总累计次数、成功率、平均排队时长、平均执行耗时、待补事项与负反馈计数。
- **运行反馈记录（`POST /routines/{id}/runs/{run_id}/feedback`）**：可在已完成或异常运行上直接标注「符合预期 / 需要改进 / 判断有偏差」，写明根因或外部评价，并可切换「等待补充信息 / 已补齐」。
- **自改进复盘建议与 Diff 审批（`POST /routines/{id}/reflect` + `/apply` / `/dismiss`）**：
  - 基于本例程的真实运行指标、交付审阅诊断与用户/外部反馈交叉分析，生成结构化改进建议卡片与字段级前后对比（Diff）。
  - 支持开启「异常后自动生成改进建议草稿（`auto_reflect`）」。
  - **严格安全边界**：AI 设计与复盘建议**绝不静默改写线上例程**，也**绝不提升 `allow_native` 权限**；必须由用户在界面核对 Diff 并以乐观锁 `expected_revision` 点击「采纳改进」后才生效。
- **版本历史与回滚（`GET /routines/{id}/revisions`）**：每次创建、手动更新与采纳改进均保存不可变快照与变更说明，随时可从编辑器载入历史版本恢复。

## 验证命令

```bash
.venv/bin/python scripts/lint_rules.py
.venv/bin/python -m unittest backend.test_routine_store backend.test_routines backend.test_todo_board backend.test_remote_access -v
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
PLAYWRIGHT_MODULE="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print((Path(playwright.__file__).parent / "driver/package/index.mjs").as_uri())')" node frontend/tests/browser/check-group-routines.mjs
PLAYWRIGHT_MODULE="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print((Path(playwright.__file__).parent / "driver/package/index.mjs").as_uri())')" node frontend/tests/browser/check-group-routine-loops.mjs
```
