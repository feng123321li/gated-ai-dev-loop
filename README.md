# Layered Delivery

`layered-delivery` 把已经确认的需求冻结为递归 Delivery Graph，再协调多个独立 Agent/WorkLoop 完成实现、逐层审查和最终验收。

当前版本：**0.28.9**

## 核心流程

```text
沟通并确认需求
  → 制定 Delivery Graph 计划
  → 用户选择“自动执行”或“手动交接”并冻结
  → 并发调度 TASK WorkLoop
  → TASK Review
  → 逐层 GROUP Review
  → Delivery Review
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

- `TASK` 是唯一执行叶子，每个 TASK 都必须有独立 Review。
- `GROUP` 只用于真实的依赖、并行汇合或分层审查，不强制存在。
- 兄弟节点之间可以声明 DAG 依赖；无依赖且资源不冲突的节点可以并发。
- `resourceClaims` 是跨 Delivery 生效的精确排他资源键，不是文件路径授权。
- Frozen Graph 保存目标、依赖、资源、项目范围和 Loop 边界，不冻结 Loop 内部实现计划。

## 解决的问题

- 把跨项目、跨模块需求拆成可恢复的递归 `GROUP` / `TASK` Graph。
- 为 TASK、GROUP 和 Delivery 提供强制分层 Review 与最终人工验收。
- 在同一批 frontier 中并发派遣互不冲突的宿主原生 Agent。
- 根据 Agent 对任务风险的分析动态选择高效、平衡或前沿模型。
- 用 claim、heartbeat、lease、重试和容量断路器处理长时间运行与失联。
- 支持一个 Delivery 覆盖多个本地 Git 项目，并冻结各项目的基线与权限上限。
- 用不可变 Delivery Revision 管理验收前的需求调整和安全结果携带。
- 以 SQLite 和哈希事件链保存机器状态，同时生成可读的中文进度与验收投影。
- 后台 Loop 在代码检查、测试、问题修复和复审等阶段上报结构化中文进度；主 Agent 以表格持续展示 Agent/模型、测试、心跳、剩余租约及失联预警，原始事件仅用于诊断。

## 能力边界

| Layered Delivery 负责 | WorkLoop 或宿主负责 |
|---|---|
| 何时运行哪个 TASK/Review | 如何分析、编码、设计、测试或讨论 |
| Graph 依赖、资源锁和并发批次 | Loop 内部计划、Gate 与修正循环 |
| Agent/模型建议与宿主原生派遣计划 | 真正创建 Agent、执行模型与沙箱权限 |
| 租约、暂停、恢复和基础设施重试 | 外部系统凭据与不可逆操作授权 |
| 分层 Review 和最终确认顺序 | Git commit、merge、push、发布与迁移 |

调度器不解析 `loop.payload` 或 `loop.result` 的业务语义，也不会把 PATH 中发现的 CLI 当成可自动派遣的 Agent。

## 使用方式

Plugin 激活后，在 Codex 或 Claude Code 的新会话中提出需求，并要求使用 `layered-delivery`。Agent 会按当前工作区状态选择创建、继续或恢复：

1. 读取工作区状态和当前 schema v3 契约。
2. 与用户沟通需求并准备 Delivery Graph。
3. 展示完整计划、Agent/模型建议，以及“自动执行 / 手动交接”两个选项。
4. 用户选择后冻结 Graph；未选择时不开始开发。
5. 自动模式持续消费 frontier，并发调度当前可运行的独立 WorkLoop。
6. 所有 Review 完成后展示验收报告，等待用户最终确认。

执行方式的区别：

| 模式 | 冻结后行为 |
|---|---|
| 自动执行 | 当前宿主继续规划并派遣可证明为 `HOST_NATIVE` 的 Agent |
| 手动交接 | 冻结后输出稳定 `rootId`，由其他会话或宿主继续消费 frontier |

新业务目标默认创建新 Delivery。一个工作区最多绑定一个未结束 Delivery；并行需求使用独立对话工作区，Git 项目优先使用 linked worktree。只有用户明确要求继续同一需求，或当前 Loop 返回 `REPLAN_REQUIRED`，才在原 `delivery.id` 上创建下一 Revision。

## Agent、模型与并发

Agent 发现、普通建议和自动派遣是三件不同的事：

- `available_agents` 只发现本机终端和非敏感模型信息。
- `recommend_executors` 返回非绑定建议，不启动 Agent。
- `plan_dispatch_batch` 只接受宿主正式 Agent API 证明为 `HOST_NATIVE` 的容量，并返回可并发 assignment。

自动选模由调度 Agent 分析当前 Loop：

| 推理分类 | 模型层级 | 典型任务 |
|---|---|---|
| `ROUTINE` | `EFFICIENT` | 明确、低歧义、可重复且验证路径确定 |
| `STANDARD` | `BALANCED` | 常规实现、设计或分析 |
| `HIGH` | `FRONTIER` | 高风险、跨边界、复杂审查或不确定任务 |

Controller 不用 Python 做本地语义判断。某个节点缺少 Agent 分析时，只能回退到宿主明确报告的当前 Agent/模型；两者都缺失时，该节点暂不自动派遣。

## 中央编排器设置

向 Agent 说“打开中央编排器设置”，或直接调用 `open_orchestrator_settings`，可以查看当前用户级策略、配置来源和 Adapter 状态。支持 MCP Apps 的宿主显示内嵌面板；其他宿主返回同一份结构化摘要。面板保存或 Agent 直接保存都会调用需审批的 `update_orchestrator_settings`。

设置工具不依赖 Delivery 工作区，不创建 `.layered-delivery` 运行状态，也不要求 Codex MCP Apps 的保存请求携带项目 sandbox metadata。其他 Graph 工具仍严格要求当前工作区身份，不能借设置入口绕过项目隔离。

默认配置开启自动编排和自动选模、关闭跨 Adapter、允许 `codex` 与 `claude-code`、最大并发为 4，并在额度耗尽时暂停等待恢复。配置文件位于 Plugin 安装目录之外，Marketplace 升级不会覆盖：

| 平台 | 默认路径 |
|---|---|
| Windows | `%APPDATA%\layered-delivery\orchestrator.json` |
| macOS | `~/Library/Application Support/layered-delivery/orchestrator.json` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/layered-delivery/orchestrator.json` |

