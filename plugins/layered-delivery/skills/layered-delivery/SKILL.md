---
name: layered-delivery
description: "治理或恢复分层软件交付。当工作区存在 `.layered-delivery/` 时接管现有 SQLite/Graph 运行；无治理状态时，按最浅合法层级规划并推进开发、门禁、审查和验收。"
allowed-tools:
  - mcp__plugin_layered-delivery_layered-delivery__*
---

# Layered Delivery

控制器治理交付；Graph 存状态并推进。

## 读取原则

- 首次只读取本文件；不得预读全部 references、源码、memory 或整树模板。
- 只使用 Plugin 启动的单一 stdio MCP Server，不用 Shell 包装。
- Server 固定项目根；工具不接受 `root`、`dogfood` 或 `confirmed`。
- 任意 Agent 在开发、认领 Task 或恢复 frozen graph 前，必须完成 Plugin MCP 启动、初始化握手和工具注册验证。MCP 未安装、未注册或未连接时立即阻断，报告 `PLUGIN_MCP_UNAVAILABLE`，不得编辑业务代码、启动 Shell/CLI 控制器，也不得开始或恢复治理写入。
- 以工具 schema 为准；仅真实超限时按 [mcp-transport.md](references/mcp-transport.md) 暂存 payloadRef，再调用原业务工具。

## 核心契约

- 只使用根 `Task`、`Capability → Task` 或 `Delivery → Capability → Task` 的最浅合法结构；Task 是执行叶子。
- 低风险单目标需求优先使用根 Task + LIGHT；允许简洁方案、定向测试和按需上下文，不复制通用说明或完整模板，但不省略独立 acceptance、精确文件授权、真实测试、P0/P1 和最终确认。
- 一个需求只有一个 `work-items/<root-id>/` 顶层目录；SQLite 是机器权威，Markdown 只是投影。
- 人只评审并冻结一次整树；Task、Agent 数、顺序、门禁和恢复由 Graph 决定。
- 每个 requirement 都必须有独立 acceptance；跨需求 acceptance 只能追加集成验收，不能代替任一需求自己的可观察通过条件。
- `scope` 按最小可用模块边界适当放宽，优先使用 `module/**`，不逐个复制计划文件，也不退化为全仓库 `**`；`developmentPlan.fileChanges` 仍冻结精确文件。Scope 重叠会约束并行，兄弟 Task 应尽量使用互不重叠的模块边界。
- `requiredSkills` 可省略/空；非空 catalog 名向后代继承。冻结整树并选 active/manual 即授权；适配器自动原生调用，以 `HOST_NATIVE_SKILL`、实际宿主和独立调用 ID 用 `record_skill_activation` 绑定 attempt，不得要求用户再次输入 `$skill` 或确认 Skill。方案宿主仅审计；跨 Agent 不重新 prepare/freeze。Read/load/提名不算激活，调用 ID 不得复用。
- 用户明确指定仅在开发过程中使用的 Skill，不作为需求分析输入：不预分析、不递归展开、不自动加入 `GATE`。登记前同时检查宿主级 `root` 与项目级 `project` catalog，并把两个来源的精确名称传给 `prepare_hierarchy.available_skills`；名称不存在或疑似拼错时停止准备，返回候选名称和来源供宿主提示用户选择，也可提示安装 Skill。宿主必须优先直接展示 `userPrompt` 的中文标题、说明、选项和兜底指引；`skillOptions` 仅用于机器处理，不得把原始技术字段直接甩给用户。存在时直接登记为仅含 `DEVELOPMENT` 的 required Skill，等实际 worker 开发时再原生调用。只有用户另行明确指定其他阶段时才进入对应阶段。
- 完整执行后由同一执行宿主用 `record_skill_conformance` 记录实际检查。成功 result/gate/review 要求逐项 `INVOKED + PASS`；`skillUsage` 不能替代 Graph 事件。验收报告只投影真实调用与符合性。细节按阶段读取 [development.md](references/development.md)、[acceptance.md](references/acceptance.md) 和宿主说明。
- 同一契约内的修正回到原 Task；不创建重复根或扩大文件授权。
- Task、聚合 gate、独立审查和用户确认都是显式图节点；只有最终用户确认后的 `COMPLETED` 表示完成。
- `active` 在冻结契约内自动推进；manual 接收会话也在一次交接后自动推进。两者都不逐 Task 确认或二次确认 Skill。
- Agent 不直接写 SQLite、baseline 或投影；最终确认和外部动作仍需用户授权。

## 选择入口

1. 先确认 Plugin MCP 工具已经注册并可调用，再调用 `workspace_status`，不按文件推断。`ACTIVE`：读取 [workflow.md](references/workflow.md) 和 [mcp-transport.md](references/mcp-transport.md)，再以 `graph_frontier` 恢复。`ABSENT/STAGING_ONLY`：没有可恢复交付。状态错误时阻断。
2. MCP 缺失、断连或工具注册失败时停止，不得读取控制器源码、memory 文件或治理文件反推格式，也不得切换到 CLI。
3. 没有可恢复交付时，仅开发新需求才进入流程；只读分析、审查或问答不创建运行包。

