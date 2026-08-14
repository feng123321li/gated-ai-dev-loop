# 外层接收与 Loop 内 Worker 边界

Delivery Graph 是 SOP 与 Graph 控制面。它决定哪个 TASK/Review Loop 可以开始、
为其预留哪个宿主接收上下文，并治理 claim、租约、进度、终态和恢复；它不推荐、
选择或切换执行模型，也不编排 Loop 内部的 helper worker。

## 外层 receiver

所有 Graph claim 都只交给当前宿主 Adapter 创建或指定的 receiver。Review 必须按编排规则使用与上游实现不同的独立接收上下文：

| 执行模式 | TASK receiver | Review receiver |
|---|---|---|
| `AUTOMATIC` | `plan_dispatch_batch` 为 Ready TASK 签发短租约 assignment；宿主立即创建独立 child，child 以 `HOST_NATIVE` transport、reservation、decision fingerprint 和显式 `operation_id` 调用 `dispatch_loop(AUTO)` | 与 TASK 相同的统一 AUTO 协议，但宿主必须创建与全部上游实现/Review 不同的独立 child |
| `MANUAL` | 已启动的 manual Graph 或指定自动 TASK 的显式人工接管；独立 receiver 以显式 context 和 `operation_id` 调用 `dispatch_loop(MANUAL)`，不带 AUTO reservation、decision fingerprint 或 transport | TASK 完成后仍使用与 `AUTOMATIC` 相同的独立 AUTO receiver |

对用户公开的执行模式始终只有 `AUTOMATIC` 和 `MANUAL`。两种模式共同进入相同的项目 scope、operation、heartbeat、progress、pause、result、lease 和资源锁校验；差异仅在 claim 输入。AUTO 必须先预留并核对决策指纹，MANUAL 必须来自允许人工领取的 TASK 状态且不创建 AUTO reservation。

workspace strategy 不改变 receiver 身份协议，并且固定为
`CURRENT_WORKSPACE_SERIAL`：同一实际 checkout 可绑定多个 Delivery 的控制状态，
但一次只承载一个 Delivery 的 receiver，每个 Delivery 使用独立分支。已有 owner 时，
只有已选择 `AUTOMATIC` 的后续 Delivery 标记为 `QUEUED`；前一个 Delivery 进入 Run 终态
或最终用户确认边界，并形成可验证业务 commit、working tree/index clean、HEAD 与冻结
binding 一致且所有 receiver/reservation 安全释放后，
宿主才消费已授权的 stash/create-or-switch/resume 准备。手动冻结 Delivery 持久化为
`HANDOFF_READY`，不加入自动队列；接收方显式启动时才尝试取得 turn。workspace、
`resourceClaims` 冲突、owner dirty、未合并或 HEAD 漂移都保持等待。现有 linked checkout
也只作为普通 current workspace，不自动创建新 worktree，也不允许跨 Delivery 并行。

AUTOMATIC assignment 还具有以下派遣约束：

- assignment 带有非空 `skillHints/receiverPrompt` 时，宿主把提示词原样放入新 child 的初始输入。用户明确指定的 Hint 对当前 Loop 适用且 catalog 在当前宿主可用时，receiver 应在相应阶段使用原生 Skill 入口；实现类 Skill 多数在 TASK。只有阶段不适用或宿主不可用才跳过，不形成 claim 或 Controller 成功门禁。
- assignment 绑定 `hostAdapterId`、`receiverAgentId`、reservation、节点、attempt、
  decision fingerprint 和 `modelPolicy=CURRENT_HOST_INHERIT`。
- receiver 继承创建它的当前宿主模型与默认推理设置。`plan_dispatch_batch` 不接收
  model inventory、模型偏好、reasoning class 或 effort，也不返回模型建议。
- Controller 不把模型或 effort 写入派遣决策指纹，不提供路由调整窗口，也不允许
  claim 后原地更换 receiver 身份。
- 需要更换外层 receiver 时，必须先按协议 pause/resume，或等待失联租约被回收；随后以
  新 receiving context/operation 重新领取，AUTO 另取新 reservation/decision。旧 operation 立即失效。

Codex、Claude Code 或其他宿主只有在 Plugin 存在对应外层 Adapter，并能提供当前
workspace、原生 receiver 类型和独立 child 调度能力时，才可运行 AUTO。PATH 中存在 CLI、
普通 helper、外部进程或本机 Profile 都不构成这种能力。每次 claim 后返回的 `operation_id`
是该 attempt 后续 mutation 的 bearer；receiver 必须显式传递并保密，不能复制给 Worker、
日志、进度、result 或用户消息。

