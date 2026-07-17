# Task 独立上下文开发

## 上下文生成

只有开发评审已经按当前指纹冻结、开发方式已明确确认、`development-mode.json` 与 registry 快照及当前 Task baseline 指纹一致、实际父链有效且 Task/Capability 依赖都 VERIFIED 的 Task 才能生成上下文。正常流程使用 `dispatch-task` 原子写入 claim 并生成绑定 operationId 的 `context-manifest.json`；`task-context` 只用于调度前诊断或恢复。上下文包含 `gateLevel`、开发方式、operation、Task 的完整 `developmentPlan`、实际存在父级的协调开发计划与子契约、依赖证据、R/A、输入输出、测试 argv 和禁止事项；根 Task 的父契约和聚合依赖数组为空，`inheritConversation` 固定为 false。

`development-handoff.md` 是自包含、可直接粘贴到全新开发会话的提示词。正常流程中 `dispatch-task --json` 返回完全一致、绑定 operationId 的 `handoffPrompt`；`task-context --json` 只返回不得用于开工的未认领诊断预览。开发 Agent 不接收 Delivery 分析对话、Capability 讨论、其他 Task 对话或宿主隐式记忆。

## 开发 Agent 契约

开发 Agent 必须：

- 只实现一个冻结 Task；
- 只写 Task `developmentPlan.fileChanges` 中已经人工评审的精确路径；scope 是外层边界，不能把未计划的 scope 内文件当成已授权；
- 不改变 baseline、registry、进度投影或 `.git/**`；
- 不提交、推送、发布或改变外部状态；
- 运行允许的局部检查并报告真实事实；
- 返回 `IMPLEMENTED` 或 `BLOCKED`，不得报告 PASS。

输入、依赖或工作区不可访问时，在任何写入前 BLOCKED。

## Active 与 Manual

- `active`：宿主生成唯一 operationId，运行 `dispatch-task`，再创建一个全新隔离 Agent；
- `manual`：宿主生成唯一 operationId，立即运行 `dispatch-task --owner manual-user --operation <id> --json`，在回复中原样输出带 operationId 的 `handoffPrompt`，用户可直接复制到任意全新 Agent；文件链接只能作为补充。

Task baseline 冻结后状态固定为 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。宿主必须展示两种方式并等待用户明确选择，然后调用：

```text
python -X utf8 <skill-root>/scripts/hdg.py select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
```

成功后写入 `development-mode.json`：

```json
{
  "schemaVersion": 3,
  "taskId": "t-example",
  "baselineFingerprint": "<sha256>",
  "mode": "active",
  "confirmedBy": "user",
  "confirmedAt": "<ISO-8601>"
}
```

两者使用相同 baseline、gateLevel、scope、operationId、结果 schema 和 gate。不得把“确认 baseline”或没有指明方式的“确认”解释成选择 active/manual，也不得默认选择执行方式。选择前 `task-context`、`claim-task` 与 `dispatch-task` 必须返回 `WORK_ITEM_DEVELOPMENT_MODE_REQUIRED`；文件缺失、被改动或与 registry/baseline 不一致也必须拒绝。开发方式一旦绑定当前 baseline 就不能原地切换；Task baseline 修订或升层会删除旧选择并重新进入等待状态。

## 结果接收

宿主用 claim 的 operationId 接收结果，核对真实 diff、写入归属和证据后执行 `task-result`，记录 `IMPLEMENTED/BLOCKED` 并清除 claim。控制器随即生成用户可读报告；`IMPLEMENTED` 报告状态必须是“等待门禁验收”。随后宿主运行冻结测试，形成严格 gate evidence 并执行 `accept-item`。根工作项继续进行独立验收和用户确认；无法证明 Agent 是否写入时保持 claim/阻断并请求人工检查，不重复派遣，也不能把开发会话的 IMPLEMENTED 当作完成。
