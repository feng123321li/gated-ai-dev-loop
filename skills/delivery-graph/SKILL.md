---
name: delivery-graph
description: "把已确认的软件需求建模为分层 Delivery Graph，并驱动 Git 基线确认、冻结、自动 Agent 派遣或手动 CLI 交接、TASK 验收、可选 GROUP seam 验收、Delivery Acceptance/Readiness、最终用户确认、归档与恢复。用于规划或修订多项目、多模块交付，选择自动/手动执行，接续既有 Delivery，或处理暂停、失联、容量等待、Git 漂移和 REPLAN_REQUIRED。"
allowed-tools:
  - mcp__plugin_delivery-graph_delivery-graph__workspace_status
  - mcp__plugin_delivery-graph_delivery-graph__recommend_assurance_profile
  - mcp__plugin_delivery-graph_delivery-graph__hierarchy_contract
  - mcp__plugin_delivery-graph_delivery-graph__preview_hierarchy
  - mcp__plugin_delivery-graph_delivery-graph__confirm_development_baseline
  - mcp__plugin_delivery-graph_delivery-graph__select_execution_mode
  - mcp__plugin_delivery-graph_delivery-graph__resume_execution_mode
  - mcp__plugin_delivery-graph_delivery-graph__create_manual_handoff
  - mcp__plugin_delivery-graph_delivery-graph__start_manual_handoff
  - mcp__plugin_delivery-graph_delivery-graph__prepare_hierarchy
  - mcp__plugin_delivery-graph_delivery-graph__prepare_delivery_revision
  - mcp__plugin_delivery-graph_delivery-graph__delivery_revision_history
  - mcp__plugin_delivery-graph_delivery-graph__plan_dispatch_batch
  - mcp__plugin_delivery-graph_delivery-graph__freeze_hierarchy
  - mcp__plugin_delivery-graph_delivery-graph__graph_frontier
  - mcp__plugin_delivery-graph_delivery-graph__graph_status
  - mcp__plugin_delivery-graph_delivery-graph__open_delivery_dashboard
  - mcp__plugin_delivery-graph_delivery-graph__graph_events
  - mcp__plugin_delivery-graph_delivery-graph__advance_graph
  - mcp__plugin_delivery-graph_delivery-graph__loop_context
  - mcp__plugin_delivery-graph_delivery-graph__dispatch_loop
  - mcp__plugin_delivery-graph_delivery-graph__heartbeat_loop
  - mcp__plugin_delivery-graph_delivery-graph__report_loop_progress
  - mcp__plugin_delivery-graph_delivery-graph__pause_loop
  - mcp__plugin_delivery-graph_delivery-graph__resume_loop
  - mcp__plugin_delivery-graph_delivery-graph__record_loop_result
---

# Delivery Graph

把本 Skill 作为“分层交付 Graph 控制面”。用 `Delivery → GROUP（可递归）→ TASK` 表达纵向层级，用依赖与资源声明表达横向 DAG；决定何时由一次性 reservation 指定的独立 receiver 运行 Loop，不规定 Loop 内怎样实现。

```text
确认需求 → 确认开发基线 → 冻结 Delivery Graph → 自动派遣 / 手动交接
         → TASK 实现与验收 → 可选 GROUP seam 验收
         → Delivery Acceptance/Readiness → 用户最终确认
```

## 不可破边界

