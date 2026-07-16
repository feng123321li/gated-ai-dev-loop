# 分层 Baseline

## 共同字段

所有工作项使用 schema v2，并包含：`id`、`kind`、`title`、`goal`、`scope`、`nonGoals`、`requirements`、`acceptance`、`testCommands`、`risks` 和 `decisions`。

- Delivery 额外包含 `decomposition.status` 和 Capability `children`；
- Capability 额外包含 `parentId`、`decomposition.status`、同 Delivery Capability `decomposition.dependsOn` 和 Task `children`；
- Task 额外包含 `parentId` 与 `execution {dependsOn, inputs, outputs}`。

Delivery/Capability authority 为 `COORDINATION`，Task 为 `EXECUTION`。

## 准备和冻结

准备只在用户批准 ID 和持久化后执行，状态为 `WAITING_FOR_BASELINE_CONFIRMATION`。冻结必须收到与该 baseline 对应的显式确认，并重新验证：

- schema、ID 和字段集合；
- R/A 追踪；
- 安全相对范围和父范围包含；
- 父级已冻结且子契约存在；
- Task 依赖属于同一 Capability；
- 测试命令为安全 argv 数组；
- baseline 和 contract 指纹。

冻结后 stage 为 `BASELINE_FROZEN`。Delivery/Capability 状态进入 `FROZEN`；Task 状态进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。Task baseline 只确定可开发契约，不代表已经选择开发方式；任何开发授权都不来自 Delivery 总览、聊天或进度投影。

## 父子指纹

子 baseline 绑定“父级稳定契约 + 该子项契约”的指纹，不绑定无关兄弟列表。这样追加兄弟 Task 不会使现有 Task 失效，同时父目标、范围、R/A、测试或自己的子契约变化仍会被检测。

用户明确选择 active/manual 后，`development-mode.json` 绑定当前 Task baseline 指纹。每次生成 Task 上下文和每次 claim 前都校验开发方式记录并重算整条父链；未选择返回 `WORK_ITEM_DEVELOPMENT_MODE_REQUIRED`，记录被改动或不匹配时拒绝，父链失配返回 `WORK_ITEM_BASELINE_STALE`。

## 修订

`revise-item` 需要：

- 当前 `expectedBaselineFingerprint`；
- 显式 `--confirmed`；
- 不得改变任何活动后代所依赖的父契约；纯追加无关兄弟 child 可以与活动 claim 并存；
- 工作项尚未 VERIFIED；
- 不删除既有 child。

修订生成新的 baseline 指纹和 revision，重置该工作项 gate。Task 修订还会删除 `development-mode.json`、上下文和 handoff，要求用户针对新 baseline 重新选择开发方式。未变化子契约继续有效；变化子契约的后代在重新冻结前保持 stale。

## 兼容边界

新工作项只写 `.hierarchical-delivery-governance/work-items/<id>/`，不扫描、迁移或解释其他历史控制目录。
