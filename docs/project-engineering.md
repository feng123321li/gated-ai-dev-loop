# 分层交付 Graph 控制面：项目实现结构

项目、Plugin 与 Skill 的 canonical 机器名为 `delivery-graph`。`.layered-delivery/` 是已有 schema v3 Delivery 的稳定数据目录，为避免破坏恢复链路不随产品名更改。

## 源码

```text
src/hdg/
├── dispatch_contracts.py
│                      # 外层 receiver 派遣决策指纹与策略版本
├── dispatch_planning.py
│                      # 当前宿主 receiver 预留与并发批次规划
├── agent_profiles.py   # 版本化专用 receiver/helper Profile Catalog
├── entry_routing.py    # 入口文本与持久状态的确定性路由
├── timing.py           # 可选 Controller/stage 结构化计时
├── supervisor_profiles.py
│                      # 可选无工具、只作决策的 Supervisor 配置
├── loop_contracts.py   # Loop descriptor、outcome、资源锁
├── review_contracts.py # Controller 对 Review 结果的机械结构/终态一致性校验
├── result_contracts.py # TASK scope/evidence 完整性门禁
├── result_ledger.py    # 全 Loop 结果账本与确定性 Delivery 组装
├── execution_metrics.py
│                      # 含全部 attempt 的耗时、关键路径与慢 Loop 指标
├── model_core.py       # schema v3 Delivery 与递归 GROUP/TASK 校验
├── git_binding.py      # Git worktree/feature/mainline 只读发现与校验
├── graph_model.py      # GROUP Join/可选 seam Review、Delivery Acceptance/Readiness、DAG 与 FSM
├── repository.py       # SQLite repository 兼容 façade
├── repository_*.py    # workspace、Revision、事件、预留、选择与投影 stores/mixins
├── planning.py         # prepare / freeze / workspace status façade
├── planning_*.py      # 规划、baseline 门禁、交互与 workspace 职责模块
├── graph_frontier.py   # 下一步调度动作
├── graph_runtime.py    # claim、lease、结果、重试、恢复
├── hierarchy_contract.py
├── model_rendering.py  # Delivery/层级总览渲染 façade
├── model_rendering_*.py
│                      # overview、baseline、acceptance 等确定性渲染模块
├── controller.py       # 协议无关的共享应用 Controller
├── operations.py       # 旧 Python 公共导入面的薄兼容 façade
├── host_policy.py      # Codex 项目根与 Claude 审批兼容策略
├── mcp_tools.py        # MCP 工具 schema 与 Controller 参数适配
├── mcp_adapter.py      # 2026-07-28 / legacy 双栈 JSON-RPC
└── mcp_server.py       # stdio framing、输入限制与进程入口
```

旧的 `acceptance.py`、`execution.py`、`remediation.py`、`skill_execution.py` 和 evidence hydration 已删除，因为这些职责属于内部 Task Loop 或已收敛到外层 scheduler。repository 职责则已拆到 `repository_*.py`，`repository.py` 只保留兼容 façade。

## 数据库

`.layered-delivery/scheduler.db` 包含：

| 表 | 内容 |
|---|---|
| `scheduler_metadata` | 当前 schema v3 Graph 生成契约标识；不兼容控制器不得共同写同一数据库 |
| `hierarchies` | Delivery 当前 Revision 的项目/Git scope、递归 GROUP/TASK hierarchy、graph、指纹，以及 `HANDOFF_READY/PREPARED/FROZEN/ARCHIVED` envelope 状态；归档后 run 仍保持 `COMPLETED` |
| `delivery_revisions` | 自动 Revision 与手动冻结快照的定义、连续性依据、原因、项目授权、执行模式和冻结/取代时间 |
| `delivery_preferences` | 每个 Delivery 已确认的单仓开发基线偏好，用于后续 Revision 缺省注入；多 Git scope 仍要求逐仓显式 binding |
| `dispatch_reservations` | 宿主创建 Agent 前的短租约派遣票据；按 run/node/attempt 原子去重、绑定决策指纹并原子预留跨 Delivery Agent 槽位 |
| `delivery_workspaces` | 多个 Delivery 与实际物理 checkout 身份的绑定；Graph 状态按 `rootId` 隔离，同一 checkout 的执行 turn 仍严格串行 |
| `runs` | 整体运行状态、冻结执行模式与宿主容量熔断 |
| `node_runs` | 每个节点的 attempt、claim、lease 和 outcome |
| `task_requirement_states` | 每个 TASK 当前 requirement revision、冻结/解冻状态与更新时间 |
| `graph_events` | 带前序哈希的不可变调度事件 |

