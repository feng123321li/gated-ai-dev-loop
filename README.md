# Delivery Graph

**分层交付 Graph 控制面**

`delivery-graph` 把已经确认的软件需求冻结为可执行、可审查、可恢复的 Delivery Graph，再协调宿主原生 Agent 完成实现、分层 Review 和最终验收。

当前版本：**0.39.21** · Schema：**v3** · 运行时：**Python 3.10+，仅标准库**

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
  → 检查真实代码和影响范围，取得确定性 LIGHT / STANDARD 建议
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
- `GROUP` 只在存在真实的并行汇合或依赖边界时使用；仅当直接子项存在真实 seam 才配置 GROUP Review。
- 无依赖且 `resourceClaims` 不冲突的节点可以并发。
- Frozen Graph 固定目标、依赖、资源、项目范围和验收边界，不固定 Loop 内部实现计划。

## 两种保障档

| 保障档 | 适用范围 | 验证路径 |
|---|---|---|
| `LIGHT` | 单一根 TASK、局部内部改动，且不触及接口、数据、权限、安全、生产部署或不可逆副作用 | TASK 定向验证 → 用户验收 |
| `STANDARD` | 默认选择；跨模块、并行、依赖复杂或影响无法可靠排除 | TASK Review → 可选 GROUP seam Review → Delivery Acceptance/Readiness → 用户确认 |

`LIGHT` 必须先由 Agent 从真实代码和 diff 形成显式分类事实，再由只读 `recommend_assurance_profile` 确定性建议，并保存响应理由。执行中发现影响扩大时，同一 Delivery 创建新的 `STANDARD` Revision，不降低既有 Review 要求。首次体验可直接使用[5 分钟 LIGHT Quickstart](docs/five-minute-quickstart.md)。

## 两种执行模式

| 模式 | 行为 |
|---|---|
| 自动执行 | `select_execution_mode` 立即持久化选择；若需准备当前分支，宿主完成动作后调用 `resume_execution_mode`，再派遣独立的宿主原生 receiver 消费 Graph frontier |
| 手动开发 | 先生成自包含 handoff；接收宿主在选定工作区启动同一 Graph，由经 Adapter 认证的独立原生 receiver 人工完成 TASK，随后仍进入相同的自动 Review Graph |

执行模式与工作区绑定是两个维度，但工作区执行策略只有 `CURRENT_WORKSPACE_SERIAL`。同一物理 checkout 可以管理多个 Delivery 的控制状态，但同一时刻只运行其中一个；每个 Delivery 使用独立分支。已有 Delivery 正在调度时，只有后来选择 `AUTOMATIC` 的 Delivery 才持久进入 `QUEUED`，并在根概览和 Delivery 概览显示“排队中（等待自动调度）”。前一个 Delivery 形成可验证 commit、working tree/index clean、HEAD 仍匹配冻结 binding 且所有 receiver 安全释放后，队首按已记录的自动选择续调，不再次询问执行模式。手动交接也会持久化 Delivery、Revision、hierarchy、双 fingerprint 和完整投影，但保持 `HANDOFF_READY`，不会进入自动队列；接收方显式调用 `start_manual_handoff` 后才创建 manual Graph Run 和 workspace binding。

Controller 只读 Git，不创建 worktree，也不执行 stash 或分支切换。`AUTOMATIC` 选择会授权宿主执行机械 workspace 准备：队首若有既存业务改动，先按 Controller 返回的精确指纹 stash 已跟踪、暂存和未跟踪内容（明确排除 `.layered-delivery/**`），再按冻结 `gitBinding` 创建或切换 Delivery 分支并调用 `resume_execution_mode`；冲突状态或 stash 后仍不干净则等待。若当前目录本身是既有 linked checkout，也只把它视为普通 current workspace，不自动创建新的 worktree。多项目 Delivery 的全部 `READ_WRITE` Git scope 必须一起满足边界后才可切换。

## 交互与 Git 基线

准备完成后，Controller 通过一个 `pendingInteraction` 依次解决两件事：

1. `DEVELOPMENT_BASELINE`：确认当前本地分支，或选择从主线创建新开发分支。
2. `EXECUTION_MODE`：选择自动执行或手动开发。

主要安全边界：

