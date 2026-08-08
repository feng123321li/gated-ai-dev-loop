# 分层交付 Graph 控制面：项目实现结构

项目、Plugin 与 Skill 的 canonical 机器名为 `delivery-graph`。`.layered-delivery/` 是已有 schema v3 Delivery 的稳定数据目录，为避免破坏恢复链路不随产品名更改。

## 源码

```text
src/hdg/
├── dispatch_contracts.py
│                      # 外层 receiver 派遣决策指纹与策略版本
├── dispatch_planning.py
│                      # 可信宿主 receiver 预留与并发批次规划
├── loop_contracts.py   # Loop descriptor、outcome、资源锁
├── model_core.py       # schema v3 Delivery 与递归 GROUP/TASK 校验
├── git_binding.py      # Git worktree/feature/mainline 只读发现与校验
├── graph_model.py      # GROUP Join/Review、Delivery Review、DAG 与 FSM
├── repository.py       # SQLite、事件链、投影
├── planning.py         # prepare / freeze / workspace status
├── graph_frontier.py   # 下一步调度动作
├── graph_runtime.py    # claim、lease、结果、重试、恢复
├── hierarchy_contract.py
├── model_rendering.py  # Delivery/层级总览渲染
├── controller.py       # 协议无关的共享应用 Controller
├── operations.py       # 旧 Python 公共导入面的薄兼容 façade
├── host_policy.py      # Codex 项目根与 Claude 审批兼容策略
├── mcp_tools.py        # MCP 工具 schema 与 Controller 参数适配
├── mcp_adapter.py      # 2026-07-28 / legacy 双栈 JSON-RPC
└── mcp_server.py       # stdio framing、输入限制与进程入口
```

旧的 `acceptance.py`、`execution.py`、`remediation.py`、`skill_execution.py`、evidence hydration 和分拆 repository 模块已经删除，因为这些职责属于内部 Task Loop 或已收敛到外层 scheduler。

## 数据库

`.layered-delivery/scheduler.db` 包含：

| 表 | 内容 |
|---|---|
| `scheduler_metadata` | 当前 schema v3 Graph 生成契约标识；不兼容控制器不得共同写同一数据库 |
| `hierarchies` | Delivery 当前 Revision 的项目/Git scope、递归 GROUP/TASK hierarchy、graph、指纹，以及 `HANDOFF_READY/PREPARED/FROZEN` 状态 |
| `delivery_revisions` | 自动 Revision 与手动冻结快照的定义、连续性依据、原因、项目授权、执行模式和冻结/取代时间 |
| `delivery_preferences` | 每个 Delivery 已确认的单仓开发基线偏好，用于后续 Revision 缺省注入；多 Git scope 仍要求逐仓显式 binding |
| `dispatch_reservations` | 宿主创建 Agent 前的短租约派遣票据；按 run/node/attempt 原子去重、绑定决策指纹并原子预留跨 Delivery Agent 槽位 |
| `delivery_workspaces` | Delivery 与对话工作区 `workspaceKey` 的绑定；linked worktree 共享主控制根但保持身份隔离 |
| `runs` | 整体运行状态、冻结执行模式与宿主容量熔断 |
| `node_runs` | 每个节点的 attempt、claim、lease 和 outcome |
| `task_requirement_states` | 每个 TASK 当前 requirement revision、冻结/解冻状态与更新时间 |
| `graph_events` | 带前序哈希的不可变调度事件 |

Loop payload/outcome 以不透明 JSON 保存。共享 `root.skillHints` 作为 hierarchy 输入原样持久化，并由 `loop_context` 在运行时交给各 TASK、TASK Review、递归 GROUP Review 和 Delivery Review Loop；数据库没有 Task-Skill 分配、文件 scope、开发计划、Gate evidence 或 Skill activation 表。

## Hierarchy 与 Graph

Hierarchy 最外层只有两个入口：

```text
hierarchy
├─ delivery            # Graph/run 身份、保障档、交付摘要、Git binding
│  ├─ gitBinding?      # 主工作区 feature/base/fork commit/integration target
│  └─ projectScopes?   # 多仓库 root/access/gitBinding；可写仓库同名分支
└─ root
   ├─ schemaVersion
   ├─ skillHints
   ├─ definition       # GROUP 或 TASK
   ├─ reviewLoop       # STANDARD 的 TASK/GROUP 必填；LIGHT 根 TASK 为 null
   └─ children         # GROUP 可递归包含 GROUP/TASK，TASK 为空
```