通过面板保存后当前 MCP 连接立即刷新；手动编辑文件后需要新建 Codex 或 Claude Code 会话。已经签发的 reservation 和已经认领的 Loop 不会被追溯改写。

### 当前跨 Adapter 限制

当前 Plugin 只接受本次会话宿主能够原生创建和认证的 Adapter。面板会把跨 Adapter 开关和 `SWITCH_ADAPTER` 额度策略锁定，并返回 `ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE`；保存工具同样拒绝启用它们。PATH 中检测到 Claude Code 或 Codex CLI 只会显示“仅检测到本机终端”，不会升级成可信执行器。

未来必须由中央宿主提供原生多 Adapter API、容量事实和同一编排根下的 receiver attestation 后，这些选项才会开放。完整字段、默认 JSON、环境变量覆盖和故障处理见[中央编排器配置](skills/layered-delivery/references/orchestrator-configuration.md)。

## 状态、隔离与恢复

- `.layered-delivery/scheduler.db` 和事件链是唯一机器权威；Markdown 仅供人类查看。
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

宿主原生能力决定当前会话实际能派遣哪些 Agent。当前版本尚未开放跨 Adapter 保存和自动派遣；外部 CLI 不会被升级为可信执行器。安装或升级 Plugin 后应新建会话，使 Skill、MCP 和 Hook 重新加载。

## 项目结构

| 路径 | 用途 |
|---|---|
| `src/hdg/` | Python Controller、Graph Runtime、Repository 与 MCP Adapter 源码 |
| `skills/layered-delivery/` | 规范 Skill、按需 references 和生成的运行包 |
| `plugins/layered-delivery/` | Codex / Claude Code 双宿主 Plugin 产物 |
| `tests/` | schema、调度、并发、Hook、配置与投影测试 |
| `scripts/build_skill.py` | 从源码重建 Skill 和 Plugin 运行包 |

项目使用 Python 3.10+ 和标准库，只维护完整 schema v3，不提供 CLI 入口或旧业务 schema 迁移。

## 开发验证

```text
python -m unittest
python -m compileall -q src tests skills/layered-delivery/scripts plugins/layered-delivery
python scripts/build_skill.py
python -X utf8 <skill-creator>/scripts/quick_validate.py skills/layered-delivery
python -X utf8 <plugin-creator>/scripts/validate_plugin.py plugins/layered-delivery
git diff --check
```

## 文档导航

- [规划、Schema v3 与冻结](skills/layered-delivery/references/planning-quickstart.md)
- [Agent 发现、模型建议与自动路由](skills/layered-delivery/references/agent-recommendations.md)
- [Frontier、并发、租约与恢复](skills/layered-delivery/references/execution-quickstart.md)
- [分层 Review 与最终验收](skills/layered-delivery/references/acceptance.md)
- [MCP、状态权威与人类投影](skills/layered-delivery/references/mcp-transport.md)
- [中央编排器配置](skills/layered-delivery/references/orchestrator-configuration.md)
- [Graph Engineering 架构](docs/graph-engineering-upgrade.md)
- [项目实现结构](docs/project-engineering.md)
- [版本记录](CHANGELOG.md)
