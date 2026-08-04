# 团队安装与运维

本文面向团队管理员和普通使用者，覆盖 `layered-delivery` 0.34.0 的安装、升级、恢复、卸载与回滚。Plugin 同时支持 Codex 和 Claude Code，项目运行时仅依赖 Python 3.10+ 标准库。

## 安装前检查

- 安装 Python 3.10 或更高版本，并确保 `python --version` 可在宿主终端运行。
- 安装至少一个受支持宿主：Codex 或 Claude Code。
- 确认能够访问公司内部 Marketplace 仓库。
- 在实际项目的新会话中使用 Plugin；不要在维护 `layered-delivery` 源码仓库时创建业务运行包。

当前发布验证基线是 Codex CLI 0.146.0 和 Claude Code 2.1.220。这是已验证版本，不代表 Codex 的硬编码最低版本；Claude Code 的敏感工具交互至少需要 2.1.199。

## 安装

### Codex

```text
codex plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git --ref master
codex plugin add layered-delivery@majorbio-skills
codex plugin list --json
```

安装后新建 Codex 任务，使 Skill、MCP Server 和 Hook 从同一版本重新加载。首次使用 Hook 时按宿主界面检查来源并建立信任；不要为普通人工会话绕过 Hook 信任。

### Claude Code

```text
claude plugin marketplace add git@git.i-sanger.com:ai/skill/marketplace.git
claude plugin install layered-delivery@majorbio-skills --scope user
claude plugin list --json
```

安装后退出旧会话并新建 Claude Code 会话。项目内不需要额外复制 MCP 配置或维护 `local.settings`；Plugin 自带 `.mcp.json` 和 Hook 配置。

### 安装验证

在源码发布包中执行不调用模型的本地探测：

```text
python scripts/host_smoke.py probe --json
```

结果必须报告 Plugin 版本 0.34.0 和 29 个 MCP 工具，并如实标记本机已安装的宿主。发布管理员还必须按[宿主兼容矩阵](host-compatibility.md)分别在 Codex、Claude Code 环境执行真实宿主冒烟任务；两个宿主不要求安装在同一台机器，普通成员也不需要重复付费冒烟。

真实冒烟默认先只展示计划，必须显式增加 `--execute` 才调用模型。两个宿主分别运行，绝不从一个终端跨调另一个 Agent：

```text
python scripts/host_smoke.py run --host claude-code --scenario light
python scripts/host_smoke.py run --host claude-code --scenario light --execute

python scripts/host_smoke.py run --host codex --scenario light
python scripts/host_smoke.py run --host codex --scenario light --execute
```

Claude 命令从当前源码候选的 `--plugin-dir` 加载包；Codex 命令要求 0.34.0 候选已经从 Marketplace 安装。发布前的最小门禁可用 LIGHT 验证同宿主 claim/heartbeat/progress/result；发布管理员需要覆盖 Review 时再把 `--scenario light` 改成 `--scenario standard`。任何输出中的 `claimedAgents` 都必须只含命令指定的当前宿主。

Codex 冒烟不能使用 `--ephemeral`：`SubagentStart` attestation 必须从宿主持久化 transcript 校验 parent/child/task。冒烟会留下一个可审计的 Codex 会话记录，但业务工作区仍位于自动清理的临时目录；若宿主无法提供该 transcript，claim 必须 fail closed。

## 升级

### 升级前

