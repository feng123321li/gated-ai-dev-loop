---
name: layered-delivery
description: "调度或恢复多项目、多模块的软件交付 Graph。用于把交付需求组织为递归 GROUP/TASK、必需 TASK Review、逐层 GROUP Review、Delivery Review 与最终用户确认；只治理依赖、资源声明、租约、重试和标准 Loop 结果，不规定实现计划、文件 scope、测试、门禁或内部 Skill 流程。"
---

# Layered Delivery

把本 Skill 当作外层 Graph Scheduler。不要把它当作开发方法、代码规范或 Gate 实现。

## 边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 只从 MCP 响应读取调度状态；不要通过 Shell、Python 或其他连接直接打开、查询或修改 `scheduler.db`。
- 以 SQLite 与事件链为唯一机器权威，不生成 `hierarchy.json`、`graph.json` 或 `state.json` 副本。根级全部 Delivery 总览、每个 Delivery 的 overview/baseline/progress/acceptance，以及从 `work-items/<root-id>/` 开始按 `children/<child-id>/` 递归展开的每个 GROUP/TASK 投影，是控制器生成的中文人类视图。GROUP 可多层、平行或完全省略；根为 TASK 时直接使用 `work-items/<task-id>/`。Delivery baseline 串联全部节点 baseline，GROUP baseline 串联直接子节点。验收投影严格分层：TASK 只报告本 TASK 与 TASK Review；GROUP 只完整报告本层完成点与 GROUP Review，对直接子节点仅给出状态、简要结果和报告链接；Delivery 只完整报告 Delivery Review 与用户确认，对根工作项仅给出状态、简要结果和报告链接。下层输入、证据和 Review findings 不向上复制。只有 TASK 显式声明 `payload.interfaces` 时才在该 TASK 目录生成 before/after 接口投影；无声明时不扫描代码或自动推断。进度、状态摘要、子节点验收和 Review 问题使用表格，长输入与证据保持结构化列表；所有标明 UTC+8 的时间使用 `YYYY-MM-DD HH:mm:ss`。MCP 提交的 hierarchy、summary 和 payload 会作为领域数据进入投影，但不要选择模板或投影文件名，也不要自行拼装、创建、修补或重写投影。
- 只使用 schema v3。调用 `hierarchy_contract` 取得当前精确结构，不从源码或旧会话猜 schema。
- 把 Delivery 作为 Graph 与最终验收边界；GROUP 只在存在真实的依赖、并行汇合或分层整体审查边界时使用，可递归也可完全省略；TASK 是唯一执行叶子，每个 TASK 必须配置 `reviewLoop` 并在 TASK Loop 后独立审查。每个已创建 GROUP 也必须配置 `reviewLoop`，在子结果齐备后完成本层整体审查；不要用只有一个 TASK 的 GROUP 制造形式层级。
- 不解释或约束 `loop.payload` 和 `loop.result`。实现方案、测试、Gate、修正循环及 Skill 调用属于相应 TASK 或 Review Loop。
- 需求包含接口契约时，按 `hierarchy_contract.projectionGuidance.interfaces` 在负责该接口的 TASK `payload.interfaces` 中显式提供协议、接口名、简介、调用标识、入参与出参；`protocol` 是开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等仅为示例。这只驱动固定的人类接口投影，不参与 Graph 调度判断。
- 冻结的是外层目标、依赖、资源声明和拓扑，不冻结 Loop 内部实现计划。payload 提供目标、明确约束和已知验收点，不是完整实现规约或工程正确性的穷举清单；Loop 必须结合真实代码、契约和数据链路推导当前 scope 的必要条件。可识别、可修复的正确性、数据完整性、边界与回归问题都由当前 Loop 自行调整方案并闭环。
- 用户给出的 Skill 只登记为 `root.skillHints`。它们对整张 Graph 共享，是运行时优先提示，不是必选项、阶段门禁或 TASK 绑定；具体 Loop 在启动后根据真实上下文发现并优先触发适用提示。
- `available_agents` 与 `recommend_executors` 只提供当前主机的动态发现和建议。建议不进入 schema v3、Frozen Graph、SQLite、claim 或 owner，工具自身不启动 CLI、切换模型或派遣 Loop；自动执行模式的总调度器可以消费建议并通过宿主原生 Agent 创建接收上下文。提供方限额恢复不调用推荐器，也不自动换 Agent。
- 不使用文件 scope 做调度授权。`resourceClaims` 是精确排他锁键，可表达项目、模块、数据库或外部环境，例如 `project:erp/module:order`。
- 不把内部 `GATE_FAILED`、`TASK_IMPLEMENTED`、可修复 Review finding 或 Skill 生命周期事件提升为外层 Graph 事件。Loop 只返回 `SUCCEEDED`、`BLOCKED`、`REPLAN_REQUIRED` 或 `CANCELLED`；`BLOCKED` 仅表示在当前 scope 和权限内已经没有继续路径，不是“Review 未通过”。
- 仅对 `RETRYABLE_INFRA` 与 `WORKER_LOST` 自动重试。业务阻断、契约变化与外部权限交给 frontier。
- 最终完成必须取得真实用户确认。Git、发布、迁移和新增外部权限继续单独授权。
- `owner`、`confirmed_by` 等调度身份使用控制器接受的可移植 ASCII 标识；具体字符约束以 MCP 契约和错误响应为准，不把运行经验写入宿主记忆来替代正式契约。
- 准备完成后只展示“自动执行 / 手动交接”两个确认开发选项，并提示用户可直接回复修改意见；不要把自由输入呈现为第三个选项。只有明确选择自动或手动才构成完整冻结授权；其他回复不冻结，只有需求实际变化时才重新 prepare。确认后立即调用由宿主自动批准的 `freeze_hierarchy`，不要追加通用 Yes/No，也不要发送内部 `confirmed` 参数。
- 总调度上下文只消费 frontier 和路由 Loop，不在自身上下文内实现 TASK 或 Review。每个 Loop 使用独立接收上下文；宿主支持 Agent 时优先自动派遣，无可用执行容量时才生成人工交接。
- 严格区分执行容量状态：未 claim 且无 Agent 容量时只生成人工交接；已 claim、租约有效且出现上下文或 Hook 压力时调用普通 `pause_loop`；宿主报告剩余额度不高于 5% 且给出真实未来 `resetAt` 时，当前 Agent 使用 `EXECUTOR` 或 `HOST` 定时 pause，并在额度耗尽前注册宿主原生的一次性恢复提示；直接收到 429 时不补建定时任务，只做人工恢复；租约过期时调用 `advance_graph`，禁止 pause。容量交接和限额等待都不提交 Loop outcome。

