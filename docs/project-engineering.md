# 项目实现结构

## 源码

```text
src/hdg/
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

Loop payload/outcome 以不透明 JSON 保存。共享 `root.skillHints` 作为 hierarchy 输入原样持久化，并由 `loop_context` 在运行时交给各 TASK、GROUP Review 和 Delivery Review Loop；数据库没有 Task-Skill 分配、文件 scope、开发计划、Gate evidence 或 Skill activation 表。

## Hierarchy 与 Graph

Hierarchy 最外层只有两个入口：

```text
hierarchy
├─ delivery            # Graph/run 身份、交付摘要、最终 Review Loop
└─ root
   ├─ schemaVersion
   ├─ skillHints
   ├─ definition       # GROUP 或 TASK
   ├─ reviewLoop       # GROUP 必填，TASK 为 null
   └─ children         # GROUP 可递归包含 GROUP/TASK，TASK 为空
```

嵌套节点不重复 `schemaVersion` 和 `skillHints` 包装字段。Delivery 不是 work item kind；`model_core.py` 只接受 `GROUP` 与 `TASK` 定义。

Graph 编译遵循以下终态规则：

- TASK 终态是 `TASK_LOOP`；
- GROUP 等待全部直接子节点终态，依次通过 `GROUP_JOIN` 和 `GROUP_REVIEW_LOOP`；
- 父 GROUP 只消费子 GROUP Review 后的终态；
- 根终态进入 `DELIVERY_REVIEW_LOOP`，最后进入一次 `USER_CONFIRMATION`。

兄弟 `dependsOn` 是启动屏障。若依赖源是 GROUP，目标子树要等待源 GROUP 的 Review 成功，而不是只等待其 Join。

## 运行包

递归 hierarchy 不映射为递归 GROUP/TASK 文件目录。每个受治理工作区共享一个 SQLite 权威，并按稳定的 Delivery ID 保存多组投影：

```text
.layered-delivery/
├── scheduler.db
├── d-order/
│   ├── hierarchy.json
│   ├── graph.json
│   ├── state.json
│   └── overview.md
└── d-portal/
    ├── hierarchy.json
    ├── graph.json
    ├── state.json
    └── overview.md
```

`scheduler.db` 是机器权威；各 `<delivery-id>` 目录中的四个文件可从数据库状态重建。目录命名使用不可变的 `delivery.id`，不使用可修改标题；同一工作区可以保留多个 Delivery 需求目录。GROUP/TASK 的逻辑父子关系保存在 hierarchy 和 Graph 中，不通过下一层目录名或文件 scope 表达。

`overview.md` 不是简化标题清单，而是冻结与运行状态的人类控制面：它绑定双指纹，提供完整 GROUP/TASK 清单和节点详情，并随状态变化刷新。所有人类时间显示为 UTC+8；机器权威仍使用 UTC。

## MCP

工具分为五组：

- 规划：`workspace_status`、`hierarchy_contract`、`prepare_hierarchy`、`freeze_hierarchy`
- 查询：`graph_frontier`、`graph_status`、`graph_events`、`loop_context`
- Loop 控制：`dispatch_loop`、`heartbeat_loop`、`pause_loop`、`resume_loop`、`record_loop_result`
- 恢复：`advance_graph`、`rebuild_graph_run`
- 终态：`record_user_confirmation`、`cancel_graph_run`

Plugin MCP 工具不接收业务 `root` 参数。Adapter 从宿主配置或请求元数据解析项目根，再通过 `ControllerContext` 注入；Python 领域函数的 `root` 仅供 Controller 注入和测试。

`controller.py` 是唯一共享应用入口；它只接受 `ControllerContext` 和 operation 参数，不导入 MCP、Codex 或 Claude 代码。`mcp_tools.py` 把 17 个工具 schema 映射到 Controller，`mcp_adapter.py` 负责协议结果、错误、版本协商和宿主策略，`mcp_server.py` 只处理 newline-delimited stdio 与进程生命周期。

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
