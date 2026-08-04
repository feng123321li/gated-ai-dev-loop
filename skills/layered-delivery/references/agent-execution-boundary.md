# 外层接收与 Loop 内 Worker 边界

Layered Delivery 是 SOP 与 Graph 控制面。它决定哪个 TASK/Review Loop 可以开始、
为其预留哪个可信宿主接收上下文，并治理 claim、租约、进度、终态和恢复；它不推荐、
选择或切换执行模型，也不编排 Loop 内部的 helper worker。

## 外层 receiver

自动派遣只创建当前可信宿主 Adapter 的独立 receiver：

- assignment 绑定 `hostAdapterId`、`receiverAgentId`、reservation、节点、attempt、
  receiving context 和 `modelPolicy=CURRENT_HOST_INHERIT`。
- receiver 继承创建它的当前宿主模型与默认推理设置。`plan_dispatch_batch` 不接收
  model inventory、模型偏好、reasoning class 或 effort，也不返回模型建议。
- Controller 不把模型或 effort 写入派遣决策指纹，不提供路由调整窗口，也不允许
  claim 后原地更换 receiver 身份。
- 需要更换外层 receiver 时，必须先按协议 pause，或等待失联租约被回收；随后由
  新 attempt、reservation 和独立 receiver 重新领取。旧 operation 立即失效。

Codex、Claude Code 或其他宿主只有在 Plugin 存在对应可信外层 Adapter、能通过
宿主原生生命周期事件证明 child/parent/reservation 关系，并能为后续控制面操作持续
证明同一 receiver 身份时，才能自动领取 Loop。PATH 中存在 CLI、普通 helper、
外部进程或本机 Profile 都不构成这种权限。

## Loop 内部 Worker

receiver 取得冻结输入并完成首次独立 heartbeat 后，才可以按当前宿主能力与任务需要
自行创建内部 Worker。内部 Worker 可以是 Codex、Claude、Grok、DeepSeek 或其他
本机/远程执行 Agent，也可以使用不同模型和 reasoning effort。选择、成本控制、失败
升级、并发和内部协作都属于 Loop 实现细节：

- 普通实现可使用成本较低的 Worker；复杂诊断、关键审查或内部重试可按需升级。
- 宿主未暴露模型或 effort 时保持未知，不询问模型自报，也不从名称或输出推断。
- Layered Delivery 不解析 Worker inventory，不规定 tier，不比较供应商能力，也不把
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

## 手动 Graph

手动 handoff 在 `start_manual_handoff` 前不绑定工作区或 receiver。启动后，TASK
仍由独立外层 receiver MANUAL claim；后续 Review 使用当前可信宿主 Adapter 的独立
receiver。自动与手动模式都遵守相同的内部 Worker 边界和遥测规则。

## 容量与额度

Controller 只治理外层 receiver 的 reservation、claim 和跨 Delivery 并发。内部
Worker 的并发与模型成本由 Loop/宿主自行管理，不占用另一个 Graph receiver 槽位。
额度耗尽策略固定为 `PAUSE_AND_RESUME`：只有宿主提供结构化容量事实和真实
`resetAt` 时才暂停并安排一次恢复；不得静默换模型、换供应商或把内部 Worker 提升
为新的外层 receiver。
