# 递归 Graph 执行

用于自动 Graph，以及手动 handoff 接收后启动的同一冻结 Graph 的运行、恢复与阻断处理。两种模式共享 claim、项目 scope、operation、heartbeat、资源锁、进度、Review、验收和恢复契约；差异只在 TASK 实现的 claim 输入。AUTOMATIC 的 TASK 与 Review 都使用 reservation + decision fingerprint + 独立 child + 显式 `operation_id`；MANUAL TASK 使用显式 receiving context/operation，且不带 AUTO reservation 或 decision fingerprint。

## Frontier

首次进入、原生 receiver 完成/需要关注、`nextWakeAt` 到达或返回 `ADVANCE_REQUIRED` 时调用一次 `graph_frontier`，并执行全部 action：

- 每次响应同时读取 `progressMonitor.waitDirective`。先完整消费 `immediateActions`，再使用宿主原生 receiver 等待能力（Codex 等价 `wait_agent`/`wait_threads`，Claude 等价 Agent completion wait）等待完成或需要关注事件；事件发生立即调用一次 `graph_frontier`。无事件时最早到 `pollNotBefore` 才调用一次只读 `graph_status`；该截止由 Controller 对齐首次心跳、进度陈旧、失联或租约等下一个有意义健康阈值，宿主不得自行改成 10 秒等固定短周期。只有 `nextWakeAt` 或 `ADVANCE_REQUIRED` 才再次调用 `graph_frontier`。禁止 back-to-back 调用这两个工具。仅在 `changeFingerprint` 变化或新告警需要关注时，把 `markdownTable` 原样作为中文表格展示到主 Agent 窗口。`graph_events` 是诊断接口，不把原始事件、operation 或 reservation 日志直接展示给普通用户。

