# 开发方式与机械门禁

## 统一运行目录

无论 CLI 是否安装、宿主是谁、开发方式是什么，都使用协调工作区的两层布局：

```text
<project>/.ai-dev-loop/
├── task-registry.json
├── workspace-overview.md
├── .task-registry.lock（宿主写入时临时存在）
├── .host-staging/（冻结前宿主投影与 event 暂存）
└── <task-id>/
    ├── mode.json
    ├── baseline.md 或 light-brief.md
    ├── acceptance.json
    ├── tasks.json
    ├── source-manifest.json
    ├── decision-log.md
    ├── development-handoff.md
    ├── state.json
    ├── development-overview.md
    ├── progress.md
    ├── final-acceptance-report.md（首次 accept / 等价验收路由后生成）
    └── rounds/
        └── round-NN/
            ├── workspace-authorization.json（跨工作区时必需）
            ├── workspace-coverage.json（跨工作区时必需）
            ├── development-mode.json
            ├── parallel-plan.json（仅 parallel）
            ├── prompt.md
            ├── gate-continuation.md
            ├── result.json
            ├── lifecycle-events/
            ├── agents/（仅 parallel）
            ├── integration-result.json（仅 parallel）
            ├── development-snapshot.json
            ├── gate-evidence.json
            ├── self-check-report.md
            ├── acceptance-report.md
            ├── review.json
            └── human-semantic-review.json（仅人工语义审查完成后）
```

新任务统一使用 `development-handoff.md`。CLI 仍可读取旧任务已有的 `handoff-to-claude.md`，但不得再生成旧名称。CLI 缺失时仍建立 `.ai-dev-loop/<task-id>/`，不得改用 `.acceptance/`。根级注册表、工作区总纲、单写锁与冻结前 staging 由宿主按 [registry-transactions.md](registry-transactions.md) 维护；CLI `prepare/freeze` 可能替换整个任务目录，所以冻结前投影不得只保存在任务根。为兼容 CLI，不向任务根新增生命周期文件，也不修改 `state.json`；lifecycle event 只放轮次目录。开发代理不得写 `.ai-dev-loop/**`。

## 选择实际开发方式

基线冻结后、任何代码写入前，先判断冻结任务是否跨目录、跨仓库或跨微服务。跨工作区时必须按照 [multi-workspace.md](multi-workspace.md) 生成并验证 `workspace-authorization.json` 与 `workspace-coverage.json`；覆盖未通过时把注册表更新为 `WAITING_USER / WAITING_FOR_WORKSPACE_AUTHORIZATION`，不得生成 `prompt.md`、展示开发方式或启动开发 Agent。

单工作区任务，或多工作区覆盖结论为 `PASS` 后，把注册表阶段设置为 `WAITING_USER / WAITING_FOR_DEVELOPMENT_MODE_SELECTION` 并显示：

```text
需求基线已冻结，请选择开发方式：
1. 直接运行（active）：由当前宿主自动启动全新隔离开发 Agent
2. 手动运行（manual）：输出通用后续提示词，由你交给任意开发 Agent
```

必须等待用户明确选择，最终只记录 `active` 或 `manual`：

- `active`：任意宿主 Agent 使用自身可用的调度能力自动启动全新隔离开发 Agent，并记录实际开发 Agent；目标 Agent 不要求与宿主同类。
- `manual`：宿主只展示完整交接卡片和一份通用后续提示词，不预选接收 Agent、不输出工具专属 CLI 命令；用户可把提示词交给任意全新开发 Agent。

用户在当前宿主对话输入“直接运行”视为明确选择 `active`，输入“手动运行”视为明确选择 `manual`。不得使用隐藏默认值。选择 active 后，宿主仍需证明全新上下文、写入边界和调用状态；无法证明时说明原因并返回方式选择，不得暗中切换。选择 manual 后不再询问 Codex、Claude 或其他运行时。把选择写入 `rounds/round-NN/development-mode.json`：

