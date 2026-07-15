# 门禁式 AI 开发循环

一套适用于任意 AI Agent 的通用开发 Skill。前期需求可以来自对话、Issue、PRD、截图、原型或代码分析；后续对话会先恢复已有任务，再处理人工验收时的修改、建议或新目标。大型完整项目在开发前必须提供总纲、里程碑、工作流、细粒度任务拆解和实时 SOP 进度。

这不是某个需求框架的插件，也不限制 Windows。核心 Skill 是纯 Markdown，安装器和辅助 CLI 使用 Node.js，可在 Windows、macOS 和 Linux 运行。

## 工作方式

1. 每次开发类消息先校验根级 `task-registry.json`：精确 ID / 路径优先，其次是有效当前焦点，再次是唯一 `ACTIVE/WAITING_USER` 候选；多个候选让用户选择，不按目录时间、名称或措辞相似度猜测。
2. 新任务分别记录 `None/Light/Full` 门禁等级、`Micro/Task/Capability/Project` 工作规模和主要变更类型，并显示固定中文代表说明及当前任务的具体说明。
3. `Full · Project · 主要变更类型` 在确认前生成开发总纲和 `rounds/planning/project-plan.md`，按 M/W/T 拆解并检查依赖、工作区、测试与完成定义。
4. 用户确认 Full 基线或 Light 简报后冻结，开发授权不再漂移。
5. 宿主维护根级工作区总纲以及任务内 `development-overview.md`、`progress.md`；每个业务任务和 SOP 状态变化后按“不可变事件 → 注册表 → 总纲与进度”立即回写周期、精确计数、责任方和证据。
6. 跨目录、跨仓库或跨微服务时，宿主为每个写入任务绑定绝对工作区、允许路径、测试目录与依赖顺序；覆盖不完整就停在 `WAITING_FOR_WORKSPACE_AUTHORIZATION`。
7. 工作区覆盖通过后、写代码前，由用户明确选择 active 或 manual，再按资格选择 single 或 parallel；这些选择不是任务模式。
8. active 自动启动全新隔离开发 Agent；manual 只返回可交给任意 Agent 的通用后续提示词。开发 Agent 均不继承前期对话。
9. 宿主逐任务、逐工作区检查改动归属和冻结测试，再对聚合 diff 执行跨服务机械门禁。
10. 优先使用与开发者分离的全新只读其他 Agent 验收；没有其他产品时使用全新验收子 Agent；均不可用时生成明确的人工验收包。
11. 开发完成后，任意新宿主可读取 `gate-continuation.md`、进度和开发结果接管门禁，无需返回原对话。
12. 人工验收时提出修改或建议，先分类为同任务修复、关联修订、建议处置或新任务，并在用户确认前禁止创建目标任务包。

完整的角色、门禁、升级和修复循环见：[工作流程图](skills/gated-ai-dev-loop/references/workflow.md)。

`gated-loop` 0.2.0 已实现 None/Light/Full 路由、基线准备、冻结、schema v1/v2 机械自检和能力驱动的验收落盘。根级任务注册表、工作规模、变更类型、任务恢复、项目总纲、进度回写、人工反馈分流、开发方式选择和 Agent 派遣由宿主按 Skill 维护；不要伪称 CLI 已原生实现这些宿主治理能力。

## 三维路由

| 工作规模 | 固定代表说明 | 典型组合 | 规划要求 |
| --- | --- | --- | --- |
| `Micro（微改）` | 修改一个局部点，不形成独立功能包 | `Light · Micro · Bugfix`；高风险时也可 Full | 简报或 baseline、一个 T 和适用 SOP |
| `Task（单任务）` | 交付一个可独立验收的结果 | `Full · Task · Feature` | 完整 R/A/T、总览和 SOP 进度 |
| `Capability（完整能力）` | 多个任务协同形成一项完整业务能力 | `Full · Capability · Feature` | 工作流、依赖波次、集成门禁和逐任务进度 |
| `Project（完整项目）` | 多个能力和里程碑组成一个完整项目 | `Full · Project · Feature` | 开发总纲、project-plan、M/W/T 拆解和完整 SOP 看板 |

变更类型独立记录为 `Feature/Bugfix/Refactor/Migration/Maintenance/Docs/Test`；`single/parallel` 仍是冻结后的执行拓扑。详细判定见[三维路由模型](skills/gated-ai-dev-loop/references/routing-profiles.md)。

## 开发总览与进度

