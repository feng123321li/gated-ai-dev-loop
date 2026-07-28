# Codex required Skill 原生激活

## Plugin 与项目绑定

Codex Plugin 启动同一个本地 Python stdio MCP Server。Server 从可信的 `codex/sandbox-state-meta.sandboxCwd` 绑定当前任务工作区；业务工具不接受 `root`、`dogfood` 或通用 `confirmed` 参数。常规中段工具按 Plugin 默认策略执行，冻结、重建、取消、人工审查接受和最终用户确认继续 prompt。

## 每个 required Skill 都要明确调用

frontier 中的 `requiredSkills` 是需求冻结的任意 catalog 名，控制器没有 Skill 白名单。实际执行 DEVELOPMENT、GATE 或 FINAL_REVIEW 的 Codex task/context 必须对每个名称分别使用显式 `$<skill-name>` 原生触发；隐式相关性、读取 `SKILL.md`、文件 load、父 task 调用或只把名称写进提示都不能作为该执行 context 的激活。

每次原生触发后立即调用 `record_skill_activation`：

- `skill_name` 与冻结名称精确一致；
- `mechanism` 固定为 `CODEX_EXPLICIT_SKILL`；
- `sessionId`、`executorId`、`executionId` 和 `nativeInvocationId` 使用当前 Codex task/session 与本次调用的宿主身份；
- DEVELOPMENT 的 executor/execution 必须与随后 `dispatch_task` 的 owner/operation 一致；
- 一个 native invocation ID 只能绑定一个 required Skill，不能复用一轮调用覆盖多个名称。

控制器返回 append-only Graph event hash 作为 `activationReceiptId`。完成 Skill 的完整流程后，调用 `record_skill_conformance`，把非空命名检查及实际代码、diff、测试或审查产物证据绑定到该 receipt。成功 result/gate/review 要求当前 node attempt 的所有 required Skill 都为 `INVOKED + PASS`；`skillUsage` 仍须精确提交，但只作 artifact 审计，不能替代 activation/conformance。

Codex 宿主若无法提供独立的内部 Skill hook ID，执行适配器必须使用当前 task/session 暴露的稳定调用身份，并由紧邻原生 `$skill` 触发的 MCP 记录形成 Graph 凭证；不得把普通 Read 伪装为 `CODEX_EXPLICIT_SKILL`。控制器机械验证 host 机制、名称、attempt、operation/owner 绑定、调用 ID 唯一性和符合性结构；任意 Skill 的语义结论必须来自针对实际产物的具体检查。
