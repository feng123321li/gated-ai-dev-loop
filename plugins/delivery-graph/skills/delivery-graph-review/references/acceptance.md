
# 分层验收、交付准备度与最终确认

Review 是一个标准 Loop，不是 delivery-graph 内置 Gate。

每个 TASK/GROUP/Delivery Review receiver 在 claim 成功后立即用当前 operation 调用 `heartbeat_loop`，早于 `loop_context` 解读、证据检查、文件检索和验证；首次 `leaseRenewed=false / NOT_REQUIRED` 保留原 `leaseExpiresAt`，并继续按 `heartbeatDirective` 约每 60 秒 heartbeat，直到提交 result 或释放 claim。Review progress 不续租，内部验证 worker 不持有控制面凭据，primary dispatcher 不得代发 heartbeat。

本文件的分层 Review 规则适用于 `STANDARD`。`LIGHT` 只用于根据真实改动内容和影响范围确认的单一低风险根 TASK，不创建 TASK Review、GROUP seam Review 或 Delivery Acceptance/Readiness；TASK 完成定向验证后直接进入用户确认。执行中发现接口、数据、权限、安全、生产配置、跨模块影响或其他不确定边界时，必须返回 `REPLAN_REQUIRED`，用同一 Delivery 的 `STANDARD` Revision 继续。

## 三段职责边界

- **Controller**：只检查 Graph 前驱是否进入合法成功终态，执行状态迁移，机械校验 Review receiver 提交的 result 结构与其声明终态是否一致，并保存事件、SQLite outcome 和人类投影。它不判断需求覆盖是否真实、证据是否充分、finding 是否实质关闭或运行准备度是否成立。
- **Review receiver**：独立执行当前层技术验收，选择复用或重跑证据，发现并闭环问题，然后提交本层结论。Delivery receiver 每个 `STANDARD` Delivery 只运行一次，消费根终态、顶层需求和报告链，负责 Delivery Acceptance/Readiness；它不逐个重新证明所有 TASK/GROUP Loop 已执行完成，也不负责 Graph 解锁。
- **用户**：在技术验收完成后作最终业务确认。用户确认不替代 Controller 门禁或 Delivery receiver 的技术判断。

因此“每一个必经 Loop 是否完成”由 Controller 沿 Graph 逐节点门禁；“整个交付是否可接受并可运行”由 Delivery receiver 判断；两者不是同一验证。`LIGHT` 是明确例外：没有独立 Review receiver，唯一 TASK 合法成功后直接进入用户确认。

## TASK Review

`STANDARD` 的每个 TASK 都必须配置并执行独立 `TASK_REVIEW_LOOP`：

1. 等待该 TASK 的 `TASK_LOOP` 成功。
2. 使用 `loop_context` 读取 TASK Review 的 ref、payload、TASK 结果和全部 `upstreamLoopResults`。
3. 通过 `dispatch_loop` claim。
4. 只对该 TASK 的冻结验收点、局部行为、公共契约和定向回归作独立判断，并按下述 Review 规则完成问题闭环。不要复查兄弟 TASK 内部实现或承担 GROUP/Delivery 验收。

TASK Review `SUCCEEDED` 后，该 TASK 才成为兄弟依赖、所属 GROUP 完成点或 Delivery Acceptance/Readiness 可消费的终态。不得用 TASK Loop 成功替代 TASK Review。

## GROUP 完成点与可选 seam Review

每个 GROUP 都会编译出机器节点 `GROUP_JOIN`，人类文档统一称为“GROUP 完成点”：

