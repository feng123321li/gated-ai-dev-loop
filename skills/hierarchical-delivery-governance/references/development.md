# Task 独立上下文开发

## 上下文生成

只有开发方式已明确确认、`development-mode.json` 与 registry 快照及当前 Task baseline 指纹一致、实际父链有效且 Task/Capability 依赖都 VERIFIED 的 Task 才能执行 `task-context`。生成的 `context-manifest.json` 包含开发方式、Task baseline、实际存在的父契约、Capability 依赖证据、Task 依赖输出/证据、R/A、输入输出、测试 argv 和禁止事项；根 Task 的父契约和聚合依赖数组为空，`inheritConversation` 固定为 false。

`development-handoff.md` 是人可读交接。开发 Agent 不接收 Delivery 分析对话、Capability 讨论、其他 Task 对话或宿主隐式记忆。

## 开发 Agent 契约

开发 Agent 必须：

- 只实现一个冻结 Task；
- 只写 Task scope 内路径；
- 不改变 baseline、registry、进度投影或 `.git/**`；
- 不提交、推送、发布或改变外部状态；
- 运行允许的局部检查并报告真实事实；
- 返回 `IMPLEMENTED` 或 `BLOCKED`，不得报告 PASS。

输入、依赖或工作区不可访问时，在任何写入前 BLOCKED。

## Active 与 Manual

- `active`：宿主创建一个全新隔离 Agent，并在派遣前 claim Task；
- `manual`：宿主输出同一份 Task handoff，用户交给任意全新 Agent。

Task baseline 冻结后状态固定为 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。宿主必须展示两种方式并等待用户明确选择，然后调用：

```text
node <skill-root>/scripts/hdg.mjs select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
```

成功后写入 `development-mode.json`：

```json
{
  "schemaVersion": 1,
  "taskId": "t-example",
  "baselineFingerprint": "<sha256>",
  "mode": "active",
  "confirmedBy": "user",
  "confirmedAt": "<ISO-8601>"
}
```

两者使用相同 baseline、scope、operationId、结果 schema 和 gate。不得把“确认 baseline”或没有指明方式的“确认”解释成选择 active/manual，也不得默认选择执行方式。选择前 `task-context` 与 `claim-task` 必须返回 `WORK_ITEM_DEVELOPMENT_MODE_REQUIRED`；文件缺失、被改动或与 registry/baseline 不一致也必须拒绝。开发方式一旦绑定当前 baseline 就不能原地切换；Task baseline 修订会删除旧选择并重新进入等待状态。Skill 内置控制器不可用时保持阻断并报告安装损坏，不在对话内模拟成功，也不把全局 CLI 当必装依赖。

## 结果接收

宿主用 claim 的 operationId 接收结果，核对真实 diff、写入归属和证据后记录 `IMPLEMENTED/BLOCKED` 并清除 claim。随后由宿主运行冻结测试和 Task gate。无法证明 Agent 是否写入时保持 claim/阻断并请求人工检查，不重复派遣。
