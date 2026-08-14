# 派遣与恢复说明

## 目录

1. [协调器状态机](#协调器状态机)
2. [自动派遣](#自动派遣)
3. [手动派遣](#手动派遣)
4. [等待与监控](#等待与监控)
5. [恢复决策](#恢复决策)

## 协调器状态机

协调器始终围绕明确 `rootId` 工作。首次接手先调用 `workspace_status(rootId=...)`：

- `HANDOFF_READY`：在实际开发 workspace 调用 `start_manual_handoff`；在此之前不检查或修改代码。
- `QUEUED`：执行响应中的串行释放检查；只有前一个 Delivery 已到安全释放边界，才机械完成授权的 branch 准备并调用 `resume_execution_mode`。
- `ACTIVE/BLOCKED/PAUSED`：调用一次 `graph_frontier`，完整消费 action。
- `CHOICE_READY/PREPARED`：转交 `$delivery-graph`，不要替用户确认 baseline 或执行模式。
- `COMPLETED` 且待最终确认：转交 `$delivery-graph`；协调器不代签。

每次写响应未知时，先通过只读状态确认是否已经持久化，再决定是否用完全相同的幂等键重试。不得凭超时直接重放新的 reservation 或 operation。

## 自动派遣

1. `graph_frontier` 返回一个或多个 `DISPATCH_LOOP` 时，调用一次 `plan_dispatch_batch`。
2. 原子 reservation 会返回完整 `assignments`。立即为每项创建新的宿主原生独立 receiver；不得遗漏并发组或在 primary 中逐项实现。
3. 原样传递 `receiverPrompt`、`contextInput`、reservation、decision fingerprint 和节点身份。receiver 自己创建新的 `operation_id`。
4. TASK assignment 触发 `$delivery-graph-task`；Review assignment 触发 `$delivery-graph-review`。
5. 全部 receiver 创建后执行 `postActionWait`。同一 reservation 的响应丢失重试必须返回原 assignment，不能重复领取。

第二个 dispatcher 只能看到 `WAIT_FOR_DISPATCH_RECEIVER`，不得抢占。receiver 创建失败时，reservation 未过期则用原 assignment 重试；过期后刷新 frontier 并重新规划。

## 手动派遣

MANUAL 只改变 TASK 的 claim 方式，不降低 Review 治理：

- `CLAIM_MANUAL_TASK`：创建独立 TASK child，原样传递 `receiverPrompt`。child 用 `dispatch_loop(MANUAL)`，没有 AUTO reservation，但必须提交可信 Adapter、独立 receiver context 和新 operation。
- TASK 完成后，TASK Review、可选 GROUP seam Review、Delivery Acceptance/Readiness 都继续使用自动 reservation 与独立 receiver。
- 自动 TASK 若要改为人工接收，必须先确认从未 claim、无有效 reservation、workspace 干净且无代码改动，并取得用户明确授权调用受保护工具；Review 永不降级。

## 等待与监控

同时存在 receiver 或 reservation 时，禁止固定周期忙轮询：

- 优先等待宿主的 receiver completed/needs-attention/start-failed 事件。
- receiver 事件发生后调用一次 `graph_frontier`。
- 无事件时，直到 `postActionWait.deadline` 或 `progressMonitor.waitDirective.pollNotBefore` 才读取一次。
- `nextWakeAt` 或 `ADVANCE_REQUIRED` 到达后调用一次 frontier/advance。
- 不 back-to-back 调用 `graph_frontier` 和 `graph_status`。
- 只有 `progressMonitor.changeFingerprint` 改变或出现新告警时，才向用户更新表格。

heartbeat 是 receiver 的 lease 操作，不是 primary 的轮询信号；宿主 completion notification 也不能代替 heartbeat。

## 恢复决策

| 情况 | 决策 |
|---|---|
| receiver 正常运行 | 等待事件或 deadline |
| receiver 失联/lease 过期 | 刷新 frontier，按 action 调用 `advance_graph`，不复用旧 operation |
| `PAUSED` | 新建独立接收上下文，由 receiver 调用 `resume_loop` |
| HOST/EXECUTOR 容量耗尽 | 仅按结构化 `resetAt` 等待并恢复，不猜文本、不换模型 |
| workspace/fingerprint/operation 错误 | receiver 立即停止仓库操作，把稳定错误码交回 |
| 物化 run 损坏 | 明确授权后用 `rebuild_graph_run` 从事件链重建 |
| TASK requirement 可局部修订 | 转 planning，完成切分预检和用户授权后 unfreeze/refreeze |
| 拓扑/依赖/资源/scope/Review/数据库契约变化 | 转 planning，创建同一 Delivery 下一 Revision |
| 最终验收已到 | 转 planning 展示并等待真实用户确认 |

取消 Graph、恢复物化状态、人工接管自动 TASK 都是受保护动作；不要因“恢复更方便”自行调用。
