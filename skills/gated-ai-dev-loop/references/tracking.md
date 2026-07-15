# 人工可读总览与进度

## 目录

- [文件职责](#文件职责)
- [development-overview.md 模板](#development-overviewmd-模板)
- [大型项目计划](#大型项目计划)
- [progress.md 模板](#progressmd-模板)
- [即时回写规则](#即时回写规则)
- [final-acceptance-report.md](#final-acceptance-reportmd)
- [必须更新时间点](#必须更新时间点)
- [人工验收入口](#人工验收入口)

## 文件职责

在任务目录根部维护三个宿主专用的人可读文件：

- `development-overview.md`：稳定的任务地图，供开发前确认、交接和人工验收快速浏览。
- `progress.md`：实时进度视图，展示当前阶段、任务状态、轮次证据、阻断项和下一步。
- `final-acceptance-report.md`：最新一次独立或人工语义验收的汇总入口；首次执行 `gated-loop accept` 后生成，每轮覆盖刷新。

Project 规模还必须维护 `rounds/planning/project-plan.md`，作为详细里程碑、工作流、任务依赖与集成计划。Capability 可把同类信息直接写入总览；内容较长时也可使用 `rounds/planning/capability-plan.md` 并从总览链接。

三者都不纳入冻结指纹。总览和进度不是开发授权或验收证据；最终验收汇总也不能替代轮次级结构化 JSON、真实 diff、测试和审查结果。发现冲突时以冻结基线和轮次原始证据为准。开发代理和审查代理不得修改这些文件。

## development-overview.md 模板

在基线归一化后、请求用户确认前创建。冻结前的范围、工作规模、变更类型或角色变化由宿主刷新，并在 `decision-log.md` 记录原因；冻结后的 `decision-log.md` 不得修改，后续变化写入 `progress.md` 时间线、当前轮次反馈证据和关联修订包的决策记录。

```markdown
# <task-id> 开发总览

## 基本信息
| 项目 | 内容 |
| --- | --- |
| 任务 | <task-id> — <title> |
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
- [验收清单](acceptance.json)
- [决策记录](decision-log.md)
- [大型项目详细计划](rounds/planning/project-plan.md)（Project）
- [当前轮次工作区授权](rounds/round-NN/workspace-authorization.json)（跨工作区时）
- [当前轮次工作区覆盖](rounds/round-NN/workspace-coverage.json)（跨工作区时）
- [轮次证据](rounds/)
```

## 大型项目计划

Project 规模在需求确认前创建 `rounds/planning/project-plan.md`，模板和质量门禁见 [project-planning.md](project-planning.md)。`development-overview.md` 只放稳定总纲和导航；详细依赖、输入输出、关键路径、阶段门禁和风险放在 project plan。两者都必须能让用户无需阅读 JSON 就理解：做什么、按什么顺序做、现在做到哪里、下一步由谁负责。

## progress.md 模板

初始化任务时创建，并在每次状态转换后、向用户交还控制权前更新。当前快照可以覆盖，时间线只能追加。

```markdown
# <task-id> 开发进度

## 当前状态
| 项目 | 内容 |
| --- | --- |
| 更新时间 | <ISO-8601 带时区> |
| 当前阶段 | <需求确认 / 方式选择 / 开发 / 门禁 / 语义验收 / 人工确认> |
| 门禁等级 | None / Light / Full |
| 工作规模 | N/A / Micro / Task / Capability / Project |
| 规模代表说明 | <固定代表说明；None 时不适用> |
| 当前任务说明 | <本任务规模的具体代表说明> |
| 变更类型 | N/A / Feature / Bugfix / Refactor / Migration / Maintenance / Docs / Test |
| 当前里程碑 / 工作流 | 不适用 / M-NNN / W-NNN |
| 状态 | <依据 progress、最终报告和最新轮次证据；state.json 仅显示冻结包阶段> |
| 当前轮次 | round-NN / 尚未开始 |
| 工作区覆盖 | 单工作区 / WAITING / PASS / BLOCKED |
| 任务进度 | <VERIFIED 数>/<总数>，不使用主观百分比 |
| SOP 进度 | <COMPLETED 数>/<适用步骤总数> |
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
- 人工验收：WAITING / ACCEPTED / REJECTED

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

1. 先确认对应证据已经落盘；
2. 更新该行状态、时间、责任方、证据和下一步；
3. 重算 VERIFIED/总数和 COMPLETED/适用 SOP 数，不填主观百分比；
4. 更新当前阶段、当前里程碑/工作流、阻断项和下一责任方；
5. 向时间线追加不可覆盖的事件；
6. 完成写回后才能派遣下一任务或向用户宣称该步骤完成。

开发 Agent 返回结果只能把 T 标为 `IMPLEMENTED`；机械门禁和语义验收证据满足后才能标为 `VERIFIED`。SOP 不适用时必须写 `SKIPPED` 和理由，不能删除该行。进度写回失败时停止推进并报告 `PROGRESS_WRITE_FAILED`。

## 必须更新时间点

在初始化、门禁等级/工作规模/变更类型判定、总纲或任务拆解完成、等待需求确认、完成冻结、等待工作区授权、工作区覆盖通过或阻断、等待开发方式或执行拓扑选择、自动派遣开始或结束、每个执行任务或并行 Agent 开始或结束、主动调用失败、手动交接和返回、结果集成、每次机械门禁、验收能力选择、独立或人工语义验收、修复轮次、等待人工确认、收到人工验收反馈、等待反馈分类、等待任务选择、等待修订确认、用户接受或拒绝、`NEED_HUMAN_REVIEW` 时更新 `progress.md`。这是一组最低更新时间点；任何 M/W/T/S 状态变化都必须按即时回写规则处理。

任务状态只能根据真实证据推进；开发者声明不能单独把任务标记为 `COMPLETED`。没有证据时保持 `PENDING`、`IN_PROGRESS` 或 `BLOCKED`。

## final-acceptance-report.md

该文件不由人工拼接。每次验收路由落盘后，宿主或 `gated-loop accept` 根据当前轮次结果覆盖刷新，至少包含：当前轮次、PASS/FAIL/NEED_HUMAN_REVIEW、机械门禁状态、验收路径、审查者与隔离方式、P0/P1/P2 数量和完整 findings、人工确认状态、修复指令，以及冻结授权、总览、进度和轮次证据链接。人工路径必须显示“尚未完成独立语义验收”。模板见 [acceptance.md](acceptance.md#final-acceptance-reportmd-模板)。

## 人工验收入口

进入 `WAITING_FOR_MANUAL_ACCEPTANCE` 或 `NEED_HUMAN_REVIEW` 时，先向用户展示任务根目录的 `final-acceptance-report.md`。用户需要追溯时，再展开 `development-overview.md`、`progress.md`、冻结基线、最新 `self-check-report.md`、`review-plan.json`、`acceptance-report.md`、`gate-evidence.json` 和 `review.json`。根级报告必须明确展示 P2 清单、独立验收是否真实完成及人工确认状态。用户接受后记录真实路径；人工语义验收不能被改写为独立 PASS。

用户提出修改或建议时，把状态更新为 `WAITING_FOR_FEEDBACK_CONFIRMATION`，在 `progress.md` 写明反馈摘要、建议分类、原任务处置、建议目标 task ID 和下一责任方。确认前不得创建新任务目录。确认后在反馈来源轮次写入 `manual-feedback.json`，再按 [post-acceptance-feedback.md](post-acceptance-feedback.md) 进入同任务修复、关联修订、建议处置或新任务。多个活动任务无法唯一选择时使用 `WAITING_FOR_TASK_SELECTION`。
