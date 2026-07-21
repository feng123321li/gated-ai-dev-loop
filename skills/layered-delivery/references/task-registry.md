# SQLite 分层工作项注册表

## 唯一机器权威

`.layered-delivery/governance.sqlite3` 是项目内唯一机器权威。一个项目只使用一个数据库；多个用户需求通过根 `work_item_id` 隔离，不为每个 `<root-id>` 创建数据库。

数据库使用 Python 标准库 `sqlite3`，当前 `PRAGMA user_version=3`。主要记录包括：

- `workspace`：协调根、全局 revision、当前焦点和更新时间；
- `work_items`：节点 registry 条目、完整 definition 和节点 state；
- `hierarchies`：每个需求根的层级指纹和统一评审状态；
- `task_contexts`：绑定 operationId 的 Task 上下文和交接内容；
- `reports`：开发复核与验收报告的结构化内容；
- `interaction_events`：追加式人机指令、决策、状态摘要和验证修正审计链。

复杂契约和完整证据 artifact 可作为规范 JSON 文本存入 SQLite 列；证据引用只保留控制器按规范 JSON 计算的 SHA-256。两者在同一写事务中保存，仍是单一数据库权威，不产生临时或持久化 JSON 文件。控制器不迁移、不兼容旧 JSON 工作区、路径式 evidence 引用或非当前数据库 schema。

## 单根嵌套 Markdown

- 根：`work-items/<root-id>`；
- 直接子级：`<parent-packagePath>/children/<child-id>`；
- 更深节点继续递归；
- 每个节点保留 `baseline.md`、开发方案、节点进度等人类投影；根节点自身进度为 `node-progress.md`，子节点自身进度为各自目录的 `progress.md`；
- 需求根额外以 `development-plan.md`、`progress.md` 保留整树方案和整树进度，并保留 `interaction-log.md` 和按阶段生成的交接/报告。

因此每个用户需求在 `work-items/` 下只有一个顶层目录。数据库可按 ID 查询平铺记录，但 Markdown 必须保持 Task、Capability→Task 或 Delivery→Capability→Task 的真实树形。

Markdown 不是机器权威。手工删除 `<root-id>` 目录不会删除数据库中的需求；后续投影刷新可能重建它。删除、归档或重置需求必须通过明确的控制器操作，不能用文件删除代替。

## 确定性恢复

恢复只使用显式 ID、有效焦点或唯一候选。候选多于一个时请求用户选择，不依赖目录时间、名称相似度或描述关键词。数据库 schema、workspace、ID、拓扑、路径或 JSON 结构损坏时全局阻断。若单个历史节点仅有 evidence 引用不符合当前契约，且完整 artifact 与其他字段仍能通过当前校验，则把该节点设为只读隔离：原始 SQLite 行保持不变，直接操作该节点会被拒绝，但新需求、有效兄弟节点和已有 claim 可以继续。`workspace-overview.md` 必须列出隔离节点。

恢复时检查：

- coordination root 与当前工作区一致；
- 数据库 `user_version` 为当前 schema v3；除明确只读隔离的历史 evidence 节点外，所有结构化记录均满足当前完整契约；
- ID 唯一，父子种类合法，无环，全部 child 已物化；
- packagePath 满足单根递归路径；
- hierarchy、baseline、state、contract 和父契约指纹一致；
- 冻结前根级 `development-plan.md` 与数据库可重建内容一致；
- 根级开发方式、Task claim、operation、gate 和 evidence 快照可解释。

Markdown 缺失时可执行 `refresh-projections`；隔离不是迁移或兼容入口，控制器不得根据旧路径读取 evidence、不得修复或重写隔离行。不能安全证明为“仅 evidence 引用过期”的机器记录仍必须阻断，也不能从 Markdown 反向猜测状态。

## 焦点、交互与命令

`currentFocus` 只帮助恢复，不授予冻结或开发权限。`record-interaction` 只追加简短可审计摘要，`interaction-log` 查询结构化事件；不得保存隐藏思考过程、密钥或不必要的原始对话。

交互输入通过 stdin 提交，严格字段为：

```json
{"schemaVersion":3,"sessionId":"session-id","actor":"USER|AGENT|SUBAGENT","eventType":"USER_INSTRUCTION|AGENT_UPDATE|DECISION","summary":"简短可审计事实","operationId":null,"hostRuntime":"codex"}
```

```text
python -X utf8 <skill-root>/scripts/hdg.py record-interaction --item <id> --interaction - --json
python -X utf8 <skill-root>/scripts/hdg.py interaction-log --item <id> --json
```

正常闭环是：

```text
prepare-hierarchy
→ 人工查看 development-plan.md
→ 选择开发方式并执行一次 freeze-hierarchy
→ dispatch-task
→ task-result / development-review.md
→ 验证发现同契约文件遗漏时 remediate-task / 回到原 Task
→ accept-item / acceptance-report.md
→ acceptance-item
```

`remediate-task` 只为未完成需求的同契约验证修正追加精确文件授权。artifact、摘要和修正前状态快照存入现有 `interaction_events`，不新增 JSON 文件、不修改 baseline，也不创建第二个需求根。诊断、恢复与执行命令还包括 `task-context`、`claim-task`、`gate-item`、`retry-item`、`ready-tasks` 和 `refresh-projections`。
