---
name: layered-delivery
description: "调度或恢复多项目、多模块的软件交付 Graph。用于把交付需求组织为递归 GROUP/TASK、必需 TASK Review、逐层 GROUP Review、Delivery Review 与最终用户确认；只治理依赖、资源声明、租约、重试和标准 Loop 结果，不规定实现计划、文件 scope、测试、门禁或内部 Skill 流程。"
---

# Layered Delivery

把本 Skill 当作外层 Graph Scheduler。不要把它当作开发方法、代码规范或 Gate 实现。

## 边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 只从 MCP 响应读取调度状态；不要通过 Shell、Python 或其他连接直接打开、查询或修改 `scheduler.db`。
- 以 SQLite 与事件链为唯一机器权威，不生成 `hierarchy.json`、`graph.json` 或 `state.json` 副本。根级全部 Delivery 总览、每个 Delivery 的 overview/baseline/progress/acceptance，以及从 `work-items/<root-id>/` 开始按 `children/<child-id>/` 递归展开的每个 GROUP/TASK 投影，是控制器生成的中文人类视图。GROUP 可多层、平行或完全省略；根为 TASK 时直接使用 `work-items/<task-id>/`。Delivery baseline 串联全部节点 baseline，GROUP baseline 串联直接子节点。验收投影严格分层：TASK 只报告本 TASK 与 TASK Review；GROUP 只完整报告本层完成点与 GROUP Review，对直接子节点仅给出状态、简要结果和报告链接；Delivery 只完整报告 Delivery Review 与用户确认，对根工作项仅给出状态、简要结果和报告链接。下层输入、证据和 Review findings 不向上复制。只有 TASK 显式声明 `payload.interfaces` 时才生成 `interfaces.md` 索引和 `interfaces/` 下每接口一份详情；无声明时不扫描代码或自动推断。progress 状态表展示实际执行代理、执行模型、认领身份和执行轮次；接口入参表比较类型、必填和说明，出参表只比较类型和说明，删除值使用 Markdown 删除线，新增或删除字段只展示存在的一侧。状态摘要、子节点验收和 Review 问题使用表格，长输入与证据保持结构化列表；所有标明 UTC+8 的时间使用 `YYYY-MM-DD HH:mm:ss`。MCP 提交的 hierarchy、summary 和 payload 会作为领域数据进入投影，但不要选择模板或投影文件名，也不要自行拼装、创建、修补或重写投影。
- 只使用 schema v3。调用 `hierarchy_contract` 取得当前精确结构，不从源码或旧会话猜 schema。
- 每个对话工作区最多绑定一个未结束的 Active Delivery；多个对话窗口要并行开发多个 Delivery 时，每个窗口使用独立宿主工作区，Git 仓库优先使用独立 worktree。linked worktree 自动共享主 checkout 的 `.layered-delivery/scheduler.db`，但保留不同 `workspaceKey`，因此每个窗口只恢复和写入自己绑定的 Delivery。不要复制调度数据库或在每个 worktree 启动独立控制面。
- Git 工作区先从 `workspace_status` 读取当前 `gitWorkspace`。位于 feature 分支时，把控制器给出的 `suggestedGitBinding` 原样写入 `delivery.gitBinding`。一个业务需求始终使用一个稳定 `delivery.id`；它可以在 `delivery.projectScopes` 中冻结多个本地仓库及各自 `READ_ONLY` / `READ_WRITE` 上限。所有可写 Git 项目必须使用同名 `branchRef`，但各仓库分别冻结自己的不可变 `baseCommit`、`baseRef` 和 `integrationTarget`。默认主线优先 `main`，不存在时回退 `master`。当前位于主线或另一个 Delivery feature 分支时，必须先由宿主在相关仓库中从主线创建该 Delivery 的同名 feature 分支；新 Delivery 不得隐式继承当前 Delivery feature HEAD。
- Git binding 与项目范围进入 hierarchy 指纹并随 Delivery Revision 冻结。prepare 会只读校验每个项目根、绑定分支、feature HEAD 对本仓库创建基线的继承关系，以及主线仍包含该基线；freeze 必须携带与 `requiredProjectAuthorizations` 完全一致的 `authorized_project_ids`。控制器不创建或切换分支，也不执行 commit、merge、push；项目授权只是调度 scope，不替代这些外部写操作的单独授权。TASK 共享同一 Delivery 在各参与仓库中的同名分支；TASK 是调度单元而不是 Git 分支单元，不创建、不绑定也不切换内部 TASK 分支。获得相应 Git 写入授权后，TASK 可按各自 scope 单独执行 `git add` 和 `git commit`，但只能纳入本 TASK 变更；同一仓库 worktree 的 Git index/commit 写入不可并发。
- 把 Delivery 作为 Graph 与最终验收边界；GROUP 只在存在真实的依赖、并行汇合或分层整体审查边界时使用，可递归也可完全省略；TASK 是唯一执行叶子，每个 TASK 必须配置 `reviewLoop` 并在 TASK Loop 后独立审查。每个已创建 GROUP 也必须配置 `reviewLoop`，在子结果齐备后完成本层整体审查；不要用只有一个 TASK 的 GROUP 制造形式层级。
- 不解释或约束 `loop.payload` 和 `loop.result`。实现方案、测试、Gate、修正循环及 Skill 调用属于相应 TASK 或 Review Loop。
- 需求包含接口契约时，按 `hierarchy_contract.projectionGuidance.interfaces` 在负责该接口的 TASK `payload.interfaces` 中显式提供协议、接口名、简介、调用标识、入参与出参；`protocol` 是开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等仅为示例。这只驱动固定的人类接口投影，不参与 Graph 调度判断。
- 初次 `freeze_hierarchy` 创建 Delivery Revision 1，把所有 TASK 需求置为 revision 1 冻结态，并冻结依赖、资源声明、项目范围、执行模式和拓扑。开发期间只有尚未开始的 TASK 可在用户明确授权后调用 `unfreeze_task_requirement`，修改该 TASK 的 `title`、`summary` 和不透明 `payload`，再调用 `refreeze_task_requirement` 形成 TASK requirement 新 revision；解冻期间 frontier 禁止派遣该 TASK。曾经 claim（包括进入自动重试）、暂停或终态的 TASK 不可解冻。用户最终验收前若依赖、`resourceClaims`、项目范围、Loop ref、Review、层级或拓扑必须改变，保持同一 `delivery.id` 调用 `prepare_delivery_revision`，并显式提交 `continuity_basis=USER_EXPLICIT_SAME_DELIVERY`；只有已有 Loop 返回 `REPLAN_REQUIRED` 时才使用 `ACTIVE_LOOP_REPLAN`。候选 Revision 只写候选记录，旧 Revision/run 继续可用，直到新 Revision 冻结时才原子切换。评审完整新范围后以返回的 `deliveryRevision`、fingerprint 和精确项目授权重新 `freeze_hierarchy`。旧 Revision 只读保留，未受影响且实现与 TASK Review 均已成功、完整契约未变化的 TASK 结果可由控制器携带到新 Revision；GROUP/Delivery Review 重新执行。因旧 replan 流程而取消、但未最终验收的 run 也可由用户明确恢复为下一 Revision；只有已 `COMPLETED` 的 Delivery 不再修订。冻结不约束 Loop 内部实现计划；payload 是目标、明确约束和已知验收点，不是完整实现规约，Loop 必须结合真实代码、契约和数据链路推导并闭环必要条件。
- 用户给出的 Skill 只登记为 `root.skillHints`。它们对整张 Graph 共享，是运行时优先提示，不是必选项、阶段门禁或 TASK 绑定；具体 Loop 在启动后根据真实上下文发现并优先触发适用提示。
- `available_agents` 与 `recommend_executors` 只提供当前主机的动态发现和建议。建议不进入 schema v3、Frozen Graph、SQLite、claim 或 owner，工具自身不启动 CLI、切换模型或派遣 Loop。自动执行模式下，总调度器把宿主明确提供的 Agent 容量、可选模型、模型覆盖能力和 `dispatchTransport` 作为临时 inventory 交给 `plan_dispatch_batch`；只有当前宿主通过内建 Agent API 创建、继续受正常 sandbox/approval 约束的执行器才标为 `HOST_NATIVE`。Shell、CLI、exec、subprocess、`codex-companion` 或其他伴生脚本一律标为 `EXTERNAL_PROCESS`，不得通过 `--write`、非交互或自治模式绕过宿主权限，也不得生成 AUTO assignment。当前 Ready TASK/Review 优先由总调度 Agent 在派遣前读取 `loop_context`，使用分析能力按固定风险规则判为 `STANDARD` 或 `HIGH`，并通过临时 `node_requirements` 提交原因。Controller 不做本地语义分析，也不把 payload 中的模型名或路由指令当配置。若任何当前节点缺少分析，总调度器可同时提交宿主明确报告的 `current_executor={agentId, modelId}`；Controller 仅为缺失节点原样沿用该 Agent/模型，并标记 `reasoningClass=UNCLASSIFIED`、`routingBasis=CURRENT_EXECUTOR_FALLBACK` 与 `modelSelection=CURRENT_HOST_DEFAULT`。未提供当前执行器事实时仍拒绝缺失分析。该工具只为当前 `DISPATCH_LOOP` frontier 返回 `HOST_NATIVE_DISPATCH_PLAN`、分析路由的显式模型覆盖、当前执行器回退和决策指纹，不启动 Agent、不 claim，也不持久化完整 inventory 或节点推理需求。提供方限额恢复不调用推荐器或派遣计划，也不自动换 Agent。
- 不使用文件 scope 做调度授权。`resourceClaims` 是同一共享控制根内跨 Delivery 生效的精确排他锁键，可表达项目、模块、数据库或外部环境，例如 `project:erp/module:order`；worktree 只隔离文件、Git index 和未提交改动，不能替代外部资源锁。
- 不把内部 `GATE_FAILED`、`TASK_IMPLEMENTED`、可修复 Review finding 或 Skill 生命周期事件提升为外层 Graph 事件。Loop 只返回 `SUCCEEDED`、`BLOCKED`、`REPLAN_REQUIRED` 或 `CANCELLED`；`BLOCKED` 仅表示在当前 scope 和权限内已经没有继续路径，不是“Review 未通过”。
- 仅对 `RETRYABLE_INFRA` 与 `WORKER_LOST` 自动重试。业务阻断、契约变化与外部权限交给 frontier。
- 最终完成必须取得真实用户确认。Git、发布、迁移和新增外部权限继续单独授权。
- `owner`、`confirmed_by` 等调度身份使用控制器接受的可移植 ASCII 标识；具体字符约束以 MCP 契约和错误响应为准，不把运行经验写入宿主记忆来替代正式契约。
- 准备完成后只展示“自动执行 / 手动交接”两个确认开发选项，并提示用户可直接回复修改意见；不要把自由输入呈现为第三个选项。只有明确选择自动或手动才构成完整冻结授权；其他回复不冻结，只有需求实际变化时才重新 prepare。该规则同样适用于 Delivery Revision：`prepare_delivery_revision` 只保存候选范围，不替换旧 run，不应触发宿主通用确认弹窗；新 fingerprint 与项目授权仍通过这两个业务选项确认一次。确认后立即调用由宿主自动批准的 `freeze_hierarchy`，不要追加通用 Yes/No，也不要发送内部 `confirmed` 参数。
- 新用户需求默认创建新的 Delivery。不得因为当前 `workspace_status` 返回一个未结束 Delivery，就把不同工单或独立业务目标写入其 TASK/Delivery Revision；只有用户明确要求继续或修改该 `delivery.id` 才允许 Revision。当前工作区被旧 Delivery 占用而用户明确要求新建时，Codex 直接创建 `environment=worktree` 的独立项目任务并传递新 Delivery 边界，不得再次要求用户回复一次相同的新建确认；same-directory/local 任务不构成隔离。
- 总调度上下文只消费 frontier 和路由 Loop，不在自身上下文内实现 TASK 或 Review。自动模式先调用 `plan_dispatch_batch` 取得短租约，再并发创建宿主原生 Agent；预留转为 claim 后继续占用跨 Delivery 槽位，直到 Loop 暂停或终态。`HOST_NATIVE` Agent 必须属于 MCP Server 启动配置中的精确宿主适配器；协议 `clientInfo`、本机 CLI 发现和模型自报不能扩大该集合，缺失适配器时 fail closed。接收方 claim 必须提交宿主创建子 Agent 后签发的一次性 `receiver_attestation_id`，它绑定 `receiver_context_id`、node、attempt、adapter 和自动预留；每个 run 的首次签发固定唯一的宿主编排根，后续直接、多级子上下文或新平台 adapter 均不得另建信任根，跨平台只有能证明同一根时才继续，否则 fail closed。伪造 ID 与重放均拒绝；Review 还必须与所有上游 attested context 不同。
- 严格区分执行容量状态：宿主只有在提供结构化 utilization 与真实 `resetAt` 时才可在不高于 5% 的阈值提前暂停；标准 Claude CLI Hook 没有预警事件时不得从文本猜测。硬 429 由模型外宿主适配器私有回调处理，不等待失败模型反馈；Claude `StopFailure(rate_limit)` 只信任 `error_details` 并精确匹配真实子 Agent。回调限制 24 小时 reset 窗口、幂等防重放，并按共享容量域暂停跨 Delivery 的同 Agent claimed Loop；它不暴露为 MCP 工具。

