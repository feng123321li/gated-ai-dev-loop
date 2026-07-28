# Task 独立上下文开发

## 上下文生成

只有根级开发方案已经通过一次整树冻结、开发方式已在同一次确认中写入 SQLite、实际父链有效且 Task/Capability 依赖都 VERIFIED 的 Task 才能生成上下文。正常流程只在 worker 真正取得执行容量时使用 `dispatch_task`，在同一 SQLite 短事务中校验 READY、保存绑定唯一 operationId 的结构化上下文和 handoff，并在 Markdown handoff 写入成功后提交 claim。`task_context` 只返回调度前诊断或恢复预览，不写入开工工件。正式上下文继承需求根开发方式，并包含 `gateLevel`、operation、`leasePolicy`、Task 的完整 `developmentPlan`、祖先与本节点聚合后的 `requiredSkills`、`requiredSkillPolicy`、验证修正记录、`authorizedFileChanges`、实际存在父级的协调开发计划与子契约、依赖证据、R/A、输入输出、测试 argv、禁止事项和紧凑 `evidenceContractRefs`；完整 result/gate/remediation 模板由 `evidence_contract` 在真正需要时从 SQLite 单项读取，不复制进每个 Task 上下文或 handoff。根 Task 的父契约和聚合依赖数组为空，`inheritConversation` 固定为 false。

`requirement-handoff.md` 是 manual 根级需求的一次性交接提示词，冻结与投影刷新都会从 SQLite 重建；它列出完整树并要求接收会话消费 Graph 自动计算的调度计划，完成 dispatch、结果写回和逐级门禁。`development-handoff.md` 仍是 `dispatch_task` 后生成的单 Task 执行上下文，只在执行循环内部交给对应开发 Agent，不再要求人逐 Task 复制。`task_context` 只返回不得用于开工的未认领诊断预览。开发 Agent 不接收 Delivery 分析对话、Capability 讨论、其他 Task 对话或执行入口隐式记忆。

接收会话优先通过已连接的 Plugin MCP 调用 `graph_frontier` 恢复 graph run，并直接消费结构化 tool result；不得固化用户目录、Skill 安装位置或操作系统路径，也不得用临时 JSON 中转只读查询结果。只有 MCP 不可用时才从当前 Skill 元数据解析控制器入口、运行 `graph-frontier --json` 并处理 stdout/stderr。`task_context` 只用于诊断，正式开工上下文必须来自 Graph 计划后的 `dispatch_task`。

## 开发 Agent 契约

开发过程不冻结某个固定 Agent 数量、并发度或调度顺序；Graph 在每次状态迁移后根据依赖、范围冲突和当前 claim 自动重算 `dispatchPlan`。执行适配器必须消费完整计划，不能自行挑选 Task。交付证据仍必须满足：

- 每个 Task 的结果和 evidence 可独立归属；
- 实际改动只能位于 `authorizedFileChanges`：人工冻结文件加上控制器校验并追加审计的验证修正文件；需要改变冻结目标、契约、拓扑或外部权限时必须阻断；
- 不改变 SQLite、baseline、进度投影或 `.git/**`；
- 不提交、推送、发布或改变外部状态；
- 持续运行相关回归、修复失败并复测，报告真实事实；
- 用户冻结整树并选择 active/manual 时已经授权全部 `requiredSkills`。worker 取得执行容量后、`dispatch_task` 前，执行适配器对 frontier 中每个 DEVELOPMENT required Skill 分别使用当前宿主原生 Skill 入口自动调用并立即写入 `record_skill_activation`；统一记录 `HOST_NATIVE_SKILL`、实际执行宿主和当前 task/session 中互不复用的调用 ID。不得要求用户再次输入 `$skill` 或确认 Skill；Read、load、父会话调用或提示中出现名称都不能单独替代当前执行 context 的激活；
- 完整执行 Skill 后、`task_result` 前，用 `record_skill_conformance` 把命名检查和实际代码/diff/测试证据绑定到激活凭证；成功结果要求当前 node attempt 的每项 Skill 都是 `INVOKED + PASS`，同一原生调用 ID 不能覆盖多个 Skill；
- 返回 `IMPLEMENTED` 或 `BLOCKED`，不得报告 PASS。

输入、依赖或工作区不可访问时，在任何写入前 BLOCKED。

## Active 与 Manual

