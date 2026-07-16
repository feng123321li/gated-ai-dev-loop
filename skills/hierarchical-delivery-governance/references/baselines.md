# 分层 Baseline

## 共同字段

所有工作项使用 schema v3，并包含：`id`、`kind`、`gateLevel`、`title`、`goal`、`scope`、`nonGoals`、`requirements`、`acceptance`、`testCommands`、`risks` 和 `decisions`。

`gateLevel` 只能是 `LIGHT|FULL`。只有 Task 可以选择 `LIGHT`；Delivery 和 Capability 必须为 `FULL`。该字段进入 baseline 与 contract 指纹，因此缺失、篡改或未经确认改变门禁等级都会被检测。`None` 是不创建工作项的路由结果，不进入 schema。

- Delivery 额外包含 `decomposition.status` 和 Capability `children`；
- Capability 额外包含可空 `parentId`、`decomposition.status`、`decomposition.dependsOn` 和 Task `children`；
- Task 额外包含可空 `parentId` 与 `execution {dependsOn, inputs, outputs}`。

`parentId: null` 表示浅层治理根。根 Task 的 Task 依赖必须为空；根 Capability 的 Capability 依赖必须为空。非空 parentId 仍必须绑定已冻结、已计划且范围包含当前项的父契约。

Delivery/Capability authority 为 `COORDINATION`，Task 为 `EXECUTION`。

## 准备和冻结

正常交互只请求一次批准。批准必须绑定刚刚展示的具体 ID、完整 baseline 内容以及“持久化并冻结”动作；宿主随后调用 `approve-item --confirmed`，控制器内部依次准备并冻结，不再向用户请求第二次确认。`prepare-item` 产生的 `WAITING_FOR_BASELINE_CONFIRMATION` 仅作为恢复/诊断中间态。冻结时仍重新验证：

- schema、ID 和字段集合；
- `gateLevel` 合法且协调层没有降为 `LIGHT`；
- R/A 追踪；
- 安全相对范围；有父级时校验父范围包含；
- 有父级时，父级已冻结且子契约存在；
- 有父级的 Task 依赖属于同一 Capability；浅层根不得声明缺失聚合层才能承载的兄弟依赖；
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

## 受控升层

升层不改变源工作项的 `kind`，而是把已冻结浅层根附着到单独准备、确认并冻结的聚合父级。只允许：

- 根 Task 附着到根 Capability；
- 根 Capability 附着到 Delivery。

父 baseline 必须预先把源列为计划 child；源和父都必须尚未运行 gate，源子树不能有活动 claim。`promote-item` 同时比较源、父当前 baseline 指纹，并且必须有显式 `--confirmed`。成功后源 baseline revision 增加、父契约指纹进入源 baseline，registry 记录包含旧源指纹、新源指纹和父指纹的 `promotionHistory`。

Task 升层会删除已选开发方式、上下文和 handoff，并回到 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`；因为新的父链属于新的执行授权上下文。Capability 升层不改变自身稳定 contract，未变化的现有 Task 子契约继续有效。升层不会代替父级准备或冻结，也不允许一次跳过 Capability 把 Task 直接附着到 Delivery。

## 兼容边界

新工作项只写 `.hierarchical-delivery-governance/work-items/<id>/`，不扫描、迁移或解释其他历史控制目录。
