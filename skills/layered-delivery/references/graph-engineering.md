# Graph Engineering 运行规则

## 权威来源

- 人工权威：经过评审的层级结构和根级 `development-plan.md`。
- 编译权威：控制器确定性编译并保存到 SQLite 的 Delivery Graph。
- 运行事实权威：绑定图指纹、前后哈希相连的图事件链。graph run 和 node run 只是可通过事件回放重建的查询快照。
- 人类可读投影：需求级 `execution-graph.md`、`frontier.md`、`run-timeline.md`，以及工作区共享的 `state-transition-graph.md`。不得从这些 Markdown 文件反向推断机器状态。

## 职责边界

- 前段由用户/需求宿主讨论需求、评审方案并明确确认冻结；
- 中段由 Graph 管理依赖、READY、目标 Agent 数、自动派发、工程门禁、失败分类、重试、回退和恢复；
- 末段由 Graph 提交已经过工程门禁与独立审查的结果，用户/需求宿主做最终验收确认。

执行适配器只把 Graph 动作映射到实际 Agent、进程或队列。它可以报告容量，但不能挑选任务子集、改写顺序、跳过动作或自行决定失败路线。

用户不直接定义任意图节点或边。`prepare-hierarchy` 将通过校验的层级结构编译成一张图，并提供两种有类型的视图：

- 执行图：包含 Task 执行、Task/Capability/Delivery 门禁、依赖、成功边和汇聚关系。
- 治理图：包含各级门禁、根级审查、用户确认和治理迁移。

## 契约 DAG 与运行时 FSM

Graph Engineering 不等于只有 DAG。控制器将以下职责分开处理：

- 契约 DAG：保存冻结节点、依赖边、并行分支、fan-in 汇聚、门禁、审查和确认。它必须保持无环，确保就绪状态与关键路径可以确定性计算。
- 运行时 FSM：保存节点状态和有类型的状态迁移。它允许重试、同合同修正、暂停/恢复和执行者失联恢复形成受控回路，但不修改冻结的依赖拓扑。
- 路由策略（Router Policy）：根据失败分类、尝试预算、路由条件和下一动作决定运行方向。该策略由控制器维护，并纳入图指纹。

节点是工作单元；Agent 是已认领 Task 节点的当前执行者。不要把每个 Agent 都建模成永久图节点。

## 冻结图合同

准备阶段同时返回 `hierarchyFingerprint` 和 `graphFingerprint`。一次 `freeze-hierarchy` 确认会冻结完整层级、开发方式和编译后的图。

冻结后必须遵守：

- 不增加或删除图节点；
- 不改写依赖边或汇聚边；
- 不跳过门禁、审查或确认节点；
- Graph 运行时自动计算目标 Agent 数、并行组和派发顺序；执行适配器只分配 owner/operationId 并消费完整计划；
- 重试和同合同修正只能创建新 attempt，不能创建新的图定义。

## 图前沿

使用：

```text
python -X utf8 <skill-root>/scripts/hdg.py graph-frontier --item <root-or-subtree-id> --json
```

返回值中的 `dispatchPlan` 是自动 Agent 调度合同：

| 字段 | 含义 |
|---|---|
| `authority` | 固定为 `GRAPH_CONTROLLER`，表示调度选择由控制器作出 |
| `strategy` | `AUTO_DISPATCH_ALL_SAFE`，消费全部本轮安全 Task |
| `dispatchTaskIds` | Graph 确定的完整稳定顺序，不是供宿主挑选的候选列表 |
| `desiredNewAgentCount` | 本轮需要新启动的 Agent 目标数 |
| `activeAgentCount` | 当前子树中已认领 Task 的 Agent 数 |
| `desiredTotalAgentCount` | 当前 Graph 运行的 Agent 总目标数 |
| `hostSelectionAllowed` | 固定为 `false` |
| `capacityPolicy` | `QUEUE_REMAINDER_STABLE`；容量不足时保持原顺序排队 |
| `claimPolicy` | `JUST_IN_TIME_ON_WORKER_START`；只有 worker 真正取得容量时才创建 claim |
| `queuedTasksRemainUnclaimed` | 固定为 `true`；稳定队列中的 Task 不提前消耗租约 |
| `recalculateAfterEveryTransition` | 每次状态迁移后重新计算 |

可能返回的动作：