- Controller 只读检查 Git；不执行 `fetch`、`switch`、`commit`、`merge`、`push` 或发布。
- 分支选择只枚举本地分支。选择“从主线创建”时会把基线提交固定为当时的主线 HEAD；当前 workspace 位于干净 feature 时还会默认推荐 stacked 子分支，把父 feature HEAD 冻结为基线并最终合回父分支。宿主仍须等父 Delivery 达到串行安全释放边界后，才创建或切换到子分支。
- 只有直接 adoption 当前脏分支时，用户才必须确认全部改动属于本 Delivery 并回传精确状态指纹；选择另一个 Delivery 分支时不归属这些改动，而是在该 Delivery 成为队首后走自动 stash 准备。
- 手动 handoff 启动前若 Git 基线漂移，启动会先被阻断；重新确认后恢复原 Revision，或在 binding 变化时生成下一不可变 Revision。
- 一个 Delivery 可以覆盖多个本地 Git 项目，但每个 Git project scope 都必须提供完整 binding；缺失时提前 fail closed，不从顶层偏好猜测其他仓库。
- 普通单仓 Delivery 可以只冻结顶层 `delivery.gitBinding`；运行时会从该 binding 与实际 Delivery workspace 合成并验证唯一 `primary` scope，receiver 不会取得空的 `projectScopes`。
- 分支占用按 Git common directory 区分；不同仓库可以使用同名 feature 分支，同一仓库中的不同 Delivery 必须使用各自独立分支。`CURRENT_WORKSPACE_SERIAL` 只允许它们在可验证 commit、clean tree、HEAD 未漂移且 receiver 已释放后依次切换同一 checkout，不允许跨 Delivery 并行运行。
- 多仓手动启动出现 Git 漂移时 fail closed；必须恢复冻结基线，或用完整多仓 bindings 显式创建下一 Revision。

## 状态与恢复

`.layered-delivery/scheduler.db` 是需求和调度状态的机器权威，Markdown 文件只是人类可读投影。不要直接编辑数据库或控制面生成物。
一个实际 workspace 可以绑定多个未结束 Delivery；每个 Graph、Revision、run 与验收仍按 `rootId` 隔离。无参 `workspace_status` 遇到多个未结束绑定时返回 `DELIVERY_SELECTION_REQUIRED`，候选中的非队首自动 Delivery 标记为 `QUEUED`；调用方必须用当前会话保存的 `rootId` 显式重查，不能按更新时间猜选。未绑定的 `CHOICE_READY/HANDOFF_READY` 也只允许显式 `rootId` 恢复。控制状态隔离不等于文件隔离：同一物理 checkout 只能按 `CURRENT_WORKSPACE_SERIAL` 依次运行各 Delivery。

TASK receiver 调用 `record_loop_result` 时，Controller 会从已验证的可写 Git scope 自动采集相对冻结 `baseCommit` 的当前 workspace 变更文件和有界 diff，并写入该 `rootId` 的 TASK 验收报告。它覆盖已提交、暂存、未暂存和未跟踪文本内容，因此验收可见性不依赖先 commit；该证据是提交时刻的 workspace 快照，不替代可验证 commit、clean tree 或归属判断。TASK 控制目录还会生成由 `acceptance.md` 相对链接的 `workspace-changes.patch`，用户可在主控制根直接审核；附件由 SQLite outcome 重建，不依赖原执行目录继续存在。


新会话用保存的 `rootId` 显式调用 `workspace_status(root_id=...)` 恢复目标 Delivery，再读取 Graph frontier；无参调用出现多个候选时只做选择，不推进任何候选。活动 receiver 通过 Graph `progressMonitor` 显示 TASK 与 Review，并由 heartbeat 与 lease 治理；失联、租约过期或可重试失败只在各自安全边界恢复。需求发生变化时创建同一 Delivery 的下一 Revision，不覆写已经冻结的版本。

AUTOMATIC 的每个 READY TASK 与 Review 都先由 `plan_dispatch_batch` 生成一次性 reservation，再由独立宿主原生 receiver 用匹配的 decision fingerprint、自己的 context 和显式 `operation_id` 调用 `dispatch_loop`。存在共享 Skill Hint 时，assignment 同时携带具体 catalog 名与建议性 `receiverPrompt`：Codex 使用 `$skill-name`，Claude Code 使用原生 Skill tool；适用且可用时尽量触发，不适用或不可用可跳过，不形成成功门禁。后续 mutation 继续受 workspace、Graph、项目 scope、lease 与 operation 校验。

后台 receiver 运行时不忙轮询：当前 frontier 的立即 action 全部消费后，宿主按 `progressMonitor.waitDirective` 使用原生完成事件等待；无事件只在 `pollNotBefore` 做一次只读 `graph_status`，该截止直接对齐首次心跳、进度陈旧、失联或租约等下一个有意义健康阈值，不再固定每 10 秒刷新。receiver 事件、`nextWakeAt` 或 `ADVANCE_REQUIRED` 才调用一次 `graph_frontier`。`changeFingerprint` 未变化时不重复播报相同进度。

验证采用证据优先策略。TASK 只运行覆盖 `affectedScopes` 的测试/构建/契约检查；Controller 把 `verificationEvidence` 与终态 workspace 及声明的相关路径绑定。后续无关文件变化不会使该证据失效，相关路径变化才标为 `CHANGED`。TASK Review 只验本 TASK，已配置的 GROUP Review 只验直接子项 seam，Delivery Acceptance/Readiness 只验顶层覆盖、整体证据、运行准备度和全局风险；三层都只复用 `PASSED + EXACT_MATCH` 的上游证据，再定向补齐本层缺口。成功 Review outcome 不复制 `upstreamLoopResults` 或下层 result body；未配置的 GROUP Review 不生成 Graph 节点、SQLite run/event/outcome 或空投影段落。只有影响范围无法界定、关键跨边界风险缺少隔离检查或冻结要求明确指定时才升级全量复跑。

