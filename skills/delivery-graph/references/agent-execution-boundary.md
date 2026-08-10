# 外层接收与 Loop 内 Worker 边界

Delivery Graph 是 SOP 与 Graph 控制面。它决定哪个 TASK/Review Loop 可以开始、
为其预留哪个可信宿主接收上下文，并治理 claim、租约、进度、终态和恢复；它不推荐、
选择或切换执行模型，也不编排 Loop 内部的 helper worker。

## 外层 receiver

所有 Graph claim 都只交给当前可信宿主 Adapter 认证的 receiver。Codex AUTOMATIC TASK 可由当前 Delivery 会话接收；Review 必须独立：

| 模式 | 授权来源 | receiver attestation |
|---|---|---|
| `INLINE_AUTO` | Codex `SessionStart` 对当前 Delivery task/session/worktree 的能力 | 无 reservation；只允许 `TASK_LOOP`，当前会话直接实现 |
| `AUTO` | `plan_dispatch_batch` 为当前 node/attempt 签发的短租约 assignment | 一次性证明绑定真实 child/parent/workspace，`reservation_id` 必须为非空且精确匹配 |
| `MANUAL` | 已启动的 manual Graph，或指定自动 TASK 的显式人工接管事件 | 一次性证明绑定相同的 child/parent/workspace，`reservation_id` 必须为 `NULL` |

三者共同进入同一 claim、项目 scope、operation、heartbeat、progress、pause、result 和 lease 校验。`INLINE_AUTO` 不是无认证直领：缺少当前 SessionStart capability 时 fail closed；它只能 claim TASK，Review 仍走 `AUTO` child。

AUTO assignment 还具有以下派遣约束：

- assignment 绑定 `hostAdapterId`、`receiverAgentId`、reservation、节点、attempt、
  receiving context 和 `modelPolicy=CURRENT_HOST_INHERIT`。
- receiver 继承创建它的当前宿主模型与默认推理设置。`plan_dispatch_batch` 不接收
  model inventory、模型偏好、reasoning class 或 effort，也不返回模型建议。
- Controller 不把模型或 effort 写入派遣决策指纹，不提供路由调整窗口，也不允许
  claim 后原地更换 receiver 身份。
- 需要更换外层 receiver 时，必须先按协议 pause，或等待失联租约被回收；随后由
  新 attempt、reservation 和独立 receiver 重新领取。旧 operation 立即失效。

Codex、Claude Code 或其他宿主只有在 Plugin 存在对应可信外层 Adapter、能通过
宿主原生生命周期事件证明 child/parent/workspace（AUTO 另含 reservation）关系，并能为后续控制面操作持续
证明同一 receiver 身份时，才能领取 Loop。PATH 中存在 CLI、普通 helper、
外部进程或本机 Profile 都不构成这种权限。

Codex `SessionStart` 只为顶层 Delivery task 发放 `DELIVERY_COORDINATOR` session capability；数据库只保存哈希，能力绑定精确 session/worktree、自动轮换并有时效。它授权 `claim_current_task`、Review planning 和当前 receiver mutation，使 code-mode 不依赖当前宿主遗漏的 nested PreToolUse。Subagent 的普通 `SessionStart` 不发 coordinator capability；AUTO Review 的 `SubagentStart` 原子消费 reservation，并只为独立 child 发放 `LOOP_RECEIVER` capability，该能力只能变更已领取的 Review，不能领取 TASK 或规划下一批。安装或升级后必须审查并信任精确 Hook 定义；“始终信任”对当前哈希持久生效，Hook 内容变化后重新审查。执行权限和模型配置不替代 Hook trust。

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
  外层 receiver 时，才新增并验证一个可信宿主 Adapter。

内部 Worker 不是 Graph receiver。它们不得调用 `dispatch_loop`、`heartbeat_loop`、
`report_loop_progress`、`pause_loop`、`resume_loop` 或 `record_loop_result`，也不得接收
operation、attestation、reservation bearer。它们只把工作结果返回外层 receiver；
receiver 负责验证、整合并以自己的受信身份更新控制面。

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
仍由独立宿主原生 receiver MANUAL claim；Adapter PreToolUse Hook 为它签发
`reservation_id` 为 `NULL` 的一次性 receiver attestation，Controller 原子消费并绑定真实
child、parent、workspace 与 operation。后续 Review 使用当前可信宿主 Adapter 的独立
AUTO receiver。自动与手动模式都遵守相同的内部 Worker、mutation 和遥测规则。

## 容量与额度

Controller 只治理外层 receiver 的 reservation、claim 和跨 Delivery 并发。内部
Worker 的并发与模型成本由 Loop/宿主自行管理，不占用另一个 Graph receiver 槽位。
额度耗尽策略固定为 `PAUSE_AND_RESUME`：只有宿主提供结构化容量事实和真实
`resetAt` 时才暂停并安排一次恢复；不得静默换模型、换供应商或把内部 Worker 提升
为新的外层 receiver。