| 动作 | Graph 执行循环应执行的操作 |
|---|---|
| `DISPATCH_TASK` | Graph 执行循环按 `dispatchPlan` 顺序，使用唯一 owner 和 operationId 自动派发该 Task |
| `RUN_GATE` | 为当前工作项构建并提交门禁证据 |
| `REQUEST_REVIEW` | 执行隔离的独立审查，或取得被接受的人工审查结果 |
| `REQUEST_USER_CONFIRMATION` | 向用户提交最终结果并取得独立的最终确认 |
| `HEARTBEAT_TASK` | 在当前 operation 的租约到期前续租 |
| `RESUME_TASK` | 恢复被显式暂停的 Task attempt |

`ready-tasks` 只返回当前 `DISPATCH_TASK` 动作中的 `workItemId`，用于兼容性只读查看。执行适配器不得另行实现第二套就绪判断或从中人工挑选；实际执行以完整 `dispatchPlan` 为准。

`blocked` 解释节点当前不可执行的原因，包括前置节点未完成、文件范围冲突、只读隔离或需求尚未冻结。应解决已记录的条件后重新查询 frontier，不能绕过它自行选择路径。

frontier 还会返回 `criticalPath`，其中包括最长剩余路径、下一个汇聚点，以及路径是否被阻断或暂停。可执行动作会携带迁移、路由条件、尝试预算、租约和命令提示；`DISPATCH_TASK` 额外携带自动派发标记和顺序。阻断项会携带失败分类、剩余尝试次数、是否耗尽、最近迁移和建议动作。控制器将自动 Agent 计划、相同信息和调度流程图渲染到双语 `frontier.md` 看板。

## 认领、心跳与自动推进

每个 claim 都包含 `claimedAt`、`lastHeartbeatAt` 和 `leaseExpiresAt`。默认软租约为 30 分钟、心跳间隔为 5 分钟、竞争宽限为 2 分钟。`dispatch-task` 与 `heartbeat-task` 返回 `leasePolicy`，其中含 `heartbeatDueAt`、`leaseExpiresAt`、`hardExpiresAt` 和精确命令提示。

```text
python -X utf8 <skill-root>/scripts/hdg.py heartbeat-task --item <task-id> --operation <id> --json
python -X utf8 <skill-root>/scripts/hdg.py advance-graph --item <root-or-subtree-id> --json
```

frontier 在心跳尚未到期时把 claim 放入 `inFlight` 并返回最早 `nextWakeAt`；只有到期后才把 `HEARTBEAT_TASK` 放入 `actions`，并标记 `NORMAL`、`CRITICAL` 或 `OVERDUE`。执行适配器必须以 `nextWakeAt` 为最长等待时间主动唤醒，不能等待开发 Agent 自己想起续租。

`heartbeat-task` 可延长匹配且尚未硬过期的 operation。软租约到期后的 2 分钟内，如果 `advance-graph` 尚未完成失联迁移，同一 operation 仍可补心跳或提交结果；事务先到者生效。硬到期后 `advance-graph` 写入 `CLAIM_LEASE_EXPIRED`、归类为 `WORKER_LOST`，然后创建新 attempt 或写入 `RETRY_EXHAUSTED`。旧 operationId 在同一 graph run 中禁止复用，因此失联 worker 的迟到结果不能污染新 attempt。控制器不会用后台 daemon 假装 worker 存活，也不会猜测任意业务失败是否可以重试。

显式运行控制命令如下：

```text
python -X utf8 <skill-root>/scripts/hdg.py pause-task --item <task-id> --operation <id> --json
python -X utf8 <skill-root>/scripts/hdg.py resume-task --item <task-id> --json
python -X utf8 <skill-root>/scripts/hdg.py cancel-graph-run --item <root-id> --confirmed --json
```

暂停和恢复沿用同一个 attempt。取消是经过明确确认的 graph run 终止迁移，不属于失败重试。

## 状态与事件

使用：

```text
python -X utf8 <skill-root>/scripts/hdg.py graph-status --item <root-or-subtree-id> --json
python -X utf8 <skill-root>/scripts/hdg.py graph-events --item <root-or-subtree-id> --json
python -X utf8 <skill-root>/scripts/hdg.py graph-replay --item <root-or-subtree-id> --json
```

`graph-status` 返回图指纹、冻结的运行时策略、graph run、有类型的节点和边，以及节点当前状态、attempt、owner、operationId、claim 租约、最近迁移、失败分类、尝试耗尽状态和阻断原因。

`graph-events` 返回按顺序排列、绑定图指纹且带前序哈希的事件链。正常生命周期事件包括图启动、Task 认领/心跳/结果、租约过期、暂停/恢复、门禁结果、重试/耗尽、审查、最终确认、同合同修正失效传播和取消。

