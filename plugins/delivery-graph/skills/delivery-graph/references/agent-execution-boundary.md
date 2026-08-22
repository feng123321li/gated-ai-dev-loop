# 外层 receiver 与控制面边界

Delivery Graph 只治理外层 receiver 的派遣、claim、租约、进度、终态和恢复。
它不选择执行模型、不接收推理档位，也不采集内部 Worker 遥测。

## 外层 receiver

| 执行模式 | TASK receiver | Review receiver |
|---|---|---|
| `AUTOMATIC` | `plan_dispatch_batch` 为 Ready TASK 创建 reservation；宿主创建独立 receiver，再以 `HOST_NATIVE`、reservation、decision fingerprint 和显式 `operation_id` 调用 `dispatch_loop(AUTO)` | 使用相同 AUTO 协议，但必须是与全部上游实现/Review 不同的独立接收上下文 |
| `MANUAL` | 已启动的 manual Graph 或经明确授权的自动 TASK 人工接管；独立 receiver 以 context 和 `operation_id` 调用 `dispatch_loop(MANUAL)` | TASK 完成后仍使用独立 AUTO receiver |

两种模式共同进入项目 scope、operation、heartbeat、progress、pause、result、lease
和资源锁校验。AUTO 必须先预留并核对决策指纹；MANUAL 只能领取允许人工执行的
TASK，且不创建 AUTO reservation。

workspace strategy 固定为 `CURRENT_WORKSPACE_SERIAL`。同一实际 checkout 一次只承载
一个 Delivery turn；已有 owner 时，已选择 AUTOMATIC 或 MANUAL 的后续 Delivery 都保持排队，直到 commit、clean、
HEAD 与 receiver/reservation 释放条件全部满足。现有 linked checkout 仍只是普通当前
workspace，不会自动创建新 worktree。

AUTO assignment 绑定宿主 Adapter、receiver Agent、专用 `agentProfileId`、版本化 catalog/team
fingerprint、reservation、节点、attempt 和 decision fingerprint。MANUAL action 只绑定节点、attempt、Agent、receiver context 与 operation，不携带 AUTO Profile/Team 或 reservation 决策字段。非空 `receiverPrompt`
与 `teamPlan` 必须原样传给 receiver。宿主 Agent 身份负责可信执行边界，profile 负责专业分工，
二者不能互相替代。Controller 不分析 Loop payload 来改变路由，也不从 receiver 输出推断
额外调度属性。

每次 claim 返回的 `operation_id` 是该 attempt 后续 mutation 的 bearer。receiver 必须
显式携带并保密，不能复制给 helper、日志、进度、result 或用户消息。需要更换外层
receiver 时，先显式暂停并恢复，或等待租约回收，再以新 context、operation 以及 AUTO
reservation 重新领取；旧 operation 立即失效。

## Loop 内部实现

receiver 取得冻结输入后，按版本化 Agent Profile Catalog 自主管理实现、测试、复核和
必要的内部协作。`teamPlan.owner` 是唯一外层 receiver；`teamPlan.helpers` 是可选的专用
内部角色，不是额外 Graph Loop。内部 helper
不是 Graph receiver，不得调用 `dispatch_loop`、`heartbeat_loop`、
`report_loop_progress`、`pause_loop`、`resume_loop` 或 `record_loop_result`，也不得接收
operation 或 reservation bearer。它们只把结果返回 receiver，由 receiver 验证、整合
并更新控制面。

Graph outcome 只保存 Loop 业务结果、验证证据和 Controller 采集的 workspace/evidence
快照；不保存 helper 身份、模型、推理档位或成本信息。进度面板只展示外层 receiver、
attempt、阶段、摘要、测试、心跳和租约健康。

## 暂停与恢复

`pause_loop` 只接受当前 live claim 的 `root_id`、`node_id` 和 `operation_id`，保留同一
attempt 并释放租约。恢复必须由独立接收上下文显式调用 `resume_loop`，随后重新读取
frontier、`loop_context` 并用新的 operation 领取。Controller 不判断供应商额度、不保存
reset 时间、不创建定时唤醒，也不自动切换 Adapter。

## 手动 Graph

手动 handoff 在选择时绑定当前 workspace 的串行队列，但在 `start_manual_handoff` 前不绑定
receiver、不创建 Run。启动后 TASK 由独立宿主 receiver MANUAL claim；后续 Review 仍使用
独立 AUTO receiver。两种模式遵守同一 queue、mutation、lease、资源锁和结果边界。
