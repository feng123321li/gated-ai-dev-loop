# Delivery Graph

**分层交付 Graph 控制面**

`delivery-graph` 把已经确认的软件需求冻结为可执行、可审查、可恢复的 Delivery Graph，再协调宿主原生 Agent 完成实现、分层 Review 和最终验收。

当前版本：**0.39.0** · Schema：**v3** · 运行时：**Python 3.10+，仅标准库**

## 它做什么

Delivery Graph 是交付控制面，不是代码生成模型。它负责：

- 把需求组织为递归 `Delivery → GROUP → TASK` Graph。
- 按依赖与资源冲突计算可并行的工作批次。
- 在自动执行和手动开发之间保持同一份冻结需求与 Review Graph。
- 用 reservation、claim、heartbeat、lease 和重试管理长时间运行的 Agent。
- 对 `TASK`、`GROUP`、`Delivery` 执行与风险匹配的质量门禁。
- 用 SQLite 保存权威状态，用不可变 Revision 记录需求变化。
- 在支持 MCP Apps 的宿主中以内嵌只读看板展示 Graph、活动 Loop、告警和 Revision 历史。

具体如何分析、编码、测试和修正，由每个 WorkLoop 自主决定；提交、合并、推送、发布和外部系统授权仍由宿主或用户负责。

## 核心流程

```text
确认需求
  → 检查真实代码和影响范围，选择 LIGHT / STANDARD
  → 冻结 Delivery Graph 与 Git 开发基线
  → 选择自动执行 / 手动开发
  → TASK 实现
  → 分层 Review（STANDARD）
  → 用户最终验收
```

Graph 的纵向层级承载审查边界，横向 DAG 承载依赖和并发：

```text
Delivery
├─ GROUP（可选，可递归）
│  ├─ TASK
│  ├─ TASK
│  └─ GROUP
└─ TASK
```

- `TASK` 是唯一执行叶子。
- `GROUP` 只在存在真实的并行汇合、依赖边界或分层 Review 时使用。
- 无依赖且 `resourceClaims` 不冲突的节点可以并发。
- Frozen Graph 固定目标、依赖、资源、项目范围和验收边界，不固定 Loop 内部实现计划。

## 两种保障档

| 保障档 | 适用范围 | 验证路径 |
|---|---|---|
| `LIGHT` | 单一根 TASK、局部内部改动，且不触及接口、数据、权限、安全、生产部署或不可逆副作用 | TASK 定向验证 → 用户验收 |
| `STANDARD` | 默认选择；跨模块、并行、依赖复杂或影响无法可靠排除 | TASK Review → 逐层 GROUP Review → Delivery Review → 用户验收 |

`LIGHT` 必须附带基于真实代码和 diff 的理由。执行中发现影响扩大时，同一 Delivery 创建新的 `STANDARD` Revision，不降低既有 Review 要求。

## 两种执行模式

| 模式 | 行为 |
|---|---|
| 自动执行 | 宿主创建或复用稳定的 linked worktree，启动后台协调任务，并派遣独立的宿主原生 receiver 消费 Graph frontier |
| 手动开发 | 先生成自包含 handoff；接收 CLI 在选定工作区启动同一 Graph，人工完成 TASK，随后仍进入相同的自动 Review Graph |

自动模式不会让 Controller 创建分支或切换 worktree；这些动作由 Codex 或 Claude Code 的宿主能力完成。手动模式不会绕过 Review，也不会把 Review 降级为手动 claim。

自动选择会按 Git repository identity 和 `branchRef` 原子预留 worktree setup。同一 Delivery 的重复请求只恢复既有 setup，不会再次派发同一路径创建；不同 Delivery 不能预留同仓同分支。宿主通过 `report_worktree_setup` 上报创建阶段、百分比和心跳；超时或失败后先核对旧进程与半成品，再由 Controller 原子授予唯一重试。`hostDispatch` 始终携带精确 `gitBinding`，宿主生成了其他分支时先恢复冻结分支，错误分支已有改动则停止并审查。多项目 Delivery 会为全部 `READ_WRITE` Git scope 返回 `projectWorktreeSetups`；所有项目仍由一个后台 coordinator 管理并向同一控制面报告进度。

## 交互与 Git 基线

准备完成后，Controller 通过一个 `pendingInteraction` 依次解决两件事：

1. `DEVELOPMENT_BASELINE`：确认当前本地分支，或选择从主线创建新开发分支。
2. `EXECUTION_MODE`：选择自动执行或手动开发。

主要安全边界：

- Controller 只读检查 Git；不执行 `fetch`、`switch`、`commit`、`merge`、`push` 或发布。
- 分支选择只枚举本地分支。选择“从主线创建”时会把基线提交固定为当时的主线 HEAD；primary 位于干净 feature 时还会默认推荐 stacked 子分支，把父 feature HEAD 冻结为基线并最终合回父分支，无需切换 primary。
- 工作树已有业务改动时，用户必须确认这些改动属于本 Delivery，并回传精确状态指纹。
- 手动 handoff 启动前若 Git 基线漂移，启动会先被阻断；重新确认后恢复原 Revision，或在 binding 变化时生成下一不可变 Revision。
- 一个 Delivery 可以覆盖多个本地 Git 项目，但每个 Git project scope 都必须提供完整 binding；缺失时提前 fail closed，不从顶层偏好猜测其他仓库。
- 分支占用按 Git common directory 区分；不同仓库可以使用同名 feature 分支，同一仓库不能被两个活动 Delivery 预留同一分支。
- 多仓手动启动出现 Git 漂移时 fail closed；必须恢复冻结基线，或用完整多仓 bindings 显式创建下一 Revision。

