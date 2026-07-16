# Task 独立上下文开发

## 上下文生成

只有冻结、父链有效且 Task/Capability 依赖都 VERIFIED 的 Task 才能执行 `task-context`。生成的 `context-manifest.json` 包含 Task baseline、相关父契约、Capability 依赖证据、Task 依赖输出/证据、R/A、输入输出、测试 argv 和禁止事项；`inheritConversation` 固定为 false。

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

两者使用相同 baseline、scope、operationId、结果 schema 和 gate。不得把“确认 baseline”解释成选择 active，也不得默认选择执行方式。

## 结果接收

宿主用 claim 的 operationId 接收结果，核对真实 diff、写入归属和证据后记录 `IMPLEMENTED/BLOCKED` 并清除 claim。随后由宿主运行冻结测试和 Task gate。无法证明 Agent 是否写入时保持 claim/阻断并请求人工检查，不重复派遣。