嵌套节点不重复 `schemaVersion` 和 `skillHints` 包装字段。Delivery 不是 work item kind；`model_core.py` 只接受 `GROUP` 与 `TASK` 定义。

保障档由规划 Agent 根据实际改动内容和影响范围判断，Controller 不解析业务 payload 或按行数猜测。省略时安全回退为 `STANDARD`；`LIGHT` 只允许一个根 TASK，并要求保存 `assuranceRationale`。Graph 编译遵循以下终态规则：

- STANDARD TASK 依次通过 `TASK_LOOP` 和 `TASK_REVIEW_LOOP`，Review 成功才是终态；
- GROUP 等待全部直接子节点终态，依次通过 `GROUP_JOIN`（GROUP 完成点）和 `GROUP_REVIEW_LOOP`；
- 父 GROUP 只消费子 GROUP Review 后的终态；
- STANDARD 根终态进入 `DELIVERY_REVIEW_LOOP`，最后进入一次 `USER_CONFIRMATION`；
- LIGHT 只有 `TASK_LOOP → USER_CONFIRMATION`，执行中发现接口、数据、权限、安全、生产部署、跨模块影响或其他范围扩大时返回 `REPLAN_REQUIRED`，由同一 Delivery 的下一 Revision 升级为 STANDARD。

兄弟 `dependsOn` 是启动屏障。若依赖源是 GROUP，目标子树要等待源 GROUP 的 Review 成功，而不是只等待其 Join。

## 运行包

递归 hierarchy 会镜像为递归 GROUP/TASK 人类投影目录，但不改变 SQLite 机器权威。每个受治理工作区按稳定的 Delivery ID 保存多组投影：

```text
.layered-delivery/
├── overview.md
├── scheduler.db
├── d-order/
│   ├── handoff-<fingerprint>.md  # 手动交接时按需
│   ├── overview.md
│   ├── baseline.md
│   ├── progress.md
│   ├── acceptance.md
│   ├── revisions.md
│   └── work-items/
│       └── g-order/
│           ├── baseline.md
│           ├── progress.md
│           ├── acceptance.md
│           └── children/
│               └── t-api/
│                   ├── baseline.md
│                   ├── progress.md
│                   ├── acceptance.md
│                   ├── interfaces.md  # 接口索引；按需
│                   └── interfaces/
│                       └── 001-<接口标识>.md  # 每接口一份详情
└── d-portal/
    ├── overview.md
    ├── baseline.md
    ├── progress.md
    └── acceptance.md
```

`scheduler.db` 是需求、Revision、Graph run 与事件链的机器权威；Markdown 只是可重建投影。运行边界如下：

- 自动与手动路径共享 `.layered-delivery/<delivery-id>/`，手动包额外生成 `handoff-<fingerprint>.md` 并先登记为 `HANDOFF_READY`。交接阶段不创建 run、事件链或 workspace binding。
- `start_manual_handoff` 只有在接收工作区 Git binding 通过校验后才创建 `execution_mode=manual` 的 run。单仓漂移先返回 `DEVELOPMENT_BASELINE` 且零写入：确认原 binding 时保持当前 Revision、要求恢复分支后重试；确认新 binding 时生成下一不可变手动 Revision与新双 fingerprint。多仓漂移 fail closed，要求以完整 project bindings 创建手动 Revision，不能用单仓选择器局部改写。
- 手动 run 只有 `TASK_LOOP` 可以 `dispatch_mode=MANUAL`；全部 TASK/GROUP/Delivery Review 仍由可信外层 receiver 自动领取，最终仍需用户确认。progress/acceptance 只由事件投影刷新。
- Revision 连续性必须显式：`prepare_delivery_revision` 使用 `USER_EXPLICIT_SAME_DELIVERY`，或在已有 `REPLAN_REQUIRED` 时使用 `ACTIVE_LOOP_REPLAN`。候选 freeze 前不移动当前 hierarchy；取代时旧 run 原子标记为 `SUPERSEDED`。
- 同一 `workspaceKey` 已有未结束 run 时，第二个 Delivery 在 prepare 写入前返回 `CREATE_INDEPENDENT_WORKTREE_TASK`。Codex 与 Claude Code 自动 Git Delivery 都采用 `HOST_NATIVE_LINKED_WORKTREE`；Claude 使用 `delivery-graph:delivery-coordinator`，Codex 创建 `environment=worktree` 项目任务。
- 多仓 `projectScopes` 在冻结时要求精确项目授权。每个 Git scope 都必须带完整 `gitBinding`；可写仓库使用同名 feature 分支，但分别冻结自己的主线与 `baseCommit`。TASK receiver 不创建或切换分支。

