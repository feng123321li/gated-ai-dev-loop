---
name: layered-delivery
description: "治理或恢复分层软件交付。当工作区存在 `.layered-delivery/` 时接管现有 SQLite/Graph 运行；无治理状态时，按最浅合法层级规划并推进开发、门禁、审查和验收。"
allowed-tools:
  - mcp__plugin_layered-delivery_layered-delivery__workspace_status
  - mcp__plugin_layered-delivery_layered-delivery__begin_payload_upload
  - mcp__plugin_layered-delivery_layered-delivery__append_payload_chunk
  - mcp__plugin_layered-delivery_layered-delivery__finalize_payload_upload
  - mcp__plugin_layered-delivery_layered-delivery__payload_upload_status
  - mcp__plugin_layered-delivery_layered-delivery__abort_payload_upload
  - mcp__plugin_layered-delivery_layered-delivery__prepare_hierarchy
  - mcp__plugin_layered-delivery_layered-delivery__ready_tasks
  - mcp__plugin_layered-delivery_layered-delivery__graph_status
  - mcp__plugin_layered-delivery_layered-delivery__graph_frontier
  - mcp__plugin_layered-delivery_layered-delivery__graph_events
  - mcp__plugin_layered-delivery_layered-delivery__graph_replay
  - mcp__plugin_layered-delivery_layered-delivery__advance_graph
  - mcp__plugin_layered-delivery_layered-delivery__task_context
  - mcp__plugin_layered-delivery_layered-delivery__evidence_contract
  - mcp__plugin_layered-delivery_layered-delivery__record_skill_activation
  - mcp__plugin_layered-delivery_layered-delivery__record_skill_conformance
  - mcp__plugin_layered-delivery_layered-delivery__dispatch_task
  - mcp__plugin_layered-delivery_layered-delivery__heartbeat_task
  - mcp__plugin_layered-delivery_layered-delivery__pause_task
  - mcp__plugin_layered-delivery_layered-delivery__resume_task
  - mcp__plugin_layered-delivery_layered-delivery__claim_task
  - mcp__plugin_layered-delivery_layered-delivery__task_result
  - mcp__plugin_layered-delivery_layered-delivery__remediate_task
  - mcp__plugin_layered-delivery_layered-delivery__retry_item
  - mcp__plugin_layered-delivery_layered-delivery__gate_item
  - mcp__plugin_layered-delivery_layered-delivery__accept_item
  - mcp__plugin_layered-delivery_layered-delivery__record_independent_review_pass
  - mcp__plugin_layered-delivery_layered-delivery__record_independent_review_blocked
  - mcp__plugin_layered-delivery_layered-delivery__refresh_projections
  - mcp__plugin_layered-delivery_layered-delivery__record_interaction
  - mcp__plugin_layered-delivery_layered-delivery__interaction_log
---

# Layered Delivery

控制器治理交付；Graph 存状态并推进。

## 读取原则

- 首次只读取本文件；不得预读全部 references、源码、memory 或整树模板。
- 首选 Plugin 的单一 stdio MCP Server，不用 Shell 包装。
- Server 固定项目根；工具不接受 `root`、`dogfood` 或 `confirmed`。
- 仅 MCP 不可用时，从当前 Skill 元数据解析 `<skill-root>`，在项目根运行 `python -X utf8 <skill-root>/scripts/hdg.py --help`；不得固化用户目录、Skill 安装位置或操作系统路径。
- 以工具 schema 为准；仅真实超限时按 [stdin-transport.md](references/stdin-transport.md) 暂存 payloadRef，再调用原业务工具。CLI 非零退出时停止解析。

## 核心契约

- 只使用根 `Task`、`Capability → Task` 或 `Delivery → Capability → Task` 的最浅合法结构；Task 是执行叶子。
- 一个需求只有一个 `work-items/<root-id>/` 顶层目录；SQLite 是机器权威，Markdown 只是投影。
- 人只评审并冻结一次整树；Task、Agent 数、顺序、门禁和恢复由 Graph 决定。
- 每个 requirement 都必须有独立 acceptance；跨需求 acceptance 只能追加集成验收，不能代替任一需求自己的可观察通过条件。
- 可省略 `requiredSkills` 或传空数组，两者都不启用 Skill 门禁；非空时需求可指定任意合法 catalog 名，控制器无 Skill 白名单，根级要求向后代继承。每项必须经宿主原生入口明确调用并用 `record_skill_activation` 绑定当前 attempt；Read/load/提示提名不算激活。Claude 记录 `CLAUDE_SKILL_TOOL` 与 tool-use ID，Codex 以显式 `$skill` 触发并记录 `CODEX_EXPLICIT_SKILL` 与 task/session 调用 ID；一个调用 ID 不能覆盖多个 Skill。
- 完整执行后用 `record_skill_conformance` 记录实际检查。成功 result/gate/review 要求逐项 `INVOKED + PASS`；`skillUsage` 只作 artifact 审计，不能替代 Graph 事件。验收报告只从事件投影真实调用与符合性。细节按阶段读取 [development.md](references/development.md)、[acceptance.md](references/acceptance.md) 和 Claude 宿主说明。
- 同一契约内的修正回到原 Task；不创建重复根或扩大文件授权。
- Task、聚合 gate、独立审查和用户确认都是显式图节点；只有最终用户确认后的 `COMPLETED` 表示完成。
- `active` 在冻结契约内自动推进，不逐 Task 确认。
- Agent 不直接写 SQLite、baseline 或投影；最终确认和外部动作仍需用户授权。

