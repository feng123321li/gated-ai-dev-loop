# 宿主兼容矩阵

兼容性分成两层，不能混为一个“支持”：

- **核心契约**：Python Controller、schema、SQLite、生成产物、调度协议测试和 stdio MCP 握手通过。
- **真实宿主**：实际 Codex 或 Claude Code 会话加载候选 Plugin，创建原生子 Agent，并完成 claim、progress、heartbeat、result，最后到达待用户确认门禁；冒烟程序不得代替用户确认。

当前 canonical Plugin/Skill 名为 `delivery-graph`，展示名为“分层交付 Graph 控制面”。`.layered-delivery/` 只是稳定的项目数据目录，不随 Plugin identity 更名。

## 0.39.10 发布候选矩阵

0.39.10 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，将 Graph 协调改为宿主事件优先，并让分层 Review 在独立判断不变的前提下复用与相关代码状态精确绑定的上游验证证据。

- 活跃 receiver 优先使用宿主原生 completion wait；超时只读 `graph_status`，并按首次心跳、heartbeat/progress stale、lease、reservation 与资源容量恢复中的最早有效时刻唤醒。稳定 `changeFingerprint` 排除纯时间倒计时，避免无变化重复播报。
- `graph_frontier` 的 no-op 不再修改 run 时间或重写投影；reservation/lease 精确到点即失效。CLAIMED reservation 的旧短 TTL 不参与后续唤醒，跨 Delivery 的真实资源冲突按 blocker deadline 恢复。
- TASK 验证绑定 affected scope 与 workspace snapshot；TASK/GROUP/Delivery Review 只复用 `PASSED + EXACT_MATCH` 证据，相关代码、环境或高风险边界变化时定向或完整复跑。Review 结果提交前重新计算 freshness，P0/P1 继续要求闭环。
- scope 状态按项目批量捕获、跨证据去重；Review context 使用紧凑 evidence index 与 workspace diff 引用，避免逐文件 Git 子进程和重复大 payload。
- 核心候选已通过 381 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Claude Plugin manifest 与差异校验。实际 Codex/Claude 会话仍需按本页定义完成宿主原生 child 冒烟，并停在 `RECORD_USER_CONFIRMATION`。

## 0.39.9 发布候选矩阵

0.39.9 保持 32 个 MCP 工具、schema v3、无 Hook 模式和 `CURRENT_WORKSPACE_SERIAL`，完成旧 worktree setup 协议的物理清理并修复调度优化中的存量状态与重试边界。Controller 不创建 linked worktree；当前目录即使是既有 linked checkout，也只作为普通 current workspace 使用。

- Plugin 不再包含 `delivery-coordinator` Agent，也不公开 worktree setup reservation、progress、lease 或 report 路径。Claude 主会话按冻结 `gitBinding` 在当前 checkout 准备分支、调用 `resume_execution_mode`，再通过 `plan_dispatch_batch` 启动独立 receiver。
- 新提交的 hierarchy、数据库变更、表字段、索引、约束、外键和验证步骤继续执行有界资源限制；同 state contract 下已持久化的数据仍按原指纹与规范形态读取，不因新上限失去可恢复性。
- READY 刷新按 `run_id + node_id + MAX(attempt)` 选择最新尝试，不受 SQLite 索引扫描方向影响；既有 scheduler 数据库在 state contract 校验通过后幂等补齐 run、lease、event 与 dispatch reservation 索引。
- 核心候选已通过 371 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、release candidate、Claude Plugin manifest 与差异校验。实际 Codex/Claude 会话仍需按本页定义完成宿主原生 child 冒烟，并停在 `RECORD_USER_CONFIRMATION`。

## 0.39.8 发布候选矩阵

0.39.8 提供 32 个 MCP 工具、schema v3 与无 Hook 模式。同一物理 checkout 可以绑定多个 Delivery，但状态必须用显式 `rootId` 路由，执行统一为 `CURRENT_WORKSPACE_SERIAL`；Controller 不再公开 worktree setup 工具，也不自动创建 linked worktree。MCP Apps 标准 `tools/call` 失败或精确缺少 project root 时可回退兼容 bridge；服务端只允许同一 Codex legacy 连接、同一 `root_id` 复用此前成功 Dashboard 读取形成的只读 workspace grant。Modern 请求、非 Codex Adapter、显式空 metadata、其他 root、其他只读工具和全部写工具继续失败关闭。

