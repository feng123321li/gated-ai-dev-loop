# 执行快速路径

用于新冻结或恢复中的 graph run。SQLite/Graph 是权威；不要从 Markdown 猜状态。

## Frontier 循环

1. 调用 `graph_frontier`，默认使用紧凑响应。
2. 完整消费 `actions` 与 `dispatchPlan.dispatchTaskIds`；不自行挑 Task、改顺序或决定 Agent 数。容量不足时按原顺序排队，排队项不提前 claim。
3. 执行动作后优先消费返回的 `nextFrontier`；等待时携带最近的 `frontierRevision`，`unchanged=true` 时不重复读上下文。
4. 仅诊断异常时请求 `response_mode="full"` 和 blocked details。

动作含义：

- `DISPATCH_TASK`：worker 真正取得容量后才创建唯一 operation 并派发。
- `RUN_GATE`：读取该 action 的 evidence contract 并提交门禁。
- `REQUEST_REVIEW`：使用独立的全新只读审查上下文。
- `REQUEST_USER_CONFIRMATION`：展示完整根验收结果并等待用户决定。
- `HEARTBEAT_TASK`：在 `nextWakeAt` 前续租；长实现、测试或等待 Agent 时也不能遗漏。
- `ADVANCE_GRAPH` / `RETRY_NODE` / `RESUME_TASK`：按 frontier 指定路线执行，不手工改状态。

## Task 与 required Skill

1. 对 frontier 当前阶段的每个 required Skill，通过当前宿主原生入口分别调用，并立即用唯一 native invocation ID 记录 `record_skill_activation`；`mechanism` 为 `HOST_NATIVE_SKILL`。
2. DEVELOPMENT 的 executor/execution 与随后 `dispatch_task` 的 owner/operation 保持一致。真正取得执行容量后再 dispatch；`task_context` 只作诊断预览，不能授权开工。
3. worker 只读取紧凑 context 和当前 Task 必需的 `humanArtifacts`。只改 `authorizedFileChanges`；`generatedFileRoots` 只允许新增并必须报告实际生成文件。
4. 完整应用 Skill 后，由同一执行宿主用 `record_skill_conformance` 把命名检查绑定到真实代码、diff、测试或审查产物。一个调用 ID 不得覆盖多个 Skill。
5. 用 action 的 `evidenceContractRef` 调用 `evidence_contract`，再提交 `task_result`。成功结果要求每项 Skill `INVOKED + PASS`；`skillUsage` 仍需填写但不能替代事件。
6. Task 结果为 `IMPLEMENTED` 只表示等待 gate；继续消费 frontier，不输出完成总结。

## 精简 evidence

默认提交 `{"evidenceDelta": {...}}`，控制器扩展并只保存完整 canonical schema v3 artifact：

- Task test 使用 `commandIndex`，控制器补齐冻结的 `argv`。
- Gate acceptance 只提交 `id/status/evidence`，控制器补齐冻结的 requirement trace。
- Gate test 使用 `commandIndex`，控制器补齐冻结的 `argv`。
- `generatedFiles` 列出生成目录下实际新增的文件，并是 `changedFiles` 子集。

## 失败、恢复与修正

- 普通可重试失败、Gate P0/P1、租约硬过期和预算内重试按 frontier 回原 Task 修复、复测和重新门禁；operation ID 不复用。
- 若目标、需求、验收、接口、数据、测试、拓扑和外部权限均不变，只是原验收所需精确文件漏列：释放 claim，按 remediation evidence contract 调用 `remediate_task`，追加精确文件授权并重跑受影响节点。独立审查或最终用户验收发现时分别使用对应 source；`USER_ACCEPTANCE` 只增量刷新受影响需求树。
- 若契约变化、需要新权限、重试耗尽或无法安全归属，则保持阻断并请求人工决定；不得创建重复根或自行扩大 scope。
- 只有 frontier/状态错误明确要求诊断时才按工具 schema 使用 `graph_status`、分页事件或 replay；重建运行快照必须取得用户确认，不能直接改事件或快照。
- MCP 断连后停止新改动。重连先 `workspace_status` 再 `graph_frontier`，以权威状态决定是否重放；响应未知的非幂等写不能盲目重试。