## 入口

1. 调用 `workspace_status`。
2. `ACTIVE`、`BLOCKED` 或 `PAUSED`：读取 [execution-quickstart.md](references/execution-quickstart.md)，从 `graph_frontier` 恢复；需要展示当前执行建议时同时读取 [agent-recommendations.md](references/agent-recommendations.md)。
3. `PREPARED`：读取 [planning-quickstart.md](references/planning-quickstart.md) 的准备结果续接规则；需求未变时保留当前准备结果，不重复 prepare，并可刷新 `available_agents` 与 `recommend_executors`，但不得据此改变已准备的 hierarchy。
4. `ABSENT`：用户要求新交付时读取规划说明；否则不创建调度状态。
5. `COMPLETED` 或 `CANCELLED`：用户要求新 Delivery 时读取规划说明；否则只报告终态，不写入新的调度状态，不触发宿主记忆、持续学习或项目文件更新。`workspace_status` 可由控制器幂等补建缺失的固定人类投影；这不改变 SQLite、事件链或 Graph 终态。
6. 只读分析、代码审查或问答不创建调度状态。

## 调度循环

1. 持续调用 `graph_frontier`，完整消费当前批次的所有 action；容量允许时立即分别派遣同批互不冲突的 `DISPATCH_LOOP`，不等待前一个 Loop 完成。
2. 对需要展示执行建议的当前 Graph，调用 `available_agents` 和 `recommend_executors`；只转述对应节点的 Agent、当前模型、置信度、备选和原因，不把建议当成 claim、owner、模型切换、外部 CLI 调用授权或限额恢复机制。
3. 对 `DISPATCH_LOOP`，优先用宿主原生 Agent 创建独立接收上下文，只交付 `rootId/nodeId`；接收方通过 MCP 调用 `loop_context` 和 `dispatch_loop`，不要复制规划会话或由总调度上下文内联执行。
4. 无可用 Agent 容量时才输出人工交接，且不要提前 claim；未 claim 的 Loop 由接收方直接读取 frontier 后 dispatch，已暂停的 Loop 按 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT` 恢复。
5. 接收方从 `loop_context` 获取 `loop.ref`、不透明 `payload`、共享 `skillHints`、TASK baseline 路径、固定 `completionPolicy` 和 `executionPolicy`。
6. Loop 先识别当前任务和宿主可用 Skill，再优先原生触发适用提示；可以跳过不适用提示，也可以按实际需要使用其他 Skill。不同节点可以作出不同选择。
7. TASK Loop 自主管理实现；TASK Review、递归 GROUP Review 和 Delivery Review Loop 自主管理独立发现、修正协调和复审。Review 把每项问题分类为 P0/P1/P2；P0、P1 必须留在同一 Review Loop 内完成修正、验证和独立复审，全部关闭后才可返回 `SUCCEEDED`；P2 不阻断成功，但必须逐项保留在 `result.reviewFindings` 并进入验收投影。每个 GROUP 的机器节点 `GROUP_JOIN` 是控制器自动推进的 GROUP 完成点，不派发实现工作；完成点之后必须进入该层 GROUP Review。
8. 长运行在租约到期前调用 `heartbeat_loop`；只有租约仍有效时，上下文容量压力或高轮次 Hook 摩擦才使用普通 pause/handoff。宿主报告剩余额度不高于 5% 且提供真实 `resetAt` 时，在额度耗尽前完成定时 pause；执行 Agent 使用 `capacity_scope=EXECUTOR`，调度宿主使用 `capacity_scope=HOST`。不得猜测剩余额度或恢复时间。
9. 对 `WAIT_FOR_EXECUTOR_CAPACITY` 或 `WAIT_FOR_HOST_CAPACITY` 不调用推荐器、不自动换 Agent。Claude Code 2.1.72+ 在当前会话注册一次性 Cron 并保持 CLI 运行；Codex Desktop 在当前任务注册计划提示并保持电脑和应用运行。恢复提示在 `resetAt` 后保留安全余量，到时由原 Agent 调用 `workspace_status`、`graph_frontier` 和 `loop_context` 后重新 dispatch。宿主不支持原生计划、宿主被关闭、计划创建失败或直接收到 429 时，只做人工恢复。控制器只在 frontier 被再次调用时推进，不会自行唤醒 Agent。
10. 只把 Loop 的真实业务终态提交给 `record_loop_result`；可修复 finding、内部 Gate 失败、容量交接和限额等待都不产生 Loop outcome。`BLOCKED` 必须显式提供 failure class，并且只能用于当前 scope/权限内无继续路径的具体条件。
11. 继续消费 frontier。TASK Review 成功后 TASK 才成为可消费终态；每层 GROUP 在完成点之后必须经自己的 GROUP Review 成功，才成为父 GROUP 可消费的终态。根终态再进入 Delivery Review。出现 `RECORD_USER_CONFIRMATION` 时读取 [acceptance.md](references/acceptance.md)，等待真实用户最终确认。

## 恢复

- MCP 写响应未知时先读取 `workspace_status`；仅当状态表明冻结 run 已存在时再读取 `graph_status` 和 `graph_frontier`。`ABSENT` 或 `PREPARED` 按规划恢复，不要调用尚不可用的运行工具或盲目重放写操作。
- `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把 `rootId/nodeId` 路由给新的独立接收上下文；接收方调用 `resume_loop`，再从 frontier 取得新的 dispatch，不重新 prepare/freeze。
- `WAIT_FOR_EXECUTOR_CAPACITY`：原执行 Agent 等待宿主原生一次性恢复提示；到时重新消费 frontier，控制器恢复同一 attempt。
- `WAIT_FOR_HOST_CAPACITY`：总调度 Agent 等待宿主原生一次性恢复提示；到时重新消费 frontier。宿主原生计划不可用或已经直接收到 429 时，等待人工恢复。
- 租约过期与基础设施失败交给 `advance_graph`；过期 operation 不得 heartbeat、pause 或提交结果，也不要手工改 attempt。
- 物化状态损坏时用 `rebuild_graph_run` 从已校验事件链重建；不要改事件。
- Loop 要求改变外层依赖、资源声明或拓扑时，记录 `REPLAN_REQUIRED`，不在原冻结图中暗改。frontier 返回 `REPLAN_HIERARCHY` 后先展示原因并等待用户决定；只有用户明确授权取消当前 run，才调用 `cancel_graph_run`，再使用新的 `delivery.id` prepare 替代图并重新评审、冻结。

## 按需参考

- 新图的层级、Loop 描述和一次冻结：[planning-quickstart.md](references/planning-quickstart.md)
- 本机 Agent/模型发现、建议原因与本地 Profile：[agent-recommendations.md](references/agent-recommendations.md)
- frontier、资源锁、租约、结果和恢复：[execution-quickstart.md](references/execution-quickstart.md)
- TASK Review、递归 GROUP Review、Delivery Review 与最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连与项目根绑定：[mcp-transport.md](references/mcp-transport.md)