- 看板可见时每 15 秒串行自动刷新，隐藏时暂停；手动刷新仍立即读取 `open_delivery_dashboard`，任何路径都不得调用 `graph_frontier` 推进状态。
- Dashboard Resource 使用 `ui://delivery-graph/dashboard-v2.html`，避免升级后命中旧缓存；无 UI 宿主仍返回相同的文字和 `structuredContent`。
- Graph 宽屏按 rank 横向绘制依赖边；面板空间不足时纵向换行并在节点内显示前置项，不产生水平滚动或节点裁切。
- 同一 checkout 的后续 Delivery 必须等待队首 Run 终态、取消 receiver 租约失效、产生可验证业务 commit、工作树与 index 干净且历史未改写；任何分支、HEAD、scope 或 dirty 漂移都失败关闭。
- TASK/TASK Review 的 Controller 可信 Git 快照会投影为主控制目录下的 `workspace-changes.patch`，供编辑器未打开实际 checkout 时审核 committed、staged、unstaged 与 untracked 变化。
- 核心候选已通过 369 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、发布与差异校验；真实 Edge 已覆盖 1280/900/600/360 四档宽度。实际 Codex/Claude 会话仍需按本页定义验证面板自动/手动刷新与文本降级。
## 0.39.7 发布候选矩阵

0.39.7 保持 33 个 MCP 工具、schema v3 与无 Hook 模式，修复 Codex Desktop 内嵌进度面板刷新，并把 Graph 改为按实际容器宽度切换布局。MCP Apps 标准 `tools/call` 失败或精确缺少 project root 时可回退兼容 bridge；服务端只允许同一 Codex legacy 连接、同一 `root_id` 复用此前成功 Dashboard 读取形成的只读 workspace grant。Modern 请求、非 Codex Adapter、显式空 metadata、其他 root、其他只读工具和全部写工具继续失败关闭。

- 看板可见时每 15 秒串行自动刷新，隐藏时暂停；手动刷新仍立即读取 `open_delivery_dashboard`，任何路径都不得调用 `graph_frontier` 推进状态。
- Dashboard Resource 使用 `ui://delivery-graph/dashboard-v2.html`，避免升级后命中旧缓存；无 UI 宿主仍返回相同的文字和 `structuredContent`。
- Graph 宽屏按 rank 横向绘制依赖边；面板空间不足时纵向换行并在节点内显示前置项，不产生水平滚动或节点裁切。
- 核心候选已通过 342 项 Python 测试（1 项按环境跳过）、编译、Skill/Plugin 镜像、Claude Plugin、发布与差异校验；真实 Edge 已覆盖 1280/900/600/360 四档宽度。实际 Codex/Claude 会话仍需按本页定义验证面板自动/手动刷新与文本降级。


## 0.39.6 发布候选矩阵

0.39.6 提供 33 个 MCP 工具，用户可选执行模式仍只有 `AUTOMATIC` 和 `MANUAL`。AUTOMATIC 的 TASK 与各级 Review 统一由 `plan_dispatch_batch` 预留，再由独立 child 用 reservation、decision fingerprint、receiver context 和 `operation_id` 调用 `dispatch_loop`。本候选删除生命周期 Hook、`claim_current_task` 和 attestation 持久化；新建状态不创建旧认证表，但 Graph compiler 契约仍为 `schema-v3-graph-compiler-v1`。旧 0.39.5 状态只有在 READY、从未 claim 且没有 reservation 时才承诺无需迁移续跑。

- Plugin manifest 不声明 lifecycle Hook，安装和升级都没有 `/hooks` 信任步骤。
- AUTO claim 必须匹配未过期 reservation、Graph/decision fingerprint、node/attempt 和显式 `operation_id`；同一 reservation 与 operation 的响应丢失重试幂等返回已提交 assignment。
- heartbeat、progress、pause 与 result 都显式携带 claim 返回的 `operation_id`，并继续受 workspace、项目 scope、lease 与资源锁校验。
- 独立 Review child 是宿主编排不变量，不再有真实 session、parent-child 或 reviewer 身份的密码学证明；这是无 Hook 模式的已知能力降级。
- Git Delivery workspace identity 使用 Git 历史 lineage 与冻结分支，不使用仓库或 worktree 绝对路径；移动仓库或重建同分支 worktree可恢复，其他分支继续返回 Git branch mismatch，旧路径哈希绑定在原路径首次访问时升级。