```json
{
  "mode": "manual",
  "hostAgent": "<agent-id>",
  "developerAgent": null,
  "topology": "single",
  "status": "WAITING_FOR_MANUAL_DEVELOPER",
  "selectedBy": "user"
}
```

确定开发方式后按 `parallel-development.md` 评估执行拓扑。Light 直接记录 `single`；Full 符合资格时必须等待用户选择。等待期间注册表使用 `WAITING_USER / WAITING_FOR_EXECUTION_TOPOLOGY_SELECTION`。

## 开发提示词契约

只能在工作区覆盖门禁通过后，从冻结交接包生成 `rounds/round-NN/prompt.md`，并把以下规则放在任务内容之后。无论 active 还是 manual，开发 Agent 只读取冻结文件、当前 assignment、允许路径、开发前快照和该提示词；不得传入需求分析对话、隐藏推理或原宿主会话摘要。

多工作区提示词必须逐个列出工作区 ID、规范化绝对根路径、任务 ID、仓库相对允许路径、测试命令的 `cwd` 与 argv、依赖波次，以及 `workspace-authorization.json`、`workspace-coverage.json` 和 schema v2 快照路径。接收 Agent 在首次写入前只读预检全部工作区；任一不可访问或不匹配时在零写入状态返回 `BLOCKED`。

```text
冻结交接包是唯一开发授权。
只实现列出的任务，并严格留在允许范围内。
不得重新分析、解释、澄清、重新设计或改写需求。
不得修改验收标准或 .ai-dev-loop/**。
不得提交、推送、合并、发布、改变外部状态或读取秘密信息。
交接包不完整或冲突时，返回 BLOCKED 并列出具体阻断原因。
只报告修改文件和实现事实，不得判断 PASS。
```

## 直接运行模式

任意宿主 Agent 都可以发起。宿主使用自身可用的 Agent、子任务、独立会话或隔离进程能力，只传入冻结交接、允许路径和结果契约。每次主动调用前先在根级锁内写 `ACTION_CLAIMED` 与 `activeOperations[]`；single 声明一个 operation，parallel 在同一事务声明当前波次全部成员，释放锁后才调用。用户确认 active + parallel 计划后，宿主自动按波次派遣开发 Agent，不再逐个请求确认。不能证明新上下文、只读交接边界或写入归属时停止自动调用，展示事实并让用户改选 manual 或 single。

临时 runner 只能放入系统临时目录。用 argv 和 `shell:false` 启动外部进程，不得在业务仓库创建 `run-*.mjs`、批处理或临时提示脚本。

single 每轮默认只进行一次主动调用；parallel 的每个 assignment 也只调用一次。只有本地参数错误且确认对应开发单元零写入时，说明原因并取得用户同意后才允许修正重试一次。遇到 `429/529`、模型容量、认证或网关错误时：

- single 确认零写入：记录失败，回到 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION` 并推荐 manual；
- parallel 的失败 Agent 确认零写入且其他归属明确：停止后续波次，按并行契约请求重新分配、manual 或 single；
- 失败开发单元已有写入或无法确认归属：停止并返回 `NEED_HUMAN_REVIEW`。

不得通过隐藏的连续重试延长不可见交互，也不得在用户未选择 manual 时自行开始手动路径。

## 手动模式

宿主必须一次性展示：

```text
开发方式：manual
项目路径：<absolute-project-path>
任务目录：<project>/.ai-dev-loop/<task-id>
冻结交接：<task-dir>/development-handoff.md
本轮提示：<task-dir>/rounds/<round>/prompt.md
执行拓扑：single | parallel
当前状态：WAITING_FOR_MANUAL_DEVELOPER
门禁接续：<task-dir>/rounds/<round>/gate-continuation.md
```

多工作区时把“项目路径”改为“协调工作区”，并紧接着列出完整工作区清单：`workspaceId`、绝对根路径、授权状态、任务 ID、允许路径、测试 `cwd` 和依赖波次。不得只展示协调工作区或当前 shell 所在目录。

紧接着输出一份与 Agent 产品、CLI 和操作系统无关的可复制提示词：

```text
请在协调工作区 <absolute-project-path> 发起本轮冻结开发任务，并仅在下列已授权工作区中写入：
<逐项列出 workspaceId、绝对根路径、任务 ID、允许路径和依赖顺序；单工作区时列出一项>

