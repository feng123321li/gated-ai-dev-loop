# 多 Agent Task 调度

并行是整树执行宿主可采用的运行策略，不是冻结契约、层级或工作项种类，也不是 active 的必要条件。active 的当前 Agent 与 manual 根级交接的接收 Agent 都可根据运行能力并行或串行。Skill 不提供 Codex/Claude 专用的子 Agent 配置文件；Python 控制器提供统一的 READY、claim、operationId 和 Task handoff，宿主使用自身能力自主执行。

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

范围只使用精确相对路径或尾部 `/**` 前缀；任一前缀包含另一范围即冲突。数据库迁移、共享 schema、代码生成清单和共同构建产物必须显式列入 scope。控制器把验证修正追加的精确文件一并纳入冲突计算；与活动 claim 重叠时拒绝修正，等待其释放后重试。多个互相冲突但都满足依赖的 Task 按 ID 稳定排序，只认领第一个，其余留在 FROZEN 并在下一次调度重算。

## 波次

按 Task 依赖图生成拓扑波次。同一波只包含互不依赖且路径互斥的 READY Task。提供方 Task VERIFIED 后，消费方才能进入后续波次。

执行宿主可按以下安全循环自主调度：

1. 调用 `ready-tasks --item <root-id>` 获取当前候选；
2. 按依赖、范围互斥和可用并发槽选择本波 Task；
3. 为每个 Task 生成不同 operationId，先执行 `dispatch-task` 完成 claim 和 handoff；
4. 再启动相互隔离的全新开发 Agent；
5. 分别写回结果并完成 Task 门禁；
6. 循环实现、回归、修复和复测，写回结果后重新计算 READY，直到发生真实阻断或全部 Task VERIFIED。

Agent 数量、并发度和调度顺序不固定。并发不足时自动串行；子 Agent 完全不可用时由执行宿主继续开发，不改变根级方式，也不询问用户。每个 Task 仍需独立 claim、结果和证据，以便归属、恢复和验收。manual 只在规划会话停止自动开发并输出一份根级 `requirement-handoff.md`；接收会话启动后使用同一安全循环处理全树，不逐 Task 返回人工交接。这些调度与回退规则只存在于 Skill 运行说明中，不写入冻结方案、baseline、层级指纹或根级方式记录。

## Claim 和归属

每个 Task 使用唯一 owner/operationId，并单独生成 context。Agent 返回后逐 Task 校验 diff 归属，任何重叠、越界或无法归属的改动都阻断聚合。

## 聚合

先逐 Task gate，再运行 Capability 集成 gate。Capability PASS 后才可向 Delivery 汇总。同一冻结文件集合内的实现或回归失败由 Agent 自动 retry、修复和复测；同一验收契约只缺少计划文件时，释放 claim 后由宿主用 `remediate-task` 追加到原 Task，其他 Agent 不得自行扩大范围或另建需求。只有目标、契约、拓扑或外部权限必须变化时，才重新规划完整需求树并取得人工确认。
