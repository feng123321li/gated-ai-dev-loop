# Delivery → Capability → Task 层级规划

这里的 Delivery 是层级治理根，不是“完整代码项目”的同义词。它可以代表完整项目、大型模块、子系统或跨服务需求；判断标准是能否形成独立交付目标、多个 Capability 和顶层聚合验收，而不是覆盖多少仓库或产品功能。

## 层级事实卡

拆分前先记录交付对象、独立验收边界、计划 Capability 及各自聚合验收、可执行叶子、依赖/集成波次、命中规则、为什么不是更小一级和缺失事实。文件、接口、服务数量和 Full 风险信号不能单独决定 Delivery。事实不足时停在草案和需求确认，不准备工作项、不冻结 baseline，也不把 Delivery 当作保守默认值。

## Delivery 总览

Delivery baseline 是顶层交付协调契约，至少包含：

- 可交付目标、范围和非目标；
- 顶层交付需求与验收映射；
- 架构、合规、兼容和跨工作区约束；
- 顶层交付测试 argv；
- Capability 子契约；
- 风险和已经确认的决策。

Delivery 不包含可执行代码任务，也不使用 Milestone/Workstream 作为治理实体。里程碑可以作为展示信息，但不能取代 Capability baseline。

## Capability 拆分

一个 Capability 应当产生完整、可集成验证的业务能力。每个 Capability：

- 只覆盖 Delivery 范围的子集；
- 明确自己的 R/A、集成测试和完成门禁；
- 声明计划 Task 的 ID、标题及父级 R/A 映射；
- 声明同 Delivery Capability 的 `dependsOn`，并保证依赖图无环；
- 用 `OPEN` 表示仍在拆分，用 `SEALED` 明确当前子项集合已封口；
- 不用“完成整个后端”之类无法独立验收的描述；
- 自己的 baseline 未冻结前不能准备 Task。

## Task 拆分

Task 是唯一可执行叶子。合格 Task 必须：

- 单一目标和精确写入范围；
- 可由一个全新 Agent 在独立上下文中完成；
- 有明确输入、输出、依赖和安全测试 argv；
- 有完整的 R/A 追踪和可观察完成结果；
- 不依赖对话隐含信息；
- 不包含子工作项。

## 持续拆分

Capability 不必一次物化全部 Task 包，但 baseline 必须先声明子契约。后续发现新的必要 Task 时：

1. 基于当前指纹起草 Capability 修订；
2. 需要继续拆分时保持/改为 OPEN，追加 Task 子契约，不删除既有子契约；
3. 展示影响范围和会 stale 的后代；
4. 用户确认后执行 `revise-item`；
5. 再为新增 Task 准备和冻结独立 baseline；准备运行 Capability gate 前显式修订为 SEALED。

纯新增兄弟 Task 不使未改变的 Task stale。修改 Capability 的目标、范围、R/A、测试或某个既有 Task 子契约时，只阻断受影响的后代。

## 完整性检查

冻结前确认：

- 层级事实卡的关键事实已经由用户确认，不存在靠实现数量或风险信号自动升级层级；
- 每条 Delivery R 都被至少一个 Delivery A 和 Capability 覆盖；
- 每条 Capability R 都被至少一个 Capability A 和 Task 覆盖；
- Task 依赖只引用同一 Capability 的计划 Task，且无环；
- 跨能力提供方/消费方通过 Capability `dependsOn` 表达，提供方先通过聚合 gate；
- 所有写入路径都落在父范围内；
- 每个聚合层都有自己的测试和 PASS 条件；
- 跨仓库边界、提供方/消费方顺序和测试 cwd 明确。
