# 递归 Graph 执行

用于冻结图的运行、恢复与阻断处理。

## Frontier

调用 `graph_frontier` 并执行全部 action：

- `DISPATCH_LOOP`：读取 `loop_context`，有真实容量时 claim。
- `CONTINUE_OR_HEARTBEAT_LOOP`：继续当前 Loop，并在租约到期前 heartbeat。
- `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把暂停节点路由给新的接收上下文；接收方 resume 后重新读取 frontier 并 dispatch。
- `RESOLVE_LOOP_BLOCK`：展示 Loop 返回的摘要和不透明 result，等待外部条件或人工决定。
- `REPLAN_HIERARCHY`：外层依赖、资源或拓扑需要变化；停止原图并创建新的人工评审版本。
- `RECORD_USER_CONFIRMATION`：Review Loop 已成功，等待用户最终接受。

不要自行增加 TASK/Gate 节点，也不要根据 payload 内容改变 frontier 顺序。

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
7. 让内部 Loop 自己选择其他必要 Skill。TASK Loop 自主管理实现、文件、测试、Gate 和修正；Review Loop 自主管理检查项、隔离方式、Gate 和复审。
8. 长任务持续调用 `heartbeat_loop`。检测到上下文容量压力或高轮次 Hook 摩擦且工作仍可继续时，不提交失败结果；在租约有效期内调用 `pause_loop`。
9. `pause_loop` 返回固定 handoff 数据。优先自动派遣新的接收 Agent；没有容量时输出人工交接。接收方使用同一 `rootId/nodeId` 调用 `resume_loop`，重新读取 frontier 和 `loop_context`，再以新 owner/operation dispatch；不重新 prepare/freeze。
10. 只有真实业务终态才用 `record_loop_result` 提交标准结果。

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
- 普通 `BLOCKED`：不自动重跑，避免把业务错误伪装成瞬时故障。
- `REPLAN_REQUIRED`：冻结图的调度契约已不适用，回到人工规划。
- `CANCELLED`：结束当前 Loop，不自动重试。
- 上下文容量不足、宿主 Agent 暂时不可用或 Hook 导致的高轮次消耗：使用 pause/handoff，不是 `BLOCKED`、`WORKER_LOST` 或 `REPLAN_REQUIRED`。

MCP 写响应未知时先读状态。operation ID 永不复用。

## 资源锁

已 claim Loop 占用其全部 `resourceClaims`。另一个 Ready Loop 只要存在相同键就不能 dispatch；无交集则可并行。相同 frontier 批次内也必须先保留已选择 Loop 的 claim，避免同时派发冲突资源。不要从路径、仓库层级或模块前缀推导额外冲突。

## 恢复

- 调用 `advance_graph` 处理租约和自动重试。
- 调用 `graph_events` 检查事件链。
- 物化 node 状态不可信时调用 `rebuild_graph_run`；它只从事件链重建快照，不改变 Loop 内容或事件历史。
- 恢复时继续遵守递归终态：下层 GROUP Review 未成功时，不得手工推进父 GROUP Join/Review。