职责严格分离：Controller 只根据 Graph 前驱终态做解锁/阻断，机械校验 receiver 提交的结果结构与声明终态一致性，并保存事件、SQLite outcome 和投影；它不判断需求是否覆盖、证据是否充分或是否具备运行准备度。独立 Review receiver 才负责当前层技术验收；Delivery receiver 每个 `STANDARD` Delivery 只执行一次顶层 Acceptance/Readiness，不逐个重验所有 Loop。真实用户只负责最终业务确认。`LIGHT` 没有独立 Review receiver，由唯一 TASK 的定向验证直接进入用户确认。

Plugin 不注册生命周期 Hook，也没有 Hook trust 步骤。代价是 Controller 不再密码学证明真实宿主 session、parent-child 或 Review receiver 独立性；独立 child 由宿主编排协议保证，控制面只验证 Adapter/workspace、reservation/fingerprint 和 operation capability。工具未进入 Agent schema 时按[宿主生命周期、健康与注册矩阵契约](docs/mcp-host-lifecycle-contract.md)诊断，不能用 Graph 自身模拟健康状态。

## 安装

Plugin 同时面向 Codex 和 Claude Code。安装前准备 Python 3.10+，并确保 `python` 在宿主终端可用。

Codex：

```text
codex plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git --ref master
codex plugin add delivery-graph@majorbio-skills
```

Codex 安装后无需进入 `/hooks`。重建、取消、归档、需求解冻/再冻结和自动 TASK 人工接管继续由 manifest 的逐工具 `prompt` 审批控制。

Claude Code：

```text
claude plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git
claude plugin install delivery-graph@majorbio-skills --scope user
```

安装或升级后新建会话，让 Skill 与 MCP Server 从同一版本加载。Claude Skill 只预批准非敏感 MCP 工具；敏感操作仍交给宿主逐次审批。团队升级、卸载和回滚步骤见[团队运维](docs/team-operations.md)。

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

执行期间可直接说“打开当前 Delivery 的进度面板”。宿主会调用 `open_delivery_dashboard`；支持 MCP Apps UI 时显示内嵌看板，不支持时继续返回相同的文字与结构化进度。看板在可见时每 15 秒自动重读权威状态，隐藏时暂停，也可手动立即刷新；这些路径都只调用 `open_delivery_dashboard`，不调用会推进调度状态的 `graph_frontier`。Graph 在宽面板中按依赖层横向显示连线，空间不足时改为纵向换行并在节点内显示前置项，不需要横向滚动。

新业务目标默认创建新 Delivery。只有明确继续同一需求，或运行结果要求 replan，才沿用原 `delivery.id` 创建下一 Revision。

## 支持的宿主

- **Codex**：Plugin Skill、MCP Server、manifest 工具审批和宿主原生 receiver。
- **Claude Code**：Plugin Skill、MCP Server、宿主工具审批和宿主原生 receiver。

外部 CLI 可以承载手动 handoff 的协调入口，但实际 TASK claim 仍必须进入受支持宿主的独立原生 child。Plugin 验证 Adapter、workspace、Graph、项目 scope 与 operation capability；没有生命周期 Hook 时不宣称能证明真实 parent-child 身份。

## 项目结构

| 路径 | 用途 |
|---|---|
| `src/hdg/` | Controller、Graph Runtime、Repository 与 MCP Adapter 源码 |
| `skills/delivery-graph/` | 规范 Skill、references 与生成的运行包 |
| `plugins/delivery-graph/` | Codex / Claude Code Plugin 产物 |
| `.agents/plugins/marketplace.json` | 本仓库的 Agent Plugin 开发 Marketplace |
| `tests/` | Graph、调度、Git、协议与投影测试 |
| `examples/team-loops/` | 可校验的 LIGHT / STANDARD hierarchy 模板 |
| `scripts/build_skill.py` | 从源码同步 Skill 与 Plugin 运行包 |
| `scripts/validate_release.py` | 离线发布候选一致性校验 |
| `scripts/mcp_registration_probe.py` | 从宿主日志生成跨 workspace/Agent 注册矩阵 |
| `scripts/mcp_dynamic_catalog_demo.py` | 会话外 supervisor 与每-turn 动态工具目录参考 Demo |

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
- [MCP 生命周期、健康与动态注册](docs/mcp-host-lifecycle-contract.md)
- [5 分钟 LIGHT Quickstart](docs/five-minute-quickstart.md)
- [版本记录](CHANGELOG.md)