Controller 只看到 Adapter 提供的 workspace、receiver 类型和 assignment 数据，无法以
密码学方式证明真实 parent-child 关系、receiver 身份延续或 reviewer 独立性；这些边界由
宿主 Adapter 的 workspace 映射、独立 child 创建和编排规则承担。Controller 仍强制检查
reservation、decision fingerprint、Graph attempt、workspace/Git/project scope、operation、
lease 和资源锁。敏感 MCP 调用是否执行由宿主自己的审批机制决定。

## Loop 内部 Worker

receiver 取得冻结输入并完成首次独立 heartbeat 后，才可以按当前宿主能力与任务需要
自行创建内部 Worker。内部 Worker 可以是 Codex、Claude、Grok、DeepSeek 或其他
本机/远程执行 Agent，也可以使用不同模型和 reasoning effort。选择、成本控制、失败
升级、并发和内部协作都属于 Loop 实现细节：

- 普通实现可使用成本较低的 Worker；复杂诊断、关键审查或内部重试可按需升级。
- 宿主未暴露模型或 effort 时保持未知，不询问模型自报，也不从名称或输出推断。
- Delivery Graph 不解析 Worker inventory，不规定 tier，不比较供应商能力，也不把
  内部 Worker 选择写进 Graph、reservation、claim、decision fingerprint 或授权。
- 新增 Worker 供应商不需要修改 Controller；只有要让该供应商成为可领取 Graph 的
  外层 receiver 时，才新增并验证一个对应宿主 Adapter。

内部 Worker 不是 Graph receiver。它们不得调用 `dispatch_loop`、`heartbeat_loop`、
`report_loop_progress`、`pause_loop`、`resume_loop` 或 `record_loop_result`，也不得接收
operation 或 AUTO reservation bearer。它们只把工作结果返回外层 receiver；
receiver 负责验证、整合并以自己的 operation 更新控制面。

## Worker 遥测

外层 receiver 在真实终态中可通过 `outcome.result.workerTelemetry` 按阶段报告内部
执行事实。推荐形状如下：

```json
{
  "status": "SUCCEEDED",
  "summary": "实现、测试和复核完成",
  "result": {
    "workerTelemetry": [
      {
        "phase": "implementation",
        "agent": "codex",
        "model": "gpt-5.6-terra",
        "reasoningEffort": "medium"
      },
      {
        "phase": "review",
        "agent": "claude",
        "model": "unreported",
        "reasoningEffort": "unreported"
      }
    ]
  }
}
```

约束如下：

- `phase` 是 Loop 自己定义的稳定阶段名；每个阶段分别报告 `agent`、`model` 和
  `reasoningEffort`。
- 无法从宿主权威观察时写字面值 `unreported`，不得猜测、补全或把默认配置冒充
  运行事实。
- 遥测由 receiver 报告，仅用于用户展示、成本分析和后续 Review，属于非权威信息；
  Controller 不据此授权、路由、重试、判断独立性或重写历史。
- 未使用内部 Worker 时可以省略 `workerTelemetry`；不得把外层 receiver 本身伪装
  成一个内部 Worker 记录来满足展示要求。
- 可选字段 `provenance`（`HOST_EVENT` / `HOST_TOOL_RESULT` / `WORKER_SELF_REPORT` / `LOCAL_CONFIG`）、`role`、`status`、`summary` 也可报告；宿主能权威观察时优先用 `HOST_EVENT` / `HOST_TOOL_RESULT`，而非 `WORKER_SELF_REPORT`。

宿主在 `dispatch_loop` 派遣后若权威观察到实际模型，可传入可选的 `actual_model_id` 作为只读展示证据；Controller 从不据此路由、授权、指纹或评估能力，与 `workerTelemetry` 同属非权威展示信息。

## 手动 Graph

手动 handoff 在 `start_manual_handoff` 前不绑定工作区或 receiver。启动后，TASK
仍由独立宿主原生 receiver MANUAL claim；receiver 显式提交 receiving context 与新的
`operation_id`，且不携带 AUTO reservation、decision fingerprint 或 transport。Controller
校验 manual run、TASK 状态、workspace/Git/project scope 后原子 claim。后续 Review 使用
当前宿主 Adapter 的独立 AUTO receiver。自动与手动模式都遵守相同的 mutation、lease、
资源锁、内部 Worker 和遥测规则。

## 容量与额度

Controller 只治理外层 receiver 的 reservation、claim、跨 Delivery 排队与容量。内部
Worker 的并发与模型成本由 Loop/宿主自行管理，不占用另一个 Graph receiver 槽位。
额度耗尽策略固定为 `PAUSE_AND_RESUME`：只有宿主提供结构化容量事实和真实
`resetAt` 时才暂停并安排一次恢复；不得静默换模型、换供应商或把内部 Worker 提升
为新的外层 receiver。
