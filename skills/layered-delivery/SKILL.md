---
name: layered-delivery
description: "规划、冻结、调度或恢复多项目、多模块交付 Graph。用于把已确认需求组织为递归 GROUP/TASK，自动派遣独立 WorkLoop，或把冻结的需求内容生成不绑定 Agent、可由任意 CLI 接管的手动开发包；也用于执行分层 Review、等待最终验收并恢复暂停、失联、额度耗尽或需要 Revision 的既有 Delivery。"
---

# Layered Delivery

把本 Skill 作为外层交付协调器：治理“何时、由谁运行哪个 Loop”，不规定 Loop 内部“怎样完成工作”。

```text
确认需求 → 检查真实代码与影响 → 生成基线与关联文档 → Controller 提供执行方式
├─ 自动执行（默认）：准备、冻结并立即进入自动派遣
│  ├─ LIGHT：单一 TASK WorkLoop → 用户确认
│  └─ STANDARD：TASK/分层 Review WorkLoop → 用户确认
└─ 手动开发：生成 handoff → 任意 CLI 直接开发
```

## 不可违反的边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE`，停止所有治理写入。
- 只从 MCP 响应读取 Graph 状态。不得用 Shell、Python 或其他连接读写 `scheduler.db`，也不得自行创建、修改或修补控制器拥有的 Graph 投影。手动冻结内容包也登记为 SQLite `HANDOFF_READY`，但不创建 Graph Run；接收 CLI 可记录 progress/acceptance，控制器刷新时保留这些人工记录，handoff、overview、baseline、revisions、接口契约和双 fingerprint 保持只读。
- 只使用 schema v3；准备前调用 `hierarchy_contract` 获取当前精确契约，不从源码、旧会话或示例猜参数。
- SQLite 是需求与调度状态的机器权威；Graph 启动后由事件链记录运行历史。Markdown 投影只用于沟通、进度和验收。
- 一个对话工作区最多绑定一个未结束 Delivery。新业务目标默认创建新 Delivery；不得因为工作区已有旧 Delivery 就把新需求写成旧 Delivery 的 Revision。
- 一个需求只使用一个稳定的 `.layered-delivery/<delivery-id>/` 目录。用户提供工单号等外部需求标识时写入 `delivery.requirementKey`；同一 key 不得换 `delivery.id`，Controller 也会从 ID/标题识别常见工单号并阻断重复目录。自动与手动开发均生成 overview、baseline、progress、acceptance、revisions 和同结构 work-items；手动开发额外包含自包含 handoff 文件。不得创建共享 `.layered-delivery/handoffs/`。手动包的需求内容已由双指纹冻结，但它不是 Graph `FROZEN`：未 prepare、未创建 Graph Run。
- 并行 Delivery 真正开始开发时使用独立宿主工作区；Git 场景优先使用独立 linked worktree。`preview_hierarchy` 会先创建共享 `scheduler.db`、根总览和 Delivery 基线/关联文档并登记 `CHOICE_READY`，但不绑定 workspace、不创建 Graph Run 或 worktree。手动内容冻结阶段同样不创建 worktree。不要复制调度数据库或启动第二套控制面。
- 总调度上下文只规划和路由，不在自身上下文内实现 TASK 或 Review。每个执行与 Review Loop 使用独立接收上下文。
- Git 创建/切换分支、commit、merge、push、发布、迁移和新增外部权限始终需要各自授权；Graph 的项目范围不替代这些授权。
- 最终完成必须取得真实用户确认。

## 入口路由

先调用 `workspace_status`；已知 Delivery 时显式传 `rootId`。

| 状态 | 下一步 |
|---|---|
| `ABSENT` | 用户要求新交付时读取[规划说明](references/planning-quickstart.md)；只读问答不创建状态 |
| `CHOICE_READY` | 基线与关联文档已生成；原样展示 Controller 返回的执行方式交互 |
| `HANDOFF_READY` | 报告手动需求快照已登记、Graph Run 未创建；按 handoff 直接开发，不调用 frontier |
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
5. 用精确 `resourceClaims` 表达跨 Delivery 排他资源；worktree 不能替代数据库、端口或环境锁。
6. 用户提供的 Skill 只登记为共享 `root.skillHints`，由各 Loop 根据真实上下文决定是否触发。
7. 调用 `preview_hierarchy`。只有响应为 `CHOICE_READY` 且 `artifactsReady=true`，确认共享数据库、根总览、baseline、progress、acceptance、revisions 与 work-items 已生成后，才展示执行方式。Controller 是交互文案的唯一所有者；宿主必须原样或机械映射 `executionChoice` 的顺序、默认项、标签、说明和自由输入行为，Skill 不得重写、猜测或增加第三个选项。
8. 用户点选按钮后只调用一次 `select_execution_mode`。选择默认的 `AUTOMATIC` 时，Controller 立即 prepare、freeze 并返回自动派遣动作；不再询问通用 Yes/No，宿主立刻进入 frontier 自动派遣循环。选择 `MANUAL` 时，Controller 生成 handoff、登记 `HANDOFF_READY`，宿主原样展示 `manualHandoff.receiverPrompt`；该提示词也已嵌入 handoff。用户直接输入修改意见时不调用选择工具，继续需求沟通；需求变化后用同一 Delivery 重新生成基线与关联文档。

