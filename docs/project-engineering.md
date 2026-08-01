# 项目实现结构

## 源码

```text
src/hdg/
├── agent_discovery.py # 本机终端 Agent、当前模型与用户 Profile 只读发现
├── agent_recommendation.py
│                      # TASK/Review 非绑定 Agent + Model 建议
├── dispatch_contracts.py
│                      # 自动派遣决策指纹与策略版本
├── dispatch_planning.py
│                      # 宿主原生容量、模型覆盖与并发批次规划
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
| `hierarchies` | Delivery 当前 Revision 的项目/Git scope、递归 GROUP/TASK hierarchy、graph、指纹与冻结状态 |
| `delivery_revisions` | 同一 Delivery 的不可变 Revision 定义、连续性依据、原因、项目授权、执行模式和冻结/取代时间 |
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
├─ delivery            # Graph/run 身份、交付摘要、Git binding、最终 Review
│  ├─ gitBinding?      # 主工作区 feature/base/fork commit/integration target
│  └─ projectScopes?   # 多仓库 root/access/gitBinding；可写仓库同名分支
└─ root
   ├─ schemaVersion
   ├─ skillHints
   ├─ definition       # GROUP 或 TASK
   ├─ reviewLoop       # TASK/GROUP 均必填
   └─ children         # GROUP 可递归包含 GROUP/TASK，TASK 为空
```

嵌套节点不重复 `schemaVersion` 和 `skillHints` 包装字段。Delivery 不是 work item kind；`model_core.py` 只接受 `GROUP` 与 `TASK` 定义。

Graph 编译遵循以下终态规则：

- TASK 依次通过 `TASK_LOOP` 和 `TASK_REVIEW_LOOP`，Review 成功才是终态；
- GROUP 等待全部直接子节点终态，依次通过 `GROUP_JOIN`（GROUP 完成点）和 `GROUP_REVIEW_LOOP`；
- 父 GROUP 只消费子 GROUP Review 后的终态；
- 根终态进入 `DELIVERY_REVIEW_LOOP`，最后进入一次 `USER_CONFIRMATION`。

兄弟 `dependsOn` 是启动屏障。若依赖源是 GROUP，目标子树要等待源 GROUP 的 Review 成功，而不是只等待其 Join。

## 运行包

递归 hierarchy 会镜像为递归 GROUP/TASK 人类投影目录，但不改变 SQLite 机器权威。每个受治理工作区按稳定的 Delivery ID 保存多组投影：

