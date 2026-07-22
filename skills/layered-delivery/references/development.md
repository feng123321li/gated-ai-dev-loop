# Task 独立上下文开发

## 上下文生成

只有根级开发方案已经通过一次整树冻结、开发方式已在同一次确认中写入 SQLite、实际父链有效且 Task/Capability 依赖都 VERIFIED 的 Task 才能生成上下文。正常流程使用 `dispatch-task` 在同一 SQLite 短事务中校验 READY、保存绑定 operationId 的结构化上下文和 handoff，并在 Markdown handoff 写入成功后提交 claim。`task-context` 只返回调度前诊断或恢复预览，不写入开工工件。上下文继承需求根开发方式，并包含 `gateLevel`、operation、Task 的完整 `developmentPlan`、验证修正记录、`authorizedFileChanges`、实际存在父级的协调开发计划与子契约、依赖证据、R/A、输入输出、测试 argv 和禁止事项；根 Task 的父契约和聚合依赖数组为空，`inheritConversation` 固定为 false。

`requirement-handoff.md` 是 manual 根级需求的一次性交接提示词，冻结与投影刷新都会从 SQLite 重建；它列出完整树并要求接收会话自行循环 READY、dispatch、结果写回和逐级门禁。`development-handoff.md` 仍是 `dispatch-task` 后生成的单 Task 执行上下文，只在执行宿主内部交给对应开发 Agent，不再要求人逐 Task 复制。`task-context --json` 只返回不得用于开工的未认领诊断预览。开发 Agent 不接收 Delivery 分析对话、Capability 讨论、其他 Task 对话或宿主隐式记忆。

## 开发 Agent 契约

开发过程不冻结 Agent 数量、并发度、调度顺序或内部实现循环。交付证据仍必须满足：

- 每个 Task 的结果和 evidence 可独立归属；
- 实际改动只能位于 `authorizedFileChanges`：人工冻结文件加上控制器校验并追加审计的验证修正文件；需要改变冻结目标、契约、拓扑或外部权限时必须阻断；
- 不改变 SQLite、baseline、进度投影或 `.git/**`；
- 不提交、推送、发布或改变外部状态；
- 持续运行相关回归、修复失败并复测，报告真实事实；
- 返回 `IMPLEMENTED` 或 `BLOCKED`，不得报告 PASS。

输入、依赖或工作区不可访问时，在任何写入前 BLOCKED。

## Active 与 Manual

- `active`：冻结后由 Agent 自动推进。Agent 可以使用多个隔离子 Agent、单个开发 Agent 或当前 Agent，根据依赖、范围冲突和运行能力自主并行或串行；能力变化时自动调整，不请求用户重新选择。开发持续循环到相关回归和复测通过，或形成真实阻断。
- `manual`：当前规划会话不创建开发 Agent。`freeze-hierarchy` 在需求根生成一份 `requirement-handoff.md`，在 `handoffPrompt` 返回同样的完整内容，并通过 `handoffCommand` 返回可直接复制到新会话的简短指令。规划会话的最终回复必须用纯文本代码块展示 `handoffCommand`，完整交接与冻结方案链接放在代码块之后；不能只返回文件链接。用户一次复制短指令到任意全新 Agent 后，接收会话成为整树执行宿主，自主计算 READY、为每个实际开工 Task 生成唯一 operationId 并执行 `dispatch-task`。它可安全并行时使用隔离子 Agent，否则自动串行；不得要求用户逐 Task 再次回复或复制交接。

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

全部后代 Task 继承同一方式，但仍各自使用独立 baseline、gateLevel、scope、operationId、结果和 gate。manual 的一次交接只移交整树执行责任，不会提前 claim 全部 Task；`dispatch-task` 仍在每个 Task 真正 READY 且准备开工时执行。冻结命令缺少明确方式时必须拒绝，不能默认选择；根级计划被改动或数据库中的层级/baseline 不一致也必须拒绝。方式一旦随当前需求树冻结就不能原地切换。子 Agent 数量、并发度、调度顺序和回退策略是运行时决策，不进入开发方式记录、开发方案、baseline 或层级指纹。

## 结果接收

宿主用 claim 的 operationId 接收完整结果 artifact，并以 `--evidence -` 从 stdin 直接执行 `task-result`。控制器在同一 SQLite 写事务内核对当前 claim、operationId、未过期租约和 artifact，计算规范 JSON 摘要，记录 `IMPLEMENTED/BLOCKED`、artifact 与摘要并清除 claim；不创建临时 evidence 文件。`IMPLEMENTED` 的 `failure` 必须为 `null`；`BLOCKED` 必须提供 `failure.class/code/summary`，让控制器在同一事务中决定自动重试、尝试耗尽、同合同修正、合同评审、外部授权或人工干预。控制器随即生成 `development-review.md`，对照冻结计划展示实际改动、接口、回归测试、复测和偏差；`IMPLEMENTED` 只表示等待门禁。Agent 应先修复回归失败并完成复测，再以相同方式提交严格 gate artifact、执行 `accept-item` 并生成验收报告。根工作项通过聚合门禁后向用户提交交付，由用户人工验收和最终确认；开发会话的 IMPLEMENTED 不能当作完成。

验证阶段若发现原验收项所需文件漏列，当前 claim 必须先正常写回并释放，再由宿主执行 `remediate-task`。控制器把补充文件加入下一次 context 的 `authorizedFileChanges`，原 Task 重新 READY 后再认领；开发 Agent 不自行编辑 baseline、计划或 SQLite，也不另起需求根。
