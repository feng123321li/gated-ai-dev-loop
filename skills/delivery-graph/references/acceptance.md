# 递归 Review 与最终确认

Review 是一个标准 Loop，不是 delivery-graph 内置 Gate。

本文件的分层 Review 规则适用于 `STANDARD`。`LIGHT` 只用于根据真实改动内容和影响范围确认的单一低风险根 TASK，不创建 TASK/GROUP/Delivery Review；TASK 完成定向验证后直接进入用户确认。执行中发现接口、数据、权限、安全、生产配置、跨模块影响或其他不确定边界时，必须返回 `REPLAN_REQUIRED`，用同一 Delivery 的 `STANDARD` Revision 继续。

## TASK Review

`STANDARD` 的每个 TASK 都必须配置并执行独立 `TASK_REVIEW_LOOP`：

1. 等待该 TASK 的 `TASK_LOOP` 成功。
2. 使用 `loop_context` 读取 TASK Review 的 ref、payload、TASK 结果和全部 `upstreamLoopResults`。
3. 通过 `dispatch_loop` claim。
4. 独立检查该 TASK 的实现、契约、测试、数据链路和边界风险，并按下述 Review 规则完成问题闭环。

TASK Review `SUCCEEDED` 后，该 TASK 才成为兄弟依赖、所属 GROUP 完成点或 Delivery Review 可消费的终态。不得用 TASK Loop 成功替代 TASK Review。

## GROUP 完成点与递归 Review

每个 GROUP 都会编译出机器节点 `GROUP_JOIN`，人类文档统一称为“GROUP 完成点”：

1. 等待该 GROUP 的所有直接子节点成功。直接子 TASK 的终态是 TASK Review；直接子 GROUP 的终态是其实际终态。
2. 调度器自动完成 GROUP 完成点，不派发 Agent，也不产生业务 result。
3. 完成点成功后使该层必需的 `GROUP_REVIEW_LOOP` Ready。
4. 使用 `loop_context` 读取 Review Loop 的 ref、payload、直接 `predecessors` 和全部 `upstreamLoopResults`；实际 TASK 和下层 Review 结果位于后者。
5. 通过 `dispatch_loop` claim。

只有本层 GROUP Review `SUCCEEDED` 后，该 GROUP 才成功并向父 GROUP 传播。多层 GROUP 依次完成各自层级的整体审查，不能用父层 Review 替代子层 Review。

## Review 问题闭环

TASK Review、GROUP Review 和 Delivery Review 使用相同规则：

1. Review Loop 结合真实审查上下文，优先触发适用的共享 Skill Hint，再自行决定其他审查 Skill、隔离方式、检查项和内部 Gate。payload 只提供目标、明确约束和已知验收点；Review 必须结合真实代码、契约和数据链路推导必要条件。
2. 将每项问题显式分类为 P0、P1 或 P2。P0/P1 是成功前必须关闭的问题：不退出 Loop，调整内部方案，自行修正或派遣内部修正上下文，补充必要验证，再由独立 Review 重新检查。
3. P2 不阻断成功，但必须逐项列示并说明接受理由或后续处置。
4. P0/P1 全部修复、验证并独立复审后，才可用 `SUCCEEDED` 返回结果；同时在 `result.reviewFindings` 提交完整问题清单，P2 不得因非阻断而省略。

`BLOCKED` 不是 Review 失败状态，只能在当前 scope 和权限内已经没有继续路径时使用并显式分类；`REPLAN_REQUIRED` 只用于必须改变冻结依赖、资源声明、项目范围或拓扑的情况。任一级真实 `BLOCKED` 都停止向上收敛；`REPLAN_REQUIRED` 由 frontier 进入 `REPLAN_HIERARCHY`，按执行说明等待用户决定，并在同一 Delivery 下评审下一 Revision。

## 证据优先验证

“完成完整声明验收”表示覆盖全部验收风险，不表示每个 Loop 都重复运行同一套全量测试：

