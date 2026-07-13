# Claude 开发与机械门禁

## 开发提示词契约

从冻结交接包生成可见提示词。把以下规则放在任务内容之后，避免被误认为可选背景：

```text
冻结交接包是唯一开发授权。
只实现列出的任务，并严格留在允许范围内。
不得重新分析、解释、澄清、重新设计或改写需求。
不得修改验收标准或冻结产物。
不得提交、推送、合并、发布、改变外部状态或读取秘密信息。
交接包不完整或冲突时，返回 BLOCKED 并列出具体阻断原因。
只报告修改文件和实现事实，不得判断 PASS。
```

## 主动启动 Claude Code

优先创建全新进程。使用 argv 数组并设置 `shell:false`。安全的基准调用为：

```json
[
  "claude",
  "-p",
  "--bare",
  "--no-session-persistence",
  "--disable-slash-commands",
  "--strict-mcp-config",
  "--permission-mode",
  "acceptEdits",
  "--tools",
  "Read,Glob,Grep,Edit,Write",
  "--append-system-prompt-file",
  "<visible-prompt.md>",
  "实现冻结交接包，只报告事实。"
]
```

不要加入 Bash、Web、Agent、notebook、MCP、resume、continue 或绕过权限的参数。Claude 退出后由宿主运行冻结测试。若已安装的 Claude CLI 不支持以上隔离调用，改用手动路径，不得静默降低隔离要求。

## 手动交接

打开新的 Claude Code 会话，提供相同的可见提示词和冻结交接包。明确这是实现会话，不是需求分析会话。只允许返回：

```json
{
  "status": "COMPLETED",
  "changedFiles": ["relative/path"],
  "facts": ["可观察的实现事实"],
  "blockers": []
}
```

`status` 只能是 `COMPLETED` 或 `BLOCKED`。`BLOCKED` 至少包含一个 blocker，`COMPLETED` 不得包含 blocker。把该结果视为开发者声明，真实 diff 和测试才是权威证据。

## 开发前快照

开始写入前记录：

- 当前 commit，或明确仓库没有 Git；
- staged、unstaged 和 untracked 路径；
- 可行时记录开发前已修改文件的哈希；
- 所有冻结产物指纹。

不得覆盖开发前已有改动，也不得把这些改动归属于 Claude。无法分离时要求人工审查。

## 机械门禁顺序

1. 重新读取并验证冻结产物。
2. 相对开发前快照计算真实改动路径。
3. 拒绝 `.git/**`、`.ai-dev-loop/**`、凭据文件、冻结产物和无关路径。
4. Light 的真实路径必须是精确 Scope 的子集，且不超过三个文件。
5. 根据真实 diff 重新执行 Full/Light 硬条件检查。
6. 逐条直接执行冻结测试 argv，不得拼成 shell 字符串。
7. 记录退出码和测试数量；失败、错误、无正当理由的跳过、超时或未运行都视为阻断。
8. 只有全部机械门禁通过后才能开始语义验收。

不要追求测试数量。优先保留少量代表性测试和边界矩阵；不再保护独立行为的重复测试应删除。
