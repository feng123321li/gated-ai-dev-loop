# 分级进度、树形投影与开发复核

## 投影原则

`work-item-registry.json` 是节点状态机器权威，根目录 `hierarchy.json` 绑定整棵需求树。`workspace-overview.md` 必须按需求根分组并使用树形结构展示 Delivery→Capability→Task；不能把父子节点渲染成彼此并列的需求行。

一个需求只显示一个根级 `development-plan.md` 入口。各节点仍有自己的 baseline、状态、进度、门禁和后续开发复核。

## 三种进度

每个节点展示：

- 自身 `stage/status/gateLevel/developmentMode/gate/claim/recordRevision`；
- `directChildren` 的 total、verified、blocked、active；
- `descendants` 的同类精确计数；
- 根节点的最终 `acceptance.status`；
- 开发前 `development-plan.md`、开发后 `development-review.md`、门禁后 `acceptance-report.md` 的对应入口。

Task 子级计数为零。不写主观百分比。协调节点声明的全部 child 必须已物化，因此不存在“计划但尚未生成”的占位进度。

## 分层视图

- Delivery：展示 Capability 目的、跨能力契约、交付波次、子级进度和顶层 gate。
- Capability：展示 Task 目的、依赖、共享契约、集成波次、子级进度和聚合 gate。
- Task：展示精确文件、接口/函数目标契约、实现逻辑、开发方式、claim、结果、复核和 gate。

根 Task、根 Capability 和 Delivery 都在自身 gate PASS 后进入独立验收和用户确认；不要为了最终验收补空父级。

## 写回时机

- `prepare-hierarchy`：一次写入完整嵌套目录、根计划和树形总览。
- `freeze-hierarchy`：一次更新全部节点确认记录和状态。
- `select-development-mode/dispatch-task`：更新 Task 模式、claim、上下文与 handoff。
- `task-result`：生成 `development-review.json/md`，明确 IMPLEMENTED 不是完成。
- `accept-item`：生成或更新 `acceptance-report.json/md`。
- retry、聚合 gate、独立审查和用户确认：立即更新 registry 和投影。

每次写回增加 registry/record revision，并重建所有受影响的树形投影。
