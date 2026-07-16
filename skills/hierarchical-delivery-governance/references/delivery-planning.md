# 可变深度的 Delivery / Capability / Task 规划

Delivery 是可选的最高聚合类型，不是“完整代码项目”的同义词。它可以代表完整项目、大型模块、子系统或跨服务需求；只有工作确实包含多个 Capability 并需要顶层聚合验收时才创建 Delivery。

## 层级事实卡

拆分前记录交付对象、独立验收边界、实际需要的聚合责任、可执行叶子、依赖/集成波次、命中规则、为什么不是更小一级和缺失事实。文件、接口、服务数量和 Full 风险信号不能单独决定 Delivery。事实不足时停在草案和需求确认，不准备工作项、不冻结 baseline，也不把 Delivery 当作保守默认值。

## 选择最浅治理根

- 根 Task：一个可独立执行、测试和验收的结果，无兄弟 Task 依赖；
- 根 Capability：多个 Task 共同交付一个能力，需要共享契约和集成 gate；
- Delivery：多个 Capability 共同交付一个目标，需要跨能力约束和顶层 gate。

不允许 Delivery 直接包含 Task，也不允许 Capability 包含 Capability。需要兄弟依赖时，必须由能表达该依赖的父级承载。

## Delivery 总览（按需）

Delivery baseline 是顶层交付协调契约，包含目标、范围、非目标、R/A、跨工作区约束、交付测试、安全决策、Capability 子契约和风险。Delivery 不包含可执行代码任务。

## Capability 拆分（按需）

Capability 可以是根，也可以从 Delivery 子契约派生。每个 Capability：

- 明确自己的 R/A、集成测试和完成 gate；
- 声明计划 Task 的 ID、标题及父级 R/A 映射；
- 有 Delivery 父级时可以声明同 Delivery Capability 的 `dependsOn`，并保证无环；
- 用 `OPEN` 表示仍在拆分，用 `SEALED` 明确当前子项集合已封口；
- baseline 未冻结前不能准备子 Task。

## Task 拆分

Task 是唯一可执行叶子，也可以直接作为根。合格 Task 必须有单一目标、精确写入范围、输入输出、安全测试 argv、完整 R/A 和可观察完成结果，并能由全新 Agent 在独立上下文中完成。根 Task 的 `dependsOn` 必须为空；Capability 下的 Task 只引用同 Capability 已计划兄弟。

## 持续拆分

Capability 不必一次物化全部 Task 包，但 baseline 必须先声明子契约。发现必要 Task 时，基于当前指纹起草 Capability 修订，展示影响和 stale 后代，取得确认后执行 `revise-item`，再准备新增 Task；聚合 gate 前显式 SEALED。

纯新增兄弟 Task 不使未改变 Task stale。修改 Capability 稳定契约或某个既有 Task 子契约时，只阻断受影响后代。

## 从浅层根受控升层

最初事实只支持根 Task 或根 Capability 时应先使用浅层治理；后续出现真实的兄弟聚合责任，不需要废弃原工作项，也不能直接改它的 kind。

- Task 需要与其他 Task 形成能力聚合：起草一个把现有 Task 列为 child 的根 Capability，按普通流程准备、确认、冻结，再明确确认 `promote-item`；
- Capability 需要与其他 Capability 形成顶层交付：起草一个把现有 Capability 列为 child 的 Delivery，准备、确认、冻结后再升层。

升层前展示源和父的当前 baseline 指纹、父子契约、scope 以及 Task 开发方式失效影响。操作只允许附着一级，保留源 ID、kind、gateLevel 和历史；不能因将来可能扩展而提前创建空父级。

## 完整性检查

- 层级事实卡已确认，没有靠实现数量或风险信号自动升级；
- 每个实际存在的聚合层，其 R/A 都被直接子级覆盖；
- 根 Task 无兄弟依赖；嵌套 Task 依赖只引用同 Capability 计划 Task；
- 根 Capability 无 Capability 依赖；跨能力依赖由 Delivery 下的 Capability `dependsOn` 表达；
- 所有写入路径落在实际父范围内；
- 每个实际存在的聚合层都有自己的测试和 PASS 条件；
- 跨仓库边界、提供方/消费方顺序和测试 cwd 明确。
- 升层时父级已独立冻结并计划现有根，双方指纹和失效影响已由用户确认。

Workstream、Micro 和 M/W/T 可以辅助规划或展示，但不作为治理实体，也不取代任一级 baseline。
