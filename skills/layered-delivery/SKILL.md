---
name: layered-delivery
description: "规划、冻结、调度或恢复多项目、多模块交付 Graph。用于把已确认需求组织为递归 GROUP/TASK，自动派遣独立 WorkLoop，或把冻结的需求内容生成不绑定 Agent、可由任意 CLI 接管的手动开发包；也用于执行分层 Review、等待最终验收并恢复暂停、失联、额度耗尽或需要 Revision 的既有 Delivery。"
allowed-tools:
  - mcp__plugin_layered-delivery_layered-delivery__*
---

# Layered Delivery

把本 Skill 作为外层交付协调器：治理“何时、由谁运行哪个 Loop”，不规定 Loop 内部“怎样完成工作”。

```text
确认需求 → 检查真实代码与影响 → 生成基线与关联文档 → Controller 提供执行方式
├─ 自动执行（默认）：准备、冻结并立即进入自动派遣
│  ├─ LIGHT：单一 TASK WorkLoop → 用户确认
│  └─ STANDARD：TASK/分层 Review WorkLoop → 用户确认
└─ 手动开发：生成 handoff → 接收 CLI 启动同一 Graph
   ├─ TASK 实现：独立接收上下文 MANUAL claim
   └─ TASK/GROUP/Delivery Review：与自动执行相同的原生派遣与最终确认
```

## 不可违反的边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE`，停止所有治理写入。
- 只从 MCP 响应读取 Graph 状态。不得用 Shell、Python 或其他连接读写 `scheduler.db`，也不得自行创建、修改或修补控制器拥有的 Graph 投影。手动冻结内容包先登记为 SQLite `HANDOFF_READY`，交接阶段不创建 Graph Run；接收 CLI 在任何代码检查、分析、读写或测试前必须调用 `start_manual_handoff`，用双 fingerprint 在实际工作区启动同一 Graph。此后 progress、acceptance 和全部 work-item 投影同自动执行一样只由控制器事件刷新，不得人工补写。
- 只使用 schema v3；准备前调用 `hierarchy_contract` 获取当前精确契约，不从源码、旧会话或示例猜参数。
- SQLite 是需求与调度状态的机器权威；Graph 启动后由事件链记录运行历史。Markdown 投影只用于沟通、进度和验收。
- 一个对话工作区最多绑定一个未结束 Delivery。新业务目标默认创建新 Delivery；不得因为工作区已有旧 Delivery 就把新需求写成旧 Delivery 的 Revision。
- 一个需求只使用一个稳定的 `.layered-delivery/<delivery-id>/` 目录。用户提供工单号等外部需求标识时写入 `delivery.requirementKey`；同一 key 不得换 `delivery.id`，Controller 也会从 ID/标题识别常见工单号并阻断重复目录。自动与手动开发均生成 overview、baseline、progress、acceptance、revisions 和同结构 work-items；手动开发额外包含自包含 handoff 文件。不得创建共享 `.layered-delivery/handoffs/`。手动包的需求内容已由双指纹冻结，但它不是 Graph `FROZEN`：未 prepare、未创建 Graph Run。
- 自动 Git Delivery 在 Claude Code 与 Codex 都使用 `HOST_NATIVE_LINKED_WORKTREE`。主会话保留 primary checkout，只负责选择、调度监控和最终用户交互；宿主消费 `worktreeSetup.hostDispatch`，创建或复用一个 Delivery 级稳定 linked worktree，并在其中启动后台协调 Agent/项目任务。Claude Code 在同一顶层会话内可短暂用 `EnterWorktree(path=...)` 进入宿主已创建或唯一匹配的 worktree，启动 `layered-delivery:delivery-coordinator` 后立即返回 primary；Hook 会用真实 cwd 为 MCP 调用签发一次性工作区证明，因此不得再启动新顶层 Claude 会话，也不得把固定 `${CLAUDE_PROJECT_DIR}` 当作执行路径。Codex 创建 `environment=worktree` 的后台项目任务。Controller 不执行 Git 写操作。`worktreeProvenance` 显示实际宿主、策略、拓扑、基线选择来源与 `baseRef/baseCommit/baseHeadCommit/integrationTarget`；feature 分支只有未被其他 worktree/Delivery 使用、基线有效时才允许 adoption。已有 diff 必须让用户确认全部属于本 Delivery，再以原响应的精确 `workingTree.stateFingerprint` 作为 `confirmed_dirty_state_fingerprint` 重查；`.layered-delivery/**` 控制面产物不计入业务 dirty 状态。`projectScopes.workspaceRoot` 是 preview 时的仓库锚点；Controller 在 prepare/runtime 只读解析同 Git common directory 下唯一匹配的 linked worktree，并返回 `verifiedProjectScopes`。receiver 的 `loop_context.projectScopes` 只包含本 Delivery 当前验证通过的实际路径，冻结锚点另存于 `projectScopeAnchors`；Loop 必须直接使用实际路径，不得自行创建、`checkout` 或 `switch` 分支。`preview_hierarchy` 仍只创建共享 `scheduler.db`、根总览和 Delivery 基线/关联文档，不绑定 workspace 或创建 Graph Run。linked worktree 复用共享控制面但获得独立 `workspaceKey`；不要复制调度数据库或启动第二套控制面。
- 总调度上下文只规划和路由，不在自身上下文内实现 TASK 或 Review。自动 TASK、手动 TASK 与每个 Review Loop 都使用独立接收上下文；手动接收主上下文也只负责启动 Graph 和消费 frontier。
- Git 创建/切换分支、commit、merge、push、发布、迁移和新增外部权限始终需要各自授权；Graph 的项目范围不替代这些授权。
- 最终完成必须取得真实用户确认。

