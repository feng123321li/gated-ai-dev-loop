---
name: delivery-graph-review
description: "在独立 receiver 上下文中执行一个已分配的 TASK_REVIEW_LOOP、GROUP_REVIEW_LOOP 或 DELIVERY_REVIEW_LOOP，复用仍为 EXACT_MATCH 的上游证据，对当前层缺口与风险做定向验证，闭环 findings，并提交本层唯一结论。仅在 Delivery Graph assignment/receiverPrompt 指定 Review 时使用；不实现 TASK、不调度其他节点。"
allowed-tools:
  - mcp__plugin_delivery-graph_delivery-graph-receiver__loop_context
  - mcp__plugin_delivery-graph_delivery-graph-receiver__dispatch_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__heartbeat_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__report_loop_progress
  - mcp__plugin_delivery-graph_delivery-graph-receiver__pause_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__resume_loop
  - mcp__plugin_delivery-graph_delivery-graph-receiver__record_loop_result
---

# Delivery Graph Review Receiver

只审查 assignment 指定的一层 Review。独立性表示独立判断，不表示机械重跑全量；不得实现 TASK、读取 frontier、派遣其他 Loop，或把上游结果全文复制进 outcome。

## 接收与证据

1. 使用 assignment 的 reservation、decision fingerprint、独立 receiver context 和新 `operation_id` 调用 `dispatch_loop(AUTO)`；Review 不支持 MANUAL claim。同一 receiver 收到多轮 assignment 时，只使用最新一份的完整凭据组；禁止把新 reservation 与旧 decision fingerprint 或旧 attempt 混搭。
2. claim 后读取一次 `loop_context`，确认运行时验证的 `projectScopes`、冻结验收、上游结论和 `validationEvidenceIndex`。
3. `STANDARD` 在审查前 heartbeat，并在证据检查、缺口确认、验证开始/完成、findings rework 与最终判断等里程碑报告 progress。
   只有确认存在当前层验证缺口且命令预计超过 60 秒时，才按项目文件选择专用命令 worker，并先用 `expected_command_seconds` heartbeat 申请有上限租约；内部 worker 不接收任何控制面凭据。
4. 只自动复用 `PASSED + EXACT_MATCH` 且 scope 覆盖当前风险的上游证据。无关 workspace 编辑不使有界 scope 失效；对 `CHANGED/UNBOUND`、缺口、findings 和高风险 seam 定向复跑。
5. 影响范围无法界定、关键跨边界风险没有隔离检查，或冻结要求明确指定全量时才升级全量验证。

## 分层所有权

- TASK Review：冻结 TASK 验收点、局部行为、公共契约与定向回归，提交 `taskAcceptance`。
- GROUP Review：仅在配置时存在，只消费子 TASK/GROUP 的结论、证据引用、契约锚点与状态指纹，只验直接子项之间的 seam；不读取或接收源码 diff，不复查 TASK 内部实现，不默认重跑子模块测试或全量 Maven/Gradle build，提交 `groupIntegration`。
- Delivery Acceptance/Readiness：只验顶层需求覆盖、整体集成/E2E 证据、运行准备度和全局风险，提交 `deliveryReadiness`。
- 不复查下层已经关闭且证据仍有效的实现细节；不把 `upstreamLoopResults` 或下层 result body 复制进 outcome。

## Findings 与终态

- Actionable finding 留在当前 Review Loop 内：记录、推动定向 rework、复验，再作结论；不要把普通 Review 未通过提交为 `BLOCKED`。
- `SUCCEEDED` 只在当前层验收成立时提交，并仅保存 `validationDecision`、`reviewFindings`、本层唯一结论字段、有界 `verificationEvidence` 和必要引用。
- 真实依赖或权限阻塞才用 `BLOCKED`；必须改变冻结需求、拓扑、依赖、资源、project scope 或数据库契约时用 `REPLAN_REQUIRED`。
- 调用 `record_loop_result` 后结束本 receiver。最终业务确认由 planning Skill 与真实用户完成，不由 Review 代签。

完整分层验收规则见[分层验收说明](references/acceptance.md)。
