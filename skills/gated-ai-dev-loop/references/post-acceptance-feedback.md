# 后续对话恢复与人工验收反馈

## 目录

- [入口不变量](#入口不变量)
- [恢复已有任务](#恢复已有任务)
- [反馈分类](#反馈分类)
- [确认协议](#确认协议)
- [同授权修复](#同授权修复)
- [修订当前任务](#修订当前任务)
- [建议与 P2](#建议与-p2)
- [全新任务](#全新任务)
- [落盘证据](#落盘证据)
- [典型判断](#典型判断)

## 入口不变量

人工验收后的用户消息仍属于门禁流程。上下文压缩、新会话、宿主变化或用户没有再次写出 Skill 名称，都不能成为忽略磁盘任务状态的理由。

每次处理开发类消息时遵守以下顺序：

1. 按 [task-registry.md](task-registry.md) 校验根级 `task-registry.json`，并按 [registry-lifecycle.md](registry-lifecycle.md#一致性并发与错误) 对任务目录做单层一致性核对；
2. 按显式 ID / 路径、当前焦点、唯一合格候选的顺序恢复任务和最新轮次；
3. 判断消息是接受、修复、修订、建议还是新任务；
4. 展示分类、原任务处置、目标 task ID、业务工作区和下一步；
5. 等待用户明确确认；
6. 确认后才写 `manual-feedback.json` 并创建轮次或任务包。

不得先调用 `gated-loop start` 再解释关系。不得因为用户换了一种描述就生成新 task ID。不得删除、移动、覆盖或“清理”旧任务目录、冻结 baseline、handoff、报告或轮次证据。

## 恢复已有任务

按以下优先级选择任务：

1. 用户明确给出的精确 task ID 或任务目录路径；
2. 注册表中证据完整的 `PROVISIONAL + creationContext` 当前焦点，或 `HEALTHY + FINALIZING_TASK_CREATION + creationContext` 当前焦点；先续提创建/关系/来源处置/最终焦点收尾，完成前不重新分类本条反馈；
3. 注册表中仍存在、`HEALTHY + creationContext=null` 的普通 `currentFocus`；非终态按原阶段恢复，终态 `FEEDBACK_CONTEXT` 只用于本节反馈分类；
4. 注册表中唯一的 `ACTIVE` 或 `WAITING_USER` 任务；
5. 多个合格任务时，由用户选择。

显式目标健康时可以覆盖失效旧焦点；无关 `UNKNOWN` 或历史目录异常只作为告警。没有显式目标时，失效焦点或待分类任务优先进入 `NEED_RESUME_CLASSIFICATION`，不得自动改选另一个活动任务。候选 ID 的精确集合按 [task-registry.md](task-registry.md#确定性恢复) 保存。

对普通健康候选至少读取：

- 注册表中的生命周期、`phase`、`nextAction`、周期、完成计数和 evidence；
- `state.json` 与冻结 `baseline.md` 或 `light-brief.md`；
- `development-overview.md` 和 `progress.md`；
- `final-acceptance-report.md`（如果存在）；
- 最新 `rounds/round-NN/` 中的 result、gate、review、acceptance 和反馈证据。

合法 `PROVISIONAL` 尚无可依赖的任务目录，只读取 registry、`creationContext`、`.host-staging/<task-id>/` 中的创建 event、结构化投影来源和 active operation evidence，再按 `nextAction` 续提；不得要求 `state.json` 或冻结 baseline。`HEALTHY + FINALIZING_TASK_CREATION + creationContext` 读取已物化任务包、关系/来源处置 evidence 和收尾 nextAction，但仍禁止开发。creationContext 与其他 integrity/phase 组合同时出现时进入 `NEED_RESUME_CLASSIFICATION`。

除上述已聚焦的合法创建中间态外，只有普通 `ACTIVE` 和 `WAITING_USER` 参与唯一候选自动续接。`BLOCKED`、`DEFERRED`、`TERMINAL`、`UNKNOWN` 和完整性异常必须展示但不自动选择；显式选择后分别恢复阻断、请求恢复授权、展示终态或进入 `NEED_RESUME_CLASSIFICATION`。禁止按目录名、修改时间、标题相似度或当前对话猜测。注册表缺失时按兼容迁移规则登记全部直接子目录，证据不足的任务写 `UNKNOWN`，不得猜测。

冻结副本与仓库来源文件职责不同：来源文件用于准备和冻结，任务目录中的冻结副本用于后续恢复。来源文件缺失时报告来源漂移，但不得据此宣称冻结任务不存在，也不得拿另一个 baseline 代替它。

## 反馈分类

| 路由 | 判断标准 | 原任务 | 下一步 |
| --- | --- | --- | --- |
| `ACCEPT_CURRENT` | 用户接受当前结果 | 标记人工接受 | 完成，不自动发布 |
| `REPAIR_CURRENT` | 反馈已经被当前冻结 R/A/T 或 P0/P1 finding 要求 | 保持同一 task 和冻结授权 | 创建下一修复轮次 |
| `REVISE_CURRENT` | 改变 Goal、Scope、Non-Goals、R/A/T、允许工作区或对外承诺 | 保留为上一版本 | 创建显式 `REVISION_OF` 修订包并重新确认 |
| `SUGGESTION` | 非阻断改进、P2 或可选想法 | 由用户决定是否接受当前结果 | defer、dismiss、revision 或 follow-up |
| `NEW_TASK` | 与当前冻结目标独立的产品目标 | 必须明确接受、保留待处理或放弃 | 创建显式关联或独立任务 |

如果一条反馈同时包含多类事项，拆成编号项逐项分类。只要任一项改变冻结授权，就不能把整条反馈伪装成 repair。

## 确认协议

宿主先展示以下内容，不执行写入性开发动作：

```text
当前任务：<task-id> / <round> / <status>
反馈分类：REPAIR_CURRENT / REVISE_CURRENT / SUGGESTION / NEW_TASK
关联依据：R/A/T/finding ID，或“超出当前冻结授权”
原任务处置：继续 / 接受 / 保持待确认 / 放弃
目标任务包：沿用 <task-id>，或建议 <new-task-id>
业务工作区：沿用哪些目录；是否需要新增授权
确认后动作：新修复轮次 / 重新冻结 / 记录建议 / 新任务
```

只有用户明确确认该分类和处置后才能继续。用户只说“改一下”“可以优化”“再加一个”不构成新 task、修订基线或实现 P2 的授权。

## 同授权修复

`REPAIR_CURRENT` 必须：

- 沿用原 task ID、冻结指纹、R/A/T、协调目录和已授权业务工作区；
- 在原任务的 `rounds/round-NN/` 创建下一轮，不创建另一个顶层任务目录；
- 反馈关联到现有 R/A/T 或 P0/P1 finding；没有关联依据时改判 `REVISE_CURRENT`；
- 允许新的隔离开发 Agent 修改上一轮已证明属于本任务的文件，但仍保护开发前无关脏改动；
- 对从最初开发快照到当前结果的聚合 diff 重新执行全部机械和语义验收。

确认修复后先取得根级单写锁，以 create-new 写反馈 evidence 与 lifecycle event，再保持同一 task ID、增加 repair round、更新周期、阶段和下一动作，最后按完整 projection 事务刷新工作区总纲与任务进度。若任务已经是终态，确认分类期间保持终态；只有用户额外确认 `REOPEN_CURRENT` 后，才按 [终态迁移矩阵](registry-lifecycle.md#修订后续任务与终态) 保存原终态历史、清空当前终态字段，把本轮机械/语义门禁重置为 `NOT_RUN`、`semanticReviewRoute` 重置为 `NOT_SELECTED`、`needHumanReviewReason` 清空为 `null`、`humanSemanticReviewOutcome` 重置为 `NOT_RUN`、`manualConfirmation` 重置为 `NOT_READY`，并进入 `ACTIVE / PREPARING_REPAIR_ROUND`。

如果当前 CLI 无法证明跨轮未提交改动归属，返回 `NEED_HUMAN_REVIEW` 并请求人工确认归属；不得用创建新任务绕过。

## 修订当前任务

冻结授权不可原地修改。`REVISE_CURRENT` 使用新的任务包保存新版本，但它仍属于原任务链：

- 保留原 `.ai-dev-loop/<task-id>/` 完整不变；
- 建议新 ID 为 `<task-id>-r02`、`<task-id>-r03`，并记录 `REVISION_OF`、来源 round 和 `manual-feedback.json` 路径；
- 默认沿用原业务工作区，不自动创建新的 Git worktree；范围新增目录或微服务时重新执行工作区授权和覆盖门禁；
- 新基线必须完整重新展示、确认和冻结，不能只提交一个增量说明；
- 修订包冻结前，非终态原任务在注册表中保持 `WAITING_USER / WAITING_FOR_REVISION_CONFIRMATION`，终态原任务保持原 disposition 不变；用户确认新 task ID 后先在 `.host-staging/<task-id>/` 写创建 event，以包含来源 task、计划关系、旧焦点和最终焦点策略的 `creationContext` 登记 `PROVISIONAL`，并把 currentFocus 临时指向新记录作为恢复指针。CLI 冻结成功、投影物化且新记录改为 `HEALTHY` 后，才登记 `REVISION_OF / REVISED_BY` 双向关系，必要时先保存原终态历史，再把原任务置为 `TERMINAL / SUPERSEDED`，清空 creationContext 并确认最终焦点；
- 原 baseline 继续作为上一版本权威，不得重命名成新任务文件，也不得因新修订存在而删除。

这里的新“任务包”是冻结授权的版本容器，不等于另建业务代码工作区。只有用户另行授权时才创建新 Git worktree。

## 建议与 P2

逐项让用户选择：

- `DEFER`：记录待办；当前结果仍可接受；
- `DISMISS`：记录不采纳及理由；
- `IMPLEMENT_AS_REVISION`：转为 `REVISE_CURRENT`，重新确认授权；
- `CREATE_FOLLOW_UP`：创建 `FOLLOW_UP_OF` 任务，并明确当前任务处置。

P2 永不自动进入修复轮次。用户说“这些建议不错”仍需确认是记录、现在实现还是另开后续任务。

## 全新任务

创建 `NEW_TASK` 前必须同时确认：

- 原任务：`ACCEPT_CURRENT`、`KEEP_PENDING` 或 `ABANDON_CURRENT`；
- 新 task ID；
- 关系：`FOLLOW_UP_OF`、`RELATED_TO` 或 `INDEPENDENT`；
- 是否沿用业务工作区；若工作区已有其他任务未提交改动，如何隔离和保护。

未确认前不得调用 `start` 或在注册表预登记目标任务。新任务准备后不得清理原任务目录。共享同一脏工作区且无法证明改动归属或路径互斥时，先请求隔离 worktree 或返回 `NEED_HUMAN_REVIEW`。

## 落盘证据

在反馈来源轮次写入确认后的 `manual-feedback.json`：

```json
{
  "schemaVersion": 1,
  "task": "order-query-full",
  "sourceRound": "round-01",
  "route": "REVISE_CURRENT",
  "summary": "调整查询目标与验收条件",
  "relatedIds": ["R-002", "A-003"],
  "currentTaskDisposition": "WAITING_FOR_REVISION_CONFIRMATION",
  "targetTask": "order-query-full-r02",
  "relation": "REVISION_OF",
  "confirmedBy": "user",
  "confirmedAt": "2026-07-15T17:30:00+08:00"
}
```

确认前，非终态原任务可更新为 `WAITING_USER / WAITING_FOR_FEEDBACK_CONFIRMATION` 并在 `progress.md` 记录反馈摘要和建议分类；终态任务必须保持原 `TERMINAL/*`，只由 `currentFocus.purpose=FEEDBACK_CONTEXT` 锚定分类，不得提前重开。不创建或登记目标任务。确认后的 JSON 不再覆盖；后续变化新增一条反馈证据。

业务顺序固定为：锁内 create-new 写 `manual-feedback.json` 与反馈 lifecycle event → staging 中的 `TASK_CREATION_APPROVED` event（如适用）→ 带 creationContext 和临时恢复焦点的 `PROVISIONAL` 新记录 → `ACTION_CLAIMED` 后在锁外调用 CLI 创建/冻结 → 锁内校验并物化 staging、改为 `HEALTHY` → 注册表关系、原任务处置、清空 creationContext 与最终焦点。每个会改变 task record 的箭头都必须先独立完成“event → registry(PENDING) → `workspace-overview.md(PENDING)` → 有关任务的 `progress.md` / 总览 → projection ack → `workspace-overview.md(CURRENT)`”，再进入下一箭头；不能把中间多次状态变化拖到最后一次性补投影。每段控制面写回都使用根级单写锁，长时间外部调用不持锁但必须有 active operation claim。`state.json` 是冻结信封，不能为了记录反馈而修改。用户接受映射为 `TERMINAL / COMPLETED`，明确放弃映射为 `TERMINAL / ABANDONED`；普通 follow-up 或 related 关系不会自动终结原任务。

## 典型判断

- “接口字段漏了，原 A-004 已明确要求”：`REPAIR_CURRENT`。
- “接口完成了，但查询目标改成另一个业务对象”：`REVISE_CURRENT`。
- “P2 的缓存建议这次先不做”：`SUGGESTION / DEFER`，随后可接受当前任务。
- “当前查询接口验收通过，再做一套库存盘点功能”：先 `ACCEPT_CURRENT`，再创建 `FOLLOW_UP_OF` 或 `INDEPENDENT` 新任务；不得把新 baseline 当成旧任务重命名后的文件。
- “原 baseline 来源文件找不到了”：从任务目录冻结副本恢复并报告来源漂移；不得自动创建 task。
