# Layered Delivery

`layered-delivery` 把已经确认的需求冻结为递归 Delivery Graph，再协调多个独立 Agent/WorkLoop 完成实现、逐层审查和最终验收。

当前版本：**0.34.3**

## 核心流程

```text
沟通并确认需求
  → 检查真实改动内容和影响范围，选择 LIGHT / STANDARD
  → 生成 Delivery Graph 基线与全部关联文档
  → Controller 提供“自动执行（默认）/ 手动开发”统一交互
  → 自动执行：立即准备并冻结 Graph，进入 TASK WorkLoop 自动派遣
  → 手动开发：生成 handoff；接收 CLI 启动同一 Graph，手动完成 TASK
  → LIGHT：单一 TASK 定向验证
  → STANDARD：TASK Review → 逐层 GROUP Review → Delivery Review
  → 用户最终验收
```

人负责确认需求、选择执行方式和最终验收。调度器负责依赖、资源、并发、租约、恢复与 Review 顺序；每个 WorkLoop 自主决定如何分析、实现、测试和修正。

## Graph 模型

```text
Delivery
├─ GROUP（可选，可递归）
│  ├─ TASK → TASK Review
│  ├─ GROUP → GROUP Review
│  └─ GROUP 完成点 → 本层 GROUP Review
└─ 根工作项完成 → Delivery Review → 用户确认
```

- `TASK` 是唯一执行叶子。STANDARD 中每个 TASK 都有独立 Review；LIGHT 仅允许一个根 TASK 并直接进入用户确认。
- `GROUP` 只用于真实的依赖、并行汇合或分层审查，不强制存在。
- 兄弟节点之间可以声明 DAG 依赖；无依赖且资源不冲突的节点可以并发。
- `resourceClaims` 是跨 Delivery 生效的精确排他资源键，不是文件路径授权。
- Frozen Graph 保存目标、依赖、资源、项目范围和 Loop 边界，不冻结 Loop 内部实现计划。
- `LIGHT` 由规划 Agent 根据真实代码/diff 和影响范围判断，只适用于无接口、数据、权限、安全、生产部署等关键边界的局部修改；影响扩大时必须升级同一 Delivery 的 STANDARD Revision。

## 解决的问题

- 把跨项目、跨模块需求拆成可恢复的递归 `GROUP` / `TASK` Graph。
- 为 STANDARD 交付的 TASK、GROUP 和 Delivery 提供强制分层 Review；LIGHT 保留定向验证与最终人工验收。
- 在同一批 frontier 中并发派遣互不冲突的宿主原生 Agent。
- 自动派遣始终继承当前宿主模型；Loop 可在内部按成本和任务需要使用其他模型、effort 与 Worker。
- 用 claim、heartbeat、lease、重试和容量断路器处理长时间运行与失联。
- 支持一个 Delivery 覆盖多个本地 Git 项目，并冻结各项目的基线与权限上限。
- 用不可变 Delivery Revision 管理验收前的需求调整和安全结果携带。
- 以 SQLite 保存需求与调度状态，Graph 运行后再用哈希事件链记录历史，同时生成可读的中文进度与验收投影。
- 后台 Loop 在代码检查、测试、问题修复和复审等阶段上报结构化进度；主 Agent 持续展示外层 receiver、测试、心跳、剩余租约及失联预警。内部 Worker 的 agent/model/effort 只从最终 `workerTelemetry` 非权威展示，未知值为 `unreported`。

## 能力边界

| Layered Delivery 负责 | WorkLoop 或宿主负责 |
|---|---|
| 何时运行哪个 TASK/Review | 如何分析、编码、设计、测试或讨论 |
| Graph 依赖、资源锁和并发批次 | Loop 内部计划、Gate 与修正循环 |
| 可信外层 receiver 的预留、派遣与身份边界 | Loop 内 Worker、模型、effort、成本与沙箱权限 |
| 租约、暂停、恢复和基础设施重试 | 外部系统凭据与不可逆操作授权 |
| 保障档、分层 Review 和最终确认顺序 | Git commit、merge、push、发布与迁移 |

调度器不解析 `loop.payload` 或 `loop.result` 的业务语义，不扫描 PATH 或执行本机 CLI 探针，也不会把外部 CLI 当成可自动派遣的 Agent。

## 使用方式

Plugin 激活后，在 Codex 或 Claude Code 的新会话中提出需求，并要求使用 `layered-delivery`。Agent 会按当前工作区状态选择创建、继续或恢复：

