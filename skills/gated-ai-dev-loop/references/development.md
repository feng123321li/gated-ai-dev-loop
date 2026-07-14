# 开发方式与机械门禁

## 统一运行目录

无论 CLI 是否安装、宿主是谁、开发方式是什么，都只使用：

```text
<project>/.ai-dev-loop/<task-id>/
├── mode.json
├── baseline.md 或 light-brief.md
├── acceptance.json
├── tasks.json
├── source-manifest.json
├── decision-log.md
├── handoff-to-claude.md
├── state.json
├── development-overview.md
├── progress.md
└── rounds/
    └── round-NN/
        ├── development-mode.json
        ├── parallel-plan.json（仅 parallel）
        ├── prompt.md
        ├── result.json
        ├── agents/（仅 parallel）
        ├── integration-result.json（仅 parallel）
        ├── development-snapshot.json
        ├── gate-evidence.json
        ├── self-check-report.md
        ├── acceptance-report.md
        └── review.json
```

当前 CLI 使用 `handoff-to-claude.md` 作为兼容文件名；把它视为冻结开发交接包，Codex 开发上下文也可以读取。CLI 缺失时仍建立 `.ai-dev-loop/<task-id>/`，不得改用 `.acceptance/`。开发代理不得写 `.ai-dev-loop/**`；总览、进度和轮次文件由宿主创建和更新。

## 选择实际开发方式

基线冻结后、任何代码写入前，设置状态 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION` 并显示：

```text
需求基线已冻结，请选择开发方式：
1. active：由当前宿主启动同类全新开发上下文
2. manual：输出完整交接，由你在新的 Codex 或 Claude 中执行
```

必须等待用户明确选择，最终只记录 `active` 或 `manual`：

- `active`：当前 Codex 宿主启动全新 Codex 开发上下文；当前 Claude 宿主启动全新 Claude 开发上下文。要求 `developerRuntime == hostRuntime`。
- `manual`：宿主展示完整交接卡片，用户把冻结包交给全新的 Codex 或 Claude。允许 `developerRuntime` 与 `hostRuntime` 不同。

不得使用隐藏默认值或持久化 `auto`。用户选择 active 后，宿主仍需证明全新上下文、写入边界和调用状态；无法证明时说明原因并重新请求选择。用户选择 manual 后，再让用户选择 `developerRuntime=codex|claude`。把最终选择写入 `rounds/round-NN/development-mode.json`：

```json
{
  "mode": "manual",
  "hostRuntime": "codex",
  "developerRuntime": "claude",
  "topology": "single",
  "status": "WAITING_FOR_MANUAL_DEVELOPER",
  "selectedBy": "user"
}
```

确定开发运行时后按 `parallel-development.md` 评估执行拓扑。Light 直接记录 `single`；Full 符合资格时必须等待用户选择。等待期间状态使用 `WAITING_FOR_EXECUTION_TOPOLOGY_SELECTION`。

## 开发提示词契约

从冻结交接包生成 `rounds/round-NN/prompt.md`，并把以下规则放在任务内容之后：

```text
冻结交接包是唯一开发授权。
只实现列出的任务，并严格留在允许范围内。
不得重新分析、解释、澄清、重新设计或改写需求。
不得修改验收标准或 .ai-dev-loop/**。
不得提交、推送、合并、发布、改变外部状态或读取秘密信息。
交接包不完整或冲突时，返回 BLOCKED 并列出具体阻断原因。
只报告修改文件和实现事实，不得判断 PASS。
```

## 主动模式

Codex 宿主应启动全新的 Codex 开发 agent、子任务或独立任务，只传入冻结交接、允许路径和结果契约。Claude 宿主应启动全新的 Claude agent、子会话或隔离进程并传入相同内容。用户确认 active + parallel 计划后，宿主自动按波次派遣多个同运行时子 Agent，不再逐个请求确认。不能证明子 Agent 能力或上下文隔离时改用 manual 或 single。

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
冻结交接：<task-dir>/handoff-to-claude.md
开发运行时：codex | claude
执行拓扑：single | parallel
当前状态：WAITING_FOR_MANUAL_DEVELOPER
完成后返回当前宿主并输入：开发完成，请继续机械门禁
```

手动 Claude 示例：在项目根目录打开全新 Claude Code 会话，把 `prompt.md` 作为系统补充提示，只发送“实现冻结交接包，只报告事实”。手动 Codex 示例：在相同项目中新建 Codex 任务，只提供 `prompt.md` 和冻结交接包。不要复用需求分析任务。

开发者只允许返回：

```json
{
  "status": "COMPLETED",
  "changedFiles": ["relative/path"],
  "facts": ["可观察的实现事实"],
  "blockers": []
}
```

`status` 只能是 `COMPLETED` 或 `BLOCKED`。宿主把声明保存为 `rounds/round-NN/result.json`；开发者不得直接写该文件。真实 diff 和测试才是权威证据。

## 开发前快照

开始写入前记录当前 commit、staged/unstaged/untracked 路径、开发前已有修改的可用哈希，以及冻结产物指纹。不得覆盖或把开发前已有改动归属于开发者；无法分离时要求人工审查。

宿主把快照写入 `rounds/round-NN/development-snapshot.json`。`allowedPaths` 是本轮唯一写入白名单；Light 只能列出冻结 Scope 中的精确文件，Full 可以使用安全的仓库相对 glob。已有敏感文件只记录路径和状态，不读取内容，并直接阻断开发。

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

缺少快照、HEAD 已变化、已有脏改动被再次修改或 diff 被截断时关闭门禁，不猜测归属。

## 机械门禁顺序

1. 重新读取并验证冻结产物。
2. 相对开发前快照计算真实改动路径。
3. 拒绝 `.git/**`、`.ai-dev-loop/**`、凭据文件、冻结产物和无关路径。
4. Light 的真实路径必须是精确 Scope 的子集，且不超过三个文件。
5. 根据真实 diff 重新执行 Full/Light 硬条件检查。
6. 逐条直接执行冻结测试 argv，不得拼成 shell 字符串。
7. 把命令、退出码和测试数量写入当前轮次 `gate-evidence.json`；失败、错误、无正当理由的跳过、超时或未运行都视为阻断。
8. 只有全部机械门禁通过后才能开始语义验收。

不要追求测试数量。优先保留少量代表性测试和边界矩阵；不再保护独立行为的重复测试应删除。
