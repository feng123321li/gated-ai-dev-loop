---
name: layered-delivery
description: "规划、冻结、调度或恢复多项目、多模块交付 Graph。用于把已确认需求组织为递归 GROUP/TASK，自动或手动派遣独立 WorkLoop，执行必需的 TASK/GROUP/Delivery Review，并在最后等待用户验收；也用于恢复暂停、失联、额度耗尽或需要 Revision 的既有 Delivery。"
---

# Layered Delivery

把本 Skill 作为外层交付协调器：治理“何时、由谁运行哪个 Loop”，不规定 Loop 内部“怎样完成工作”。

```text
确认需求 → 准备 Graph → 用户选择执行方式并冻结
→ TASK/Review WorkLoop → 分层汇合与 Review → 用户最终验收
```

## 不可违反的边界

- 只调用 Plugin 注册的 MCP 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE`，停止所有治理写入。
- 只从 MCP 响应读取状态。不得用 Shell、Python 或其他连接读写 `scheduler.db`，也不得自行创建、修改或修补人类投影。
- 只使用 schema v3；准备前调用 `hierarchy_contract` 获取当前精确契约，不从源码、旧会话或示例猜参数。
- SQLite 与事件链是机器权威。Markdown 投影只用于沟通、进度和验收。
- 一个对话工作区最多绑定一个未结束 Delivery。新业务目标默认创建新 Delivery；不得因为工作区已有旧 Delivery 就把新需求写成旧 Delivery 的 Revision。
- 并行 Delivery 使用独立宿主工作区；Git 场景优先使用独立 linked worktree。不要复制调度数据库或启动第二套控制面。
- 总调度上下文只规划和路由，不在自身上下文内实现 TASK 或 Review。每个执行与 Review Loop 使用独立接收上下文。
- Git 创建/切换分支、commit、merge、push、发布、迁移和新增外部权限始终需要各自授权；Graph 的项目范围不替代这些授权。
- 最终完成必须取得真实用户确认。

## 入口路由

先调用 `workspace_status`；已知 Delivery 时显式传 `rootId`。

| 状态 | 下一步 |
|---|---|
| `ABSENT` | 用户要求新交付时读取[规划说明](references/planning-quickstart.md)；只读问答不创建状态 |
| `PREPARED` | 读取规划说明并续接已有方案；需求未变时不要重复 prepare |
| `ACTIVE` / `BLOCKED` / `PAUSED` | 读取[执行说明](references/execution-quickstart.md)，从 `graph_frontier` 恢复 |
| `COMPLETED` | 报告终态；新目标使用新 Delivery |
| `CANCELLED` | 默认报告终态；只有用户明确继续未验收的同一需求时才准备下一 Revision |

MCP 写响应未知、连接恢复、Git 绑定异常或投影问题时，先读取[MCP 与状态说明](references/mcp-transport.md)，不要盲目重放写操作。

## 规划与冻结

规划新 Delivery 或 Revision 时，完整遵循[planning-quickstart.md](references/planning-quickstart.md)：

1. 与用户确认目标、边界、验收点、项目范围、依赖和排他资源。
2. 用 `GROUP` 表达真实的并行汇合或分层 Review；可以递归，也可以完全省略。不要为单个 TASK 制造形式层级。
3. 用 `TASK` 表达唯一执行叶子；每个 TASK 必须配置独立 TASK Review，每个 GROUP 必须配置本层 GROUP Review，Delivery 必须配置最终 Review。
4. 把实现目标和明确约束放入不透明 `loop.payload`。调度器不解释实现计划、测试、Gate 或内部 Skill 流程。
5. 用精确 `resourceClaims` 表达跨 Delivery 排他资源；worktree 不能替代数据库、端口或环境锁。
6. 用户提供的 Skill 只登记为共享 `root.skillHints`，由各 Loop 根据真实上下文决定是否触发。
7. 准备后展示完整计划，并且只提供“自动执行 / 手动交接”两个冻结选项；同时允许用户直接提出修改意见。未明确选择时不冻结。
8. 用户选择后立即以返回的 fingerprint、Revision 和精确项目授权调用 `freeze_hierarchy`。不要追加第二次通用 Yes/No。

需求连续性规则：

- 未开始 TASK 的 `title`、`summary` 或 `payload` 可在用户明确授权后 `unfreeze_task_requirement → refreeze_task_requirement`；不得借此修改依赖、资源、Loop、Review 或拓扑。
- 最终验收前需要改变外层范围时，只有用户明确继续同一 `delivery.id`，或已有 Loop 返回 `REPLAN_REQUIRED`，才调用 `prepare_delivery_revision`。
- 候选 Revision 不替换当前 run；新 Revision 冻结时才原子切换。不要先取消旧 run，也不要为同一需求创建新 Delivery ID。

## 调度循环

冻结后持续读取 `graph_frontier`，完整消费当前批次的 action。精确参数、claim 顺序、租约与恢复规则以[execution-quickstart.md](references/execution-quickstart.md)为准。

1. `REFREEZE_TASK_REQUIREMENT`：停止派遣该 TASK，按当前 requirement revision 完成用户授权的修改并重新读取 frontier。
2. `DISPATCH_LOOP`：手动模式生成交接；自动模式先读取每个 Ready 节点的 `loop_context`，再调用 `plan_dispatch_batch`。
3. 自动选模由总调度 Agent 分析任务风险并提交 `ROUTINE`、`STANDARD` 或 `HIGH`；Controller 不用 Python 做语义判级。缺少分析时只可回退宿主明确报告且与 inventory 匹配的当前 Agent/模型；两者都缺失时保持 deferred。
4. 自动 assignment 只接受宿主正式 Agent API 证明的 `HOST_NATIVE` 容量。PATH、CLI、exec、subprocess 或 companion bridge 一律是 `EXTERNAL_PROCESS`，不得伪装成自动派遣能力。
5. 按 `concurrentDispatchGroups` 并发创建同批独立接收 Agent，严格使用 assignment 的模型、推理强度、工作区、预留 ID、决策指纹和宿主任务名。不得先创建普通 helper 再抢占预留，也不得跨 Delivery 复用上下文或工作区。
6. 严格遵循宿主接收协议：Claude 由真实子 Agent 消费一次性 attestation 后 claim；Codex 由 `SubagentStart` Hook 校验真实 child/parent/task/model 并在 child 可见前 claim。Hook、预留或宿主身份无法证明时 fail closed。
7. 接收方从 `loop_context` 获取冻结输入，自主管理分析、实现、测试、Gate 与修正；长运行在租约到期前 heartbeat。
8. TASK Review、GROUP Review 和 Delivery Review 在各自 Loop 内完成独立发现、修正、验证和复审。P0/P1 未关闭不得成功；P2 必须保留。详见[验收说明](references/acceptance.md)。
9. 只向 `record_loop_result` 提交真实业务终态：`SUCCEEDED`、`BLOCKED`、`REPLAN_REQUIRED` 或 `CANCELLED`。内部 Gate 失败、可修复 finding、容量交接和限额等待都不是 Loop outcome。
10. frontier 返回 `RECORD_USER_CONFIRMATION` 时，展示分层验收结果并等待真实用户确认。

## Agent、模型与容量

- 需要展示本机候选或建议时读取[agent-recommendations.md](references/agent-recommendations.md)。`available_agents` / `recommend_executors` 只读且非绑定，不启动 Agent、不切换模型、不写执行事实。
- 自动编排、自动选模、跨 Adapter、最大并发、额度策略和 Review 多样性由用户级配置控制；读取[orchestrator-configuration.md](references/orchestrator-configuration.md)。
- 跨 Adapter 当前未开放修改；面板和保存工具都以 `ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE` fail closed。只有中央宿主未来能证明可创建、可认证、有容量且属于同一可信编排根的多个 Adapter 时才可开放。
- 只有宿主提供结构化利用率和真实 `resetAt` 时才可提前暂停；不得从文本猜测额度。
- 硬 429 由模型外宿主容量回调处理，不等待失败模型反馈。收到容量等待 action 后不调用推荐器或静默换 Agent，只按宿主提供的一次性恢复方式等待。

## 恢复

- `PAUSED` 或 `RESUME_LOOP_IN_INDEPENDENT_CONTEXT`：路由到新的独立接收上下文，调用 `resume_loop` 后重新取得 dispatch；不要重新 prepare/freeze。
- 容量等待：到恢复时间后由原调度或执行 Agent 重新消费 frontier；不生成业务 outcome。
- 租约过期或基础设施失败：交给 `advance_graph`；旧 operation 不得 heartbeat、pause 或提交结果。
- `WORKER_LOST` 生成新 attempt 后，同一 Adapter 的新编排会话可在下一次成功 claim 时轮换已失联的接收方信任根；控制器记录 `RECEIVER_ROOT_ROTATED`，无需重新 prepare/freeze 或直接改库。跨 Adapter、仍有已认领 Loop 或冲突的有效接收凭据时保持拒绝。
- 物化状态损坏：调用 `rebuild_graph_run` 从已校验事件链重建，不修改事件。
- 外层契约变化：记录 `REPLAN_REQUIRED`，等待用户决定是否准备同一 Delivery 的下一 Revision。

## 按需参考

- 新建或修订 Graph、Git/project scope、接口契约与冻结：[planning-quickstart.md](references/planning-quickstart.md)
- Frontier、自动派遣、接收协议、租约、资源锁与恢复：[execution-quickstart.md](references/execution-quickstart.md)
- Agent/模型发现、推理分类、建议与回退：[agent-recommendations.md](references/agent-recommendations.md)
- TASK/GROUP/Delivery Review 和最终确认：[acceptance.md](references/acceptance.md)
- MCP 断连、工作区绑定、SQLite 权威与投影：[mcp-transport.md](references/mcp-transport.md)
- 中央编排器默认值、配置面板、跨平台路径和跨 Adapter 策略：[orchestrator-configuration.md](references/orchestrator-configuration.md)
