# 递归 Graph 执行

用于冻结图的运行、恢复与阻断处理。

## Frontier

调用 `graph_frontier` 并执行全部 action：

- `DISPATCH_LOOP`：自动模式下先用宿主真实 inventory 调用 `plan_dispatch_batch`，原子预留后按计划创建接收 Agent；接收方读取 `loop_context` 后凭预留 claim。
- `WAIT_FOR_DISPATCH_RECEIVER`：另一个调度器已为该 Ready Loop 取得短租约派遣预留；不得重复创建 Agent，等待接收方 claim 或预留过期。
- `CONTINUE_OR_HEARTBEAT_LOOP`：继续当前 Loop，并在租约到期前 heartbeat。
- `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把暂停节点路由给新的接收上下文；接收方 resume 后重新读取 frontier 并 dispatch。
- `WAIT_FOR_EXECUTOR_CAPACITY`：执行 Agent 在软阈值暂停后等待宿主原生的一次性恢复提示；到时由原 Agent 重新消费 frontier。
- `WAIT_FOR_HOST_CAPACITY`：总调度 Agent 在软阈值暂停后等待宿主原生的一次性恢复提示；到时重新消费 frontier。两种等待都不调用推荐器或自动换 Agent。
- `RESOLVE_LOOP_BLOCK`：展示 Loop 返回的摘要和不透明 result，等待外部条件或人工决定。
- `REPLAN_HIERARCHY`：展示外层契约变化及当前 Revision 无法继续的原因，等待用户决定。用户明确要求修改且尚未最终验收时，保持同一 `delivery.id` 调用 `prepare_delivery_revision`；重新评审、授权项目并冻结后，旧 run 自动成为 `SUPERSEDED`。
- `REFREEZE_TASK_REQUIREMENT`：该未开始 TASK 的需求处于解冻编辑态，当前不可派遣。按用户已经明确提出的修改完成 `unfreeze_task_requirement → refreeze_task_requirement`，再重新读取 frontier。
- `RECORD_USER_CONFIRMATION`：Review Loop 已成功；读取 [acceptance.md](acceptance.md)，等待用户最终接受。

不要自行增加 TASK/Gate 节点，也不要根据 payload 内容改变 frontier 顺序。

需要展示当前节点的执行建议时，调用 `available_agents` 和 `recommend_executors`，按 `nodeId` 选择对应建议。推荐工具不得据此启动外部 CLI、切换模型、改变 owner、提前 claim、绕过宿主原生 Agent 容量或接管限额恢复；自动执行由总调度器在工具返回后使用宿主原生 Agent 完成。CC-Switch、配置或容量变化后可以重新调用，旧建议不作为缓存权威。

自动模式不直接消费终端建议来启动进程。Plugin MCP Server 启动时先读取用户级中央编排器配置；配置文件不存在时默认开启自动编排与自动选模、关闭跨 Adapter、只允许 `codex`/`claude-code`、最多并发 4 个执行器、额度耗尽暂停恢复并优先用不同 Adapter Review。当前跨 Adapter 开关和 `SWITCH_ADAPTER` 保存均以 `ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE` fail closed；宿主原生多 Adapter 桥接开放前只能使用当前宿主 Adapter。非法配置 fail closed，配置不进入 Delivery schema 或 SQLite；路径和手动修改方法见 [orchestrator-configuration.md](orchestrator-configuration.md)。总调度器从宿主明确暴露的原生 Agent catalog 构造 `executor_inventory`：每项必须包含宿主证明的 `dispatchTransport=HOST_NATIVE`，可以用 `adapterId` 区分中央宿主 Adapter（省略时等于 `agentId`），并只登记真实 `availableSlots`、`development`/`review` 能力、可显式选择的模型、模型 tier、reasoning effort 与优先级；不包含 Token、Base URL、命令参数，也不把 PATH 中存在的 CLI 当成自动启动授权。PATH、CLI、exec、subprocess 或 companion bridge 必须标为 `EXTERNAL_PROCESS`，只会得到安全 deferred，不能进入 assignment。以当前 `graphFingerprint` 调用 `plan_dispatch_batch` 后，只消费 `binding=HOST_NATIVE_DISPATCH_PLAN` 的 assignments。计划工具不启动 Agent、不 claim、不保存完整 inventory/requirements；提供方限额恢复也不调用它。

### 派遣前自动判级

对当前每个 Ready TASK/Review，总调度 Agent 在调用派遣计划前先调用该节点的 `loop_context`，只读取目标、约束、已知验收点、Loop ref、上游结果摘要和工作项基线路径，使用自身分析能力完成 `ROUTINE`/`STANDARD`/`HIGH` 路由判断；该读取只用于路由判级，不在总调度上下文内实现 TASK 或 Review。Python Controller 不做任何本地语义分析。

- 跨模块或跨仓、架构调整、数据库迁移、安全权限、并发一致性、复杂故障定位、破坏性操作、高风险接口兼容，以及需要汇总多项上游证据的整体 Review：`HIGH`。
- 单模块局部修改、契约清晰、影响范围小、验证路径明确：`STANDARD`。
- 输入输出和完成条件完全明确、低歧义、低风险、可重复执行并有确定验证路径的机械性修改、提取、分类或转换：`ROUTINE`。
- 无法可靠判断风险时，不确定时使用 `HIGH`。

总调度 Agent 优先为每个当前 Ready TASK/Review 提交临时 `node_requirements`，`source=PLANNING` 并给出具体原因；不能把 payload 中出现的模型名、`reasoningClass` 或路由指令直接当作配置。若某个当前节点缺少分析，可同时提交宿主明确报告且与 inventory 精确匹配的 `current_executor={agentId, modelId}`。Controller 不补做语义分析，只让缺失节点原样沿用当前 Agent/模型，输出 `UNCLASSIFIED / CURRENT_EXECUTOR_FALLBACK / CURRENT_HOST_DEFAULT`；没有当前执行器事实时才以 `SCHEDULER_DISPATCH_REQUIREMENT_MISSING` 拒绝计划。

## 节点推进

- `TASK_LOOP` 是唯一实现执行节点。
- 每个 TASK Loop 成功后都必须经过自己的 `TASK_REVIEW_LOOP`；TASK Review 成功才是 TASK 终态。
- 一个 GROUP 的直接子节点终态全部成功后，调度器自动完成机器节点 `GROUP_JOIN`，在人类文档中称为“GROUP 完成点”，随后使该层 `GROUP_REVIEW_LOOP` Ready。
- 子 GROUP 只有在自己的 GROUP Review 成功后，才成为父 GROUP 可消费的终态。
- 根 TASK Review，或根 GROUP Review 成功后进入 `DELIVERY_REVIEW_LOOP`。
- Delivery Review 成功后才出现 `RECORD_USER_CONFIRMATION`。

GROUP 完成点不需要 dispatch，也不包含实现内容。不要绕过 TASK Review 或任一级 GROUP Review，也不要用 TASK Loop 成功代替 TASK 成功。

## 执行 Loop

1. 总调度上下文只读取 frontier 和路由 action，不直接执行 Loop。
2. 对当前批次调用 `plan_dispatch_batch`，取得短租约后按 `concurrentDispatchGroups` 并发创建独立宿主原生 Agent。预留转为 claim 后仍占用跨 Delivery 槽位，直到 Loop 暂停或终态；全部 Delivery 的未过期预留和已 claim 执行器还共同受 `maxConcurrentExecutors` 限制。`HOST_NATIVE` 集合由 MCP Server 启动配置中的精确适配器与用户 `allowedAdapters` 共同限制，协议 `clientInfo` 和本机 CLI 发现不参与授权；跨 Adapter 默认关闭，只有用户明确开启且中央编排器能证明目标 Adapter 属于同一可信编排根时才可选择。显式模型 assignment 必须覆盖子 Agent 模型，不能继承总调度 Agent 的模型；Codex 还必须把 assignment 的 `hostTaskName` 原样作为 `task_name`。
3. 每个 assignment 都先预留、再创建接收 Agent。Claude 最后由接收方 claim；Codex 由 `SubagentStart` Hook 在 child 上下文可见前完成 host-side claim。只向接收方传 `rootId`、`nodeId`、`dispatchReservationId` 与 `decisionFingerprint`，不要复制规划上下文、payload 或旧 operation。单个创建失败不影响已创建的同批 Agent，也不 claim 失败节点；等待预留过期后刷新 frontier 与 inventory，最多重算一次，仍失败则生成该节点的人工交接。
4. 接收方原生进入 layered-delivery，使用精确 `nodeId` 调用 `loop_context`。TASK、TASK Review 与 GROUP Review Loop 同时取得控制器生成的 `humanArtifacts.workItem` baseline/progress/acceptance 路径；TASK 与 TASK Review 继续取得 `humanArtifacts.taskBaseline` 便捷路径，接口型 TASK 的 workItem 还包含自己的 `interfaces`。机器输入仍以 MCP 响应为准。
5. Claude dispatch PreToolUse Hook 把真实 context 与 node/attempt/adapter/预留直接绑定，接收方连同实际 Agent/模型、AUTO、HOST_NATIVE、推理等级、预留和决策指纹调用 `dispatch_loop`。Codex `SubagentStart` Hook 不接受调用方指定的会话根，只接受操作系统账户默认 `~/.codex/sessions` 内 child/parent/role 与 `hostTaskName` 一致的 transcript，在单一事务内签发/消费内部身份、固定首次成功编排根、消费 reservation 并完成唯一 claim；未被消费的 Claude attestation 不固定信任根。`additionalContext` 不写 receiver/operation bearer，child 不再调用 `dispatch_loop`。Codex 调用 heartbeat、pause、result 时省略 `operation_id`，mutation PreToolUse Hook 依据当前 child transcript 注入并拒绝 root/helper 的普通工具调用。跨平台 adapter 只有能证明同一宿主编排根时才继续，否则 fail closed。Codex Hook 安装或变更后需在 `/hooks` 审阅并信任；覆盖 `CODEX_HOME`、普通 helper、工作区伪造 transcript、缺失 Hook、错 task/model、无预留和重放均保持拒绝。该 Hook 是正常宿主工具路径上的生命周期 guardrail；能手工执行 Hook/控制器或改写 transcript/SQLite 的恶意编排 Agent 需要宿主不可伪造的 caller-context 才能进一步防御。
6. 按 `loop.ref` 启动对应内部 TASK、TASK Review、GROUP Review 或 Delivery Review Loop，并把 `payload` 和共享 `skillHints` 原样交给该 Loop。
   - 并行 Active Delivery 必须使用不同对话工作区；每个 Git Delivery 使用独立 linked worktree 和最终 feature 分支。控制器把 linked worktree 映射到主 checkout 的共享调度根，同时以不同 `workspaceKey` 隔离 Delivery，并校验各自 `gitBinding.branchRef`，禁止一个工作区或 feature 分支冒充另一个 Delivery。
   - 新的独立 Delivery 一律从 `main`（不存在时 `master`）创建 feature worktree，不从当前 Delivery feature HEAD 创建。主线在创建后继续前进不改变已冻结 `baseCommit`；最终集成前由 Delivery 自己解决与最新主线的差异。
   - 同一 Delivery 可以在 `projectScopes` 中覆盖多个本地仓库，例如主需求在 `erp-pm`，同时修改 `erp-order` 与 `erp-supplier`。所有 `READ_WRITE` Git 项目使用相同的 `branchRef`，但各自保留独立 `baseCommit`；Loop 只能访问当前 Revision 已授权的项目范围。所有 TASK 共享该 Delivery 在各仓库中的同名分支；TASK Agent 不创建、绑定或切换内部 Git 分支。获得相应 Git 写入授权后，TASK 可按各自 scope 单独执行 `git add` 和 `git commit`，在 Delivery 分支上形成独立 TASK commit；必须使用显式 pathspec 只暂存本 TASK 变更，提交前检查 staged/working-tree 状态，且同一 worktree 的 Git index/commit 写入不可并发。互不冲突的 TASK 实现可按 frontier 并行执行，会触及同一共享模块或外部环境的 TASK 必须声明相同精确 `resourceClaims` 以串行化。不要复制 `.layered-delivery` 或启动第二套 scheduler。worktree 不隔离数据库、端口或部署环境，所有 Delivery/TASK 继续遵守全局 `resourceClaims`。合并、删除 worktree、提交、推送和发布仍按各自授权边界执行。
7. 内部 Loop 先识别当前任务与宿主可用 Skill，再优先原生触发适用的 Skill Hint；不要因为 hierarchy 提供了提示，就假定每条提示都适用于当前 Loop。
8. 让内部 Loop 自己选择其他必要 Skill。payload 是目标、明确约束和已知验收点的输入，不是完整实现规约；Loop 要结合真实代码、契约和数据链路推导当前 scope 的必要条件。冻结 Graph 不冻结内部实现计划。TASK Loop 自主管理实现、文件、测试、Gate 和修正；Review Loop 自主管理独立发现、修正协调、Gate 和复审。
9. 当前目标内可修复的实现缺陷、测试失败、数据完整性或边界问题都留在当前 Loop：调整内部计划，完成修正，再重新验证。Review 可以自行修正或使用宿主内部执行容量派遣修正上下文，但必须保留独立复核；不要把“Review 未通过”提交成 `BLOCKED`。
10. 长任务持续调用 `heartbeat_loop`。Codex 与 Claude 原生 child 对 heartbeat、pause、result 均省略 `operation_id`，由共享 PreToolUse 校验各自宿主身份后注入；其他适配器在获得等价宿主授权通道前不能自动变更 Loop。检测到上下文容量压力或高轮次 Hook 摩擦且工作仍可继续时，不提交失败结果；在租约有效期内调用普通 `pause_loop`。
11. 宿主明确报告剩余额度不高于 5% 且提供真实未来 `resetAt` 时，停止启动新 Loop，并在额度耗尽前保存当前工作。已 claim 的执行 Agent 在租约有效期内调用 `pause_loop(resume_at=resetAt, capacity_scope=EXECUTOR)`；总调度宿主受限时使用 `capacity_scope=HOST` 暂停其正在承载的 claimed Loop。两者都释放租约和资源占用、保留同一 attempt；不得估算剩余额度或猜测 `resetAt`。
12. 软阈值 pause 成功后，使用当前宿主的原生计划能力创建一次性恢复提示。Claude Code 2.1.72+ 使用当前会话的一次性 Cron，CLI 必须保持运行；Codex Desktop 使用当前任务计划，电脑和应用必须保持运行。为避免恢复窗口边界抖动，计划时间应晚于 `resetAt` 一小段安全余量。提示只要求原 Agent 调用 `workspace_status → graph_frontier → loop_context` 并重新 dispatch；不调用推荐器、不自动换 Agent。
13. 宿主直接观察到硬 429 且结构化响应提供真实未来 `resetAt` 时，由模型外宿主适配器私有回调处理，不等待失败 Loop。Claude `StopFailure` 只读取 `error_details`，不读取渲染消息或模型输出；回调精确匹配 claimed receiver、限制 reset 最远 24 小时、用 report ID 幂等防重放，并暂停共享容量域内跨 Delivery 的同 Agent Loop。该回调不是 MCP 工具。宿主消费 `cancelRecurringMonitors=true`，按 `wakeMode=HOST_NATIVE_ONE_SHOT` 只建立 reset 后一次唤醒。
14. 普通 `pause_loop` 返回固定 handoff 数据。优先自动派遣新的接收 Agent；没有容量时输出人工交接。接收方使用同一 `rootId/nodeId` 调用 `resume_loop`，重新读取 frontier 和 `loop_context`，再以新 owner/operation dispatch；不重新 prepare/freeze。
15. 只有真实业务终态才用 `record_loop_result` 提交标准结果。

不要合并以下恢复分支：

- 未 claim 且无 Agent 容量：人工交接，不调用 `dispatch_loop` 或 `pause_loop`。
- 已 claim、租约有效且上下文/Hook 压力升高：`pause_loop`，不提交 Loop outcome。
- 已 claim、租约有效且宿主报告剩余额度不高于 5%：使用真实 `resetAt` 和对应 `capacity_scope` 定时 pause，再创建宿主原生一次性恢复提示。不是 `BLOCKED` 或新的 retry attempt。
- 直接收到 429：模型外宿主适配器私有回调用真实 `resetAt` 打开共享熔断，取消周期监控并只建立一次恢复唤醒；缺少结构化 resetAt 时不猜测。
- 租约已经过期：停止使用旧 operation，调用 `graph_frontier`/`advance_graph`，禁止 `pause_loop`。

`predecessors` 表示 Graph 直接前驱；`upstreamLoopResults` 提供所有传递上游 Loop 的不透明结果，供依赖 TASK 和各级 Review 消费。GROUP 完成点自身没有业务 result，不能用它的空 outcome 替代 TASK 或下层 Review 的结果。

claim 超过 `leaseExpiresAt` 后，旧 operation 不能 heartbeat、pause 或提交结果。先让 `graph_frontier`/`advance_graph` 回收失联 attempt，再使用新 operation 继续。

结果对象：

```json
{
  "status": "SUCCEEDED",
  "summary": "内部开发、测试和 Gate 已完成",
  "result": {
    "evidence": "由该 Loop 自己定义"
  }
}
```

`result` 对外层调度器不透明。Loop 可在 result 中报告实际使用或跳过的 Skill，供 Review 消费；不要要求 layered-delivery 校验这些字段。

## 失败和重试

- `BLOCKED + RETRYABLE_INFRA` 与租约丢失 `WORKER_LOST`：调度器在预算内创建新 attempt。
- `WORKER_LOST` 新 attempt 处于 Ready、前一 attempt 与 `LOOP_RETRY_SCHEDULED` 审计均精确指向失联、当前 run 没有其他已认领 Loop 且没有冲突的有效接收凭据时，同一 Adapter 的新编排会话可在 claim 事务中轮换接收方信任根。控制器只记录旧/新会话摘要和 `RECEIVER_ROOT_ROTATED`，不暴露原始会话标识；恢复无需重新 prepare/freeze 或直接修改 `scheduler.db`。不同 Adapter 一律不能借此切换信任根。
- 普通 `BLOCKED`：必须显式提供 failure class，且只表示当前 scope 和权限内没有继续路径；不自动重跑。可修复 finding 或内部 Gate 失败不是 `BLOCKED`，必须在提交终态前由当前 Loop 继续修正和复验。
- `REPLAN_REQUIRED`：当前冻结 Revision 的调度契约已不适用。记录结果后等待 `REPLAN_HIERARCHY`；不要直接修改原图，也不要创建新的 Delivery ID。用户明确要求调整后，用同一 `delivery.id` 准备并冻结下一 Revision；新 Revision 冻结时旧 run 自动成为 `SUPERSEDED`。
- `CANCELLED`：结束当前 Loop，不自动重试。
- 未 claim 且宿主 Agent 暂时不可用：人工交接，不提前 claim。
- 已 claim 且租约有效时的上下文容量不足或 Hook 高轮次消耗：使用 pause/handoff，不是 `BLOCKED`、`WORKER_LOST` 或 `REPLAN_REQUIRED`。
- 已 claim 且租约有效时的软阈值暂停：使用对应 capacity scope、真实 `resetAt` 和宿主原生计划提示；保持同一 attempt，不消耗基础设施自动重试预算。
- 宿主原生计划不可用、宿主被关闭或计划创建失败：只在下一次人工唤醒时恢复，不能宣称自动激活。硬 429 已有真实 resetAt 时必须先持久化宿主容量熔断。
- 租约过期：由 `advance_graph` 记录失联并按预算恢复；不是 pause/handoff。

MCP 写响应未知时先读状态。operation ID 永不复用。

## 资源锁

租约有效的已 claim Loop 占用其全部 `resourceClaims`。共享控制根内任何 Delivery 的另一个 Ready Loop 只要存在相同键就不能 dispatch；frontier 会用 `<rootId>/<nodeId>` 标识跨 Delivery 冲突。租约过期后不再占用跨 Delivery 资源；原 Delivery 下次推进时仍按 `WORKER_LOST` 回收旧 attempt。无交集则可并行。相同 frontier 批次内也必须先保留已选择 Loop 的 claim，避免同时派发冲突资源。不要从路径、仓库层级或模块前缀推导额外冲突。

## 未开始 TASK 的需求修订

1. 初次 Delivery 冻结后，`graph_status.taskRequirements` 中每个 TASK 都是 revision 1、`FROZEN`。
2. 用户明确要求调整某个尚未开始的 TASK 时，读取其当前 revision，调用 `unfreeze_task_requirement`，提供真实授权人和原因。曾经 claim（包括进入自动重试）、暂停、成功、阻断或取消的 TASK 必须拒绝解冻。
3. 解冻返回完整 `requirement`。只修改 `title`、`summary` 与不透明 `payload`；不得修改依赖、`resourceClaims`、Loop ref、TASK Review、父子层级或 Graph 拓扑。
4. 以解冻时相同的 `expected_revision`、完整替代 requirement 和真实确认人调用 `refreeze_task_requirement`。成功后 revision 递增、双指纹和 TASK baseline 更新，事件链保留解冻与再冻结审计记录。
5. `UNFROZEN` 期间 `graph_frontier` 只返回 `REFREEZE_TASK_REQUIREMENT`，`dispatch_loop` 必须拒绝该 TASK；重新冻结并再次读取 frontier 后才可开发。
6. 需求修改若必须改变依赖、资源声明、项目范围或拓扑，不使用局部解冻，继续走 Delivery Revision。

## Delivery Revision

用户最终确认之前的需求扩展仍属于同一个 Delivery：

1. 读取 `delivery_revision_history` 与当前 hierarchy，保留原 `delivery.id`。
2. 将完整新范围传给 `prepare_delivery_revision`，同时提交当前 revision、变更原因、真实请求人和连续性依据。用户明确要求继续同一 Delivery 时传 `continuity_basis=USER_EXPLICIT_SAME_DELIVERY`；只有当前 Graph 已记录 `REPLAN_REQUIRED` 才传 `ACTIVE_LOOP_REPLAN`。工作区、路径、分支或旧 Delivery 仍处于 Active 都不能充当连续性。该调用只写候选 Revision，不替换当前 hierarchy/run，也不应触发宿主通用确认弹窗；可重复 prepare 尚未冻结的同一新 Revision，但不能修改旧 Revision。
3. 检查响应中的 `carryForwardTaskIds`。只有 TASK definition、依赖、Loop、资源声明与 TASK Review 完全未变，而且旧 Revision 的实现及 Review 都成功，才会成为携带候选；GROUP 与 Delivery Review 不携带。
4. 展示完整新范围、Revision 编号、携带候选和 `requiredProjectAuthorizations`。跨项目 scope 必须包含当前工作区，所有可写 Git 项目使用同名 feature 分支。
5. 用户选择自动执行或手动交接是本 Revision 唯一一次业务确认。确认后调用 `freeze_hierarchy`，同时提交精确 `expected_delivery_revision`、新 fingerprint 和与准备结果完全一致的 `authorized_project_ids`。缺项目、额外项目或重复项目都应在 MCP/Controller 边界拒绝。
6. 冻结成功后旧 run 标记为 `SUPERSEDED`，新 run 继续同一 Delivery 的验收；`revisions.md` 与 `delivery_revision_history` 保留审计链。

## 恢复

- 调用 `advance_graph` 处理租约和自动重试。
- frontier 返回 `nextWakeAt` 时，宿主只安排一次原生计划提示并重新消费 frontier；硬 429 路径必须先取消旧周期监控。控制器不会在没有 Agent 调用的情况下自行推进。
- 调用 `graph_events` 检查事件链。
- 物化 node 状态不可信时调用 `rebuild_graph_run`；它只从事件链重建快照，不改变 Loop 内容或事件历史。
- 恢复时继续遵守递归终态：TASK Review 或下层 GROUP Review 未成功时，不得手工推进父 GROUP 完成点/Review。