Controller 只读发现和校验 Git，不执行 branch、worktree、stage、commit 或 push。`workspace_status.worktreeProvenance` 记录实际宿主、策略、拓扑、`selectionSource`、`baseRef`、`baseCommit`、`baseHeadCommit` 与 `integrationTarget`。只有未被其他 worktree/Delivery 使用且基线有效的 feature 分支才可 adoption。缺少 binding 时，干净或脏工作树都先返回 `DEVELOPMENT_BASELINE`；脏树的 `stateFingerprint` 覆盖 porcelain、变化路径的 worktree blob 与 index state，任一状态变化都会使旧确认失效。`.layered-delivery/**` 不计入业务 dirty 状态。

工作区根 `overview.md` 只列 Delivery 标识、标题、状态、更新时间和详情；Delivery `overview.md` 才展示本交付的 TASK 完成度、GROUP 数量与导航。根总览对每个 Delivery 独立校验：无关 Delivery 损坏时只把该行标为“调度状态异常”，健康 Delivery 的 frontier、状态查询和投影刷新继续运行；直接访问损坏 Delivery 仍返回带实际 `rootId` 的完整性错误。其他 Delivery 的投影目录损坏或不可写时，显式当前 `rootId` 的 `workspace_status` 通过 `projectionIssues` 报告并继续。顶层 `baseline.md` 保存基线树和节点链接，`progress.md` 聚合运行进展，`acceptance.md` 只完整展示 Delivery 本层 Review 与用户确认，并以摘要和链接串联根工作项报告。每个 GROUP/TASK 在递归节点目录下拥有自己的 baseline、progress 和 acceptance；GROUP baseline 链接直接子节点，TASK baseline 展示冻结 Loop 输入。TASK 验收只展开本 TASK 与 TASK Review；GROUP 验收只展开本层完成点与 Review，对直接子节点只显示状态、简要结果和验收链接。任何下层输入、证据或 Review findings 都不向上重复复制。progress 状态表显示 claim 事件记录的外层 receiver、认领身份和执行轮次；内部 Worker 的 agent/model/effort 只从 outcome 的 `workerTelemetry` 非权威展示，未知值保持 `unreported`。acceptance 摘要、子节点结果和 Review P0/P1/P2 问题使用表格。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md` 索引和 `interfaces/` 下每接口一份详情。完整 before/after 契约会被确定性比较：入参表展示类型、必填和说明，出参表不展示必填；删除值使用 Markdown 删除线，新增或删除字段只显示存在的一侧，真正修改的属性才使用“修改前 → 修改后”。`protocol` 为开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等只是示例，通用协议可用 `identifier` 定位。无声明时不生成。代码可辅助提取和校验，但不是动态投影源。所有文件绑定双指纹并可随权威状态重建；`workspace_status` 会为早期 schema v3 Delivery 补建当前适用的投影树，异常的其他 Delivery 通过 `projectionIssues` 报告，但不迁移数据库或 Graph。所有固定文案和状态保持中文，标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`；机器权威仍使用 UTC。

## MCP

工具分为六组：

- 外层 receiver 派遣计划：`plan_dispatch_batch`。它按当前可信宿主 Adapter 直接预留 receiver，固定 `modelPolicy=CURRENT_HOST_INHERIT`，不接收模型 inventory、风险判级或 effort。
- 规划与交接：`workspace_status`、`hierarchy_contract`、`preview_hierarchy`、`confirm_development_baseline`、`select_execution_mode`、`resume_execution_mode`、`create_manual_handoff`、`start_manual_handoff`、`prepare_hierarchy`、`freeze_hierarchy`。`workspace_status(base_ref=...)` 可承接宿主明确选择的基线；未指定时按有效 `origin/HEAD`、本地 `main`、本地 `master` 降级发现。preview 先登记 `CHOICE_READY` 并生成关联投影，再返回唯一 `pendingInteraction`：缺 binding 时为 `DEVELOPMENT_BASELINE`，确认后为 `EXECUTION_MODE`。`developmentBaseline` / `executionChoice` 只是该对象的兼容别名。Codex 映射 `request_user_input`，Claude 映射 `AskUserQuestion`，可调用时必须使用原生选择器。AUTOMATIC 由 `hostDispatch` 在 linked worktree 后台续接；手动 Git 漂移遵循上一节的单仓双分支和多仓 fail-closed 规则。
- Delivery 修订：`delivery_revision_history`、`prepare_delivery_revision`
- 需求修订：`unfreeze_task_requirement`、`refreeze_task_requirement`
- 查询：`graph_frontier`、`graph_status`、`graph_events`、`loop_context`
- Loop 控制：`dispatch_loop`、`heartbeat_loop`、`report_loop_progress`、`pause_loop`、`resume_loop`、`record_loop_result`

