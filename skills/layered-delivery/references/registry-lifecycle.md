# 分层工作项生命周期

## 阶段与状态

准备阶段：

- `WAITING_FOR_BASELINE_CONFIRMATION / PREPARED`
- `BASELINE_FROZEN / FROZEN`（Delivery / Capability / Task）

Task 执行状态：

- `FROZEN`：已随整树冻结并继承根级开发方式，等待依赖或可调度；
- `CLAIMED`：一个 operation 拥有执行权；
- `IMPLEMENTED`：开发返回，尚未通过 gate；
- `BLOCKED`：开发或 gate 阻断；
- `VERIFIED`：Task gate PASS。

对应的图节点 FSM 还显式包含 `PENDING / READY / CLAIMED / SUCCEEDED / BLOCKED / PAUSED / CANCELLED / COMPLETED`。工作项状态用于产品治理，图节点状态用于运行调度；两者由控制器事务同步，不允许 Agent 自行拼接。

有 Delivery 父级的 Capability，其 `dependsOn` 未全部 VERIFIED 时，后代 Task 即使自身 Task 依赖满足也不 READY。根 Capability 不声明 Capability 依赖，根 Task 不声明 Task 依赖。

Delivery/Capability 保持 `FROZEN`，直到 decomposition 为 SEALED、全部计划直接子级 VERIFIED 且自身 gate PASS，之后为 `VERIFIED`。根 Task 在自身 gate PASS 后 VERIFIED；根 Capability 在自己的聚合 gate PASS 后 VERIFIED。READY 是 Task 的派生谓词，不是这里的持久状态。

每个治理根另有 acceptance 状态：`NOT_READY → WAITING_FOR_INDEPENDENT_REVIEW → WAITING_FOR_USER_CONFIRMATION → COMPLETED`。工作项 `VERIFIED` 与最终交付 `COMPLETED` 不合并；非根子项不重复执行最终确认。

## 合法迁移

```text
整树 PREPARED --人工评审方案、选择方式并一次确认--> 全部 baseline FROZEN
Task FROZEN --dispatch_task 认领并生成 handoff--> CLAIMED
Task CLAIMED --写回结果--> IMPLEMENTED | BLOCKED
Task CLAIMED --heartbeat_task--> CLAIMED + 租约延长
Task CLAIMED --pause_task--> PAUSED + 释放 claim
Task PAUSED --resume_task--> READY | PENDING
Task CLAIMED --租约过期 + advance_graph--> BLOCKED(WORKER_LOST) --预算内--> 新 attempt READY
Task BLOCKED(RETRYABLE) --预算内自动路由--> 新 attempt READY
Task BLOCKED(RETRYABLE/WORKER_LOST) --第三次失败--> RETRY_EXHAUSTED
Task IMPLEMENTED --accept_item 通过--> VERIFIED + acceptance report
Task IMPLEMENTED --accept_item 未通过--> BLOCKED + acceptance report
Task/协调节点 BLOCKED --retry_item 校验当前指纹--> FROZEN
Task IMPLEMENTED/BLOCKED/VERIFIED --remediate_task 同契约补充文件--> 原 Task FROZEN + 图下游已推进节点失效并创建新 attempt
协调节点 FROZEN --全部直接子级 VERIFIED + 聚合门禁通过--> VERIFIED
治理根 VERIFIED --独立/人工审查通过--> WAITING_FOR_USER_CONFIRMATION
WAITING_FOR_USER_CONFIRMATION --用户确认--> COMPLETED
活动 Graph Run --用户确认取消--> CANCELLED
```

协调工作项没有 CLAIMED/IMPLEMENTED；它们通过 child 状态和自己的 gate 推进。

## 完成条件

- Task：实现证据存在、冻结测试执行、Task gate PASS，并生成该级验收报告；
- Capability：所有计划 Task VERIFIED，集成测试与 Capability gate PASS；
- Delivery：所有计划 Capability VERIFIED，顶层交付测试与 Delivery gate PASS；
- 治理根最终交付：独立语义验收 PASS 或明确接受人工验收结果，并且用户随后确认交付；适用于根 Task、根 Capability 和 Delivery，不为此虚构空父级。

不得根据百分比、对话陈述、子级数量相等或文件存在推断 PASS。

## 阻断与恢复

BLOCKED 必须记录事实、责任方和解除条件。依赖完成或环境恢复后重新计算 READY；不要直接跳过 gate。父链或冻结契约发生变化时保持阻断，重新准备、评审并冻结完整需求树。

Task 结果为 BLOCKED 时必须结构化分类。`RETRYABLE` 和控制器产生的 `WORKER_LOST` 在 3 次总尝试预算内自动创建下一 attempt；耗尽后保持阻断。`REMEDIATION_REQUIRED`、`CONTRACT_CHANGE`、`EXTERNAL_AUTHORITY`、`NON_RETRYABLE` 不自动重试，分别路由到修正、评审、授权或人工干预。`retry_item` 仍用于符合条件的门禁或显式人工恢复，但不再是普通 Task 可重试失败的必经步骤。所有重试都不修改需求、拓扑或 scope；冻结契约需要变化时保持阻断并重新进行完整需求规划。

若验证发现原验收项所需的精确文件在冻结方案中遗漏，但目标、需求、验收、接口行为、数据契约、拓扑和外部权限均不变，使用 `remediate_task` 把补充文件追加到原 Task 的有效授权。原 baseline 不改，Task 回到 FROZEN；已通过的 Capability、Delivery gate 和根级最终验收状态逐级失效，修正后必须重新运行。此路径不是新需求，不生成新根，也不再次选择开发方式。

## 后续工作

VERIFIED 但尚未完成最终验收的工作项，可以通过严格的同契约验证修正回到原 Task；修正事实追加审计，不改写原 baseline。已 `COMPLETED` 的需求不可原地改写。契约变化或需求已经完成时，以新的完整需求树进入人工评审，并保留原工作项和证据。