协调工作区的 `.ai-dev-loop/` 根级维护：

- `task-registry.json`：全部任务的机器索引、生命周期、当前焦点、关系、周期和精确完成计数；状态迁移必须引用真实轮次或用户确认 evidence；
- `workspace-overview.md`：给人看的工作区任务总纲，显示当前焦点、全部非终态与异常任务、最近终态、关系链、周期、M/W/T/SOP 完成数、下一步和证据入口。

注册表是生命周期规范记录，但不能覆盖冻结授权；工作区总纲是可重建投影。现有 CLI 严格校验任务包和受保护路径，因此本次只增加这两个持久根级文件，并使用临时 `.host-staging/` 与 `.task-registry.lock` 完成冻结兼容和单写保护；不向已冻结任务根新增生命周期文件，也不修改 `state.json`。初始化前必须确认这些控制路径和目标 task 路径未被 Git 跟踪且已被忽略；控制名称不能用作 task ID。

每个任务都在 `.ai-dev-loop/<task-id>/` 中维护：

- `development-overview.md`：目标、范围、R/A/T 追踪、开发与验收安排、风险和产物导航；
- `progress.md`：当前阶段、门禁等级、工作规模、固定/具体代表说明、变更类型、M/W/T 或普通任务状态、SOP 看板、精确完成数、门禁结论、阻断项、下一步和追加式时间线；
- `final-acceptance-report.md`：最新验收轮次的人可读总入口；`gated-loop accept` 生成首次汇总，人工语义审查或最终确认变化后由宿主按规范状态重渲染。

Project 规模另有 `rounds/planning/project-plan.md`，保存详细里程碑、工作流、任务依赖、关键路径、阶段门禁和风险。

这些人可读文件最终由宿主按注册表 revision 维护；CLI 只生成其已实现命令对应的初始报告，开发和审查上下文保持只读。它们不替代冻结基线、真实 diff、测试、独立审查或人工语义审查 JSON。详细契约已拆分为[注册表、焦点与总纲](skills/gated-ai-dev-loop/references/task-registry.md)、[事务、暂存与并发](skills/gated-ai-dev-loop/references/registry-transactions.md)和[生命周期、终态与迁移](skills/gated-ai-dev-loop/references/registry-lifecycle.md)。

## 跨目录与多微服务交接

需求分析可以从任意一个项目开始，但开发授权不能只包含当前目录。只要冻结任务需要修改多个目录、仓库或微服务，宿主就选择一个协调工作区保存根级注册表、工作区总纲和 `.ai-dev-loop/<task-id>/`；其他工作区不复制这些控制面产物。当前轮次生成：

- `workspace-authorization.json`：用户确认的所有工作区绝对根路径、任务、允许路径、测试 `cwd` 与访问状态；
- `workspace-coverage.json`：每个写入任务是否已获得完整工作区覆盖，以及缺失项和解除条件；
- `development-snapshot.json` schema v2：逐工作区的分支、HEAD、允许路径和开发前已有改动。

只有覆盖结论为 `PASS` 才能显示 active/manual 选择、创建 `prompt.md` 或派遣开发 Agent。manual 提示词必须逐个列出所有工作区；接收端如果因机器环境不同无法访问其中之一，必须在任何写入前返回 `BLOCKED`。完整契约见[多工作区与多微服务交接](skills/gated-ai-dev-loop/references/multi-workspace.md)。

## 验收报告与严重级别

每轮机械门禁生成 `self-check-report.md` 和 `gate-evidence.json`；语义验收先生成 `review-plan.json`，再生成轮次级 `acceptance-report.md` 和 `review.json`，并刷新任务根目录的 `final-acceptance-report.md`：

- `P0`：数据、安全、权限、不可逆破坏或关键服务级严重问题，阻断验收；
- `P1`：需求、功能、关键边界、事务、兼容性或测试级阻断问题，阻断验收；
- `P2`：不阻断当前验收的改进建议，允许 PASS，但人工验收时必须展示。

证据、隔离或改动归属无法证明，或没有全新隔离 Agent/子 Agent 能力时使用 `NEED_HUMAN_REVIEW`，不得归类为普通 P1。验收报告不包含自动合并、提交或 Git commit message 建议。

## 环境要求

- Node.js `20.19` 或更高版本；
- 需要 active 开发时，宿主必须能创建全新隔离开发 Agent；
- 独立验收不要求第二种 Agent 产品；宿主可以使用全新其他 Agent 或同产品验收子 Agent，并把 JSON 结果交给 CLI 校验。Codex/Claude CLI 只是用户显式启用的可选适配器。