恢复优先使用精确 ID、数据库焦点或唯一候选；多个候选才请求选择。Markdown 缺失时使用 `refresh_projections`。

## 推进流程

1. 新需求读取规划类 references，选择最浅层级并形成完整 schema v3 树；分别取得宿主级 root 和当前 project 已注册 Skill catalog 的精确名称列表，通过 MCP 结构化参数以 `available_skills={"root":[...],"project":[...]}` 调用 `prepare_hierarchy`。缺失的 required Skill 必须优先展示 `userPrompt`，让用户按带“宿主级/项目级”来源的中文候选选择正确名称，或按兜底指引修正、安装；`skillOptions` 保留给宿主程序处理，不得自动改名、生成或冻结方案。
2. 展示返回的开发方案和图入口，说明范围、契约、依赖、测试与失败路由。每次确认提示都必须同时展示 `active` 和 `manual` 两种开发方式；修改方案时重新准备同一整树。
3. 用户明确同意方案并选择方式的回复，就是当前指纹方案的一次冻结确认。紧邻该回复使用返回的 `hierarchyFingerprint` 一次调用 `freeze_hierarchy`；不得再次询问、等待单独的工具批准或重放旧选择，MCP 也不传 `confirmed` 布尔参数。
4. 冻结后每次迁移都重新查询 `graph_frontier`，完整消费 `actions` 与 `dispatchPlan`，不自行挑选 Task、排序或确定 Agent 数；`ADVANCE_GRAPH` 是租约硬过期后的确定性自动恢复动作，不请求人工重置。
5. `DISPATCH_TASK`：完整消费调度计划并稳定排队，但只在 worker 真正取得执行容量时调用 `dispatch_task`，让 claim 按实际开工即时创建。当前会话就是没有独立宿主适配器时的执行适配器，必须以 `nextWakeAt` 为最长等待时间消费到期的 `HEARTBEAT_TASK`，不能在长实现、长测试或等待子 Agent 时漏掉续租；心跳使用控制器的轻量增量投影，不应触发整工作区 Markdown 重建。
6. `DISPATCH_TASK`、`RUN_GATE`、`REQUEST_REVIEW`：冻结 action 已授权；适配器对每个 required Skill 自动执行“原生调用 → `record_skill_activation` → 完整流程 → `record_skill_conformance`”，不得索取用户触发。DEVELOPMENT 在 `dispatch_task` 前绑定同一 owner/operation；成功迁移要求全部 PASS。再按 `evidenceContractRef` 获取模板并提交精确 `skillUsage`。Read/load/usage 自述不能替代；`REQUEST_USER_CONFIRMATION` 不由 Skill 代替。
7. Task 工作完成后，最终总结前必须先按 `evidenceContractRefs.result` 查询当前 operation 的模板并提交 `task_result`，再继续消费 gate/review 动作；不得以“代码和测试已完成”代替 Graph 收尾。`ADVANCE_GRAPH`、`RETRY_NODE` 或其他租约失败按 frontier 自动路由；硬过期时先推进、重新查询、用新 operation 重新认领并提交既有工作结果，只有 `RETRY_EXHAUSTED`、契约变化或真实外部权限阻断才请求人工干预。Task gate 的 P0/P1 FAIL 必须回到 execution 修复、复测，不能无限重跑 gate。
8. 原契约漏列必要文件时用 `remediate_task`；契约或权限变化才回到人工评审。
9. manual 在当前窗口确认、冻结并输出一次交接；新窗口从 `graph_frontier` 恢复同一 graph run，不重新准备/冻结、选择方式、逐 Task 确认或再次确认 required Skill，自动开发、测试、修复、逐级门禁和恢复至 `WAITING_FOR_USER_CONFIRMATION`，停在 `REQUEST_USER_CONFIRMATION`。
10. 根 gate 和独立审查通过后请求最终用户确认；未确认时保持等待。

## 按动作读取

- 规划、拆树和冻结方案：[routing-profiles.md](references/routing-profiles.md)、[delivery-planning.md](references/delivery-planning.md)、[development-plan.md](references/development-plan.md)、[baselines.md](references/baselines.md)
- 执行与恢复：[workflow.md](references/workflow.md)、[graph-engineering.md](references/graph-engineering.md)、[development.md](references/development.md)、[parallel-development.md](references/parallel-development.md)、[registry-lifecycle.md](references/registry-lifecycle.md)
- gate、审查、最终确认和同契约补充文件：[acceptance.md](references/acceptance.md)、[validation-remediation.md](references/validation-remediation.md)
- 存储与传输：[task-registry.md](references/task-registry.md)、[registry-transactions.md](references/registry-transactions.md)、[tracking.md](references/tracking.md)、[mcp-transport.md](references/mcp-transport.md)
- 宿主：[claude-automation.md](references/claude-automation.md)、[codex-automation.md](references/codex-automation.md)；其他：[multi-workspace.md](references/multi-workspace.md)、[post-acceptance-feedback.md](references/post-acceptance-feedback.md)

只读取当前动作需要的一组及其中直接相关文件。
