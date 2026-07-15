# 注册表生命周期、终态与迁移

## 目录

- [生命周期状态](#生命周期状态)
- [周期与完成情况](#周期与完成情况)
- [修订、后续任务与终态](#修订后续任务与终态)
- [旧任务迁移](#旧任务迁移)
- [一致性、并发与错误](#一致性并发与错误)

注册表 schema、当前焦点和确定性恢复见 [task-registry.md](task-registry.md)；事件、投影、锁与并发事务见 [registry-transactions.md](registry-transactions.md)。

## 生命周期状态

`lifecycleStatus` 只描述任务级生命周期类别：

| 状态 | 含义 | 自动续接资格 |
| --- | --- | --- |
| `ACTIVE` | 宿主、开发或门禁步骤正在推进 | 有 |
| `WAITING_USER` | 等待需求、模式、工作区、验收或反馈确认 | 有 |
| `BLOCKED` | 存在明确阻断和解除条件 | 无；显式选择时只展示阻断 |
| `DEFERRED` | 用户明确延期或保留待办 | 无 |
| `TERMINAL` | 已完成、放弃或被修订替代 | 无 |
| `UNKNOWN` | 旧任务或证据冲突，无法可靠分类 | 无；必须人工分类 |

`phase` 保存可恢复的细粒度阶段，例如 `WAITING_FOR_REQUIREMENT_CONFIRMATION`、`WAITING_FOR_WORKSPACE_AUTHORIZATION`、`WAITING_FOR_DEVELOPMENT_MODE_SELECTION`、`WAITING_FOR_EXECUTION_TOPOLOGY_SELECTION`、`PREPARING_REPAIR_ROUND`、`DEVELOPMENT`、`MECHANICAL_GATE`、`SEMANTIC_ACCEPTANCE`、`WAITING_FOR_MANUAL_ACCEPTANCE`、`WAITING_FOR_FEEDBACK_CONFIRMATION` 或 `NEED_HUMAN_REVIEW`。`nextAction` 必须是一个明确动作，不能只写“继续处理”。

`terminalDisposition` 仅在 `lifecycleStatus=TERMINAL` 时使用：

- `COMPLETED`：所有适用任务和门禁有证据，且用户已最终接受；
- `ABANDONED`：用户明确放弃；
- `SUPERSEDED`：已有成功建立并冻结的修订任务替代当前版本。

`lifecycleStatus=TERMINAL` 当且仅当 `terminalDisposition` 与 `cycle.completedAt` 非空；所有非终态记录的这两个字段必须为 `null`。`BLOCKED` 必须至少有一个未解除 blocker，并使用 `BLOCKER_CONTEXT`（若被显式聚焦）；非 `BLOCKED` 记录不得保留已解除 blocker。`DEFERRED` 只能来自用户延期 evidence；`UNKNOWN` 的 phase 必须指向分类动作，不能携带可派遣的 `nextAction`。

开发 Agent 返回的总体 `COMPLETED` 只表示一次实现调用结束，最多把执行任务标记为 `IMPLEMENTED`；它绝不等于 `TERMINAL/COMPLETED`。

## 周期与完成情况

周期使用真实时间点和轮次数，不猜工期：

- `createdAt`：`TASK_CREATION_APPROVED` 与首个 `PROVISIONAL` 记录成功提交、任务首次可恢复的时间；不是 CLI 冻结目录出现时间；
- `packageFrozenAt`：CLI/等价冻结包完成并校验指纹的时间；冻结前和 pre-freeze tombstone 为 `null`；
- `developmentStartedAt`：第一次实际开发写入开始时间；
- `lastActivityAt`：最后一条已落盘迁移证据时间；
- `completedAt`：进入终态时间，非终态为 `null`；
- `developmentRounds`、`repairRounds`、`acceptanceRounds`：分别按已落盘轮次证据计数；
- `plannedStart`、`plannedEnd`：只反映明确计划，不自动生成。

`workspace-overview.md` 可根据这些时间点显示“已历时”或终态总周期；动态时长不反写成新的事实。

完成情况只显示精确计数和门禁状态，不使用主观百分比：

- 里程碑：`verified / total`；Micro、Task、Capability 不适用时使用 `0 / 0`，Project 必须按计划计数；
- 工作流：`verified / total`；Micro、Task 不适用时使用 `0 / 0`，Capability 与 Project 必须按总览/计划计数；
- 执行任务：`implemented`、`verified`、`total`；
- SOP：`completed`、`applicable`、`skipped` 和 `total`，每个跳过项必须在 `progress.md` 有理由；
- 机械门禁：`NOT_RUN / PASS / FAIL / NEED_HUMAN_REVIEW`；
- 语义验收：`NOT_RUN / PASS / FAIL / NEED_HUMAN_REVIEW`；
- 语义验收路线：`semanticReviewRoute = NOT_SELECTED / INDEPENDENT / HUMAN`；
- 需要人工处理的原因：`needHumanReviewReason = null / ISOLATED_REVIEWER_UNAVAILABLE / EVIDENCE_INCOMPLETE / OWNERSHIP_UNRESOLVED / INTEGRITY_CONFLICT`；
- 人工语义审查：`humanSemanticReviewOutcome = NOT_RUN / PASS / FAIL`；只有缺少隔离 reviewer 的人工路径使用；
- 最终人工确认：`manualConfirmation = NOT_READY / WAITING / ACCEPTED / REJECTED`，只表示图 4 的用户最终决定。

所有计数和轮次数都是非负整数。必须满足 `milestones.verified <= milestones.total`、`workstreams.verified <= workstreams.total`、`tasks.verified <= tasks.implemented <= tasks.total`、`sop.completed <= sop.applicable`、`sop.applicable = sop.total - sop.skipped` 和 `repairRounds <= developmentRounds`。`repairRounds` 是 `developmentRounds` 中被归类为修复的子集；一次开发轮次可以同时产生验收证据，所以 `acceptanceRounds` 也可能与开发轮次重叠，三个数字不能相加当作总轮次。

`createdAt <= lastActivityAt`；其他时间非空时必须满足 `createdAt <= packageFrozenAt <= developmentStartedAt <= lastActivityAt`，未发生的中间阶段为 `null`。首次进入终态时 `completedAt` 等于该迁移的 `lastActivityAt`；终态后的反馈证据可以继续推进 `lastActivityAt`，因此此后允许 `completedAt <= lastActivityAt`。当前 disposition 发生变化时按该终态迁移重新写 `completedAt`。`plannedStart/plannedEnd` 同时存在时前者不得晚于后者。pre-freeze 取消允许只有 `createdAt/lastActivityAt/completedAt` 非空。

计数不能单独终结任务。只有 `tasks.verified = tasks.total`、`sop.completed = sop.applicable`、适用的 milestones/workstreams 全部 verified、`mechanicalGate=PASS`，并且满足下列任一路线，最后再由用户把 `manualConfirmation` 明确写为 `ACCEPTED`，才能写 `TERMINAL/COMPLETED`：

- 独立路线：`semanticReviewRoute=INDEPENDENT`、`semanticAcceptance=PASS`、`needHumanReviewReason=null` 且人工语义审查未运行；
- 人工路线：`semanticReviewRoute=HUMAN`、`semanticAcceptance=NEED_HUMAN_REVIEW`、`needHumanReviewReason=ISOLATED_REVIEWER_UNAVAILABLE`、`humanSemanticReviewOutcome=PASS`，并且引用的 `review-plan.json` 明确为 human route、`reviewerKind=human-review`、`isolation=not-available`。此时不得存在 evidence、归属、完整性或其他未解除 blocker。

人工路径不得改写成独立 `PASS`，也不能用人工 PASS 绕过证据或归属缺失。`ABANDONED` 与 `SUPERSEDED` 使用各自的用户/关系证据，不伪造上述完成等式。

## 修订、后续任务与终态

- `REPAIR_CURRENT`：保持同一 task 记录，增加 repair round，更新周期和证据，不创建新条目。
- `REVISE_CURRENT`：用户确认后才创建修订任务目录和新记录；新任务可恢复且基线已冻结后，写入双向关系，再把旧任务置为 `TERMINAL/SUPERSEDED` 并把焦点切到新任务。
- `CREATE_FOLLOW_UP`：创建 `FOLLOW_UP_OF/FOLLOWED_BY` 关系，但不会自动终结原任务；原任务处置必须单独确认。
- `NEW_TASK + INDEPENDENT`：创建独立记录；在切换焦点前明确原任务是接受、延期、继续还是放弃。
- 用户最终接受：写 `TERMINAL/COMPLETED`、`completedAt` 和确认 evidence，把焦点目的改为 `FEEDBACK_CONTEXT`；只有用户明确清除或切换时才离开该焦点。
- 用户放弃已建立任务包：写 `TERMINAL/ABANDONED` 和确认 evidence；如果它正是当前焦点，改为 `FEEDBACK_CONTEXT`，只有用户明确清除或切换才离开。pre-freeze 取消按 `TOMBSTONE` 规则清空或恢复先前焦点。不得仅因长期无活动自动放弃。

终态任务收到反馈时使用以下迁移矩阵，确认分类前保持原终态不变：

| 当前终态 | 已确认路线 | 迁移动作 |
| --- | --- | --- |
| `COMPLETED` | 同一冻结 R/A/T 的修复 | 先取得 `REOPEN_CURRENT` 明确确认，把原终态追加到 `terminalHistory`；清空当前 disposition 与 `completedAt`，重置 `semanticReviewRoute=NOT_SELECTED`、`needHumanReviewReason=null`、人工语义审查为 `NOT_RUN`、最终确认为 `NOT_READY`、本轮机械/语义门禁为 `NOT_RUN`，按受影响 T 重算完成计数，转为 `ACTIVE / PREPARING_REPAIR_ROUND` 并增加 repair round |
| `COMPLETED` | 目标、范围或验收变化 | 原任务保持 `COMPLETED`，新修订冻结成功后把完成记录追加到历史，再把当前 disposition 改为 `SUPERSEDED` |
| `ABANDONED` + `HEALTHY` 冻结包 | 恢复原授权 | 必须先取得显式重开授权，再按 `REOPEN_CURRENT` 保存历史并恢复；不得因一句“继续”自动重开 |
| `ABANDONED` + `TOMBSTONE` | 恢复未冻结的旧意图 | 禁止重开或复用原 ID；展示取消证据，用户确认后用新 ID 创建 `FOLLOW_UP_OF/FOLLOWED_BY` 关联任务 |
| `SUPERSEDED` | 修改旧版本 | 禁止直接 `REOPEN_CURRENT`。沿规范 revision DAG 解析唯一未被替代叶子，并在用户确认后从该叶子创建新修订；旧任务、关系、门禁和计数保持终态不变 |

`REOPEN_CURRENT` 只是已有冻结包终态任务执行 `REPAIR_CURRENT` 的额外确认步骤，不改变 frozen baseline，也不创建新的 task ID；它不适用于 pre-freeze `TOMBSTONE`。旧终态确认、门禁和完成记录必须继续可追溯。`terminalHistory` 每项保存原 disposition、原终态时间、当时的 completion / gate / semanticReviewRoute / needHumanReviewReason / humanSemanticReviewOutcome / manualConfirmation 快照及确认 evidence；当前 `cycle.completedAt` 始终表示当前 disposition 的终态迁移时间，重开时清空，从 `COMPLETED` 改为 `SUPERSEDED` 时先保存原完成快照，再把当前值改为替代发生时间。

“最新修订”只指从目标沿折叠后的 `旧 → 新` 规范边可达的唯一非 superseded 叶子；不存在或出现多个叶子时进入 `NEED_RESUME_CLASSIFICATION`，不得按时间、名称或 revision 字样猜测，也不得同时自动激活多个分支。

关系不得自引用，目标 task 必须存在；`REVISION_OF/REVISED_BY`、`FOLLOW_UP_OF/FOLLOWED_BY` 必须成对，`RELATED_TO` 必须双向对称。无环检查不能直接使用互反存储边：先把一对 revision 折叠为规范语义边“旧任务 → 新修订”，把 follow-up 折叠为“来源任务 → 后续任务”，分别对这两类规范边做 DAG 校验；`RELATED_TO` 不参与无环检查。每一侧都引用同一事务建立关系的 evidence。

跨任务更新的安全顺序是：反馈 event → staging 中的 `TASK_CREATION_APPROVED` event → 带 `creationContext` 的 `PROVISIONAL` 新记录并把 currentFocus 临时指向它（此时已可恢复，来源任务不变）→ claim 后在锁外调用 CLI 创建并冻结任务包 → 锁内物化 staging 并校验 → 新记录改为 `HEALTHY / FINALIZING_TASK_CREATION`，保留 creationContext 并明确下一收尾动作 → 双向关系 → 旧任务终态（如适用）→ 清空 creationContext 并确认最终焦点。每一段任务事实提交后都必须完整执行“registry(PENDING) → workspace overview(PENDING) → task projections → projection ack → workspace overview(CURRENT)”写回事务，不能止于总纲或任务 Markdown。任何一步失败都不得删除旧任务包或重新生成另一个 task ID。

## 旧任务迁移

注册表不存在但 `.ai-dev-loop/` 已有任务目录时，执行一次兼容迁移：

1. 只枚举 `.ai-dev-loop/` 的直接普通子目录；候选必须是合法 task ID 且包含 `mode.json`、`state.json` 或冻结副本之一。明确排除保留的 `.host-staging`；CLI 的 `.gated-loop-runtime-<hash>.lock` 与对应 `.recovery.json` 是文件而非任务。形如 `<task>.tmp-*` 或 `<task>.backup.tmp-*` 的目录只有被有效 CLI lock / recovery journal 精确引用时才作为控制项排除，不能按通配名称排除，因为该名称也可能是历史合法 task ID；不按名称或时间排序选择；
2. 读取每个任务已有的 `state.json`、冻结副本、`progress.md`、`final-acceptance-report.md`、最新轮次证据和全部 `rounds/**/lifecycle-events/event-*.json`；对所有将作为迁移输入的文件先保存相对路径与摘要，不覆盖未知旧内容。历史 event 必须校验 taskId、文件名/recordRevision、严格连续性、create-new 唯一性、evidence 摘要和事件自身一致性；
3. 有直接证据时填入生命周期、阶段、周期和计数；无法证明时使用 `UNKNOWN`，绝不能只凭 `state.json` 推断完成。没有历史 lifecycle event 的 task 使用 `recordRevision=0`、`evidence.lastTransition=null`；事件链完整时导入最大连续 revision 与最后 event；链不连续、重复或冲突时使用 `UNKNOWN / EVIDENCE_CONFLICT`，把 `recordRevision` 保留为已发现最大事件编号、`lastTransition=null` 并建立 integrity issue，禁止自动更新。每项有 task projection 时把 expected 设为上述 record revision、`renderedRecordRevision=null`、`status=PENDING`；
4. 取得根级单写锁并复核目录与历史事件集合后，以 `basedOnRegistryRevision=0`、`committedRegistryRevision=1` 创建首个 `WORKSPACE_REGISTRY_MIGRATED` workspace event。它必须列出全部 task ID、每个来源路径/摘要、历史事件导入结果、保留的最大 record revision、推导或 `UNKNOWN` 理由、integrity issue、迁移操作者和时间；`affectedTaskEvents=[]`、`previousEventSha256=null`。迁移初始化不伪造 task lifecycle event；无历史事件的任务后续从 `event-000001` 开始，已导入链从最大 revision 加一，冲突链则必须先人工解决且永不覆盖已有文件；
5. 只有一个有证据的 `ACTIVE/WAITING_USER` 时才可在 revision 1 设置焦点；多个候选或任何可能影响选择的 `UNKNOWN` 存在时进入人工选择或分类。兼容迁移无法取得选择 event 时，`selectionEvidence=null` 并由 migration workspace event 解释；
6. 原子提交包含全部目录的 registry revision 1，先重建 `workspace-overview.md(PENDING)`，再从 registry、冻结证据和已摘要的旧可读内容确定性生成或导入各 task projection；每份写该任务实际导入或保留的 `recordRevision` marker（无历史事件才是 0）并核对摘要，不能改写 baseline、brief、state 或其他冻结文件。`EVIDENCE_CONFLICT` 的投影必须明确展示冲突和禁止自动推进，不能伪装成健康状态；
7. 追加 `PROJECTIONS_ACKNOWLEDGED` workspace event 提交 revision 2，把所有已渲染任务投影标为 `CURRENT`，再重建 `workspace-overview.md(CURRENT)`。任一步中断都按 registry transaction/revision 恢复，不能重新扫描后生成另一套 revision 1；
8. 不移动、重命名、归档或删除旧任务目录，不向任务根新增 CLI 不认识的文件。

注册表存在但发现未登记目录时，先在顶层 `integrityIssues` 记录 `UNREGISTERED_TASK_DIRECTORY` 并停止自动选择；用户确认导入后才创建 `UNKNOWN` task entry 并移除对应 issue。注册表记录的目录缺失时把 `integrity` 置为 `MISSING_TASK_DIRECTORY` 并排除自动恢复。不能为了让注册表“干净”而清理历史。

`.host-staging/` 不参与任务候选枚举，但每次恢复都要单独检查其直接子目录：已有匹配 `PROVISIONAL` entry 时按 `nextAction` 续提；匹配 `TOMBSTONE` 的 staging 只作为取消证据保留；匹配 `HEALTHY` 的残留 staging 只有在 `activeOperations` 为空、所有已物化逻辑路径与摘要和 projection ack 完全一致时，才可在锁内清理为一次幂等收尾，否则记录 integrity issue。orphan staging 只有在 `TASK_CREATION_APPROVED.basedOnRegistryRevision` 与当前 revision 精确相等、task ID 尚未登记、`currentFocus.status=NONE`、摘要与用户批准证据完整时，才允许在锁内恢复同一 ID 的 provisional 创建；首次尚无 registry 时只为该比较使用 revision `0` 与空焦点。任一条件不满足就写带 `issueId` 的 integrity issue 并请求用户确认；不能复活旧批准、覆盖现焦点或触发新的 task ID。

## 一致性、并发与错误

开始恢复和每次写回前检查：

- schema、协调根、revision、task ID 和相对路径合法；
- 可执行 current focus 必须指向存在且 `HEALTHY`、`creationContext=null` 的普通记录；创建流程焦点可以指向与 staging、创建 event 和 `nextAction` 完整匹配的 `PROVISIONAL` 记录，或 phase/nextAction 完整匹配的 `HEALTHY + creationContext` 收尾记录，但两者都只能续提创建、关系、来源处置与最终焦点，不能派遣开发；
- lifecycle 与 terminal disposition 组合合法；
- 终态、关系、计数和时间都有存在的 evidence；
- 任务目录集合与注册表完整对应；`TOMBSTONE` 以匹配 staging 取消 evidence 代替任务目录。若 `activeOperations` 中有 task package 创建/替换，目标目录暂时缺失只报告 `TASK_PACKAGE_OPERATION_IN_PROGRESS`，不得提前改为 `MISSING_TASK_DIRECTORY`；
- 不存在两个宿主基于同一旧 revision 覆盖写入。

仓库迁移到新的绝对路径时不能静默改写 `coordinationRoot`。只有用户确认重绑定、旧根与新根身份可核对且重绑定 evidence 已落盘后，才能在一次 revision 更新中修改根路径并重建总纲。

错误处理：

- `TASK_REGISTRY_INVALID`：schema、根路径、字段或状态组合无效；
- `TASK_REGISTRY_INCONSISTENT`：目录、证据、关系或投影与注册表冲突；
- `TASK_REGISTRY_CONFLICT`：并发 revision 冲突；
- `TASK_REGISTRY_LOCKED`：另一个可核对的宿主持有活跃单写锁；
- `TASK_REGISTRY_LOCK_RECOVERY_REQUIRED`：残留锁、所有权或中断事务无法安全自动续提；
- `TASK_PACKAGE_OPERATION_IN_PROGRESS`：已有已声明的 task package 创建或替换操作，目标目录暂时不可判定；
- `TASK_REGISTRY_WRITE_FAILED`：规范注册表未成功持久化；
- `TASK_REGISTRY_PROJECTION_FAILED`：总纲或任务 Markdown 未能从最新 revision 刷新；
- `TASK_REGISTRY_NOT_IGNORED`：根级控制文件已跟踪或未被 Git 忽略；
- `TASK_CONTROL_NAME_CONFLICT`：根级控制文件名已被 task 目录或其他条目占用；
- `WAITING_FOR_TASK_SELECTION`：存在多个合格候选；
- `NEED_RESUME_CLASSIFICATION`：至少一个相关任务为 `UNKNOWN`。

所有错误都 fail closed：保留磁盘事实、展示候选和解除条件，不调用 `start`，不生成新 task ID，也不靠最近修改时间自动修复。
