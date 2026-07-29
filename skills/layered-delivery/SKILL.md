---
name: layered-delivery
description: "调度或恢复多项目、多模块的软件交付 Graph。用于把交付需求组织为递归 GROUP/TASK、逐级 GROUP Review、Delivery Review 与最终用户确认；只治理依赖、资源声明、租约、重试和标准 Loop 结果，不规定实现计划、文件 scope、测试、门禁或内部 Skill 流程。"
---

# Layered Delivery

把本 Skill 当作外层 Graph Scheduler。不要把它当作开发方法、代码规范或 Gate 实现。

## 边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 只从 MCP 响应读取调度状态；不要通过 Shell、Python 或其他连接直接打开、查询或修改 `scheduler.db`。
- 以 SQLite 与事件链为机器权威。Markdown/JSON 只是控制器用固定版本模板生成的人类投影；MCP 提交的 hierarchy、summary 和 payload 会作为领域数据进入投影，但不要选择模板或投影文件名，也不要自行拼装、创建、修补或重写投影。
- 只使用 schema v3。调用 `hierarchy_contract` 取得当前精确结构，不从源码或旧会话猜 schema。
- 把 Delivery 作为 Graph 与最终验收边界；递归 GROUP 只协调子图，TASK 是唯一执行叶子。
- 不解释或约束 `loop.payload` 和 `loop.result`。实现方案、测试、Gate、修正循环及 Skill 调用属于相应 TASK 或 Review Loop。
- 用户给出的 Skill 只登记为 `root.skillHints`。它们对整张 Graph 共享，是运行时优先提示，不是必选项、阶段门禁或 TASK 绑定；具体 Loop 在启动后根据真实上下文发现并优先触发适用提示。
- 不使用文件 scope 做调度授权。`resourceClaims` 是精确排他锁键，可表达项目、模块、数据库或外部环境，例如 `project:erp/module:order`。
- 不把内部 `GATE_FAILED`、`TASK_IMPLEMENTED` 或 Skill 生命周期事件提升为外层 Graph 事件。Loop 只返回 `SUCCEEDED`、`BLOCKED`、`REPLAN_REQUIRED` 或 `CANCELLED`。
- 仅对 `RETRYABLE_INFRA` 与 `WORKER_LOST` 自动重试。业务阻断、契约变化与外部权限交给 frontier。
- 最终完成必须取得真实用户确认。Git、发布、迁移和新增外部权限继续单独授权。

## 入口

1. 调用 `workspace_status`。
2. `ACTIVE`、`BLOCKED` 或 `PAUSED`：读取 [execution-quickstart.md](references/execution-quickstart.md)，从 `graph_frontier` 恢复。
3. `ABSENT` 或 `PREPARED` 且用户要求新交付：读取 [planning-quickstart.md](references/planning-quickstart.md)。
4. `COMPLETED` 或 `CANCELLED`：用户要求新 Delivery 时读取规划说明；否则只报告终态，不写入新的调度状态。
5. 只读分析、代码审查或问答不创建调度状态。

## 调度循环

1. 持续调用 `graph_frontier`，按顺序消费所有 action。
2. 对 `DISPATCH_LOOP`，读取 `loop_context`，在真实执行容量可用时调用 `dispatch_loop`。
3. 将 `loop.ref`、不透明 `payload` 和共享 `skillHints` 交给该 Loop 的执行适配器。
4. Loop 先识别当前任务和宿主可用 Skill，再优先原生触发适用提示；可以跳过不适用提示，也可以按实际需要使用其他 Skill。不同节点可以作出不同选择。
5. TASK Loop 自主管理实现；GROUP Review 和 Delivery Review Loop 自主管理审查。`GROUP_JOIN` 由调度器在直接子节点终态齐备后推进，不派发实现工作。
6. 长运行在租约到期前调用 `heartbeat_loop`。
7. 只把 Loop 的标准终态提交给 `record_loop_result`；不要把内部步骤映射成外层节点。
8. 继续消费 frontier。每个 GROUP Review 成功后才成为父 GROUP 可消费的终态；根终态再进入 Delivery Review 和最终用户确认。

## 恢复

- MCP 响应未知时先重新读取 `workspace_status`、`graph_status` 和 `graph_frontier`，不要盲目重放写操作。
- 租约过期与基础设施失败交给 `advance_graph`；不要手工改 attempt。
- 物化状态损坏时用 `rebuild_graph_run` 从已校验事件链重建；不要改事件。
- Loop 要求改变外层依赖、资源声明或拓扑时，记录 `REPLAN_REQUIRED` 并回到新的人工评审，不在原冻结图中暗改。

## 按需参考

- 新图的层级、Loop 描述和一次冻结：[planning-quickstart.md](references/planning-quickstart.md)
- frontier、资源锁、租约、结果和恢复：[execution-quickstart.md](references/execution-quickstart.md)
- 递归 GROUP Review、Delivery Review 与最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连与项目根绑定：[mcp-transport.md](references/mcp-transport.md)