## 入口路由

先调用 `workspace_status`；已知 Delivery 时显式传 `rootId`。如果用户提出新业务目标而当前状态属于另一个未结束 Delivery，先按规划说明创建宿主原生 worktree 会话，再在新工作区重新调用 `workspace_status`；不得按旧 Delivery 的状态行续接。

| 状态 | 下一步 |
|---|---|
| `ABSENT` | 用户要求新交付时读取[规划说明](references/planning-quickstart.md)；只读问答不创建状态 |
| `CHOICE_READY` | 基线与关联文档已生成；若无 `executionSelection`，按 `preview_hierarchy` 返回原样展示：返回 `developmentBaseline` 时先确认开发基线（见下「规划与冻结」），返回 `executionChoice` 时展示执行方式交互；若已记录 `AUTOMATIC`，按 `nextAction` 迁移 worktree 并调用 `resume_execution_mode`，不得再次展示选择器 |
| `HANDOFF_READY` | 报告手动需求快照已登记、Graph Run 尚未创建；原样展示 handoff 接收提示。接收 CLI 在实际工作区调用 `start_manual_handoff` 后立即进入 frontier，不得直接开发 |
| `PREPARED` | 读取规划说明并续接已有方案；需求未变时不要重复 prepare |
| `ACTIVE` / `BLOCKED` / `PAUSED` | 读取[执行说明](references/execution-quickstart.md)，从 `graph_frontier` 恢复 |
| `COMPLETED` | 报告终态；新目标使用新 Delivery |
| `CANCELLED` | 默认报告终态；只有用户明确继续未验收的同一需求时才准备下一 Revision |

MCP 写响应未知、连接恢复、Git 绑定异常或投影问题时，先读取[MCP 与状态说明](references/mcp-transport.md)，不要盲目重放写操作。

## 规划与冻结

规划新 Delivery 或 Revision 时，完整遵循[planning-quickstart.md](references/planning-quickstart.md)：

