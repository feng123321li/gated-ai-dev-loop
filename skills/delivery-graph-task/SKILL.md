---
name: delivery-graph-task
description: "在独立 receiver 上下文中执行一个已分配的 TASK_LOOP：校验 reservation/operation 与 projectScopes，检查真实代码，形成可调整的内部计划，实施变更，运行覆盖影响范围的验证，持续 lease/progress，并提交可审计的标准结果。仅在 Delivery Graph assignment、manual action 或 receiverPrompt 指定 TASK 时使用；不用于规划、派遣其他节点或作 Review 结论。"
allowed-tools:
  - mcp__plugin_delivery-graph_delivery-graph-receiver__loop_context
  - mcp__plugin_delivery-graph_delivery-graph-receiver__dispatch_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__heartbeat_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__report_loop_progress
  - mcp__plugin_delivery-graph_delivery-graph-receiver__pause_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__resume_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__record_loop_result
---

# Delivery Graph TASK Receiver

只执行 assignment 指定的一个 `TASK_LOOP`。本 Skill 是外层 receiver，不是 primary coordinator；不得规划 Delivery、读取 frontier、派遣其他 Loop 或代替 Review。

## 接收顺序

1. 原样读取 assignment/manual action；始终保留 `rootId`、`nodeId`、receiver context 和新的 `operation_id`。AUTO 还必须保留 reservation、decision fingerprint、`agentProfileId`、`agentCatalogFingerprint` 与 `teamPlan`；MANUAL action 不携带这些 AUTO-only 字段。同一 receiver 收到多轮 assignment 时，只使用最新一份的完整凭据组；禁止把新 reservation 与旧 decision/profile fingerprint 或旧 attempt 混搭。
2. AUTO 使用 `dispatch_loop(AUTO)` 并提交一次性 reservation 与完整 Profile/Team 决策；MANUAL 省略 reservation、decision/Profile/Team fingerprint，但必须使用独立 receiver context、新 operation 和可信 Adapter。
3. claim 成功后，AUTO 先确认返回的 `agentProfileId`、catalog/team fingerprint 与 assignment 一致；MANUAL 只核对返回的 node/attempt、Agent、receiver context 与 operation。随后立即用精确 operation 调用 `heartbeat_loop`，早于 `loop_context` 解读及任何代码检查、文件检索、依赖分析或命令。首次返回 `leaseRenewed=false / NOT_REQUIRED` 只表示尚未进入续租阈值：保留原 `leaseExpiresAt`，不得停止本轮 heartbeat 计划。
4. 随后读取 claim 响应中已经返回的 Loop context；确需刷新时再调用一次 `loop_context`。至少存在一个运行时验证过的 `projectScopes`；只访问其中授权的路径，不创建、切换或 checkout Git 分支。
5. 检查真实代码、依赖、数据流和外部契约，形成 Loop 内部计划。冻结 payload 给出方向与明确约束，不是完整实现说明；必要细节由 TASK 自己推导。
6. 用户 `skillHints` 在当前阶段适用且宿主可用时原生触发；只有阶段不适用或宿主不可用才跳过，不伪造已使用。不得把 Skill 默认示例、命名或实现偏好升级为冻结需求事实。
7. 实施最小、完整的变更；actionable 的实现、测试或代码审查发现留在本 Loop 内修复和复验。

## 专用 Team

- 外层 receiver 是唯一 owner，始终自己保持 heartbeat、汇总证据并提交最终结果。`teamPlan.helpers` 只是允许使用的专用辅助角色，不是必须全部启动的固定流水线。
- `codebase-researcher` 只做只读定位与依赖追踪；`test-runner` 只规划/执行有界验证并回传摘要；`result-checker` 在提交前按验收、证据与结果契约检查漏项。
- helper 只获得完成其子任务所需的最小代码上下文；不得获得 reservation、operation、decision fingerprint 或 receiver MCP 生命周期工具。owner 必须核验 helper 结果，不能把建议原样当成权威 outcome。

## 验证与结果

- 先界定 `result.affectedScopes`。`paths` 使用字面量仓库相对路径，并覆盖相关依赖和契约锚点。
- 运行覆盖该范围的测试、构建、静态检查或契约检查；在 `verificationEvidence` 记录命令摘要、scope、结果和必要说明。不要宣称未运行的验证。
- 长测试/构建先估算耗时并优先缩小命令范围（单模块、指定测试类、离线依赖解析）。按项目文件选择命令 worker：`pom.xml/.mvn/mvnw` 使用 Maven，Gradle wrapper 使用 Gradle，其他语言按 lockfile/module manifest 选择；首次依赖预热、install 或预计超过 60 秒的命令必须交给不持有控制面凭据的内部 worker/非阻塞监控，并先用 `expected_command_seconds` heartbeat 申请有上限的命令租约。测试前后都报告进度。
- 整文件 Write、大 patch、批量编辑与其他宿主 tool call 同样先估算耗时。既有大文件不得为方便而单次整体重写；优先拆成可审查的语义小 patch，每块之间 heartbeat。确实无法拆分且预计超过 60 秒时，必须在调用前用 `heartbeat_loop(expected_command_seconds=...)` 申请能覆盖整个原子调用与收尾的有界租约。
- claim 后到 `record_loop_result` 或显式释放 claim 之前，代码检查、文件检索、依赖分析、编辑、构建、测试和 rework 都是租约执行期；外层 receiver 按响应 `heartbeatDirective` 约每 60 秒 heartbeat。内部 worker 不接收 reservation、operation 或 MCP 凭据，primary 也不得代发 heartbeat。
- `report_loop_progress` 在代码检查、根因确认、编辑完成、测试开始/完成、rework 和最终验证等真实里程碑调用；progress 不续租。
- 数据库 TASK 只应用和验证冻结的 `databaseChanges[*].after`，不得在 Loop 内改设计。
- 成功后调用 `record_loop_result` 提交标准 outcome 和可审计证据。Controller 会绑定 workspace/evidence snapshots；不要直接写投影。
- 只有实际没有当前权限内的可行路径才提交 `BLOCKED`。需要改变冻结拓扑、依赖、资源、project scope、Review 契约或 databaseChanges 才提交 `REPLAN_REQUIRED`。
- reservation、workspace、fingerprint 或 operation 错误发生后立即停止仓库操作，把稳定错误码交回 coordinator。

详细执行边界见[TASK 执行说明](references/task-execution.md)。
