# 人工可读总览与进度

## 目录

- [文件职责](#文件职责)
- [development-overview.md 模板](#development-overviewmd-模板)
- [progress.md 模板](#progressmd-模板)
- [final-acceptance-report.md](#final-acceptance-reportmd)
- [必须更新时间点](#必须更新时间点)
- [人工验收入口](#人工验收入口)

## 文件职责

在任务目录根部维护三个宿主专用的人可读文件：

- `development-overview.md`：稳定的任务地图，供开发前确认、交接和人工验收快速浏览。
- `progress.md`：实时进度视图，展示当前阶段、任务状态、轮次证据、阻断项和下一步。
- `final-acceptance-report.md`：最新一次独立或人工语义验收的汇总入口；首次执行 `gated-loop accept` 后生成，每轮覆盖刷新。

三者都不纳入冻结指纹。总览和进度不是开发授权或验收证据；最终验收汇总也不能替代轮次级结构化 JSON、真实 diff、测试和审查结果。发现冲突时以冻结基线和轮次原始证据为准。开发代理和审查代理不得修改这些文件。

## development-overview.md 模板

在基线归一化后、请求用户确认前创建。只有用户批准范围、模式或角色发生变化时才由宿主刷新，并在 `decision-log.md` 记录原因。

```markdown
# <task-id> 开发总览

## 基本信息
| 项目 | 内容 |
| --- | --- |
| 任务 | <task-id> — <title> |
| 任务模式 | Full / Light |
| 宿主 | <agent-id> |
| 开发方式 | 待选择 / active / manual |
| 开发 Agent | 待派遣 / 用户手动选择 / <agent-id> |
| 执行拓扑 | 待选择 / single / parallel |
| 工作区覆盖 | 单工作区 / 待授权 / PASS / BLOCKED |
| 权威基线 | [baseline.md](baseline.md) 或 [light-brief.md](light-brief.md) |
| 实时进度 | [progress.md](progress.md) |

## 目标与边界
- 目标：<一段可观察结果>
- 范围：<路径或行为摘要>
- 非目标：<明确排除项>

## 追踪索引
| 需求 | 验收 | 任务 | 摘要 |
| --- | --- | --- | --- |
| R-001 | A-001 | T-001 | <简要说明> |

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
- [当前轮次工作区授权](rounds/round-NN/workspace-authorization.json)（跨工作区时）
- [当前轮次工作区覆盖](rounds/round-NN/workspace-coverage.json)（跨工作区时）
- [轮次证据](rounds/)
```

## progress.md 模板

初始化任务时创建，并在每次状态转换后、向用户交还控制权前更新。当前快照可以覆盖，时间线只能追加。

```markdown
# <task-id> 开发进度

## 当前状态
| 项目 | 内容 |
| --- | --- |
| 更新时间 | <ISO-8601 带时区> |
| 当前阶段 | <需求确认 / 方式选择 / 开发 / 门禁 / 语义验收 / 人工确认> |
| 状态 | <与 state.json 一致> |
| 当前轮次 | round-NN / 尚未开始 |
| 工作区覆盖 | 单工作区 / WAITING / PASS / BLOCKED |
| 任务进度 | <已完成数>/<总数>，不使用主观百分比 |
| 活跃开发 Agent | <agent-id 列表或无> |
| 下一责任方 | 用户 / 宿主 Agent / developer Agent / reviewer Agent |

## 任务进度
| 任务 | 波次 / Agent | 状态 | 证据 | 说明 |
| --- | --- | --- | --- | --- |
| T-001 | wave-1 / agent-01 | PENDING / IN_PROGRESS / BLOCKED / COMPLETED | <相对链接> | <事实> |

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

## 必须更新时间点

在初始化、等待需求确认、完成冻结、等待工作区授权、工作区覆盖通过或阻断、等待开发方式或执行拓扑选择、自动派遣开始或结束、每个并行 Agent 开始或结束、主动调用失败、手动交接和返回、结果集成、每次机械门禁、验收能力选择、独立或人工语义验收、修复轮次、等待人工确认、用户接受或拒绝、`NEED_HUMAN_REVIEW` 时更新 `progress.md`。

任务状态只能根据真实证据推进；开发者声明不能单独把任务标记为 `COMPLETED`。没有证据时保持 `PENDING`、`IN_PROGRESS` 或 `BLOCKED`。

## final-acceptance-report.md

该文件不由人工拼接。每次验收路由落盘后，宿主或 `gated-loop accept` 根据当前轮次结果覆盖刷新，至少包含：当前轮次、PASS/FAIL/NEED_HUMAN_REVIEW、机械门禁状态、验收路径、审查者与隔离方式、P0/P1/P2 数量和完整 findings、人工确认状态、修复指令，以及冻结授权、总览、进度和轮次证据链接。人工路径必须显示“尚未完成独立语义验收”。模板见 [acceptance.md](acceptance.md#final-acceptance-reportmd-模板)。

## 人工验收入口

进入 `WAITING_FOR_MANUAL_ACCEPTANCE` 或 `NEED_HUMAN_REVIEW` 时，先向用户展示任务根目录的 `final-acceptance-report.md`。用户需要追溯时，再展开 `development-overview.md`、`progress.md`、冻结基线、最新 `self-check-report.md`、`review-plan.json`、`acceptance-report.md`、`gate-evidence.json` 和 `review.json`。根级报告必须明确展示 P2 清单、独立验收是否真实完成及人工确认状态。用户接受后记录真实路径；人工语义验收不能被改写为独立 PASS。用户拒绝时记录关联 finding 或验收 ID 和原因，再进入修复轮次。
