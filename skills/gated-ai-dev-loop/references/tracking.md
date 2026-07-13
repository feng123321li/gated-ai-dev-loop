# 人工可读总览与进度

## 目录

- [文件职责](#文件职责)
- [development-overview.md 模板](#development-overviewmd-模板)
- [progress.md 模板](#progressmd-模板)
- [必须更新时间点](#必须更新时间点)
- [人工验收入口](#人工验收入口)

## 文件职责

在任务目录根部维护两个宿主专用文件：

- `development-overview.md`：稳定的任务地图，供开发前确认、交接和人工验收快速浏览。
- `progress.md`：实时进度视图，展示当前阶段、任务状态、轮次证据、阻断项和下一步。

两者都不是开发授权或验收证据，不纳入冻结指纹。冻结基线、结构化 JSON、真实 diff、测试和审查结果始终具有更高权威。发现冲突时先修正视图，再继续流程。开发代理和审查代理不得修改这两个文件。

## development-overview.md 模板

在基线归一化后、请求用户确认前创建。只有用户批准范围、模式或角色发生变化时才由宿主刷新，并在 `decision-log.md` 记录原因。

```markdown
# <task-id> 开发总览

## 基本信息
| 项目 | 内容 |
| --- | --- |
| 任务 | <task-id> — <title> |
| 任务模式 | Full / Light |
| 宿主 | Codex / Claude |
| 开发方式 | 待选择 / active / manual |
| 开发运行时 | 待选择 / Codex / Claude |
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
- 开发者：<选择规则或已选运行时>
- 机械门禁：<测试 argv 和范围检查摘要>
- 独立审查：优先全新只读 Codex，否则全新空上下文只读 Claude
- 最终确认：用户人工验收

## 风险与关键决策
- <风险或 decision-log.md 链接>

## 产物导航
- [任务清单](tasks.json)
- [验收清单](acceptance.json)
- [决策记录](decision-log.md)
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
| 当前阶段 | <需求确认 / 方式选择 / 开发 / 门禁 / 独立验收 / 人工验收> |
| 状态 | <与 state.json 一致> |
| 当前轮次 | round-NN / 尚未开始 |
| 任务进度 | <已完成数>/<总数>，不使用主观百分比 |
| 下一责任方 | 用户 / 宿主 / Codex developer / Claude developer / reviewer |

## 任务进度
| 任务 | 状态 | 证据 | 说明 |
| --- | --- | --- | --- |
| T-001 | PENDING / IN_PROGRESS / BLOCKED / COMPLETED | <相对链接> | <事实> |

## 最新门禁与验收
- 开发结果：<尚未产生或 result.json 链接>
- 机械门禁：<尚未运行或 gate-evidence.json 链接与结论>
- 独立审查：<尚未运行或 review.json 链接与结论>
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

在初始化、等待需求确认、完成冻结、等待开发方式选择、开始或结束开发、主动调用失败、手动交接和返回、每次机械门禁、独立审查、修复轮次、等待人工验收、用户接受或拒绝、`NEED_HUMAN_REVIEW` 时更新 `progress.md`。

任务状态只能根据真实证据推进；开发者声明不能单独把任务标记为 `COMPLETED`。没有证据时保持 `PENDING`、`IN_PROGRESS` 或 `BLOCKED`。

## 人工验收入口

进入 `WAITING_FOR_MANUAL_ACCEPTANCE` 时，先向用户展示并链接 `development-overview.md`、`progress.md`、冻结基线、最新 `gate-evidence.json` 和 `review.json`。用户可按 `progress.md` 的任务、阻断项和证据逐项查看。接受后记录 `ACCEPTED` 并进入 `COMPLETED`；拒绝时记录关联 ID 和原因，进入新的修复轮次，不得直接覆盖原结论。