公开的 `freeze_hierarchy` 不接收 `execution_mode`，自动路径固定创建 `active` run。只有 `start_manual_handoff` 能把精确 `HANDOFF_READY` 双 fingerprint 启动为 `manual` run；Git 漂移 blocker 在任何控制状态写入前返回。`dispatch_loop(MANUAL)` 只允许该 run 的 `TASK_LOOP`，要求显式 receiver context，且不接受自动 reservation、decision、transport、attestation 或模型参数。manual run 的 Review 继续使用可信外层 receiver 的 AUTO claim。

`report_loop_progress` 写入有界的 `LOOP_PROGRESS_REPORTED` 可观测事件，不参与 Graph FSM、不续租。摘要、里程碑和下一步使用用户当前语言。`graph_status` 与会先推进租约的 `graph_frontier` 返回 `progressMonitor`：结构化行用于宿主监控，`markdownTable` 汇总 attempt、外层 receiver、当前阶段、摘要、里程碑、下一步、测试、心跳/租约和健康预警。内部 Worker 遥测只在最终 outcome 中显示为非权威信息。
- 恢复：`advance_graph`、`rebuild_graph_run`
- 终态：`record_user_confirmation`、`cancel_graph_run`

Plugin MCP 工具不接收业务 `root` 参数。Adapter 从宿主配置或请求元数据解析项目根，再通过 `ControllerContext` 注入；Python 领域函数的 `root` 仅供 Controller 注入和测试。

`prepare_hierarchy.inputSchema` 由 `hierarchy_contract.py` 的 schema v3 生成器直接构建，以 `oneOf` 暴露 GROUP/TASK 两种根节点，并保持所有结构对象闭合、仅 `loop.payload` 开放。`validate_tool_arguments` 在调用 Controller 前复用 `validate_hierarchy_definition` 完成跨字段语义预检，将 hierarchy 契约错误统一包装为 `MCP_TOOL_ARGUMENT_INVALID`；Controller 内部校验继续作为非 MCP 调用的防御边界。

`loop_context.completionPolicy` 明确输入和终态边界：payload 是目标、明确约束和已知验收点的输入，Loop 在运行时从真实代码、契约和数据链路推导 scope 内必要条件；冻结 Graph 不冻结内部实现计划，可修复 finding 必须在当前 Loop 内调整方案、修正并复验。`loop_context.projectScopes` 是按当前 Delivery workspace 与冻结 Git binding 验证后的实际 worktree 列表，`projectScopeAnchors` 才是 hierarchy 中不可变的 preview 路径；这两个层次分离后，并行 Delivery 的 receiver 不会回到同仓库主检出互相切分支。Loop 不拥有分支生命周期，只能在有效 scope 路径内开发。STANDARD 执行完整声明验收并保留分层 Review；LIGHT 对已声明改动做定向验证，并在实际内容或影响超出判断依据时以 `REPLAN_REQUIRED` 退出，不能继续借轻量档绕过 Review。初始 freeze 同时为所有 TASK 建立 revision 1 冻结记录；`unfreeze_task_requirement` 只接受未开始 TASK，`refreeze_task_requirement` 只替换标题、摘要和 payload，并原子更新 hierarchy/graph 双指纹、事件链和人类投影。`record_loop_result` 的 `BLOCKED` 要求显式 failure class，只用于当前 scope 和权限内没有继续路径的真实终态；调度器仍不解释不透明 finding，也不为返工创建 Graph 环。

`controller.py` 是唯一共享应用入口；`mcp_tools.py` 把 29 个模型可调用工具映射到 Controller。每个工具发布人类可读 `title`、根对象 `inputSchema` / `outputSchema`，以及完整 `readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint` annotations；发布校验会逐工具检查。receiver 凭证签发和硬额度熔断是模型外宿主回调，不进入 MCP 工具目录。`mcp_adapter.py` 负责协议、精确启动 Adapter 身份与宿主策略。Controller 不扫描 PATH、不执行本机 CLI 版本探针，也不提供 Agent/模型发现或中央设置工具。

