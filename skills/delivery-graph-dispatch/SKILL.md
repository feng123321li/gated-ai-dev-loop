---
name: delivery-graph-dispatch
description: "协调已冻结或已启动的 Delivery Graph：启动 MANUAL handoff、读取 frontier、原子预留 READY TASK/Review、创建独立 receiver、监控 lease/进度、处理等待、暂停、失联和安全重建。用于状态为 HANDOFF_READY、ACTIVE、BLOCKED、PAUSED、QUEUED，或 receiverPrompt 明确要求总协调时；不用于需求规划、TASK 实现或 Review 判断。"
allowed-tools:
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__workspace_status
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__resume_execution_mode
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__start_manual_handoff
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__plan_dispatch_batch
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__graph_frontier
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__graph_status
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__open_delivery_dashboard
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__graph_events
  - mcp__plugin_delivery-graph_delivery-graph-dispatch__advance_graph
---

# Delivery Graph 派遣与恢复

把本 Skill 作为 primary coordinator。只路由、派遣、等待和恢复；任何 TASK 或 Review 都必须交给独立 receiver，绝不在本上下文 claim、实现或审查。

## 固定边界

- 只调用 frontmatter 中列出的 dispatch Profile 工具。不得调用 planning 或 receiver Profile 的工具。
- 始终保留并传递明确 `rootId`；SQLite、事件链和 frontier 是机器权威，不读写 `scheduler.db`。
- `CURRENT_WORKSPACE_SERIAL` 同时约束自动和手动 Run；同一物理 checkout 一次只推进一个 Delivery turn。
- `PAUSED`、`COMPLETED`、`CANCELLED` 只是释放资格边界，不等于已释放。固定顺序为 `quiesce receiver/reservation → 到达 eligible state → 在各自冻结独立分支完成业务 commit 且 clean → 再次调用协议并持久化 WORKSPACE_TURN_RELEASED → 才可切分支或推进下一 Delivery`。响应为 `workspaceRelease=PENDING` 时只能执行其 `nextAction`，不得准备或切换下一分支。
- AUTOMATIC 的每个 READY TASK/Review 先由 `plan_dispatch_batch` 原子 reservation，再创建不同的宿主原生 receiver。primary 不得 claim 或把 assignment 交给普通 helper。
- assignment 的 `receiverPrompt` 必须原样传递：TASK 会路由到 `$delivery-graph-task`，所有 Review 会路由到 `$delivery-graph-review`。
- 只有外层 receiver 持有 reservation、decision fingerprint、receiver context 和 `operation_id`。内部 Worker 不接触控制面凭据。
- primary 不持有或借用 receiver operation，绝不代发 heartbeat；每个 receiver 自己在 claim 后立即 heartbeat，并持续到 result/claim release。`NOT_REQUIRED` 不取消其约 60 秒计划，progress 不续租。
- 完整传递 assignment 的 `receiverPrompt`；receiver 在预计超过 60 秒的整文件 Write、大 patch、批量编辑或命令前自行申请覆盖租约，可拆编辑改为语义小 patch 并在块间 heartbeat；primary 只监控，不代执行或代续租。
- 派遣和等待只依据 assignment `reasons`、reservation/lease、节点 `resultProvenance`、progress/heartbeat 与事件链；把这些结构化原因展示给用户，不用隐藏推断解释“为什么在等”。
- Graph 不授权 commit、merge、push、发布、迁移或新增权限；这些动作仍分别取得授权。

## 启动与 frontier

1. 调用 `workspace_status(rootId=...)`。先处理 `workspaceRelease`：`PENDING` 时只完成返回的 quiesce/commit/clean/恢复冻结分支/recheck 动作；只有 `RELEASED` 才允许宿主切换分支。`HANDOFF_READY` 时，在实际开发 workspace、任何代码检查前调用 `start_manual_handoff`；Git 漂移返回 baseline 交互时转回 `$delivery-graph`。
2. 首次进入、receiver 事件、`nextWakeAt` 到达或 `ADVANCE_REQUIRED` 时调用一次 `graph_frontier`。
3. 完整消费当前批次所有立即 action；不得只处理第一项，也不得在 reservation 后继续分析 assignment。
4. 对 READY 自动节点调用一次 `plan_dispatch_batch`，完整创建所有 `assignments` 的独立 receiver，并原样传递 `receiverPrompt`。
5. 对 `CLAIM_MANUAL_TASK` 创建独立人工 TASK receiver。MANUAL 只允许 TASK；Review 仍走 AUTOMATIC reservation。
6. 全部派遣后严格执行 `postActionWait` 或 `progressMonitor.waitDirective`。优先等待宿主原生 receiver 事件；到 deadline 才读取一次 frontier/status，禁止 back-to-back 轮询。

## Action 路由

| action | 处理 |
|---|---|
| `DISPATCH_LOOP` | 调用 `plan_dispatch_batch`，为每个 assignment 创建独立 receiver |
| `CLAIM_MANUAL_TASK` | 原样传递 action 的 `receiverPrompt`，创建 `$delivery-graph-task` child |
| `CONTINUE_OR_HEARTBEAT_LOOP` | 不代替 receiver；按 wait directive 等待 |
| `RESUME_LOOP_IN_INDEPENDENT_CONTEXT` | 新建独立 receiver，并让它使用原 node 调用 `resume_loop`；若暂停时已释放 turn，该调用先重新排队，返回 `QUEUED` 时保持节点 `PAUSED`，轮到且冻结分支 clean 后再次调用才重获 turn 并恢复为 READY |
| `ADVANCE_REQUIRED` | 调用一次 `advance_graph`，再刷新一次 frontier |
| `REFREEZE_TASK_REQUIREMENT` | 停止派遣，转回 `$delivery-graph` 取得用户授权并准备 Revision |
| `RECORD_USER_CONFIRMATION` | 转回 `$delivery-graph` 展示验收并等待真实用户确认 |

## 失败与恢复

- AUTO receiver 启动或 claim 失败时不得由 primary 直接领取。reservation 有效时仅按原参数重试；过期后重新规划批次。
- 人工接管自动 TASK 需要确认从未 claim、无有效 reservation、workspace 干净且无代码改动，并再次取得用户明确授权；Review 不能降级为人工 claim。
- `dispatch_loop` 响应未知时，只允许 receiver 用原 reservation、fingerprint、context 和 operation 幂等重试；primary 不伪造 operation。
- `SCHEDULER_DISPATCH_DECISION_MISMATCH` 返回 `retryWithSameReservation=true` 时，receiver 用同一 reservation 与 `expectedDecisionFingerprint` 重试；否则丢弃整组旧凭据并重新规划，禁止跨轮混搭。
- 租约过期、receiver 失联或基础设施失败时刷新 frontier，再按 action 调用 `advance_graph`；不得复用旧 operation。
- 暂停释放后恢复必须经过 `WORKSPACE_TURN_REQUEUED → 队首/冻结 binding/clean 复核 → WORKSPACE_TURN_REACQUIRED`；重获 turn 前不得派遣、复用旧 operation、跳过 reservation/resource/fingerprint 门禁或自行把节点改为 READY。
- 物化状态损坏时，受保护的 `rebuild_graph_run` 只能在明确恢复动作下从已校验事件链重建，不修改事件。
- 需求方向、拓扑、依赖、资源、项目 scope、Review 契约或 databaseChanges 变化属于 `REPLAN_REQUIRED`，转回 `$delivery-graph` 创建同一 Delivery 的下一 Revision。

详细协议见[派遣与恢复说明](references/dispatch-and-recovery.md)。