## 状态与恢复

`.layered-delivery/scheduler.db` 是需求和调度状态的机器权威，Markdown 文件只是人类可读投影。不要直接编辑数据库或控制面生成物。

新会话从 `workspace_status` 恢复当前 Delivery，再读取 Graph frontier。worktree setup 与活动 receiver 都有独立 heartbeat 和 lease；前者通过 `worktreeSetup.progressMonitor` 在主仓显示全部项目，后者通过 Graph `progressMonitor` 显示 TASK 与 Review。失联、租约过期或可重试失败只在各自安全边界恢复。需求发生变化时创建同一 Delivery 的下一 Revision，不覆写已经冻结的版本。

Codex Desktop 的 `SubagentStart` Hook 会在隔离账户与登录用户 profile 不同时，继续以宿主 transcript 路径验证真实 sessions 根。自动 receiver 最多重派一次仍无法启动时，用户可对 clean、READY、从未领取且无有效 reservation 的单个 TASK 显式调用 `handoff_ready_automatic_task`；只把该 TASK 改为人工接收，AUTOMATIC Graph、Revision、基线、双 fingerprint 和后续自动 Review 均保持不变。

## 安装

Plugin 同时面向 Codex 和 Claude Code。安装前准备 Python 3.10+，并确保 `python` 在宿主终端可用。

Codex：

```text
codex plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git --ref master
codex plugin add delivery-graph@majorbio-skills
```

Claude Code：

```text
claude plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git
claude plugin install delivery-graph@majorbio-skills --scope user
```

安装或升级后新建会话，让 Skill、MCP Server 和 Hook 从同一版本加载。团队升级、卸载和回滚步骤见[团队运维](docs/team-operations.md)。

## 使用

在实际项目的新会话中直接提出需求：

```text
使用 delivery-graph 处理这项需求：<目标、约束和验收标准>
```

典型交互只有以下几步：

1. Agent 检查当前代码与影响范围，与你确认需求和保障档。
2. `preview_hierarchy` 生成 Graph 基线和关联文档。
3. 若项目使用 Git，先确认开发基线；多 Git 项目需逐项目提供 binding。
4. 选择自动执行或手动开发。
5. 执行期间查看当前 frontier、进度、测试、心跳和 Review findings。
6. 所有要求完成后查看验收报告，并由你最终确认。

执行期间可直接说“打开当前 Delivery 的进度面板”。宿主会调用 `open_delivery_dashboard`；支持 MCP Apps UI 时显示内嵌看板，不支持时继续返回相同的文字与结构化进度。面板刷新只读取状态，不调用会推进调度状态的 `graph_frontier`。

新业务目标默认创建新 Delivery。只有明确继续同一需求，或运行结果要求 replan，才沿用原 `delivery.id` 创建下一 Revision。

## 支持的宿主

- **Codex**：Plugin Skill、MCP Server、工具授权 Hook 和宿主原生 worktree receiver。
- **Claude Code**：Plugin Skill、MCP Server、工作区证明 Hook、结构化限额处理和宿主原生 receiver。

外部 CLI 可以接收手动 handoff，但不会因此成为可自动派遣的可信 receiver。Plugin 只信任能证明宿主生命周期和接收身份的 Adapter。

## 项目结构

| 路径 | 用途 |
|---|---|
| `src/hdg/` | Controller、Graph Runtime、Repository 与 MCP Adapter 源码 |
| `skills/delivery-graph/` | 规范 Skill、references 与生成的运行包 |
| `plugins/delivery-graph/` | Codex / Claude Code Plugin 产物 |
| `.agents/plugins/marketplace.json` | 本仓库的 Agent Plugin 开发 Marketplace |
| `tests/` | Graph、调度、Git、Hook、协议与投影测试 |
| `examples/team-loops/` | 可校验的 LIGHT / STANDARD hierarchy 模板 |
| `scripts/build_skill.py` | 从源码同步 Skill 与 Plugin 运行包 |
| `scripts/validate_release.py` | 离线发布候选一致性校验 |

## 开发验证

```text
python scripts/build_skill.py
python -X utf8 -m unittest discover -s tests -t .
python -m compileall -q src tests scripts skills/delivery-graph/scripts plugins/delivery-graph
python scripts/validate_release.py
git diff --check
```

维护源码时直接修改源码、Skill、Plugin、文档和测试，不创建业务 `.layered-delivery/**` 运行包。项目只维护完整 schema v3，不提供旧 schema 兼容入口。

## 文档

- [Skill 入口](skills/delivery-graph/SKILL.md)
- [规划、Schema v3 与冻结](skills/delivery-graph/references/planning-quickstart.md)
- [执行、并发与恢复](skills/delivery-graph/references/execution-quickstart.md)
- [分层 Review 与验收](skills/delivery-graph/references/acceptance.md)
- [项目实现结构](docs/project-engineering.md)
- [宿主兼容矩阵](docs/host-compatibility.md)
- [版本记录](CHANGELOG.md)
