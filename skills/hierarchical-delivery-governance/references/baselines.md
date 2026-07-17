# 分层 Baseline

## 共同字段

所有工作项只使用当前完整 schema v3，并包含：`id`、`kind`、`gateLevel`、`title`、`goal`、`scope`、`nonGoals`、`requirements`、`acceptance`、`testCommands`、`risks`、`decisions` 和 `developmentPlan`。缺少任何当前字段的包都不恢复、不修订、不写入。

`gateLevel` 只能是 `LIGHT|FULL`。只有 Task 可以选择 `LIGHT`；Delivery 和 Capability 必须为 `FULL`。该字段进入 baseline 与 contract 指纹，因此缺失、篡改或未经确认改变门禁等级都会被检测。`None` 是不创建工作项的路由结果，不进入 schema。

- Delivery 额外包含 `decomposition.status`、Capability `children`，以及 `developmentPlan {purpose, childPlans, sharedContracts, integrationFlow, deliveryWaves, testPlan, reviewPoints}`；
- Capability 额外包含可空 `parentId`、`decomposition.status`、`decomposition.dependsOn`、Task `children`，以及同结构的协调层 `developmentPlan`；
- Task 额外包含可空 `parentId`、`execution {dependsOn, inputs, outputs}`，以及 `developmentPlan {purpose, scenarios, fileChanges, interfaces, logic, dataAndTransactions, compatibility, testPlan, reviewPoints}`。

Task `fileChanges` 必须是 scope 内的精确相对路径；`interfaces` 根据场景描述 HTTP/RPC/函数/方法/类/事件/schema/config/CLI/UI/文件格式等当前与目标契约，不涉及接口时允许空数组并在人类投影中明确显示。协调层 `childPlans` 必须覆盖全部直接子级并冻结依赖；子级实际 `dependsOn` 必须一致。`deliveryWaves` 覆盖全部直接子级并满足依赖先后。所有 acceptance 都必须映射到 `testPlan` 中的冻结测试命令。

`parentId: null` 表示浅层治理根。根 Task 的 Task 依赖必须为空；根 Capability 的 Capability 依赖必须为空。非空 parentId 仍必须绑定已冻结、已计划且范围包含当前项的父契约。

Delivery/Capability authority 为 `COORDINATION`，Task 为 `EXECUTION`。

## 准备和冻结

正常交互分成两个机械阶段：

1. `prepare-item` 校验 definition 并持久化 `baseline.json/md`、`development-plan.json`、`development-review.md` 等文件，状态为 `WAITING_FOR_BASELINE_CONFIRMATION`；返回 `humanArtifacts` 和 baseline 指纹。此阶段的目的就是让人工在冻结前有真实文件可看。
2. 宿主展示 `development-review.md`。用户明确同意当前文件和指纹后，调用 `freeze-item --expected-baseline <sha256> --confirmed`；state 中写入 `review.status=APPROVED`、`reviewedBy=user` 和时间，并更新评审 Markdown。

评审阶段提出修改时，可用同一 ID 和完整新 definition 再次执行 `prepare-item`；控制器只允许替换仍处于等待评审、且 kind/parent 未改变的包，生成新指纹并使旧指纹无法 freeze。已经冻结的工作项必须走 `revise-item`，不能用 prepare 覆盖。

CLI 不提供一步完成准备与冻结的 `approve-item`。冻结时仍重新验证：

- schema、ID 和字段集合；
- `gateLevel` 合法且协调层没有降为 `LIGHT`；
- R/A 追踪；
- `developmentPlan` 层级结构、场景、精确文件、接口/共享契约、子级覆盖、依赖波次和测试映射；
- 安全相对范围；有父级时校验父范围包含；
- 有父级时，父级已冻结且子契约存在；
- 有父级的 Task 依赖属于同一 Capability；浅层根不得声明缺失聚合层才能承载的兄弟依赖；
- 测试命令为安全 argv 数组；
- baseline 和 contract 指纹。

冻结后 stage 为 `BASELINE_FROZEN`。Delivery/Capability 状态进入 `FROZEN`；Task 状态进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。冻结的 `developmentPlan` 会进入 Task 独立上下文，Task PASS evidence 的实际文件不得超出 `fileChanges`。Task baseline 只确定可开发契约，不代表已经选择开发方式；任何开发授权都不来自 Delivery 总览、聊天或进度投影。

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
