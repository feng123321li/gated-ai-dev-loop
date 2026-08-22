# Delivery Graph

把已确认的软件需求拆成可执行的 Delivery Graph，协调 Agent 完成实现、Review 和最终验收。

当前版本：**0.43.5** · Schema：**v3** · Python：**3.10+，仅标准库**

## 核心能力

- 用 `Delivery → GROUP → TASK` 管理任务、依赖和整体进度。
- 规划、调度、实现、Review 职责分离，避免单个 Agent 承担全部上下文。
- 保存 Git 基线和运行状态，任务中断或换会话后可以继续。
- 用版本化 Agent Profile Catalog 为不同 Loop 配置专用 owner/helper Team，同时保持单一控制面 owner。
- 用完整结果账本和确定性 Result Assembler 防止漏项，并输出关键路径与慢 Loop 指标。
- 对项目范围、技术 Review、Revision 完成和上线交付关闭设置明确门禁。

Delivery Graph 负责组织、状态和调度，不替代 Agent 分析代码，也不会擅自提交、合并、推送或发布。

## 4 个 Skill，3 个 MCP Profile

| Skill | 职责 | MCP Profile |
|---|---|---|
| `$delivery-graph` | 入口路由、需求确认、Graph 规划、Git 基线、冻结、确定性结果和交付关闭 | `planning`：18 个工具 |
| `$delivery-graph-dispatch` | 入口复核、派遣、等待和恢复 | `dispatch`：13 个工具 |
| `$delivery-graph-task` | TASK 实现与验证 | `receiver`：7 个工具 |
| `$delivery-graph-review` | 独立分层 Review | `receiver`：7 个工具 |

三个 MCP server 共用同一 Controller，但只向当前角色提供所需工具。完整能力为 35 个工具，跨 Profile 调用会被拒绝。

```text
需求确认 → Graph 规划与冻结 → TASK 实现与分层 Review → Revision 完成（未上线，可继续优化）→ 上线交付关闭 → 可选归档
```

- `LIGHT`：单一、局部、低风险 TASK，定向验证后由用户确认。
- `STANDARD`：默认选择，适用于跨模块、有依赖或影响不明确的需求，包含独立 Review。

## 使用

在业务项目的新会话中输入：

```text
使用 delivery-graph 处理这项需求：<目标、约束和验收标准>
```

Agent 会依次确认需求、Git 开发基线、保障档和执行方式，然后推进 TASK 与 Review。执行期间可以说“打开当前 Delivery 的进度面板”。

当前 Revision 完成后 Delivery 保持 `OPEN/未上线`，测试或业务验收反馈可继续追加 Revision；只有测试、业务验收和生产上线都完成后，才显式关闭为 `CLOSED/已上线交付`。关闭后不能追加 Revision，归档仍是独立的可选动作。

中断后，在新会话中提供原 Delivery 的 `rootId` 即可继续；存在多个未完成 Delivery 时需要明确选择一个。不要手动修改 `.layered-delivery/`。

首次体验见[5 分钟 LIGHT Quickstart](docs/five-minute-quickstart.md)。

## 安全边界

- Controller 只读检查 Git，不执行 `fetch`、`switch`、`commit`、`merge`、`push` 或发布。
- 同一物理 checkout 同一时间只执行一个 Delivery，其他 Delivery 排队等待。
- TASK 只能访问已确认的项目范围，缺少 Git 基线或授权时停止。
- Review Agent 负责技术验收，真实用户负责最终业务确认。

## 开发验证

```text
python scripts/build_skill.py
python -X utf8 -m unittest discover -s tests -t .
python -m compileall -q src tests scripts skills plugins/delivery-graph
python scripts/validate_release.py
git diff --check
```

维护仓库时不创建业务 `.layered-delivery/**` 运行包。项目只维护完整 schema v3。

## 详细文档

- [规划、Git 基线与冻结](skills/delivery-graph/references/planning-quickstart.md)
- [派遣、等待与恢复](skills/delivery-graph-dispatch/references/dispatch-and-recovery.md)
- [Agent Profile Catalog 与专用 Team](docs/agent-profile-catalog.md)
- [可选多 Supervisor 入口路由](docs/supervisor-routing.md)
- [TASK 执行](skills/delivery-graph-task/references/task-execution.md)
- [分层 Review 与验收](skills/delivery-graph-review/references/acceptance.md)
- [按变更范围验证与发布](docs/release-strategy.md)
- [Marketplace 安装、升级与回滚](docs/team-operations.md) · [宿主兼容](docs/host-compatibility.md) · [项目结构](docs/project-engineering.md)
- [版本记录](CHANGELOG.md)
