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

路由后把工作规模、固定代表说明、当前任务说明和主要变更类型写入 `development-overview.md` 与 `progress.md`；`mode.json` 仍只保存 CLI 支持的 None、Light 或 Full：

- `Micro`：只授权一个局部点；低风险时使用 Light 简报，命中硬条件时仍使用 Full baseline；
- `Task`：Full baseline 的每个 T 都必须是可独立验证的执行任务；
- `Capability`：除 R/A/T 外，在总览中列出工作流、任务依赖波次、组件或服务集成点和能力级聚合门禁；
- `Project`：按 [project-planning.md](project-planning.md) 创建项目开发总纲和 `rounds/planning/project-plan.md`，使用 M/W/T 分层拆解。

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
无论是否安装 CLI，运行态材料都只写入 `<project>/.ai-dev-loop/<task-id>/`，不得另建兼容目录或临时项目目录。

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

CLI 只自动完成路由、校验、指纹和冻结。实现编排、机械门禁、独立验收和最终确认以本 Skill 为准。

## 冻结检查表

- 删除占位符和未解决选项。
- 确认 Scope 与 Non-Goals 不冲突。
- 确认每个写入任务所属的逻辑工作区或服务；跨工作区时明确提供方、消费方和契约依赖。
- 确认验收结果可观察。
- 确认每条测试命令是 argv 数组，并指向项目真实测试。
- 使用安全的小写 Agent 标识如实记录宿主，例如 `codex`、`claude`、`opencode`。
- Full 可能并行时，为每个任务记录精确允许路径和依赖；路径或依赖不明确时不得提供 parallel。
- 记录工作规模、固定代表说明、当前任务说明和主要变更类型；Capability 检查工作流、依赖和集成门禁，Project 额外通过 project-planning.md 的全部拆解质量门禁。
- 按 `tracking.md` 创建 `development-overview.md` 和 `progress.md`，把进度置为等待需求确认。
- 初始化 `progress.md` 的全部业务任务和 SOP 步骤；大型项目同时初始化全部 M/W/T，不能只写一个总任务。
- 展示授权并取得用户明确确认。
- 在任何实现写入前冻结。
- 冻结完成后，单工作区可进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`；跨工作区先按 `multi-workspace.md` 完成授权和覆盖门禁，未通过时进入 `WAITING_FOR_WORKSPACE_AUTHORIZATION`。需求确认不能代替工作区授权或开发方式确认。
