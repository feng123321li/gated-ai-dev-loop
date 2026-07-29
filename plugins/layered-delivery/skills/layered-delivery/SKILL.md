---
name: layered-delivery
description: "调度或恢复多项目、多模块的软件交付 Graph。用于把需求组织成 Task Loop、Capability/Delivery Join、Review Loop 与最终用户确认；只治理依赖、资源声明、租约、重试和标准 Loop 结果，不规定实现计划、文件 scope、测试、门禁或内部 Skill 流程。"
---

# Layered Delivery

把本 Skill 当作外层 Graph Scheduler。不要把它当作开发方法、代码规范或 Gate 实现。

## 边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入；不要直接修改 SQLite 或投影。
- 以 SQLite 与事件链为机器权威；Markdown/JSON 文件只是可读投影。
- 只使用 schema v3。调用 `hierarchy_contract` 取得当前精确结构，不从源码或旧会话猜 schema。
- 不解释或约束 `loop.payload` 和 `loop.result`。实现方案、测试、Gate、修正循环及 Skill 调用属于相应 Task Loop。
- 用户给出的 Skill 只登记为 hierarchy 顶层共享 `skillHints`。它们是运行时优先提示，不是必选项、阶段门禁或 Task 绑定；具体 Loop 在启动后根据真实上下文发现并优先触发适用提示。
- 不使用文件 scope 做调度授权。`resourceClaims` 是精确排他锁键，可表达项目、模块、数据库或外部环境，例如 `project:erp/module:order`。
- 不把内部 `GATE_FAILED`、`TASK_IMPLEMENTED` 或 Skill 生命周期事件提升为外层 Graph 事件。Loop 只返回 `SUCCEEDED`、`BLOCKED`、`REPLAN_REQUIRED` 或 `CANCELLED`。
- 仅对 `RETRYABLE_INFRA` 与 `WORKER_LOST` 自动重试。业务阻断、契约变化与外部权限交给 frontier。
- 最终完成必须取得真实用户确认。Git、发布、迁移和新增外部权限继续单独授权。

## 入口

1. 调用 `workspace_status`。
2. `ACTIVE`、`BLOCKED` 或 `PAUSED`：读取 [execution-quickstart.md](references/execution-quickstart.md)，从 `graph_frontier` 恢复。
3. `ABSENT` 或 `PREPARED` 且用户要求新交付：读取 [planning-quickstart.md](references/planning-quickstart.md)。
4. 只读分析、代码审查或问答不创建调度状态。

## 调度循环

1. 持续调用 `graph_frontier`，按顺序消费所有 action。
2. 对 `DISPATCH_LOOP`，读取 `loop_context`，在真实执行容量可用时调用 `dispatch_loop`。
3. 将 `loop.ref`、不透明 `payload` 和共享 `skillHints` 交给该 Loop 的执行适配器。
4. Loop 先识别当前任务和宿主可用 Skill，再优先原生触发适用提示；可以跳过不适用提示，也可以按实际需要使用其他 Skill。不同节点可以作出不同选择。
5. 长运行在租约到期前调用 `heartbeat_loop`。
6. 只把 Loop 的标准终态提交给 `record_loop_result`；不要把内部步骤映射成外层节点。
7. 继续消费 frontier，直到阻断、需要重新规划或需要最终用户确认。

## 恢复

- MCP 响应未知时先重新读取 `workspace_status`、`graph_status` 和 `graph_frontier`，不要盲目重放写操作。
- 租约过期与基础设施失败交给 `advance_graph`；不要手工改 attempt。
- 物化状态损坏时用 `rebuild_graph_run` 从已校验事件链重建；不要改事件。
- Loop 要求改变外层依赖、资源声明或拓扑时，记录 `REPLAN_REQUIRED` 并回到新的人工评审，不在原冻结图中暗改。

## 按需参考

- 新图的层级、Loop 描述和一次冻结：[planning-quickstart.md](references/planning-quickstart.md)
- frontier、资源锁、租约、结果和恢复：[execution-quickstart.md](references/execution-quickstart.md)
- Review Loop 与最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连与项目根绑定：[mcp-transport.md](references/mcp-transport.md)