```text
.layered-delivery/
├── scheduler.db
├── d-order/
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

`scheduler.db` 是唯一机器权威；各 `<delivery-id>` 目录只保存可重建的人类投影。稳定 `delivery.id` 下可以有多个不可变 Revision；`revisions.md` 默认展示当前 Revision 并串联旧 run 的 `SUPERSEDED` 审计状态。新用户需求不能因为当前路径恢复了旧 Delivery 就隐式进入其 Revision；`prepare_delivery_revision` 必须提交 `USER_EXPLICIT_SAME_DELIVERY`，或在已有 `REPLAN_REQUIRED` 事件时提交 `ACTIVE_LOOP_REPLAN`。候选只写 `delivery_revisions`，不会移动 `hierarchies` 当前指针，旧 run 直到候选 freeze 时才被原子取代。同一 `workspaceKey` 已有未结束 run 时，第二个 Delivery 在 `prepare` 写入前即被拒绝并返回 `CREATE_INDEPENDENT_WORKTREE_TASK`，避免留下无法冻结或迁移的 PREPARED 状态。`projectScopes` 允许一个需求覆盖多个本地仓库，冻结时要求精确项目 ID 授权；所有可写 Git 项目使用同名 feature 分支，但分别绑定自己的主线与 `baseCommit`。TASK Agent 不创建内部分支；Git stage/commit/push 仍需各自授权。`work-items/<root-id>/children/...` 镜像逻辑父子关系，但不表达文件授权。

工作区根 `overview.md` 只列 Delivery 标识、标题、状态、更新时间和详情；Delivery `overview.md` 才展示本交付的 TASK 完成度、GROUP 数量与导航。顶层 `baseline.md` 保存基线树和节点链接，`progress.md` 聚合运行进展，`acceptance.md` 只完整展示 Delivery 本层 Review 与用户确认，并以摘要和链接串联根工作项报告。每个 GROUP/TASK 在递归节点目录下拥有自己的 baseline、progress 和 acceptance；GROUP baseline 链接直接子节点，TASK baseline 展示冻结 Loop 输入。TASK 验收只展开本 TASK 与 TASK Review；GROUP 验收只展开本层完成点与 Review，对直接子节点只显示状态、简要结果和验收链接。任何下层输入、证据或 Review findings 都不向上重复复制。progress 状态表显示 claim 事件记录的实际执行代理、执行模型、认领身份和执行轮次；acceptance 摘要、子节点结果和 Review P0/P1/P2 问题使用表格。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md` 索引和 `interfaces/` 下每接口一份详情。完整 before/after 契约会被确定性比较：入参表展示类型、必填和说明，出参表不展示必填；删除值使用 Markdown 删除线，新增或删除字段只显示存在的一侧，真正修改的属性才使用“修改前 → 修改后”。`protocol` 为开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等只是示例，通用协议可用 `identifier` 定位。无声明时不生成。代码可辅助提取和校验，但不是动态投影源。所有文件绑定双指纹并可随权威状态重建；`workspace_status` 会为早期 schema v3 Delivery 补建当前适用的投影树，但不迁移数据库或 Graph。所有固定文案和状态保持中文，标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`；机器权威仍使用 UTC。

## MCP

工具分为六组：

- 发现、建议与派遣计划：`available_agents`、`recommend_executors`、`plan_dispatch_batch`
- 规划：`workspace_status`、`hierarchy_contract`、`prepare_hierarchy`、`freeze_hierarchy`
- Delivery 修订：`delivery_revision_history`、`prepare_delivery_revision`
- 需求修订：`unfreeze_task_requirement`、`refreeze_task_requirement`
- 查询：`graph_frontier`、`graph_status`、`graph_events`、`loop_context`
- Loop 控制：`dispatch_loop`、`heartbeat_loop`、`pause_loop`、`resume_loop`、`record_loop_result`
- 恢复：`advance_graph`、`rebuild_graph_run`
- 终态：`record_user_confirmation`、`cancel_graph_run`

Plugin MCP 工具不接收业务 `root` 参数。Adapter 从宿主配置或请求元数据解析项目根，再通过 `ControllerContext` 注入；Python 领域函数的 `root` 仅供 Controller 注入和测试。

`prepare_hierarchy.inputSchema` 由 `hierarchy_contract.py` 的 schema v3 生成器直接构建，以 `oneOf` 暴露 GROUP/TASK 两种根节点，并保持所有结构对象闭合、仅 `loop.payload` 开放。`validate_tool_arguments` 在调用 Controller 前复用 `validate_hierarchy_definition` 完成跨字段语义预检，将 hierarchy 契约错误统一包装为 `MCP_TOOL_ARGUMENT_INVALID`；Controller 内部校验继续作为非 MCP 调用的防御边界。

`loop_context.completionPolicy` 明确输入和终态边界：payload 是目标、明确约束和已知验收点的输入，Loop 在运行时从真实代码、契约和数据链路推导 scope 内必要条件；冻结 Graph 不冻结内部实现计划，可修复 finding 必须在当前 Loop 内调整方案、修正并复验。初始 freeze 同时为所有 TASK 建立 revision 1 冻结记录；`unfreeze_task_requirement` 只接受未开始 TASK，`refreeze_task_requirement` 只替换标题、摘要和 payload，并原子更新 hierarchy/graph 双指纹、事件链和人类投影。`record_loop_result` 的 `BLOCKED` 要求显式 failure class，只用于当前 scope 和权限内没有继续路径的真实终态；调度器仍不解释不透明 finding，也不为返工创建 Graph 环。

`controller.py` 是唯一共享应用入口；`mcp_tools.py` 把 24 个模型可调用工具映射到 Controller。接收凭证签发和硬额度熔断是模型外宿主回调，不进入 MCP 工具目录。`mcp_adapter.py` 负责协议、精确启动适配器身份与宿主策略。

`agent_discovery.py` 只读取 PATH、终端 `--version`、非敏感模型字段和用户本地 Profile，不启动开发命令或返回绝对路径、凭据与服务地址。`agent_recommendation.py` 只按 `TASK_LOOP`/Review 角色、显式 Profile 优先级、可用性和上游开发 Agent 多样性排序；发现候选固定标记为 `LOCAL_TERMINAL / EXTERNAL_PROCESS / hostDispatchEligible=false`，不读取 Loop payload，不持久化结果，也不调用 `dispatch_loop`。因此 CC-Switch 或本机配置变化无需重建 Frozen Graph。

`dispatch_planning.py` 与终端发现分离：它只消费宿主显式提交且标为 `HOST_NATIVE` 的原生 Agent inventory、当前 frontier、临时 `node_requirements` 和可选当前执行器事实。可信原生 Agent 集合来自 MCP Server 启动配置的精确适配器 ID；协议 `clientInfo` 不参与授权，缺失配置时自动派遣 fail closed。计划预留和已认领 Loop 共同占用跨 Delivery Agent 槽位，直到 Loop 暂停或终态才释放。Claude Code 接收方必须消费 PreToolUse Hook 签发并直接绑定 node/attempt/context/reservation 的一次性 attestation。Codex assignment 返回唯一 `hostTaskName`，`SubagentStart` Hook 通过 Codex transcript 校验 child/parent/task/model 后，在单一数据库事务内签发并消费内部身份、固定成功编排根、消费 reservation 和写入唯一 claim；child 只收到不含 receiver/operation bearer 的 assignment。后续 heartbeat、pause 和 result 由共享 PreToolUse Hook 注入 operation：Codex 依据当前 child transcript，Claude 依据真实 `agent_id` 与 consumed attestation；普通 root/helper 或缺少宿主身份的工具调用统一拒绝。后续直接、多级子上下文或首次引入另一平台 adapter 都不能另建信任根。跨平台派遣只有两个原生 adapter 能证明同一宿主编排根时才继续，否则 fail closed。Review 再校验该 attested context 不复用上游上下文。

Codex Plugin 通过约定的 `hooks/hooks.json` 注册 `SubagentStart` 与 Loop mutation `PreToolUse` Hook，并由 manifest 的 MCP 启动环境声明可信 `codex` adapter；同一 Hook bundle 继续承载 Claude Code 的 PreToolUse 与 StopFailure 回调。Hook 不信任调用方环境里的会话目录，只接受操作系统账户默认 `~/.codex/sessions` 中与实际 child、parent 和 assignment `hostTaskName` 相符的 transcript，并要求绑定工作区存在 active Delivery、精确预留与实际模型匹配。校验成功后 claim 的权威状态在一个事务提交；内部一次性身份、operation 均不写入 `SubagentStart.additionalContext`。重复 Hook 可从已提交 identity 幂等恢复 assignment，投影后处理失败不撤销权威 claim。覆盖 `CODEX_HOME`、普通 helper、工作区伪造 transcript、错误模型、过期预留、手动 claim 和重放均得不到正常工具路径授权。Codex 会按 Hook 内容哈希要求用户审阅和信任，未信任或宿主版本不支持相应事件时不会降级为模型自证，预留保持未认领。该校验遵循 Codex Hook 的 guardrail 信任边界，不等同于恶意编排 Agent 的密码学宿主证明；手工调用 Hook/控制器或改写 transcript/SQLite 需要宿主不可伪造的 caller-context 才能进一步防御。若需支持自定义 Codex home，也必须由宿主提供调用方不可伪造的会话根。

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

Plugin manifest 与 Hook 不在 canonical Skill 内，由仓库直接维护。

## 版本原则

- 只维护完整 schema v3；
- 不增加旧字段兼容；
- Delivery 保持顶层交付与验收边界，不增加 `DELIVERY` work item kind；
- 工作项只使用递归 `GROUP` / `TASK`，不恢复固定三层结构；
- 不恢复 CLI；
- 外层新增字段必须能证明是调度所必需；
- 实现内容优先放入具体 Loop，而不是 layered-delivery。
