# Graph 自动并行调度

并行是 Graph 运行时自动计算的执行策略，不是冻结契约、层级或工作项种类。用户/需求宿主只在开发前讨论并确认方案，在工程门禁通过后做最终验收；中间的选任务、定顺序、计算目标 Agent 数、失败路由和恢复均由 Graph 管理。

Python 控制器提供统一的 READY、`dispatchPlan`、claim、operationId 和 Task handoff。执行平台只提供 Agent 与队列能力：能并行就立即启动，容量不足就按 Graph 顺序排队，完全没有子 Agent 时由当前 Agent 串行消费。执行平台不得挑选 Task 子集或另定优先级。

## 资格

一个 Task 可并行调度仅当：

- 自身和父链 baseline 有效；
- 所有依赖 VERIFIED；
- 所属 Capability 的提供方 Capability 全部 VERIFIED；
- 没有 claim；
- 与本波其他 Task 及活动 claim 的写入范围不重叠；
- 每个 Task 有独立测试和结果边界；
- 每个实际并行执行者都使用相互隔离的全新开发上下文。

不满足时退回串行或 BLOCKED，不通过提示词约定掩盖共享写入。

范围只使用精确相对路径或尾部 `/**` 前缀；任一前缀包含另一范围即冲突。数据库迁移、共享 schema、代码生成清单和共同构建产物必须显式列入 scope。控制器把验证修正追加的精确文件一并纳入冲突计算；与活动 claim 重叠时拒绝修正，等待其释放后重试。多个互相冲突但都满足依赖的 Task 按稳定图顺序只把第一个列入本轮 `dispatchPlan`，其余保持 FROZEN 并在下一次状态迁移后重算。

## 波次

按 Task 依赖图生成拓扑波次。同一波只包含互不依赖且路径互斥的 READY Task。提供方 Task VERIFIED 后，消费方才能进入后续波次。

Graph 执行循环必须按以下方式自动调度：

1. 调用 MCP `graph_frontier` 获取当前结构化动作、`dispatchPlan`、并行组与阻断原因；仅 CLI fallback 使用 `graph-frontier --item <root-id>`；
2. 读取 `dispatchPlan.dispatchTaskIds`、`desiredNewAgentCount` 与 `desiredTotalAgentCount`。这就是本轮完整且有序的自动调度结果，执行适配器不能选择其中一部分；
3. 为计划中的每个 Task 按顺序预留稳定队列位置，但排队项保持未认领；平台确认某个 worker/Agent 真正取得执行容量后，才生成本 graph run 中从未使用过的 operationId 并执行 `dispatch_task`；
4. 为已取得执行容量的 Task 启动相互隔离的全新开发 Agent；执行适配器独立按 frontier 的 `nextWakeAt` 唤醒并消费到期的 `HEARTBEAT_TASK`，没有独立适配器时由当前 Agent 承担续租，没有子 Agent 能力时由当前 Agent 顺序消费同一调度合同；
5. 先按 `evidenceContractRefs.result` 查询当前模板，再分别写回结果并完成 Task 门禁；
6. 每次认领、结果、门禁、失败或恢复迁移后重新查询 frontier，由 Graph 重算 Agent 目标数与队列；硬过期时消费 `ADVANCE_GRAPH`，再用新 operation 重新认领并提交已完成工作，不请求人工重置；继续 `RUN_GATE`、后继 `DISPATCH_TASK`、review 和 confirmation，直到发生真实阻断或图完成。

Agent 数量不是人工固定值。`desiredNewAgentCount` 是当前需要启动或入队的 Agent 数，`activeAgentCount` 是当前子树已认领 Task 数，`desiredTotalAgentCount` 是 Graph 给出的运行目标。执行平台的容量只改变立即运行或稳定排队，不改变 Graph 的任务选择；稳定排队不等于提前 claim。每个 Task 仍需独立 claim、唯一 operationId、结果和证据，以便归属、恢复和验收。manual 只在规划会话停止自动开发并输出一份根级 `requirement-handoff.md`；接收会话启动后使用同一 Graph 循环处理全树，不逐 Task 返回人工交接。这些瞬时调度与回退结果不写入冻结方案、baseline、层级指纹、图指纹或根级方式记录。

## Claim 和归属

每个 Task 使用唯一 owner/operationId，并单独生成 context。Agent 返回后逐 Task 校验 diff 归属，任何重叠、越界或无法归属的改动都阻断聚合。

## 聚合

先逐 Task gate，再运行 Capability 集成 gate。Capability PASS 后才可向 Delivery 汇总。同一冻结文件集合内的实现或回归失败由 Agent 自动 retry、修复和复测；同一验收契约只缺少计划文件时，释放 claim 后由 Graph 执行循环按修正路由用 `remediate_task` 追加到原 Task，其他 Agent 不得自行扩大范围或另建需求。只有目标、契约、拓扑或外部权限必须变化时，才重新规划完整需求树并取得人工确认。