需求连续性规则：

- 未开始 TASK 的 `title`、`summary` 或 `payload` 可在用户明确授权后 `unfreeze_task_requirement → refreeze_task_requirement`；不得借此修改依赖、资源、Loop、Review 或拓扑。
- 最终验收前需要改变外层范围时，只有用户明确继续同一 `delivery.id`，或已有 Loop 返回 `REPLAN_REQUIRED`，才调用 `prepare_delivery_revision`。
- 已是 `HANDOFF_READY` 的手动需求发生变化时，保持同一 `delivery.id` 重新 preview，并调用 `create_manual_handoff` 提交当前 Revision、`USER_EXPLICIT_SAME_DELIVERY` 和修订原因；Controller 在原目录生成下一不可变手动 Revision，不调用自动路径的 `prepare_delivery_revision`。
- 候选 Revision 不替换当前 run；新 Revision 冻结时才原子切换。不要先取消旧 run，也不要为同一需求创建新 Delivery ID。

## 调度循环

冻结后持续读取 `graph_frontier`，完整消费当前批次的 action。精确参数、claim 顺序、租约与恢复规则以[execution-quickstart.md](references/execution-quickstart.md)为准。

1. `REFREEZE_TASK_REQUIREMENT`：停止派遣该 TASK，按当前 requirement revision 完成用户授权的修改并重新读取 frontier。
2. `DISPATCH_LOOP`：只存在于已经开始的自动执行 Graph。先读取每个 Ready 节点的 `loop_context`，再调用 `plan_dispatch_batch`。首次稳定路由返回 `HOST_NATIVE_ROUTE_REVIEW` 时，用中文表格展示当前批次和 30 秒倒计时，不询问确认；用户可在窗口内直接指定其他可用原生模型。倒计时结束后自动用同一输入再次调用计划工具并派遣，路由发生变化的节点重新开始 30 秒窗口。
3. 自动选模由总调度 Agent 分析任务风险并提交 `ROUTINE`、`STANDARD` 或 `HIGH`；Controller 不用 Python 做语义判级。缺少分析时只可回退宿主明确报告且与 inventory 匹配的当前 Agent/模型；两者都缺失时保持 deferred。
4. 自动 assignment 只接受宿主正式 Agent API 证明的 `HOST_NATIVE` 容量。PATH、CLI、exec、subprocess 或 companion bridge 一律是 `EXTERNAL_PROCESS`，不得伪装成自动派遣能力。
5. 只消费 `HOST_NATIVE_DISPATCH_PLAN` 的 `concurrentDispatchGroups`，并发创建同批独立接收 Agent；`HOST_NATIVE_ROUTE_REVIEW` 没有预留，不得提前创建 Agent。严格把 assignment 的 `model.id` 当作 Claude/Codex 原生模型选择器，并使用 assignment 的宿主原生推理参数、工作区、预留 ID、决策指纹和宿主任务名。任何本机转发或模型替换都在原生调用之后发生，不得把转发后的实际模型倒填为派遣选择器。不得先创建普通 helper 再抢占预留，也不得跨 Delivery 复用上下文或工作区。
6. 严格遵循宿主接收协议：Claude 由真实子 Agent 消费一次性 attestation 后 claim；Codex 由 `SubagentStart` Hook 校验真实 child/parent/task 并在 child 可见前 claim。claim 成功后，接收方读取一次 `loop_context`，随即在任何代码检查、分析、读写或测试前提交首次独立 `heartbeat_loop`；不得把 claim 自带租约当成首次 heartbeat。claim 的 `modelId` 来自 reservation 中的原生选择器；宿主若能观测转发后的模型，只将其记录为展示用 `actualModelId`。Hook、预留或宿主身份无法证明时 fail closed。
7. 接收方从 `loop_context` 获取冻结输入，自主管理分析、实现、测试、Gate 与修正。`STANDARD` 在领取、代码检查完成、测试、修复、复审和最终验证等阶段上报进度；`LIGHT` 只在发现问题和最终验证时上报，短 Loop 可只报最终验证。进度不续租，长运行仍须在租约到期前 heartbeat。
8. `STANDARD` 的 TASK Review、GROUP Review 和 Delivery Review 在各自 Loop 内完成独立发现、修正、验证和复审。`LIGHT` 不创建这些 Review 节点，TASK 成功后直接等待用户确认；若实际 diff 或影响扩大，必须提交 `REPLAN_REQUIRED` 并升级同一 Delivery 的下一 Revision 为 `STANDARD`。详见[验收说明](references/acceptance.md)。
9. 只向 `record_loop_result` 提交真实业务终态：`SUCCEEDED`、`BLOCKED`、`REPLAN_REQUIRED` 或 `CANCELLED`。内部 Gate 失败、可修复 finding、容量交接和限额等待都不是 Loop outcome。
10. frontier 返回 `RECORD_USER_CONFIRMATION` 时，展示分层验收结果并等待真实用户确认。