- 只调用本 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE`，停止治理写入。
- 只使用 schema v3；准备前调用 `hierarchy_contract` 获取精确契约，不从源码、示例或旧会话猜参数。
- 把 SQLite 和 Graph 事件链视为机器权威。不得用 Shell、Python 或数据库连接读写 `scheduler.db`，不得人工修补 Graph 或 Markdown 投影。
- primary 总协调上下文只规划、路由和监控。AUTOMATIC 的每个 TASK 与 Review 都先由 `plan_dispatch_batch` 预留，再交给不同的独立 receiver 调用 `dispatch_loop`；primary 不得领取或内联任何 Loop。
- 不把 Graph 范围当作 Git 或外部操作授权。只有用户选择 `AUTOMATIC` 时，才把 Controller 精确返回的 stash/create-or-switch workspace 准备视为该选择的一部分；commit、merge、push、发布、迁移和新增权限仍分别取得授权。
- 不让 Controller 执行 Git 写操作。只读确认 binding；`AUTOMATIC` 的一次用户选择明确授权宿主机械执行 Controller 返回的 workspace 准备：精确复核 dirty 指纹、stash 业务改动（排除 `.layered-delivery/**`）、创建或切换 Delivery 分支，再调用 `resume_execution_mode`。commit、merge、push、发布等仍分别授权。现有 linked checkout 也只视为普通 current workspace，不自动创建新 worktree；receiver 只使用控制器验证后的实际项目路径。
- 一个实际 workspace/worktree 可以绑定多个未结束 Delivery，控制状态始终以 `rootId` 隔离，但工作区策略只有 `CURRENT_WORKSPACE_SERIAL`：每个 Delivery 使用独立分支，同一物理 checkout 一次只运行一个 Delivery。已有调度 owner 时，后启动或后发现的自动 Delivery 标记为 `QUEUED`，保留无需再次确认的自动 continuation；前一个安全释放后再自动准备和续调队首。owner dirty、资源冲突、未合并状态或 HEAD 漂移时停止切换。禁止跨 Delivery 共享 checkout/branch 并行执行。新业务目标默认创建新 Delivery；只有同一需求延续或 `REPLAN_REQUIRED` 才创建同一 Delivery 的 Revision。
- 为同一需求保持稳定 `delivery.id`、`requirementKey` 和 `.layered-delivery/<delivery-id>/`；不要创建共享 handoff 目录或第二套控制面。
- 在手动接收方检查、分析、修改或测试代码前调用 `start_manual_handoff`。`HANDOFF_READY` 只是冻结的 handoff，不是已启动的 Graph Run。
- 执行模式只有 `AUTOMATIC` 和 `MANUAL`。AUTOMATIC TASK 与 Review 都要求非空的一次性 reservation、匹配的 decision fingerprint、独立 receiver context 和显式 `operation_id`；MANUAL 只允许 TASK，省略 AUTO reservation 但仍要求独立 receiver、显式 `operation_id`、可信 Adapter 与已验证 workspace。primary、普通 helper 和内部 Worker 均不得 claim。
- 只有真实用户确认后才记录最终完成。

## 入口路由

先调用 `workspace_status`；已知 Delivery 时始终传 `rootId`。无参返回 `DELIVERY_SELECTION_REQUIRED` 时只展示候选并按本会话持有的 `rootId` 重查，不能按更新时间猜选；仍可为新需求调用 `preview_hierarchy`。无参发现不恢复未绑定的 `CHOICE_READY/HANDOFF_READY`，这两种草稿必须用创建响应中的 `rootId` 显式续接。

| 状态 | 执行 |
|---|---|
| `ABSENT` | 新交付读取[规划说明](references/planning-quickstart.md)；只读问答不创建状态 |
| `DELIVERY_SELECTION_REQUIRED` | 不推进任何候选；按当前会话持有的 `rootId` 再调用 `workspace_status`，或为明确的新需求 preview 新 Delivery |
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
2. 用户明确指定 Skill 时先记录为共享 `root.skillHints`。初步检查后，只有该 Skill 能帮助把握需求方向、关键约束、验收、风险或 TASK 边界时，才在形成候选 hierarchy 前按宿主原生入口预触发；不适用于规划或宿主不可用时不阻塞，留给后续相应 Loop。实现类 Skill 多数应由 TASK receiver 在真实代码上下文中调用。
3. 调用只读 `recommend_assurance_profile`，按真实任务事实明确填写根 TASK 数、项目数、结构影响范围、高影响风险项、验证计划和风险级别；使用其确定性 `recommendedProfile`，并把 `reasons` 写入 `assuranceRationale`。事实不明确时填 `UNKNOWN`，结果会保守返回 `STANDARD`；不得从自由文本自行猜档。
4. 只为真实分层、依赖或并行汇合创建 `GROUP`。GROUP 的 `reviewLoop` 仅在直接子项之间存在必须独立验证的 seam 时配置；纯协调/汇合 GROUP 使用 `null`。不要为单 TASK 制造形式层级。
5. 需求涉及建表、改表或删表时，在 preview 前读取真实当前结构、完成字段级 before/after 设计，并按 `projectionGuidance.databaseChanges` 写入负责 TASK 的 `loop.payload.databaseChanges`；不得把数据库设计留给执行 Loop。
6. 规划层只把需求方向、目标、用户明确约束、已确认外部契约和已知验收按需写入对应 `loop.payload`，不要求面面俱到。Graph 把工作项整理为 hierarchy/DAG，统一维护依赖、资源、frontier、指纹、全局进度、结果汇总和验收路由，但不创作业务需求或决定实现。普通文件/目录、实现类、内部方法、代码结构和详细测试方案由 Loop 结合真实代码展开；仅当需求本身明确指定，或用户确认必须兼容的外部契约明确指定精确标识时才写入。不要把 Skill 的默认命名、示例或实现建议升级成需求事实。用 `resourceClaims` 表达跨 Delivery 排他资源；每项数据库变更的 `resourceClaim` 必须同时存在于该 TASK 的资源声明中。
7. 调用 `hierarchy_contract` 后构造 schema v3。候选层级形成后、调用 `preview_hierarchy` 或局部 `refreeze_task_requirement` 前，必须执行其 `projectionGuidance.taskSplitIntegrityPreflight`：先做每个 TASK 可独立实现和验收的 L0 检查；删除、改名、移动或公共字段/方法/签名变化再由规划宿主按项目语言触发 L1 定向引用分析。任何 TASK 的构建或验收依赖后继 TASK 恢复时，先调整切分；不要 preview、refreeze 或取得 dispatch reservation。Controller 不分析自然语言 payload，也不替代该规划预检。
8. 较大层级先写 JSON 文件并校验，再通过 `hierarchy_file` 传给 `preview_hierarchy`。
9. 仅在返回 `CHOICE_READY` 且 `artifactsReady=true` 后处理 `pendingInteraction`。

## 处理待确认交互

把 `pendingInteraction` 作为唯一规范入口；`developmentBaseline` 和 `executionChoice` 仅为兼容别名。原样遵循 Controller 的 `presentationPolicy`、选项顺序、默认项、推荐项和文案，不自行增删选项。

### `DEVELOPMENT_BASELINE`

1. 展示 Controller 返回的本地分支、`NEW_FROM_MAINLINE`，以及仅在干净的当前 feature workspace 出现的 `NEW_FROM_CURRENT_BRANCH`；两个 NEW 选项都要求新分支名，后者是用户显式授权的 stacked Delivery 子分支。
2. 调用 `confirm_development_baseline`，原样回传交互中的 hierarchy、Graph、Revision 和 baseline context fingerprints。
3. 只有 adoption 当前脏分支时，才让用户确认全部改动属于本 Delivery，并回传精确 `workingTree.stateFingerprint` 作为 `confirmed_dirty_state_fingerprint`。选择 `NEW_FROM_MAINLINE` 或另一个本地分支时不归属当前改动，先冻结目标 binding，待队首 workspace 准备自动 stash；状态变化后必须重新计算指纹。
4. 让 Controller 只读冻结 `gitBinding`。`NEW_FROM_MAINLINE` 把 `baseCommit` 钉在确认时的主线 HEAD；`NEW_FROM_CURRENT_BRANCH` 把 clean 当前 feature 的 HEAD 钉为 `baseCommit`，并让该父 feature 同时成为 `baseRef/integrationTarget`。不要在这里创建分支或 worktree；宿主只在父 Delivery 达到可验证 commit、clean、HEAD 未漂移与 receiver 安全释放边界后创建或切换子分支。
5. 对 Git 探测错误 fail closed。仅当确认不是 Git 工作区时跳过基线交互。
6. 多 Git 项目必须在每个 `projectScopes[*]` 显式提供完整 `gitBinding`；任一缺失就停止，不得用顶层偏好推断其他仓库。
7. 普通单仓 Delivery 可以省略 `projectScopes`；运行时必须从顶层 `delivery.gitBinding` 与实际 Delivery workspace 合成并验证唯一 `primary` scope。`loop_context.projectScopes` 为空时停止开发，不把 primary checkout 或模型输入路径当作隐式授权。

确认成功后继续处理返回的 `pendingInteraction(kind=EXECUTION_MODE)`。

### `EXECUTION_MODE`

- 用户只确认一次执行模式；`select_execution_mode` 立即持久化该选择。若 Controller 返回 `PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION`，宿主完成串行释放检查和当前分支动作后调用 `resume_execution_mode`，不重试选择、不重新提问。用户输入需求修改意见时不要调用选择工具；继续规划并使旧选择失效。
- 选择 `AUTOMATIC` 时只使用 `CURRENT_WORKSPACE_SERIAL`，不创建或预留新 worktree。已有 owner 时，响应与投影把后启动 Delivery 标记为 `QUEUED`；保存其 `deliveryQueue.continuation`，前一个 Delivery 产生可验证 commit、working tree/index clean、HEAD 与冻结 binding 一致且 receiver 全部安全释放后自动续接，不重选模式。
- 同一 Delivery 冻结任意后续 Revision（`N → N+1`）时不开始新的物理 workspace turn。项目集合、checkout、分支与冻结基线未变时，Controller 复用最初的 clean `workspaceTurnStart`，允许前序 Revision 已产生的 tracked、staged 或 untracked 业务改动原地进入下一 Revision；不得要求用户删除生成物、stash 或创建检查点提交，且 Revision 确认仍不授权 commit。未解决冲突、原始 turn 历史被改写，或项目/绑定变化时继续 fail closed，并按返回的清洁边界处理。
- 队首读取 `workspacePreparation.automaticHostPreparation` 并按顺序机械执行。dirty 且无冲突时，先核对每个 `workingTreeStateFingerprint`，用返回的 pathspec stash tracked/staged/untracked 业务改动且排除 `.layered-delivery/**`；再创建或切换冻结分支，最后以明确 `rootId` 与双 fingerprint 调用 `resume_execution_mode`。保留 stash 直到回到原分支用 index 语义成功恢复，不自动 pop。clean 时直接创建或切换分支并 resume；未合并或 stash 后仍 dirty 时保持排队/等待。
- 多项目 Delivery 的全部 `READ_WRITE` Git scope 必须同时满足上述切换条件并一起完成自动准备；任一 scope 存在资源冲突、owner dirty、未合并状态、HEAD 漂移或无法证明安全释放时停止切换。现有 primary 或 linked checkout 都按当前实际 workspace 处理，不自动创建第二个 worktree。同一 checkout 一次只推进一个显式 `rootId`。
- `FROZEN_DELIVERY_BRANCH_REQUIRED` 只允许在可验证 commit、clean tree、HEAD 未漂移且 receiver 安全释放后恢复冻结分支；`FROZEN_DELIVERY_BRANCH_DIRTY` 必须停止切换。全部 project workspace 验证通过后才按响应继续 `resume_execution_mode` 或 frontier。
- 选择 `MANUAL` 时，原样展示 `manualHandoff.receiverPrompt`。让接收宿主在实际工作区调用 `start_manual_handoff` 后，再创建独立原生 TASK child；其 `dispatch_loop(MANUAL)` 省略 AUTO reservation，但必须提交自己的 receiver context 与新 `operation_id`，并通过 Adapter、workspace、Graph 与项目 scope 校验。

## 手动启动的 Git 漂移

- 单仓启动返回 `BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION` 时，停止开发并处理其 `pendingInteraction(kind=DEVELOPMENT_BASELINE)`。
- 原样回传响应中的期望 Graph、Revision 和 baseline context fingerprints；只有 adoption 当前脏分支时才要求用户确认并回传精确 dirty fingerprint，切换到其他 Delivery 分支由已选择的 AUTOMATIC workspace 准备处理。
- binding 未变时恢复原 Revision；binding 改变时让 Controller 为同一 Delivery 创建下一不可变手动 Revision。始终使用确认响应返回的权威双 fingerprint 重试 `start_manual_handoff`。
- 多仓手动启动出现 Git 漂移时 fail closed。不要自动重绑定、猜测仓库对应关系或创建 Revision；先恢复已冻结基线，或按完整多仓 bindings 显式修订后再启动。

## 执行 Graph

首次进入、receiver 完成/需要关注、`nextWakeAt` 到达或返回 `ADVANCE_REQUIRED` 时调用一次 `graph_frontier`，并先完整消费当前批次的立即 action。存在后台 receiver 或 dispatch reservation 时，随后严格执行 `progressMonitor.waitDirective`：优先使用宿主原生 receiver 等待；无事件只在 `pollNotBefore` 调用一次只读 `graph_status`。该截止已对齐下一个有意义健康阈值，不得自行缩短为固定短周期。禁止 back-to-back 调用 `graph_frontier` 或 `graph_status`；`changeFingerprint` 未变化时不重复播报进度。精确 claim、reservation、heartbeat、资源锁和接收协议见[执行说明](references/execution-quickstart.md)。

- `REFREEZE_TASK_REQUIREMENT`：停止派遣该 TASK，先对受影响 TASK 重新执行切分完整性预检，再只修改用户授权的需求字段并重新冻结。任一当前 Run 的未领取 dispatch reservation 都绑定旧 Graph 指纹；`unfreeze_task_requirement` / `refreeze_task_requirement` 返回 `SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE` 时，等到其 `retryAfter` 后再续接，期间不得强改需求或复用旧 assignment。
- `CLAIM_MANUAL_TASK`：为手动 Graph，或已由 `handoff_ready_automatic_task` 显式恢复的单个自动 TASK，创建独立人工 receiver；只有 TASK 可 MANUAL claim，后续 Review 仍走 AUTOMATIC reservation 派遣。action 带有非空 `skillHints/receiverPrompt` 时，把提示词原样交给 child：用户明确指定的 Hint 对当前 Loop 适用且宿主可用时应在相应阶段原生触发，只有阶段不适用或不可用才跳过。MANUAL 不带 AUTO reservation，但必须提交独立 receiver context 与新 `operation_id`，并通过 Adapter、workspace、Graph 和项目 scope 校验。
- `DISPATCH_LOOP`：对每个 READY TASK 或 Review 调用一次 `plan_dispatch_batch`，完整消费 `concurrentDispatchGroups`，并立即创建独立 receiver；不要在 reservation 后继续分析。assignment 带有非空 `skillHints/receiverPrompt` 时，把提示词原样交给 child；Codex 使用其中的 `$skill-name`，Claude Code 使用原生 Skill tool，其他宿主使用自己的原生 Skill 入口。用户明确指定的 Skill 在当前阶段适用且可用时应触发，实现类 Skill 多数在 TASK；只有阶段不适用或宿主不可用才跳过，但不形成 Controller 成功门禁。receiver 用 assignment 的 reservation、decision fingerprint、自己的 context 和新 `operation_id` 调用 `dispatch_loop(AUTO)`。全部 assignment 消费后严格执行返回的 `postActionWait`：优先等待 receiver 原生事件，最迟到最早 reservation 截止时间再调用一次 `graph_frontier`；禁止忙轮询。同一 reservation 与 operation 的响应丢失重试必须返回已提交 assignment，不能重复领取。
- AUTO receiver 启动或 claim 失败时不得由总协调器直接领取。刷新 frontier；仍有效的 reservation 只按原参数重试，已过期的 reservation 重新规划。仍需人工接管时，确认 TASK 从未领取、无有效 reservation、Delivery workspace 干净且无代码改动，再取得用户明确授权调用 `handoff_ready_automatic_task`；它只把当前 READY TASK 改为人工接收，Review 不降级。
- claim 成功后，独立 receiver 读取一次 `loop_context`，确认至少一个已验证的 `projectScopes`。`STANDARD` 在任何代码工作前用精确 `operation_id` 提交首次 `heartbeat_loop`；短时 `LIGHT` 的 claim 已建立初始租约，可在租约窗口内不发 heartbeat/progress，直接完成定向验证并提交真实终态，超出窗口则按期 heartbeat。后续 heartbeat、progress、pause 与 result 都显式携带同一 operation。
- 只让外层 receiver 持有 reservation 和 `operation_id` 并调用 claim、heartbeat、progress、pause、resume 和 `record_loop_result`。内部 Worker 不得持有控制面凭据。
- 让 receiver 使用已验证的 `projectScopes`，按租约 heartbeat，并在关键阶段报告 progress；progress 不续租。
- TASK 先从实际改动、依赖和契约界定 `result.affectedScopes`；其中 `paths` 使用字面量仓库相对路径并覆盖相关依赖/契约锚点。只运行覆盖该范围的测试、构建或契约检查，并在 `result.verificationEvidence` 记录命令摘要、scope 和结果；Controller 在终态记录可信 `evidenceWorkspaceSnapshots` 与逐相关路径的 `evidenceScopeSnapshots`。Review 的独立性是独立判断，不是机械重跑全量：只自动复用 `validationEvidenceIndex` 中 `PASSED + EXACT_MATCH` 的证据；无关文件变化不使有界 scope 失效，再对缺口、相关路径 `CHANGED/UNBOUND`、findings 和高风险边界定向复跑。影响范围无法界定等明确风险才升级全量。
- Review 各守一层：TASK Review 只验冻结 TASK 验收点、局部行为、公共契约与定向回归；GROUP Review 可选，只验直接子项 seam；Delivery Acceptance/Readiness 只验顶层需求覆盖、整体集成/E2E 证据、运行准备度和全局风险。不得复查已由下层关闭的实现细节或单测。
- 严格分离 Controller、Review receiver 和用户：Controller 只依据 Graph 前驱终态解锁节点、机械校验结果结构/声明终态一致性，并持久化事件、SQLite outcome 与投影；它不判断技术验收、证据充分性或运行准备度。独立 Review receiver 才作当前层验收决定；Delivery receiver 每个 `STANDARD` Delivery 只执行一次顶层 Acceptance/Readiness，不逐个重验全部 Loop。用户只作最终业务确认。`LIGHT` 不创建独立 Review。
- `SUCCEEDED` Review 的 `result` 只保存 `validationDecision`、`reviewFindings`、本层唯一结论字段（`taskAcceptance` / `groupIntegration` / `deliveryReadiness`）、有界验证证据和 Controller 快照；`upstreamLoopResults` 只存在于运行 context，禁止复制进 outcome。未配置的 GROUP Review 不生成 Graph 节点、SQLite run/event/outcome 或投影段落。
- 跨 Delivery 出现相同 `resourceClaims` 或物理工作区冲突时，只把已选择 `AUTOMATIC` 的后启动或后发现 Delivery 标记为 `QUEUED`；手动冻结保持 `HANDOFF_READY`。只有前一个 Delivery 已形成可验证 commit、工作树 clean、HEAD 未漂移且 receiver 安全释放后才能自动续调队首；任一条件不满足就保持排队。资源无交集也不能绕过 `CURRENT_WORKSPACE_SERIAL` 的单 checkout 串行边界，且绝不能 stash 正在运行 owner 的未完成改动。
- 数据库 TASK 只应用和验证冻结 `databaseChanges[*].after`，不得在 Loop 内另行设计字段、索引、约束或迁移策略；任何必要偏离都提交 `REPLAN_REQUIRED`。
- 只提交真实业务终态。实际范围或风险超出冻结契约时提交 `REPLAN_REQUIRED`，不要硬完成。
- `record_loop_result` 成功时，Controller 从已验证的 `READ_WRITE` Git project scope 自动保存相对冻结 `baseCommit` 的工作区变更快照，并在 TASK 验收中展示文件清单和 diff；它是验收时刻的 workspace 证据，不替代 commit/clean/HEAD 归属判断。主控制目录中的 `acceptance.md` 必须相对链接 `workspace-changes.patch` 供审核。
- frontier 返回 `RECORD_USER_CONFIRMATION` 时，展示分层验收结果并等待真实用户确认。

## 恢复

- 对 `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`，路由到新的独立接收上下文并调用 `resume_loop`，不要重新 prepare/freeze；对租约过期、receiver 失联或基础设施失败，刷新 frontier 并交给 `advance_graph`，不得复用旧 operation。
- `dispatch_loop` 响应未知时，只用原 reservation、decision fingerprint、receiver context 和 `operation_id` 幂等重试；返回 reservation、fingerprint、workspace 或 operation 错误时，child 立即停止仓库与 Loop 操作，只把稳定错误码报告协调器。
- 仅根据宿主提供的结构化容量状态与 `resetAt` 等待；不要从文本猜测额度或静默切换模型/Adapter。
- 对物化状态损坏，调用 `rebuild_graph_run` 从已校验事件链重建且不要修改事件；对需求范围、依赖、资源或 Review 契约变化，记录 `REPLAN_REQUIRED`，等待用户决定是否准备同一 Delivery 的下一 Revision。

## 按需读取

- 新建/修订 Graph、Git baseline、project scopes、schema 与冻结：[planning-quickstart.md](references/planning-quickstart.md)
- Frontier、自动派遣、手动 claim、租约、资源锁与恢复：[execution-quickstart.md](references/execution-quickstart.md)
- 外层 receiver、Loop 内 Worker、身份边界与遥测：[agent-execution-boundary.md](references/agent-execution-boundary.md)
- TASK、可选 GROUP seam 与 Delivery Acceptance/Readiness：[acceptance.md](references/acceptance.md)
- MCP 断连、重放安全、SQLite 权威、工作区绑定与投影：[mcp-transport.md](references/mcp-transport.md)
