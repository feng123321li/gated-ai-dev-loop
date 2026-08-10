---
name: delivery-graph
description: "把已确认的软件需求建模为分层 Delivery Graph，并驱动 Git 基线确认、冻结、自动 Agent 派遣或手动 CLI 交接、TASK/GROUP/Delivery Review、最终验收、归档与恢复。用于规划或修订多项目、多模块交付，选择自动/手动执行，接续既有 Delivery，或处理暂停、失联、容量等待、Git 漂移和 REPLAN_REQUIRED。"
allowed-tools:
  - mcp__plugin_delivery-graph_delivery-graph__*
---

# Delivery Graph

把本 Skill 作为“分层交付 Graph 控制面”。用 `Delivery → GROUP（可递归）→ TASK` 表达纵向层级，用依赖与资源声明表达横向 DAG；只决定何时、由哪个独立 receiver 运行哪个 Loop，不规定 Loop 内怎样实现。

```text
确认需求 → 确认开发基线 → 冻结 Delivery Graph → 自动派遣 / 手动交接
         → TASK 实现 → TASK / GROUP / Delivery Review → 用户最终验收
```

## 不可破边界

- 只调用本 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE`，停止治理写入。
- 只使用 schema v3；准备前调用 `hierarchy_contract` 获取精确契约，不从源码、示例或旧会话猜参数。
- 把 SQLite 和 Graph 事件链视为机器权威。不得用 Shell、Python 或数据库连接读写 `scheduler.db`，不得人工修补 Graph 或 Markdown 投影。
- 让总协调上下文只规划、路由和监控。让每个 TASK、Review 使用独立 receiver；不得在总协调上下文内实现或 Review。
- 不把 Graph 范围当作 Git 或外部操作授权。创建/切换分支、commit、merge、push、发布、迁移和新增权限仍分别取得授权。
- 不让 Controller 执行 Git 写操作。只读确认 binding；让宿主创建或复用 linked worktree，让 receiver 使用控制器验证后的实际项目路径。
- 一个工作区最多绑定一个未结束 Delivery。新业务目标默认创建新 Delivery；只有同一需求延续或 `REPLAN_REQUIRED` 才创建同一 Delivery 的 Revision。
- 为同一需求保持稳定 `delivery.id`、`requirementKey` 和 `.layered-delivery/<delivery-id>/`；不要创建共享 handoff 目录或第二套控制面。
- 在手动接收方检查、分析、修改或测试代码前调用 `start_manual_handoff`。`HANDOFF_READY` 只是冻结的 handoff，不是已启动的 Graph Run。
- 只有真实用户确认后才记录最终完成。

## 入口路由

先调用 `workspace_status`；已知 Delivery 时传 `rootId`。新目标若撞上另一未结束 Delivery，切换到新宿主工作区后重新检查，不要续接旧 Graph。

| 状态 | 执行 |
|---|---|
| `ABSENT` | 新交付读取[规划说明](references/planning-quickstart.md)；只读问答不创建状态 |
| `CHOICE_READY` | 处理 `pendingInteraction`；已有 `executionSelection` 时按 `nextAction` 恢复，不重复询问 |
| `HANDOFF_READY` | 在实际工作区调用 `start_manual_handoff`；Graph 启动前不得开发 |
| `PREPARED` | 续接当前方案；需求未变时不要重复 prepare |
| `ACTIVE` / `BLOCKED` / `PAUSED` | 读取[执行说明](references/execution-quickstart.md)，从 `graph_frontier` 恢复 |
| `COMPLETED` | 报告终态；仅在用户明确要求后调用 `archive_delivery`，新目标创建新 Delivery |
| `ARCHIVED` | 已从默认工作区发现中隐藏；历史和详情投影仍按 `rootId` 可查 |
| `CANCELLED` | 报告终态；仅在用户明确续接同一未验收需求时创建 Revision |

遇到未知写响应、MCP 重连、Git binding 异常或投影问题时，先读取[MCP 与状态说明](references/mcp-transport.md)，不要盲目重放写操作。

## 规划 Graph

完整规划规则见[规划说明](references/planning-quickstart.md)。按以下顺序执行：

1. 检查真实代码和工作区；与用户确认目标、边界、验收点、项目范围、依赖和排他资源。
2. 选择保障档。不确定时用 `STANDARD`；仅当单一根 TASK、局部低风险且定向验证充分时用 `LIGHT`，并写明 `assuranceRationale`。
3. 只为真实分层、并行汇合或 Review 边界创建 `GROUP`。不要为单 TASK 制造形式层级。
4. 需求涉及建表、改表或删表时，在 preview 前读取真实当前结构、完成字段级 before/after 设计，并按 `projectionGuidance.databaseChanges` 写入负责 TASK 的 `loop.payload.databaseChanges`；不得把数据库设计留给执行 Loop。
5. 把其他实现目标和约束放入 `loop.payload`；用 `resourceClaims` 表达跨 Delivery 排他资源；每项数据库变更的 `resourceClaim` 必须同时存在于该 TASK 的资源声明中；把用户指定 Skill 记录为共享 `root.skillHints`。
6. 调用 `hierarchy_contract` 后构造 schema v3。较大层级先写 JSON 文件并校验，再通过 `hierarchy_file` 传给 `preview_hierarchy`。
7. 仅在返回 `CHOICE_READY` 且 `artifactsReady=true` 后处理 `pendingInteraction`。

## 处理待确认交互

把 `pendingInteraction` 作为唯一规范入口；`developmentBaseline` 和 `executionChoice` 仅为兼容别名。原样遵循 Controller 的 `presentationPolicy`、选项顺序、默认项、推荐项和文案，不自行增删选项。

### `DEVELOPMENT_BASELINE`

1. 展示 Controller 返回的本地分支、`NEW_FROM_MAINLINE`，以及仅在干净 primary feature 工作区出现的 `NEW_FROM_CURRENT_BRANCH`；两个 NEW 选项都要求新分支名，后者是用户显式授权的 stacked Delivery 子分支。
2. 调用 `confirm_development_baseline`，原样回传交互中的 hierarchy、Graph、Revision 和 baseline context fingerprints。
3. 工作树非干净时，先让用户确认全部改动属于本 Delivery，再回传精确 `workingTree.stateFingerprint` 作为 `confirmed_dirty_state_fingerprint`。状态变化后必须重新确认。
4. 让 Controller 只读冻结 `gitBinding`。`NEW_FROM_MAINLINE` 把 `baseCommit` 钉在确认时的主线 HEAD；`NEW_FROM_CURRENT_BRANCH` 把 clean 当前 feature 的 HEAD 钉为 `baseCommit`，并让该父 feature 同时成为 `baseRef/integrationTarget`。不要在这里创建分支或 worktree，也不要要求 primary 释放父分支。
5. 对 Git 探测错误 fail closed。仅当确认不是 Git 工作区时跳过基线交互。
6. 多 Git 项目必须在每个 `projectScopes[*]` 显式提供完整 `gitBinding`；任一缺失就停止，不得用顶层偏好推断其他仓库。

确认成功后继续处理返回的 `pendingInteraction(kind=EXECUTION_MODE)`。

### `EXECUTION_MODE`

- 用户选择后只调用一次 `select_execution_mode`。用户输入需求修改意见时不要调用选择工具；继续规划并使旧选择失效。
- 选择 `AUTOMATIC` 时，消费 `worktreeSetup.hostDispatch`；多项目同时完整消费 `projectWorktreeSetups`，为每个 `READ_WRITE` Git scope 准备精确 binding 的 linked worktree，但只启动一个后台 Delivery coordinator。创建开始后立即按 `hostDispatch.progressReporting` 调用 `report_worktree_setup`，之后按 30 秒心跳间隔上报阶段、摘要与百分比。后台用原双 fingerprint 调用 `workspace_status → resume_execution_mode → graph_frontier`，所有项目进度仍写入同一控制面。主会话留在 primary checkout，仅监控和处理用户交互。
- 严格遵循 `hostDispatch.launchPolicy`：`IMMEDIATE` 才创建；`DO_NOT_REISSUE` 等待既有 setup；secondary scope 的 `CONTINUE_EXISTING_WORKTREE_TASK` 由当前 coordinator 完成，不另起 coordinator。`branchRef/gitBinding` 是宿主 Git 写入的精确目标，不得自行生成替代分支。
- `WORKTREE_SETUP_LEASE_EXPIRED` 或 `WORKTREE_SETUP_FAILED` 时停止重发。只有确认旧进程已停止且半成品目录/worktree 已安全核对后，才用唯一 `retry_request_id` 调用 `report_worktree_setup(RETRY_CONFIRMED)`；只消费 Controller 原子授予的新 attempt，未知响应仅用同一 ID 恢复。
- `FROZEN_DELIVERY_BRANCH_REQUIRED` 只允许在干净 worktree 恢复冻结分支；`FROZEN_DELIVERY_BRANCH_DIRTY` 必须先审查并处理现有改动，不能直接切换。所有 project worktree 为 `READY` 后才调用 `resume_execution_mode`。
- 选择 `MANUAL` 时，原样展示 `manualHandoff.receiverPrompt`。让接收 CLI 在实际工作区调用 `start_manual_handoff` 后再消费 frontier。

## 手动启动的 Git 漂移

- 单仓启动返回 `BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION` 时，停止开发并处理其 `pendingInteraction(kind=DEVELOPMENT_BASELINE)`。
- 原样回传响应中的期望 Graph、Revision 和 baseline context fingerprints；dirty 状态仍要求用户确认并回传精确 fingerprint。
- binding 未变时恢复原 Revision；binding 改变时让 Controller 为同一 Delivery 创建下一不可变手动 Revision。始终使用确认响应返回的权威双 fingerprint 重试 `start_manual_handoff`。
- 多仓手动启动出现 Git 漂移时 fail closed。不要自动重绑定、猜测仓库对应关系或创建 Revision；先恢复已冻结基线，或按完整多仓 bindings 显式修订后再启动。

## 执行 Graph

持续调用 `graph_frontier` 并完整消费当前批次 action；精确 claim、reservation、heartbeat、资源锁和接收协议见[执行说明](references/execution-quickstart.md)。

- `REFREEZE_TASK_REQUIREMENT`：停止派遣该 TASK，只修改用户授权的需求字段，重新冻结后刷新 frontier。
- `CLAIM_MANUAL_TASK`：为手动 Graph 创建独立 TASK receiver；只有 TASK 可 MANUAL claim，后续 Review 仍走可信宿主自动派遣。
- `DISPATCH_LOOP`：按 `plan_dispatch_batch` 返回的并发组立即创建独立 receiver；不要在 reservation 后继续分析，不要跨 Delivery 复用 receiver 或工作区。
- claim 成功后，让 receiver 读取一次 `loop_context`，并在任何代码工作前提交首次独立 `heartbeat_loop`。
- 只让可信外层 receiver 调用 claim、heartbeat、progress、pause、resume 和 `record_loop_result`。内部 Worker 不得持有控制面凭据。
- 让 receiver 使用已验证的 `projectScopes`，按租约 heartbeat，并在关键阶段报告 progress；progress 不续租。
- 数据库 TASK 只应用和验证冻结 `databaseChanges[*].after`，不得在 Loop 内另行设计字段、索引、约束或迁移策略；任何必要偏离都提交 `REPLAN_REQUIRED`。
- 只提交真实业务终态。实际范围或风险超出冻结契约时提交 `REPLAN_REQUIRED`，不要硬完成。
- frontier 返回 `RECORD_USER_CONFIRMATION` 时，展示分层验收结果并等待真实用户确认。

## 恢复

- 对 `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`，路由到新的独立接收上下文并调用 `resume_loop`；不要重新 prepare/freeze。
- 对租约过期、receiver 失联或基础设施失败，刷新 frontier 并交给 `advance_graph`；不得复用旧 operation。
- 仅根据宿主提供的结构化容量状态与 `resetAt` 等待；不要从文本猜测额度或静默切换模型/Adapter。
- 对物化状态损坏，调用 `rebuild_graph_run` 从已校验事件链重建；不要修改事件。
- 对需求范围、依赖、资源或 Review 契约变化，记录 `REPLAN_REQUIRED`，等待用户决定是否准备同一 Delivery 的下一 Revision。

## 按需读取

- 新建/修订 Graph、Git baseline、project scopes、schema 与冻结：[planning-quickstart.md](references/planning-quickstart.md)
- Frontier、自动派遣、手动 claim、租约、资源锁与恢复：[execution-quickstart.md](references/execution-quickstart.md)
- 外层 receiver、Loop 内 Worker、身份边界与遥测：[agent-execution-boundary.md](references/agent-execution-boundary.md)
- TASK/GROUP/Delivery Review 与最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连、重放安全、SQLite 权威、工作区绑定与投影：[mcp-transport.md](references/mcp-transport.md)