- `CLAIM_MANUAL_TASK`：只对应 `TASK_LOOP`，来源是 `start_manual_handoff` 已启动的 manual Graph，或指定自动 TASK 的显式人工接管。总协调上下文不得实现或 claim；宿主创建独立原生 child，child 以新的显式 `operation_id` 和 receiving context 调用 `dispatch_loop(MANUAL)`，不带 AUTO reservation/decision。claim 后立即 heartbeat，再解读 claim 已返回的 Loop context；所有保障档都持续 heartbeat 到 result/claim release。
- `DISPATCH_LOOP`：所有 READY `TASK_LOOP` 与 Review 都由当前宿主调用一次 `plan_dispatch_batch`；Controller 按当前 frontier、固定并发槽位和资源锁创建绑定 decision fingerprint 的短租约 reservation。宿主按 `concurrentDispatchGroups` 立即创建独立 child，每个 child 以 `dispatch_transport=HOST_NATIVE`、assignment 和新的显式 `operation_id` 调用 `dispatch_loop(AUTO)`。完整消费所有 assignment 后执行响应的 `postActionWait`：receiver 完成、需要关注或启动失败时立即刷新一次 frontier；无事件则等到最早 reservation 截止时间只刷新一次，禁止连续轮询。
- `WAIT_FOR_DISPATCH_RECEIVER`：另一个调度器已为该 Ready Loop 取得短租约派遣预留；不得重复创建 Agent，等待接收方 claim 或预留过期。
- `CONTINUE_OR_HEARTBEAT_LOOP`：继续当前 Loop，并在租约到期前 heartbeat。当前 claim 使用 5 分钟短租约、60 秒心跳间隔和 2 分钟续租阈值；阈值外的真实心跳只保活，进入阈值后才把到期时间推进到“当前时间 + 5 分钟”。
- `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：把暂停节点路由给新的接收上下文；接收方 resume 后重新读取 frontier 并 dispatch。
- `RESOLVE_LOOP_BLOCK`：展示 Loop 返回的摘要和不透明 result，等待外部条件或人工决定。
- `REPLAN_HIERARCHY`：展示外层契约变化及当前 Revision 无法继续的原因，等待用户决定。用户明确要求修改且 Delivery 仍为 `OPEN/未上线` 时，保持同一 `delivery.id` 调用 `prepare_delivery_revision`。
- `REFREEZE_TASK_REQUIREMENT`：该未开始 TASK 的需求处于解冻编辑态，当前不可派遣。按用户已经明确提出的修改重跑 TASK 切分完整性预检，再完成 `unfreeze_task_requirement → refreeze_task_requirement`；后者从 SQLite 当前不可变 hierarchy 生成并冻结同一 Delivery 的下一 Revision，沿用执行模式并返回新 Run/双指纹。任一未领取 reservation 都绑定旧 Graph 指纹；需求修订入口因此会阻断并返回 `SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE` 与 `retryAfter`，不得让旧 assignment 与新需求并存。
- `RECORD_USER_CONFIRMATION`：Controller 已按 Graph 确认 `STANDARD` 的 Delivery Acceptance/Readiness 节点进入合法成功终态，或 `LIGHT` 的唯一 TASK 已进入合法成功终态。先调用 `delivery_result` 读取确定性结果账本；只有 `completeness.complete=true` 才结合 [acceptance.md](acceptance.md) 展示全部 Loop 结果、证据、验收与 finding，并等待用户最终接受。缺少 TASK 影响范围、未通过或未绑定 scope 的证据、Review 悬空引用及任一 Loop 结果都会失败关闭。这里是完整性门禁，不是 Controller 再做一次技术验收。若业务变更已 commit、工作区干净且 receiver/reservation 全部释放，可先释放物理 workspace turn；Delivery 仍保持待用户确认。

不要自行增加 TASK/Gate 节点，也不要根据 payload 内容改变 frontier 顺序。

用户选择 AUTOMATIC 或 MANUAL 时，`select_execution_mode` 立即持久化一次业务确认；workspace strategy 统一为 `CURRENT_WORKSPACE_SERIAL`。同一物理 checkout 一次只运行一个 Delivery。已选择任一模式的后续 Delivery 都标记为 `QUEUED` 并携带 mode-specific continuation，等待前一个 Delivery 进入 Run 终态，或到达 `RECORD_USER_CONFIRMATION`，且形成可验证业务 commit、working tree/index clean、HEAD 与冻结 binding 一致、所有 receiver/reservation 安全释放。轮到队首后，宿主按 `automaticHostPreparation.actions` 或 `manualHostPreparation.actions` 执行：必要时核对指纹并 stash 业务改动（排除 `.layered-delivery/**`），创建或切换目标 Delivery 的独立分支，再以明确 `rootId` 和双 fingerprint 调用 `resume_execution_mode` 或 `start_manual_handoff`；不得重试 `select_execution_mode` 或再次询问用户。人工 Graph 达到同一安全边界时也可先 commit 并切换新 Delivery 分支，最终用户确认稍后按原 `rootId` 补录。资源冲突、owner dirty、未合并状态、HEAD 漂移或释放状态不明时保持排队。现有 linked checkout 只按普通 current workspace 处理，不自动创建新 worktree。状态恢复始终显式调用 `workspace_status(root_id=...)`。手动交接冻结会持久化 `HANDOFF_READY` Delivery、完整需求快照、MANUAL 选择与 workspace 队列绑定；接收方轮到后显式调用 `start_manual_handoff`，才创建 manual Run 和独立 TASK receiver。

### 接收上下文与内部协作

外层 receiver 由当前宿主创建。Controller 只绑定 Adapter、receiver Agent、reservation、节点、attempt 和 decision fingerprint，不接收额外路由属性。需要更换 receiver 时必须显式 pause 并恢复，或等待租约回收，再使用独立 receiving context 和新的 operation。

receiver 完成 claim 后，先按响应 `heartbeatDirective` 立即提交首次独立 heartbeat，再解读 claim 已返回的 Loop context，并在 Loop 内自行组织实现、测试、复核和必要协作。代码检查、文件检索、依赖分析、编辑、构建、测试、Review 与 rework 都属于租约执行期；所有保障档都持续每约 60 秒 heartbeat 到 result/claim release。内部 helper 只向 receiver 返回工作结果，不得调用任何 Graph mutation 工具或获得 operation/reservation bearer。

Plugin 内置外层 receiver 最大并发 4。MCP Server 不读取用户级编排配置；机器上残留的旧 `orchestrator.json` 不参与启动、派遣、授权或指纹。

## 节点推进

Controller 在本节只负责 Graph 前驱成功门禁、状态迁移、结果契约校验和持久化。它确认的是每个必经节点存在合法终态，不判断验收内容为真；技术判断属于对应独立 receiver，最终业务确认属于用户。

- `TASK_LOOP` 是唯一实现执行节点。
- `STANDARD` 中每个 TASK Loop 成功后都必须经过自己的 `TASK_REVIEW_LOOP`；TASK Review 成功才是 TASK 终态。
- 一个 GROUP 的直接子节点终态全部成功后，调度器自动完成机器节点 `GROUP_JOIN`，在人类文档中称为“GROUP 完成点”。只有配置了真实直接子项 seam Review 时，才继续使该层 `GROUP_REVIEW_LOOP` Ready。
- 子 GROUP 的实际终态可被父 GROUP 消费：配置 seam Review 时是 Review 成功；否则是 GROUP 完成点成功。
- 根 TASK Review，或根 GROUP 的实际终态成功后进入 `DELIVERY_REVIEW_LOOP`，它表示 Delivery Acceptance/Readiness。
- Delivery Acceptance/Readiness 成功后才出现 `RECORD_USER_CONFIRMATION`。
- `LIGHT` 只有一个根 TASK，不创建任何 Review 或 GROUP 节点；TASK 成功后直接出现 `RECORD_USER_CONFIRMATION`。实际修改一旦触及关键边界或影响范围无法确认，必须以 `REPLAN_REQUIRED` 升级同一 Delivery 的下一 Revision 为 `STANDARD`。

GROUP 完成点不需要 dispatch，也不包含实现内容。`STANDARD` 不得绕过 TASK Review、已配置的 GROUP seam Review 或 Delivery Acceptance/Readiness，也不要用 TASK Loop 成功代替 TASK 成功。没有真实 seam 的 GROUP 不得为满足形式而配置 Review。

## 执行 Loop

1. primary 总调度上下文只读取 frontier、规划批次和路由 action，不直接实现或 claim Loop。AUTOMATIC 的 TASK 与 Review 一律交给按 assignment 创建的独立 child。
   - manual handoff 的总协调上下文先调用一次 `start_manual_handoff`；响应未知时用 `workspace_status(root_id)` 判定，已经是同 fingerprint 的 `executionMode=manual` 时幂等恢复，不重复 prepare/freeze。明确返回 `BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION` 时不读取 frontier、不开发；展示 `pendingInteraction`，用其精确 Revision、双 fingerprint、context fingerprint（脏树还包括状态 fingerprint）调用 `confirm_development_baseline`，再按响应双指纹重试启动。
   - `CLAIM_MANUAL_TASK` 不进入 `plan_dispatch_batch`。它来自完整 manual Graph，或来自 `handoff_ready_automatic_task` 已显式恢复的单个 READY 自动 TASK。总协调上下文创建独立宿主原生 TASK child，传入 root/node、冻结双 fingerprint、新的 `operation_id`，以及 action 中非空的建议性 `skillHints/receiverPrompt`；child 的 `dispatch_loop(MANUAL)` 不带 AUTO reservation、decision、模型、reasoning class 或 HOST_NATIVE transport。Controller 校验 receiving context、workspace/Git/project scope 与唯一 operation 后原子 claim；claim 后 child 立即独立 heartbeat，再进行任何上下文解读或仓库工作，并遵守后续所有租约、结果和恢复规则。
   - `MANUAL` 只能 claim manual Graph 的 `TASK_LOOP`；自动 Graph 的 TASK、任何模式的 TASK Review、已配置的 GROUP seam Review 与 Delivery Acceptance/Readiness 都拒绝 MANUAL。Review 必须按宿主编排规则由与全部上游实现/Review context 不同、且不是上游 Loop 派生的独立宿主原生接收上下文 AUTO claim；Plugin 不对这种独立性提供密码学证明。
2. 对当前 frontier 调用一次 `plan_dispatch_batch`。Controller 为所有可派遣的 Ready TASK/Review 原子创建短租约 reservation，绑定 node、attempt、Graph/Revision fingerprint、decision fingerprint、Adapter、receiver 类型、容量和资源锁；同一批次内也先预留冲突资源。
3. 按 `concurrentDispatchGroups` 立即创建独立 receiver。assignment 带有非空 `skillHints/receiverPrompt` 时，把提示词原样放入 child 的初始输入；它会列出具体 catalog 名与当前宿主原生触发形式。每个 child 以 `dispatch_transport=HOST_NATIVE`、assignment 的 reservation/decision fingerprint 和新的显式 `operation_id` 调用 `dispatch_loop(AUTO)`；Controller 校验 live reservation 与当前状态后原子 claim。`HOST_NATIVE` 是编排要求，不是进程或身份的密码学证明。响应未知时只以同一 reservation/operation 重试恢复，不生成第二个 operation。claim 成功响应直接携带 Loop context、`progressMonitor` 与 `heartbeatDirective`；child 先按 directive 立即 heartbeat，再解读上下文或进行任何代码检查、文件检索、依赖分析、构建、测试或 Review。宿主把 monitor 作为主 Agent 的首次运行进度面板输出。
4. 接收方原生进入 delivery-graph，使用精确 `nodeId` 调用 `loop_context`。其中 `projectScopes` 是 Controller 按当前 Delivery 的冻结分支、Git common directory 与 workspace 绑定只读解析后的实际 workspace 路径；未显式声明 `delivery.projectScopes` 的普通单仓 Delivery 从顶层 `delivery.gitBinding` 合成并验证唯一 `primary` scope，多仓 Delivery 逐项验证显式 scope。`projectScopeAnchors` 保留 preview 时的冻结仓库锚点，仅供审计，不是开发目录。`projectScopes` 为空或 binding 无效时，在任何仓库工作前停止。receiver 必须直接使用这些 scope，不得为“校准环境”创建、`checkout` 或 `switch` 分支。TASK、TASK Review 与已配置的 GROUP seam Review 同时取得控制器生成的 `humanArtifacts.workItem` baseline/progress/acceptance 路径；TASK 与 TASK Review 继续取得 `humanArtifacts.taskBaseline` 便捷路径，接口型 TASK 的 workItem 还包含自己的 `interfaces`。机器输入仍以 MCP 响应为准。
5. Plugin 不安装生命周期 Hook。Plugin 只接收宿主 Adapter 提供的请求 workspace、receiver 类型和 assignment 数据。宿主负责创建独立 child；Controller 无法密码学证明真实 parent-child、receiver 延续或 reviewer 独立性，但仍强制校验 reservation、decision fingerprint、attempt、workspace/Git/project scope、operation、lease 和资源锁。所有 mutation 都显式携带 claim 对应的 `operation_id`；该 bearer 不得进入 Worker 输入、终端、进度、result 或用户消息。
6. 按 `loop.ref` 启动对应内部 TASK、TASK Review、已配置的 GROUP seam Review 或 Delivery Acceptance/Readiness Loop，并把 `payload` 和共享 `skillHints` 原样交给该 Loop。
   - 一个实际 workspace/worktree 可以绑定多个 Delivery，控制状态仍按显式 `rootId` 隔离。Adapter 把 MCP 主控制根与实际执行 workspace 分开提供，Controller 校验当前 Delivery 的冻结 `gitBinding`；`loop_context` 只下发该 `rootId` 已验证的 `projectScopes`。receiver 不能切换分支，也不能在同一 checkout 与另一个 Delivery receiver 重叠运行；宿主必须在派遣前验证前一个 Delivery 的 commit、clean、HEAD 与安全释放边界。
   - 每个独立 Delivery 都从已确认开发基线取得自己的 feature 分支。默认主线按 `origin/HEAD → main → master` 发现，但用户也可显式选择合法的本地进行中分支；不得未经确认从当前 Delivery feature HEAD 分叉。`CURRENT_WORKSPACE_SERIAL` 依次在当前 checkout 运行这些分支，不创建新 worktree。基线在创建后继续前进不改变已冻结 `baseCommit`；最终集成前由 Delivery 自己解决与集成目标最新状态的差异。
   - 同一 Delivery 可以在 `projectScopes` 中覆盖多个本地仓库，例如主需求位于 `project-api`，同时修改 `project-provider` 与 `project-consumer`。所有 `READ_WRITE` Git 项目使用相同的 `branchRef`，但各自保留独立 `baseCommit`；Loop 只能访问当前 Revision 已授权的项目范围。所有 TASK 共享该 Delivery 在各仓库中的同名分支；TASK Agent 不创建、绑定或切换内部 Git 分支。获得相应 Git 写入授权后，TASK 可按各自 scope 单独执行 `git add` 和 `git commit`，在 Delivery 分支上形成独立 TASK commit；必须使用显式 pathspec 只暂存本 TASK 变更，且同一 workspace 的 Git index/commit 写入不可并发。同一 Delivery 内互不冲突的 TASK 可按 frontier 并行，但同一实际 workspace 的不同 Delivery 不并行；后启动或后发现者等待完整串行释放边界。
7. 内部 Loop 先识别当前任务与宿主可用 Skill，再按 `receiverPrompt/skillHintPrompt` 判断相应阶段。用户明确指定的 Skill 对当前 Loop 适用且可用时应优先原生触发；实现、生成器、测试和编码规范类 Skill 通常在 TASK 阶段使用。Codex 使用 `$skill-name`，Claude Code 使用原生 Skill tool。只有当前阶段不适用或宿主不可用时才跳过；它不阻塞 Loop、不要求用户再次确认，也不得伪造已使用。
8. 让内部 Loop 自己选择其他必要 Skill。规划层按需生成的 payload 只提供需求方向、目标、明确约束、已确认外部契约和已知验收，不是完整实现规约；Graph 通过 hierarchy/DAG 统一把控依赖、资源、frontier、全局进度、结果汇总和验收路由，并把不透明输入路由到对应 Loop，但不创作需求或选择实现。Loop 要结合真实代码、契约和数据链路推导当前 scope 的必要条件，并自主确定普通文件名、实现类、内部方法、代码结构和详细测试计划；只有需求明确指定或用户确认的外部兼容契约才固定精确标识。TASK Loop 自主管理实现、文件、测试、Gate 和修正，并从实际 changed files、依赖与契约界定最小充分验证范围；不因进入 TASK 就默认运行全仓测试。`affectedScopes.paths` 使用字面量仓库相对文件或目录，并纳入相关依赖与契约锚点。结果在 `verificationEvidence` 记录有界的 check、kind、命令摘要、scope、状态、测试计数、完成时间和测试时 `testedWorkspaceSnapshots`。
9. Review Loop 自主管理独立发现、修正协调、Gate 和复审，但独立判断不等于自动全量复测。只自动复用 `validationEvidenceIndex` 中已通过、命令可审计、scope 覆盖当前风险且 `freshness=EXACT_MATCH` 的证据。Controller 比较 `evidenceScopeSnapshots` 的声明相关路径，无关文件变化不使该证据失效。TASK Review 只验冻结 TASK 验收点、局部行为、公共契约与定向回归；已配置的 GROUP Review 只验直接子项 seam；Delivery Acceptance/Readiness 只验顶层需求覆盖、整体证据、运行准备度和全局风险。不得重复下层实现审查或已关闭 finding。证据缺失、失败、相关路径 `CHANGED/UNBOUND`、Review 修正只使受影响范围及其依赖证据失效；只有影响范围无法界定、关键跨边界风险没有隔离检查，或冻结 payload 明确要求时才全量复跑。
10. 当前目标内可修复的实现缺陷、测试失败、数据完整性或边界问题都留在当前 Loop：receiver 调整内部计划，按需创建成本合适的 Codex、Claude、Grok、DeepSeek 等 Worker，完成修正后重新验证。内部 Worker 只能向 receiver 返回结果；只有 receiver 能上报进度或终态。Review 必须保留独立复核，不要把“Review 未通过”提交成 `BLOCKED`。
11. `STANDARD` Loop 在领取、代码检查完成、确认根因、完成修改、实际执行测试时的开始与结束、发现问题、修复、复审和最终验证等有意义的阶段立即调用 `report_loop_progress`；`LIGHT` 可减少 progress，但不能省略 heartbeat。启动测试或构建前先估算耗时，优先缩小命令范围（单模块、指定测试类、离线依赖解析）而不是接受长阻塞；预计超过 60 秒即必须先用 `heartbeat_loop(expected_command_seconds=...)` 申请受 Graph 上限约束并含收尾缓冲的租约，再转后台。长时间测试或构建必须由 receiver 以非阻塞进程/宿主异步命令启动，或交给不持有控制面凭据的独立监控 Worker，使持有 operation 的外层 receiver 不被单次 shell/tool call 占满；开始前 progress + heartbeat，运行期间至少每 60 秒 heartbeat，结束后立即 heartbeat + progress。`summary_zh`、`completed_zh` 与 `next_step_zh` 使用用户当前语言，测试结果使用结构化计数；字段名为现有 schema v3 契约，不代表内容必须包含中文字符。禁止提交原始终端日志或内部推理。进度事件是可观测事实，不是 Graph 状态迁移，不更新 `lastHeartbeatAt`、不续租，也不改变 heartbeat 计划。
12. 所有 receiver 从 claim 后首次独立 heartbeat 起，继续按响应 `heartbeatDirective` 约 60 秒间隔调用 `heartbeat_loop`，直到 `record_loop_result` 成功或 claim 被显式 pause/release。每次真实心跳都更新 SQLite 运行态、审计事件和响应中的 `progressMonitor`；宿主据此刷新主 Agent 面板，显示最后心跳、剩余租期以及“仅保活/已续租”。剩余租期大于 2 分钟时返回 `leaseRenewed=false / NOT_REQUIRED`，原 `leaseExpiresAt` 保持不变且下一个约 60 秒 heartbeat 仍必须发生；达到 2 分钟阈值才续成从当前时刻起 5 分钟。heartbeat 与显式业务 progress 都不重写 `progress.md` 或其他 Markdown 投影；progress 只持久化有界里程碑事件，`progressMonitor` 按读取时刻即时计算并只在 Agent/Dashboard 展示。投影仅在冻结/派遣、暂停/恢复、结果、重试和终态等关键状态节点刷新。宿主未发出 child 完成通知只表示没有终态通知，既不是 heartbeat，也不能证明 receiver 仍存活；primary dispatcher 不得借用 operation 代发。`SUSPECT_LOST` 也只证明控制面心跳和进度都静默，不能自行归因为 Maven 阻塞、上下文错配或进程退出。AUTO 与 MANUAL receiver 对 heartbeat、progress、pause 和 result 都必须显式提交当前 claim 的 `operation_id`；错误或旧 operation 一律拒绝。检测到上下文容量压力且工作仍可继续时，不提交失败结果；在租约有效期内调用普通 `pause_loop`。
13. `pause_loop` 返回固定 handoff 数据。接收方使用同一 `rootId/nodeId` 调用 `resume_loop`，重新读取 frontier 和 `loop_context`，再以新 owner/operation dispatch；不重新 prepare/freeze。
14. 只有真实业务终态才用 `record_loop_result` 提交标准结果。
    - 接收方不构造或声称 `result.workspaceChanges` 的归属。Controller 会从本次
      Adapter workspace 的已验证 `READ_WRITE` Git scopes 只读采集相对冻结
      `baseCommit` 的工作区快照，并覆盖任何调用方自报的同名字段。
    - 快照用于让主会话和用户查看实际文件与 diff，只代表提交时刻证据，不替代
      可验证 commit、clean tree、HEAD 一致性或变更归属判断。快照会投影回主控制目录。

阻塞操作不只是 shell 命令：整文件 Write、大 patch、批量编辑和其他宿主 tool call 都必须先估时。既有大文件优先拆成可审查的语义小 patch，并在分块之间 heartbeat；只有无法拆分时才使用单次原子调用，且预计超过 60 秒时必须在调用前以 `heartbeat_loop(expected_command_seconds=...)` 取得覆盖整个调用及收尾的有界租约。

## 后台进度与失联预警

- `report_loop_progress` 只写入 `LOOP_PROGRESS_REPORTED` 可观测事件，不改变节点状态、不更新 `lastHeartbeatAt`、不延长 `leaseExpiresAt`。
- `graph_status` 与 `graph_frontier` 返回中文 `progressMonitor.markdownTable`，包含节点、attempt、外层 receiver、阶段、摘要、已完成、下一步、测试、心跳/租约和健康状态。
- claim 后 90 秒仍无首次独立 heartbeat：`SUSPECT_NOT_STARTED / 疑似未启动`。
- 已有 progress 但 90 秒仍无首次独立 heartbeat：`HEARTBEAT_MISSING / 已开始但无独立心跳`；所有保障档一致。
- heartbeat 仍在预期窗口内，但超过 5 分钟没有 progress：`ALIVE_WITHOUT_PROGRESS / 存活但无可见进展`。
- heartbeat 与 progress 均超过 `heartbeatSeconds + graceSeconds`：`SUSPECT_LOST / 疑似失联`。
- `SUSPECT_LOST.diagnosis.claimMatched=true` 表示该 attempt 最初已经合法 claim；`cause=UNDETERMINED_CONTROL_PLANE_SILENCE` 明确禁止仅凭告警猜测长命令阻塞、接收上下文不匹配或宿主进程仍存活。租约有效时继续监控；只有显式 mutation 错误可证明 operation/receiver 匹配失败。
- lease 到期后，下一次 `graph_frontier` 先调用 `advance_graph`，记录 `CLAIM_LEASE_EXPIRED / WORKER_LOST` 并在重试预算内生成新 attempt。

## 多会话协调与 Review 接力

- 协调会话不形成可轮换的 receiver trust root，也不持有已派遣 child 的 mutation 权限。它只在当前 Adapter/workspace 中读取 frontier、计划批次和创建独立 child。
- 前一 Loop 已 `SUCCEEDED` 且下一层 frontier Ready 时，任何符合当前 Adapter/workspace 条件的协调会话都可调用 `plan_dispatch_batch` 派遣下一层 TASK、TASK Review、已配置的 GROUP seam Review 或 Delivery Acceptance/Readiness；Controller 只记录新的 reservation 与 claim 事件。
- 仍有活跃 claim 时，其他会话不能用新的 owner 或 operation 接管该 attempt；只有匹配原 claim 的 `operation_id` 能 heartbeat、progress、pause 或提交 result。
- Controller 只看到 Adapter 提供的 workspace、receiver 类型和 assignment 数据，因此不密码学证明 Review child 与上游 receiver 的 parent-child 或独立关系。宿主必须创建新的独立 child，Controller 仍通过新 reservation、decision fingerprint 和 operation 隔离 attempt。
- mutation 因 operation 不匹配而拒绝时，主 Agent 不得代交结果或猜测 operation。等待 lease 回收后派遣新 attempt；新接收方可以复用工作区成果，但必须重新检查并验证。

不要合并以下恢复分支：

- 未 claim 且无 Agent 容量：保持 READY，不调用 `dispatch_loop` 或 `pause_loop`；仅在满足 `handoff_ready_automatic_task` 的无领取、无 reservation、clean、用户确认条件后显式转为人工接管，再由独立人工 receiver MANUAL claim。
- 已 claim、租约有效且上下文压力升高：以当前 `operation_id` 调用 `pause_loop`，不提交 Loop outcome。
- 租约已经过期：停止使用旧 operation，调用 `graph_frontier`/`advance_graph`，禁止 `pause_loop`。

`predecessors` 表示 Graph 直接前驱；`upstreamLoopResults` 提供所有传递上游 Loop 的不透明结果，供依赖 TASK 和各级 Review 消费。GROUP 完成点自身没有业务 result，不能用它的空 outcome 替代 TASK 或下层 Review 的结果。

claim 超过 `leaseExpiresAt` 后，旧 operation 不能 heartbeat、pause 或提交结果。先让 `graph_frontier`/`advance_graph` 回收失联 attempt，再使用新 operation 继续。

结果对象：

```json
{
  "status": "SUCCEEDED",
  "summary": "内部开发、测试和 Gate 已完成",
  "result": {
    "affectedScopes": [
      {
        "scopeId": "task-change",
        "projectId": "primary",
        "paths": ["src/example.py", "tests/test_example.py"],
        "modules": ["example"],
        "contracts": [],
        "dependencyBasis": "实现与直接单元测试",
        "exclusions": ["未改变公开接口"]
      }
    ],
    "verificationEvidence": [
      {
        "evidenceId": "task-module-tests",
        "kind": "TEST",
        "check": "受影响模块单元测试",
        "command": "构建工具的模块级 test 命令",
        "scope": "本 TASK 改动及直接依赖",
        "scopeRefs": ["task-change"],
        "status": "PASSED",
        "tests": {"total": 12, "passed": 12, "failed": 0, "skipped": 0},
        "completedAt": "2026-08-12T08:00:00Z",
        "testedWorkspaceSnapshots": [
          {
            "projectId": "primary",
            "bindingState": "BOUND",
            "headCommit": "测试完成时 loop_context 返回的 HEAD",
            "workingTreeStateFingerprint": "测试完成时 loop_context 返回的 64 位指纹"
          }
        ]
      }
    ]
  }
}
```

TASK `result` 的业务内容仍由 Loop 定义，外层调度器不解释测试覆盖。成功 Review 的 `result` 是有界契约：共同字段为 `validationDecision` 和 `reviewFindings`，并且只带本层唯一结论 `taskAcceptance`、`groupIntegration` 或 `deliveryReadiness`；可另带 `affectedScopes`、`verificationEvidence` 和 Controller 快照。不得提交 `upstreamLoopResults`、其他层结论或下层 result body。Controller 在结果记录时覆盖 `evidenceWorkspaceSnapshots` 和 `evidenceScopeSnapshots`，并在 Review context 输出紧凑 `validationEvidenceIndex`；只有 `PASSED + EXACT_MATCH` 可自动复用。带 `scopeRefs` 的 evidence 按声明相关路径判断新鲜度，无关 workspace 变化不触发复测；没有可绑定路径的旧 evidence 保守回退到整个 workspace。Graph 不持久化源码 diff；GROUP/Delivery Review 的 `upstreamLoopResults` 连 `workspaceChanges` 清单也不下发，只消费结论、证据引用、契约锚点和状态指纹。

`workspaceChanges` 是上述不透明 result 中唯一由 Controller 在 MCP
`record_loop_result` 路径自动替换的验收证据字段。它只保存项目、基线、HEAD、
状态指纹和变更文件清单，随 outcome 与事件链持久化；不保存源码 diff，也不生成
补丁附件。调用方不要自行填充、删改或把它当作文件写授权；需要内容时从授权
workspace 或对应提交读取。

## 失败和重试

- `BLOCKED + RETRYABLE_INFRA` 与租约丢失 `WORKER_LOST`：调度器在预算内创建新 attempt。
- `WORKER_LOST` 新 attempt 处于 Ready 时，当前协调会话重新调用 `plan_dispatch_batch`，以新的 reservation、decision fingerprint、独立 receiver 和 `operation_id` 领取；旧 operation 保持失效。恢复无需重新 prepare/freeze 或直接修改 `scheduler.db`。
- 普通 `BLOCKED`：必须显式提供 failure class（取值：`RETRYABLE_INFRA`、`WORKER_LOST`、`LOOP_BLOCKED`、`REPLAN_REQUIRED`、`EXTERNAL_AUTHORITY`、`NON_RETRYABLE`），且只表示当前 scope 和权限内没有继续路径；不自动重跑。当前 Loop 内可修复的 finding 或内部 Gate 失败不是 `BLOCKED`，必须在提交终态前继续修正和复验；依赖外部人工或权限的用 `EXTERNAL_AUTHORITY`，契约不再适用的用 `REPLAN_REQUIRED`。
- `REPLAN_REQUIRED`：当前冻结 Revision 的调度契约已不适用。记录结果后等待 `REPLAN_HIERARCHY`；不要直接修改原图，也不要创建新的 Delivery ID。用户明确要求调整后，用同一 `delivery.id` 准备并冻结下一 Revision；新 Revision 冻结时旧 run 自动成为 `SUPERSEDED`。
- `CANCELLED`：结束当前 Loop，不自动重试。
- 未 claim 且宿主 Agent 暂时不可用：人工交接，不提前 claim。
- 已 claim 且租约有效时的上下文容量不足：使用当前 operation pause/handoff，不是 `BLOCKED`、`WORKER_LOST` 或 `REPLAN_REQUIRED`。
- 租约过期：由 `advance_graph` 记录失联并按预算恢复；不是 pause/handoff。

MCP 写响应未知时先读状态。operation ID 永不复用。

## 资源锁

租约有效的已 claim Loop 占用其全部 `resourceClaims`。共享控制根内任何 Delivery 的另一个 Ready Loop 只要存在相同键就不能 dispatch；frontier 会用 `<rootId>/<nodeId>` 标识跨 Delivery 冲突。租约过期后不再占用跨 Delivery 资源；原 Delivery 下次推进时仍按 `WORKER_LOST` 回收旧 attempt。无 claim 交集也不能绕过 `CURRENT_WORKSPACE_SERIAL`：同一实际 workspace 中已选择 `AUTOMATIC` 或 `MANUAL` 的后启动或后发现 Delivery 都标记为 `QUEUED`，直到前一个 Delivery 已进入 Run 终态或到达最终用户确认边界，并形成可验证业务 commit、working tree/index clean、HEAD 未漂移且 receiver/reservation 安全释放才按记录模式续调；手动冻结 Delivery 内部保持 `HANDOFF_READY`。`CANCELLED` 在该安全边界释放 owner，不需要归档；终态查询忽略过期 `workspaceRebase`。冲突、owner dirty、未合并状态或漂移使队列保持等待，不创建新 worktree 规避，也不 stash owner 的未完成改动。不要从路径、仓库层级或模块前缀推导额外资源锁。

## 未开始 TASK 的需求修订

1. 初次 Delivery 冻结后，`graph_status.taskRequirements` 中每个 TASK 都是 revision 1、`FROZEN`。
2. 用户明确要求调整某个尚未开始的 TASK 时，先按 `hierarchy_contract.projectionGuidance.taskSplitIntegrityPreflight` 对受影响 TASK 和相关后继执行阻断式预检。构建或 Review 必须等待后继恢复、或破坏性符号仍被后继引用时，先调整候选切分。
3. 读取当前 requirement revision，调用 `unfreeze_task_requirement`，提供真实授权人和原因。曾经 claim（包括进入自动重试）、暂停、成功、阻断或取消的 TASK 必须拒绝解冻。
4. `unfreeze_task_requirement` 与 `refreeze_task_requirement` 都会先让已到期 reservation 失效，再检查当前 Run 是否仍有未领取的有效 reservation。存在时返回 `SCHEDULER_TASK_REQUIREMENT_RESERVATION_ACTIVE`、全部冲突 reservation 和最早 `retryAfter`；等待该时刻后重新读取一次 frontier 再重试，不复用旧 assignment，不轮询，也不绕过控制面修改需求。
5. 解冻返回完整 `requirement`。只修改 `title`、`summary` 与不透明 `payload`；不得修改依赖、`resourceClaims`、Loop ref、TASK Review、父子层级或 Graph 拓扑。
6. 以解冻时相同的 TASK `expected_revision`、完整替代 requirement 和真实确认人调用 `refreeze_task_requirement`。Controller 不改写当前 Revision，而是从 SQLite 权威定义创建并冻结下一不可变 Delivery Revision；旧 Revision 双指纹保持不变，新 Run、Graph 指纹、TASK requirement revision 和 baseline 一致更新，并沿用原 AUTOMATIC/MANUAL 模式。
7. `UNFROZEN` 期间 `graph_frontier` 只返回 `REFREEZE_TASK_REQUIREMENT`，`dispatch_loop` 必须拒绝该 TASK；成功响应后只使用返回的新 Revision/Graph 指纹重新读取 frontier 和规划 assignment，不复用旧指纹、旧 reservation，也不从 Markdown 重建调度状态。
8. 需求修改若必须改变依赖、资源声明、项目范围或拓扑，不使用局部解冻，继续走 Delivery Revision。

## Delivery Revision

Delivery 保持 `OPEN/未上线` 时，测试反馈、业务验收优化或需求扩展仍可属于同一个 Delivery：

1. 读取 `delivery_revision_history` 与当前 hierarchy，保留原 `delivery.id`。
2. 将完整新范围传给 `prepare_delivery_revision`，同时提交当前 revision、变更原因、真实请求人和连续性依据。用户明确要求继续同一 Delivery 时传 `continuity_basis=USER_EXPLICIT_SAME_DELIVERY`；只有当前 Graph 已记录 `REPLAN_REQUIRED` 才传 `ACTIVE_LOOP_REPLAN`。工作区、路径、分支或旧 Delivery 仍处于 Active 都不能充当连续性。该调用只写候选 Revision，不替换当前 hierarchy/run，也不应触发宿主通用确认弹窗；可重复 prepare 尚未冻结的同一新 Revision，但不能修改旧 Revision。
3. 检查响应中的 `carryForwardTaskIds`。只有 TASK definition、依赖、Loop、资源声明与 TASK Review 完全未变，而且旧 Revision 的实现及 Review 都成功，才会成为携带候选；GROUP 与 Delivery Acceptance/Readiness 不携带。
4. 展示完整新范围、Revision 编号、携带候选和 `requiredProjectAuthorizations`。跨项目 scope 必须包含当前工作区，所有可写 Git 项目使用同名 feature 分支。
5. 后续 Revision 没有 Controller `executionChoice`：宿主用自己的原生对话询问自动或手动（这是本 Revision 唯一一次业务确认），随后直接调用对应工具，不要再调用 `select_execution_mode`。自动执行调用 `freeze_hierarchy`，同时提交精确 `expected_delivery_revision`、新 fingerprint、与准备结果完全一致的 `authorized_project_ids` 和真实 `confirmed_by`。手动开发调用 `create_manual_handoff` 输出修订后的完整冻结内容包，但不替换当前 run；接收方真正开始开发前需再次确认如何承接该活动 Delivery。
6. 自动冻结同一 Delivery 的任意后续 Revision（`N → N+1`）时，若原物理 turn 尚未释放，项目集合、checkout、分支与冻结基线完全一致，Controller 复用最初的 clean `workspaceTurnStart`；当前 tracked、staged 与 untracked 业务改动继续属于同一次 Delivery turn，不要求删除、stash 或检查点提交，且本次 Revision 确认不扩大为 commit 授权。若旧 Revision 已在最终用户确认边界释放 turn，用户提出修改时下一 Revision 重新排到当前 owner 之后；轮到后宿主按返回的 workspace preparation 切回冻结分支并捕获新的 clean `workspaceTurnStart`。存在未解决冲突、turn 历史改写，或项目/绑定变化时仍按 Controller 返回 fail closed。
7. 只有自动冻结成功后才替换当前 Revision。活动旧 run 标记为 `SUPERSEDED`；已经完成的旧 run 保持 `COMPLETED`，仅旧 Revision scope 标为 `SUPERSEDED`。`revisions.md` 与 `delivery_revision_history` 保留审计链。

## 恢复

- 调用 `advance_graph` 处理租约和自动重试。
- frontier 返回 `nextWakeAt` 时，宿主只安排一次原生计划提示并重新消费 frontier；硬 429 路径必须先取消旧周期监控。控制器不会在没有 Agent 调用的情况下自行推进。
- 调用 `graph_events` 检查事件链。
- 物化 node 状态不可信时调用 `rebuild_graph_run`；它只从事件链重建快照，不改变 Loop 内容或事件历史。
- 恢复时继续遵守递归终态：TASK Review 或已配置的下层 GROUP seam Review 未成功时，不得手工推进父 GROUP 完成点/Review；未配置 Review 的 GROUP 以完成点为终态。