1. 与用户确认目标、边界、验收点、项目范围、依赖和排他资源。
2. 根据真实代码、预计或已有 diff 和影响范围选择保障档；不确定时使用 `STANDARD`。`LIGHT` 只适用于单一根 TASK、局部内部改动、定向验证明确且不涉及接口、数据、权限、安全、生产部署或不可逆副作用；必须写入 `assuranceRationale`，并把 TASK/Delivery `reviewLoop` 设为 `null`。
3. `STANDARD` 用 `GROUP` 表达真实的并行汇合或分层 Review；可以递归，也可以完全省略。每个 TASK 配置独立 TASK Review，每个 GROUP 配置本层 GROUP Review，Delivery 配置最终 Review。不要为单个 TASK 制造形式层级。
4. 把实现目标和明确约束放入不透明 `loop.payload`。调度器不解释实现计划、测试、Gate 或内部 Skill 流程。
5. 用精确 `resourceClaims` 表达跨 Delivery 排他资源；worktree 不能替代数据库、端口或环境锁。并行 Delivery 改同一文件/区域时，在相关 TASK 声明同一个 claim 即自动串行（详见 planning-quickstart「并行 Delivery 与同资源串行化」）。
6. 用户提供的 Skill 只登记为共享 `root.skillHints`，由各 Loop 根据真实上下文决定是否触发。
7. 调用 `preview_hierarchy`（层级较大或 payload 详细时，先 Write 到工作区文件、用 `python -m json.tool` 校验，再以 `hierarchy_file` 传入，避免内联大 JSON 出错；详见 planning-quickstart）。只有响应为 `CHOICE_READY` 且 `artifactsReady=true`，确认共享数据库、根总览、baseline、progress、acceptance、revisions 与 work-items 已生成后，才展示执行方式。Controller 是交互文案的唯一所有者；宿主必须按 `executionChoice.presentationPolicy` 优先使用当前 Adapter 的原生选择器，从 `options` 机械映射顺序、ID、默认项、推荐项、标签、说明和自由输入行为。只有映射工具在当前上下文不可调用时才原样显示 `markdown`；不得改写降级文案、要求用户回复选项文字或增加第三个选项。
8. 当 `preview_hierarchy` 返回 `developmentBaseline`（工作树干净、无已记忆基线、层级未带 `gitBinding` 时触发；即便已在 feature 分支上也会触发）时，先用当前 Adapter 的原生选择器展示其 `options`：仅本地分支（不含远端）加上「从主线创建新分支」（`NEW_FROM_MAINLINE` 需提供新分支名）。选择后只调用一次 `confirm_development_baseline`：它会记录该基线（同一 Delivery 后续 Revision 不再重复询问）、只读计算 `gitBinding` 并冻结回层级，再返回更新后的 `hierarchyFingerprint` 与 `executionChoice`。Controller 不创建分支或 worktree；`NEW_FROM_MAINLINE` 把 `baseCommit` 钉在当前主线 HEAD，分支由宿主在 worktree 准备时创建。已记忆基线、或工作树非干净、或非 Git 工作区时不触发，直接进入 `executionChoice`。

9. 用户点选按钮后只调用一次 `select_execution_mode`。选择默认的 `AUTOMATIC` 时，Controller 先持久记录该次业务确认并返回 `worktreeSetup.hostDispatch`。Claude Code 机械执行 `agentDispatch`：创建/复用 Delivery worktree、在同一会话启动后台 `delivery-coordinator`，随后回到 primary；Codex 创建稳定的 worktree 项目任务。后台协调方用原双 fingerprint 调用 `workspace_status → resume_execution_mode`，再消费 frontier。不要求用户手动 `cd` 或启动新顶层 CLI。主会话不得自行实现、不得调用 `EnterWorktree` 后留在执行分支，也不得重复展示同一 handoff；它只按监控间隔读取 frontier。任何续接都不得再次展示执行方式、询问 Yes/No、改走 MANUAL 或自行拼出第三个解卡菜单。选择 `MANUAL` 时，Controller 生成 handoff、登记 `HANDOFF_READY`，宿主原样展示 `manualHandoff.receiverPrompt`；该提示词也已嵌入 handoff。接收 CLI 在实际工作区显式调用 `start_manual_handoff`，然后用 `CLAIM_MANUAL_TASK` 完成 TASK 实现，并让后续 Review 回到与自动执行相同的 `DISPATCH_LOOP`。用户直接输入修改意见时不调用选择工具，继续需求沟通；需求变化后用同一 Delivery 重新生成基线与关联文档，并使旧的待执行选择失效。

需求连续性规则：

- 未开始 TASK 的 `title`、`summary` 或 `payload` 可在用户明确授权后 `unfreeze_task_requirement → refreeze_task_requirement`；不得借此修改依赖、资源、Loop、Review 或拓扑。
- 最终验收前需要改变外层范围时，只有用户明确继续同一 `delivery.id`，或已有 Loop 返回 `REPLAN_REQUIRED`，才调用 `prepare_delivery_revision`。
- 已是 `HANDOFF_READY` 的手动需求发生变化时，保持同一 `delivery.id` 重新 preview，并调用 `create_manual_handoff` 提交当前 Revision、`USER_EXPLICIT_SAME_DELIVERY` 和修订原因；Controller 在原目录生成下一不可变手动 Revision，不调用自动路径的 `prepare_delivery_revision`。
- 候选 Revision 不替换当前 run；新 Revision 冻结时才原子切换。不要先取消旧 run，也不要为同一需求创建新 Delivery ID。

