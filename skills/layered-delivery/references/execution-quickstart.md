# 递归 Graph 执行

用于自动 Graph，以及手动 handoff 接收后启动的同一冻结 Graph 的运行、恢复与阻断处理。两种模式共享依赖、资源锁、进度、Review、验收和恢复契约；差异只在 TASK 实现的 claim 来源。

## Frontier

调用 `graph_frontier` 并执行全部 action：

- 每次响应同时读取 `progressMonitor`。后台 Agent 运行期间严格按 `recommendedPollSeconds`（当前为 10 秒）继续刷新，不得把 90 秒首次心跳告警阈值当作 sleep 或轮询间隔；宿主收到原生 child 完成通知时立即中断等待并刷新 frontier。仅在进度表或预警变化时，把 `markdownTable` 原样作为中文表格展示到主 Agent 窗口。`graph_events` 是诊断接口，不把原始事件、operation 或 reservation 日志直接展示给普通用户。

- `CLAIM_MANUAL_TASK`：只出现在 `start_manual_handoff` 已启动的 manual Graph，且只对应 `TASK_LOOP`。总协调上下文不得实现；由独立 receiver 读取 `loop_context`，显式提交真实 receiving context、唯一 operation 与 `dispatch_mode=MANUAL`，claim 后立即独立 heartbeat，再进入完整 TASK Loop。
- `DISPATCH_LOOP`：自动 Graph 的全部 Loop，以及 manual Graph 中 TASK 完成后的全部 Review 都按当前可信宿主 Adapter 调用一次 `plan_dispatch_batch`。计划直接预留外层 receiver，固定 `modelPolicy=CURRENT_HOST_INHERIT`，不接收或返回模型建议。receiver 读取 `loop_context` 后凭预留 AUTO claim。
- `WAIT_FOR_DISPATCH_RECEIVER`：另一个调度器已为该 Ready Loop 取得短租约派遣预留；不得重复创建 Agent，等待接收方 claim 或预留过期。
- `CONTINUE_OR_HEARTBEAT_LOOP`：继续当前 Loop，并在租约到期前 heartbeat。
- `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把暂停节点路由给新的接收上下文；接收方 resume 后重新读取 frontier 并 dispatch。
- `WAIT_FOR_EXECUTOR_CAPACITY`：receiver 在软阈值暂停后等待宿主原生的一次性恢复提示；到时由原宿主重新消费 frontier。
- `WAIT_FOR_HOST_CAPACITY`：总调度 Agent 在软阈值暂停后等待宿主原生的一次性恢复提示；到时重新消费 frontier。两种等待都固定 `PAUSE_AND_RESUME`，不自动换 Adapter、模型或 Worker。
- `RESOLVE_LOOP_BLOCK`：展示 Loop 返回的摘要和不透明 result，等待外部条件或人工决定。
- `REPLAN_HIERARCHY`：展示外层契约变化及当前 Revision 无法继续的原因，等待用户决定。用户明确要求修改且尚未最终验收时，保持同一 `delivery.id` 调用 `prepare_delivery_revision`；重新评审、授权项目并冻结后，旧 run 自动成为 `SUPERSEDED`。
- `REFREEZE_TASK_REQUIREMENT`：该未开始 TASK 的需求处于解冻编辑态，当前不可派遣。按用户已经明确提出的修改完成 `unfreeze_task_requirement → refreeze_task_requirement`，再重新读取 frontier。
- `RECORD_USER_CONFIRMATION`：`STANDARD` 的全部 Review 已成功，或 `LIGHT` 的唯一 TASK 已完成定向验证；读取 [acceptance.md](acceptance.md)，等待用户最终接受。

不要自行增加 TASK/Gate 节点，也不要根据 payload 内容改变 frontier 顺序。

用户选择自动执行时，`select_execution_mode(AUTOMATIC)` 已记录一次业务确认。Claude Code 与 Codex 的 Git Delivery 都消费 `worktreeSetup.hostDispatch`：Claude 在同一顶层会话内创建/进入稳定 Delivery worktree，启动后台 `layered-delivery:delivery-coordinator` 后立即返回 primary；Codex 创建 `environment=worktree` 的后台项目任务。后台协调方调用 `workspace_status(root_id)` 并以原双 fingerprint 调用 `resume_execution_mode`，随后进入本执行循环。主会话不切换协调 checkout、不实现 Loop，只以 `MONITOR_ONLY` 从共享控制根读取 frontier；不得要求用户手动 `cd`、不得启动新顶层 CLI、不得重复 handoff。任何路径都不得再次展示选择器、询问或重放 prepare/freeze。Ready 批次按当前可信 `host_adapter_id` 调用一次 `plan_dispatch_batch`，直接取得 reservation 和并发组。计划不展示模型表、不等待调整窗口，也不接收 inventory、node requirements、current executor、模型或 effort 参数。用户选择手动开发时，`select_execution_mode(MANUAL)` 只生成冻结内容包与嵌入的接收提示词；接收 CLI 选定实际工作区后，在任何代码工作前调用 `start_manual_handoff`，随后立即消费 frontier。

### 宿主继承与内部 Worker

外层 receiver 始终继承当前宿主模型和默认推理设置。模型不进入 reservation、claim 授权或决策指纹；已 claim receiver 也不能在原 attempt 中热切身份。需要更换 receiver 时必须 pause 或等待租约回收，再使用新 attempt、reservation 和独立 receiving context。

receiver 完成 claim、读取 `loop_context` 并提交首次独立 heartbeat 后，可以在 Loop 内根据成本、任务和本机能力自主创建 Codex、Claude、Grok、DeepSeek 等 Worker，选择其模型、effort、并发和升级路径。内部 Worker 只向 receiver 返回工作结果，不得调用任何 Graph mutation 工具或获得 operation、attestation、reservation。新增 Worker 供应商不改变 Layered Delivery；只有要让供应商成为外层 receiver 时才需要新增可信 Adapter。

Plugin 内置外层 receiver 最大并发 4 与固定 `quotaExhaustionPolicy=PAUSE_AND_RESUME`。MCP Server 不读取用户级编排配置；机器上残留的旧 `orchestrator.json` 不参与启动、派遣、授权或指纹。

## 节点推进

- `TASK_LOOP` 是唯一实现执行节点。
- `STANDARD` 中每个 TASK Loop 成功后都必须经过自己的 `TASK_REVIEW_LOOP`；TASK Review 成功才是 TASK 终态。
- 一个 GROUP 的直接子节点终态全部成功后，调度器自动完成机器节点 `GROUP_JOIN`，在人类文档中称为“GROUP 完成点”，随后使该层 `GROUP_REVIEW_LOOP` Ready。
- 子 GROUP 只有在自己的 GROUP Review 成功后，才成为父 GROUP 可消费的终态。
- 根 TASK Review，或根 GROUP Review 成功后进入 `DELIVERY_REVIEW_LOOP`。
- Delivery Review 成功后才出现 `RECORD_USER_CONFIRMATION`。
- `LIGHT` 只有一个根 TASK，不创建任何 Review 或 GROUP 节点；TASK 成功后直接出现 `RECORD_USER_CONFIRMATION`。实际修改一旦触及关键边界或影响范围无法确认，必须以 `REPLAN_REQUIRED` 升级同一 Delivery 的下一 Revision 为 `STANDARD`。

GROUP 完成点不需要 dispatch，也不包含实现内容。`STANDARD` 不得绕过 TASK Review 或任一级 GROUP Review，也不要用 TASK Loop 成功代替 TASK 成功。

## 执行 Loop

1. 总调度上下文只读取 frontier 和路由 action，不直接执行 Loop。
   - manual handoff 的总协调上下文先调用一次 `start_manual_handoff`；响应未知时用 `workspace_status(root_id)` 判定，已经是同 fingerprint 的 `executionMode=manual` 时幂等恢复，不重复 prepare/freeze。
   - `CLAIM_MANUAL_TASK` 不进入 `plan_dispatch_batch`。总协调上下文创建或切换到独立 TASK receiver，只传 root/node 和 handoff 双 fingerprint；receiver 读取一次 `loop_context`，用显式 receiving context、唯一 operation 和 `MANUAL` claim。MANUAL 不带 reservation、decision、模型、reasoning class、HOST_NATIVE transport 或 attestation。claim 后仍必须先独立 heartbeat，并遵守后续所有进度、租约、结果和恢复规则。
   - `MANUAL` 只能 claim manual Graph 的 `TASK_LOOP`；自动 Graph 的 TASK、任何模式的 TASK/GROUP/Delivery Review 都拒绝 MANUAL。Review 必须由与全部上游实现/Review context 不同、且不是上游 Loop 派生的独立宿主原生接收上下文 AUTO claim。
2. 对当前批次调用一次 `plan_dispatch_batch`，只提交 Graph fingerprint 与当前可信宿主上下文。取得计划后按 `concurrentDispatchGroups` 并发创建独立 receiver；assignment 使用 `hostAdapterId`、`receiverAgentId` 和 `modelPolicy=CURRENT_HOST_INHERIT`，不包含模型、reasoning class 或 model selection。预留与已 claim receiver 共同受 Plugin 内置 `maxConcurrentExecutors=4` 限制。可信 Adapter 来自 Plugin 注册和宿主身份通道；协议 `clientInfo`、PATH 中的二进制、本机 CLI 或内部 Worker 均不参与授权。
3. 每个 assignment 都先预留、再立即创建 receiver，不得在取得 reservation 后继续读文档、检查实现或做额外分析；当前 reservation 只有 300 秒，receiver 必须优先 claim。Claude 最后由 receiver claim；Codex 由 `SubagentStart` Hook 在 child 上下文可见前完成 host-side claim。只向 receiver 传 `rootId`、`nodeId`、`dispatchReservationId` 与 `decisionFingerprint`，不要复制规划上下文、payload 或旧 operation。单个创建失败不影响已创建的同批 receiver，也不 claim 失败节点；等待预留过期后刷新 frontier，最多重派一次，仍失败则生成该节点的人工交接。
4. 接收方原生进入 layered-delivery，使用精确 `nodeId` 调用 `loop_context`。其中 `projectScopes` 是 Controller 按当前 Delivery 的冻结分支、Git common directory 与 workspace 绑定只读解析后的实际 worktree 路径；`projectScopeAnchors` 保留 preview 时的冻结仓库锚点，仅供审计，不是开发目录。receiver 必须直接使用 `projectScopes`，不得为“校准环境”创建、`checkout` 或 `switch` 分支。TASK、TASK Review 与 GROUP Review Loop 同时取得控制器生成的 `humanArtifacts.workItem` baseline/progress/acceptance 路径；TASK 与 TASK Review 继续取得 `humanArtifacts.taskBaseline` 便捷路径，接口型 TASK 的 workItem 还包含自己的 `interfaces`。机器输入仍以 MCP 响应为准。
5. AUTO assignment 的 Claude child 进入后只读取一次 `loop_context`，随即调用 `dispatch_loop`；模型与 reasoning 参数不提交。Claude dispatch PreToolUse Hook 把真实 context 与 node/attempt/adapter/预留绑定并注入 receiver 凭证。Codex `SubagentStart` Hook 只接受操作系统账户默认 `~/.codex/sessions` 内 child/parent/role 与 `hostTaskName` 一致的 transcript，在单一事务内签发/消费内部身份、固定首次成功编排根、消费 reservation 并完成唯一 claim。`additionalContext` 不写 receiver/operation bearer。两类 AUTO receiver 均在 claim 后读取一次 `loop_context`，随即在任何代码检查、分析、读写或测试前提交首次独立 `heartbeat_loop`；claim 自带的初始租约不算 heartbeat。Codex 调用 heartbeat、progress、pause、result 时省略 `operation_id`，mutation PreToolUse Hook 依据当前 child transcript 注入并拒绝 root/helper 和内部 Worker 的调用。新平台只有实现等价可信外层 Adapter 时才能领取，否则 fail closed。
6. 按 `loop.ref` 启动对应内部 TASK、TASK Review、GROUP Review 或 Delivery Review Loop，并把 `payload` 和共享 `skillHints` 原样交给该 Loop。
   - Claude Code 与 Codex 的自动 Git Delivery 都使用独立、稳定的 linked worktree。控制器把 linked worktree 映射到 primary checkout 的共享调度根，同时以不同 `workspaceKey` 隔离 Delivery，并校验各自 `gitBinding.branchRef`，禁止一个工作区或 feature 分支冒充另一个 Delivery。Claude Hook 以真实 cwd 的一次性证明把固定 MCP 控制根与实际执行 worktree 解耦；`loop_context` 只把本 Delivery 已验证的 linked worktree 作为有效 `projectScopes` 下发，receiver 不读取或切换另一个 Delivery 的检出分支。
   - 新的独立 Delivery 一律从 `main`（不存在时 `master`）创建 feature worktree，不从当前 Delivery feature HEAD 创建。主线在创建后继续前进不改变已冻结 `baseCommit`；最终集成前由 Delivery 自己解决与最新主线的差异。
   - 同一 Delivery 可以在 `projectScopes` 中覆盖多个本地仓库，例如主需求位于 `project-api`，同时修改 `project-provider` 与 `project-consumer`。所有 `READ_WRITE` Git 项目使用相同的 `branchRef`，但各自保留独立 `baseCommit`；Loop 只能访问当前 Revision 已授权的项目范围。所有 TASK 共享该 Delivery 在各仓库中的同名分支；TASK Agent 不创建、绑定或切换内部 Git 分支。获得相应 Git 写入授权后，TASK 可按各自 scope 单独执行 `git add` 和 `git commit`，在 Delivery 分支上形成独立 TASK commit；必须使用显式 pathspec 只暂存本 TASK 变更，提交前检查 staged/working-tree 状态，且同一 worktree 的 Git index/commit 写入不可并发。互不冲突的 TASK 实现可按 frontier 并行执行，会触及同一共享模块或外部环境的 TASK 必须声明相同精确 `resourceClaims` 以串行化。不要复制 `.layered-delivery` 或启动第二套 scheduler。worktree 不隔离数据库、端口或部署环境，所有 Delivery/TASK 继续遵守全局 `resourceClaims`。合并、删除 worktree、提交、推送和发布仍按各自授权边界执行。
7. 内部 Loop 先识别当前任务与宿主可用 Skill，再优先原生触发适用的 Skill Hint；不要因为 hierarchy 提供了提示，就假定每条提示都适用于当前 Loop。
8. 让内部 Loop 自己选择其他必要 Skill。payload 是目标、明确约束和已知验收点的输入，不是完整实现规约；Loop 要结合真实代码、契约和数据链路推导当前 scope 的必要条件。冻结 Graph 不冻结内部实现计划。TASK Loop 自主管理实现、文件、测试、Gate 和修正；Review Loop 自主管理独立发现、修正协调、Gate 和复审。
9. 当前目标内可修复的实现缺陷、测试失败、数据完整性或边界问题都留在当前 Loop：receiver 调整内部计划，按需创建成本合适的 Codex、Claude、Grok、DeepSeek 等 Worker，完成修正后重新验证。内部 Worker 只能向 receiver 返回结果；只有 receiver 能上报进度或终态。Review 必须保留独立复核，不要把“Review 未通过”提交成 `BLOCKED`。
10. `STANDARD` Loop 在领取、代码检查完成、确认根因、完成修改、测试开始与结束、发现问题、修复、复审和最终验证等有意义的阶段立即调用 `report_loop_progress`。长时间测试或构建必须由 receiver 以非阻塞进程/宿主异步命令启动，或交给独立监控 Worker，使外层 receiver 不被单次 shell/tool call 占满；开始前 progress + heartbeat，运行期间至少每 `heartbeatSeconds` heartbeat，结束后立即 heartbeat + progress。`LIGHT` 只在发现问题和最终验证时上报，执行时间很短且没有问题时可只报最终验证。`summary_zh`、`completed_zh` 与 `next_step_zh` 使用用户当前语言，测试结果使用结构化计数；字段名为现有 schema v3 契约，不代表内容必须包含中文字符。禁止提交原始终端日志或内部推理。进度事件是可观测事实，不是 Graph 状态迁移，也不续租。
11. 首次独立 heartbeat 之后，长任务继续按租约调用 `heartbeat_loop`。宿主未发出 child 完成通知只表示没有终态通知，既不是 heartbeat，也不能证明 receiver 仍存活；`SUSPECT_LOST` 也只证明控制面心跳和进度都静默，不能自行归因为 Maven 阻塞、会话错配或进程退出。Codex 与 Claude 原生 child 对 heartbeat、progress、pause、result 均省略 `operation_id`，由共享 PreToolUse 校验各自宿主身份后注入；其他适配器在获得等价宿主授权通道前不能自动变更 Loop。检测到上下文容量压力或高轮次 Hook 摩擦且工作仍可继续时，不提交失败结果；在租约有效期内调用普通 `pause_loop`。
12. 宿主明确报告剩余额度不高于 5% 且提供真实未来 `resetAt` 时，停止启动新 Loop，并在额度耗尽前保存当前工作。已 claim 的执行 Agent 在租约有效期内调用 `pause_loop(resume_at=resetAt, capacity_scope=EXECUTOR)`；总调度宿主受限时使用 `capacity_scope=HOST` 暂停其正在承载的 claimed Loop。两者都释放租约和资源占用、保留同一 attempt；不得估算剩余额度或猜测 `resetAt`。
13. 软阈值 pause 成功后，使用当前宿主的原生计划能力创建一次性恢复提示。为避免恢复窗口边界抖动，计划时间应晚于 `resetAt` 一小段安全余量。提示只要求原宿主调用 `workspace_status → graph_frontier → loop_context` 并重新 dispatch；额度策略固定 `PAUSE_AND_RESUME`，不自动换 Adapter、模型或 Worker。
14. 宿主直接观察到硬 429 且结构化响应提供真实未来 `resetAt` 时，由模型外宿主适配器私有回调处理，不等待失败 Loop。Claude `StopFailure` 只读取 `error_details`，不读取渲染消息或模型输出；回调精确匹配 claimed receiver、限制 reset 最远 24 小时、用 report ID 幂等防重放，并暂停共享容量域内跨 Delivery 的同 Agent Loop。该回调不是 MCP 工具。宿主消费 `cancelRecurringMonitors=true`，按 `wakeMode=HOST_NATIVE_ONE_SHOT` 只建立 reset 后一次唤醒。
15. 普通 `pause_loop` 返回固定 handoff 数据。优先自动派遣新的接收 Agent；没有容量时输出人工交接。接收方使用同一 `rootId/nodeId` 调用 `resume_loop`，重新读取 frontier 和 `loop_context`，再以新 owner/operation dispatch；不重新 prepare/freeze。
16. 只有真实业务终态才用 `record_loop_result` 提交标准结果。

## 后台进度与失联预警

- `report_loop_progress` 只写入 `LOOP_PROGRESS_REPORTED` 可观测事件，不改变节点状态、不更新 `lastHeartbeatAt`、不延长 `leaseExpiresAt`。
- `graph_status` 与 `graph_frontier` 返回中文 `progressMonitor.markdownTable`，包含节点、attempt、外层 receiver、阶段、摘要、已完成、下一步、测试、心跳/租约和健康状态。内部 Worker 事实只来自最终 `workerTelemetry`，显示为非权威信息。
- claim 后 90 秒仍无首次独立 heartbeat：`SUSPECT_NOT_STARTED / 疑似未启动`。
- 已有 progress 但 90 秒仍无首次独立 heartbeat：`HEARTBEAT_MISSING / 已开始但无独立心跳`。
- heartbeat 仍在预期窗口内，但超过 5 分钟没有 progress：`ALIVE_WITHOUT_PROGRESS / 存活但无可见进展`。
- heartbeat 与 progress 均超过 `heartbeatSeconds + graceSeconds`：`SUSPECT_LOST / 疑似失联`。
- `SUSPECT_LOST.diagnosis.claimMatched=true` 表示该 attempt 最初已经合法 claim；`cause=UNDETERMINED_CONTROL_PLANE_SILENCE` 明确禁止仅凭告警猜测长命令阻塞、会话身份不匹配或宿主进程仍存活。租约有效时继续监控；只有显式 mutation 错误可证明 operation/receiver 匹配失败。
- lease 到期后，下一次 `graph_frontier` 先调用 `advance_graph`，记录 `CLAIM_LEASE_EXPIRED / WORKER_LOST` 并在重试预算内生成新 attempt。

## 多会话 Review 接力

- 同一 run 的 receiver 根默认固定为首次成功派遣的可信 `hostAdapterId + orchestrator_context_id`。
- 前一 Loop 已 `SUCCEEDED`、下一层 frontier Ready、当前没有任何 `CLAIMED` Loop，且不存在其他主会话的有效 receiver attestation/identity 时，同一可信 Adapter 的新主会话可以派遣下一层 TASK/GROUP/Delivery Review。Controller 在新 claim 中记录 `RECEIVER_ROOT_ROTATED(reason=IDLE_FRONTIER_HANDOFF)`。
- 仍有任一活跃 claim 时，新主会话必须得到 `SCHEDULER_RECEIVER_PARENT_UNTRUSTED`；不得把“多会话接力”解释成接管正在执行的 receiver。跨 Adapter 接力同样拒绝。
- 当前 attempt 已合法 claim 后出现 `SUSPECT_LOST`，说明最初匹配成功；这与“下一层 Review 由新主会话接力”是两类问题。等待租约到期后的 `WORKER_LOST` retry 仍使用既有恢复规则。
- mutation Hook 拒绝 heartbeat、progress、pause 或 result 时，主 Agent 不得代交结果或手填 operation。等待 lease 回收，修复宿主 Hook/child/model 身份后再派遣新 attempt；新接收方复用工作区成果并重新验证。

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
    "evidence": "由该 Loop 自己定义",
    "workerTelemetry": [
      {
        "phase": "implementation",
        "agent": "codex",
        "model": "gpt-5.6-terra",
        "reasoningEffort": "medium"
      },
      {
        "phase": "review",
        "agent": "unreported",
        "model": "unreported",
        "reasoningEffort": "unreported"
      }
    ]
  }
}
```

