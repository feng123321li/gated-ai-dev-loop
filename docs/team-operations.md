# 团队安装与运维

本文面向团队管理员和普通使用者，覆盖 `delivery-graph` 0.39.22 的安装、升级、恢复、卸载与回滚。展示名为“分层交付 Graph 控制面”。Plugin 同时支持 Codex、Claude Code 与 ZCode，项目运行时仅依赖 Python 3.10+ 和标准库。

## 安装前检查

- 安装 Python 3.10 或更高版本，并确保 `python --version` 可在宿主终端运行。
- 安装至少一个受支持宿主：Codex 或 Claude Code。
- 确认能够访问公司内部 Marketplace 仓库。
- 在实际项目的新会话中使用 Plugin；不要在维护 `delivery-graph` 源码仓库时创建业务运行包。

当前版本的真实宿主候选基线与结果记录在[宿主兼容矩阵](host-compatibility.md)。列出的宿主版本只是本轮冒烟目标，不是永久最低版本；在矩阵回填真实运行结果前，只能表述为“候选验证中”。

## 安装

### Codex

```text
codex plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git --ref master
codex plugin add delivery-graph@majorbio-skills
codex plugin list --json
```

Plugin 通过 Skill、Agent 描述、MCP 与宿主元数据工作，不需要额外的生命周期信任步骤；安装后新建或恢复 Codex Delivery 任务即可。敏感 MCP 工具继续由 Codex 宿主以 `approval_mode=prompt` 请求批准，不要用全局执行权限或宽泛持久规则绕过逐次审批。

### Claude Code

```text
claude plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git
claude plugin install delivery-graph@majorbio-skills --scope user
claude plugin list --json
```

安装后退出旧会话并新建 Claude Code 会话。项目内不需要额外复制 MCP 配置或维护 `local.settings`；Plugin 自带 `.mcp.json`。敏感工具由 Claude Code 自身的宿主审批处理。

### 安装验证

在源码发布包中执行不调用模型的本地探测：

```text
python scripts/host_smoke.py probe --json
```

结果必须报告 Plugin 版本 0.39.22 和 33 个 MCP 工具，并如实标记本机已安装的宿主。`probe` 只验证本地发布产物和宿主可发现性，不调用模型，也不能作为真实宿主通过记录。发布管理员还必须按[宿主兼容矩阵](host-compatibility.md)分别在目标宿主执行真实宿主冒烟任务；宿主不要求安装在同一台机器。MCP 工具是否真正注入各 workspace/Agent schema，使用[注册矩阵与生命周期契约](mcp-host-lifecycle-contract.md)中的只读 Demo 单独验证。

真实冒烟默认先只展示计划，必须显式增加 `--execute` 才调用模型。两个宿主分别运行，绝不从一个终端跨调另一个 Agent：

```text
python scripts/host_smoke.py run --host claude-code --scenario light
python scripts/host_smoke.py run --host claude-code --scenario light --execute

python scripts/host_smoke.py run --host codex --scenario light
python scripts/host_smoke.py run --host codex --scenario light --execute
```

Claude 命令从当前 0.39.22 源码发布包的 `--plugin-dir` 加载 Plugin；Codex 命令要求候选 Plugin 已从 Marketplace 安装。发布前可用 LIGHT 验证 `recommend_assurance_profile → select_execution_mode → 当前 checkout 分支准备 → resume_execution_mode → plan_dispatch_batch → 独立 TASK child → dispatch_loop(AUTO) → result`；短任务允许没有显式 heartbeat/progress。再用 STANDARD 覆盖首次 heartbeat、进度和独立 Review。Claude 由主会话在 `CURRENT_WORKSPACE_SERIAL` 边界内协调当前分支和独立 receiver Agent，不启动专用后台 coordinator。任何输出中的 `claimedAgents` 都只能包含命令指定的当前宿主。

0.39.22 真实冒烟还必须覆盖以下交互和失败关闭边界：

