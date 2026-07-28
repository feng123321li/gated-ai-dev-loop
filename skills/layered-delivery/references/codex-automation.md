# Codex required Skill 原生激活

## Plugin 与项目绑定

Codex Plugin 启动同一个本地 Python stdio MCP Server。Server 从可信的 `codex/sandbox-state-meta.sandboxCwd` 绑定当前任务工作区；业务工具不接受 `root`、`dogfood` 或通用 `confirmed` 参数。用户评审当前指纹方案并选择 `active` 或 `manual` 的回复，就是一次冻结确认；Agent 紧邻该回复调用 `freeze_hierarchy`，不再请求第二个工具批准。常规中段工具按 Plugin 默认策略执行，Graph 重建、Graph 取消、人工审查接受和最终用户确认继续 `prompt`。

Codex 可以执行由任意已接入 Plugin MCP 的 Agent 宿主创建并冻结的需求。冻结状态中的 `hostRuntime` 只记录方案创建宿主，不限制当前执行宿主；Plugin MCP 从当前 Codex sandbox metadata 形成实际执行宿主凭证。遇到其他宿主创建的 frozen graph 时直接从 `graph_frontier` 接续并记录统一 `HOST_NATIVE_SKILL`，不得重新 prepare/freeze、要求用户重新确认或再次输入 `$skill`。

## 每个 required Skill 都由执行适配器自动调用

frontier 中的 `requiredSkills` 是需求冻结的任意合法 catalog 名；控制器没有硬编码白名单，但 prepare 前已要求 Codex 分别提交宿主级 root 与项目级 project 的实际登记名称。缺失或疑似拼错时，控制器同时返回机器字段 `skillOptions` 和可直接展示的中文 `userPrompt`；Codex 必须优先展示 `userPrompt` 的标题、说明、带来源选项和兜底指引，让用户确认正确 Skill 或安装，不得直接输出技术结构或自动替换。用户批准整树并选择 active/manual 时已经一次授权这些 Skill；frontier action 是当前 Codex task/context 的自动调用指令，不是新的授权请求。执行适配器必须通过 Codex 原生 Skill 路径逐项激活、完整应用并记录凭证，不得暂停并要求用户再次输入 `$<skill-name>`、确认 Skill 或复制触发文本。隐式相关性、只读取 `SKILL.md`、文件 load、父 task 调用或只把名称写进提示都不能单独作为该执行 context 的激活。

每次由执行适配器自动完成原生激活后立即调用 `record_skill_activation`：

- `skill_name` 与冻结名称精确一致；
- `mechanism` 固定为 `HOST_NATIVE_SKILL`；
- `sessionId`、`executorId`、`executionId` 和 `nativeInvocationId` 使用当前 Codex task/session 与本次调用的宿主身份；
- DEVELOPMENT 的 executor/execution 必须与随后 `dispatch_task` 的 owner/operation 一致；
- 一个 native invocation ID 只能绑定一个 required Skill，不能复用一轮调用覆盖多个名称。

控制器返回 append-only Graph event hash 作为 `activationReceiptId`。完成 Skill 的完整流程后，由同一 Codex 执行宿主调用 `record_skill_conformance`，把非空命名检查及实际代码、diff、测试或审查产物证据绑定到该 receipt。成功 result/gate/review 要求当前 node attempt 的所有 required Skill 都为 `INVOKED + PASS`；`skillUsage` 仍须精确提交，但只作 artifact 审计，不能替代 activation/conformance。

Codex 宿主若无法提供独立的内部 Skill hook ID，执行适配器必须使用当前 task/session 暴露的稳定调用身份，为每个 Skill 生成互不复用的本次激活身份，并由紧邻自动原生调用的 MCP 记录形成 Graph 凭证；不得把普通 Read 伪装为 `HOST_NATIVE_SKILL`。如果当前宿主确实无法自动调用冻结 Skill，应记录具体 `BLOCKED` 并按 Graph 失败路由处理，不能改为索取用户二次触发。控制器机械验证 host 机制、名称、attempt、operation/owner 绑定、调用 ID 唯一性和符合性结构；任意 Skill 的语义结论必须来自针对实际产物的具体检查。
