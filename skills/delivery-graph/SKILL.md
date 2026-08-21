---
name: delivery-graph
description: "把已确认的软件需求规划或修订为 schema v3 Delivery Graph，负责 workspace/Delivery 发现、Git 开发基线确认、层级与 DAG 建模、不可变 Revision、执行模式选择和冻结。用于新建交付、澄清边界、确认 baseline、选择 AUTOMATIC/MANUAL，或处理 REPLAN_REQUIRED；Graph 已进入 ACTIVE、BLOCKED、PAUSED 或需要 frontier 调度时改用 delivery-graph-dispatch。"
allowed-tools:
  - mcp__plugin_delivery-graph_delivery-graph__workspace_status
  - mcp__plugin_delivery-graph_delivery-graph__hierarchy_contract
  - mcp__plugin_delivery-graph_delivery-graph__preview_hierarchy
  - mcp__plugin_delivery-graph_delivery-graph__confirm_development_baseline
  - mcp__plugin_delivery-graph_delivery-graph__select_execution_mode
  - mcp__plugin_delivery-graph_delivery-graph__resume_execution_mode
  - mcp__plugin_delivery-graph_delivery-graph__create_manual_handoff
  - mcp__plugin_delivery-graph_delivery-graph__prepare_hierarchy
  - mcp__plugin_delivery-graph_delivery-graph__prepare_delivery_revision
  - mcp__plugin_delivery-graph_delivery-graph__delivery_revision_history
  - mcp__plugin_delivery-graph_delivery-graph__freeze_hierarchy
---

# Delivery Graph 规划与冻结

把本 Skill 作为唯一规划入口。用 `Delivery → GROUP（可递归）→ TASK` 表达纵向层级，用依赖和 `resourceClaims` 表达横向 DAG；只定义方向、边界、已确认契约和验收，不替 TASK receiver 决定内部实现。

## 不可破边界

- 只调用 frontmatter 中列出的 planning Profile 工具。MCP 不可用时报告 `PLUGIN_MCP_UNAVAILABLE` 并停止控制面写入。
- 只使用 schema v3；准备前调用 `hierarchy_contract`，不得从源码、旧会话或示例猜参数。
- 把 SQLite 与事件链视为机器权威；不得用 Shell、Python或数据库连接读写 `scheduler.db`，不得人工修补 Graph 或 Markdown 投影。
- 本 Skill 只做发现、规划、基线、Revision、执行选择、冻结、Revision 完成确认与 Delivery 关闭交互；不得调用 frontier、dispatch、claim、heartbeat、result 等执行工具，也不得在 primary 内实现或审查 Loop。
- Graph 范围不是 Git 或外部操作授权。Controller 不写 Git；commit、merge、push、发布、迁移和新增权限仍分别取得授权。
- 一个物理 checkout 只运行一个 Delivery turn，策略固定为 `CURRENT_WORKSPACE_SERIAL`。每个 Delivery 使用独立分支；不得把 linked checkout 当成自动新建 worktree 的授权。
- `PAUSED`、`COMPLETED`、`CANCELLED` 不等同于 workspace release。必须先收束 receiver/reservation，再在每个冻结独立分支完成业务 commit 并保持 tree/index clean 与 binding 匹配，由协议复核并持久化 `WORKSPACE_TURN_RELEASED`；只有响应明确为 `workspaceRelease=RELEASED` 后宿主才可切换分支。
- 同一需求保持稳定 `delivery.id`、`requirementKey` 和 `.layered-delivery/<delivery-id>/`。新业务目标默认新建 Delivery；同一需求延续或 `REPLAN_REQUIRED` 才创建下一不可变 Revision。
- 只有真实用户确认后才记录当前 Revision 完成。`COMPLETED` 不会自动关闭 Delivery：`OPEN/未上线` 可继续追加 Revision；测试、业务验收和生产上线完成后，必须再次明确授权 `close_delivery` 才进入 `CLOSED/已上线交付`。归档与关闭分离，也必须单独明确授权。

## 入口路由

先调用 `workspace_status`；已知 Delivery 时始终传 `rootId`。

| 状态 | 动作 |
|---|---|
| `ABSENT` | 读取[规划说明](references/planning-quickstart.md)，只读问答不创建状态 |
| `DELIVERY_SELECTION_REQUIRED` | 展示候选并按本会话持有的 `rootId` 重查，绝不按更新时间猜测 |
| `CHOICE_READY` | 严格处理 `pendingInteraction`，已有选择时不重复询问 |
| `HANDOFF_READY` | 原样交付 `manualHandoff.receiverPrompt`；已绑定同一串行队列，接收方使用 `$delivery-graph-dispatch` |
| `PREPARED` | 需求未变且尚无 `executionSelection` 时不要重复 preview |
| `ACTIVE` / `BLOCKED` / `PAUSED` | 停止本 Skill，切换到 `$delivery-graph-dispatch` |
| `COMPLETED` + `OPEN/未上线` | 当前 Revision 已完成；展示结果，按用户意图继续同一 Delivery 的 Revision，或在生产上线后明确关闭 |
| `COMPLETED` + `CLOSED/已上线交付` | 不得追加 Revision；用户单独明确要求时才归档 |
| `ARCHIVED` / `CANCELLED` | 报告终态；已关闭/归档后的新增改动创建新 Delivery |