1. 读取工作区状态和当前 schema v3 契约。
2. 与用户沟通需求，检查真实代码、预计或已有 diff 和影响范围；无法可靠判断时使用 STANDARD。调用 `preview_hierarchy` 登记 `CHOICE_READY`，先生成共享数据库、根总览、baseline 及全部关联文档。
3. 只有 `artifactsReady=true` 后，宿主才原样或机械映射 Controller 返回的 `executionChoice`。交互只有“自动执行（默认）/ 手动开发”两个按钮；直接输入文字继续需求沟通，不创建“其他”按钮。
4. 用户选择自动执行后调用一次 `select_execution_mode(AUTOMATIC)`；Controller 立即完成 prepare、freeze 并要求宿主进入 frontier 自动派遣，不增加第二次确认。
5. 用户选择手动开发后调用一次 `select_execution_mode(MANUAL)`；Controller 把需求转为 `HANDOFF_READY`，生成自包含 handoff，并返回已嵌入文件的 `manualHandoff.receiverPrompt`。交接阶段不创建 Graph Run、workspace 绑定、任务或 worktree。
6. 接收 CLI 在任何代码工作前调用 `start_manual_handoff`，用双 fingerprint 在实际工作区启动同一 Graph。只有 TASK 实现走 `MANUAL` claim；TASK/GROUP/Delivery Review 全部沿用自动派遣、独立审查与 findings 闭环。
7. 自动模式及 manual Graph 的 Review 批次由当前可信宿主直接预留独立 receiver；receiver 继承当前宿主模型，不进行调度前模型推荐或调整。
8. 两种模式都持续消费 frontier，调度当前可运行的独立 WorkLoop。
9. STANDARD 在所有 Review 完成后展示验收报告；LIGHT 在唯一 TASK 定向验证完成后展示改动与影响依据。两者都等待用户最终确认。

执行方式的区别：

| 模式 | 选择后的行为 |
|---|---|
| 自动执行 | 开始实际开发：按需创建 worktree，准备并冻结 Graph，再由当前宿主派遣可证明为 `HOST_NATIVE` 的 Agent |
| 手动开发 | 先登记 `HANDOFF_READY` 并生成同结构内容包；接收 CLI 选定工作区后调用 `start_manual_handoff`，手动完成 TASK，随后执行与自动模式完全相同的 Review Graph 和最终确认 |

新业务目标默认创建新 Delivery。自动与手动开发使用同一个稳定的 `.layered-delivery/<delivery-id>/` 和同结构投影，不再创建共享 `handoffs` 目录。选择前的 `CHOICE_READY` 已创建 SQLite、根总览与全部基线文档，但未绑定 workspace、创建 Run 或 worktree；手动响应的 `requirementSnapshotStatus=FROZEN` 表示需求内容已冻结，仍不代表 Graph 已 prepare、freeze 或创建 Run。一个工作区最多绑定一个未结束 Delivery；并行需求真正开始开发时使用独立对话工作区，Git 项目优先使用 linked worktree。只有用户明确要求继续同一需求，或当前 Loop 返回 `REPLAN_REQUIRED`，才在原 `delivery.id` 上创建下一 Revision。

## Receiver、Worker 与并发

- `plan_dispatch_batch` 只为当前可信宿主 Adapter 预留外层 receiver。assignment 使用 `hostAdapterId`、`receiverAgentId` 与 `modelPolicy=CURRENT_HOST_INHERIT`，不包含模型推荐、reasoning class 或 effort。
- 只有外层 receiver 能 claim、heartbeat、progress、pause、resume 和提交 result。普通 helper 与 Loop 内 Worker 不能获得 reservation、attestation 或 operation。
- receiver 完成首次独立 heartbeat 后，可按成本和任务需要自行使用 Codex、Claude、Grok、DeepSeek 或其他 Worker，并自主选择模型、effort、并发与升级路径。
- 新增 Worker 供应商无需修改 Layered Delivery；只有要让供应商直接领取 Graph 时，才需要实现一个能证明宿主生命周期和 receiver 身份的可信外层 Adapter。
- 最终 `outcome.result.workerTelemetry` 可按 phase 报告内部 Worker 的 `agent`、`model` 和 `reasoningEffort`。未知写 `unreported`；这些值只用于展示、成本分析和 Review，不参与路由、授权、指纹或重试。

## Plugin 内置协调策略

外层 receiver 的最大并发固定为 4，额度耗尽固定采用 `PAUSE_AND_RESUME`。策略随 Plugin 版本发布，不读取用户级 `orchestrator.json`，也不提供中央设置工具或 MCP Apps 面板。Codex 与 Claude 因此不会再被机器上残留的旧配置阻断 MCP 启动。

