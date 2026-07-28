---
name: layered-delivery
description: "治理或恢复分层软件交付。当工作区存在 `.layered-delivery/` 时接管现有 SQLite/Graph 运行；无治理状态时，按最浅合法层级规划并推进开发、门禁、审查和验收。"
allowed-tools:
  - mcp__plugin_layered-delivery_layered-delivery__*
---

# Layered Delivery

控制器保存契约和状态，Graph 决定运行方向。

## 硬边界

- 首次只读取本文件；不得预读全部 references、源码、memory 或整树模板。
- 只调用 Plugin 注册的 MCP 工具；Server 固定项目根，业务工具不接收 `root`、`dogfood` 或通用 `confirmed`。
- MCP 未安装、未连接或工具未注册时报告 `PLUGIN_MCP_UNAVAILABLE` 并停止；不得编辑业务代码、启动 Shell/CLI 控制器、直接写 SQLite 或从源码/Markdown 猜状态。
- SQLite/Graph 是机器权威，Markdown 只是投影。Agent 不直接改 SQLite、baseline、图或投影。
- 只用完整 schema v3；具体输入以当前工具 schema 和结构化错误为准，不在上下文复制完整模板。

## 不变量

- 选择 `Task`、`Capability → Task` 或 `Delivery → Capability → Task` 中最浅的合法结构；Task 是唯一执行叶子。
- 人只评审并冻结一次整树；Graph 决定 Task、Agent 数、顺序、门禁、重试和恢复。`active` 与 manual 接收会话都不逐 Task 二次确认。
- 每个 requirement 都有独立、可观察的 acceptance。`scope` 使用最小可用模块边界，精确修改/删除由 `fileChanges` 授权，批量新增只能落入 ADD-only `generatedFileRoots`。
- 用户指定的开发 Skill 不进入需求分析：先验证宿主级 `root` 与项目级 `project` catalog；不存在或疑似拼错时停止并优先展示人类友好的 `userPrompt`。存在时只登记 `DEVELOPMENT`，不预读、不递归展开、不自动加入 `GATE`。
- required Skill 在实际阶段由当前执行宿主原生调用；分别记录 activation 与 conformance。Read/load、父会话调用或 `skillUsage` 自述不能替代，成功 result/gate/review 要求逐项 `INVOKED + PASS`。
- 同契约修正回原 Task；契约或权限变化回人工评审。只有用户最终确认后才能 `COMPLETED`。
- Graph 重建、运行取消、人工审查接受和用户最终确认都必须来自真实用户决定，不能由模型自授权。
- 提交、推送、合并、迁移、发布及新增外部权限始终需要单独授权。

## 路由

1. 确认 MCP 已注册后调用 `workspace_status`，不按文件推断。
2. `ACTIVE`：读取 [execution-quickstart.md](references/execution-quickstart.md)，再用 `graph_frontier` 恢复。优先使用精确 ID、数据库焦点或唯一候选；多候选才请用户选择。
3. `ABSENT/STAGING_ONLY`：没有可恢复交付。只读分析、审查或问答直接完成；开发新需求才读取 [planning-quickstart.md](references/planning-quickstart.md)。
4. 冻结或恢复后消费 Graph，直到真实阻断或 `REQUEST_USER_CONFIRMATION`；不得用聊天总结代替 Graph 收尾。

## 按需读取

- 新需求规划与一次冻结：[planning-quickstart.md](references/planning-quickstart.md)
- Graph 执行、Skill 调用、证据、重试和修正：[execution-quickstart.md](references/execution-quickstart.md)
- gate、独立审查与最终验收：[acceptance.md](references/acceptance.md)
- 仅当 payload 确实超限或 MCP 断连时：[mcp-transport.md](references/mcp-transport.md)
