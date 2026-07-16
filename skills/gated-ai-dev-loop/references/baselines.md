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

要求 `R-NNN`、`A-NNN`、`T-NNN` 唯一。每个验收项至少关联一个需求，每个任务同时关联需求与验收项。AI 分析发现任务跨目录、跨仓库或跨微服务时，Scope 和 Tasks 必须说明每个任务所属的逻辑工作区或服务、提供方/消费方关系及依赖顺序；不要把尚未提供的物理绝对路径写成需求事实。测试命令只能保存为 JSON argv 数组，不得保存 shell 拼接字符串；具体工作区和 `cwd` 在当前轮次工作区授权中绑定。

## 工作规模与拆解深度

路由后先把工作规模、固定代表说明、当前任务说明和主要变更类型写入根级 registry 的 provisional 任务记录，再投影到 `development-overview.md` 与 `progress.md`；`mode.json` 仍只保存 CLI 支持的 None、Light 或 Full：

| 工作规模 | 授权与拆解 | 必须初始化并跟踪 |
| --- | --- | --- |
| `Micro` | 只授权一个局部点；低风险时使用 Light 简报，命中硬条件时仍使用 Full baseline | T/S；M/W 不适用 |
| `Task` | 一个独立验收边界；Full baseline 的每个 T 都必须是可独立验证的执行任务 | T/S；M/W 不适用 |
| `Capability` | 除 R/A/T 外，在总览中列出工作流、任务依赖波次、组件或服务集成点和能力级聚合门禁 | W/T/S；M 不适用且计数为 `0 / 0` |
| `Project` | 按 [project-planning.md](project-planning.md) 创建项目开发总纲和 `rounds/planning/project-plan.md`，使用 M/W/T 分层拆解 | M/W/T/S |

请求冻结确认前，`development-overview.md` 与 `progress.md` 必须都按 [tracking.md](tracking.md) 写出完整的“工作规模判定记录”，逐项展示交付对象、是否完整交付、独立能力及验收边界、里程碑/发布边界、工作流/依赖波次、单轮安全性、命中规则、为什么不是更小一级和缺失事实。每项都必须有明确事实或“不适用”；`缺失事实` 必须为“无”。存在未知、占位符或无法解释规模边界时保持 `WAITING_FOR_REQUIREMENT_CONFIRMATION`，不得冻结。

禁止把“完成整个后端”“实现全部接口”“完成项目开发”作为一个 T。一个 T 必须有单一可观察结果、关联 R/A、工作区与允许范围、依赖、输入输出、测试和完成定义；否则继续拆分后再请求确认。

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
无论是否安装 CLI，冻结后的任务包与轮次材料都写入 `<project>/.ai-dev-loop/<task-id>/`；宿主按 [registry-transactions.md](registry-transactions.md) 在 `.ai-dev-loop/` 根维护 `task-registry.json`、`workspace-overview.md` 与暂存事务。冻结前的 lifecycle event、总览、进度和 Project plan 只暂存在保留的 `.ai-dev-loop/.host-staging/<task-id>/`，因为 CLI 可能整体替换任务目录；冻结成功后再物化。不得另建其他兼容目录，也不得向任务根新增当前 CLI 不认识的生命周期文件。

```text
gated-loop route "<任务>" --signals signals.json --json
gated-loop start "<任务>" --signals signals.json --task <id> --host-runtime <agent-id> --json
```

Full：

```text
gated-loop prepare --task <id> --baseline requirements/baseline.md --source <明确来源>
gated-loop freeze --task <id> --confirmed
```

Light：把结构化简报传给 `start`，只有用户确认后才加入 `--confirmed`。

CLI 负责确定性路由、任务包准备/冻结、指纹与 schema 校验、`self-check` 机械门禁、`accept` 验收结果校验和落盘；宿主 Skill 负责根级 registry、生命周期 event、实现编排、reviewer 调度和用户最终确认。不得把任一侧尚未实现的能力归给另一侧。

## 冻结检查表

- 删除占位符和未解决选项。
- 新 task ID 必须满足 CLI 的精确 ID 规则，且不得占用 `task-registry.json`、`workspace-overview.md`、`.host-staging`、`.task-registry.lock` 或任何 `.task-registry.lock.recovery-*`；初始化控制面前确认根级控制路径、staging、锁、锁恢复隔离文件和目标 task 路径都未被 Git 跟踪且已被忽略，否则进入 `WAITING_FOR_REGISTRY_IGNORE_CONFIGURATION`。
- 确认 Scope 与 Non-Goals 不冲突。
- 确认每个写入任务所属的逻辑工作区或服务；跨工作区时明确提供方、消费方和契约依赖。
- 确认验收结果可观察。
- 确认每条测试命令是 argv 数组，并指向项目真实测试。
- 使用安全的小写 Agent 标识如实记录宿主，例如 `codex`、`claude`、`opencode`。
- Full 可能并行时，为每个任务记录精确允许路径和依赖；路径或依赖不明确时不得提供 parallel。
- 记录工作规模、固定代表说明、当前任务说明和主要变更类型；确认 `development-overview.md` 与 `progress.md` 的“工作规模判定记录”九项事实齐全且 `缺失事实=无`；Capability 检查工作流、依赖和集成门禁，Project 额外通过 project-planning.md 的全部拆解质量门禁。
- 用户批准 task ID 后，先取得单写锁，在 `.host-staging/<task-id>/` 以 create-new 写 `TASK_CREATION_APPROVED` event，并登记 `PROVISIONAL / CREATING_TASK_PACKAGE`，再刷新工作区总纲；反馈派生任务只有用户确认后才执行这一步。
- 按 `tracking.md` 在 staging 创建 `development-overview.md`、`progress.md` 和适用的 Project plan，把 provisional 记录推进为 `WAITING_USER / WAITING_FOR_REQUIREMENT_CONFIRMATION`；用户确认后调用 CLI 冻结，成功物化到任务目录，再把 registry 改为 `HEALTHY` 并进入工作区授权或开发方式选择。
- 按规模初始化 `progress.md`：Micro/Task 初始化 T/S，Capability 初始化 W/T/S 且里程碑不适用，Project 初始化 M/W/T/S；不能只写一个总任务，也不能为不适用层级伪造占位行。
- 展示授权并取得用户明确确认。
- 在任何实现写入前冻结。
- 冻结完成后，单工作区可进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`；跨工作区先按 `multi-workspace.md` 完成授权和覆盖门禁，未通过时进入 `WAITING_FOR_WORKSPACE_AUTHORIZATION`。需求确认不能代替工作区授权或开发方式确认。