`SchedulerRepository` 只保留 SQLite 连接/事务、共享定义校验与兼容 facade。workspace 绑定、执行模式与选择、hierarchy/revision/run 生命周期、Graph 事件状态、dispatch reservation 以及人类投影分别由 `repository_workspaces.py`、`repository_execution_setup.py`、`repository_execution_selection.py`、`repository_hierarchies.py`、`repository_events.py`、`repository_event_projection_facade.py`、`repository_dispatch.py` 和 `repository_projections.py` 管理。各 store/mixin 复用同一事务连接与 SQLite schema，不引入第二套状态，也不改变外部方法签名。

TASK Loop payload/outcome 以不透明 JSON 保存；成功 Review outcome 只允许本层结论、findings、有界证据元数据和 Controller 快照，不复制 `upstreamLoopResults` 或下层 result body。共享 `root.skillHints` 作为 hierarchy 输入原样持久化，并由 `loop_context` 在运行时交给各 TASK、TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness Loop；数据库没有 Task-Skill 分配、文件 scope、开发计划、Gate evidence 或 Skill activation 表。

Controller、Review receiver 与用户是三个独立职责。Controller 只管理 Graph 状态迁移、前驱成功门禁、Review result 契约校验以及事件/SQLite/投影持久化；该校验只证明结构和 receiver 声明的终态相容，不证明技术结论为真。Review receiver 独立判断当前层验收、证据充分性和 finding 闭环，其中 Delivery receiver 每个 `STANDARD` Delivery 只负责一次顶层 Acceptance/Readiness，不逐个重验下层 Loop。用户只作最终业务确认，不能替代前两者。

## Hierarchy 与 Graph

Hierarchy 最外层只有两个入口：

```text
hierarchy
├─ delivery            # Graph/run 身份、保障档、交付摘要、Git binding
│  ├─ gitBinding?      # 主工作区 feature/base/fork commit/integration target
│  └─ projectScopes?   # 多仓库显式 root/access/gitBinding；省略时运行态合成 primary scope
└─ root
   ├─ schemaVersion
   ├─ skillHints
   ├─ definition       # GROUP 或 TASK
   ├─ reviewLoop       # STANDARD TASK 必填；GROUP 按直接子项 seam 可空；LIGHT 根 TASK 为 null
   └─ children         # GROUP 可递归包含 GROUP/TASK，TASK 为空
```

嵌套节点不重复 `schemaVersion` 和 `skillHints` 包装字段。Delivery 不是 work item kind；`model_core.py` 只接受 `GROUP` 与 `TASK` 定义。

保障档不再由 Agent 风险分类或推荐工具决定。默认使用 `STANDARD`；只有用户明确要求 `LIGHT` 且 hierarchy 满足单根 TASK、无独立 Review 的结构约束时才使用 `LIGHT`，并把用户选择与定向验证要求写入 `assuranceRationale`。Graph 编译遵循以下终态规则：

- STANDARD TASK 依次通过 `TASK_LOOP` 和 `TASK_REVIEW_LOOP`，Review 成功才是终态；
- GROUP 等待全部直接子节点终态并通过 `GROUP_JOIN`（GROUP 完成点）；只有存在真实直接子项 seam 时才继续 `GROUP_REVIEW_LOOP`；
- 父 GROUP 消费子 GROUP 的实际终态：有 seam Review 时是 Review，否则是完成点；
- STANDARD 根终态进入表示 Delivery Acceptance/Readiness 的 `DELIVERY_REVIEW_LOOP`，最后进入一次 `USER_CONFIRMATION`；
- LIGHT 只有 `TASK_LOOP → USER_CONFIRMATION`，执行中发现接口、数据、权限、安全、生产部署、跨模块影响或其他范围扩大时返回 `REPLAN_REQUIRED`，由同一 Delivery 的下一 Revision 升级为 STANDARD。

兄弟 `dependsOn` 是启动屏障。若依赖源是 GROUP，目标子树等待源 GROUP 的实际终态：已配置 seam Review 时等待 Review，否则等待 Join。

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