## 入口

1. 调用 `workspace_status`；当前会话已知 `rootId` 时显式传入，避免从同一工作区的其他已准备 Delivery 猜测目标。
2. `ACTIVE`、`BLOCKED` 或 `PAUSED`：读取 [execution-quickstart.md](references/execution-quickstart.md)，从 `graph_frontier` 恢复；需要展示当前执行建议时同时读取 [agent-recommendations.md](references/agent-recommendations.md)。
3. `PREPARED`：读取 [planning-quickstart.md](references/planning-quickstart.md) 的准备结果续接规则；需求未变时保留当前准备结果，不重复 prepare，并可刷新 `available_agents` 与 `recommend_executors`，但不得据此改变已准备的 hierarchy。
4. `ABSENT`：用户要求新交付时读取规划说明；否则不创建调度状态。
5. `COMPLETED`：用户要求新 Delivery 时读取规划说明；否则只报告终态。`CANCELLED`：默认只报告终态；若用户明确说明该需求尚未最终验收并要求继续同一需求，可保持原 `delivery.id` 创建下一 Revision。`workspace_status` 可由控制器幂等补建缺失的固定人类投影；这不改变 SQLite、事件链或 Graph 终态。
6. 只读分析、代码审查或问答不创建调度状态。

## 调度循环

