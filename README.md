# 门禁式 AI 开发循环

一套同时适用于 Codex 和 Claude Code 的通用开发 Skill。前期需求可以来自对话、Issue、PRD、截图、原型或代码分析；开发前统一冻结唯一授权，随后让 Claude 专注写代码，再由独立上下文验收。

这不是某个需求框架的插件，也不限制 Windows。核心 Skill 是纯 Markdown，安装器和辅助 CLI 使用 Node.js，可在 Windows、macOS 和 Linux 运行。

## 工作方式

1. 当前 Codex 或 Claude 宿主采集、分析并审核需求，自动选择 `Full`、`Light` 或 `None`。
2. 用户确认 Full 基线或 Light 简报后冻结，开发授权不再漂移。
3. 新的 Claude 开发上下文只实现冻结任务，不做二次需求分析，也不判断 `PASS`。
4. 宿主检查真实 diff、范围、指纹和冻结测试。
5. 优先启动新的只读 Codex 验收；没有 Codex 时，启动新的、空上下文、只读 Claude 验收。
6. 独立审查通过后仍由用户最终确认。

完整的角色、门禁、升级和修复循环见：[工作流程图](skills/gated-ai-dev-loop/references/workflow.md)。

`gated-loop` 当前只自动完成路由、基线准备和冻结。Claude 开发、机械门禁、独立验收及修复轮次由 Skill 指导执行，避免把尚未实现的自动化包装成可用命令。

## 环境要求

- Node.js `20.19` 或更高版本；
- 需要主动开发时安装 Claude Code；
- 需要 Codex 独立验收时安装或使用 Codex。

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

Claude 可以作为前期宿主完成分析、审核和冻结，不强制交给 Codex 复审。进入开发阶段后，应启动新的 Claude 上下文执行冻结交接；原开发上下文不能验收自己的工作。

## 模式

- `None`：只回答，不改文件。
- `Light`：影响明确、没有高风险语义、最多三个普通文件，使用 Goal、Scope、Acceptance、Risks 四段式简报。
- `Full`：公共契约、迁移、权限、状态、事务、并发、幂等、新依赖、未决设计、阈值决策、超过三个文件或影响未知，使用带 `R-NNN`、`A-NNN`、`T-NNN` 追踪的完整基线。

只要存在疑问就使用 Full。实现后必须根据真实 diff 再分类，Light 越界时停止并升级。

## 辅助 CLI

当前实现的命令只有：

```text
gated-loop route "<任务>" --signals signals.json --json
gated-loop start "<任务>" --signals signals.json --host-runtime codex --json
gated-loop prepare --task <id> --baseline requirements/baseline.md --source requirements/notes.md
gated-loop freeze --task <id> --confirmed
```

CLI 不从任务描述猜测权限、迁移或影响范围；这些事实必须放进 `signals.json`。未提供结构化信号时会保守选择 Full。

Light 简报通过 `start --brief brief.json` 传入，只有用户确认后才加入 `--confirmed`。运行产物保存在 `.ai-dev-loop/<task>/`，该目录默认被 Git 忽略。

## 安全边界

- 测试命令保存为 JSON argv，并用直接进程方式执行，不拼接 shell 字符串。
- 不自动读取 `.env`、生产配置、凭据目录或用户主目录作为需求来源。
- 不自动提交、推送、合并、迁移、发布或创建其他外部状态。
- 无法确认改动归属、上下文隔离或只读约束时返回 `NEED_HUMAN_REVIEW`。
- 测试未运行不能进入语义验收；独立审查 `PASS` 也不替代用户最终确认。

## 验证

```text
npm test
npm run test:coverage
python -X utf8 <skill-creator>/scripts/quick_validate.py skills/gated-ai-dev-loop
```

测试数量不是目标。仓库保留覆盖不同安全边界的代表性测试，后续根据真实使用继续精简和完善。

## 许可证

MIT