- 自动与手动路径共享 `.layered-delivery/<delivery-id>/`。手动冻结会把 Delivery、不可变 Revision、完整 hierarchy 与双 fingerprint 持久化到 SQLite，生成完整人类投影和额外的 `handoff-<fingerprint>.md`，内部状态登记为 `HANDOFF_READY`，并原子记录 MANUAL 选择与 workspace binding；已有 owner 时对外投影为统一 `QUEUED`，交接阶段不创建 run 或事件链。
- `start_manual_handoff` 只有在已绑定工作区取得串行 turn 且 Git binding 通过校验后才创建 `execution_mode=manual` 的 run。单仓漂移先返回 `DEVELOPMENT_BASELINE` 且不创建 Run：确认原 binding 时保持当前 Revision、要求恢复分支后重试；确认新 binding 时生成下一不可变手动 Revision与新双 fingerprint。多仓漂移 fail closed，要求以完整 project bindings 创建手动 Revision，不能用单仓选择器局部改写。旧版未绑定 `HANDOFF_READY` 仅允许明确 `rootId` 查询；当 hierarchy、节点和边与当前编译结果完全一致且无 Run 时，启动前只刷新版本化 runtime policy、Graph 编译协议与 graph fingerprint，不改变 hierarchy fingerprint 或 Revision。ACTIVE/FROZEN Graph 继续要求完整 graph fingerprint 精确匹配。
- 手动 run 只有 `TASK_LOOP` 可以 `dispatch_mode=MANUAL`；TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness 仍由外层 receiver 自动领取，最终仍需用户确认。MANUAL 授权来自 manual run 或指定自动 TASK 的人工接管事件；独立 child 显式提交 receiving context 与新的 `operation_id`，且不携带 AUTO reservation 或 decision fingerprint。Controller 继续校验 workspace/Git/project scope、attempt、operation、lease 和资源锁；progress 里程碑持久化为事件并只在 Agent/Dashboard 实时展示，acceptance 与关键状态继续由事件刷新 Markdown 投影。
- Revision 连续性必须显式：`prepare_delivery_revision` 使用 `USER_EXPLICIT_SAME_DELIVERY`，或在已有 `REPLAN_REQUIRED` 时使用 `ACTIVE_LOOP_REPLAN`。Delivery 为 `OPEN/未上线` 时，即使上一 Revision 已 `COMPLETED` 仍可追加；候选 freeze 前不移动当前 hierarchy，取代时活动旧 run 原子标记为 `SUPERSEDED`，已完成旧 run 保持 `COMPLETED`，仅旧 Revision scope 标记为 `SUPERSEDED`。`CLOSED/已上线交付` 后拒绝新 Revision。
- 同一个实际物理 workspace 可以绑定多个 Delivery，状态始终以显式 `rootId` 路由；执行策略只有 `CURRENT_WORKSPACE_SERIAL`。已有调度 owner 时，后启动的 AUTOMATIC 或 MANUAL Delivery 都持久记录为 `QUEUED` 并投影队列状态。`PAUSED`、Run 终态或 `RECORD_USER_CONFIRMATION` 只表示进入 release eligibility，不等于物理 turn 已释放；所有 READ_WRITE scope 必须在各自冻结的独立分支上形成 turn start 之后的可验证业务 commit，并保持 working tree/index clean、HEAD 与 binding 匹配且 receiver/reservation 安全收束。`CANCELLED` 且全部 scope 从 turn start 起零业务变化、clean、binding 匹配、HEAD 未移动时，以确定性零变化证据代替业务提交。Controller 原子复核全部 scope 并持久化 `WORKSPACE_TURN_RELEASED` 后，宿主才能切分支和续调队首。当前 Revision 经用户确认后进入 `COMPLETED`，但 Delivery 仍是 `OPEN/未上线`；此时生命周期 `nextAction` 是继续 Revision 或上线后关闭，Git 释放动作单独放在 `workspaceNextAction`。PAUSED turn 尚未释放且仍由本 Delivery 持有时可原地恢复；只有已经释放的暂停节点才重新排队并捕获新的 clean turn start。
- 多仓 `projectScopes` 在冻结时要求精确项目授权。每个 Git scope 都必须带完整 `gitBinding`；同一 Delivery 的可写仓库使用同名 feature 分支，但分别冻结自己的主线与 `baseCommit`。两种执行模式都在当前实际 workspace 串行准备全部 `READ_WRITE` scope；普通单仓 Delivery 可以不声明该数组，运行时会从顶层 `delivery.gitBinding` 和已绑定 workspace 合成并验证唯一 `primary` scope。TASK receiver 不创建或切换分支。