1. 等待该 GROUP 的所有直接子节点成功。直接子 TASK 的终态是 TASK Review；直接子 GROUP 的终态是其实际终态。
2. 调度器自动完成 GROUP 完成点，不派发 Agent，也不产生业务 result。
3. 若该 GROUP 没有需要独立验证的直接子项 seam，`reviewLoop` 必须为 `null`，GROUP 完成点本身就是该 GROUP 的终态；不创建 Review Graph 节点、SQLite run/event/outcome 或投影段落。
4. 只有存在接口兼容、数据/控制流、事务、错误传播或其他真实直接子项 seam 时才配置 `GROUP_REVIEW_LOOP`。完成点成功后该 Review 才 Ready。
5. GROUP Review 的输入只包含子层终态摘要、验证证据引用、契约锚点与状态/范围指纹；不包含 `workspaceChanges` 或源码 diff。默认复用 `PASSED + EXACT_MATCH` 证据，只为尚未覆盖的直接子项 seam 运行新命令；不得默认重跑 TASK 局部套件或全量 Maven/Gradle build。只有明确的 seam 缺口需要命令时，才按项目语言选择不持有控制面凭据的专用命令 worker。
5. Review 使用 `loop_context` 读取 ref、payload、直接 `predecessors` 和全部 `upstreamLoopResults`，只验证直接子项之间的组合关系，再通过 `dispatch_loop` claim。

配置了 GROUP Review 时，只有它 `SUCCEEDED` 后该 GROUP 才向父 GROUP 传播；未配置时由 GROUP 完成点直接传播。父层 Review 只处理父层直接子项 seam，不能替代或重复下层验收。

## Review 问题闭环

TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness 使用相同的问题闭环规则：

1. Review Loop 结合真实审查上下文，优先触发适用的共享 Skill Hint，再自行决定其他审查 Skill、隔离方式、检查项和内部 Gate。payload 只提供目标、明确约束和已知验收点；Review 必须结合真实代码、契约和数据链路推导必要条件。
2. 将每项问题显式分类为 P0、P1 或 P2。P0/P1 是成功前必须关闭的问题：不退出 Loop，调整内部方案，自行修正或派遣内部修正上下文，补充必要验证，再由独立 Review 重新检查。
3. P2 不阻断成功，但必须逐项列示并说明接受理由或后续处置。
4. P0/P1 全部修复、验证并独立复审后，才可用 `SUCCEEDED` 返回结果；同时在 `result.reviewFindings` 提交完整问题清单，P2 不得因非阻断而省略。

`BLOCKED` 不是 Review 失败状态，只能在当前 scope 和权限内已经没有继续路径时使用并显式分类；`REPLAN_REQUIRED` 只用于必须改变冻结依赖、资源声明、项目范围或拓扑的情况。任一级真实 `BLOCKED` 都停止向上收敛；`REPLAN_REQUIRED` 由 frontier 进入 `REPLAN_HIERARCHY`，按执行说明等待用户决定，并在同一 Delivery 下评审下一 Revision。

## 证据优先验证

“完成完整声明验收”表示覆盖全部验收风险，不表示每个 Loop 都重复运行同一套全量测试：

