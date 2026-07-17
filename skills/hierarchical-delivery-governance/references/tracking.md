# 分级进度、树形投影与开发复核

## 投影原则

项目级 `governance.sqlite3` 是节点、层级和进度的唯一机器权威。`workspace-overview.md` 按需求根分组展示所有需求；每个需求根的 `progress.md` 使用 Markdown 表格，在第一列保留与该根 `development-plan.md` 相同的工作项 ID、父子顺序和 Delivery→Capability→Task 层级。两个投影都不能把父子节点渲染成彼此并列的需求行。

每个需求根只有一个整树 `development-plan.md` 作为统一人工冻结入口。各实际节点仍有自己的 `development-plan.md`、`progress.md`、baseline、状态、门禁和后续开发复核；节点进度必须直接链接本节点方案。

## 三种进度

每个节点展示：

- 自身 `stage/status/gateLevel/gate/claim/recordRevision`，以及需求评审时确定的根级开发方式；
- `directChildren` 的 total、verified、blocked、active；
- `descendants` 的同类精确计数；
- 根节点的最终 `acceptance.status`；
- 开发前 `development-plan.md`、manual 冻结后的根级 `requirement-handoff.md`、开发后 `development-review.md`、门禁后 `acceptance-report.md` 的对应入口。

Task 子级计数为零。不写主观百分比。协调节点声明的全部 child 必须已物化，因此不存在“计划但尚未生成”的占位进度。

## 根级整树明细

需求根 `progress.md` 在汇总计数之后使用表格展示整棵需求树：

- “层级工作项”列使用稳定 ID 和层级符号保留 `development-plan.md` 的节点顺序，并链接到对应开发方案章节；
- 阶段、状态、门禁和“当前执行”各自使用独立列，不把全部信息拼成一个长行；
- “当前执行”是 claim 的可读投影：协调节点为“不适用”，无 claim 的待执行 Task 为“未认领”，活动 claim 显示 `owner / operationId`，结果已写回的 Task 为“已释放”；
- “节点文件”列链接该节点自己的 `development-plan.md/progress.md`；
- “阶段产物”列为 manual 需求根增加一次性交接入口，并在开发结果或门禁证据存在后增加 `development-review.md` 或 `acceptance-report.md` 入口，尚未生成时显示“无”；
- 明细只由 SQLite 重建，Agent 不直接编辑投影文件。

## 分层视图

- Delivery：展示 Capability 目的、跨能力契约、交付波次、子级进度和顶层 gate。
- Capability：展示 Task 目的、依赖、共享契约、集成波次、子级进度和聚合 gate。
- Task：展示精确文件、接口/函数目标契约、实现逻辑、根级开发方式、claim、结果、复核和 gate。

根 Task、根 Capability 和 Delivery 都在自身 gate PASS 后进入独立验收和用户确认；不要为了最终验收补空父级。

## 写回时机

- `prepare-hierarchy`：一次写入完整嵌套目录、根计划、树形总览和待确认的整树进度明细。
- `freeze-hierarchy`：用同一次确认记录根级开发方式，并更新全部节点确认记录和状态；manual 同时生成根级 `requirement-handoff.md`。
- `dispatch-task`：更新单个 Task 的 claim、上下文与 handoff。
- `task-result`：结构化结果写入 SQLite，生成 `development-review.md`，明确 IMPLEMENTED 不是完成。
- `accept-item`：结构化报告写入 SQLite，生成或更新 `acceptance-report.md`。
- `record-interaction`：追加指令、决策或状态摘要，刷新需求根 `interaction-log.md`。
- retry、聚合 gate、独立审查和用户确认：立即更新数据库和投影。

每次状态写回增加 workspace/record revision，并重建 `workspace-overview.md`、需求根整树明细和每个已物化节点的 `overview.md/progress.md`。因此 Task 被认领、写回实现或阻断、重试、门禁验证，以及父级聚合和最终验收后，根级明细都立即反映新状态。