Controller 只读发现和校验 Git，不执行 branch、worktree、stash、stage、commit 或 push。`workspace_status.workspaceProvenance` 记录当前实际 checkout 的宿主、拓扑、`selectionSource`、`baseRef`、`baseCommit`、`baseHeadCommit` 与 `integrationTarget`；它只提供来源诊断，不选择并行策略。primary 与既有 linked checkout 使用完全相同的串行基线与分支 adoption 规则。只有未被其他 Delivery 使用且基线有效的 feature 分支才可 adoption。release handshake 响应以 `workspaceRelease=PENDING|RELEASED` 区分状态边界和物理释放；`PENDING` 时不能收到或执行 branch preparation，显式恢复同一 PAUSED Loop 且 turn 尚未释放时可原地 `resume_loop`，其他让路场景只执行响应的 `nextAction`。只有 `RELEASED` 才可消费 `automaticHostPreparation` / `manualHostPreparation`。随后宿主以明确 `rootId` 与原双 fingerprint 调用 `resume_execution_mode` 或 `start_manual_handoff`；stash 的 pathspec 排除 `.layered-delivery/**`，恢复使用 index 语义且不自动 pop。`CANCELLED` 的 owner 在同一安全边界独立释放，归档不参与解锁，未过期 receiver lease 继续 fail closed；终态查询不继续投影过期 `workspaceRebase`。缺少 binding 时，干净或脏工作树都先返回 `DEVELOPMENT_BASELINE`；仅 adoption 当前脏分支要求归属确认，选择其他分支把 dirty 处理延迟到队首准备。`stateFingerprint` 覆盖 porcelain、变化路径的 workspace blob 与 index state，任一状态变化都会使旧指纹失效。

工作区根 `overview.md` 分别列未归档 Delivery 的当前阶段与上线状态；Delivery `overview.md` 另展示本交付的 TASK 完成度、GROUP 数量与导航。`close_delivery` 只接受当前 `COMPLETED` run，以追加式 `DELIVERY_CLOSED` 事件把 Delivery 标为 `CLOSED/已上线交付`，不归档也不删除历史。`archive_delivery` 只接受已关闭 Delivery，把 hierarchy 与当前 Revision envelope 标为 `ARCHIVED`；它不删除 SQLite/事件链/详情投影，也不释放 `requirementKey`。显式 `workspace_status(root_id=...)` 与 revision history 仍可审计。根总览对每个未归档 Delivery 独立校验：无关 Delivery 损坏时只把该行标为“调度状态异常”，健康 Delivery 的 frontier、状态查询和投影刷新继续运行；直接访问损坏 Delivery 仍返回带实际 `rootId` 的完整性错误。其他 Delivery 的投影目录损坏或不可写时，显式当前 `rootId` 的 `workspace_status` 通过 `projectionIssues` 报告并继续。顶层 `baseline.md` 保存基线树和节点链接，`progress.md` 聚合关键运行状态快照且不嵌入实时监控表，`acceptance.md` 只完整展示 Delivery Acceptance/Readiness 与当前 Revision 完成确认，并以摘要和链接串联根工作项报告。每个 GROUP/TASK 在递归节点目录下拥有自己的 baseline、progress 和 acceptance；GROUP baseline 链接直接子节点，TASK baseline 展示冻结 Loop 输入。TASK 验收只展开本 TASK 与 `taskAcceptance`；GROUP 只展开完成点，配置 seam Review 时再展开 `groupIntegration`，否则不生成空 Review 表格；Delivery 只展开 `deliveryReadiness`。任何下层输入、result body、证据、workspace snapshot 或 Review findings 都不向上重复复制。未配置的 GROUP Review 不进入 graph、`node_runs`、`graph_events` 或投影。progress 状态表只显示 claim 事件记录的外层 receiver、认领身份和执行轮次。acceptance 摘要、子节点结果和 Review P0/P1/P2 问题使用表格。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md` 索引和 `interfaces/` 下每接口一份详情。完整 before/after 契约会被确定性比较：入参表展示类型、必填和说明，出参表不展示必填；删除值使用 Markdown 删除线，新增或删除字段只显示存在的一侧，真正修改的属性才使用“修改前 → 修改后”。`protocol` 为开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等只是示例，通用协议可用 `identifier` 定位。无声明时不生成。代码可辅助提取和校验，但不是动态投影源。所有文件绑定双指纹并可随权威状态重建；`workspace_status` 会为早期 schema v3 Delivery 补建当前适用的投影树，异常的其他 Delivery 通过 `projectionIssues` 报告，但不迁移数据库或 Graph。所有固定文案和状态保持中文，标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`；机器权威仍使用 UTC。

