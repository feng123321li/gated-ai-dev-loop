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
├── operations.py
├── mcp_tools.py
└── mcp_server.py
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

Plugin MCP 固定项目根，不接收 root 参数。Python 应用层函数的 `root` 仅供 Server 注入和测试。

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