## 选择入口

1. MCP 先调用 `workspace_status`，不按文件推断。`ACTIVE`：读取 [workflow.md](references/workflow.md) 和 [stdin-transport.md](references/stdin-transport.md)，再以 `graph_frontier` 恢复。`ABSENT/STAGING_ONLY`：没有可恢复交付。状态错误时阻断。
2. MCP 不可用才调用 CLI `workspace-status`；不得读取控制器源码或 memory 文件反推格式。
3. 没有可恢复交付时，仅开发新需求才进入流程；只读分析、审查或问答不创建运行包。

恢复优先使用精确 ID、数据库焦点或唯一候选；多个候选才请求选择。Markdown 缺失时使用 `refresh_projections`。

## 推进流程

1. 新需求读取规划类 references，选择最浅层级并形成完整 schema v3 树；通过 MCP 结构化参数调用 `prepare_hierarchy`，仅 CLI fallback 使用 stdin。
2. 展示返回的开发方案和图入口，说明范围、契约、依赖、测试与失败路由。每次确认提示都必须同时展示 `active` 和 `manual` 两种开发方式；修改方案时重新准备同一整树。
3. 用户明确同意方案并选择方式后，使用返回的 `hierarchyFingerprint` 一次调用 `freeze_hierarchy`；MCP 不传 `confirmed` 布尔参数。Claude Code 还须先满足 [claude-automation.md](references/claude-automation.md) 的 tool 级权限前置条件。
4. 冻结后每次迁移都重新查询 `graph_frontier`，完整消费 `actions` 与 `dispatchPlan`，不自行挑选 Task、排序或确定 Agent 数；`ADVANCE_GRAPH` 是租约硬过期后的确定性自动恢复动作，不请求人工重置。
5. `DISPATCH_TASK`：完整消费调度计划并稳定排队，但只在 worker 真正取得执行容量时调用 `dispatch_task`，让 claim 按实际开工即时创建。当前会话就是没有独立宿主适配器时的执行适配器，必须以 `nextWakeAt` 为最长等待时间消费到期的 `HEARTBEAT_TASK`，不能在长实现、长测试或等待子 Agent 时漏掉续租；心跳使用控制器的轻量增量投影，不应触发整工作区 Markdown 重建。
6. `DISPATCH_TASK`、`RUN_GATE`、`REQUEST_REVIEW`：对 action 的每个 required Skill 分别执行“宿主原生明确调用 → `record_skill_activation` → 完整流程 → `record_skill_conformance`”。DEVELOPMENT 在 `dispatch_task` 前绑定同一 owner/operation；成功迁移要求全部 PASS。再按 `evidenceContractRef` 获取模板并提交精确 `skillUsage`。Read/load/baseline/usage 自述不能替代；报告只显示 Graph 事件。`REQUEST_USER_CONFIRMATION` 不由 Skill 代替。
7. Task 工作完成后，最终总结前必须先按 `evidenceContractRefs.result` 查询当前 operation 的模板并提交 `task_result`，再继续消费 gate/review 动作；不得以“代码和测试已完成”代替 Graph 收尾。`ADVANCE_GRAPH`、`RETRY_NODE` 或其他租约失败按 frontier 自动路由；硬过期时先推进、重新查询、用新 operation 重新认领并提交既有工作结果，只有 `RETRY_EXHAUSTED`、契约变化或真实外部权限阻断才请求人工干预。Task gate 的 P0/P1 FAIL 必须回到 execution 修复、复测，不能无限重跑 gate。
8. 原契约漏列必要文件时用 `remediate_task`；契约或权限变化才回到人工评审。
9. manual 在当前窗口确认、冻结并输出一次交接；新窗口从 `graph_frontier` 恢复同一 graph run，不重新准备/冻结、选择方式或逐 Task 确认，自动开发、测试、修复、逐级门禁和恢复至 `WAITING_FOR_USER_CONFIRMATION`，停在 `REQUEST_USER_CONFIRMATION`。
10. 根 gate 和独立审查通过后请求最终用户确认；未确认时保持等待。

## 按动作读取

- 规划、拆树和冻结方案：[routing-profiles.md](references/routing-profiles.md)、[delivery-planning.md](references/delivery-planning.md)、[development-plan.md](references/development-plan.md)、[baselines.md](references/baselines.md)
- 执行与恢复：[workflow.md](references/workflow.md)、[graph-engineering.md](references/graph-engineering.md)、[development.md](references/development.md)、[parallel-development.md](references/parallel-development.md)、[registry-lifecycle.md](references/registry-lifecycle.md)
- gate、审查、最终确认和同契约补充文件：[acceptance.md](references/acceptance.md)、[validation-remediation.md](references/validation-remediation.md)
- 存储与传输：[task-registry.md](references/task-registry.md)、[registry-transactions.md](references/registry-transactions.md)、[tracking.md](references/tracking.md)、[stdin-transport.md](references/stdin-transport.md)
- 宿主：[claude-automation.md](references/claude-automation.md)、[codex-automation.md](references/codex-automation.md)；其他：[multi-workspace.md](references/multi-workspace.md)、[post-acceptance-feedback.md](references/post-acceptance-feedback.md)

只读取当前动作需要的一组及其中直接相关文件。
