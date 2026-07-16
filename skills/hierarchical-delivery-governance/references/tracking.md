# 分级进度、交付状态与投影

## 投影原则

`work-item-registry.json` 是机器权威；`workspace-overview.md` 和每个包的 `overview.md/progress.md` 都由它重建。投影不能授权开发或证明 PASS。

## 层级事实卡投影

在任何工作项持久化前，协调视图先展示人可读的层级事实卡：交付对象和独立验收边界、计划 Capability 与各自聚合验收、可执行叶子、依赖和集成波次、命中规则、为什么不是更小一级、缺失事实及待确认项。它是路由依据，不是创建授权；事实不足时保持草案，不生成 ID、不准备包、不冻结 baseline。

## 三种进度

每个工作项都展示：

- 自身 `stage/status/developmentMode/gate/claim/recordRevision`；
- `directChildren`：直接子级 total、verified、blocked、active；
- `descendants`：全部后代的同类精确计数。
- Delivery 的 `delivery.status`：最终审查和用户确认阶段；非 Delivery 显示 `n/a`。

Task 的子级计数为零。不要写主观百分比、故事点完成率或“基本完成”。计划但尚未物化的 child 计入 total，状态视为 planned。

## Delivery 视图

Delivery overview 是顶层交付视图，不要求范围覆盖整个仓库或产品。它按 Capability 展示状态、直接 Task 完成数、阻断项、聚合 gate、delivery 和证据入口。Delivery VERIFIED 需要所有计划 Capability VERIFIED 且顶层交付 gate PASS；只有审查完成并取得用户确认后 delivery 才为 COMPLETED。

## Capability 视图

Capability progress 展示计划 Task、依赖、READY/CLAIMED/IMPLEMENTED/VERIFIED/BLOCKED 状态和集成 gate。新增 Task 后 total 立即增加；既有 Task 状态不被兄弟追加重置。

## Task 视图

Task progress 展示父链、baseline 指纹、开发方式及确认记录、依赖、claim、实现证据、gate、下一动作和阻断解除条件。未选择时下一动作是明确选择 active/manual；开发 Agent 不更新控制投影，宿主验证返回结果后写入。

## 写回时机

准备、冻结、开发方式选择、修订、claim、Task result、retry、gate、独立/人工审查和用户确认后立即写回，不在整轮结束后批量补写。每次写回增加 registry/record revision，并重建所有受影响投影。
