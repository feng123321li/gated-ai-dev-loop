# 注册表事务、暂存与并发

## 目录

- [Git 忽略与保留名称门禁](#git-忽略与保留名称门禁)
- [文件布局与权威层级](#文件布局与权威层级)
- [CLI 冻结替换兼容](#cli-冻结替换兼容)
- [生命周期事件](#生命周期事件)
- [写回事务](#写回事务)
- [单写锁与并发恢复](#单写锁与并发恢复)

注册表 schema、当前焦点和确定性恢复见 [task-registry.md](task-registry.md)；生命周期、计数、终态与迁移见 [registry-lifecycle.md](registry-lifecycle.md)。

## Git 忽略与保留名称门禁

现有机械门禁把 `.ai-dev-loop/**` 视为受保护路径，并只从真实 diff 中排除当前 task 目录。根级注册表和总纲如果已被 Git 跟踪，或作为未忽略的 untracked 文件出现，会使 `self-check` 失败。因此在创建或更新根级文件前必须验证：

1. `task-registry.json`、`workspace-overview.md`、`.host-staging/**`、`.task-registry.lock` 和 `.task-registry.lock.recovery-*` 均未被 Git 跟踪；
2. `git check-ignore --no-index` 或宿主等价检查确认上述控制路径和目标 task 路径都被忽略；优先要求整个 `.ai-dev-loop/` 被仓库级规则覆盖，不能只验证两个文件；
3. `.ai-dev-loop/` 根没有同名目录、符号链接、junction 或其他重解析点；
4. 新 task ID 不等于 `task-registry.json`、`workspace-overview.md`、`.host-staging`、`.task-registry.lock` 或任何 `.task-registry.lock.recovery-*` 控制名称。

任一目标未被忽略时返回 `WAITING_FOR_REGISTRY_IGNORE_CONFIGURATION`。展示事实，让用户明确授权把 `.ai-dev-loop/` 加入项目 `.gitignore`，或由用户采用等价仓库级忽略配置；不得静默修改 `.gitignore`、`.git/info/exclude` 或全局 Git 配置。根级文件已被跟踪时必须先由用户决定如何解除跟踪，不能继续写入并期待门禁自动排除。`writeBlocked` 与该等待结果只是本次只读检查的宿主内存状态，不是 registry schema 字段；因为控制路径尚不安全，不能为了记录“禁止写入”反而写入它们。

该状态只阻断写入，不阻断纯只读 `None` 回答。已有控制面仍可只读检查并报告问题；一旦要恢复写入型任务、迁移 registry 或创建新任务，必须先解除 `writeBlocked`。

保留名称已被历史 task 目录占用时报告 `TASK_CONTROL_NAME_CONFLICT`，不重命名、移动或覆盖旧目录。必须先由用户选择兼容迁移或等待未来 CLI 支持。

## 文件布局与权威层级

```text
<coordination-root>/.ai-dev-loop/
├── task-registry.json       # 完整机器索引、生命周期权威、当前焦点
├── workspace-overview.md    # 给人的工作区任务总纲，可重建投影
├── .task-registry.lock      # 宿主单写锁；正常完成后不存在
├── .task-registry.lock.recovery-* # 仅显式残留锁恢复时短暂存在
├── .host-staging/           # 冻结前宿主暂存；成功物化后删除
└── <task-id>/               # 现有 CLI 兼容任务包，不新增生命周期文件
    ├── state.json           # 仅表示冻结信封阶段
    ├── baseline.md 或 light-brief.md
    ├── development-overview.md
    ├── progress.md
    ├── final-acceptance-report.md
    └── rounds/
        └── round-NN/lifecycle-events/
```

根级注册表和总纲只保存在协调工作区；跨仓库或多微服务的其他业务工作区不得复制。

权威层级如下：

1. `baseline.md` / `light-brief.md` 与冻结指纹决定开发授权；
2. 真实仓库状态、轮次 JSON、报告和用户确认记录是状态迁移证据；
3. `task-registry.json` 是任务生命周期、关系、当前焦点、周期和完成计数的规范记录，但任何迁移都必须引用第 2 项证据；
4. `workspace-overview.md`、任务内 `development-overview.md`、`progress.md` 和 `final-acceptance-report.md` 是人可读投影；
5. `state.json` 只是冻结信封，不能证明完整生命周期或任务完成。

证据与注册表冲突时停止推进并报告冲突，不能用 Markdown 反向覆盖注册表，也不能用注册表改写冻结授权。

## CLI 冻结替换兼容

现有 Full `prepare` / `freeze` 和 Light confirmed freeze 可能原子替换整个 `.ai-dev-loop/<task-id>/`。即使 `development-overview.md`、`progress.md` 和 `rounds/` 是冻结后的允许可变项，提前放进任务目录也可能在冻结时被删除。因此宿主采用两阶段物化：

1. 用户批准创建 task ID 后，原子取得根级单写锁，再在 `.ai-dev-loop/.host-staging/<task-id>/` 写不可变的 `TASK_CREATION_APPROVED` event；
2. 在同一锁内把 registry 写为 `integrity=PROVISIONAL`、`phase=CREATING_TASK_PACKAGE`、完整 `creationContext` 和当前焦点，先刷新 `workspace-overview.md`，再把冻结前的开发总纲、progress 初始状态与 Project plan 写入 staging。修订或 follow-up 的来源任务此时保持原生命周期和处置；新 PROVISIONAL 焦点只是恢复指针，最终焦点要等冻结、关系和来源处置完成后确认。投影完成后追加 `TASK_PROJECTIONS_STAGED` event，以新 record revision 把 phase 推进为 `WAITING_FOR_REQUIREMENT_CONFIRMATION`，重建投影并安全释放锁；
3. 调用 CLI `start` / `prepare` / `freeze` 前，在锁内写 `ACTION_CLAIMED` event 与 `activeOperations` 条目后释放锁；任务目录中不得存在唯一一份宿主可变状态，并假定 CLI 会替换该目录。看到未清空 operation 的其他宿主只能核对结果并续提，不能重复调用；
4. 冻结成功并重新校验指纹后，重新取得单写锁，把 staging 中的结构化投影来源与事件物化到任务目录允许位置；事件保持字节不变，Markdown 按最终相对路径确定性重渲染。`TASK_PACKAGE_FROZEN` event 必须记录每个来源与最终产物的双路径、双摘要和物化策略。随后把 registry entry 改为 `integrity=HEALTHY`、进入下一 phase，并按“registry(PENDING) → workspace overview(PENDING) → task projections → projection ack → workspace overview(CURRENT)”完成写回事务；
5. 在同一受锁事务链中把 registry 与 `currentFocus.selectionEvidence` 等所有临时路径重定向到已物化的任务相对路径并复核摘要；完整写回事务成功后才删除 staging。失败或崩溃时保留 `PROVISIONAL` 与 staging，下一宿主按 `nextAction` 继续同一 task ID，不重新 `start` 新任务；
6. CLI 缺失时仍使用同一两阶段顺序，避免目录创建成功而 registry 未登记的窗口。

`.host-staging/` 必须已被 Git 忽略、不得交给开发或审查 Agent、不得包含业务代码或秘密。恢复扫描明确排除它。staging 不是冻结授权；最终开发仍只读取已冻结任务包。

冻结前用户取消 task ID 或拒绝继续整项需求时，不得把记录永久卡在 `PROVISIONAL`：在锁内 create-new 写 `TASK_CREATION_CANCELLED` event，把记录置为 `TERMINAL/ABANDONED`、`integrity=TOMBSTONE`、`packageFrozenAt=null`，所有计数为零，并根据 `creationContext.previousFocus` 只恢复仍合法的先前焦点，否则清空为 `NONE`。`.host-staging/<task-id>/` 只保留创建/取消事件与最小 tombstone 元数据；经用户取消授权可清理宿主生成的草稿投影，但不得删除用户来源或业务文件。此 tombstone 合法地没有 `.ai-dev-loop/<task-id>/` 冻结目录，task ID 永久占用且不得用 `REOPEN_CURRENT` 重开；用户以后要恢复需求时，必须先查看取消证据并明确批准一个新 task ID，再建立 `FOLLOW_UP_OF/FOLLOWED_BY` 双向关系，不能自动复活或静默新建。

staging 内事件的 `evidence[].path` 始终写最终任务目录下的稳定逻辑路径，并显式使用 `pathState=PLANNED`；此时必须同时保存确实存在的 `stagingPath`、`stagingSha256` 和结构化 `sourceSha256`，通用“目标必须存在”规则只对这个精确 pre-freeze 组合例外。冻结物化 event 另以 `materializations[]` 保存 `logicalPath`、`stagingPath`、`stagingSha256`、`materializedPath`、`materializedSha256`、`sourceSha256` 和 `strategy=BYTE_COPY|DETERMINISTIC_RENDER`；不能修改旧 event 来补摘要。staging 版 Markdown 必须带“冻结前投影”标记，并使用 staging 可解析链接或把冻结后目标显示为不可点击文本；冻结成功后根据同一结构化来源确定性重渲染最终相对链接，不要求逐字节复制 Markdown，但必须逐项核对上述双路径、双摘要与来源摘要。

## 生命周期事件

每次会改变 task record、M/W/T、SOP、任务关联焦点、阻断或人工确认的动作，都先写一条不可变 task lifecycle event。冻结前写到 `.host-staging/<task-id>/rounds/round-NN/lifecycle-events/`，冻结后物化到 `<task-id>/rounds/round-NN/lifecycle-events/`。文件名使用目标 `recordRevision`，例如 `event-000007.json`；已存在时不得覆盖。没有合法 task 目标的纯全局变化按下述 workspace event 规则处理。

```json
{
  "schemaVersion": 1,
  "eventId": "event-000007",
  "transactionId": "txn-20260715-000011",
  "taskId": "account-export-full",
  "recordRevision": 7,
  "basedOnRegistryRevision": 10,
  "committedRegistryRevision": 11,
  "type": "MANUAL_ACCEPTANCE_REQUESTED",
  "operationId": null,
  "operationIds": [],
  "occurredAt": "2026-07-15T17:35:00+08:00",
  "actor": { "kind": "host", "id": "codex" },
  "from": { "lifecycleStatus": "ACTIVE", "phase": "SEMANTIC_ACCEPTANCE" },
  "to": { "lifecycleStatus": "WAITING_USER", "phase": "WAITING_FOR_MANUAL_ACCEPTANCE" },
  "changes": {
    "milestones": [],
    "workstreams": [],
    "tasks": [],
    "sop": [{ "id": "S-010", "from": "IN_PROGRESS", "to": "COMPLETED" }],
    "manualConfirmation": { "from": "NOT_READY", "to": "WAITING" }
  },
  "reason": "独立语义验收 PASS，等待用户最终确认",
  "evidence": [
    {
      "path": "account-export-full/rounds/round-02/review.json",
      "sha256": "<sha256>"
    }
  ],
  "userConfirmation": null
}
```

事件规则：

- `type` 至少覆盖 task 创建、路由、焦点选择、需求确认、冻结、工作区授权、开发方式/拓扑选择、M/W/T/SOP 状态变化、阻断/解除、门禁、验收、反馈分类、终态、重开和关系变化；
- 单个 claim/dispatched/result 事件必须携带相同的稳定 `operationId`；parallel 波次的批量 `ACTION_CLAIMED` 用升序、唯一的 `operationIds` 一次声明所有成员，后续每个 `ACTION_DISPATCH_CONFIRMED` / result event 使用自己的 `operationId` 结算。不涉及锁外外部动作时 `operationId=null` 且 `operationIds=[]`。
- 用户选择或最终确认事件必须包含 `userConfirmation`，记录确认种类、确认时间和可审计来源；不能拿 reviewer 输出冒充用户选择 evidence；
- `changes` 保存受影响行的前后状态，使 `progress.md` 能从冻结 tasks / project plan 与事件序列确定性重建；未变化的集合使用空数组；
- event 自身不声称机械或语义 PASS，只引用相应 CLI/审查证据及摘要；
- task event 的 `evidence[].path` 使用最终任务目录下的稳定逻辑路径；冻结前只能按 pre-freeze 例外同时保存 `pathState=PLANNED`、`stagingPath`、`stagingSha256` 和 `sourceSha256`。`TASK_PACKAGE_FROZEN.materializations[]` 必须逐项证明从 staging 到最终路径的 byte copy 或确定性重渲染；TOMBSTONE 的创建/取消 event 则直接引用永久保留的 staging evidence；
- event 的 `recordRevision` 必须恰好是当前任务上一 revision 加一，`basedOnRegistryRevision` 必须等于加锁后读到的 registry revision，`committedRegistryRevision` 必须等于前者加一；同一原子事务的所有 task / workspace event 使用同一个 `transactionId`；
- 单任务事务写一条对应 task event；跨任务事务必须在同一锁内为每个受影响 task 各写一条 event，再追加一条列出全部 `affectedTaskEvents` 的 workspace event，最后一次性提交一个 registry revision。纯 currentFocus、integrity issue、迁移、lock recovery 或 projection ack 没有合法 task 目标时只写 workspace event，不伪造 task ID / record revision；
- workspace event 按 `committedRegistryRevision` 升序追加；首项 `previousEventSha256=null`，其余必须等于前一项摘要，`eventSha256` 对不含自身字段的规范化事件计算。链断裂、旧项被改写或 revision 倒退都使 registry 无效；
- registry 提交成功后，`evidence.lastTransition` 必须指向该 event 并保存 sha256；事件存在但 registry 未推进表示中断事务，恢复时按锁与 revision 核对后完成或报告冲突，不能重复执行外部动作；
- `workspace-overview.md` 标注 global registry revision；`development-overview.md`、`progress.md`、适用的 Project plan 和已生成的 `final-acceptance-report.md` 标注 task record revision。投影落后时从 registry、冻结授权、结构化计划和有序事件重建。

## 写回事务

每次任务或 SOP 状态变化按以下顺序执行：

1. 先收集真实外部结果、阻断事实或用户输入，但不在锁外创建、覆盖任何宿主规范 evidence；CLI 已按 `activeOperations` claim 产生的文件只作为待校验输入；
2. 原子取得 `.task-registry.lock`，在锁内重新读取注册表并确认全局与目标任务 revision 没有被其他宿主改变；
3. 校验输入、允许的生命周期迁移、计数和关系，再以 create-new 语义写规范轮次 evidence 与目标 revision 的不可变 lifecycle event；
4. 更新对应任务记录与必要的全局字段，增加 task / registry revision。对有 `projections` 的任务，把 `expectedRecordRevision` 设为新 task revision、保留旧 rendered 并标为 `PENDING`；`TOMBSTONE` 在取消、关系或其他合法元数据事务中始终保持 `projections=null`，只跳过它自己的 task Markdown 与 projection ack，不跳过 task event、record revision、registry 或跨任务 workspace event。随后原子替换 `task-registry.json`；跨任务或纯全局变化按 workspace event 规则一并提交；
5. 从该 registry revision 重建一次 `workspace-overview.md`，明确显示待刷新的 task projection；
6. 对本事务中所有有 `projections` 的受影响任务，渲染 `development-overview.md`、`progress.md`、适用的 Project plan 和已存在的 `final-acceptance-report.md`，写入 task record marker 并核对摘要；TOMBSTONE 不进入本步；
7. 为本事务中所有有 `projections` 的受影响任务追加一个 `PROJECTIONS_ACKNOWLEDGED` workspace event，以新的全局 registry revision 把各自 rendered 推进到 expected、标为 `CURRENT`，但不增加 task `recordRevision`；再从最终 revision 原子重建 `workspace-overview.md`。若受影响集合只有 TOMBSTONE，则没有 task projection ack，只从其 task event 已提交的 registry revision 重建总纲；
8. 所有投影写回完成且安全释放锁后，才能派遣下一任务、创建后续轮次或向用户宣称状态已推进。

注册表写入失败时报告 `TASK_REGISTRY_WRITE_FAILED`，状态迁移视为未完成。注册表已成功但 task Markdown 投影或 ack 前总纲失败时，注册表仍是生命周期权威且 task projection 保持 `PENDING`；报告 `TASK_REGISTRY_PROJECTION_FAILED` 并停止推进，下一次进入先重建或核对已提前落盘的 marker / 摘要，再提交 ack。ack 已提交、task projection 已是 `CURRENT`，但最终 `workspace-overview.md` 写入失败时，不把 task projection 伪装回 `PENDING`；同样报告投影失败，任何后续动作前由恢复步骤根据 registry revision marker 强制重建总纲。revision 被其他宿主改变时报告 `TASK_REGISTRY_CONFLICT`，重新读取后让用户确认焦点，不静默合并两个宿主的决策。

## 单写锁与并发恢复

`revision + 临时文件重命名` 只能防止半写文件，不能阻止两个宿主同时基于同一旧 revision 提交。所有宿主写入 event、registry 或其投影时必须共享 `.ai-dev-loop/.task-registry.lock`；不得用进程内互斥、对话记忆或“当前只有我在运行”代替。

1. 使用 create-new / `O_CREAT|O_EXCL` 语义原子创建锁，内容至少包含随机 `ownerToken`、宿主与会话标识、可核对的进程标识、`acquiredAt`、`expectedRegistryRevision` 和目标 task `expectedRecordRevision`；获取失败不得覆盖现有锁。
2. 持锁后重新读取 registry、目标目录和 evidence，确认锁内 revision 与预期相同；不同时报告 `TASK_REGISTRY_CONFLICT`，不写 event。
3. 在任务或 staging 的目标目录用临时文件加原子重命名写入不可变 event；若同名 event 已存在，只能在字节与摘要完全一致时视为中断续提，否则报告冲突。
4. 原子替换 domain registry 后按写回事务执行 `workspace-overview(PENDING) → task projections → projection ack → workspace-overview(CURRENT)`。每个投影写入自身 revision 标记，旧投影不能反向覆盖 registry。
5. 只有当前锁文件中的 `ownerToken` 仍匹配时才能释放；锁被替换、删除失败或所有权不明时停止并报告恢复要求，不能删除另一个宿主的锁。

长时间 CLI、开发 Agent 或 reviewer 调用不持续占用根级锁。调用前必须先按 `activeOperations` 规则提交 `ACTION_CLAIMED` event；释放锁调用运行时后，一取得 durable run/session handle 就重新取锁，以同一 `operationId` 提交 `ACTION_DISPATCH_CONFIRMED` 和回执摘要，再继续等待。调用完成后再次取锁、核对同一 operation 与输出摘要，提交结果 event。若宿主在 claim 与 dispatch receipt 之间崩溃，恢复只能按“确定性恢复”的运行时核对规则证明已启动或未启动；无法证明时阻断，不能重新执行一个可能已经产生写入的外部动作。

锁没有仅凭时间生效的自动过期。发现残留锁时，新宿主不能直接删除或覆盖；先证明 owner 进程/会话已经终止，读取并固定锁的文件身份、完整字节与摘要，核对 registry revision 和中断位置，再取得用户对该精确锁摘要的恢复授权。随后必须使用同文件系统的原子 compare-and-quarantine 原语把仍完全匹配的锁改名为 `.task-registry.lock.recovery-<old-token>`；普通的“先读后 rename”不够，环境没有该原语时保持阻断并交给操作者。quarantine 成功后立即用 `O_EXCL` 建立含新 token 与 `recoveredFrom` 摘要的新锁，并在新锁内复核 revision 与中断产物。

恢复提交必须保留已经被不可变 event 预留的 revision：若存在唯一、摘要有效且目标为当前 `N → N+1` 的中断 task lifecycle event，先确定性完成它原本描述的 registry 提交；不得先用 `LOCK_RECOVERED` 抢占 `N+1`。若 registry 已经包含对应 workspace event，则该 revision 已提交，只续提其未完成的投影，不能再次追加同一事件。原事务稳定后，再在下一个全局 revision 追加 `LOCK_RECOVERED` workspace event，引用 quarantine 摘要、中断 event 和恢复结果，然后按唯一下一动作续提。若中断 event 有多个解释、目标 revision 已被不同事务占用、或无法从 event/证据重建原提交，则保持阻断。任一文件身份、摘要、revision 或竞争变化都停止；恢复审计成功提交后才能清理 quarantine。

投影失败、校验失败等已知错误必须在 `finally` 中按 owner token 安全释放当前宿主仍拥有的锁；只有锁所有权或事务落点本身无法确定时才保留并进入 recovery。严禁只因 `acquiredAt` 很旧就破锁。活跃锁返回 `TASK_REGISTRY_LOCKED`；残留、损坏、所有权或中断事务无法确定时返回 `TASK_REGISTRY_LOCK_RECOVERY_REQUIRED`。
