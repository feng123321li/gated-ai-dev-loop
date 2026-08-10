# 宿主兼容矩阵

兼容性分成两层，不能混为一个“支持”：

- **核心契约**：Python Controller、schema、SQLite、生成产物、Hook 单元测试和 stdio MCP 握手通过。
- **真实宿主**：实际 Codex 或 Claude Code 会话加载候选 Plugin，创建原生子 Agent，并完成 claim、progress、heartbeat、result，最后到达待用户确认门禁；冒烟程序不得代替用户确认。

当前 canonical Plugin/Skill 名为 `delivery-graph`，展示名为“分层交付 Graph 控制面”。`.layered-delivery/` 只是稳定的项目数据目录，不随 Plugin identity 更名。

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