中央协调本身仍由 Controller 事务维护：未过期 reservation、已 claim receiver、`resourceClaims` 和容量断路器继续限制重复或冲突派遣。模型、Worker、Adapter allowlist 与 Review 多样性仍不属于控制面配置。

## 状态、隔离与恢复

- `.layered-delivery/scheduler.db` 是需求与调度状态的机器权威；Graph 启动后事件链记录运行历史，Markdown 仅供人类查看。
- Agent 只能通过 Plugin MCP 读取和改变调度状态，不能直接修改数据库或投影。
- linked worktree 共享同一控制数据库，但使用独立 `workspaceKey` 隔离 Delivery。
- 多项目 Delivery 的可写仓库使用同名 feature 分支，并分别冻结自己的基线提交。
- 软额度阈值可提前暂停；结构化硬 429 由宿主容量回调暂停同一容量域，并在真实恢复时间后一次性唤醒。
- 租约过期、执行器失联和物化状态损坏分别由 frontier、`advance_graph` 和事件重建处理。
- `WORKER_LOST` 自动重试可由同一 Adapter 的新编排会话安全接管接收方信任根，并记录 `RECEIVER_ROOT_ROTATED` 审计事件；恢复无需重冻，也无需直接修改 `scheduler.db`。不同 Adapter、仍有已认领 Loop 或旧接收凭据仍有效时继续 fail closed。

完整执行和恢复规则见[执行快速说明](skills/layered-delivery/references/execution-quickstart.md)与[MCP、状态和投影](skills/layered-delivery/references/mcp-transport.md)。

## 宿主支持

仓库构建同一份双宿主 Plugin：

- Codex：MCP Server、`SubagentStart` 与 Loop mutation Hook。
- Claude Code：MCP Server、PreToolUse 与结构化限额失败 Hook。

宿主原生能力决定当前会话是否能创建并认证外层 receiver。外部 CLI 和内部 Worker 不会被升级为可信执行器；安装或升级 Plugin 后应新建会话，使 Skill、MCP 和 Hook 重新加载。

## 项目结构

| 路径 | 用途 |
|---|---|
| `src/hdg/` | Python Controller、Graph Runtime、Repository 与 MCP Adapter 源码 |
| `skills/layered-delivery/` | 规范 Skill、按需 references 和生成的运行包 |
| `plugins/layered-delivery/` | Codex / Claude Code 双宿主 Plugin 产物 |
| `tests/` | schema、调度、并发、Hook 与投影测试 |
| `examples/team-loops/` | 可校验的团队 LIGHT / STANDARD hierarchy 模板 |
| `scripts/build_skill.py` | 从源码重建 Skill 和 Plugin 运行包 |
| `scripts/validate_release.py` | 无网络发布候选一致性校验 |
| `scripts/host_smoke.py` | 双宿主本地探测与显式 opt-in 的同宿主原生派遣冒烟 |

项目使用 Python 3.10+ 和标准库，只维护完整 schema v3，不提供 CLI 入口或旧业务 schema 迁移。

## 开发验证

```text
python -m unittest
python -m compileall -q src tests skills/layered-delivery/scripts plugins/layered-delivery
python scripts/build_skill.py
python scripts/validate_release.py
python scripts/host_smoke.py probe --json
python -X utf8 <skill-creator>/scripts/quick_validate.py skills/layered-delivery
python -X utf8 <plugin-creator>/scripts/validate_plugin.py plugins/layered-delivery
git diff --check
```

## 文档导航

- [规划、Schema v3 与冻结](skills/layered-delivery/references/planning-quickstart.md)
- [外层 receiver、Loop 内 Worker、权限与遥测](skills/layered-delivery/references/agent-execution-boundary.md)
- [Frontier、并发、租约与恢复](skills/layered-delivery/references/execution-quickstart.md)
- [分层 Review 与最终验收](skills/layered-delivery/references/acceptance.md)
- [MCP、状态权威与人类投影](skills/layered-delivery/references/mcp-transport.md)
- [Graph Engineering 架构](docs/graph-engineering-upgrade.md)
- [项目实现结构](docs/project-engineering.md)
- [团队安装、升级、恢复、卸载与回滚](docs/team-operations.md)
- [宿主兼容矩阵](docs/host-compatibility.md)
- [团队 Loop 模板与 resource claim 规范](docs/team-loop-templates.md)
- [版本记录](CHANGELOG.md)
