# 工作区任务注册表、焦点与总纲

## 目录

- [目标与兼容边界](#目标与兼容边界)
- [task-registry.json 契约](#task-registryjson-契约)
- [确定性恢复](#确定性恢复)
- [workspace-overview.md](#workspace-overviewmd)

事务、暂存、事件、投影与并发规则见 [registry-transactions.md](registry-transactions.md)；生命周期、周期、终态、关系与迁移规则见 [registry-lifecycle.md](registry-lifecycle.md)。

## 目标与兼容边界

当 `.ai-dev-loop/` 中积累很多任务时，不能再靠目录名、修改时间或自然语言相似度猜测当前任务。协调工作区必须维护一个完整的机器注册表和一个给人看的工作区总纲，用显式状态选择任务。

本规则只扩展宿主 Skill，不宣称辅助 CLI 已原生实现任务注册表。现有 CLI 会严格校验 `.ai-dev-loop/<task-id>/` 的文件集合，因此不得为了记录生命周期而向已冻结任务根部新增 `lifecycle.json` 或修改 `state.json`。生命周期、当前焦点、周期和完成计数统一保存在协调工作区根级 `task-registry.json`；CLI 继续管理任务目录中的冻结信封、机械门禁和验收文件。

`None` 仍然只回答、不写文件。只有创建或接管写入型任务时才初始化注册表。

## task-registry.json 契约

注册表至少使用以下结构。`tasks` 必须包含协调目录下全部已登记任务，不能只保留活动任务。

```json
{
  "schemaVersion": 1,
  "coordinationRoot": "<normalized-absolute-root>",
  "runtimeRoot": ".ai-dev-loop",
  "revision": 12,
  "updatedAt": "2026-07-15T18:00:00+08:00",
  "updatedBy": "codex",
  "currentFocus": {
    "status": "FOCUSED",
    "taskId": "account-export-full",
    "candidateTaskIds": [],
    "candidateIssueIds": [],
    "purpose": "WORKING_TASK",
    "selectedAt": "2026-07-14T09:00:00+08:00",
    "selectedBy": "host",
    "selectionReason": "UNIQUE_ELIGIBLE_TASK",
    "selectionEvidence": {
      "path": "account-export-full/rounds/round-01/lifecycle-events/event-000001.json",
      "sha256": "<sha256>"
    }
  },
  "integrityIssues": [],
  "workspaceEvents": [
    {
      "eventId": "workspace-event-000012",
      "transactionId": "txn-20260715-000012",
      "basedOnRegistryRevision": 11,
      "committedRegistryRevision": 12,
      "type": "PROJECTIONS_ACKNOWLEDGED",
      "occurredAt": "2026-07-15T18:00:00+08:00",
      "actor": { "kind": "host", "id": "codex" },
      "changes": {
        "taskId": "account-export-full",
        "renderedRecordRevision": { "from": 6, "to": 7 }
      },
      "evidence": [],
      "affectedTaskEvents": [],
      "previousEventSha256": null,
      "eventSha256": "<sha256>"
    }
  ],
  "tasks": [
    {
      "taskId": "account-export-full",
      "title": "增加用户导出能力",
      "relativePath": "account-export-full",
      "gateLevel": "Full",
      "workScale": "Task",
      "scaleRepresentative": "交付一个可独立验收的结果",
      "currentTaskDescription": "以一个可单独验收的导出结果作为交付边界",
      "changeType": "Feature",
      "lifecycleStatus": "WAITING_USER",
      "phase": "WAITING_FOR_MANUAL_ACCEPTANCE",
      "terminalDisposition": null,
      "terminalHistory": [],
      "currentRound": "round-02",
      "nextAction": "展示最终验收报告并等待用户确认",
      "nextResponsibleParty": "user",
      "activeOperations": [],
      "creationContext": null,
      "integrity": "HEALTHY",
      "blockers": [],
      "relations": [],
      "cycle": {
        "createdAt": "2026-07-14T09:00:00+08:00",
        "developmentStartedAt": "2026-07-14T10:30:00+08:00",
        "lastActivityAt": "2026-07-15T17:35:00+08:00",
        "completedAt": null,
        "packageFrozenAt": "2026-07-14T10:00:00+08:00",
        "plannedStart": null,
        "plannedEnd": null,
        "developmentRounds": 2,
        "repairRounds": 1,
        "acceptanceRounds": 2
      },
      "completion": {
        "milestones": { "verified": 0, "total": 0 },
        "workstreams": { "verified": 0, "total": 0 },
        "tasks": { "implemented": 4, "verified": 4, "total": 4 },
        "sop": { "completed": 10, "applicable": 11, "skipped": 0, "total": 11 },
        "mechanicalGate": "PASS",
        "semanticAcceptance": "PASS",
        "semanticReviewRoute": "INDEPENDENT",
        "needHumanReviewReason": null,
        "humanSemanticReviewOutcome": "NOT_RUN",
        "manualConfirmation": "WAITING"
      },
      "evidence": {
        "authorization": {
          "path": "account-export-full/baseline.md",
          "sha256": "<sha256>"
        },
        "latestRound": "account-export-full/rounds/round-02",
        "lastTransition": {
          "eventId": "event-000007",
          "path": "account-export-full/rounds/round-02/lifecycle-events/event-000007.json",
          "sha256": "<sha256>",
          "recordRevision": 7
        }
      },
      "projections": {
        "developmentOverview": "account-export-full/development-overview.md",
        "progress": "account-export-full/progress.md",
        "projectPlan": null,
        "finalAcceptanceReport": "account-export-full/final-acceptance-report.md",
        "expectedRecordRevision": 7,
        "renderedRecordRevision": 7,
        "status": "CURRENT",
        "sha256": {
          "developmentOverview": "<sha256>",
          "progress": "<sha256>",
          "projectPlan": null,
          "finalAcceptanceReport": "<sha256>"
        }
      },
      "recordRevision": 7,
      "updatedAt": "2026-07-15T17:35:00+08:00",
      "updatedBy": "codex"
    }
  ]
}
```

字段规则：

- `coordinationRoot` 必须是项目或协调工作区的规范化绝对根路径；`runtimeRoot` 固定为 `.ai-dev-loop`。所有 registry 相对路径都以 `.ai-dev-loop/` 为基准，不以项目根为基准；与当前工作区不一致时禁止使用。
- `revision` 每次成功写入加一；`recordRevision` 只在任务的领域事实、生命周期、计数、关系、证据或 active operation 变化时加一。`projections` 是非领域的投影传输元数据：纯 `PROJECTIONS_ACKNOWLEDGED` workspace 事务可以更新其 rendered/status/sha256 而不增加 task `recordRevision`，避免 expected revision 无限自增；这种例外不得夹带任何任务事实变化。
- `currentFocus.status` 使用 `NONE`、`FOCUSED`、`WAITING_FOR_TASK_SELECTION` 或 `NEED_RESUME_CLASSIFICATION`。只有 `FOCUSED` 时 `taskId` 非空；等待任务选择时 `candidateTaskIds` 保存完整候选，等待分类时由 `candidateTaskIds` 与 `candidateIssueIds` 分别保存任务和顶层一致性问题。
- `currentFocus.purpose` 使用 `WORKING_TASK`、`BLOCKER_CONTEXT`、`DEFERRED_CONTEXT` 或 `FEEDBACK_CONTEXT`。合法组合固定为：`ACTIVE/WAITING_USER + WORKING_TASK` 可自动续接，`BLOCKED + BLOCKER_CONTEXT` 只展示解除条件，`DEFERRED + DEFERRED_CONTEXT` 必须先取得恢复授权，`TERMINAL + FEEDBACK_CONTEXT` 只进入反馈分类；`UNKNOWN` 不能成为可执行焦点。用户明确切换或清除焦点后才改为其他任务或 `NONE`。
- `currentFocus` 是注册表内的显式工作区焦点，不是从“最近修改”推断的结果。
- `selectedBy` 使用 `user/host/migration`。`selectionReason` 只能是 `EXPLICIT_TASK_ID`、`EXPLICIT_TASK_PATH`、`EXPLICIT_USER_SELECTION`、`UNIQUE_ELIGIBLE_TASK` 或 `TERMINAL_ACCEPTANCE_CONTEXT`；`selectionEvidence` 保存产生或保留该焦点的不可变 lifecycle event 路径与 sha256，旧任务迁移无法取得时才允许为 `null`。
- `relativePath` 必须是 `.ai-dev-loop/` 下单层普通任务目录名，并与 `taskId` 一致；不允许越过协调根，也不接受符号链接、junction 或其他重解析点。
- task ID 必须精确匹配 `^[a-z0-9][a-z0-9._-]*$`、不得以 `.` 结尾，并且不匹配 Windows 保留设备名 `^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)`；另保留 `task-registry.json`、`workspace-overview.md`、`.host-staging`、`.task-registry.lock` 和 `.task-registry.lock.recovery-*` 作为 Skill 控制名称。宿主规则必须至少与当前 CLI 校验同样严格，不能生成 CLI 会拒绝的 ID。
- `integrity` 使用 `PROVISIONAL`、`HEALTHY`、`TOMBSTONE`、`MISSING_TASK_DIRECTORY` 或 `EVIDENCE_CONFLICT`；只有 `HEALTHY` 可自动续接执行，证据完整且正被聚焦的 `PROVISIONAL` 只可续提原 task package 创建流程。`TOMBSTONE` 只允许 pre-freeze `TERMINAL/ABANDONED` 且有匹配 staging 取消 evidence。尚未登记的目录记录在顶层 `integrityIssues`，不伪装成 task entry 状态。
- `integrityIssues` 每项至少包含稳定 `issueId`、`code`、发现路径、摘要、`detectedAt`、evidence 和解除动作；`UNREGISTERED_TASK_DIRECTORY` 在用户批准导入前只存在于这里，不能凭空生成一个看似已登记的 task 状态。
- `tasks` 按 `taskId` 升序稳定序列化，task ID 必须唯一。
- `gateLevel` 使用 `Light/Full`，`workScale` 使用 `Micro/Task/Capability/Project`，`changeType` 使用七类变更类型；固定代表说明和当前任务说明都是必填字符串。
- `nextResponsibleParty` 使用 `user/host/developer/reviewer/none`。`blockers` 每项必须有稳定 ID、摘要、责任方、解除条件和 evidence；没有阻断时为空数组。
- `activeOperations` 平时为空数组。每项保存稳定 `operationId`、动作类型、owner、`startedAt`、输入 evidence 摘要、恢复条件、`status=CLAIMED|DISPATCH_CONFIRMED|RESULT_OBSERVED`，以及可选的 `waveId`、`agentId`、`assignedTaskIds` 和 `externalRun`。`externalRun` 至少包含运行时、可查询的 run/session handle、调度确认时间和确认摘要；运行时没有 durable handle 时明确记为不可自动重放。宿主需要在锁外执行 CLI、派遣 Agent 或其他不可瞬时完成的外部动作时，必须先在锁内写 `ACTION_CLAIMED` event 并加入条目；取得运行时调度回执后立即在新锁事务写 `ACTION_DISPATCH_CONFIRMED`，完成或确认失败后再写结果 event 并删除对应条目。parallel 必须在一次锁内事务中声明同波全部成员，保证 operation ID、Agent、T 范围互异后才并发派遣；每个成员单独结算，不能用一个无法展开的单值 operation 代表整波。
- `creationContext` 在普通健康任务上为 `null`。每个 `PROVISIONAL` 记录都必须保存 `kind=INDEPENDENT|REVISION|FOLLOW_UP`、可空 `sourceTaskId`、计划的双向关系、创建前 `previousFocus` 快照、最终焦点策略和用户批准 evidence。派生任务登记时，`currentFocus` 临时指向该 PROVISIONAL 记录，作为崩溃恢复指针；这不改变来源任务的生命周期、终态或最终焦点处置。冻结物化后若关系或来源处置尚未提交，允许短暂成为 `HEALTHY + creationContext`，但 phase 必须是 `FINALIZING_TASK_CREATION`、nextAction 必须精确指向关系/来源/焦点收尾，且仍禁止开发。新任务冻结、关系和来源处置原子完成后清空 `creationContext` 并确认最终焦点；取消时把它保留在 TOMBSTONE 中，并只在旧焦点仍合法时恢复 `previousFocus`，否则使用 `NONE`。
- `relations` 中每项包含 `type`、`taskId` 和 `evidence`；类型使用 `REVISION_OF`、`REVISED_BY`、`FOLLOW_UP_OF`、`FOLLOWED_BY` 或 `RELATED_TO`。
- `terminalHistory` 是追加式历史；任务被显式重开或从 `COMPLETED` 改为 `SUPERSEDED` 前，先保存原 disposition、终态时间、确认 evidence、重开/替代时间和对应 evidence。
- `plannedStart` 和 `plannedEnd` 只记录用户或冻结计划明确给出的日期，不由 Agent 猜测。
- `evidence` 全部使用相对 `.ai-dev-loop/` 的路径并携带摘要；正常记录的 `lastTransition.recordRevision` 必须等于当前任务领域记录 revision。首次兼容迁移分三类：没有历史 lifecycle event 时使用 `recordRevision=0`、`lastTransition=null`，后续首次变化写 `event-000001`；存在完整兼容事件链时导入其最大连续 record revision 与最后 event；发现不连续、重复、摘要冲突或无法验证的历史事件时，使用 `integrity=EVIDENCE_CONFLICT`、`lifecycleStatus=UNKNOWN`、`recordRevision=<已发现的最大保留编号>`、`lastTransition=null`，并由首个 `WORKSPACE_REGISTRY_MIGRATED` workspace event 和顶层 integrity issue 完整承载冲突。冲突记录在人工解决前不得写 task event；解决 event 必须从保留最大编号加一，避免覆盖旧文件。目标不存在、越界、摘要不匹配或迁移不一致时不能推进状态，只有带完整 `pathState=PLANNED + stagingPath + stagingSha256 + sourceSha256` 的 `PROVISIONAL` pre-freeze evidence 允许逻辑目标暂不存在。TOMBSTONE 的创建/取消 evidence 使用保留且实际存在的 `.host-staging/<task-id>/` 路径，不使用已不存在的最终目录路径。
- `projections.expectedRecordRevision` 是本次应渲染的 task record revision，`renderedRecordRevision` 与各文件摘要只表示最后一次成功落盘版本；普通任务的 `status` 使用 `PENDING/CURRENT`。任务事实提交时先把 expected 推进并保持旧 rendered、标为 `PENDING`；首次迁移没有旧投影时允许 `renderedRecordRevision=null` 和空摘要。全部文件写入并核对 marker / 摘要后，才用不增加 task `recordRevision` 的全局 projection-ack 事务把 rendered 推进并标为 `CURRENT`。因此投影失败不会虚报已同步。`projectPlan` 与 `finalAcceptanceReport` 在不适用或尚未生成时为 `null`；其他任务或焦点变化不要求重写它们。只有 `workspace-overview.md` 跟随最终全局 registry revision。投影落后时从 registry、冻结授权和 lifecycle event 重建，绝不能反向解析 Markdown 覆盖 registry。

`TOMBSTONE` 使用独立字段矩阵：`currentRound=null`、`activeOperations=[]`、保留取消时的 `creationContext`、`evidence.authorization=null`、`completion` 的 M/W/T/SOP 全为零且全部门禁为 `NOT_RUN`、`semanticReviewRoute=NOT_SELECTED`、`needHumanReviewReason=null`、`humanSemanticReviewOutcome=NOT_RUN`、`manualConfirmation=NOT_READY`，并且 `projections=null`。`evidence.latestRound`、`evidence.lastTransition` 与取消授权必须指向保留的 staging tombstone evidence。取消或以后建立 follow-up 关系时都不创建或等待该 tombstone 的 task Markdown projection ack；它仍以 staging lifecycle event 增加 record revision，并与其他受影响任务在同一 registry/workspace event 事务提交。
- `workspaceEvents` 是 registry 内追加式、哈希串联的工作区事件，只承载无法归属单一 task 的 currentFocus / integrity issue / migration / lock recovery / projection ack，以及跨任务原子事务摘要；旧条目不得编辑或删除。每项记录 `basedOnRegistryRevision`、`committedRegistryRevision`、前一事件摘要、完整 changes / evidence，以及跨任务时全部 `affectedTaskEvents` 路径、record revision 与摘要。

`currentFocus` 的空值组合固定如下；不满足即为 `TASK_REGISTRY_INVALID`：

| status | taskId | candidateTaskIds / candidateIssueIds | purpose / selection 字段 |
| --- | --- | --- | --- |
| `NONE` | `null` | 两者均为空数组 | `purpose` 与全部 selection 字段为 `null` |
| `FOCUSED` | 已登记 task ID | 两者均为空数组 | `purpose` 非空；除兼容迁移外 selection 字段与 evidence 完整 |
| `WAITING_FOR_TASK_SELECTION` | `null` | task 至少两个；issue 为空 | `purpose` 与 selection 字段为 `null` |
| `NEED_RESUME_CLASSIFICATION` | `null` | task 与 issue 合计至少一个 | `purpose` 与 selection 字段为 `null` |

## 确定性恢复

每次开发类消息在 `start`、新 task ID、baseline 草稿或新任务目录之前按以下短路顺序执行：

1. 先只读校验已有 JSON/schema、协调根、revision、task ID 唯一性和路径不越界，并验证 `workspace-overview.md` 的 registry revision marker 与从当前 registry 确定性渲染的内容一致；同时只读计算 Git 忽略/保留名称门禁。JSON 等全局不变量失败时关闭写入型恢复。总纲缺失、落后或损坏时，任何写入型恢复或外部动作前都必须在根级锁内按当前 revision 原子重建；重建失败报告 `TASK_REGISTRY_PROJECTION_FAILED`。Git 门禁失败只设置本次宿主内存中的 `writeBlocked`，不得反向写入未安全忽略的控制面；在首次 registry、staging、任务包、投影修复或业务写入前要求配置。若消息最终明确路由为 `None` 且不依赖修改任务状态，可在展示只读告警后直接回答，不初始化或更新控制面；
2. 只比较 `.ai-dev-loop/` 的单层普通任务目录名与注册表 `relativePath`，记录局部缺失或未登记项；按有效 CLI lock / recovery journal 排除其精确控制路径，不得按通配名称忽略目录。另行检查 `.host-staging/` 并记录与 `PROVISIONAL` entry 的对应关系；orphan staging 按迁移规则处理，不得递归扫描或据此另选任务；
3. 用户给出精确 task ID、精确任务路径，或刚从候选表明确选择时，只校验目标记录、目标目录和目标 evidence。目标存在非空 `activeOperations` 时只能逐项核对并续提已有动作，不能重新执行。目标为 `TOMBSTONE` 时只展示取消 evidence，并询问是否明确创建使用新 ID 的 `FOLLOW_UP_OF` 关联任务；不得重开或复用 tombstone ID。证据完整的 `PROVISIONAL + creationContext` 只按 `nextAction` 续提原创建流程；`HEALTHY + phase=FINALIZING_TASK_CREATION + creationContext` 只续提关系、来源处置和最终焦点收尾，二者都不得进入开发。没有 creationContext 的 `HEALTHY` 才可原子覆盖旧焦点并按正常阶段继续。creationContext 出现在其他 integrity/phase 组合时进入 `NEED_RESUME_CLASSIFICATION`，不得处置来源任务；无关 `UNKNOWN` 或历史目录异常只保留告警，不得阻断这次显式选择；
4. 没有显式目标但 `currentFocus.status=FOCUSED` 时，先处理任何非空 `activeOperations`，逐项核对已有动作及其输出并禁止重新派遣；随后只有证据完整的 `PROVISIONAL + creationContext`，或 `HEALTHY + phase=FINALIZING_TASK_CREATION + creationContext`，才按 `nextAction` 续提创建/关系/来源处置/最终焦点收尾并禁止开发。其余目标才按合法组合处理：`ACTIVE/WAITING_USER + WORKING_TASK` 按原阶段续接；`BLOCKED + BLOCKER_CONTEXT` 只展示阻断与解除条件；`DEFERRED + DEFERRED_CONTEXT` 请求显式恢复授权；`TERMINAL + FEEDBACK_CONTEXT` 只进入反馈分类。焦点为 `UNKNOWN`，或目标缺失、完整性异常、creationContext 组合非法、状态与 purpose 组合非法时写 `NEED_RESUME_CLASSIFICATION`，不得静默回退到另一个任务；
5. 没有显式目标和有效焦点时，若存在 `UNKNOWN` 或局部完整性异常，先写 `NEED_RESUME_CLASSIFICATION`；否则计算全部健康 `ACTIVE/WAITING_USER` 候选：大于一个写 `WAITING_FOR_TASK_SELECTION`，恰好一个才以 `UNIQUE_ELIGIBLE_TASK` 自动设置焦点，零个则展示 `BLOCKED`、`DEFERRED` 和终态摘要后进入反馈目标或新任务确认；
6. 选中后读取冻结授权、注册表 evidence、任务总览、进度、最终报告和最新轮次，按 `phase` / `nextAction` 进入原流程节点。

`candidateTaskIds` 必须按 task ID 升序保存，`candidateIssueIds` 按 issue ID 升序保存：`WAITING_FOR_TASK_SELECTION` 只包含健康的 `ACTIVE/WAITING_USER` 候选；`NEED_RESUME_CLASSIFICATION` 的两个数组分别包含待分类/焦点失效任务与无合法 task entry 的完整性问题。焦点不会因时间流逝而“过期”；只有目标缺失、状态组合非法、完整性不健康或证据冲突才算失效。

`activeOperations.status=CLAIMED` 但没有 durable dispatch receipt 时，恢复者不能根据“看不到结果”推断动作未启动，更不能重新派遣。只能使用运行时提供的 operationId 幂等查询或权威 run/session 列表核对：证明已启动则补写 `ACTION_DISPATCH_CONFIRMED`；证明从未启动则以 `ACTION_NOT_DISPATCHED` 结算旧 operation，再用新 operationId 重新 claim；两者都无法证明时把任务写为 `BLOCKED`、焦点改为 `BLOCKER_CONTEXT` 并等待用户/运行时解除。任何用户确认都不能伪造运行时事实。

禁止按目录修改时间、最新文件、task ID 命名、标题相似度、消息哈希或“看起来像同一需求”选择任务。目录很多不会改变上述优先级。

显式选择 `BLOCKED` 任务时只恢复其阻断信息和解除条件，不自动派遣；显式选择 `DEFERRED` 任务时先取得恢复授权；`UNKNOWN` 必须先完成 `NEED_RESUME_CLASSIFICATION`。路径、证据或协调根不一致时关闭自动恢复。

## workspace-overview.md

每次注册表成功迁移后确定性重建根级总纲，至少包含：

1. 协调工作区、生成时间、registry revision 和当前焦点；
2. `ACTIVE / WAITING_USER / BLOCKED / DEFERRED / UNKNOWN / TERMINAL` 精确数量；
3. 当前焦点的阶段、当前轮次、周期、完成计数、下一动作和证据入口；
4. 全部 `ACTIVE` 与 `WAITING_USER` 任务，不得截断；
5. 全部 `BLOCKED`、`DEFERRED`、`UNKNOWN` 和一致性异常；
6. 最近终态任务，默认按 `completedAt` 显示最近 20 条；完整历史始终保留在 JSON；
7. 修订、后续和相关任务关系链；
8. 每项的精确 M/W/T/SOP 完成数、机械/语义门禁、人工语义审查、最终确认、周期和证据入口。

使用以下稳定骨架：

```markdown
# 工作区任务总纲

> coordination root: <absolute-root>
> task-registry revision: <N>
> generated at: <ISO-8601>

## 当前焦点
| task | 状态 / 阶段 | 当前轮次 | 周期 | M / W / T / SOP | 门禁 / 人工语义 / 最终确认 | 下一步 | 证据入口 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| <task-id> | WAITING_USER / WAITING_FOR_MANUAL_ACCEPTANCE | round-02 | <起止与历时> | 0/0 · 0/0 · 4/4 · 10/11 | PASS/PASS · NOT_RUN · WAITING | <动作> | <lastTransition / latestRound> |

## 状态汇总
| ACTIVE | WAITING_USER | BLOCKED | DEFERRED | UNKNOWN | TERMINAL |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 1 | 0 | 0 | 0 | 8 |

## 活动与等待用户
<列出全部，不截断>

## 阻断、延期、待分类与一致性异常
<列出全部；每项含解除条件或人工动作>

## 最近终态
<默认最近 20 条；完整历史见 task-registry.json>

## 任务关系链
<REVISION / FOLLOW_UP / RELATED 关系和 evidence>
```

总纲只显示注册表已有事实。它可以为了可读性限制终态展示数量，但不得隐藏任何非终态或异常任务，也不得作为恢复选择的输入。