1. 持续调用 `graph_frontier`，完整消费当前批次的所有 action；自动模式下把宿主真实 capacity/model catalog 交给 `plan_dispatch_batch`，按返回的 `concurrentDispatchGroups` 并发创建同批互不冲突的接收 Agent，不等待前一个 Loop 完成。
2. `REFREEZE_TASK_REQUIREMENT` 表示该 TASK 正在编辑需求，禁止派遣。只有用户已经明确提出修改该未开始 TASK 时，才以当前 revision 调用 `unfreeze_task_requirement`；把返回的完整 requirement 修改为新的 `title`、`summary`、`payload` 后，以相同 expected revision 和真实确认人调用 `refreeze_task_requirement`。重新读取 frontier 后才能派遣。不要借此改变依赖、资源锁、Loop ref、Review 或拓扑。
3. 对需要展示执行建议的当前 Graph，调用 `available_agents` 和 `recommend_executors`；只转述对应节点的 Agent、当前模型、置信度、备选和原因，不把普通建议当成 claim、owner、模型切换、外部 CLI 调用授权或限额恢复机制。自动执行另行调用 `plan_dispatch_batch`；宿主原生 Agent 填 `dispatchTransport=HOST_NATIVE`，PATH、CLI、exec、subprocess 或 companion bridge 填 `EXTERNAL_PROCESS` 并只用于得到安全 deferred 说明，不得伪装为可自动启动的宿主 Agent。对每个当前 Ready TASK/Review，优先读取 `loop_context` 并按执行说明用 Agent 分析能力自动判级，向 `node_requirements` 提交 `STANDARD`/`HIGH`、`source=PLANNING` 和原因；完成分析但不确定时使用 `HIGH`。若某些节点没有分析结果，提交与 inventory 精确匹配的宿主 `current_executor`，只让这些节点走 `UNCLASSIFIED / CURRENT_EXECUTOR_FALLBACK`，不得由 Python 补做判级；如果宿主也没有报告当前 Agent/模型，则缺失节点不可派遣。
4. 对已取得派遣预留的每个 assignment，在该 Delivery 的 `workspaceIsolation.workspaceKey` 和 `gitBinding.branchRef` 对应宿主 worktree 内并发创建独立接收 Agent；`EXPLICIT_OVERRIDE` 创建参数必须显式使用 assignment 的 `model.id` 与适用的 `reasoningEffort`，`CURRENT_HOST_DEFAULT` 则在独立子上下文沿用 assignment 指定的当前 Agent/模型。两者都只交付 `rootId`、`nodeId`、`dispatchReservationId` 与 `decisionFingerprint`。创建动作必须由 `dispatchTransport=HOST_NATIVE` 对应的正式宿主 API 完成，禁止执行 `codex --write`、`codex-companion` 或等价外部自治命令。先预留、再创建接收 Agent、最后 claim；单个创建失败不 claim 该节点，等待短租约过期后刷新 frontier/inventory 重算，仍失败则人工交接。不要跨 Delivery 复用工作区、切到其他 Delivery 分支、复制规划会话或由总调度上下文内联执行。
5. 宿主创建原生接收 Agent 后，由模型外适配器签发一次性 `receiver_attestation_id`；Claude Code 使用 dispatch PreToolUse Hook 从真实 `agent_id` 自动注入。当前标准 Codex Plugin 尚无同等生命周期回调，默认不启用可信 adapter，必须 fail closed 并人工交接；只有 Codex 宿主实现私有签发回调后才可显式启用。模型或 shell 直接调用 issuer 不构成证明。接收方再以实际 Agent/模型、attested context、AUTO、HOST_NATIVE、预留 ID 和决策指纹调用 `dispatch_loop`。
6. 接收方从 `loop_context` 获取 `loop.ref`、不透明 `payload`、共享 `skillHints`、TASK baseline 路径、固定 `completionPolicy` 和 `executionPolicy`。
7. Loop 先识别当前任务和宿主可用 Skill，再优先原生触发适用提示；可以跳过不适用提示，也可以按实际需要使用其他 Skill。不同节点可以作出不同选择。
8. TASK Loop 自主管理实现；TASK Review、递归 GROUP Review 和 Delivery Review Loop 自主管理独立发现、修正协调和复审。Review 把每项问题分类为 P0/P1/P2；P0、P1 必须留在同一 Review Loop 内完成修正、验证和独立复审，全部关闭后才可返回 `SUCCEEDED`；P2 不阻断成功，但必须逐项保留在 `result.reviewFindings` 并进入验收投影。每个 GROUP 的机器节点 `GROUP_JOIN` 是控制器自动推进的 GROUP 完成点，不派发实现工作；完成点之后必须进入该层 GROUP Review。
9. 长运行在租约到期前调用 `heartbeat_loop`；只有租约仍有效时，上下文容量压力或高轮次 Hook 摩擦才使用普通 pause/handoff。宿主报告剩余额度不高于 5% 且提供真实 `resetAt` 时，在额度耗尽前完成定时 pause；执行 Agent 使用 `capacity_scope=EXECUTOR`，调度宿主使用 `capacity_scope=HOST`。不得猜测剩余额度或恢复时间。
10. 对 `WAIT_FOR_EXECUTOR_CAPACITY` 或 `WAIT_FOR_HOST_CAPACITY` 不调用推荐器、不自动换 Agent。硬 429 私有回调返回 `cancelRecurringMonitors=true / HOST_NATIVE_ONE_SHOT` 后，宿主删除周期监控并只注册一次 reset 后唤醒。宿主不支持原生计划时只做人工恢复。
11. 只把 Loop 的真实业务终态提交给 `record_loop_result`；可修复 finding、内部 Gate 失败、容量交接和限额等待都不产生 Loop outcome。`BLOCKED` 必须显式提供 failure class，并且只能用于当前 scope/权限内无继续路径的具体条件。
12. 继续消费 frontier。TASK Review 成功后 TASK 才成为可消费终态；每层 GROUP 在完成点之后必须经自己的 GROUP Review 成功，才成为父 GROUP 可消费的终态。根终态再进入 Delivery Review。出现 `RECORD_USER_CONFIRMATION` 时读取 [acceptance.md](references/acceptance.md)，等待真实用户最终确认。