发布候选必须完成 Python 全量测试、compileall、UTF-8 Skill 校验、33 工具与生成镜像发布校验、Claude Plugin manifest 校验和差异检查；真实宿主 smoke 不再传递 Hook 事件或绕过 Hook trust。

## 0.39.2 发布候选矩阵

0.39.2 保持 33 个 MCP 工具，把 MANUAL 与 AUTOMATIC 收敛到同一可信 receiver 身份链，并修复 Codex Plugin Hook 未被 manifest 激活、单仓 runtime `projectScopes=[]` 和失败 reservation 必须等待 TTL 的问题。两种 dispatch 都要求宿主 Adapter 为真实原生 child 签发并一次性消费 attestation；AUTO 必须绑定非空 reservation，MANUAL 的 `reservation_id` 必须为 `NULL`。claim 后的 scope、operation、heartbeat、progress、pause、result 和 lease 门禁完全一致，差异只在授权来源。

Codex 候选包必须在 manifest 中显式声明 `./hooks/hooks.json`。真实宿主验证先在新任务的 `/hooks` 审查并信任该 Plugin 的 Hook，再覆盖以下边界：

- Hook 未加载或未信任时，`plan_dispatch_batch` 在创建任何 reservation 前返回 `SCHEDULER_HOST_HOOK_NOT_READY`；恢复信任后才能重新计划。
- AUTO `SubagentStart` 与 MANUAL `dispatch_loop` PreToolUse 都从宿主可信 `.codex/sessions` 中验证真实 child/parent，root/helper、内部 Worker、自定义 `CODEX_HOME` 和伪造 transcript 均 fail closed。
- 普通单仓 Delivery 从顶层 `gitBinding` 与实际 Delivery workspace 合成唯一 `primary` runtime scope；AUTO claim 和 `loop_context` 必须返回该 scope，多仓仍逐 scope 验证。
- `SubagentStart` 已定位 AUTO reservation、但身份/workspace/scope attestation 失败时，TASK 保持 READY，尚未绑定 receiver 的 reservation 立即释放，child 在任何仓库检查或修改前停止。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Adapter attestation、scope 合成、reservation 原子释放与 Hook 配置回归 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | `/hooks` 信任、AUTO/MANUAL 原生 child、真实 transcript 与 mutation 链 |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；Codex Desktop 的历史故障实例为 0.147.0-alpha.6.5。版本号只用于本轮复现与验证，不构成永久最低版本承诺。

## 0.39.1 发布候选矩阵

0.39.1 保持 33 个 MCP 工具，修复 Codex Desktop `SubagentStart` 先于直接 child transcript 首条 `session_meta` 落盘时的 claim 竞态。核心契约必须模拟 transcript 先为空、随后写入合法 session metadata，并验证 Hook 只在当前 child 文件名、可信 sessions 根、精确 parent/role/task 和有效 reservation 全部匹配后原子 claim；超时、伪造路径、自定义 `CODEX_HOME`、错误角色和过期 reservation 继续 fail closed。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Hook 时序、身份绑定与协议回归 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | Codex 空 transcript 竞态已复现；0.39.1 待重新派遣验证 | 原生 child claim、heartbeat 与后续 Loop |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；Codex Desktop 实际失败实例为 0.147.0-alpha.6.5。版本号用于复现记录，不构成永久最低版本承诺。

## 0.39.0 发布候选矩阵

0.39.0 提供 33 个 MCP 工具和一个静态 MCP Apps Resource，并新增数据库 baseline 强制契约、clean primary feature 的 stacked 子分支基线、Codex Desktop sandbox transcript 识别及未领取自动 TASK 的显式人工恢复。核心契约必须验证数据库结构在执行前生成并冻结、缺失设计或 LIGHT fail closed、Loop 只执行 after，以及 `NEW_FROM_CURRENT_BRANCH` 的 child/base/integration binding 与 hostDispatch 完全一致。`SubagentStart` 必须在 Hook 隔离账户与宿主 profile 不同时仍验证真实 transcript；`handoff_ready_automatic_task` 只允许 clean、READY、从未领取且无有效 reservation 的 TASK，并保持 Review 自动派遣。Modern/Legacy 两种 wire shim 继续共享同一 tools/resources dispatcher，`open_delivery_dashboard` 只读取当前状态，UI 不包含控制面写工具或外部资源，无 UI 宿主仍能使用文字与 `structuredContent`。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Adapter、Resource、只读 snapshot 与 UI 静态契约 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | MCP Apps 渲染、刷新和文本降级 |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；这是本轮验证目标，不是永久最低版本。UI 刷新不得调用 `graph_frontier`，按钮也不得绕过宿主审批或 Controller 权限。