- `preview_hierarchy`、`workspace_status` 和手动接管只返回一个当前 `pendingInteraction`。缺少 `gitBinding` 时先处理 `DEVELOPMENT_BASELINE`，确认后才出现 `EXECUTION_MODE`；同一 Delivery 的后续 Revision 可复用已记忆基线。
- 干净和脏工作树都进入这条基线流程。直接 adoption 当前脏分支时必须回传原响应的 `dirtyStateFingerprint`，且变化路径内容、暂存区或 porcelain 状态任一改变都会使旧指纹失效；选择另一个 Delivery 分支时不把当前改动归属给新 Delivery，待其成为队首后由 `automaticHostPreparation` stash。
- `start_manual_handoff` 的单仓 Git 漂移先阻断且零写入。确认原 binding 时保持当前 Revision，确认新 binding 时生成下一不可变 Revision；多仓漂移 fail closed，要求用完整 project bindings 创建新的手动 Revision。
- Controller 只读计算并冻结 binding；当前 checkout 的分支创建或切换仍由宿主完成。Plugin 不公开 worktree setup 工具，也不自动创建 linked worktree；已存在的 linked checkout 仅作为普通 current workspace。
- 同一物理 workspace 可以绑定多个 Delivery，但每个会话必须保存并显式传自己的 `rootId`。无参发现多个未结束绑定时只返回 `DELIVERY_SELECTION_REQUIRED`，不能按更新时间猜选。
- AUTOMATIC 选择在排队时立即持久化。非队首 Delivery 对外标记为 `QUEUED`，包含队列位置、owner 和无需再次确认的 `resume_execution_mode` continuation；不创建 Run、不派遣 receiver。前序 Delivery 释放后，宿主自动消费 `automaticHostPreparation`：必要时按精确指纹 stash 业务改动（排除 `.layered-delivery/**`），创建或切换目标分支，再调用 `resume_execution_mode`，不能重选。
- 手动交接冻结同样持久化 Delivery、不可变 Revision、完整 hierarchy、双 fingerprint 和人类投影，但状态保持 `HANDOFF_READY`。它不产生 `QUEUED`、自动 continuation、Graph Run 或 workspace binding；只有接收方显式调用 `start_manual_handoff` 才尝试取得串行 turn，等待时仍返回手动 `WAITING_FOR_WORKSPACE_*` 状态。
- 前序 Delivery 只有在 Run 终态、被取消 receiver 的租约已结束、存在相对 turn-start 的非空业务 commit、历史未改写且工作树/index 干净时才释放。错分支、dirty、HEAD 或 scope 漂移全部失败关闭。
- 主会话当前 checkout 与所有 `READ_WRITE` project workspace 都参与事务内 owner gate；任一 physical workspace 冲突时只允许先到 Delivery 运行。
- TASK/TASK Review 的 Controller 可信 Git 快照必须生成 `workspace-changes.patch`，覆盖 committed、staged、unstaged 与 untracked 内容，供未打开实际 checkout 的用户审核。
- 同一冻结分支仍不能同时属于两个未终结 Delivery；每个 Delivery 使用自己的 feature branch。
- 未显式声明 `projectScopes` 的单仓 Delivery 必须在 AUTO claim 与 `loop_context` 中得到一个经顶层 `gitBinding` 和实际 workspace 验证的 `primary` scope；无效 binding 必须在 child 读取或修改仓库前 fail closed。
- 执行模式只允许 `AUTOMATIC` 和 `MANUAL`。所有 AUTO TASK/Review 必须由 `plan_dispatch_batch` 创建绑定 decision fingerprint 的非空短租约 reservation，再由宿主创建独立 child，以 `dispatch_transport=HOST_NATIVE` 和新的显式 `operation_id` 调用 `dispatch_loop(AUTO)`。MANUAL 只允许 TASK，以显式 receiving context/operation claim，不带 AUTO reservation/decision/transport。普通 coordinator、helper 和内部 Worker 都不能持有 operation/reservation bearer。
- `archive_delivery` 只接受已完成 Delivery，默认状态发现和根总览不再列出它；显式 `root_id` 仍能读取 `ARCHIVED`、完成 run、Revision 历史和详情投影。
- 对已有 run 输入“打开当前 Delivery 的进度面板”，支持 MCP Apps 的宿主应渲染 `Delivery Graph 运行看板`；看板可见时每 15 秒自动更新，隐藏时暂停，点击“刷新状态”立即重读 `open_delivery_dashboard`。宽面板 Graph 不产生水平溢出，窄面板转为纵向并展示前置项。所有刷新都只能调用只读 Dashboard 工具；不支持 UI 的宿主必须继续返回可读文字和结构化结果，且不得改用 `graph_frontier` 模拟只读刷新。
- Codex 与 Claude manifest 都只声明当前实际 payload，Plugin 包中不得保留生命周期命令目录。敏感 MCP 工具必须继续触发各宿主自身的审批，不得由 Plugin 自动批准。
- 有效 Adapter/workspace 调用 `plan_dispatch_batch` 时应创建统一 AUTO assignment；缺失宿主 Adapter、workspace/Git/project scope、容量或资源条件时必须在 claim 前 fail closed，不能靠模型输入或未声明元数据绕过。
- AUTO child 使用错误/过期 reservation、错误 decision fingerprint、错误 attempt/workspace/scope 或错误 operation 时，`dispatch_loop` 或后续 mutation 必须拒绝；协调器刷新 frontier，等待 reservation/lease 恢复规则，不代交结果。

