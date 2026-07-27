# Layered Delivery

面向 AI Agent 的分层交付治理插件。它把人工评审的开发方案编译成可恢复、可调度、可机械门禁的执行图，并使用 SQLite 保存唯一机器状态。

## 安装

GitHub 直接安装 Skill：

```text
npx skills add feng123321li/layered-delivery --skill layered-delivery --global --agent codex --agent claude-code --yes
```

更新时重新执行上述命令。

## 核心契约

- 合法层级只有 `Task`、`Capability → Task`、`Delivery → Capability → Task`。
- 使用满足聚合责任的最浅结构，Task 是唯一执行叶子。
- 人只评审一份根级开发方案，并一次冻结整棵需求树。
- 每个 requirement 都有独立 acceptance；跨需求 acceptance 只能追加集成验收。
- 冻结后由 Graph 自动选择 Task、计算 Agent 数、执行门禁并处理重试与恢复。
- `.layered-delivery/governance.sqlite3` 是唯一机器权威，Markdown 只是可重建投影。
- Agent 不自动提交、推送、合并、迁移或发布；外部动作需要单独授权。

## 使用流程

向 Codex 或 Claude 提出需求，并要求使用 `layered-delivery`：

```text
使用 layered-delivery 规划并治理当前开发需求。
```

工作流：

1. Agent 选择最浅合法层级并生成根级开发方案。
2. 用户评审方案，选择 `active` 或 `manual`。
3. Agent 一次冻结整树。
4. `active` 由当前会话自动调度；`manual` 生成可复制的完整交接。
5. Graph 驱动实现、回归、修正、分级门禁和恢复。
6. 根门禁通过后，由用户最终验收确认。

## 关键产物

| 文件 | 用途 |
|---|---|
| `development-plan.md` | 冻结前评审完整开发方案 |
| `execution-graph.md` | 查看执行图和治理图 |
| `frontier.md` | 查看下一步、关键路径和阻断原因 |
| `development-review.md` | 对照计划检查实际开发结果 |
| `acceptance-report.md` | 查看门禁证据和验收结论 |
| `run-timeline.md` | 查看 attempt、失败与恢复记录 |

## 性能诊断

控制器默认保持纯净的 stdout JSON 契约。需要定位本地 `hdg.py` 耗时时，在任一命令上添加全局 `--timing`：

```text
python -X utf8 <skill-root>/scripts/hdg.py --timing graph-frontier --project-root <project-root> --json
```

stderr 会额外输出一行 `HDG_TIMING` JSON，按阶段列出 SQLite 锁等待、提交、投影与文件写入耗时，并报告实际更新或跳过的 registry 行和文件。0.14.0 起，Markdown 投影在 SQLite 提交后执行；高频心跳只刷新 graph/timeline/frontier，相同内容的投影不再重复替换或 `fsync`。

## 仓库维护

修改控制器后重新构建插件载荷：

```text
python scripts/build_skill.py
```

完整验证：

```text
python scripts/build_skill.py
python <plugin-creator>/scripts/validate_plugin.py plugins/layered-delivery
claude plugin validate plugins/layered-delivery
python -m unittest discover -s tests -t . -v
python -m compileall -q src scripts tests
python -X utf8 <skill-validator>/quick_validate.py skills/layered-delivery
git diff --check
```

本仓库是 `layered-delivery` Plugin 与 Skill 的唯一源码。公司内部 Marketplace
只维护指向 `plugins/layered-delivery` 的 Git 版本映射，不复制本仓库的插件载荷。

源码维护不创建 `.layered-delivery/**` 运行包。只有明确要求 dogfood 时，控制面写命令才可执行并携带 `--dogfood`。

## 详细文档

- [版本更新记录](CHANGELOG.md)
- [Skill 入口](skills/layered-delivery/SKILL.md)
- [完整工作流](skills/layered-delivery/references/workflow.md)
- [Graph Engineering](skills/layered-delivery/references/graph-engineering.md)
- [开发方案字段](skills/layered-delivery/references/development-plan.md)
- [验收与最终确认](skills/layered-delivery/references/acceptance.md)
- [SQLite 状态与恢复](skills/layered-delivery/references/task-registry.md)