1. 记录当前 `codex plugin list --json` 或 `claude plugin list --json` 输出中的版本。
2. 若用户级 `orchestrator.json` 仍为 schema v1，升级前将其明确改为 `{"schemaVersion":2,"maxConcurrentExecutors":4,"quotaExhaustionPolicy":"PAUSE_AND_RESUME"}`，其中并发值可保留原配置。v2 不再接受模型选择、Adapter allowlist、跨 Adapter 或 Review 多样性字段；旧文件会 fail closed，不会静默迁移。
3. 对 0.31 及更早版本的旧式 manual Graph run，先在旧版本完成或取消。当前版本的 manual Graph 只能由 `start_manual_handoff` 从精确 `HANDOFF_READY` 快照创建，且 MANUAL 只允许 TASK；不要把旧 run 当作新协议恢复。
4. 自动 schema v3 Graph 可在新会话通过 `workspace_status → graph_frontier` 恢复。升级前仍建议让正在写结果的 Loop 完成，避免恰好跨越 Hook 重载窗口。
5. 已由早期 0.33.0 生成、但缺少根 `scheduler.db`/`overview.md` 的手动内容包不会从 Markdown 反向迁移。升级后应在仍持有原 hierarchy 与双 fingerprint 的需求会话中重新调用 `create_manual_handoff` 完成 SQLite 登记；不要手工创建数据库或拼接总览。
6. 不删除项目中的 `.layered-delivery`，也不直接修改 `scheduler.db`。

### Codex 升级

```text
codex plugin marketplace upgrade majorbio-skills
codex plugin remove layered-delivery@majorbio-skills
codex plugin add layered-delivery@majorbio-skills
codex plugin list --json
```

### Claude Code 升级

```text
claude plugin marketplace update majorbio-skills
claude plugin update layered-delivery@majorbio-skills
claude plugin list --json
```

Claude Code 更新后必须重启会话。若宿主提示命令名称不同，以当前 `claude plugin marketplace --help` 输出为准；不要通过手工覆盖 Plugin cache 模拟升级。

## 恢复

| 现象 | 安全恢复方式 |
|---|---|
| 新会话不知道旧任务状态 | 在同一工作区调用 `workspace_status`，对返回的 `rootId` 调用 `graph_frontier` |
| MCP 连接中断，写操作结果未知 | 先重连并读取 `workspace_status`、`graph_status` 或 `graph_frontier`，不要重放未知写操作 |
| Loop 心跳和进度停止 | 等待租约回收；下一次 `graph_frontier` 触发 `WORKER_LOST`，随后使用新 attempt 和新接收上下文 |
| Projection 缺失或损坏 | 对已校验事件链调用 `rebuild_graph_run`；不要直接写 Markdown 或 SQLite |
| Plugin 更新后 `not host-attested` | 确认使用新会话和真实原生 child；重新审阅 Codex Hook 信任。有效 claim 无法提前释放时，等待租约恢复 |
| Claude 结构化 429 | 由 StopFailure Hook 记录真实 reset 时间并暂停；到期由一次性宿主唤醒重新读取 frontier |
| LIGHT 执行中发现影响扩大 | 提交 `REPLAN_REQUIRED`，保持同一 `delivery.id` 准备 `STANDARD` Revision |

恢复过程中不得手工伪造 heartbeat、progress、operation、attestation 或终态。工作区代码可以保留并由新 attempt 重新检查，但调度状态只通过 Plugin MCP 改变。

## 卸载

### Codex

```text
codex plugin remove layered-delivery@majorbio-skills --json
```

### Claude Code

```text
claude plugin uninstall layered-delivery@majorbio-skills --scope user
```

卸载后新建会话，并用宿主的 Plugin 列表确认已移除。卸载 Plugin 不等于删除项目交付记录：项目中的 `.layered-delivery` 和用户级中央编排器配置默认保留，便于审计或重新安装后恢复。需要清理这些数据时应单独评审精确路径、确认没有活动 Delivery，并使用可恢复方式处理。

## 回滚

团队回滚由 Marketplace 管理员执行：把 Codex 与 Claude 两份 Marketplace manifest 同时重新固定到最后已验证的 tag 和 40 位提交 SHA，然后让用户刷新 Marketplace、重新安装/更新 Plugin 并新建会话。

回滚到 0.32.0 前应先保存 0.33.x 手动开发包中的 progress/acceptance；0.32.0 不会生成完整冻结内容包，也不识别 `requirementSnapshotStatus`。自动 schema v3 Graph 仍应优先在当前版本完成，避免在活动 claim 期间跨版本切换 Hook。回滚不修改项目数据库，不通过删除 cache 伪造版本切换。