## MCP

工具分为八组：

- 入口与外层 receiver 派遣：`route_entry_intent` 先排除被否定的生命周期动作，再把唯一肯定入口文本与持久化状态合成为确定性路由；多个肯定动作失败关闭为 `AMBIGUOUS`。可选 decision-only Supervisor 默认关闭且无工具权限。`plan_dispatch_batch` 按当前宿主 Adapter、版本化 Agent Profile Catalog、profile 并发槽位和 frontier 直接预留 receiver，不接收模型或推理档位字段。

## 维护与性能边界

所有 `src/hdg/*.py` 与 `tests/*.py` 均由架构测试限制为不超过 900 行；兼容 façade 通过职责模块或 mixin 组合公共方法，不把实现重新复制回大文件。测试保留当前正向契约、失败关闭和数据完整性场景，删除只断言历史符号已经不存在的墓碑测试。

设置 `HDG_TIMING=1` 后，Controller 在 stderr 输出单行 `controller.timing` JSON，包含 operation、总耗时、stage 聚合与文件写入计数；默认关闭，stdout 与业务结果契约不变。仓库内 `scripts/benchmark_controller.py` 使用临时目录合成 schema v3 Delivery，量化入口 Router、prepare/freeze、workspace status 和 graph frontier 的均值、P95、最大值及预算。真实模型、Agent 和业务构建速度按[性能量化与真实项目验收](performance-validation.md)另行验证。
- 规划与交接：`workspace_status`、`hierarchy_contract`、`preview_hierarchy`、`confirm_development_baseline`、`select_execution_mode`、`resume_execution_mode`、`create_manual_handoff`、`start_manual_handoff`、`prepare_hierarchy`、`freeze_hierarchy`。`workspace_status(base_ref=...)` 可承接宿主明确选择的基线；未指定时按有效 `origin/HEAD`、本地 `main`、本地 `master` 降级发现。preview 先登记 `CHOICE_READY` 并生成关联投影，再返回唯一 `pendingInteraction`：缺 binding 时为 `DEVELOPMENT_BASELINE`，确认后为 `EXECUTION_MODE`。`developmentBaseline` / `executionChoice` 只是该对象的兼容别名。Codex 映射 `request_user_input`，Claude 映射 `AskUserQuestion`，可调用时必须使用原生选择器。AUTOMATIC 先持久化业务确认，再按 `CURRENT_WORKSPACE_SERIAL` 在当前实际 checkout 续接；手动 Git 漂移遵循上一节的单仓双分支和多仓 fail-closed 规则。
- Delivery 修订与关闭：`delivery_revision_history`、`prepare_delivery_revision`、`close_delivery`
- 需求修订：`unfreeze_task_requirement`、`refreeze_task_requirement`
- 查询：`graph_frontier`、`graph_status`、`delivery_result`、`graph_events`、`loop_context`、`open_delivery_dashboard`。`delivery_result` 用结果账本完整枚举所有 Loop，输出确定性验收/证据汇总，并把当前 Revision 的全部 retry/lost attempts 计入总耗时、关键路径和慢 Loop 指标；结果不完整时用户确认门禁失败关闭。
- Loop 控制：`dispatch_loop`、`handoff_ready_automatic_task`、`heartbeat_loop`、`report_loop_progress`、`pause_loop`、`resume_loop`、`record_loop_result`

公开的 `freeze_hierarchy` 不接收 `execution_mode`，自动路径固定创建 `active` run。只有 `start_manual_handoff` 能把精确 `HANDOFF_READY` 双 fingerprint 启动为 `manual` run；Git 漂移 blocker 在任何控制状态写入前返回。`dispatch_loop(MANUAL)` 通常只允许该 run 的 `TASK_LOOP`；唯一例外是 `handoff_ready_automatic_task` 已对 active Graph 中 READY、从未领取、clean 且无有效 reservation 的指定 TASK 记录显式人工恢复事件。两种 MANUAL claim 都拒绝 AUTO reservation、decision fingerprint、transport 和模型选择，要求独立 receiver 显式提交 receiving context 与新的 `operation_id`；Review 继续使用统一 AUTO reservation/decision/独立 child 协议。