`dispatch_planning.py` 内置 `maxConcurrentExecutors=4` 与固定 `quotaExhaustionPolicy=PAUSE_AND_RESUME`，不读取 Plugin 外的用户配置。它只消费当前 frontier、可信 `host_adapter_id` 和宿主可原生创建的 receiver Agent 集合；assignment 固定 `modelPolicy=CURRENT_HOST_INHERIT`，模型与 effort 不进入 planning、reservation、claim 或 decision fingerprint。预留和已认领 receiver 共同占用协调槽位，容量断路器继续跨 Delivery 生效。Claude Code receiver 消费绑定 node/attempt/context/reservation 的一次性 attestation；Codex assignment 返回唯一 `hostTaskName`，`SubagentStart` Hook 校验 child/parent/task 后在单一事务内签发身份、固定编排根、消费 reservation 并 claim。后续 heartbeat、progress、pause 和 result 由 PreToolUse Hook 为同一 receiver 注入 operation；普通 root/helper 与内部 Worker统一拒绝。前一 Loop 成功且无活跃 claim/凭据时，同一 Adapter 的新主会话可用 `IDLE_FRONTIER_HANDOFF` 接力下一层 Review，活跃 claim 期间仍禁止轮换。长时间 shell/build 必须与 heartbeat 解耦；`SUSPECT_LOST` 只表示控制面静默。Codex、Claude、Grok、DeepSeek 等内部 Worker 只把结果返回 receiver，并可在最终 `workerTelemetry` 中按 phase 非权威报告 agent/model/effort。

Codex 默认清单 `hooks/hooks.json` 只注册 `SubagentStart` 与 Loop mutation `PreToolUse`，通过 `${PLUGIN_ROOT}`（Windows 为 `%PLUGIN_ROOT%`）定位；不注册 Codex 不支持的 `StopFailure`。Claude manifest 单独指向 `hooks/claude-hooks.json`，其 `PreToolUse` 与 `StopFailure` 通过 `${CLAUDE_PLUGIN_ROOT}` 定位。Codex Hook 只接受默认 `~/.codex/sessions` 中与实际 child、parent 和 assignment `hostTaskName` 相符的 transcript，并要求 active/manual Delivery 与精确预留；普通 helper、内部 Worker、过期预留和重放均无控制面权限。transcript 内部格式不是稳定协议，因此解析失败必须 fail closed，并在每个目标 Codex 版本做真实子 Agent 冒烟。

提供方限额按容量范围分流。软阈值只有宿主提供结构化 utilization/reset 时才提前暂停；标准 Claude CLI Hook 未暴露预警事件时不从模型文本猜测。硬 429 由模型外私有回调处理：Claude `StopFailure` 只解析 `error_details`，校验实际子 Agent 上下文，限制 reset 最远 24 小时并用 report ID 幂等去重。共享 `host_capacity_breakers` 会暂停同容量域的跨 Delivery claimed Loop并禁止新派遣；到点后 `advance_graph` 恢复同一 attempt。重建旧 run 时，恢复操作必须匹配原 `reportId/resetAt`，打开操作只有同一未恢复报告或更晚 `reportedAt` 才能更新共享行，不能覆盖另一 Delivery 的新断路器。该能力不在 MCP 工具目录中，失败模型无需也不能主动触发。

本地 Tools-over-stdio profile 的协议优先级为：

1. `2026-07-28`：`server/discover`、每请求 `_meta`、无协议会话、`resultType`、`ttlMs/cacheScope`；
2. `2025-11-25`：Claude Code 与旧 Codex 使用的 `initialize` 会话。

现代请求必须包含 `io.modelcontextprotocol/protocolVersion` 和 `io.modelcontextprotocol/clientCapabilities`；不支持的版本返回 `-32022` 及有序支持列表。旧初始化只协商 `2025-11-25`。MCP Tasks 是可选扩展，当前不广告也不实现；Controller 的持久状态继续使用显式 Graph/Loop 标识。

## 构建

`python scripts/build_skill.py`：

1. 将 `src/hdg` 复制到 canonical Skill runtime；
2. 删除 CLI 入口；
3. 生成 `hdg_mcp.py`；
4. 将 canonical Skill 整体复制到双宿主 Plugin payload。

canonical Skill 位于 `skills/delivery-graph/`，Plugin 位于 `plugins/delivery-graph/`。Plugin manifest 与 Hook 不在 canonical Skill 内，由仓库直接维护。

## 版本原则

- 只维护完整 schema v3；
- 不增加旧字段兼容；
- Delivery 保持顶层交付与验收边界，不增加 `DELIVERY` work item kind；
- 工作项只使用递归 `GROUP` / `TASK`，不恢复固定三层结构；
- 不恢复 CLI；
- 外层新增字段必须能证明是调度所必需；
- 实现内容优先放入具体 Loop，而不是 `delivery-graph` 外层控制面。
