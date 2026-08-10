# 团队安装与运维

本文面向团队管理员和普通使用者，覆盖 `delivery-graph` 0.39.4 的安装、升级、恢复、卸载与回滚。展示名为“分层交付 Graph 控制面”。Plugin 同时支持 Codex 和 Claude Code，项目运行时仅依赖 Python 3.10+ 和标准库。

## 安装前检查

- 安装 Python 3.10 或更高版本，并确保 `python --version` 可在宿主终端运行。
- 安装至少一个受支持宿主：Codex 或 Claude Code。
- 确认能够访问公司内部 Marketplace 仓库。
- 在实际项目的新会话中使用 Plugin；不要在维护 `delivery-graph` 源码仓库时创建业务运行包。

当前本地探测到的 0.39.4 真实宿主候选基线是 Codex CLI 0.147.0 和 Claude Code 2.1.226。这两个版本是本轮冒烟目标，不是永久最低版本。在兼容矩阵回填真实运行结果前，只能表述为“候选验证中”。

## 安装

### Codex

```text
codex plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git --ref master
codex plugin add delivery-graph@majorbio-skills
codex plugin list --json
```

安装后打开 `/hooks`，审查来源为当前 `delivery-graph` Plugin 的 `SessionStart`、`SubagentStart` 与 `PreToolUse` 命令并选择始终信任当前定义，再新建或恢复 Codex Delivery 任务。信任按精确内容哈希持久保存；未升级时不重复提示，Hook 内容变化后必须重审。不要用普通执行权限、模型配置或未来版本通配信任绕过此边界。

### Claude Code

```text
claude plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git
claude plugin install delivery-graph@majorbio-skills --scope user
claude plugin list --json
```

安装后退出旧会话并新建 Claude Code 会话。项目内不需要额外复制 MCP 配置或维护 `local.settings`；Plugin 自带 `.mcp.json` 和 Hook 配置。

### 安装验证

在源码发布包中执行不调用模型的本地探测：

```text
python scripts/host_smoke.py probe --json
```

结果必须报告 Plugin 版本 0.39.4 和 34 个 MCP 工具，并如实标记本机已安装的宿主。`probe` 只验证本地发布产物和宿主可发现性，不调用模型，也不能作为真实宿主通过记录。发布管理员还必须按[宿主兼容矩阵](host-compatibility.md)分别在 Codex、Claude Code 环境执行真实宿主冒烟任务；两个宿主不要求安装在同一台机器。

真实冒烟默认先只展示计划，必须显式增加 `--execute` 才调用模型。两个宿主分别运行，绝不从一个终端跨调另一个 Agent：

```text
python scripts/host_smoke.py run --host claude-code --scenario light
python scripts/host_smoke.py run --host claude-code --scenario light --execute

python scripts/host_smoke.py run --host codex --scenario light
python scripts/host_smoke.py run --host codex --scenario light --execute
```

Claude 命令从当前 0.39.4 源码发布包的 `--plugin-dir` 加载 Plugin；Codex 命令要求候选 Plugin 已从 Marketplace 安装并在 `/hooks` 中信任。发布前可用 LIGHT 验证当前 session TASK claim/heartbeat/progress/result，再用 STANDARD 覆盖独立 Review。任何输出中的 `claimedAgents` 都只能包含命令指定的当前宿主。Claude coordinator 的完整名称为 `delivery-graph:delivery-coordinator`。

0.39.4 真实冒烟还必须覆盖以下交互和失败关闭边界：