`report_loop_progress` 写入有界的 `LOOP_PROGRESS_REPORTED` 可观测事件，不参与 Graph FSM、不续租，也不重写 `progress.md` 或其他 Markdown 投影；响应直接返回更新后的实时 `progressMonitor`。事件保留最新里程碑在进程重启、MCP 重连和 run 重建后的可恢复性；`progressMonitor` 本身不持久化。摘要、里程碑和下一步使用用户当前语言。`graph_status` 与会先推进租约的 `graph_frontier` 同样按读取时刻即时构造 `progressMonitor`：结构化行用于宿主监控，`markdownTable` 汇总 attempt、外层 receiver、当前阶段、摘要、里程碑、下一步、测试、心跳/租约和健康预警。内部 Worker 遥测只在最终 outcome 中显示为非权威信息。
- 恢复：`advance_graph`、`rebuild_graph_run`
- Run 终态与归档：`record_user_confirmation`、`cancel_graph_run`、`archive_delivery`

Plugin MCP 工具不接收业务 `root` 参数。Adapter 从宿主配置或请求元数据解析项目根，再通过 `ControllerContext` 注入；Python 领域函数的 `root` 仅供 Controller 注入和测试。

`prepare_hierarchy.inputSchema` 由 `hierarchy_contract.py` 的 schema v3 生成器直接构建，以 `oneOf` 暴露 GROUP/TASK 两种根节点，并保持所有结构对象闭合、仅 `loop.payload` 开放。`validate_tool_arguments` 在调用 Controller 前复用 `validate_hierarchy_definition` 完成跨字段语义预检，将 hierarchy 契约错误统一包装为 `MCP_TOOL_ARGUMENT_INVALID`；Controller 内部校验继续作为非 MCP 调用的防御边界。

`loop_context.completionPolicy` 明确输入和终态边界：payload 是目标、明确约束和已知验收点的输入，Loop 在运行时从真实代码、契约和数据链路推导 scope 内必要条件；冻结 Graph 不冻结内部实现计划，可修复 finding 必须在当前 Loop 内调整方案、修正并复验。TASK 的默认验证范围是实际变更影响面的最小充分 `affectedScopes`，并以有界 `verificationEvidence` 记录检查 scope 与结果；每个 scope 必须由至少一项 `PASSED` 证据引用，Controller 在结果写入和最终账本处双重失败关闭，并附加轻量 `evidenceWorkspaceSnapshots` 与逐相关路径的 `evidenceScopeSnapshots`。Review 使用 `EVIDENCE_FIRST_TARGETED_RERUN`：`validationEvidenceIndex` 把证据标为 `EXACT_MATCH/CHANGED/UNBOUND`，只自动复用匹配的通过证据；本层执行证据和验收 evidence refs 必须能解析到本层或显式复用记录。无关文件变化不使有界 scope 失效，相关路径变化才触发定向补验。Review 按 TASK 缺口、GROUP seam、Delivery 最终 smoke/E2E 证据分层补验；普通局部失效只重跑受影响范围，只有无法界定影响面等明确风险才全量复跑。为控制 Review context，传递上游 outcome 时只保留有界证据与状态元数据，不传递源码 diff 或补丁附件；需要内容时从授权 workspace 或对应提交读取。`loop_context.projectScopes` 是按当前 Delivery workspace 与冻结 Git binding 验证后的实际 workspace 列表；单仓未声明 `projectScopes` 时包含合成的 `primary`，多仓则逐项验证显式 scope。`projectScopeAnchors` 才是 hierarchy 中不可变的 preview 路径；当前实际 workspace 的不同 Delivery receiver 严格按 turn 串行，不能同时切换分支或写文件。Loop 不拥有分支生命周期，只能在有效 scope 路径内开发。STANDARD 执行完整声明验收并保留分层 Review；LIGHT 只来自用户明确选择，对已声明改动做定向验证，并在实际内容或影响超出判断依据时以 `REPLAN_REQUIRED` 退出，不能继续借轻量档绕过 Review。初始 freeze 同时为所有 TASK 建立 revision 1 冻结记录；`unfreeze_task_requirement` 只接受未开始 TASK，`refreeze_task_requirement` 只替换标题、摘要和 payload，并把确认后的完整定义冻结为同一 Delivery 的下一不可变 Revision。旧 Revision hierarchy/Graph 双指纹保持不变，新 Run、TASK requirement revision、事件链与人类投影使用新指纹一致重建。`record_loop_result` 的 `BLOCKED` 要求显式 failure class，只用于当前 scope 和权限内没有继续路径的真实终态；调度器仍不解释不透明 finding，也不为返工创建 Graph 环。