## 调度循环

冻结后持续读取 `graph_frontier`，完整消费当前批次的 action。精确参数、claim 顺序、租约与恢复规则以[execution-quickstart.md](references/execution-quickstart.md)为准。

1. `REFREEZE_TASK_REQUIREMENT`：停止派遣该 TASK，按当前 requirement revision 完成用户授权的修改并重新读取 frontier。
2. `CLAIM_MANUAL_TASK`：只存在于 `start_manual_handoff` 已启动的 manual Graph，且只允许 `TASK_LOOP`。总协调上下文创建或切换到独立 TASK receiver；接收方读取一次 `loop_context`，以真实 receiving context、唯一 operation 和 `dispatch_mode=MANUAL` claim，随后立即独立 heartbeat。不得对任何 Review 使用 MANUAL，也不得让总协调上下文直接实现。
3. `DISPATCH_LOOP`：用于自动 Graph 的全部 Loop，也用于 manual Graph 中 TASK 成功后的所有 TASK/GROUP/Delivery Review。按当前可信宿主 Adapter 调用一次 `plan_dispatch_batch`，取得 reservation 后立即并发创建同批独立 receiver。计划不接收模型 inventory、风险判级、模型偏好或 reasoning effort；assignment 固定 `modelPolicy=CURRENT_HOST_INHERIT`，receiver 继承当前宿主模型。
4. 自动 assignment 只接受宿主正式 Agent API 证明的可信外层 Adapter。PATH、CLI、exec、subprocess、普通 helper 或 companion bridge 都不能领取 Graph；新增供应商只有实现同等身份与生命周期证明的外层 Adapter 后才能加入自动派遣。
5. 只消费派遣计划的 `concurrentDispatchGroups`，并按 assignment 的 `hostAdapterId`、`receiverAgentId`、工作区、预留 ID、决策指纹和宿主任务名创建 receiver。不得在预留后继续做额外分析，也不得跨 Delivery 复用 receiver、上下文或工作区。
6. AUTO claim 严格遵循宿主接收协议：Claude 由真实 child 消费一次性 attestation 后 claim；Codex 由 `SubagentStart` Hook 校验真实 child/parent/task 并在 child 可见前 claim。claim 成功后，receiver 读取一次 `loop_context`，随即在任何代码检查、分析、读写或测试前提交首次独立 `heartbeat_loop`；不得把 claim 自带租约当成首次 heartbeat。Hook、预留或宿主身份无法证明时 fail closed。
7. 只有外层 receiver 可以 claim、heartbeat、progress、pause、resume 或提交 result。receiver 可在 Loop 内按成本和任务需要创建 Codex、Claude、Grok、DeepSeek 等内部 Worker，自主选择模型、effort、并发和升级策略；内部 Worker 不得持有 operation、attestation 或 reservation，也不得直接调用控制面工具。
8. receiver 从 `loop_context` 获取冻结输入，自主管理分析、实现、测试、Gate 与修正。`projectScopes` 是运行时已验证的有效路径，`projectScopeAnchors` 只用于审计冻结时的仓库锚点；receiver 不得把锚点当作开发目录，也不得为“校准环境”创建或切换 Git 分支。`STANDARD` 在领取、代码检查完成、确认根因、完成修改、测试开始与结束、修复、复审和最终验证等阶段立即上报进度。长时间测试或构建必须使用非阻塞进程或独立监控，使 receiver 能在命令运行期间按 `heartbeatSeconds` 继续 heartbeat；开始前先 progress + heartbeat，结束后立即 heartbeat + progress。宿主 child 未发送完成通知不等于 heartbeat，也不能证明 receiver 仍存活。`LIGHT` 只在发现问题和最终验证时上报。进度不续租。
9. `STANDARD` 的 TASK Review、GROUP Review 和 Delivery Review 在各自独立 receiver 内完成发现、修正、验证和复审。`LIGHT` 不创建这些 Review 节点；若实际 diff 或影响扩大，必须提交 `REPLAN_REQUIRED` 并升级同一 Delivery 的下一 Revision 为 `STANDARD`。详见[验收说明](references/acceptance.md)。
10. 只向 `record_loop_result` 提交真实业务终态。可在 `outcome.result.workerTelemetry` 中按 phase 报告内部 Worker 的 `agent`、`model`、`reasoningEffort`；未知字段写 `unreported`。该遥测只用于展示和后续 Review，不参与授权、路由、重试或独立性判断。
11. frontier 返回 `RECORD_USER_CONFIRMATION` 时，展示分层验收结果并等待真实用户确认。