只读取并严格执行：
1. <absolute-task-dir>/development-handoff.md
2. <absolute-task-dir>/rounds/<round>/prompt.md

不要重新分析、澄清、设计或改写需求。只实现冻结范围并只报告事实；不要修改 .ai-dev-loop/**，不要提交、推送、合并或发布。
若缺少冻结范围内任一仓库的可写工作区、必要权限或上游契约实现，返回 BLOCKED 并列出阻断项，不要自行降级、绕过或扩大范围。
完成后按 prompt.md 规定的结构化结果契约返回。
```

不能输出 `claude`、`codex` 等工具专属启动命令，也不能在手动交接前要求用户选择接收 Agent。冻结范围包含多个工作区时，必须先完成交接覆盖门禁，随后在提示词中逐个列出绝对路径和授权状态；宿主已知缺少任一工作区时不得输出提示词。接收端环境与宿主不一致而缺少工作区时，开发 Agent 点名该工作区并在任何写入前返回 `BLOCKED`。`manual + parallel` 为每个 assignment 分别输出一份只含自身任务、工作区、允许路径和对应提示词路径的通用提示词。

开发 Agent 不需要知道原宿主是谁。它只返回后文规定的结构化结果，不自行验收。宿主在开始开发前同时生成 `rounds/round-NN/gate-continuation.md`：

```markdown
# <task-id> <round> 门禁接续

任意宿主 Agent 都可以接管本轮，不需要原需求或开发对话。

1. 先校验协调工作区的 task-registry.json、当前焦点和目标 task 的 phase / nextAction，再读取任务根目录的 mode、冻结授权、acceptance、tasks、state、development-overview 和 progress；Project 规模同时读取 rounds/planning/project-plan.md。
2. 读取本轮 development-mode、development-snapshot、prompt，以及用户提供的开发 Agent 结构化结果。
3. 验证冻结指纹、HEAD、开发前已有改动和真实 diff；无法归属时返回 NEED_HUMAN_REVIEW。
4. 接管宿主取得根级锁并复核 revision 后，以 create-new 保存已校验的 result.json 与 lifecycle event；再按“registry(PENDING) → workspace-overview(PENDING) → task projections → projection ack → workspace-overview(CURRENT)”逐 T 更新 IMPLEMENTED 或 BLOCKED，并完成 S-008 回写；不得让开发 Agent 写 .ai-dev-loop/**。
5. 先为 `gated-loop self-check --task <task-id> --round <NN>` 写机械门禁 operation claim，再在锁外运行并锁内结算；非 PASS 时停止，并按同一顺序回写 S-009、阻断项和证据。
6. 机械门禁 PASS 后按验收能力路由：为其他独立 Agent 或同宿主全新只读子 Agent 分别 claim reviewer operation；没有隔离能力时生成完整人工验收包。随后为 `gated-loop accept` 写独立 CLI operation claim，锁外运行、锁内结算，并按同一顺序回写 S-010 和符合证据的 VERIFIED 任务。
7. 每个 SOP 状态变化都先按 [registry-transactions.md](registry-transactions.md#写回事务) 和 [tracking.md](tracking.md) 完成规范状态与人可读投影，再继续；不重新分析或改写冻结需求，不自动提交、推送、合并或发布。
```

开发完成后，用户可以把开发结果交给任意新的宿主 Agent，并输入：

```text
请接管项目 <absolute-project-path> 的门禁流程。
读取 <absolute-task-dir>/rounds/<round>/gate-continuation.md，并使用下面的开发 Agent 结果继续；不要重新分析需求：

<粘贴开发 Agent 返回的结构化 JSON>
```

不要求回到原宿主对话。接管 Agent 可以运行确定性机械门禁，但不能让开发 Agent 验收自己的改动；语义验收优先使用全新无开发上下文的其他 Agent 或子 Agent。两者都不可用时转人工，并明确不声称完成独立语义验收。

开发者只允许返回：

```json
{
  "status": "COMPLETED",
  "changedFiles": ["relative/path"],
  "taskResults": [
    {
      "task": "T-001",
      "status": "IMPLEMENTED",
      "facts": ["该任务的可观察实现事实"],
      "blockers": []
    }
  ],
  "facts": ["可观察的实现事实"],
  "blockers": []
}
```

总体 `status` 只能是 `COMPLETED` 或 `BLOCKED`；这里的 `COMPLETED` 仅表示实现调用结束，不是注册表的任务终态。每个已分配的 T 必须且只能出现一次，任务状态只能是 `IMPLEMENTED` 或 `BLOCKED`。宿主把声明保存为 `rounds/round-NN/result.json`，随后逐项更新 registry 和 `progress.md`；开发者不得直接写这些文件。真实 diff 和测试才是权威证据，`IMPLEMENTED` 不能被表述为已验收。

## 开发前快照

开始写入前记录当前 commit、staged/unstaged/untracked 路径、开发前已有修改的可用哈希，以及冻结产物指纹。不得覆盖或把开发前已有改动归属于开发者；无法分离时要求人工审查。

宿主把快照写入 `rounds/round-NN/development-snapshot.json`。`allowedPaths` 是本轮唯一写入白名单；Light 只能列出冻结 Scope 中的精确文件，Full 可以使用安全的仓库相对 glob。已有敏感文件只记录路径和状态，不读取内容，并直接阻断开发。

以下 schema v1 只用于单工作区。跨工作区使用 [multi-workspace.md](multi-workspace.md#多工作区开发快照) 定义的 schema v2，对每个工作区分别记录根路径、分支、HEAD、任务、允许路径和已有改动。

```json
{
  "schemaVersion": 1,
  "task": "task-id",
  "round": "round-01",
  "baseCommit": "40-or-64-character-git-object-id",
  "frozenFingerprint": "sha256",
  "allowedPaths": ["src/example/**"],
  "preExistingChanges": [
    {
      "path": "src/existing.ext",
      "statusCode": " M",
      "worktreeSha256": "sha256-or-null-for-deleted-file"
    }
  ]
}
```

开发返回后优先运行：

```text
gated-loop self-check --task <task-id> --round <NN>
```

命令自动识别 schema v1 或 v2。schema v2 会读取当前轮次的工作区授权与覆盖文件，按 `dependsOn` 计算波次，逐工作区执行 Git、白名单、已有改动和冻结测试门禁，再生成聚合 `gate-evidence.json` 与 `self-check-report.md`。前置工作区失败时后置测试保持 `BLOCKED`。

缺少快照、HEAD 已变化、已有脏改动被再次修改或 diff 被截断时关闭门禁，不猜测归属。

## 机械门禁顺序

1. 重新读取并验证冻结产物。
2. 相对开发前快照计算真实改动路径；多工作区逐个计算并验证归属。
3. 拒绝 `.git/**`、`.ai-dev-loop/**`、凭据文件、冻结产物和无关路径。
4. Light 的真实路径必须是精确 Scope 的子集，且不超过三个文件。
5. 根据真实 diff 重新执行 Full/Light 硬条件检查。
6. 逐条在授权的工作区和 `cwd` 中直接执行冻结测试 argv，不得拼成 shell 字符串；再按依赖顺序执行跨服务集成检查。
7. 把命令、退出码和测试数量写入当前轮次 `gate-evidence.json`；失败、错误、无正当理由的跳过、超时或未运行都视为阻断。
8. 只有全部机械门禁通过后才能开始语义验收。

不要追求测试数量。优先保留少量代表性测试和边界矩阵；不再保护独立行为的重复测试应删除。