- `active`：冻结后由 Graph 自动推进。控制器计算 READY、安全并行集合、目标 Agent 数和稳定顺序；执行 Agent/适配器完整消费计划并自动调用冻结 Skill。运行能力变化只影响立即启动还是排队，不请求用户重新选择或确认 Skill。开发持续循环到相关回归和复测通过，或形成真实阻断。当前宿主是 Claude Code 时，必须先满足 `hostAutomation` 的 Auto 权限前置条件，再冻结和认领 Task。
- `manual`：当前规划会话不创建开发 Agent。`freeze_hierarchy` 在需求根生成一份 `requirement-handoff.md`，在 `handoffPrompt` 返回同样的完整内容，并通过 `handoffCommand` 返回可直接复制到新会话的简短指令。规划会话的首次最终回复必须按 `responseContract` 提供一个纯文本代码块，用户一次复制到任意全新 Agent 后即可接管需求。可以直接使用 `handoffCommand`，也可以生成覆盖 `requiredSemantics` 的语义等价文本，不要求逐字一致；完整交接与冻结方案链接放在代码块之后，不能只返回文件链接。方案创建宿主只作审计：Claude、Codex、Cursor 或其他 Agent CLI 可相互交接，接收会话不得因宿主变化重新 prepare/freeze。交接目标是 Claude Code 时，同时展示 `claudeCodeAutoHandoff` 中适合界面的 Auto 启动方式。接收会话成为 Graph 执行入口，读取 `dispatchPlan`、自动调用冻结 Skill、为每个计划 Task 生成唯一 operationId 并执行 `dispatch_task`。平台可并行时启动隔离子 Agent，容量不足时稳定排队并串行消费；不得挑选子集，也不得要求用户逐 Skill、逐 Task 再次回复或复制交接。

用户在评审 `development-plan.md` 时选择一次根级方式；Agent 使用同一次确认调用：

```text
python -X utf8 <skill-root>/scripts/hdg.py freeze-hierarchy --item <root-id> --expected-hierarchy <sha256> --development-mode active|manual --confirmed
```

冻结成功后只在 SQLite 的需求根记录中保存开发方式：

```json
{
  "schemaVersion": 3,
  "rootId": "c-example",
  "baselineFingerprint": "<sha256>",
  "mode": "active",
  "confirmedBy": "user",
  "confirmedAt": "<ISO-8601>"
}
```

全部后代 Task 继承同一方式，但仍各自使用独立 baseline、gateLevel、scope、operationId、结果和 gate。manual 的一次交接只移交整树执行责任，不会提前 claim 全部 Task；`dispatch_task` 仍在 Graph 判定 Task READY 并把它列入 `dispatchPlan` 时执行。冻结工具缺少明确方式时必须拒绝，不能默认选择；根级计划被改动或数据库中的层级/baseline 不一致也必须拒绝。方式一旦随当前需求树冻结就不能原地切换。目标 Agent 数、并发组、调度顺序和容量回退由 Graph 运行时计算，不进入开发方式记录、开发方案、baseline 或层级指纹。

## 结果接收

Graph 执行循环先完成每项原生激活，在 `dispatch_task` 得到绑定 claim 后完整执行 Skill；结束时先用 `record_skill_conformance` 写入真实检查，再调用 `evidence_contract` 取得绑定当前 claim/operationId 的两份 result 模板和冻结测试、有效授权、失败分类、required Skills 约束，最后以结构化 evidence 调用 `task_result`。控制器在同一 SQLite 写事务内同时核对 claim/operationId、租约、artifact，以及当前 attempt 中每个 required Skill 的 host 机制、executor/execution 绑定、独立原生调用凭证和符合性结果。`IMPLEMENTED` 要求全部 `INVOKED + PASS`，并且 `skillUsage` 精确覆盖 DEVELOPMENT 要求；`BLOCKED` 必须提供 failure，Skill 不可用时激活、符合性和 usage 都如实记录 `BLOCKED`。`development-review.md` 的“实际 Skill 原生调用与符合性”来自 Graph 事件，原有 Skill usage 表只展示 artifact 自述。Agent 完成 Task 结果后继续以相同流程执行 gate。只有 CLI fallback 使用 stdin。

验证阶段若发现原验收项所需文件漏列，当前 claim 必须先正常写回并释放，再由 Graph 执行循环按修正路由执行 `remediate_task`。控制器把补充文件加入下一次 context 的 `authorizedFileChanges`，原 Task 重新 READY 后再认领；开发 Agent 不自行编辑 baseline、计划或 SQLite，也不另起需求根。
