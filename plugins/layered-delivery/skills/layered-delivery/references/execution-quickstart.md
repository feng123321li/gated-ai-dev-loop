# 递归 Graph 执行

用于冻结图的运行、恢复与阻断处理。

## Frontier

调用 `graph_frontier` 并执行全部 action：

- `DISPATCH_LOOP`：读取 `loop_context`，有真实容量时 claim。
- `CONTINUE_OR_HEARTBEAT_LOOP`：继续当前 Loop，并在租约到期前 heartbeat。
- `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把暂停节点路由给新的接收上下文；接收方 resume 后重新读取 frontier 并 dispatch。
- `RECOMMEND_ALTERNATE_OR_WAIT`：当前 Loop 在开发中命中提供方限额。用 `excludedOwner` 刷新临时排除后的推荐；有独立候选则提前 resume 并自动派遣，没有则按 `resumeAt` / `nextWakeAt` 定时唤醒。到时 frontier 自动恢复同一 attempt。
- `WAIT_FOR_HOST_CAPACITY`：调度宿主自身的模型容量受限。限额窗口内不要调用推荐器；由模型外宿主定时器按 `resumeAt` / `nextWakeAt` 唤醒并重新消费 frontier。
- `RESOLVE_LOOP_BLOCK`：展示 Loop 返回的摘要和不透明 result，等待外部条件或人工决定。
- `REPLAN_HIERARCHY`：展示外层契约变化及原图无法继续的原因，等待用户决定。只有用户明确授权后才调用 `cancel_graph_run`；取消成功后使用新的 `delivery.id` prepare 替代图并重新评审、冻结。
- `RECORD_USER_CONFIRMATION`：Review Loop 已成功；读取 [acceptance.md](acceptance.md)，等待用户最终接受。

不要自行增加 TASK/Gate 节点，也不要根据 payload 内容改变 frontier 顺序。

需要展示当前节点的执行建议时，调用 `available_agents` 和 `recommend_executors`，按 `nodeId` 选择对应建议。处理限额转派时传入 `temporarily_unavailable_agent_ids=[excludedOwner]`。推荐工具不得据此启动外部 CLI、切换模型、改变 owner、提前 claim 或绕过宿主原生 Agent 容量；自动执行由总调度器在工具返回后使用宿主原生 Agent 完成。CC-Switch、配置或容量变化后可以重新调用，旧建议不作为缓存权威。

## 节点推进

- `TASK_LOOP` 是唯一实现执行节点。
- 每个 TASK Loop 成功后都必须经过自己的 `TASK_REVIEW_LOOP`；TASK Review 成功才是 TASK 终态。
- 一个 GROUP 的直接子节点终态全部成功后，调度器自动完成机器节点 `GROUP_JOIN`，在人类文档中称为“GROUP 完成点”，随后使该层 `GROUP_REVIEW_LOOP` Ready。
- 子 GROUP 只有在自己的 GROUP Review 成功后，才成为父 GROUP 可消费的终态。
- 根 TASK Review，或根 GROUP Review 成功后进入 `DELIVERY_REVIEW_LOOP`。
- Delivery Review 成功后才出现 `RECORD_USER_CONFIRMATION`。

GROUP 完成点不需要 dispatch，也不包含实现内容。不要绕过 TASK Review 或任一级 GROUP Review，也不要用 TASK Loop 成功代替 TASK 成功。

## 执行 Loop

1. 总调度上下文只读取 frontier 和路由 action，不直接执行 Loop。
2. 对 `DISPATCH_LOOP`，宿主支持原生 Agent 时自动创建一个新的独立接收上下文；只传 `rootId/nodeId`，不要复制规划上下文、payload 或旧 operation。
3. 接收方原生进入 layered-delivery，使用精确 `nodeId` 调用 `loop_context`。TASK、TASK Review 与 GROUP Review Loop 同时取得控制器生成的 `humanArtifacts.workItem` baseline/progress/acceptance 路径；TASK 与 TASK Review 继续取得 `humanArtifacts.taskBaseline` 便捷路径，接口型 TASK 的 workItem 还包含自己的 `interfaces`。机器输入仍以 MCP 响应为准。
4. 接收方创建全局唯一 `operation_id` 并调用 `dispatch_loop`。没有可用 Agent 容量时才把同样的 `rootId/nodeId` 作为人工交接，且在接收方存在前不要提前 claim。
5. 按 `loop.ref` 启动对应内部 TASK、TASK Review、GROUP Review 或 Delivery Review Loop，并把 `payload` 和共享 `skillHints` 原样交给该 Loop。
   - 开发型 TASK 可在宿主和仓库支持时使用独立 Git worktree，尤其适合并行 TASK 隔离未提交改动；这是 Loop 内部实现选择，不进入 hierarchy、Graph 或 baseline 契约。
   - 每个并行 TASK 使用独立 worktree 与分支，仍遵守 `resourceClaims`。MCP 协调根保持原工作区，不要在每个 worktree 启动独立 scheduler 或复制 `.layered-delivery`；合并、删除 worktree、提交、推送和发布仍按各自授权边界执行。
6. 内部 Loop 先识别当前任务与宿主可用 Skill，再优先原生触发适用的 Skill Hint；不要因为 hierarchy 提供了提示，就假定每条提示都适用于当前 Loop。
7. 让内部 Loop 自己选择其他必要 Skill。payload 是目标、明确约束和已知验收点的输入，不是完整实现规约；Loop 要结合真实代码、契约和数据链路推导当前 scope 的必要条件。冻结 Graph 不冻结内部实现计划。TASK Loop 自主管理实现、文件、测试、Gate 和修正；Review Loop 自主管理独立发现、修正协调、Gate 和复审。
8. 当前目标内可修复的实现缺陷、测试失败、数据完整性或边界问题都留在当前 Loop：调整内部计划，完成修正，再重新验证。Review 可以自行修正或使用宿主内部执行容量派遣修正上下文，但必须保留独立复核；不要把“Review 未通过”提交成 `BLOCKED`。
9. 长任务持续调用 `heartbeat_loop`。检测到上下文容量压力或高轮次 Hook 摩擦且工作仍可继续时，不提交失败结果；在租约有效期内调用普通 `pause_loop`。
10. 开发中命中提供方 Token 限额时，从真实错误读取未来 `resetAt`。只有某个执行 Agent 受限而总调度仍可运行时，在租约有效期内调用 `pause_loop(resume_at=resetAt, capacity_scope=EXECUTOR)`；若总调度宿主自身也无法继续模型调用，则宿主适配器在模型调用失败边界调用 `pause_loop(resume_at=resetAt, capacity_scope=HOST)` 并注册模型外定时器。两者都释放租约和资源占用、保留同一 attempt；不得猜测 `resetAt`。
11. 收到 `RECOMMEND_ALTERNATE_OR_WAIT` 后，调用 `recommend_executors` 临时排除 `excludedOwner`。存在不同且真实可用的 Agent 时，立即 `resume_loop`，再用宿主原生 Agent 自动派遣；全部不可用时使用宿主原生定时唤醒等待 `nextWakeAt`。收到 `WAIT_FOR_HOST_CAPACITY` 时不要调用推荐器，只等待模型外宿主定时器。到时重新读取 frontier；控制器会自动把节点恢复为 Ready。
12. 普通 `pause_loop` 返回固定 handoff 数据。优先自动派遣新的接收 Agent；没有容量时输出人工交接。接收方使用同一 `rootId/nodeId` 调用 `resume_loop`，重新读取 frontier 和 `loop_context`，再以新 owner/operation dispatch；不重新 prepare/freeze。
13. 只有真实业务终态才用 `record_loop_result` 提交标准结果。

不要合并以下恢复分支：

- 未 claim 且无 Agent 容量：人工交接，不调用 `dispatch_loop` 或 `pause_loop`。
- 已 claim、租约有效且上下文/Hook 压力升高：`pause_loop`，不提交 Loop outcome。
- 已 claim、租约有效且单个执行 Agent 命中提供方限额：使用真实 `resetAt` 和 `capacity_scope=EXECUTOR` 定时 pause；优先动态推荐其他 Agent，无候选才等待。不是 `BLOCKED` 或新的 retry attempt。
- 已 claim、租约有效且调度宿主自身命中提供方限额：宿主适配器使用真实 `resetAt` 和 `capacity_scope=HOST` 定时 pause，由模型外定时器唤醒；限额窗口内不推荐。控制器只在被调用时推进，不会自行启动进程。
- 租约已经过期：停止使用旧 operation，调用 `graph_frontier`/`advance_graph`，禁止 `pause_loop`。

`predecessors` 表示 Graph 直接前驱；`upstreamLoopResults` 提供所有传递上游 Loop 的不透明结果，供依赖 TASK 和各级 Review 消费。GROUP 完成点自身没有业务 result，不能用它的空 outcome 替代 TASK 或下层 Review 的结果。

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
- 已 claim 且租约有效时的执行 Agent Token 限额：使用 `EXECUTOR` 定时 pause、临时排除后的推荐和宿主定时唤醒；保持同一 attempt，不消耗基础设施自动重试预算。
- 调度宿主自身的 Token 限额：使用 `HOST` 定时 pause 和模型外宿主定时器；如果宿主没有模型外定时能力，只能在下一次外部或用户唤醒时恢复，不能宣称自动激活。
- 租约过期：由 `advance_graph` 记录失联并按预算恢复；不是 pause/handoff。

MCP 写响应未知时先读状态。operation ID 永不复用。

## 资源锁

已 claim Loop 占用其全部 `resourceClaims`。另一个 Ready Loop 只要存在相同键就不能 dispatch；无交集则可并行。相同 frontier 批次内也必须先保留已选择 Loop 的 claim，避免同时派发冲突资源。不要从路径、仓库层级或模块前缀推导额外冲突。

## 恢复

- 调用 `advance_graph` 处理租约和自动重试。
- frontier 返回 `nextWakeAt` 时，宿主必须安排一次到时唤醒并重新消费 frontier；控制器不会在没有宿主调用的情况下自行启动进程。
- 调用 `graph_events` 检查事件链。
- 物化 node 状态不可信时调用 `rebuild_graph_run`；它只从事件链重建快照，不改变 Loop 内容或事件历史。
- 恢复时继续遵守递归终态：TASK Review 或下层 GROUP Review 未成功时，不得手工推进父 GROUP 完成点/Review。
