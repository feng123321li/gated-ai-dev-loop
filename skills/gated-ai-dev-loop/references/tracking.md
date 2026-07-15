# 人工可读总览与进度

## 目录

- [文件职责](#文件职责)
- [工作区总纲](#工作区总纲)
- [development-overview.md 模板](#development-overviewmd-模板)
- [大型项目计划](#大型项目计划)
- [progress.md 模板](#progressmd-模板)
- [即时回写规则](#即时回写规则)
- [final-acceptance-report.md](#final-acceptance-reportmd)
- [必须更新时间点](#必须更新时间点)
- [人工验收入口](#人工验收入口)

## 文件职责

协调工作区先按 [task-registry.md](task-registry.md) 定义的 schema 与总纲契约，并按 [registry-transactions.md](registry-transactions.md) 的写回事务维护两个根级文件：

- `.ai-dev-loop/task-registry.json`：完整机器索引、生命周期、当前焦点、周期和精确完成计数的规范记录；迁移必须引用真实 evidence。
- `.ai-dev-loop/workspace-overview.md`：由 registry revision 确定性重建、给人看的工作区任务总纲。

冻结完成后，每个任务目录根部继续维护三个宿主专用的人可读文件；冻结前同构内容先放在 `.ai-dev-loop/.host-staging/<task-id>/`，避免被 CLI `prepare/freeze` 替换：

- `development-overview.md`：稳定的任务地图，供开发前确认、交接和人工验收快速浏览。
- `progress.md`：实时进度视图，展示当前阶段、任务状态、轮次证据、阻断项和下一步。
- `final-acceptance-report.md`：最新一次独立或人工语义验收的汇总入口；首次执行 `gated-loop accept` 或等价验收路由后生成，人工语义审查和最终确认变化时由宿主继续重渲染。

Project 规模还必须维护 `rounds/planning/project-plan.md`，作为详细里程碑、工作流、任务依赖与集成计划。Capability 可把同类信息直接写入总览；内容较长时也可使用 `rounds/planning/capability-plan.md` 并从总览链接。

人工语义审查完成后，当前轮次还必须 create-new 保留 `rounds/round-NN/human-semantic-review.json`；它是用户审查结论证据，不是人可读投影，也不是当前 CLI 原生产物。

这些人可读文件都不纳入冻结指纹。任务总览和进度不是开发授权或验收证据；最终验收汇总也不能替代轮次级结构化 JSON、真实 diff、测试和审查结果。生命周期以注册表为规范记录，开发授权以冻结基线为准，迁移事实以轮次 evidence 为准；三者冲突时停止并报告，不能用 Markdown 反向覆盖。开发代理和审查代理不得修改这些文件。

## 工作区总纲

`workspace-overview.md` 必须显示当前焦点、各生命周期状态精确数量、全部活动/待用户/阻断/延期/待分类任务、最近终态任务、关系链、周期、M/W/T/SOP 完成计数、门禁结论、下一动作和证据入口。完整历史保留在 `task-registry.json`；总纲可以只展示最近 20 个终态任务，但不得截断任何非终态或异常任务，也不得作为恢复选择输入。

## development-overview.md 模板

在基线归一化后、请求用户确认前创建；冻结前写入 staging，冻结成功后物化到任务目录。staging 版本必须标明“冻结前投影”，根级链接使用 staging 可解析路径，尚不存在的最终 baseline/brief/plan 只显示目标文本；冻结后从同一结构化来源重新渲染本模板的最终相对链接并核对摘要，不能原样复制一组失效链接。冻结前的范围、工作规模、变更类型或角色变化由宿主写入 lifecycle decision event 与明确 baseline 来源，重新渲染 staging 投影，并在 CLI 已 prepare 时重新执行 prepare；不得直接编辑或依赖可能被替换的 CLI `decision-log.md`。冻结后的 `decision-log.md` 不得修改，后续变化写入 `progress.md` 时间线、当前轮次反馈证据和关联修订包的决策记录。

```markdown
# <task-id> 开发总览

## 基本信息
| 项目 | 内容 |
| --- | --- |
| 任务 | <task-id> — <title> |
| task record revision | <recordRevision> |
| 门禁等级 | Full / Light |
| 工作规模 | Micro / Task / Capability / Project |
| 规模代表说明 | <固定代表说明> |
| 当前任务说明 | <本任务为何属于该规模的具体一句话> |
| 变更类型 | Feature / Bugfix / Refactor / Migration / Maintenance / Docs / Test |
| 宿主 | <agent-id> |
| 开发方式 | 待选择 / active / manual |
| 开发 Agent | 待派遣 / 用户手动选择 / <agent-id> |
| 执行拓扑 | 待选择 / single / parallel |
| 工作区覆盖 | 单工作区 / 待授权 / PASS / BLOCKED |
| 权威基线 | [baseline.md](baseline.md) 或 [light-brief.md](light-brief.md) |
| 工作区任务总纲 | [workspace-overview.md](../workspace-overview.md) |
| 生命周期记录 | [task-registry.json](../task-registry.json) 中的当前 task 条目 |
| 实时进度 | [progress.md](progress.md) |
| 详细计划 | 普通任务不适用 / [project-plan.md](rounds/planning/project-plan.md) |

## 目标与边界
- 目标：<一段可观察结果>
- 范围：<路径或行为摘要>
- 非目标：<明确排除项>

## 追踪索引
| 需求 | 验收 | 任务 | 摘要 |
| --- | --- | --- | --- |
| R-001 | A-001 | T-001 | <简要说明> |

## 里程碑与工作流（Capability / Project）
| 里程碑 | 工作流 | 可观察结果 | 任务 | 依赖 | 完成门禁 |
| --- | --- | --- | --- | --- | --- |
| M-001 | W-001 | <阶段结果> | T-001、T-002 | 无 | A-001、<测试> |

## 任务拆解摘要
| 任务 | M / W | R / A | 工作区与允许路径 | dependsOn | 输出 | 完成定义 |
| --- | --- | --- | --- | --- | --- | --- |
| T-001 | M-001 / W-001 | R-001 / A-001 | service-a: src/** | 无 | <产物> | <测试与观察结果> |

## 开发与验收安排
- 开发者：<自动派遣规则、手动交接或实际 Agent 标识>
- 工作区：<单工作区摘要；或 workspace-authorization.json、workspace-coverage.json 与依赖波次>
- 机械门禁：<测试 argv 和范围检查摘要>
- 语义验收：其他全新只读 Agent → 同宿主全新只读子 Agent → 人工语义验收；前两者均无开发上下文，第三种不得声称独立 PASS
- 最终确认：用户人工验收

## 风险与关键决策
- <风险或 decision-log.md 链接>

## 产物导航
- [任务清单](tasks.json)
- [工作区任务注册表](../task-registry.json)
- [工作区任务总纲](../workspace-overview.md)
- [验收清单](acceptance.json)
- [决策记录](decision-log.md)
- [大型项目详细计划](rounds/planning/project-plan.md)（Project）
- [当前轮次工作区授权](rounds/round-NN/workspace-authorization.json)（跨工作区时）
- [当前轮次工作区覆盖](rounds/round-NN/workspace-coverage.json)（跨工作区时）
- [轮次证据](rounds/)
```

## 大型项目计划

Project 规模在需求确认前创建最终位于 `rounds/planning/project-plan.md` 的计划；冻结前先写入 staging 的同构路径，模板和质量门禁见 [project-planning.md](project-planning.md)。`development-overview.md` 只放稳定总纲和导航；详细依赖、输入输出、关键路径、阶段门禁和风险放在 project plan。两者都必须能让用户无需阅读 JSON 就理解：做什么、按什么顺序做、现在做到哪里、下一步由谁负责。

## progress.md 模板

初始化任务时在 staging 创建，冻结成功后物化；此后在每次状态转换后、向用户交还控制权前更新。当前快照可以覆盖，时间线只能追加。

```markdown
# <task-id> 开发进度

## 当前状态
| 项目 | 内容 |
| --- | --- |
| 更新时间 | <ISO-8601 带时区> |
| task record revision | <recordRevision> |
| 门禁等级 | None / Light / Full |
| 工作规模 | N/A / Micro / Task / Capability / Project |
| 规模代表说明 | <固定代表说明；None 时不适用> |
| 当前任务说明 | <本任务规模的具体代表说明> |
| 变更类型 | N/A / Feature / Bugfix / Refactor / Migration / Maintenance / Docs / Test |
| 当前里程碑 / 工作流 | 不适用 / M-NNN / W-NNN |
| 生命周期状态 | <task-registry.json 投影：ACTIVE / WAITING_USER / BLOCKED / DEFERRED / TERMINAL / UNKNOWN> |
| 当前阶段 | <task-registry.json 的 phase 投影；state.json 仅显示冻结包阶段> |
| 当前轮次 | round-NN / 尚未开始 |
| 工作区覆盖 | 单工作区 / WAITING / PASS / BLOCKED |
| 任务进度 | <IMPLEMENTED 数> implemented，<VERIFIED 数>/<总数> verified，不使用主观百分比 |
| SOP 进度 | <COMPLETED 数>/<适用步骤总数>，另列 skipped 数与全部步骤 total |
| 周期 | <createdAt / packageFrozenAt / developmentStartedAt / lastActivityAt / completedAt / 已落盘轮次数> |
| 人工语义审查 | NOT_RUN / PASS / FAIL |
| 最终用户确认 | NOT_READY / WAITING / ACCEPTED / REJECTED |
| 活跃开发 Agent | <agent-id 列表或无> |
| 下一责任方 | 用户 / 宿主 Agent / developer Agent / reviewer Agent |

## 里程碑与工作流进度（Capability / Project）
| ID | 类型 | 状态 | 已验证任务 / 总任务 | 当前阻断 | 证据 |
| --- | --- | --- | --- | --- | --- |
| M-001 | MILESTONE | PENDING / IN_PROGRESS / VERIFIED / BLOCKED / DEFERRED | 0/2 | 无 | <链接> |
| W-001 | WORKSTREAM | PENDING / IN_PROGRESS / VERIFIED / BLOCKED / DEFERRED | 0/2 | 无 | <链接> |

## 执行任务进度
| 任务 | M / W | 波次 / Agent | 状态 | 更新时间 | 证据 | 下一步 / 阻断 |
| --- | --- | --- | --- | --- | --- | --- |
| T-001 | M-001 / W-001 | wave-1 / agent-01 | PENDING / IN_PROGRESS / IMPLEMENTED / VERIFIED / BLOCKED / DEFERRED | <ISO-8601> | <相对链接> | <事实> |

## SOP 进度
| SOP | 步骤 | 状态 | 更新时间 | 责任方 | 证据 / 说明 |
| --- | --- | --- | --- | --- | --- |
| S-001 | 恢复已有任务与路由 | PENDING / IN_PROGRESS / COMPLETED / BLOCKED / SKIPPED | <ISO-8601> | 宿主 | <链接或事实> |
| S-002 | 需求分析与三维路由 | PENDING | <ISO-8601> | 宿主 | <门禁等级、工作规模、变更类型及代表说明> |
| S-003 | 总纲、任务拆解与基线起草 | PENDING | <ISO-8601> | 宿主 | <链接> |
| S-004 | 用户确认与冻结 | PENDING | <ISO-8601> | 用户 / 宿主 | <链接> |
| S-005 | 工作区授权与覆盖 | PENDING | <ISO-8601> | 用户 / 宿主 | <链接或不适用> |
| S-006 | 开发方式与执行拓扑确认 | PENDING | <ISO-8601> | 用户 / 宿主 | <选择> |
| S-007 | 开发快照、交接与派遣 | PENDING | <ISO-8601> | 宿主 | <链接> |
| S-008 | 实现、结果回收与集成 | PENDING | <ISO-8601> | developer / 宿主 | <链接> |
| S-009 | 机械门禁 | PENDING | <ISO-8601> | 宿主 | <链接> |
| S-010 | 独立或人工语义验收 | PENDING | <ISO-8601> | reviewer / 用户 | <链接> |
| S-011 | 人工最终确认与反馈分流 | PENDING | <ISO-8601> | 用户 / 宿主 | <链接> |

## 工作区状态
| 工作区 | 绝对根路径 | 任务 | 授权 | 快照 / 门禁 | 阻断 |
| --- | --- | --- | --- | --- | --- |
| service-a | <absolute-root> | T-001 | CONFIRMED / MISSING | <相对链接或尚未生成> | 无 / <解除条件> |

## 最新门禁与验收
- 开发结果：<尚未产生或 result.json 链接>
- 机械门禁：<尚未运行或 self-check-report.md、gate-evidence.json 链接与结论>
- 语义验收：<尚未运行或 review-plan.json、acceptance-report.md、review.json 链接、路径、结论与 P0/P1/P2 数量>
- 人工语义审查：NOT_RUN / PASS / FAIL；完成时链接 human-semantic-review.json
- 最终用户确认：NOT_READY / WAITING / ACCEPTED / REJECTED

## 当前阻断项
- 无；或列出关联 R/A/T ID、责任方和解除条件。

## 下一步
- <一个明确动作，以及完成后由谁返回哪里>

## 时间线
| 时间 | 从 | 到 | 事件 | 证据 |
| --- | --- | --- | --- | --- |
| <ISO-8601> | INIT | WAITING_FOR_REQUIREMENT_CONFIRMATION | 已生成基线 | [baseline.md](baseline.md) |
```

## 即时回写规则

每个 M/W/T 或 `S-NNN` 开始、完成、阻断、跳过或延期后立即回写，不等整轮结束：

1. 先收集真实外部结果或用户输入；宿主不得在锁外创建或覆盖规范 evidence；
2. 原子取得 `.task-registry.lock`，在锁内重读并校验全局 registry revision 与 task record revision，再以 create-new 写规范 evidence 和目标 record revision 的 lifecycle event；
3. 原子写入 `task-registry.json`，把 expected 推进、rendered 保持旧值并标为 `PENDING`，随后重建一次 `workspace-overview.md`；
4. 更新任务内对应行的状态、时间、责任方、证据和下一步，并写 task record revision marker；
5. 重算 VERIFIED/总数和 COMPLETED/适用 SOP 数，不填主观百分比；
6. 更新当前阶段、当前里程碑/工作流、阻断项和下一责任方，并向时间线追加不可覆盖的事件；
7. 核对全部 task projection 摘要后，以不增加 task record revision 的 workspace event 提交 projection ack，标为 `CURRENT` 并重建最终 `workspace-overview.md`；
8. 安全释放锁后，才能派遣下一任务或向用户宣称该步骤完成。

开发 Agent 返回结果只能把 T 标为 `IMPLEMENTED`；机械门禁和语义验收证据满足后才能标为 `VERIFIED`。SOP 不适用时必须写 `SKIPPED` 和理由，不能删除该行。注册表写入失败时报告 `TASK_REGISTRY_WRITE_FAILED`；注册表已成功但人可读投影失败时报告 `TASK_REGISTRY_PROJECTION_FAILED`。两种情况都停止推进，不能口头宣称完成。

## 必须更新时间点

在初始化、门禁等级/工作规模/变更类型判定、总纲或任务拆解完成、等待需求确认、完成冻结、等待工作区授权、工作区覆盖通过或阻断、等待开发方式或执行拓扑选择、自动派遣开始或结束、每个执行任务或并行 Agent 开始或结束、主动调用失败、手动交接和返回、结果集成、每次机械门禁、验收能力选择、独立或人工语义验收、修复轮次、等待人工确认、收到人工验收反馈、等待反馈分类、等待任务选择、等待修订确认、用户接受或拒绝、`NEED_HUMAN_REVIEW` 时更新 `progress.md`。这是一组最低更新时间点；任何 M/W/T/S 状态变化都必须按即时回写规则处理。

任务状态只能根据真实证据推进；开发者声明中的 `COMPLETED` 只是一次实现调用结束，不能把生命周期标为终态。只有全部适用门禁有证据且用户最终接受后，注册表才能写 `TERMINAL / COMPLETED`。没有证据时保持原状态或进入 `UNKNOWN/BLOCKED`。

## final-acceptance-report.md

该文件不由人工拼接。`gated-loop accept` 或等价验收路由先根据机械证据和 `review.json` 生成汇总；人工语义审查或最终确认变化后，宿主再根据 `human-semantic-review.json`、注册表状态和对应 lifecycle event 确定性重渲染。报告至少包含：当前轮次、PASS/FAIL/NEED_HUMAN_REVIEW、机械门禁状态、验收路径、审查者与隔离方式、P0/P1/P2 数量和完整 findings、人工语义审查结果、最终用户确认状态、修复指令，以及冻结授权、总览、进度和轮次证据链接。人工路径必须显示“尚未完成独立语义验收”。模板见 [acceptance.md](acceptance.md#final-acceptance-reportmd-模板)。

## 人工验收入口

进入 `WAITING_FOR_MANUAL_ACCEPTANCE` 或 `NEED_HUMAN_REVIEW` 时，先向用户展示任务根目录的 `final-acceptance-report.md`。用户需要追溯时，再展开 `development-overview.md`、`progress.md`、冻结基线、最新 `self-check-report.md`、`review-plan.json`、`acceptance-report.md`、`gate-evidence.json`、`review.json`，以及人工路径已完成时的 `human-semantic-review.json` 和对应 lifecycle event。任务根级报告必须明确展示 P2 清单、独立验收是否真实完成、人工语义审查及最终确认状态。用户接受后记录真实路径；人工语义验收不能被改写为独立 PASS。

用户提出修改或建议时，非终态原任务可转为 `WAITING_USER / WAITING_FOR_FEEDBACK_CONFIRMATION`；终态任务保持原 `TERMINAL/*`，只把 `currentFocus.purpose` 设为 `FEEDBACK_CONTEXT`。随后在 `progress.md` 写明反馈摘要、建议分类、原任务处置、建议目标 task ID 和下一责任方。确认前不得创建或登记新任务。确认后在反馈来源轮次写入 `manual-feedback.json`，再按 [post-acceptance-feedback.md](post-acceptance-feedback.md) 进入同任务修复、关联修订、建议处置或新任务。多个合格候选无法唯一选择时使用 `WAITING_FOR_TASK_SELECTION`。