- `preview_hierarchy`、`workspace_status` 和手动接管只返回一个当前 `pendingInteraction`。缺少 `gitBinding` 时先处理 `DEVELOPMENT_BASELINE`，确认后才出现 `EXECUTION_MODE`；同一 Delivery 的后续 Revision 可复用已记忆基线。
- 干净和脏工作树都进入这条基线流程。脏树确认必须回传原响应的 `dirtyStateFingerprint`；变化路径内容、暂存区或 porcelain 状态任一改变，旧指纹都失效。
- `start_manual_handoff` 的单仓 Git 漂移先阻断且零写入。确认原 binding 时保持当前 Revision，确认新 binding 时生成下一不可变 Revision；多仓漂移 fail closed，要求用完整 project bindings 创建新的手动 Revision。
- Controller 只读计算并冻结 binding；分支和 worktree 写操作始终由宿主完成。
- 同一 AUTOMATIC 选择并发触发时只允许一个 `IMMEDIATE` worktree 创建，其余必须 `DO_NOT_REISSUE`；同仓同分支跨 Delivery 必须原子拒绝。
- worktree 创建开始后立即调用 `report_worktree_setup`，以后按 30 秒间隔续租；主仓监控应显示阶段、百分比、最后上报时间和 10 秒建议轮询。
- setup 超时或显式失败后不得直接重放；只有确认旧进程停止且残留目录/worktree 已安全核对，才允许一个并发调用获得下一 attempt。
- `hostDispatch` 必须携带精确 `branchRef/gitBinding`；宿主错分支 clean 时可恢复，dirty 时停止审查。
- 多项目 AUTOMATIC 必须准备全部 `READ_WRITE` scope 的 worktree，只启动一个 coordinator，并在共享控制根观察全部进度。
- 未显式声明 `projectScopes` 的单仓 Delivery 必须在 AUTO claim 与 `loop_context` 中得到一个经顶层 `gitBinding` 和实际 workspace 验证的 `primary` scope；无效 binding 必须在 child 读取或修改仓库前 fail closed。
- 执行模式只允许 `AUTOMATIC` 和 `MANUAL`。Codex AUTOMATIC TASK 必须由顶层 Delivery task 的 `SessionStart` coordinator capability 或调用时 Hook 通过 `claim_current_task` 在当前会话 claim，reservation 为空；该工具必须拒绝所有 Review。普通 subagent 的 SessionStart 不签发 coordinator role；Review 仍使用非空 reservation 与独立 `SubagentStart` receiver capability，后者只能变更已领取 Review，不能 claim TASK 或规划下一批。MANUAL reservation 为 `NULL`。普通 helper 和内部 Worker都不能持有 session capability。
- `archive_delivery` 只接受已完成 Delivery，默认状态发现和根总览不再列出它；显式 `root_id` 仍能读取 `ARCHIVED`、完成 run、Revision 历史和详情投影。
- 对已有 run 输入“打开当前 Delivery 的进度面板”，支持 MCP Apps 的宿主应渲染 `Delivery Graph 运行看板`；点击“刷新状态”只能重读 `open_delivery_dashboard`。不支持 UI 的宿主必须继续返回可读文字和结构化结果，且不得改用 `graph_frontier` 模拟只读刷新。
- Codex manifest 必须显式声明 `./hooks/hooks.json`；`SessionStart` 必须在 startup/resume/compact 发放当前 session capability，AUTO Review `SubagentStart` 必须原子 claim 并发放独立 child capability。code-mode 内嵌 MCP 未触发 nested PreToolUse 时，claim 与 mutation 仍须由 session capability fail closed。Claude 单独加载 `hooks/claude-hooks.json` 中的 `PreToolUse`/`StopFailure`。
- 在 Codex `/hooks` 中暂不信任或禁用 Plugin Hook 后调用 `plan_dispatch_batch`，必须在创建 reservation 前返回 `SCHEDULER_HOST_HOOK_NOT_READY` 且 `reservationCreated=false`；恢复信任并新建任务后才允许重新计划。
- Codex AUTO child 的 `SubagentStart` 若已从真实 transcript 定位 reservation、但 receiver/workspace/scope attestation 失败，TASK 必须保持 READY、reservation 立即释放并记录启动失败；child 只能报告协调器，不得调用 Loop 工具或检查、修改仓库。

Codex 冒烟不能使用 `--ephemeral`：AUTO `SubagentStart` 和 MANUAL `dispatch_loop` attestation 都必须从宿主持久化的真实 child transcript 校验 parent/child/task。冒烟会留下一个可审计的 Codex 会话记录，但业务工作区仍位于自动清理的临时目录；若宿主无法提供该 transcript，claim 必须 fail closed。

## 升级

### 升级前

1. 记录当前 `codex plugin list --json` 或 `claude plugin list --json` 输出中的版本。
2. 0.34.2 不再读取用户级 `orchestrator.json`；机器上残留的 schema v1/v2 文件不会阻断 MCP，也不参与派遣。无需在升级前编辑或删除它。
3. 对 0.31 及更早版本的旧式 manual Graph run，先在旧版本完成或取消。当前版本的 manual Graph 只能由 `start_manual_handoff` 从精确 `HANDOFF_READY` 快照创建，且 MANUAL 只允许 TASK；不要把旧 run 当作新协议恢复。
4. 自动 schema v3 Graph 可在新会话通过 `workspace_status → graph_frontier` 恢复。升级前仍建议让正在写结果的 Loop 完成，避免恰好跨越 Hook 重载窗口。
5. 已由早期 0.33.0 生成、但缺少根 `scheduler.db`/`overview.md` 的手动内容包不会从 Markdown 反向迁移。升级后应在仍持有原 hierarchy 与双 fingerprint 的需求会话中重新调用 `create_manual_handoff` 完成 SQLite 登记；不要手工创建数据库或拼接总览。
6. 不删除项目中的 `.layered-delivery`，也不直接修改 `scheduler.db`。