后台 Loop 运行期间，总调度 Agent 按 `progressMonitor.recommendedPollSeconds` 持续读取 `graph_frontier`，仅在表格内容或预警变化时把 `progressMonitor.markdownTable` 展示到主 Agent 窗口。普通用户界面不展开 `graph_events`、operation、reservation 或原始英文状态；这些信息只保留给诊断。领取后 90 秒无首次独立心跳显示“疑似未启动”，心跳正常但超过 5 分钟无进度显示“存活但无可见进展”，心跳和进度均超过预期窗口显示“疑似失联”；租约到期由下一次 `graph_frontier` 自动按 `WORKER_LOST` 回收。

## Agent、模型与容量

- `available_agents` 仅用于通用本机发现，不参与手动开发路由；`recommend_executors` 只接受 `AUTOMATIC`，并且只在当前宿主 Agent 内按原生 tier 生成只读预览。正式 Ready 批次由 `plan_dispatch_batch` 开启持久化 30 秒路由调整窗口，超时自动派遣。初始手动选择通过 `select_execution_mode(MANUAL)` 生成冻结内容包；后续手动 Revision 才直接调用 `create_manual_handoff`。两者都不指定 Agent、模型或接收会话，也不创建任务/worktree；用户切换任意 CLI、读取该目录并开始实际开发时，才确定执行 Agent、创建或选择工作区并展示自己的本地模型表。详见[agent-recommendations.md](references/agent-recommendations.md)。
- 所有派遣、tier 匹配、Review 多样性、reservation、claim 授权与决策指纹只使用宿主原生 `modelId`。`actualModelId` 是原生调用完成后的可选宿主观测，只用于中文状态和进度表；未知时显示“未报告”，不得读取特定修改器、猜测对应关系或据此改变编排。
- 自动编排、自动选模、跨 Adapter、最大并发、额度策略和 Review 多样性由用户级配置控制；读取[orchestrator-configuration.md](references/orchestrator-configuration.md)。
- 跨 Adapter 当前未开放修改；面板和保存工具都以 `ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE` fail closed。只有中央宿主未来能证明可创建、可认证、有容量且属于同一可信编排根的多个 Adapter 时才可开放。
- 只有宿主提供结构化利用率和真实 `resetAt` 时才可提前暂停；不得从文本猜测额度。
- 硬 429 由模型外宿主容量回调处理，不等待失败模型反馈。收到容量等待 action 后不调用推荐器或静默换 Agent，只按宿主提供的一次性恢复方式等待。

## 恢复

- `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：路由到新的独立接收上下文，调用 `resume_loop` 后重新取得 dispatch；不要重新 prepare/freeze。
- 容量等待：到恢复时间后由原调度或执行 Agent 重新消费 frontier；不生成业务 outcome。
- 租约过期或基础设施失败：交给 `advance_graph`；旧 operation 不得 heartbeat、pause 或提交结果。
- `WORKER_LOST` 生成新 attempt 后，同一 Adapter 的新编排会话可在下一次成功 claim 时轮换已失联的接收方信任根；控制器记录 `RECEIVER_ROOT_ROTATED`，无需重新 prepare/freeze 或直接改库。跨 Adapter、仍有已认领 Loop 或冲突的有效接收凭据时保持拒绝。
- 物化状态损坏：调用 `rebuild_graph_run` 从已校验事件链重建，不修改事件。
- 外层契约变化：记录 `REPLAN_REQUIRED`，等待用户决定是否准备同一 Delivery 的下一 Revision。

## 按需参考

- 新建或修订 Graph、Git/project scope、接口契约与冻结：[planning-quickstart.md](references/planning-quickstart.md)
- Frontier、自动派遣、接收协议、租约、资源锁与恢复：[execution-quickstart.md](references/execution-quickstart.md)
- Agent/模型发现、推理分类、建议与回退：[agent-recommendations.md](references/agent-recommendations.md)
- TASK/GROUP/Delivery Review 和最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连、工作区绑定、SQLite 权威与投影：[mcp-transport.md](references/mcp-transport.md)
- 中央编排器默认值、配置面板、跨平台路径和跨 Adapter 策略：[orchestrator-configuration.md](references/orchestrator-configuration.md)