## 恢复

- MCP 写响应未知时先读取 `workspace_status`；仅当状态表明冻结 run 已存在时再读取 `graph_status` 和 `graph_frontier`。`ABSENT` 或 `PREPARED` 按规划恢复，不要调用尚不可用的运行工具或盲目重放写操作。
- `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把 `rootId/nodeId` 路由给新的独立接收上下文；接收方调用 `resume_loop`，再从 frontier 取得新的 dispatch，不重新 prepare/freeze。
- `WAIT_FOR_EXECUTOR_CAPACITY`：原执行 Agent 等待宿主原生一次性恢复提示；到时重新消费 frontier，控制器恢复同一 attempt。
- `WAIT_FOR_HOST_CAPACITY`：总调度 Agent 等待宿主原生一次性恢复提示；硬 429 熔断时先取消旧周期监控，只保留 reset 后一次唤醒。宿主原生计划不可用时等待人工恢复。
- 租约过期与基础设施失败交给 `advance_graph`；过期 operation 不得 heartbeat、pause 或提交结果，也不要手工改 attempt。
- 物化状态损坏时用 `rebuild_graph_run` 从已校验事件链重建；不要改事件。
- Loop 要求改变外层依赖、资源声明、项目范围或拓扑时，记录 `REPLAN_REQUIRED`，不在原冻结 Revision 中暗改。frontier 返回 `REPLAN_HIERARCHY` 后先展示原因并等待用户决定；用户明确要求修改且 Delivery 尚未最终验收时，保持原 `delivery.id` 调用 `prepare_delivery_revision`，再评审、精确授权项目并冻结新 Revision。新 Revision 冻结时原 run 自动成为 `SUPERSEDED`，不要先调用 `cancel_graph_run`，也不要为同一需求制造新的 Delivery ID。

## 按需参考

- 新图的层级、Loop 描述和一次冻结：[planning-quickstart.md](references/planning-quickstart.md)
- 本机 Agent/模型发现、建议原因与本地 Profile：[agent-recommendations.md](references/agent-recommendations.md)
- frontier、资源锁、租约、结果和恢复：[execution-quickstart.md](references/execution-quickstart.md)
- TASK Review、递归 GROUP Review、Delivery Review 与最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连与项目根绑定：[mcp-transport.md](references/mcp-transport.md)