## 0.37.3 发布候选矩阵

0.37.3 提供 31 个 MCP 工具，在 0.37.2 的 worktree setup 监控基础上新增显式的完成后 `archive_delivery`。真实宿主必须验证：归档只接受 `COMPLETED`，归档操作经过敏感工具审批，默认状态发现与根总览隐藏归档项，而显式 `root_id` 仍保留完成 run、Revision 历史、事件链和详情投影。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | 归档状态机、SQLite 契约与协议回归 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | 双宿主敏感审批与显式历史查询 |

候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；这是本轮验证目标，不是永久最低版本。Controller 仍不执行 Git 或目录写操作。

## 0.37.2 发布矩阵

0.37.2 提供 30 个 MCP 工具，新增 `report_worktree_setup` 和 worktree setup 进度监控。除 0.37.1 的 reservation、精确分支和多项目场景外，真实宿主必须验证：创建阶段与百分比能在主仓 `progressMonitor` 刷新；30 秒 heartbeat 可续 120 秒租约；超时/失败不会自动重发；核对旧进程与半成品后，并发 retry 只有一个获得新 attempt 与 `IMMEDIATE`。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | setup 状态机、SQLite 并发与协议契约 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | 原生 worktree 心跳、失败核对与安全 retry |

该版本的候选宿主基线为 Codex CLI 0.147.0 和 Claude Code 2.1.226。Controller 不执行 Git 或目录写操作。

## 0.37.1 发布候选矩阵

0.37.1 保持 29 个 MCP 工具，在 0.37.0 双宿主协议上新增 worktree setup reservation、精确分支 dispatch 和多项目 worktree 编排。核心门禁除全量测试、`compileall`、镜像一致性和发布校验外，必须真实验证：同一选择并发调用只有一个 `IMMEDIATE`；宿主错分支 clean/dirty 两条路径；两个 Delivery 同仓同分支 fail closed；两个不同仓库可用同名分支；多项目全部 `READ_WRITE` worktree 就绪前不创建 Graph Run，且只有一个 coordinator 向共享控制根报告。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 / 3.12 / 3.14 | CI 自动 | 不适用 | Python 与 SQLite 并发契约 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex/Claude 结果待回填 | 原生 worktree、分支恢复与后台 coordinator |

0.37.1 的候选宿主版本继续使用 Codex CLI 0.147.0 和 Claude Code 2.1.226；这是本轮验证目标，不是永久最低版本。两个宿主都必须确认 Controller 不执行 Git 写操作，且 secondary project setup 不会启动第二 coordinator。

## 0.37.0 发布候选矩阵

0.37.0 保持 29 个 MCP 工具。Python 全量测试、`compileall`、Skill/Plugin 镜像一致性、协议元数据、发布校验和 diff 检查是核心候选门禁；实际结果以对应候选提交和 CI 为准，不可替代真实宿主验证。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动 | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动 | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动 | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex 结果待回填 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Claude Code 结果待回填 | Claude PreToolUse、StopFailure 与子 Agent |

### 0.37.0 真实宿主验证基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.147.0 | 从待验证的 `delivery-graph` 0.37.0 Marketplace 包安装 | `pendingInteraction` 的 `DEVELOPMENT_BASELINE → EXECUTION_MODE` 顺序；dirty 内容或 index 变化使旧指纹失效；primary checkout 创建独立 worktree 项目任务；manual TASK、单仓手动漂移双分支、多仓漂移 fail closed；`SubagentStart`/`PreToolUse`；待用户确认状态 |
| Claude Code | 2.1.226 | `delivery-graph` 0.37.0 `--plugin-dir` 包及最终 Marketplace 安装 | 相同交互与 Git 漂移边界；普通 MCP 工具自动放行且敏感工具仍询问；`delivery-graph:delivery-coordinator` 在稳定 linked worktree 后台运行；Claude 专用 `PreToolUse`/`StopFailure`；receiver attestation 与 progress/heartbeat/result |