1. TASK 根据 changed files、依赖方向、公开契约和失败影响界定最小充分范围，在 `result.affectedScopes` 记录 project/path/module/contract、依赖依据与排除项；`paths` 必须是字面量仓库相对文件或目录，并覆盖相关依赖与契约锚点。优先执行对应测试类、受影响模块、构建或契约检查。测试完成后重新读取 `loop_context.currentWorkspaceSnapshots`，把其中全部 `BOUND` 状态写入该项 `testedWorkspaceSnapshots`；随后不要再修改相关代码再提交旧证据。每项结果写入 `result.verificationEvidence`，至少包含稳定 `evidenceId`、类型、检查名、命令摘要、scope、状态、完成时间，并用 `scopeRefs` 关联覆盖范围。
2. Controller 在记录终态时用可信 Git 状态附加 `result.evidenceWorkspaceSnapshots` 与逐声明相关路径的 `result.evidenceScopeSnapshots`；Review 的 `validationEvidenceIndex` 将每项上游 evidence 标为 `EXACT_MATCH | CHANGED | UNBOUND`。带 `scopeRefs` 的证据只在相关路径内容变化时变为 `CHANGED`，后续 TASK 的无关文件修改不使它失效；没有安全有界路径的旧证据保守回退到整个 workspace。只有证据为通过、命令与结果可审计、scope 覆盖当前风险且状态为 `EXACT_MATCH` 时才可自动复用；非 Git、捕获不稳定或缺少绑定的旧结果为 `UNBOUND`，不得仅凭“测试通过”复用。`CHANGED` 先做影响分析，能界定时只补受影响范围。
3. TASK Review 只补本 TASK 验收缺口或高风险行为；已配置的 GROUP Review 只验证直接子项 seam/integration；Delivery Acceptance/Readiness 只确认顶层需求覆盖矩阵、整体集成或 E2E 证据、运行准备度和全局风险。已有充分且 `EXACT_MATCH` 的结果不再执行。各级 receiver 必须独立作出本层决定，但不机械重复下层代码审查或同一全量命令。
4. 证据缺失/失败、相关 workspace 局部变化或 Review 修正，只使受影响范围及其依赖证据失效并触发定向复跑。只有影响范围无法可靠界定、关键跨边界风险没有隔离检查，或冻结 Review payload 明确要求全量时，才运行全量套件。
5. Review 在 `result.validationDecision` 记录 `REUSED | TARGETED_RERUN | FULL_RERUN`、复用与新执行的 evidence refs、risk triggers 和理由。上游 ref 使用 `{nodeId, attempt, evidenceId}`，避免不同 Loop 的同名 evidence 混淆。Review 修改代码、测试或配置后必须重新比较最终 workspace 状态；不得沿用修改前已失效的判断。

Maven 等构建工具中的“模块全量单测”仍只是一个 evidence scope；除非冻结验收明确包含集成 profile，否则不能把默认 `test` 生命周期误报为已经覆盖独立的集成、兼容或 E2E 检查。

## Delivery Acceptance/Readiness

根终态成功后，frontier 使 `DELIVERY_REVIEW_LOOP` Ready：

1. 使用 `loop_context` 读取 `delivery.reviewLoop` 的 ref、payload、根前驱和全部 `upstreamLoopResults`。
2. 通过 `dispatch_loop` claim。
3. 独立检查顶层需求/验收点是否逐项覆盖，跨 GROUP 或系统级集成/E2E 证据是否充分且新鲜，部署、迁移、配置、监控、回滚等运行准备是否就绪，以及是否仍有全局阻断风险。
4. 不重新审查下层代码细节、子 TASK 单测或已经关闭的下层 finding；发现真正缺口时只定位责任节点和缺失证据，并在当前授权范围内完成闭环。
5. 对本层问题分类为 P0/P1/P2。P0/P1 在当前 Loop 内完成修正、验证和独立复审；P2 可不阻断成功，但必须逐项列入验收结果。
6. 只有需求覆盖完整、整体证据充分、运行准备就绪或明确不适用、全局阻断风险为空且 P0/P1 全部关闭时，才返回 `SUCCEEDED`。

Delivery Acceptance/Readiness 对普通实现缺陷、测试失败或未显式列入需求的工程正确性问题不得返回 `BLOCKED`。真实 `BLOCKED` 时外层只记录阻断，不解释 findings；返回 `REPLAN_REQUIRED` 时由 frontier 进入 `REPLAN_HIERARCHY`；只有 `SUCCEEDED` 才解锁最终用户确认。

TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness 都自主管理内部 Gate、修正闭环、复审与 Skill 生命周期。独立性要求 receiver 独立发现和重新验证，并只作出本层决策；这里的重新验证可以审查并复用充分、可审计且状态匹配的上游证据，不要求无条件重跑同一全量测试。它也不禁止 receiver 推动或执行当前授权范围内的修正。`result.reviewFindings`、`result.verificationEvidence` 与 `result.validationDecision` 是有界验收投影约定，不是新的调度状态；调度器不解释问题、不把问题转换成外层节点，也不创建 Graph 环。

Review 成功时必须提交共同字段和且仅一个本层结论字段；没有问题也提交空数组：