真实冒烟必须验证宿主确实按 assignment 创建独立 TASK/Review child，但 Plugin 只看到 Adapter 提供的 workspace/receiver 元数据，不对 parent-child、receiver 延续或 reviewer 独立性提供密码学证明。测试记录只能作为宿主编排证据，不能宣称 Controller 已认证这些关系。

## 升级

### 升级前

1. 记录当前 `codex plugin list --json` 或 `claude plugin list --json` 输出中的版本。
2. 0.34.2 不再读取用户级 `orchestrator.json`；机器上残留的 schema v1/v2 文件不会阻断 MCP，也不参与派遣。无需在升级前编辑或删除它。
3. 对 0.31 及更早版本的旧式 manual Graph run，先在旧版本完成或取消。当前版本的 manual Graph 只能由 `start_manual_handoff` 从精确 `HANDOFF_READY` 快照创建，且 MANUAL 只允许 TASK；不要把旧 run 当作新协议恢复。
4. 自动 schema v3 Graph 可在新会话通过 `workspace_status → graph_frontier` 恢复。升级前仍建议让正在写结果的 Loop 完成，避免恰好跨越 Plugin/Adapter 版本切换窗口。
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
| `dispatch_loop(AUTO)` 返回 reservation、decision fingerprint、attempt、workspace 或 scope 不匹配 | child 停止 Loop 与仓库操作；协调器刷新 frontier，并按 reservation 租约/恢复规则重新计划，不猜测或重写 assignment |
| heartbeat/progress/pause/result 返回 operation 不匹配 | 停止旧 operation；租约仍有效时由原 receiver 使用精确 operation，无法恢复时等待 lease 回收并派遣新 attempt |
| 宿主收到结构化 429 | Plugin 不自动记录；宿主停止供应商调用，保留真实 `resetAt`，能显式 pause 时使用当前 operation，否则等待 lease 回收，到期用一次性宿主唤醒重新读取 frontier |
| LIGHT 执行中发现影响扩大 | 提交 `REPLAN_REQUIRED`，保持同一 `delivery.id` 准备 `STANDARD` Revision |

恢复过程中不得手工伪造 heartbeat、progress、operation、reservation、decision fingerprint 或终态。工作区代码可以保留并由新 attempt 重新检查，但调度状态只通过 Plugin MCP 改变。

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

回滚到 0.32.0 前应先保存 0.33.x 手动开发包中的 progress/acceptance；0.32.0 不会生成完整冻结内容包，也不识别 `requirementSnapshotStatus`。自动 schema v3 Graph 仍应优先在当前版本完成，避免在活动 claim 期间跨版本切换 Plugin/Adapter 协议。回滚不修改项目数据库，不通过删除 cache 伪造版本切换。