两个宿主都必须确认 Controller 不执行 Git 写操作；Codex 默认 Hook 清单不得包含 `StopFailure`，Claude manifest 必须指向独立的 `claude-hooks.json`。上述版本是候选目标，不是永久兼容承诺。

## 0.36.0 历史发布矩阵

以下内容记录 0.36.0 当时的发布与候选状态，不是 0.37.0 的现行能力说明。

源码发布事实：`main` 与 tag `v0.36.0` 指向提交 `ad19c33`；本地核心契约已完成 258 项 Python 测试、`compileall`、Skill/Plugin 镜像一致性和 `validate_release`（29 个 MCP 工具）校验。上述事实不包含模型账户、Keyring、Hook 信任、Marketplace 安装或原生子 Agent 的真实宿主验证。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex 结果待回填 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Claude Code 结果待回填 | Claude PreToolUse、StopFailure 与子 Agent |

发布管理员完成真实冒烟后，应在发布记录中填写准确宿主版本和结果；矩阵中的“CI 自动”不等于已经验证模型账户、Keyring、Hook 信任或原生 Agent 容量。

当前矩阵只验证可信外层 receiver：Claude 宿主的 claim 必须来自受认证的 `claude-code` receiver，Codex 宿主的 claim 必须来自受认证的 `codex` receiver。PATH 中存在的 CLI 或 Loop 内 Worker 不能取得 Graph 控制面权限。新增外层供应商 Adapter 后必须作为独立矩阵维度验证，不能复用内部 Worker 成功记录宣称支持。

## 0.36.0 历史真实宿主验证基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.146.0 | 从待验证的 0.36.0 Marketplace 包安装 | `DEVELOPMENT_BASELINE → EXECUTION_MODE` 原生选择器顺序与同 Delivery Revision 偏好复用、primary checkout 自动创建 worktree 项目任务且不切换 `main`/`master`、manual TASK 接入、`SubagentStart`、receiver mutation Hook、当前宿主继承策略、待用户确认状态 |
| Claude Code | 2.1.220 | 0.36.0 `--plugin-dir` 发布包及最终 Marketplace 安装 | `DEVELOPMENT_BASELINE → EXECUTION_MODE` 原生选择器顺序与同 Delivery Revision 偏好复用、普通 MCP 工具由 Skill `allowed-tools` 自动放行且敏感 Hook 仍询问、自动 Delivery 在稳定 linked worktree 启动后台 coordinator 且主会话仅监控、PreToolUse Hook 注入工作区 attestation 并同会话续接、manual TASK 接入、receiver attestation、progress/heartbeat/result、StopFailure 兼容 |

上述版本是 0.36.0 当时的真实宿主验证目标，不是永久兼容承诺；文档未记录它们对 0.36.0 的实测通过结果。该版本尚未把脏工作树纳入基线前置交互，`start_manual_handoff` 的 Git 漂移阻断重确认也仍是后续 Phase 2；这些限制已由 0.37.0 的现行契约取代。宿主升级后若 Hook 事件字段、Plugin manifest 或 MCP 工具命名发生变化，应先在自托管 Runner 重跑真实宿主冒烟，再更新矩阵。

## 模型与内部 Worker 兼容

外层调度不选择模型。自动 receiver 继承当前宿主模型与默认 reasoning 设置，模型不进入 reservation、decision fingerprint 或 claim 授权。CC Switch、本地配置、企业网关和其他转发器属于宿主/Loop 内部能力，不改变 Graph 身份。

receiver 可以在 Loop 内使用 Codex、Claude、Grok、DeepSeek 或其他 Worker，并在最终 `workerTelemetry` 中按 phase 非权威报告 agent/model/effort；无法权威观察时写 `unreported`。这些供应商不需要单独调度分支。只有要让某个供应商直接领取 Graph 时，才必须增加并验证可信外层 Adapter。

## 支持状态定义

| 状态 | 含义 |
|---|---|
| 已验证 | 当前版本、当前平台真实完成对应门禁 |
| 核心契约通过 | Controller 与 MCP 合约通过，但未启动真实模型宿主 |
| 候选验证中 | 已登记宿主，尚未完成发布候选真实冒烟 |
| 不支持 | 缺少所需 Plugin、Hook、MCP 或原生 Agent 能力 |

团队对外说明只能使用已经取得的状态；不得把 PATH 中存在某个 CLI 写成“真实宿主已验证”。
