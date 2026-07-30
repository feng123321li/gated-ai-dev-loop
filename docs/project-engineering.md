# 项目实现结构

## 源码

```text
src/hdg/
├── agent_discovery.py # 本机终端 Agent、当前模型与用户 Profile 只读发现
├── agent_recommendation.py
│                      # TASK/Review 非绑定 Agent + Model 建议
├── loop_contracts.py   # Loop descriptor、outcome、资源锁
├── model_core.py       # schema v3 Delivery 与递归 GROUP/TASK 校验
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
| `hierarchies` | Delivery 交付信息、递归 GROUP/TASK hierarchy、graph、指纹与冻结状态 |
| `runs` | 整体运行状态 |
| `node_runs` | 每个节点的 attempt、claim、lease 和 outcome |
| `graph_events` | 带前序哈希的不可变调度事件 |

Loop payload/outcome 以不透明 JSON 保存。共享 `root.skillHints` 作为 hierarchy 输入原样持久化，并由 `loop_context` 在运行时交给各 TASK、TASK Review、递归 GROUP Review 和 Delivery Review Loop；数据库没有 Task-Skill 分配、文件 scope、开发计划、Gate evidence 或 Skill activation 表。

## Hierarchy 与 Graph

Hierarchy 最外层只有两个入口：

```text
hierarchy
├─ delivery            # Graph/run 身份、交付摘要、最终 Review Loop
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
│                   └── interfaces.md  # 按需
└── d-portal/
    ├── overview.md
    ├── baseline.md
    ├── progress.md
    └── acceptance.md
```

`scheduler.db` 是唯一机器权威；各 `<delivery-id>` 目录只保存可从数据库状态重建的中文人类投影，不再复制 hierarchy、Graph 或运行状态 JSON。目录命名使用不可变的 `delivery.id` 和节点 ID，不使用可修改标题；同一工作区可以保留多个 Delivery 需求目录。`work-items/<root-id>/children/...` 镜像逻辑父子关系，但不表达 `dependsOn`、文件 scope 或调度授权；根 TASK 不增加 GROUP 目录。

工作区根 `overview.md` 只列 Delivery 标识、标题、状态、更新时间和详情；Delivery `overview.md` 才展示本交付的 TASK 完成度、GROUP 数量与导航。顶层 `baseline.md` 保存基线树和节点链接，`progress.md` 聚合运行进展，`acceptance.md` 只完整展示 Delivery 本层 Review 与用户确认，并以摘要和链接串联根工作项报告。每个 GROUP/TASK 在递归节点目录下拥有自己的 baseline、progress 和 acceptance；GROUP baseline 链接直接子节点，TASK baseline 展示冻结 Loop 输入。TASK 验收只展开本 TASK 与 TASK Review；GROUP 验收只展开本层完成点与 Review，对直接子节点只显示状态、简要结果和验收链接。任何下层输入、证据或 Review findings 都不向上重复复制。progress 状态、acceptance 摘要、子节点结果和 Review P0/P1/P2 问题使用表格。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md`，展示 `CREATE` / `MODIFY` / `DELETE` 的完整 before/after 契约；`protocol` 为开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等只是示例，通用协议可用 `identifier` 定位。无声明时不生成。代码可辅助提取和校验，但不是动态投影源。所有文件绑定双指纹并可随权威状态重建；`workspace_status` 会为早期 schema v3 Delivery 补建当前适用的投影树，但不迁移数据库或 Graph。所有固定文案和状态保持中文，标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`；机器权威仍使用 UTC。

## MCP

工具分为六组：

- 发现与建议：`available_agents`、`recommend_executors`
- 规划：`workspace_status`、`hierarchy_contract`、`prepare_hierarchy`、`freeze_hierarchy`
- 查询：`graph_frontier`、`graph_status`、`graph_events`、`loop_context`
- Loop 控制：`dispatch_loop`、`heartbeat_loop`、`pause_loop`、`resume_loop`、`record_loop_result`
- 恢复：`advance_graph`、`rebuild_graph_run`
- 终态：`record_user_confirmation`、`cancel_graph_run`

Plugin MCP 工具不接收业务 `root` 参数。Adapter 从宿主配置或请求元数据解析项目根，再通过 `ControllerContext` 注入；Python 领域函数的 `root` 仅供 Controller 注入和测试。

`loop_context.completionPolicy` 明确输入和终态边界：payload 是目标、明确约束和已知验收点的输入，Loop 在运行时从真实代码、契约和数据链路推导 scope 内必要条件；冻结 Graph 不冻结内部实现计划，可修复 finding 必须在当前 Loop 内调整方案、修正并复验。`record_loop_result` 的 `BLOCKED` 要求显式 failure class，只用于当前 scope 和权限内没有继续路径的真实终态；调度器仍不解释不透明 finding，也不为返工创建 Graph 环。

`controller.py` 是唯一共享应用入口；它只接受 `ControllerContext` 和 operation 参数，不导入 MCP、Codex 或 Claude 代码。`mcp_tools.py` 把 19 个工具 schema 映射到 Controller，`mcp_adapter.py` 负责协议结果、错误、版本协商和宿主策略，`mcp_server.py` 只处理 newline-delimited stdio 与进程生命周期。

`agent_discovery.py` 只读取 PATH、终端 `--version`、非敏感模型字段和用户本地 Profile，不启动开发命令或返回绝对路径、凭据与服务地址。`agent_recommendation.py` 只按 `TASK_LOOP`/Review 角色、显式 Profile 优先级、可用性和上游开发 Agent 多样性排序；不读取 Loop payload，不持久化结果，也不调用 `dispatch_loop`。因此 CC-Switch 或本机配置变化无需重建 Frozen Graph。

提供方限额按容量范围分流。单个执行 Agent 受限时，`pause_loop(..., capacity_scope=EXECUTOR)` 持久化真实 `resetAt`，frontier 允许临时排除该 Agent 后动态推荐其他执行者；调度宿主自身受限时，`capacity_scope=HOST` 只产生 `WAIT_FOR_HOST_CAPACITY` 和 `nextWakeAt`。后者必须由 MCP/模型之外的宿主适配器捕获限额、记录暂停并注册定时器，因为 Controller 只在被调用时推进 Graph，不能在模型已不可用时自行启动进程。

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