对于由 artifact 驱动的事件，控制器会保存一份 bound evidence，其中包含原始 artifact，以及对 `runId`、`nodeId`、`attempt`、`graphFingerprint` 和 artifact 哈希的绑定；绑定整体还会计算独立的规范 SHA-256。Graph 执行循环只提交原始 artifact，不得自行构造绑定字段，也不得将 bound artifact 复用到其他图坐标。

`graph-replay` 从 `GRAPH_RUN_STARTED` 开始应用完整事件流，重建每个 node attempt 和 graph 状态，计算 replay fingerprint，并报告事件回放结果与 graph/node run 快照之间的差异。出现差异时，正常状态和 frontier 查询必须阻断。

如果事件链和证据链校验通过，只有查询快照损坏，可以在明确确认后执行：

```text
python -X utf8 <skill-root>/scripts/hdg.py rebuild-graph-run --item <root-id> --confirmed --json
```

该命令从事件重建 graph/node run 快照，并记录恢复交互；它不会修改冻结图、事件或 evidence。

不得直接修改图表、registry 记录、attempt 或事件。

## 失败分类与重试

状态为 BLOCKED 的 `TASK_RESULT` 必须包含：

```json
{"failure":{"class":"RETRYABLE","code":"REGRESSION_FAILURE","summary":"说明本次 attempt 失败的原因。"}}
```

路由规则是确定性的：

| 失败分类 | 路由 |
|---|---|
| `RETRYABLE` | 在 3 次总尝试预算内自动创建下一 attempt |
| `WORKER_LOST` | 仅由控制器用于过期 claim，使用相同的自动重试预算 |
| `REMEDIATION_REQUIRED` | 提交同合同验证修正证据 |
| `CONTRACT_CHANGE` | 返回人工评审，不得静默重试 |
| `EXTERNAL_AUTHORITY` | 请求用户授予外部权限 |
| `NON_RETRYABLE` | 请求人工干预 |

当自动恢复类失败在第 3 次 attempt 仍未成功时，控制器写入 `RETRY_EXHAUSTED`，将节点标记为尝试耗尽，并阻断 graph run。`retry-item` 仍用于符合条件的门禁或人工恢复场景，但同样受当前节点的 3 次 attempt 预算约束。Task gate 重试会同时创建新的 execution 与 gate attempt，让修复重新经过认领、结果写回和门禁；协调节点 gate 只重试自身。普通的可重试 Task 失败由 Graph 自动路由，不再要求执行平台手动调用它。

新的 Task 派发不得复用当前 graph run 中任何历史 attempt 的 operationId，控制器机械拒绝复用。

## 同合同修正与失效传播

只有目标、需求、验收、接口、数据合同、测试命令、拓扑和外部权限均保持不变时，才允许执行 `remediate-task`。

控制器从 Task execution 节点开始沿出边计算受影响范围，使依赖修正结果且已经推进的下游节点失效，其中包括消费方和聚合门禁。控制器为失效且已推进的节点创建新 attempt，同时保持原图定义和 baseline 不变。

如果受影响的下游 Task 存在活动 claim，同合同修正必须阻断。应先结束或释放该 claim，再重新执行修正命令。

已经完成的需求不可原地修改；后续变化必须形成新需求。

## 双语图投影

所有架构图和生成的图使用 `中文 / English` 标签。图投影必须区分：

- `执行图 / Execution Graph`；
- `治理图 / Governance Graph`；
- 节点类型和工作项 ID；
- `成功 / Success`、`通过后 / Requires Pass`、`全部汇聚 / All Of` 等有类型的边。

生成文件都是只读投影，可以通过 `refresh-projections` 重建。

需求根 `execution-graph.md` 和工作区 `.layered-delivery/state-transition-graph.md` 默认嵌入控制器用 Python 标准库确定性生成的 SVG，因此不依赖 Markdown 查看器支持 Mermaid。Mermaid 源图、节点表和迁移表放入折叠区，继续承担兼容与审计用途。执行图与治理图 SVG 位于需求根 `assets/`；开发流程与节点 FSM SVG 位于工作区 `.layered-delivery/assets/`。它们都可由 `refresh-projections` 重建，并在冻结前参与投影防篡改校验。

工作区 `state-transition-graph.md` 由控制器当前 schema v3 的共享运行时策略生成，必须同时展示开发执行流程和节点 FSM，包括失败分类、重试耗尽、暂停/恢复和取消。由于有效 Delivery Graph 的 `runtime` 必须等于该共享策略，需求根不得重复保存这份投影。