1. TASK 根据 changed files、依赖方向、公开契约和失败影响界定最小充分范围，在 `result.affectedScopes` 记录 project/path/module/contract、依赖依据与排除项；`paths` 必须是字面量仓库相对文件或目录，并覆盖相关依赖与契约锚点。优先执行对应测试类、受影响模块、构建或契约检查。测试完成后重新读取 `loop_context.currentWorkspaceSnapshots`，把其中全部 `BOUND` 状态写入该项 `testedWorkspaceSnapshots`；随后不要再修改相关代码再提交旧证据。每项结果写入 `result.verificationEvidence`，至少包含稳定 `evidenceId`、类型、检查名、命令摘要、scope、状态、完成时间，并用 `scopeRefs` 关联覆盖范围。
2. Controller 在记录终态时用可信 Git 状态附加 `result.evidenceWorkspaceSnapshots` 与逐声明相关路径的 `result.evidenceScopeSnapshots`；Review 的 `validationEvidenceIndex` 将每项上游 evidence 标为 `EXACT_MATCH | CHANGED | UNBOUND`。带 `scopeRefs` 的证据只在相关路径内容变化时变为 `CHANGED`，后续 TASK 的无关文件修改不使它失效；没有安全有界路径的旧证据保守回退到整个 workspace。只有证据为通过、命令与结果可审计、scope 覆盖当前风险且状态为 `EXACT_MATCH` 时才可自动复用；非 Git、捕获不稳定或缺少绑定的旧结果为 `UNBOUND`，不得仅凭“测试通过”复用。`CHANGED` 先做影响分析，能界定时只补受影响范围。
3. TASK Review 默认只补 TASK 未覆盖或高风险行为；GROUP Review 默认验证直接子项之间的 seam/integration；Delivery Review 默认确保覆盖矩阵和最新的最终 smoke/E2E 证据存在，已有充分且 `EXACT_MATCH` 的结果不再执行。各级 Review receiver 必须独立作出决定，但不机械重复同一全量命令。
4. 证据缺失/失败、相关 workspace 局部变化或 Review 修正，只使受影响范围及其依赖证据失效并触发定向复跑。只有影响范围无法可靠界定、关键跨边界风险没有隔离检查，或冻结 Review payload 明确要求全量时，才运行全量套件。
5. Review 在 `result.validationDecision` 记录 `REUSED | TARGETED_RERUN | FULL_RERUN`、复用与新执行的 evidence refs、risk triggers 和理由。上游 ref 使用 `{nodeId, attempt, evidenceId}`，避免不同 Loop 的同名 evidence 混淆。Review 修改代码、测试或配置后必须重新比较最终 workspace 状态；不得沿用修改前已失效的判断。

Maven 等构建工具中的“模块全量单测”仍只是一个 evidence scope；除非冻结验收明确包含集成 profile，否则不能把默认 `test` 生命周期误报为已经覆盖独立的集成、兼容或 E2E 检查。

## Delivery Review

根终态成功后，frontier 使 `DELIVERY_REVIEW_LOOP` Ready：

1. 使用 `loop_context` 读取 `delivery.reviewLoop` 的 ref、payload、根前驱和全部 `upstreamLoopResults`。
2. 通过 `dispatch_loop` claim。
3. 独立评估完整 Delivery，并在运行时选择适用的共享 Skill Hint 和其他审查 Skill。
4. 对问题分类为 P0/P1/P2。P0/P1 在当前 Loop 内完成修正、验证和独立复审；冻结 Graph 不要求沿用已经证明不完整的实现方案。P2 可不阻断成功，但必须逐项列入验收结果。
5. 只有 P0/P1 全部关闭或遇到真实外部阻断时，才用标准 Loop outcome 返回结果，并在 `result.reviewFindings` 提交完整清单。

Delivery Review 对普通实现缺陷、测试失败或未显式列入需求的工程正确性问题不得返回 `BLOCKED`。真实 `BLOCKED` 时外层只记录阻断，不解释 findings；返回 `REPLAN_REQUIRED` 时由 frontier 进入 `REPLAN_HIERARCHY`；只有 `SUCCEEDED` 才解锁最终用户确认。

TASK Review、GROUP Review 和 Delivery Review 都自主管理内部 Gate、修正闭环、复审与 Skill 生命周期。独立性要求 Review 独立发现和重新验证，并作出自己的验证决策；这里的重新验证可以审查并复用充分、可审计且状态匹配的上游证据，不要求无条件重跑同一全量测试。它也不禁止 Review 推动或执行当前授权范围内的修正。`result.reviewFindings`、`result.verificationEvidence` 与 `result.validationDecision` 是有界验收投影约定，不是新的调度状态；调度器不解释问题、不把问题转换成外层节点，也不创建 Graph 环。

