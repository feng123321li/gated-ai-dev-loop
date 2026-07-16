# 分层治理路由模型

路由由三个正交维度组成。

## 门禁等级

- `None`：只读问答或报告，无文件写入；
- `Light`：低风险、小范围、影响已知的 Task；
- `Full`：高风险、影响未知、跨边界或协调工作项。Delivery/Capability 默认 Full。

安全、认证、权限、迁移、兼容、事务、并发、外部契约、依赖变化、未知写路径等信号强制 Full。用户请求 Light 不能覆盖硬信号。

## 工作项种类

- `DELIVERY`：多个 Capability 组成的顶层可交付工作单元，可代表完整项目、大型模块、子系统或跨服务需求；
- `CAPABILITY`：多个 Task 组成的完整业务能力；
- `TASK`：唯一执行叶子。

Micro 不再是工作项种类，只可作为 Task 的低风险执行特征。Workstream 和 M/W/T 编号不是新流程层级。

## 层级事实卡

推荐 `DELIVERY/CAPABILITY/TASK` 前先起草人可读的层级事实卡，至少记录：

- 交付对象、独立验收边界和顶层完成定义；
- 计划的 Capability 及各自聚合验收；
- 可执行叶子、依赖关系和必要的集成波次；
- 命中的层级规则，以及为什么不是更小、更浅一级；
- 仍缺失、待用户确认的事实。

层级只由交付边界和聚合责任决定：一个可独立执行结果使用 Task；多个 Task 共同形成一个聚合能力时使用 Capability；多个 Capability 共同形成一个独立交付目标且需要顶层聚合门禁时才使用 Delivery。

文件、接口或服务数量，以及公共契约、状态机、幂等、多工作区等 Full 风险信号，只影响门禁等级、拆分和审查强度，不能单独决定升级为 Delivery。缺失事实存在时只展示事实卡草案并等待需求确认；不得保守默认 Delivery，不得准备工作项包或冻结 baseline。

## 变更类型

`Feature/Bugfix/Refactor/Migration/Maintenance/Docs/Test` 只描述主要改动性质，不决定父子层级。

## 创建授权不是路由结果

路由只返回建议，不创建包、不生成 ID、不冻结 baseline。用户必须明确批准 ID 和持久化；冻结、修订和 dogfood 分别需要独立明确授权。

维护 `hierarchical-delivery-governance` 实现仓库时，无论描述中出现什么关键词，都优先进入 `SELF_HOSTING_MAINTENANCE`。只有显式 `--dogfood` 使用标准运行包路线。
