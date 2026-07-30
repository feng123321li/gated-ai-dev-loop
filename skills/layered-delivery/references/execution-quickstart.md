# 递归 Graph 执行

用于冻结图的运行、恢复与阻断处理。

## Frontier

调用 `graph_frontier` 并执行全部 action：

- `DISPATCH_LOOP`：读取 `loop_context`，有真实容量时 claim。
- `CONTINUE_OR_HEARTBEAT_LOOP`：继续当前 Loop，并在租约到期前 heartbeat。
- `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把暂停节点路由给新的接收上下文；接收方 resume 后重新读取 frontier 并 dispatch。
- `RESOLVE_LOOP_BLOCK`：展示 Loop 返回的摘要和不透明 result，等待外部条件或人工决定。
- `REPLAN_HIERARCHY`：展示外层契约变化及原图无法继续的原因，等待用户决定。只有用户明确授权后才调用 `cancel_graph_run`；取消成功后使用新的 `delivery.id` prepare 替代图并重新评审、冻结。
- `RECORD_USER_CONFIRMATION`：Review Loop 已成功；读取 [acceptance.md](acceptance.md)，等待用户最终接受。

不要自行增加 TASK/Gate 节点，也不要根据 payload 内容改变 frontier 顺序。

需要展示当前节点的执行建议时，调用 `available_agents` 和 `recommend_executors`，按 `nodeId` 选择对应建议。建议只用于解释本机当前可用组合；不得据此启动外部 CLI、切换模型、改变 owner、提前 claim 或绕过宿主原生 Agent 容量。CC-Switch 或配置变化后可以重新调用以刷新结果，旧建议不作为缓存权威。

## 节点推进

- `TASK_LOOP` 是唯一实现执行节点。
- 一个 GROUP 的直接子节点终态全部成功后，调度器自动完成 `GROUP_JOIN`，再使该 GROUP 的 `GROUP_REVIEW_LOOP` Ready。
- 子 GROUP 只有在自身 Review 成功后才成为父 GROUP 可消费的终态，因此 Review 沿递归层级向上收敛。
- 根 TASK Loop 或根 GROUP Review 成功后进入 `DELIVERY_REVIEW_LOOP`。
- Delivery Review 成功后才出现 `RECORD_USER_CONFIRMATION`。

Join 不需要 dispatch，也不包含实现内容。不要绕过某一级 GROUP Review，或用子 TASK 成功代替 GROUP 成功。

## 执行 Loop

1. 总调度上下文只读取 frontier 和路由 action，不直接执行 Loop。
2. 对 `DISPATCH_LOOP`，宿主支持原生 Agent 时自动创建一个新的独立接收上下文；只传 `rootId/nodeId`，不要复制规划上下文、payload 或旧 operation。
3. 接收方原生进入 layered-delivery，使用精确 `nodeId` 调用 `loop_context`。TASK Loop 同时取得控制器生成的 `humanArtifacts.taskBaseline` 路径；机器输入仍以 MCP 响应为准。
4. 接收方创建全局唯一 `operation_id` 并调用 `dispatch_loop`。没有可用 Agent 容量时才把同样的 `rootId/nodeId` 作为人工交接，且在接收方存在前不要提前 claim。
5. 按 `loop.ref` 启动对应内部 TASK、GROUP Review 或 Delivery Review Loop，并把 `payload` 和共享 `skillHints` 原样交给该 Loop。
6. 内部 Loop 先识别当前任务与宿主可用 Skill，再优先原生触发适用的 Skill Hint；不要因为 hierarchy 提供了提示，就假定每条提示都适用于当前 Loop。
7. 让内部 Loop 自己选择其他必要 Skill。payload 是目标、明确约束和已知验收点的输入，不是完整实现规约；Loop 要结合真实代码、契约和数据链路推导当前 scope 的必要条件。冻结 Graph 不冻结内部实现计划。TASK Loop 自主管理实现、文件、测试、Gate 和修正；Review Loop 自主管理独立发现、修正协调、Gate 和复审。
8. 当前目标内可修复的实现缺陷、测试失败、数据完整性或边界问题都留在当前 Loop：调整内部计划，完成修正，再重新验证。Review 可以自行修正或使用宿主内部执行容量派遣修正上下文，但必须保留独立复核；不要把“Review 未通过”提交成 `BLOCKED`。
9. 长任务持续调用 `heartbeat_loop`。检测到上下文容量压力或高轮次 Hook 摩擦且工作仍可继续时，不提交失败结果；在租约有效期内调用 `pause_loop`。
10. `pause_loop` 返回固定 handoff 数据。优先自动派遣新的接收 Agent；没有容量时输出人工交接。接收方使用同一 `rootId/nodeId` 调用 `resume_loop`，重新读取 frontier 和 `loop_context`，再以新 owner/operation dispatch；不重新 prepare/freeze。
11. 只有真实业务终态才用 `record_loop_result` 提交标准结果。

不要合并以下恢复分支：

- 未 claim 且无 Agent 容量：人工交接，不调用 `dispatch_loop` 或 `pause_loop`。
- 已 claim、租约有效且上下文/Hook 压力升高：`pause_loop`，不提交 Loop outcome。
- 租约已经过期：停止使用旧 operation，调用 `graph_frontier`/`advance_graph`，禁止 `pause_loop`。

`predecessors` 表示 Graph 直接前驱；`upstreamLoopResults` 提供所有传递上游 Loop 的不透明结果，供依赖 TASK 和各级 Review 消费。Join 自身没有业务 result，不能用 Join 的空 outcome 替代 TASK 或下层 Review 的结果。

claim 超过 `leaseExpiresAt` 后，旧 operation 不能 heartbeat、pause 或提交结果。先让 `graph_frontier`/`advance_graph` 回收失联 attempt，再使用新 operation 继续。

结果对象：

```json
{
  "status": "SUCCEEDED",
  "summary": "内部开发、测试和 Gate 已完成",
  "result": {
    "evidence": "由该 Loop 自己定义"
  }
}
```

`result` 对外层调度器不透明。Loop 可在 result 中报告实际使用或跳过的 Skill，供 Review 消费；不要要求 layered-delivery 校验这些字段。

## 失败和重试

- `BLOCKED + RETRYABLE_INFRA` 与租约丢失 `WORKER_LOST`：调度器在预算内创建新 attempt。
- 普通 `BLOCKED`：必须显式提供 failure class，且只表示当前 scope 和权限内没有继续路径；不自动重跑。可修复 finding 或内部 Gate 失败不是 `BLOCKED`，必须在提交终态前由当前 Loop 继续修正和复验。
- `REPLAN_REQUIRED`：冻结图的调度契约已不适用。记录结果后等待 `REPLAN_HIERARCHY`；不要自动取消当前 run，也不要复用其已冻结的 `delivery.id`。用户明确授权取消后，才创建新的替代图。
- `CANCELLED`：结束当前 Loop，不自动重试。
- 未 claim 且宿主 Agent 暂时不可用：人工交接，不提前 claim。
- 已 claim 且租约有效时的上下文容量不足或 Hook 高轮次消耗：使用 pause/handoff，不是 `BLOCKED`、`WORKER_LOST` 或 `REPLAN_REQUIRED`。
- 租约过期：由 `advance_graph` 记录失联并按预算恢复；不是 pause/handoff。

MCP 写响应未知时先读状态。operation ID 永不复用。

## 资源锁

已 claim Loop 占用其全部 `resourceClaims`。另一个 Ready Loop 只要存在相同键就不能 dispatch；无交集则可并行。相同 frontier 批次内也必须先保留已选择 Loop 的 claim，避免同时派发冲突资源。不要从路径、仓库层级或模块前缀推导额外冲突。

## 恢复

- 调用 `advance_graph` 处理租约和自动重试。
- 调用 `graph_events` 检查事件链。
- 物化 node 状态不可信时调用 `rebuild_graph_run`；它只从事件链重建快照，不改变 Loop 内容或事件历史。
- 恢复时继续遵守递归终态：下层 GROUP Review 未成功时，不得手工推进父 GROUP Join/Review。
