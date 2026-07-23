# 分级进度、树形投影与开发复核

## 投影原则

项目级 `governance.sqlite3` 是节点、层级和进度的唯一机器权威。`workspace-overview.md` 只保留按最近更新时间倒序的全局需求索引；索引展示本机时区的创建时间和更新时间（均精确到分），以及根类型、状态、门禁、后代进度和入口。所有面向人的状态报告同样必须把 SQLite 和控制器 JSON 中的 UTC 时间转换为当前运行环境的本机时区，并显式标注 UTC 偏移（例如 `UTC+08:00`）；SQLite、事件链和 JSON 机器字段继续保持 UTC 原值。`workspace-overview/YYYY-MM.md` 是月度索引，详细层级写入 `workspace-overview/YYYY-MM/<root-id>.md`，每个需求保留开始时间、完成日期和 Delivery→Capability→Task 表格。完成日期只能来自 `COMPLETED` 的最终用户确认时间，不能用 gate 时间或最近更新时间代替。物理目录保持稳定根 ID，不添加日期。每个需求根的 `progress.md` 使用 Markdown 表格，在第一列保留与该根 `development-plan.md` 相同的工作项 ID、父子顺序和层级。投影不能把父子节点渲染成彼此并列的需求行，也不能使用会被 Markdown 折叠为长段落的连续普通文本行。

月度明细链接必须直接指向以需求根 ID 命名的独立 Markdown 文件，不携带标题片段，确保原始 Markdown 编辑器和预览查看器都可导航。

每个需求根只有一个整树 `development-plan.md` 作为统一人工冻结入口。需求根的 `progress.md` 是整树总进度；根节点自身进度使用同目录 `node-progress.md`，子节点自身进度使用各自目录 `progress.md`。各实际节点仍有自己的开发内容、节点进度、baseline、状态、门禁和后续开发复核；节点进度必须直接链接本节点方案。

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
- “节点文件”列链接该节点自己的方案和节点进度：根节点进度链接 `node-progress.md`，子节点进度链接其目录下的 `progress.md`；根节点不得回链整树 `progress.md`；
- “阶段产物”列为 manual 需求根增加一次性交接入口，并在开发结果或门禁证据存在后增加 `development-review.md` 或 `acceptance-report.md` 入口，尚未生成时显示“无”；
- 明细只由 SQLite 重建，Agent 不直接编辑投影文件。

## 分层视图

- Delivery：展示 Capability 目的、跨能力契约、交付波次、子级进度和顶层 gate。
- Capability：展示 Task 目的、依赖、共享契约、集成波次、子级进度和聚合 gate。
- Task：展示冻结精确文件、验证修正补充文件、有效授权集合、接口/函数目标契约、实现逻辑、根级开发方式、claim、结果、复核和 gate。

根 Task、根 Capability 和 Delivery 都在自身 gate PASS 后进入独立验收和用户确认；不要为了最终验收补空父级。

## 写回时机

- `prepare-hierarchy`：一次写入完整嵌套目录、根计划、树形总览和待确认的整树进度明细。
- `freeze-hierarchy`：用同一次确认记录根级开发方式，并更新全部节点确认记录和状态；manual 同时生成根级 `requirement-handoff.md`。
- `dispatch-task`：更新单个 Task 的 claim、上下文与 handoff。
- `task-result`：结构化结果写入 SQLite，生成 `development-review.md`，明确 IMPLEMENTED 不是完成。
- `remediate-task`：把同一验收契约的文件遗漏追加到原 Task，失效相关 gate，并在开发复核、验收报告和交互日志中展示修正明细；不生成新需求根。
- `accept-item`：结构化报告写入 SQLite，生成或更新 `acceptance-report.md`。
- `record-interaction`：追加指令、决策或状态摘要，刷新需求根 `interaction-log.md`。
- retry、聚合 gate、独立审查和用户确认：立即更新数据库和投影。

每次状态写回增加 workspace/record revision，并重建 `workspace-overview.md`、全部 `workspace-overview/YYYY-MM.md` 和 `workspace-overview/YYYY-MM/<root-id>.md`、需求根整树 `progress.md`、根节点 `node-progress.md`，以及每个已物化子节点的 `overview.md/progress.md`。不再存在的月份文件与需求明细会随月度投影目录的原子重建一并清理。因此 Task 被认领、写回实现或阻断、重试、门禁验证，以及父级聚合和最终验收后，索引与月度明细都立即反映新状态。
