# Layered Delivery

`layered-delivery` 用于治理 AI Agent 的软件开发过程。它先把需求整理成一份可人工评审的开发方案，再将方案冻结为可恢复的执行图，驱动 Agent 完成开发、测试、门禁、审查和最终验收。

当前版本：**0.16.4**

## 能做什么

- 根据需求复杂度选择最浅合法层级：`Task`、`Capability → Task` 或 `Delivery → Capability → Task`。
- 在开发前生成完整方案，让用户确认范围、文件、接口、测试和验收标准。
- 用户只需确认一次方案并选择开发方式，不会再为同一次冻结弹出第二个确认。
- 自动调度 Task，支持并行开发、测试失败修复、门禁重试和中断恢复。
- 允许需求指定任意 required Skill，并检查实际原生调用和产物符合性，而不只记录 Skill 被读取。
- 使用 SQLite 保存权威状态，新 Agent 或新会话可以从原执行图继续。
- 开发和门禁完成后停在最终验收，只有用户明确接受才完成需求。

## 支持的 Agent

项目直接提供 Claude Code 和 Codex 的插件配置。两者都可以：

- 规划并冻结新需求；
- 自动开发自己冻结的需求；
- 接续另一 Agent 已冻结的需求。

Cursor 或其他 Agent 也可以接入，但宿主需要同时提供：

- `layered-delivery` MCP Server 的启动和项目根绑定；
- 原生 Skill 调用入口；
- 对敏感工具的人工确认策略。

方案由哪个 Agent 创建只作审计，不限制后续由哪个 Agent 开发。

## 开始前

- Python 3.10+；
- `python` 可从 PATH 启动；
- 已安装并启用 `layered-delivery`；
- 当前会话已成功连接 MCP Server，并注册全部 38 个工具。

MCP 未连接或工具注册失败时不能开始开发，也不能使用 Shell 或直接修改 SQLite 绕过治理。

从组织或公开 Marketplace 安装或更新插件后，必须新建 Agent 会话，让宿主重新加载 Skill、MCP Server 和权限配置。旧会话会继续使用启动时缓存的版本。

## 怎么用

在已接入插件的 Agent 中直接提出需求：

```text
使用 layered-delivery 规划并治理这个需求：
<需求内容>
```

Agent 会按以下流程处理：

1. 检查当前工作区是否已有可恢复的交付运行。
2. 新需求生成开发方案、执行图和验收标准。
3. 用户评审方案，并选择 `active` 或 `manual`。
4. 这一次选择就是当前方案的冻结确认；Agent 紧接着冻结整棵需求树，不再请求第二次批准。
5. Graph 驱动开发、测试、修复、门禁和审查。
6. 全部通过后等待用户最终验收。

### 选择开发方式

| 方式 | 用途 |
|---|---|
| `active` | 当前会话冻结后立即自动开发，持续推进到最终验收。 |
| `manual` | 当前会话冻结并生成一次性交接，新 Agent 从同一执行图自动继续。 |

`manual` 交接后不会重新准备需求、重新冻结、重新选择方式或逐 Task 请求确认。

如果需求必须使用某个 Skill，直接在对话中说明即可，用户不需要填写 `requiredSkills` 字段。用户明确指定仅在开发过程中使用的 Skill 时，Agent 不预分析、不递归展开，也不自动加入 `GATE`；但会先同时检查宿主级 root 和当前项目级 project 的 Skill catalog。存在时才登记为 `DEVELOPMENT` 执行约束并在开发时调用；不存在或疑似打错字时，准备阶段会给出带来源的近似 Skill 选项，并显示人类友好的中文标题、说明、“宿主级/项目级”来源和安装兜底指引，让用户选择正确名称或安装，不会静默改名。

Scope 按最小可用模块边界适当放宽，通常使用 `module/**`，以容纳同模块内必要的新文件；实际写授权仍由开发方案中的精确 `fileChanges` 冻结。不要使用全仓库 `**`，因为重叠 Scope 会减少 Task 并行度。

低风险单目标需求优先采用根 Task + LIGHT：方案文字保持简洁、优先运行定向测试、开发 handoff 只携带最小开工上下文。独立验收、精确文件授权、真实测试、P0/P1 和最终用户确认仍保留。

## 哪些操作会要求确认

| 操作 | 是否需要新的用户确认 |
|---|---|
| 评审方案、选择 `active` 或 `manual` 并冻结 | 只确认一次 |
| 冻结范围内的 Skill 调用、开发、测试、门禁、重试和恢复 | 否 |
| Graph 重建、Graph 取消、人工审查接受、最终用户验收 | 是 |
| Git 提交、推送、合并、迁移、发布或新增外部权限 | 是 |

Claude Code 的 Auto 权限或代码编辑、测试命令权限属于宿主启动前置条件，不是第二次冻结确认。

## 恢复与故障

- 新会话先调用 `workspace_status` 检查工作区状态。
- 存在活动交付时，从 `graph_frontier` 恢复，不重新准备或冻结。
- MCP 意外断开时立即停止新的代码和治理写入，并返回 `PLUGIN_MCP_DISCONNECTED`。
- 写操作响应未送达时，提交状态记为未知；重连后以 SQLite 状态为准，不能盲目重放。
- MCP 未安装、未连接或工具注册失败时，返回 `PLUGIN_MCP_UNAVAILABLE`。

## 主要产物

| 文件 | 用途 |
|---|---|
| `development-plan.md` | 开发前评审完整方案 |
| `execution-graph.md` | 查看执行与治理节点 |
| `frontier.md` | 查看下一步、关键路径和阻断 |
| `development-review.md` | 对照方案检查实际开发结果 |
| `acceptance-report.md` | 查看门禁、Skill 执行和验收证据 |
| `run-timeline.md` | 查看 attempt、失败和恢复记录 |
| `requirement-handoff.md` | `manual` 模式的一次性交接 |

## 更多文档

- [版本更新记录](CHANGELOG.md)
- [Skill 使用规则](skills/layered-delivery/SKILL.md)
- [规划与一次冻结](skills/layered-delivery/references/planning-quickstart.md)
- [Graph 执行与修正](skills/layered-delivery/references/execution-quickstart.md)
- [验收与最终确认](skills/layered-delivery/references/acceptance.md)
- [超限传输与断连恢复](skills/layered-delivery/references/mcp-transport.md)