`controller.py` 是唯一共享应用入口；`mcp_tools.py` 把 35 个模型可调用工具映射到 Controller。`mcp_catalog.py` 把它们声明为 `planning`、`dispatch`、`receiver` 三个静态 Profile，并维护 Skill→Profile 路由；Profile 联集必须覆盖全部工具，planning/dispatch 只在 workspace 发现、入口路由和队列恢复上有明确重叠。Plugin 启动三个独立 stdio MCP 进程，它们使用同一运行包和 project root，因此共享同一个 `scheduler.db`，但 `tools/list` 只返回当前 Profile，`tools/call` 对目录外工具返回 `MCP_TOOL_OUTSIDE_PROFILE`。

每个工具发布人类可读 `title`、根对象 `inputSchema` / `outputSchema`，以及完整 `readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint` annotations；发布校验会逐工具检查。`mcp_adapter.py` 只保留 Modern discovery 与 Legacy initialize/ping 两层 wire shim、请求校验和调用分派；长工作流说明、Profile 集合、工具目录过滤与角色指令都在 `mcp_catalog.py`。初始化后的 tools/resources 方法共享一个 dispatcher，避免为两个协议各维护一套业务分支。Controller 不扫描 PATH、不执行本机 CLI 版本探针，也不提供 Agent/模型发现或中央设置工具。敏感调用是否执行由宿主审批；Plugin 不通过生命周期回调绕过或代替该审批。

`mcp_apps.py` 发布固定的 `ui://delivery-graph/dashboard-v2.html` Resource，`dashboard.py` 把当前定义、`graph_status` 与 Revision 历史投影成有界只读 view model。该投影删除 Loop payload、operation ID、Revision 原因和操作者等非展示字段；HTML 只通过 MCP Apps bridge 接收 `structuredContent`，不访问 SQLite、网络、Cookie 或宿主 DOM。看板可见时每 15 秒串行自动重读，隐藏时暂停，手动按钮可立即刷新；标准 `tools/call` 与 `window.openai.callTool` 兼容路径都只调用 `open_delivery_dashboard`，绝不调用会先推进状态的 `graph_frontier`。Codex modern MCP Apps 请求会保留协议 `_meta` 但可能缺少 `codex/sandbox-state-meta`，legacy 兼容 bridge 也可能完全省略 `_meta`；两种 wire shim 只能复用同一 MCP 连接上、此前带有效 sandbox metadata 且成功读取的精确 `root_id`/workspace grant。grant 仅限可信 Codex Adapter 与 Dashboard 工具，modern/legacy era 隔离，采用 5 分钟滑动 TTL、每连接最多 8 个 root，重新授权同一 root 时替换 workspace，连接关闭时全部撤销；跨连接、其他宿主、其他工具、未授权 root，以及 legacy 显式空或畸形 metadata 仍失败关闭。宽面板按 rank 绘制横向依赖边，空间不足时改为纵向换行并在节点内展示前置项。无 UI 宿主继续消费同一工具的文字/结构化降级结果。

`dispatch_planning.py` 内置全局 `maxConcurrentExecutors=4`，并允许业务项目用严格的 `delivery-graph.agents.json` 完整定义专用 receiver/helper profile、capabilities、输出契约和每 RECEIVER profile 并发上限；HELPER 由宿主在 owner 内部按需协调，不建立 Controller reservation 或独立并发计数。缺少文件时使用 Plugin 内置 catalog。它复用当前 frontier 状态快照，不再为 attempt/执行模式重复读取完整 run；无资源冲突的 frontier Loop 继续并行，EXACT_MATCH 证据继续复用。预留和已认领 receiver 共同占用协调槽位。`plan_dispatch_batch` 为 Ready TASK/Review 创建绑定 node、attempt、Graph、agent catalog、profile 和 team fingerprint 的短租约 reservation；宿主只创建 `teamPlan.owner` 外层 child，helper 永不取得控制面凭据。child 以 `dispatch_transport=HOST_NATIVE`、live reservation、匹配 decision fingerprint 和新的显式 `operation_id` 调用 `dispatch_loop(AUTO)`。MANUAL TASK 不进入 planning。后续 heartbeat、progress、pause 和 result 都显式携带 claim 对应的 operation；已安全释放 turn 的暂停节点在 `resume_loop` 时先重新排队，只有重获 workspace turn、冻结 binding/clean 复核通过并记录新 turn start 后才恢复 Ready，再以新 operation、reservation、fingerprint 和资源门禁重新领取。Controller 继续强制 workspace/Git/project scope、attempt、reservation、fingerprint、operation、lease 和资源锁。长时间 shell/build 必须与 heartbeat 解耦；`SUSPECT_LOST` 只表示控制面静默。

