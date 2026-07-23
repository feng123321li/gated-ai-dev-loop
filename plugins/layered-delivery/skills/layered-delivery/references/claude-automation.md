# Claude Code 自动执行与权限前置条件

## 为什么交接提示不能消除 Process 弹窗

Claude Code 的权限模式属于执行宿主安全边界，不能由聊天提示、Skill 或仓库内容自行切换。`acceptEdits` 只自动接受文件编辑和有限的文件系统命令；运行 Python、测试、构建器和控制器等 Process 仍可能弹出授权。因此只把“不要询问用户”写进 handoff，并不能形成无人值守执行。

项目级 `.claude/settings.json` 和 `.claude/settings.local.json` 不能把会话默认模式设为 `auto`。Auto 必须通过用户级或托管设置、Desktop/IDE 模式选择器，或 CLI 启动参数启用。官方说明：

- [Permission modes](https://code.claude.com/docs/en/permission-modes)
- [Permission rules](https://code.claude.com/docs/en/permissions)

## 一次性用户配置

希望本机新 Claude 会话默认适合长任务时，可由用户自行在用户级 Claude settings 中配置：

```json
{
  "permissions": {
    "defaultMode": "auto"
  }
}
```

这是用户安全偏好，不由 `layered-delivery` 写入。组织策略、所选模型或提供商不支持 Auto 时，该设置不能强行启用；此时只能在认领 Task 前通过 `/permissions` 精确预批准完整命令集合，无法满足时保持未认领并报告外部授权阻断。

不默认使用 `bypassPermissions`。它跳过常规权限检查，只适合用户明确配置并隔离的容器或虚拟机。

## 入口一：在 Claude 中启动并选择 active

`prepare-hierarchy` 在 `hostRuntime` 为 `claude`、`claude-code` 等 Claude 标识时返回 `hostAutomation`：

- `recommendedPermissionMode` 为 `auto`；
- `acceptEditsIsUnattended` 为 `false`；
- `promptCanChangePermissionMode` 为 `false`；
- `claimPrecondition` 要求在 active 冻结和首次 `dispatch-task` 前完成权限配置。

规划 Agent 必须在用户确认 active 前展示这个前置条件。Auto 未就绪时不得冻结后直接认领，不得持有 30 分钟租约等待 Process 弹窗。

## 入口二：从其他宿主 manual 交接到 Claude

manual 冻结除通用 `handoffCommand` 外，还返回 `claudeCodeAutoHandoff`：

- `desktopInstruction`：Desktop/IDE 先在模式选择器选中 Auto，再粘贴 `handoffCommand`；
- `interactiveCommand`：以 `claude --permission-mode auto` 启动交互式接收会话；
- `unattendedCommand`：以 `claude -p --permission-mode auto` 启动无人值守接收任务；
- `interactiveArgv` 与 `unattendedArgv`：供不应拼接 shell 字符串的宿主直接传递参数。

CLI 启动参数在聊天之外选择权限模式，因此不会依赖 Claude 自己批准自己的权限。交接提示仍禁止修改权限配置、启用 bypass、重新准备需求或逐 Task 请求人工启动。

## 运行中阻断

Auto 减少日常 Process 授权，但不会批准敏感、破坏性或超出用户要求的动作。此类阻断不是额外的 layered-delivery 人工门禁：如果动作不属于冻结目标，应放弃；如果确实需要新的外部权限或授权，必须在没有活动 claim 时按 `EXTERNAL_AUTHORITY` 路由返回用户。