Review 成功时使用以下结果约定；没有问题也提交空数组：

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
  ]
}
```

`SUCCEEDED` 不得携带状态为 `OPEN` 的 P0/P1；P2 必须始终出现在验收报告的问题表中。问题的内部修正过程仍留在 Review Loop，不新增 Graph 节点或状态。

## 分层验收投影

每份验收报告只完整展开当前层，跨层只串联，不复制全部信息：

- TASK `acceptance.md` 展示本 TASK 的已知验收输入、TASK 结果、TASK Review 输入、结果、证据和 findings。
- GROUP `acceptance.md` 展示本层直接子节点的状态、结果摘要和报告链接，以及本层 GROUP 完成点、GROUP Review 输入、结果、证据和 findings。子 TASK 或子 GROUP 的详细输入、证据和 findings 留在各自报告。
- Delivery `acceptance.md` 展示根工作项的状态、结果摘要和报告链接，以及本层 Delivery Review 输入、结果、证据、findings 与最终用户确认。整棵 TASK/GROUP 验收内容不得再次内联到 Delivery 报告。

因此，同一 finding 只在产生它的 Review 层完整出现；上层通过对应报告链接追溯，并只消费终态与简要结果。

### 工作区变更证据

通过 MCP 提交 `record_loop_result` 时，Controller 对每个已验证的
`READ_WRITE` Git project scope 自动采集相对冻结 `baseCommit` 的当前工作区
快照，覆盖调用方自报的同名字段，并把结构化 `result.workspaceChanges` 写入
Loop outcome 与事件链。快照包含 committed、staged、unstaged 和 untracked 的
变更文件清单与可展示 diff；`.layered-delivery/**` 不计入业务变更。Controller
只读 Git，不执行 stage、commit 或其他 Git 写操作。过大的 diff 会按 Controller
上限截断并在验收投影中明确标记，完整文件清单仍保留。

当前层 `acceptance.md` 在对应 Loop 结果下显示“工作区变更证据”。这是结果提交
时的物理 workspace 快照，不是 TASK、Loop 或 Delivery 的独占归属证明。默认
`CURRENT_WORKSPACE_SERIAL` 要求每个 Delivery 使用独立分支，并只在 working tree、
index clean、已有可验证 commit、HEAD 与冻结 binding 一致且在途 receiver 安全释放后
切换。发现其他 Delivery 改动、资源冲突、dirty 或 HEAD 漂移时，后启动/后发现者必须
等待并停止切换，不能继续共享 checkout。现有 linked checkout 也只按普通 current
workspace 处理，不自动创建新 worktree。验收仍需结合需求、Review、提交边界和结果摘要判断。

只要 TASK 或其 TASK Review 已保存该快照，Controller 还会在主控制根的
`.layered-delivery/<rootId>/work-items/<taskId>/workspace-changes.patch` 生成稳定附件，
并从同目录 `acceptance.md` 提供相对链接。附件按 Loop 阶段与 project scope 合并，
保留实际执行 workspace 路径、冻结 base、HEAD、快照指纹和非独占归属声明；正文
仍保留 inline diff。附件与验收 Markdown 都只由 SQLite outcome 重建，因此用户
能在主控制根通过 `acceptance.md` 与 `workspace-changes.patch` 直接审核提交时内容。

## 用户最终确认

frontier 返回 `RECORD_USER_CONFIRMATION` 后：

1. `STANDARD` 向用户展示根工作项摘要和报告链接、递归 Review 报告链、Delivery Review 摘要以及重要阻断/风险；`LIGHT` 展示保障判断依据、实际 diff 范围、定向测试和唯一 TASK 结果。不要重复展开无关内容。
2. 等待用户明确接受。用户此时提出需求修改，说明当前 Delivery 尚未结束；不要确认完成，也不要直接修改已冻结 Revision。保持同一 `delivery.id` 进入 `prepare_delivery_revision`。
3. 用户明确接受本身就是写入最终验收的授权；用 `root_id`、`confirmed=true`、控制器接受的可移植 ASCII `confirmed_by` 和简短 `summary` 调用 `record_user_confirmation`，不要再请求通用 Yes/No，也不要触发宿主权限弹窗。
4. Graph 进入 `COMPLETED` 后只返回简短终态摘要；不要自行写入宿主记忆、触发持续学习、维护旧 schema 笔记或更新任何项目文件。
5. 归档不是完成的自动副作用。只有用户再次明确要求归档时才调用 `archive_delivery`；它只接受当前 `COMPLETED` Delivery，从默认 `workspace_status` 与工作区总览隐藏该 Delivery，但保留 SQLite、Revision/Run 历史、事件链、详情投影及 `requirementKey` 身份映射。显式传 `root_id` 仍返回 `ARCHIVED`。

不要用冻结确认、测试通过、内部 Gate PASS 或 Review Loop 自述替代用户确认。完成 Graph 不自动授权提交、推送、合并、迁移或发布。
