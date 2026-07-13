# 基线与路由

## Full 基线

严格使用以下章节顺序：

```markdown
# Development Baseline

## Goal
描述可观察的最终结果。

## Background
只记录实现所需背景。

## Scope
描述允许的行为与仓库范围。

## Non-Goals
列出明确排除项。

## Requirements
### R-001: 简短标题
写出完整、规范的需求。

## Acceptance
### A-001 [R-001]
描述可观察的验收结果。

## Tasks
- [ ] T-001 [R-001] [A-001] 实现限定的改动。

## Risks
列出风险及缓解方式。

## Test Commands
- ["node","--test"]

## Decisions
记录已解决的选择及理由。
```

要求 `R-NNN`、`A-NNN`、`T-NNN` 唯一。每个验收项至少关联一个需求，每个任务同时关联需求与验收项。测试命令只能保存为 JSON argv 数组，不得保存 shell 拼接字符串。

## Light 简报

严格使用四个非空章节：

```markdown
## Goal
描述一个边界明确的结果。

## Scope
- src/example.ext
- tests/example.test.ext

## Acceptance
- 描述一个可观察结果。
- Test command: ["test-runner","arg"]

## Risks
- 明确确认所有 Full 硬条件均为假。
```

至少包含一个可观察结果、一条安全的 argv 测试命令、最多三个普通文件组成的精确路径清单，并明确确认影响已知。

## 使用 CLI 冻结

只在 CLI 已安装时使用。传入结构化信号，不要期待 CLI 从自然语言推断安全事实。
无论是否安装 CLI，运行态材料都只写入 `<project>/.ai-dev-loop/<task-id>/`，不得另建兼容目录或临时项目目录。

```text
gated-loop route "<任务>" --signals signals.json --json
gated-loop start "<任务>" --signals signals.json --task <id> --host-runtime codex|claude --json
```

Full：

```text
gated-loop prepare --task <id> --baseline requirements/baseline.md --source <明确来源>
gated-loop freeze --task <id> --confirmed
```

Light：把结构化简报传给 `start`，只有用户确认后才加入 `--confirmed`。

CLI 只自动完成路由、校验、指纹和冻结。实现编排、机械门禁、独立验收和最终确认以本 Skill 为准。

## 冻结检查表

- 删除占位符和未解决选项。
- 确认 Scope 与 Non-Goals 不冲突。
- 确认验收结果可观察。
- 确认每条测试命令是 argv 数组，并指向项目真实测试。
- 如实记录宿主是 `codex` 或 `claude`。
- 按 `tracking.md` 创建 `development-overview.md` 和 `progress.md`，把进度置为等待需求确认。
- 展示授权并取得用户明确确认。
- 在任何实现写入前冻结。
- 冻结完成后进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`，单独等待用户选择 `active` 或 `manual`；需求确认不能代替开发方式确认。