### Codex 升级

```text
codex plugin marketplace upgrade majorbio-skills
codex plugin remove layered-delivery@majorbio-skills
codex plugin add delivery-graph@majorbio-skills
codex plugin list --json
```

0.36.0 → 0.37.0 是 Plugin/Skill identity 更名，因此先移除旧名再安装新名；不要删除项目中的 `.layered-delivery`，它仍是稳定的 schema v3 数据目录。

### Claude Code 升级

```text
claude plugin marketplace update majorbio-skills
claude plugin uninstall layered-delivery@majorbio-skills --scope user
claude plugin install delivery-graph@majorbio-skills --scope user
claude plugin list --json
```

Claude Code 更新后必须重启会话。若宿主提示命令名称不同，以当前 `claude plugin marketplace --help` 输出为准；不要通过手工覆盖 Plugin cache 模拟升级。

## 恢复

| 现象 | 安全恢复方式 |
|---|---|
| 新会话不知道旧任务状态 | 在同一工作区调用 `workspace_status`，对返回的 `rootId` 调用 `graph_frontier` |
| 0.34.0/0.34.1 因旧 `orchestrator.json` 无法启动 MCP | 升级到 0.34.2；新版本不读取该文件，无需修改 Graph 状态 |
| MCP 连接中断，写操作结果未知 | 先重连并读取 `workspace_status`、`graph_status` 或 `graph_frontier`，不要重放未知写操作 |
| Loop 心跳和进度停止 | 等待租约回收；下一次 `graph_frontier` 触发 `WORKER_LOST`，随后使用新 attempt 和新接收上下文 |
| Projection 缺失或损坏 | 对已校验事件链调用 `rebuild_graph_run`；不要直接写 Markdown 或 SQLite |
| `claim_current_task` 或 `plan_dispatch_batch` 返回 `SCHEDULER_HOST_HOOK_NOT_READY` | 在 `/hooks` 核对并始终信任当前精确 Plugin Hook，随后新建或恢复 Delivery 任务触发 `SessionStart`；不要改模型配置或无认证重放 |
| AUTO child 返回 `DELIVERY_GRAPH_STARTUP_ERROR` | child 停止所有仓库和 Loop 操作，只把错误码报告协调器；Controller 已在安全前提满足时立即释放未领取 reservation，协调器刷新 frontier 后修复身份、workspace 或 scope 前置条件 |
| Claude 结构化 429 | 由 StopFailure Hook 记录真实 reset 时间并暂停；到期由一次性宿主唤醒重新读取 frontier |
| LIGHT 执行中发现影响扩大 | 提交 `REPLAN_REQUIRED`，保持同一 `delivery.id` 准备 `STANDARD` Revision |

恢复过程中不得手工伪造 heartbeat、progress、operation、attestation 或终态。工作区代码可以保留并由新 attempt 重新检查，但调度状态只通过 Plugin MCP 改变。

## 卸载

### Codex

```text
codex plugin remove delivery-graph@majorbio-skills --json
```

### Claude Code

```text
claude plugin uninstall delivery-graph@majorbio-skills --scope user
```

卸载后新建会话，并用宿主的 Plugin 列表确认已移除。卸载 Plugin 不等于删除项目交付记录：项目中的 `.layered-delivery` 默认保留，便于审计或重新安装后恢复。需要清理这些数据时应单独评审精确路径、确认没有活动 Delivery，并使用可恢复方式处理。

## 回滚

团队回滚由 Marketplace 管理员执行：把 Codex 与 Claude 两份 Marketplace manifest 同时重新固定到最后已验证的 tag 和 40 位提交 SHA，然后让用户刷新 Marketplace、重新安装/更新 Plugin 并新建会话。

回滚到 0.32.0 前应先保存 0.33.x 手动开发包中的 progress/acceptance；0.32.0 不会生成完整冻结内容包，也不识别 `requirementSnapshotStatus`。自动 schema v3 Graph 仍应优先在当前版本完成，避免在活动 claim 期间跨版本切换 Hook。回滚不修改项目数据库，不通过删除 cache 伪造版本切换。