## 安装

克隆仓库后安装依赖：

```text
npm install
```

先查看 Skill 安装计划，不写文件：

```text
npm run skill:install -- --target both --scope user --dry-run
```

安装到当前用户的 Codex 和 Claude Code：

```text
npm run skill:install -- --target both --scope user
```

用户级目录：

- Codex：`$CODEX_HOME/skills/gated-ai-dev-loop`；未设置 `CODEX_HOME` 时使用 `~/.codex/skills/gated-ai-dev-loop`。
- Claude Code：`~/.claude/skills/gated-ai-dev-loop`。

安装到某个项目，让仓库内的 Agent 自动发现：

```text
npm run skill:install -- --target both --scope project --project-root /path/to/project
```

项目级目录：

- Codex：`<project>/.agents/skills/gated-ai-dev-loop`。
- Claude Code：`<project>/.claude/skills/gated-ai-dev-loop`。

目标已存在时安装器默认停止。确认需要更新后加入 `--force`；安装器会在同一目录暂存并替换，不使用平台专属 shell 命令。

如需全局使用辅助 CLI：

```text
npm install -g .
gated-loop --help
```

也可以不做全局安装，直接运行：

```text
node bin/gated-loop.mjs --help
```

## 在 Codex 中使用

安装后重启或新建 Codex 任务，然后显式调用：

```text
$gated-ai-dev-loop 为当前项目增加用户导出功能，先分析并选择模式，基线确认前不要写代码。
```

也可以直接描述功能；当任务符合 Skill 描述时，Codex 可以自动选择它。

## 在 Claude Code 中使用

安装后重启 Claude Code 或打开新会话：

```text
/gated-ai-dev-loop 为当前项目修复订单重复提交问题，先完成路由和基线确认。
```

Claude Code、Codex 或其他支持 Skill 的 Agent 都可以作为前期宿主完成分析、审核和冻结，不强制跨模型复审。原开发上下文不能验收自己的工作。

## 两种开发方式

- `active`（直接运行）：用户在当前对话输入“直接运行”，宿主自动启动可调度的全新隔离开发 Agent。
- `manual`（手动运行）：宿主输出项目绝对路径、`.ai-dev-loop/<task-id>/`、`development-handoff.md`、当前轮次提示词、一份通用后续提示词和返回方式，用户交给任意全新开发 Agent。

单工作区基线冻结后，或多工作区覆盖门禁通过后，必须显示以下选择，并等待用户回复：

```text
需求基线已冻结，请选择开发方式：
1. 直接运行（active）
2. 手动运行（manual）
```

manual 选中后不再选择 Codex、Claude 或其他运行时，也不返回工具专属 CLI 命令。宿主必须给出一份可复制到任意开发 Agent 的通用提示词；它引用 `development-handoff.md` 与当前轮次 `prompt.md`，禁止二次分析和越界写入。跨仓库任务在交接前必须已经通过工作区覆盖门禁；不能把宿主已知缺少的目录交给开发 Agent 再等待 `BLOCKED`。同时生成 `gate-continuation.md`；开发结束后，任意新宿主先校验根级注册表和目标 task 的 `phase/nextAction`，再结合接续文件与结构化开发结果继续机械门禁，不要求返回原对话。主动调用遇到网关容量、认证或模型不可用时，如果确认没有代码写入，重新展示选择并推荐 manual，不得自行切换；已经写入或无法确认时停止并要求人工检查。

## 单 Agent 与并行开发

开发方式确定后再选择执行拓扑：

- `single`：一个开发上下文完成冻结任务；Light 固定使用。
- `parallel`：多个隔离开发 Agent 按任务组并行；Agent 产品可以不同，只对可证明路径互斥、没有并发语义依赖且具备聚合测试的 Full 开放。

parallel 必须先展示 `parallel-plan.json` 中的任务、验收 ID、文件白名单、依赖、波次和最大并发数并取得用户选择。选择 `active + parallel` 后，宿主自动按确认计划派遣子 Agent，不再逐个询问；manual + parallel 则输出多份独立交接。每个 Agent 只写自己的路径；宿主逐个检查归属、机械集成无冲突结果，再对最终完整 diff 执行所有测试和能力驱动的语义验收。任何路径重叠、语义冲突或归属不明都会停止自动集成。

## 模式