```json
{
  "validationDecision": {
    "decision": "REUSED | TARGETED_RERUN | FULL_RERUN",
    "reusedEvidenceRefs": [
      {
        "nodeId": "loop:t-example",
        "attempt": 1,
        "evidenceId": "task-targeted-tests"
      }
    ],
    "executedEvidenceRefs": [],
    "riskTriggers": [],
    "rationale": "证据状态匹配且覆盖当前风险。"
  },
  "reviewFindings": [
    {
      "severity": "P0 | P1 | P2",
      "summary": "问题摘要",
      "status": "RESOLVED | ACCEPTED | OPEN",
      "resolution": "修正或接受说明",
      "evidence": "验证或复审证据"
    }
  ],
  "taskAcceptance | groupIntegration | deliveryReadiness": {
    "...": "见下方本层契约"
  }
}
```

- TASK Review 使用 `taskAcceptance`：逐项 `acceptanceChecks` 必须为 `SATISFIED` 并引用证据，`localBehavior=VERIFIED`、`publicContract=VERIFIED | NOT_APPLICABLE`、`targetedRegression=VERIFIED`、`decision=ACCEPTED`。
- GROUP Review 使用 `groupIntegration`：至少一个 `seams` 条目，每项列出至少两个直接参与方、`status=VERIFIED` 和证据引用，`decision=INTEGRATED`。没有真实 seam 时不要伪造条目，应把 GROUP `reviewLoop` 设为 `null`。
- Delivery Acceptance/Readiness 使用 `deliveryReadiness`：逐项 `requirementCoverage` 必须为 `COVERED` 并列出责任节点和证据引用，`integrationEvidence=SUFFICIENT`、`operationalReadiness=READY | NOT_APPLICABLE`、`openBlockingRisks=[]`、`decision=READY_FOR_USER_CONFIRMATION`。

成功结果顶层只允许上述本层字段、`validationDecision`、`reviewFindings`、`affectedScopes`、`verificationEvidence` 和 Controller 生成的 workspace/evidence snapshots。`upstreamLoopResults` 是运行时只读 context，不得复制到 outcome；也不得复制其他层的结论字段或下层 result body。`SUCCEEDED` 不得携带状态为 `OPEN` 的 P0/P1；P2 必须始终出现在验收报告的问题表中。问题的内部修正过程仍留在 Review Loop，不新增 Graph 节点或状态。

## 分层验收投影

每份验收报告只完整展开当前层，跨层只串联，不复制全部信息：

- TASK `acceptance.md` 展示本 TASK 的已知验收输入、TASK 结果，以及 TASK Review 的 `taskAcceptance`、本层证据引用和 findings。
- GROUP `acceptance.md` 展示直接子节点的状态、结果摘要和报告链接、GROUP 完成点；只有配置了 seam Review 时才展示其输入、`groupIntegration`、本层证据引用和 findings。未配置时只标明完成点即本层终态，不生成空 Review 表格。
- Delivery `acceptance.md` 展示根工作项的状态、结果摘要和报告链接，以及本层 `deliveryReadiness`、本层证据引用、findings 与最终用户确认。整棵 TASK/GROUP 验收内容不得再次内联到 Delivery 报告。

因此，同一 finding 和结论 body 只在产生它的 Review 层完整出现；上层通过对应报告链接追溯，并只消费终态、简要结果和证据引用。投影文件不展开原始 `upstreamLoopResults`。

### 工作区变更证据

通过 MCP 提交 `record_loop_result` 时，Controller 对每个已验证的
`READ_WRITE` Git project scope 自动采集相对冻结 `baseCommit` 的当前工作区
证据索引，覆盖调用方自报的同名字段，并把结构化 `result.workspaceChanges` 写入
Loop outcome 与事件链。索引只包含 committed、staged、unstaged 和 untracked 的
变更文件清单、base/HEAD、工作区与快照指纹；不包含源码 diff，
`.layered-delivery/**` 不计入业务变更。Controller 只读 Git，不执行 stage、commit
或其他 Git 写操作。Review 需要代码内容时直接从已授权 workspace 按需读取。

