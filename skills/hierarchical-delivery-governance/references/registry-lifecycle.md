# 分层工作项生命周期

## 阶段与状态

准备阶段：

- `WAITING_FOR_BASELINE_CONFIRMATION / PREPARED`
- `BASELINE_FROZEN / FROZEN`（Delivery / Capability）
- `BASELINE_FROZEN / WAITING_FOR_DEVELOPMENT_MODE_SELECTION`（刚冻结或修订后的 Task）

Task 执行状态：

- `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`：等待用户明确选择 active/manual；
- `FROZEN`：开发方式已持久化，等待依赖或可调度；
- `CLAIMED`：一个 operation 拥有执行权；
- `IMPLEMENTED`：开发返回，尚未通过 gate；
- `BLOCKED`：开发或 gate 阻断；
- `VERIFIED`：Task gate PASS。

Capability `dependsOn` 未全部 VERIFIED 时，其后代 Task 即使自身 Task 依赖满足也不 READY。

Delivery/Capability 保持 `FROZEN`，直到 decomposition 为 SEALED、全部计划直接子级 VERIFIED 且自身 gate PASS，之后为 `VERIFIED`。READY 是 Task 的派生谓词，不是这里的持久状态。

Delivery 另有 delivery 状态：`NOT_READY → WAITING_FOR_INDEPENDENT_REVIEW → WAITING_FOR_USER_CONFIRMATION → COMPLETED`。工作项 `VERIFIED` 与最终交付 `COMPLETED` 不合并。

## 合法迁移

```text
DELIVERY/CAPABILITY PREPARED --confirm--> FROZEN
TASK PREPARED --confirm--> WAITING_FOR_DEVELOPMENT_MODE_SELECTION
WAITING_FOR_DEVELOPMENT_MODE_SELECTION --explicit mode confirmation--> FROZEN
FROZEN --claim--> CLAIMED
CLAIMED --result--> IMPLEMENTED | BLOCKED
IMPLEMENTED --gate PASS--> VERIFIED
IMPLEMENTED --gate FAIL--> BLOCKED
BLOCKED --retry-item(current fingerprint + confirmation)--> FROZEN
DELIVERY/CAPABILITY FROZEN/BLOCKED --confirmed baseline revision--> FROZEN
TASK FROZEN/BLOCKED --confirmed baseline revision--> WAITING_FOR_DEVELOPMENT_MODE_SELECTION
DELIVERY VERIFIED --independent/human review--> WAITING_FOR_USER_CONFIRMATION
WAITING_FOR_USER_CONFIRMATION --user confirmation--> COMPLETED
```

协调工作项没有 CLAIMED/IMPLEMENTED；它们通过 child 状态和自己的 gate 推进。

## 完成条件

- Task：实现证据存在、冻结测试执行、Task gate PASS；
- Capability：所有计划 Task VERIFIED，集成测试与 Capability gate PASS；
- Delivery：所有计划 Capability VERIFIED，顶层交付测试与 Delivery gate PASS；
- 用户交付：独立语义验收 PASS 或明确接受人工验收结果，并且用户随后确认交付。

不得根据百分比、对话陈述、子级数量相等或文件存在推断 PASS。

## 阻断与恢复

BLOCKED 必须记录事实、责任方和解除条件。依赖完成、环境恢复或用户补充授权后重新计算 READY；不要直接跳过 gate。父链 stale 时先修订并重新冻结受影响 baseline。

同一 baseline 下重试任何 BLOCKED 工作项时，只使用 `retry-item` 提交当前 expected baseline 指纹和显式确认。Task 回到 FROZEN 后沿用仍与该 baseline 绑定的开发方式并重新计算 READY；Task baseline 修订则清除开发方式并回到 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。Capability/Delivery 回到 FROZEN 后重新运行聚合 gate。重试不修改需求或 scope；需要改契约时走 baseline 修订。不提供旧命令别名。

## 后续工作

已 VERIFIED 工作项默认不可原地修订。新反馈按同 Task 修复、父 baseline 修订、后续 Task/Capability/Delivery 或建议延期分类，并保留关系与旧证据。
