# 多人并行 Task 调度

并行是 READY Task 的执行方式，不是层级或工作项种类。

## 资格

一个 Task 可并行调度仅当：

- 自身和父链 baseline 有效；
- 所有依赖 VERIFIED；
- 所属 Capability 的提供方 Capability 全部 VERIFIED；
- 没有 claim；
- 与本波其他 Task 及活动 claim 的写入范围不重叠；
- 每个 Task 有独立测试和结果边界；
- 宿主能创建相互隔离的全新开发上下文。

不满足时退回串行或 BLOCKED，不通过提示词约定掩盖共享写入。

范围只使用精确相对路径或尾部 `/**` 前缀；任一前缀包含另一范围即冲突。数据库迁移、共享 schema、代码生成清单和共同构建产物必须显式列入 scope。多个互相冲突但都满足依赖的 Task 按 ID 稳定排序，只认领第一个，其余留在 FROZEN 并在下一次调度重算。

## 波次

按 Task 依赖图生成拓扑波次。同一波只包含互不依赖且路径互斥的 READY Task。提供方 Task VERIFIED 后，消费方才能进入后续波次。

## Claim 和归属

每个 Task 使用唯一 owner/operationId，并单独生成 context。Agent 返回后逐 Task 校验 diff 归属，任何重叠、越界或无法归属的改动都阻断聚合。

## 聚合

先逐 Task gate，再运行 Capability 集成 gate。Capability PASS 后才可向 Delivery 汇总。一个 Agent 失败不授权其他 Agent 扩大范围替它完成；必须重新规划并取得必要确认。
