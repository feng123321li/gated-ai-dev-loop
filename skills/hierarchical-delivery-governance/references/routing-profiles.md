# 分层治理路由模型

路由由门禁强度、机器工作项和可选规划投影三个正交维度组成。

## 门禁等级与持久化

- `None`：只读问答或报告，无文件写入，也没有 registry 工作项；
- `LIGHT`：低风险、小范围、影响已知的 Task，可精简非关键说明，但 baseline 字段仍完整，并执行 baseline 确认、开发方式确认和 gate 三项机械门禁；
- `FULL`：高风险、影响未知、跨边界或协调工作项。Delivery/Capability 必须为 `FULL`。

安全、认证、权限、迁移、兼容、事务、并发、外部契约、依赖变化、未知写路径等信号强制 `FULL`。用户请求 `LIGHT` 不能覆盖硬信号。`LIGHT` 只降低材料和审查负担，不取消状态机。

一旦持久化，schema v3 的每个工作项都必须有 `gateLevel`。该值进入 baseline/contract 指纹、registry、上下文和进度投影；缺失或非法降级必须机械拒绝。`None` 不作为 `gateLevel` 值写入，因为它意味着没有工作项。

## 机器工作项种类与浅层根

- `TASK`：单一、可独立执行和验收的叶子；没有兄弟依赖时可直接作为治理根；
- `CAPABILITY`：多个 Task 的能力聚合契约和集成门禁；可以作为治理根；
- `DELIVERY`：多个 Capability 的独立交付目标、跨能力约束和顶层聚合门禁。

合法形态只有：

```text
Task
Capability → Task
Delivery → Capability → Task
```

一个可独立执行结果使用 Task；多个 Task 共同形成一个聚合能力时使用 Capability；多个 Capability 共同形成一个独立交付目标且需要顶层聚合门禁时才使用 Delivery。不要创建空父级来满足固定深度。

根 Task 的 Task `dependsOn` 必须为空；根 Capability 的 Capability `dependsOn` 必须为空。出现兄弟依赖时选择能承载该依赖的上一聚合层。

后续事实证明需要更高聚合责任时，路由只建议升层，不直接修改。先独立准备并冻结包含现有根的父 baseline，再取得绑定双方当前指纹的明确确认，最后执行受控 `TASK → CAPABILITY` 或 `CAPABILITY → DELIVERY` 附着；不改变现有工作项的 kind，也不自动创建或冻结父级。

## Micro、Workstream 与 M/W/T

这些概念不进入 `kind` 枚举，但仍可使用：

- `Micro`：Task 的规模或低风险执行特征；
- `Workstream`：跨工作项的可选规划、排期或汇报视图；
- `M-NNN/W-NNN/T-NNN`：可选的人类可读规划编号或别名。

它们不拥有 baseline、claim 或 gate，不作为 registry 父子关系，也不取代稳定工作项 ID。

## 层级事实卡

推荐根层级前先起草人可读事实卡，至少记录：

- 交付对象、独立验收边界和完成定义；
- 是否存在 Task 聚合、Capability 聚合及各自验收责任；
- 可执行叶子、依赖关系和必要的集成波次；
- 命中的层级规则，以及为什么不是更小、更浅一级；
- 仍缺失、待用户确认的事实。

文件、接口或服务数量，以及公共契约、状态机、幂等、多工作区等 `FULL` 风险信号，只影响门禁等级、拆分和审查强度，不能单独决定升级为 Delivery。缺失事实存在时只展示事实卡草案并等待需求确认；不得保守默认 Delivery，不得准备工作项包或冻结 baseline。

## 变更类型与创建授权

`Feature/Bugfix/Refactor/Migration/Maintenance/Docs/Test` 只描述主要改动性质，不决定父子层级。路由只返回建议，不创建包、不生成 ID、不冻结 baseline。用户必须明确批准 ID 和持久化；冻结、修订和 dogfood 分别需要独立明确授权。

维护 `hierarchical-delivery-governance` 实现仓库时，无论描述中出现什么关键词，都优先进入 `SELF_HOSTING_MAINTENANCE`。只有显式 dogfood 才使用标准运行包路线。