`progressMonitor.changeFingerprint` 只覆盖节点状态、业务进度、健康与告警等有意义变化，不包含观测时间、心跳年龄或租约倒计时。`waitDirective` 要求先消费当前立即 action，再使用宿主原生 receiver event 等待；`pollNotBefore` 直接取首次心跳、进度陈旧、失联或租约的下一个有意义健康阈值，不使用固定短周期轮询。截止时只读一次 `graph_status`，只有 receiver event、`nextWakeAt` 或 `ADVANCE_REQUIRED` 才推进 `graph_frontier`。这是一项宿主编排契约，不依赖 MCP resource subscription。

Codex 与 Claude Plugin 通过 Skill、Agent 描述、MCP 和宿主元数据工作，安装和升级不需要额外的生命周期信任。Codex manifest 对敏感 MCP 工具保留宿主 `approval_mode=prompt`，Claude Code 使用自身的工具审批，普通执行权限不能代替这些宿主决策。Adapter 提供当前 workspace 和 receiver 类型，Controller 再校验冻结 binding 与 project scope；workspace/scope 无效时在 claim 前 fail closed。Controller 不提供真实 parent-child、receiver 身份延续或 reviewer 独立性的密码学证明。AUTO 的安全边界是短租约 reservation、decision fingerprint、attempt、显式 operation、lease 与资源锁；MANUAL 的安全边界是允许人工领取的 TASK 状态、workspace/scope、显式 operation 与 lease。

提供方限额按容量范围分流。软阈值只有宿主提供结构化 utilization/reset 时才提前以当前 operation 调用 `pause_loop`；不从模型文本猜测。硬 429 不由 Plugin 自动记录，宿主负责停止供应商调用、保留真实 `resetAt` 并执行自己的审批/重试和一次性恢复计划；receiver 已无法显式 pause 时由 lease 到期和 `advance_graph` 进入新 attempt。

本地 Tools-over-stdio profile 的协议优先级为：

1. `2026-07-28`：`server/discover`、每请求 `_meta`、无协议会话、`resultType`、`ttlMs/cacheScope`；
2. `2025-11-25`：Claude Code 与旧 Codex 使用的 `initialize` 会话。

现代请求必须包含 `io.modelcontextprotocol/protocolVersion` 和 `io.modelcontextprotocol/clientCapabilities`；不支持的版本返回 `-32022` 及有序支持列表。旧初始化只协商 `2025-11-25`。两代协议都广告静态 `resources` 能力并提供同一个 Dashboard Resource；Modern 额外返回 complete/TTL/cache metadata。MCP Tasks 是可选扩展，当前不广告也不实现；Controller 的持久状态继续使用显式 Graph/Loop 标识。

## 构建

`python scripts/build_skill.py`：

1. 将 `src/hdg` 复制到 planning Skill 的共享 runtime；
2. 删除 CLI 入口；
3. 生成 `hdg_mcp.py`；
4. 将 planning/dispatch/task/review 四个 canonical Skill 复制到多宿主 Plugin payload。

四个 canonical Skill 位于 `skills/delivery-graph*/`，共享 runtime 只由 `skills/delivery-graph/` 携带；Plugin 位于 `plugins/delivery-graph/`。Plugin manifest 不在 canonical Skill 内，由仓库直接维护；当前 Plugin payload 由声明的 Skill、Agent 描述、三套 MCP Profile 和宿主元数据组成。

## 版本原则

- 只维护完整 schema v3；
- 不增加旧字段兼容；
- Delivery 保持顶层交付与验收边界，不增加 `DELIVERY` work item kind；
- 工作项只使用递归 `GROUP` / `TASK`，不恢复固定三层结构；
- 不恢复 CLI；
- 外层新增字段必须能证明是调度所必需；
- 实现内容优先放入具体 Loop，而不是 `delivery-graph` 外层控制面。
