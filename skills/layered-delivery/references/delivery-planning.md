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

Delivery baseline 是顶层交付协调契约，包含目标、范围、非目标、R/A、跨工作区约束、交付测试、安全决策、Capability 子契约和风险。根级 `development-plan.md` 必须逐项展示 Capability 的开发目的/交付物、依赖、跨 Capability 接口或共享契约、交付波次和顶层测试映射。Delivery 不包含可执行代码任务。

## Capability 拆分（按需）

Capability 可以是根，也可以从 Delivery 子契约派生。每个 Capability：

- 明确自己的 R/A、集成测试和完成 gate；
- 声明计划 Task 的 ID、标题及父级 R/A 映射；
- 有 Delivery 父级时可以声明同 Delivery Capability 的 `dependsOn`，并保证无环；
- 用 `OPEN` 表示仍在拆分，用 `SEALED` 明确当前子项集合已封口；
- 在开发评审中逐项展示 Task 目的/交付物、跨 Task 接口或共享契约、集成流程、波次和测试映射；
- 全部 Task 必须与 Capability 在同一次 `prepare_hierarchy` 中物化和评审。

## Task 拆分

Task 是唯一可执行叶子，也可以直接作为根。合格 Task 必须有单一目标、按最小实际模块划分的外层 Scope、输入输出、安全测试 argv、完整 R/A 和可观察完成结果，并在评审文件中明确变更场景、精确文件动作、接口/函数当前与目标契约、实现逻辑、数据事务、兼容性和测试映射，能由全新 Agent 在独立上下文中完成。Scope 通常使用 `module/**` 并允许同模块内必要文件生成，但精确写授权仍由 `developmentPlan.fileChanges` 给出；不得使用全仓库 `**`。根 Task 的 `dependsOn` 必须为空；Capability 下的 Task 只引用同 Capability 已计划兄弟。

用户明确指定仅供开发使用的 Skill 不参与层级事实卡、需求推导或 Task 拆分，也不触发更深治理层级。规划者不预读或递归展开该 Skill，但必须先合并宿主级 root 与项目级 project catalog 精确核对名称；不存在或疑似拼错时，把控制器返回的带来源候选交给宿主提示用户选择或安装，不得继续 prepare 或静默纠正。验证存在后，只把用户确认的 catalog 名作为 `DEVELOPMENT` required Skill 绑定到实际执行它的 Task；Skill 内部依赖留到 worker 执行完整流程时处理，不自动形成新的 required Skill 或 GATE。

## 完整物化与重新规划

`OPEN` 可以表达未来可能继续分析，但当前 baseline 声明的全部 child 必须一次物化。等待人工评审期间发现必要 Task 或 Capability 时，用同一根 ID 重新准备完整树，旧层级指纹自动失效。整树冻结后不允许单节点升层、平铺追加或局部改写拓扑；新增独立需求使用新的需求根，原需求边界变化则先保持阻断并重新进行人工层级规划。

## 完整性检查

- 层级事实卡已确认，没有靠实现数量或风险信号自动升级；
- 每个实际存在的聚合层，其 R/A 都被直接子级覆盖；
- 根 Task 无兄弟依赖；嵌套 Task 依赖只引用同 Capability 计划 Task；
- 根 Capability 无 Capability 依赖；跨能力依赖由 Delivery 下的 Capability `dependsOn` 表达；
- 所有写入路径落在实际父范围内；
- 每个实际存在的聚合层都有自己的测试和 PASS 条件；
- Task 精确文件/接口方案、Capability 的 Task 组合方案、Delivery 的 Capability 组合方案均已进入同一根级 `development-plan.md`；
- 跨仓库边界、提供方/消费方顺序和测试 cwd 明确。
- 人工评审当前方案并选择根级开发方式，Agent 使用准备结果中的层级指纹和同一次确认执行整树冻结。

Workstream、Micro 和 M/W/T 可以辅助规划或展示，但不作为治理实体，也不取代任一级 baseline。
