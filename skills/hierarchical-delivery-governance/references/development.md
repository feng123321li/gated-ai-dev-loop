# Task 独立上下文开发

## 上下文生成

只有根级开发方案已经通过一次整树冻结、开发方式已在同一次确认中记录、根级 `development-mode.json` 与 registry 快照及根 baseline 指纹一致、实际父链有效且 Task/Capability 依赖都 VERIFIED 的 Task 才能生成上下文。正常流程使用 `dispatch-task` 在同一受锁调度事务中校验 READY、准备绑定 operationId 的 `context-manifest.json` 和 handoff，并在工件写入成功后提交 claim；`task-context` 只返回调度前诊断或恢复预览，不写入开工工件。上下文继承需求根开发方式，并包含 `gateLevel`、operation、Task 的完整 `developmentPlan`、实际存在父级的协调开发计划与子契约、依赖证据、R/A、输入输出、测试 argv 和禁止事项；根 Task 的父契约和聚合依赖数组为空，`inheritConversation` 固定为 false。

`development-handoff.md` 是自包含、可直接粘贴到全新开发会话的提示词。正常流程中 `dispatch-task --json` 返回完全一致、绑定 operationId 的 `handoffPrompt`；`task-context --json` 只返回不得用于开工的未认领诊断预览。开发 Agent 不接收 Delivery 分析对话、Capability 讨论、其他 Task 对话或宿主隐式记忆。

## 开发 Agent 契约

开发过程不冻结 Agent 数量、并发度、调度顺序或内部实现循环。交付证据仍必须满足：

- 每个 Task 的结果和 evidence 可独立归属；
- 实际改动仍在人工评审过的目标、scope 和安全授权内；需要改变冻结需求或扩大权限时必须阻断；
- 不改变 baseline、registry、进度投影或 `.git/**`；
- 不提交、推送、发布或改变外部状态；
- 持续运行相关回归、修复失败并复测，报告真实事实；
- 返回 `IMPLEMENTED` 或 `BLOCKED`，不得报告 PASS。

输入、依赖或工作区不可访问时，在任何写入前 BLOCKED。

## Active 与 Manual

- `active`：冻结后由 Agent 自动推进。Agent 可以使用多个隔离子 Agent、单个开发 Agent 或当前 Agent，根据依赖、范围冲突和运行能力自主并行或串行；能力变化时自动调整，不请求用户重新选择。开发持续循环到相关回归和复测通过，或形成真实阻断。
- `manual`：不创建开发 Agent。宿主为用户选定的 READY Task 生成唯一 operationId，运行 `dispatch-task --owner manual-user --operation <id> --json`，在回复中原样输出带 operationId 的 `handoffPrompt`，用户可复制到任意全新 Agent；文件链接只能作为补充。

用户在评审 `development-plan.md` 时选择一次根级方式；Agent 使用同一次确认调用：

```text
python -X utf8 <skill-root>/scripts/hdg.py freeze-hierarchy --item <root-id> --expected-hierarchy <sha256> --development-mode active|manual --confirmed
```

冻结成功后只在需求根写入 `development-mode.json`：

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

全部后代 Task 继承同一方式，但仍各自使用独立 baseline、gateLevel、scope、operationId、结果和 gate。冻结命令缺少明确方式时必须拒绝，不能默认选择；根级文件缺失、被改动或与 registry/baseline 不一致也必须拒绝。方式一旦随当前需求树冻结就不能原地切换。`development-mode.json` 只保存 active/manual 选择；子 Agent 数量、并发度、调度顺序和回退策略是运行时决策，不进入该文件、开发方案、baseline 或层级指纹。

## 结果接收

宿主用 claim 的 operationId 接收结果，核对真实 diff、写入归属和证据后执行 `task-result`，记录 `IMPLEMENTED/BLOCKED` 并清除 claim。控制器随即生成 `development-review.json/md`，对照冻结计划展示实际改动、接口、回归测试、复测和偏差；`IMPLEMENTED` 只表示等待门禁。Agent 应先修复回归失败并完成复测，再形成严格 gate evidence、执行 `accept-item` 并生成验收报告。根工作项通过聚合门禁后向用户提交交付，由用户人工验收和最终确认；开发会话的 IMPLEMENTED 不能当作完成。