后台 Loop 运行期间，总调度 Agent 严格按 `progressMonitor.recommendedPollSeconds`（当前为 10 秒）持续读取 `graph_frontier`；不得把 90 秒首次心跳告警阈值用作 sleep 或轮询间隔。宿主收到原生 child 完成通知时立即中断等待并刷新 frontier。仅在表格内容或预警变化时把 `progressMonitor.markdownTable` 展示到主 Agent 窗口。普通用户界面不展开 `graph_events`、operation、reservation 或原始英文状态；这些信息只保留给诊断。领取后 90 秒无首次独立心跳显示“疑似未启动”，心跳正常但超过 5 分钟无进度显示“存活但无可见进展”，心跳和进度均超过预期窗口显示“疑似失联”；租约到期由下一次 `graph_frontier` 自动按 `WORKER_LOST` 回收。

## Receiver、Worker 与容量

- Layered Delivery 只调度可信外层 receiver，并始终继承当前宿主模型；不发现、推荐、选择或切换派遣模型。完整边界见[外层接收与 Loop 内 Worker](references/agent-execution-boundary.md)。
- 内部 Worker 是 Loop 实现细节。Codex、Claude、Grok、DeepSeek 或其他供应商都可由 receiver 按宿主能力使用；只有要成为 Graph receiver 的供应商才需要新增可信外层 Adapter。
- 外层 receiver 最大并发和额度恢复策略由 Plugin 内置；不得读取、创建或要求用户修复用户级编排配置。
- 只有宿主提供结构化利用率和真实 `resetAt` 时才可提前暂停；不得从文本猜测额度，也不得因额度问题静默换模型、Worker 或 Adapter。
- 硬 429 由模型外宿主容量回调处理，不等待失败模型反馈；收到容量等待 action 后只按宿主提供的一次性恢复方式等待。

## 恢复

- `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：路由到新的独立接收上下文，调用 `resume_loop` 后重新取得 dispatch；不要重新 prepare/freeze。
- 容量等待：到恢复时间后由原调度或执行 Agent 重新消费 frontier；不生成业务 outcome。
- 租约过期或基础设施失败：交给 `advance_graph`；旧 operation 不得 heartbeat、pause 或提交结果。
- `WORKER_LOST` 生成新 attempt 后，同一 Adapter 的新编排会话可在下一次成功 claim 时轮换已失联的接收方信任根；控制器记录 `RECEIVER_ROOT_ROTATED`，无需重新 prepare/freeze 或直接改库。跨 Adapter、仍有已认领 Loop 或冲突的有效接收凭据时保持拒绝。
- 前一 Loop 已成功、当前没有任何 claimed Loop 或其他会话的有效接收凭据时，同一可信 Adapter 的新主会话可在下一层 frontier 的 claim 中以 `IDLE_FRONTIER_HANDOFF` 安全轮换编排根；这用于多会话接力 TASK/GROUP/Delivery Review。任一活跃 claim 存在时仍 fail closed，不能借接力接管正在运行的 receiver。
- 物化状态损坏：调用 `rebuild_graph_run` 从已校验事件链重建，不修改事件。
- 外层契约变化：记录 `REPLAN_REQUIRED`，等待用户决定是否准备同一 Delivery 的下一 Revision。

## 按需参考

- 新建或修订 Graph、Git/project scope、接口契约与冻结：[planning-quickstart.md](references/planning-quickstart.md)
- Frontier、自动派遣、接收协议、租约、资源锁与恢复：[execution-quickstart.md](references/execution-quickstart.md)
- 外层 receiver、Loop 内 Worker、权限边界与遥测：[agent-execution-boundary.md](references/agent-execution-boundary.md)
- TASK/GROUP/Delivery Review 和最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连、工作区绑定、SQLite 权威与投影：[mcp-transport.md](references/mcp-transport.md)