无参发现不会恢复未绑定的 `CHOICE_READY` 或旧版 `HANDOFF_READY` 草稿；必须使用创建响应中的 `rootId`。当前 MANUAL 选择会原子绑定当前 workspace 并进入与 AUTOMATIC 相同的串行队列。遇到未知写响应、MCP 重连、Git binding 异常或投影问题时，读取[MCP 与状态说明](references/mcp-transport.md)，不要盲目重放写操作。

## 规划顺序

1. 检查真实代码和 workspace，确认目标、边界、验收点、项目范围、依赖、排他资源和外部兼容契约。
2. 用户指定的开发 Skill 记录为共享 `root.skillHints`。仅当它能帮助规划方向、风险、边界或验收时才预触发；实现类 Skill 多数应由 `$delivery-graph-task` 在真实代码上下文中使用。
3. `assuranceProfile` 默认使用 `STANDARD`；只有用户明确要求 `LIGHT` 且 hierarchy 满足 LIGHT 结构约束时才使用 `LIGHT`，不得由 Agent 做风险分档或自动推荐。
4. 只为真实分层、依赖或汇合创建 GROUP。仅在直接子项存在需要独立验证的 seam 时配置 GROUP Review。
5. 数据库变更在 preview 前读取当前结构，冻结字段级 before/after 和迁移策略；不得把设计留给 TASK。
6. 调用 `hierarchy_contract`，构造 schema v3，并执行 `projectionGuidance.taskSplitIntegrityPreflight`。普通文件名、实现类、内部方法和测试组织仍由 Loop 决定；仅把用户明确要求或确认的外部契约写成冻结事实。
7. hierarchy 默认直接以内联 JSON 传入，不创建 `.layered-delivery/staging` 或其他项目内中转目录。只有宿主明确触发参数大小限制且内联确实不可用时，才使用一次性 `hierarchy_file`，成功读取后立即清理该临时文件。
8. `preview_hierarchy` 返回 `CHOICE_READY` 且 `artifactsReady=true` 后，才处理唯一规范入口 `pendingInteraction`。

## 待确认交互

- 原样遵循 `presentationPolicy`、选项顺序、默认项、推荐项和文案。宿主原生 selector 可用时机械映射；不可用时才原样展示 `markdown`，不自行添加选项。
- `DEVELOPMENT_BASELINE` 冻结只读 Git binding。adopt 当前脏分支时必须取得用户归属确认并回传精确 dirty fingerprint；NEW 选项在这里不创建分支或 worktree。
- 多 Git 项目的每个 `projectScopes[*]` 都必须有完整 binding；任一缺失即停止。
- `EXECUTION_MODE` 只确认一次。`select_execution_mode` 已持久化选择；若返回 workspace 准备动作，机械完成被授权的串行准备后，AUTOMATIC 调用 `resume_execution_mode`，MANUAL 调用 `start_manual_handoff`，不得重试选择。
- `AUTOMATIC` 与 `MANUAL` 都只使用 `CURRENT_WORKSPACE_SERIAL`。已有 owner 时保持 `QUEUED`，不要抢占或 stash owner 的未完成改动。
- `MANUAL` 原样展示 `manualHandoff.receiverPrompt`。`HANDOFF_READY` 是已排队的冻结包，不是 Graph Run；接收宿主必须先使用 `$delivery-graph-dispatch` 调用 `start_manual_handoff`。旧版未绑定 handoff 只能按明确 `rootId` 恢复；层级完全一致且尚未启动时，启动操作会把旧运行时策略刷新为当前 Graph 编译协议。

## Revision、完成与关闭

- 初次开发前用户修改需求时重新 preview；冻结后拓扑、依赖、资源、项目 scope、Review 契约或 databaseChanges 必须变化时，调用 `prepare_delivery_revision`，保持相同 `delivery.id`，不要创建新的 Delivery ID。
- TASK 局部 requirement 只有在用户明确授权、未开始且无有效 reservation 时才可 unfreeze/refreeze；这些受保护工具不在自动允许列表中。
- 执行完成后由 `$delivery-graph-dispatch` 返回 `RECORD_USER_CONFIRMATION`。本 Skill 展示分层验收并等待真实用户确认；只有确认后调用受保护的 `record_user_confirmation`。它只把当前 Revision/Graph Run 标为 `COMPLETED`，Delivery 保持 `OPEN/未上线`。该响应会把 Graph 终态与 Git release 分开报告：`workspaceRelease=PENDING` 时按 `workspaceNextAction` 在原冻结分支 commit/clean 并调用 `workspace_status(rootId=...)` 复核，禁止先切分支；`RELEASED` 后仍按 `nextAction=PREPARE_REVISION_OR_CLOSE_DELIVERY` 决定后续。
- 用户在测试或业务验收后回来优化时，保持同一 `delivery.id` 调用 `prepare_delivery_revision`；冻结新 Revision 时，已完成的旧 run 保持 `COMPLETED` 审计终态，只有旧 Revision scope 标为 `SUPERSEDED`。
- 只有用户明确确认测试、业务验收与生产上线均已完成时，才调用受保护的 `close_delivery(confirmed=true)`。关闭后不得追加 Revision，后续新增需求创建新 Delivery。
- `archive_delivery` 只接受 `CLOSED/已上线交付`，且只在关闭后又收到单独、明确的归档请求时调用。

## 按需读取

- 新建/修订 Graph、Git baseline、project scopes、schema 与冻结：[planning-quickstart.md](references/planning-quickstart.md)
- MCP 断连、重放安全、SQLite 权威、workspace binding 与投影：[mcp-transport.md](references/mcp-transport.md)
- 已冻结后的派遣、等待和恢复：切换到 `$delivery-graph-dispatch`