当前层 `acceptance.md` 在对应 Loop 结果下显示“工作区变更证据”。这是结果提交
时的物理 workspace 快照，不是 TASK、Loop 或 Delivery 的独占归属证明。默认
`CURRENT_WORKSPACE_SERIAL` 要求每个 Delivery 使用独立分支，并只在 working tree、
index clean、已有可验证业务 commit、HEAD 与冻结 binding 一致且在途 receiver/reservation
安全释放后推进自动队首。前一个 Run 已终态，或已到 `RECORD_USER_CONFIRMATION`，均可在
满足该 Git 安全边界后释放物理 turn；后一种情况只释放 workspace，不把 Delivery 标记为
`COMPLETED`。已有 owner 时，只有已选择 `AUTOMATIC` 的后续 Delivery 标记为 `QUEUED`；
手动交接冻结仍持久化为 `HANDOFF_READY`，不进入自动队列。发现资源冲突、owner dirty、
未合并或 HEAD 漂移时保持等待，不能继续共享 checkout。队首的非 owner 既存业务改动
只能按已授权的精确 stash 准备处理，不能 stash 正在运行 owner 的未完成改动。现有 linked
checkout 也只按普通 current workspace 处理，不自动创建新 worktree。验收仍需结合需求、
Review、提交边界和结果摘要判断。

TASK 与 TASK Review 的 `acceptance.md` 只展示上述变更索引，不生成
`workspace-changes.patch`，也不内联源码 diff。Graph 用状态和范围指纹证明证据绑定，
用户或 Review receiver 需要具体内容时从已授权 workspace 或对应提交读取。

## 用户最终确认

frontier 返回 `RECORD_USER_CONFIRMATION` 后：

1. `STANDARD` 向用户展示根工作项摘要和报告链接、实际存在的分层验收报告链、Delivery Acceptance/Readiness 摘要以及重要阻断/风险；`LIGHT` 展示保障判断依据、实际 diff 范围、定向测试和唯一 TASK 结果。不要重复展开无关内容。
2. 等待用户明确接受。到达此边界后，若业务改动已有可验证 commit、working tree/index clean、HEAD 未漂移且 receiver/reservation 全部释放，Controller 可以先释放物理 workspace turn；人工或自动宿主都可在已有 Git 授权范围内切换并开发下一 Delivery。此释放不等于用户验收，旧 Delivery 继续显示为待确认。
3. 用户此时提出需求修改，说明当前 Delivery 尚未结束；不要确认完成，也不要直接修改已冻结 Revision。保持同一 `delivery.id` 进入 `prepare_delivery_revision`。若旧 turn 已释放，下一 Revision 重新进入串行队列，轮到后切回冻结分支并捕获新的 clean turn start；不能抢占当前 Delivery。
4. 用户明确接受本身就是写入最终验收的授权；用 `root_id`、`confirmed=true`、控制器接受的可移植 ASCII `confirmed_by` 和简短 `summary` 调用 `record_user_confirmation`，不要再请求通用 Yes/No，也不要触发宿主权限弹窗。该确认只写控制面，可在 workspace 已切到另一 Delivery 分支后按旧 `root_id` 补录，不得要求恢复旧 checkout。
5. Graph 进入 `COMPLETED` 后只返回简短终态摘要；不要自行写入宿主记忆、触发持续学习、维护旧 schema 笔记或更新任何项目文件。
6. 归档不是完成的自动副作用。只有用户再次明确要求归档时才调用 `archive_delivery`；它只接受当前 `COMPLETED` Delivery，从默认 `workspace_status` 与工作区总览隐藏该 Delivery，但保留 SQLite、Revision/Run 历史、事件链、详情投影及 `requirementKey` 身份映射。显式传 `root_id` 仍返回 `ARCHIVED`。`CANCELLED` 的 workspace turn 在安全边界独立释放，不靠归档清理 owner。

不要用冻结确认、测试通过、内部 Gate PASS 或 Review Loop 自述替代用户确认。完成 Graph 不自动授权提交、推送、合并、迁移或发布。