`result` 对外层调度器不透明。`workerTelemetry` 由外层 receiver 按 phase 报告内部 Worker 的 agent/model/effort；宿主无法权威观察的值写 `unreported`。它只用于展示、成本分析和后续 Review，不参与授权、路由、重试、指纹或独立性判断。Loop 也可在 result 中报告实际使用或跳过的 Skill；不要要求 layered-delivery 校验这些字段。

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
5. 用户选择自动执行或手动开发是本 Revision 唯一一次业务确认。自动执行调用 `freeze_hierarchy`，同时提交精确 `expected_delivery_revision`、新 fingerprint 和与准备结果完全一致的 `authorized_project_ids`。手动开发调用 `create_manual_handoff` 输出修订后的完整冻结内容包，但不替换当前 run；接收方真正开始开发前需再次确认如何承接该活动 Delivery。
6. 只有自动冻结成功后，旧 run 才标记为 `SUPERSEDED`，新 run 继续同一 Delivery 的验收；`revisions.md` 与 `delivery_revision_history` 保留审计链。

## 恢复

- 调用 `advance_graph` 处理租约和自动重试。
- frontier 返回 `nextWakeAt` 时，宿主只安排一次原生计划提示并重新消费 frontier；硬 429 路径必须先取消旧周期监控。控制器不会在没有 Agent 调用的情况下自行推进。
- 调用 `graph_events` 检查事件链。
- 物化 node 状态不可信时调用 `rebuild_graph_run`；它只从事件链重建快照，不改变 Loop 内容或事件历史。
- 恢复时继续遵守递归终态：TASK Review 或下层 GROUP Review 未成功时，不得手工推进父 GROUP 完成点/Review。