- `None`：只回答，不改文件。
- `Light`：影响明确、没有高风险语义、最多三个普通文件，使用 Goal、Scope、Acceptance、Risks 四段式简报。
- `Full`：公共契约、跨工作区/仓库/微服务写入、迁移、权限、状态、事务、并发、幂等、新依赖、未决设计、阈值决策、超过三个文件或影响未知，使用带 `R-NNN`、`A-NNN`、`T-NNN` 追踪的完整基线。

只要存在疑问就使用 Full。实现后必须根据真实 diff 再分类，Light 越界时停止并升级。

## 辅助 CLI

当前实现的命令只有：

```text
gated-loop route "<任务>" --signals signals.json --json
gated-loop start "<任务>" --signals signals.json --host-runtime codex --json
gated-loop prepare --task <id> --baseline requirements/baseline.md --source requirements/notes.md
gated-loop freeze --task <id> --confirmed
gated-loop self-check --task <id> --round 1
gated-loop accept --task <id> --round 1
```

CLI 不从任务描述猜测权限、迁移或影响范围；这些事实必须放进 `signals.json`。未提供结构化信号时会保守选择 Full。

Light 简报通过 `start --brief brief.json` 传入，只有用户确认后才加入 `--confirmed`。无论 CLI 是否安装、开发方式是 active 还是 manual，冻结后的任务包与轮次产物都保存在 `.ai-dev-loop/<task-id>/`；宿主维护的任务注册表和工作区总纲位于 `.ai-dev-loop/` 根。开始前必须实际验证控制路径与目标 task 路径已被 Git 忽略，不能假定仓库默认配置正确。

开始开发前，宿主必须在当前轮次写入 `development-snapshot.json`，记录基线指纹、开发前 commit、允许路径和已有脏改动。单工作区使用 schema v1；跨工作区使用 schema v2，并同时提供用户确认的 `workspace-authorization.json` 与 PASS 的 `workspace-coverage.json`。v2 的每个 `root` 必须是 Git worktree 顶层；单仓库内的前后端或模块通过 `allowedPaths` 与命令 `cwd` 区分。`self-check` 会自动识别版本：v2 校验工作区、冻结命令分配和无环 `dependsOn` 图，按依赖波次逐仓库检查分支、HEAD、范围、已有改动与测试；前置构建、契约或验证失败会把消费方测试标记为 `BLOCKED`，全部通过后才生成聚合 PASS。`accept` 会再次验证所有仓库和聚合 diff 未变化。会联网、发布制品或改写工作区的依赖安装不属于隐式机械检查，必须在计划中单独授权。

`accept` 只接受 PASS 的机械证据，并先写出可见的 `review-plan.json`。宿主能调度 Agent 时，优先让与开发者分离的其他 Agent 验收，没有其他产品时再启动空开发上下文的同产品验收子 Agent，并用 `--review-result <file>` 或 `--review-result -` 提交结果。没有隔离能力时，CLI 默认不扫描或启动外部工具，而是生成 `NEED_HUMAN_REVIEW` 人工验收包；只有显式指定 `--reviewer codex|claude|auto` 才启用可选 CLI 适配器。CLI 校验 reviewer 来源、无开发上下文隔离、全部验收 ID、P0/P1/P2 数量和结论，再写入 `review.json`、轮次级 `acceptance-report.md` 与任务根级 `final-acceptance-report.md` 的首次汇总。用户随后完成的人工语义审查由宿主另存为当前轮次 `human-semantic-review.json`，并与最终确认状态一起重渲染汇总；不得宣称这是 CLI 原生能力。

## 安全边界

- 测试命令保存为 JSON argv，并用直接进程方式执行，不拼接 shell 字符串。
- 不自动读取 `.env`、生产配置、凭据目录或用户主目录作为需求来源。
- 不自动提交、推送、合并、迁移、发布或创建其他外部状态。
- 无法确认改动归属、上下文隔离或只读约束时返回 `NEED_HUMAN_REVIEW`。
- 测试未运行不能进入语义验收；没有第二种 Agent 不阻止开发和机械门禁，但人工路径不得标记为独立 `PASS`；独立审查 `PASS` 也不替代用户最终确认。

## 验证

```text
npm test
npm run test:coverage
python -X utf8 <skill-creator>/scripts/quick_validate.py skills/gated-ai-dev-loop
```

测试数量不是目标。仓库保留覆盖不同安全边界的代表性测试，后续根据真实使用继续精简和完善。

## 许可证

MIT
