# Claude Code MCP 自动执行与权限前置条件

## MCP 改变的是授权粒度

完整 Plugin 通过 `.mcp.json` 启动一个本地 stdio MCP Server，并把 37 个 `layered-delivery` 工具交给 Claude。控制器调用不再表现为任意 Bash/Python 进程，因此不得为 `Bash(python *hdg.py *)`、`Bash(python *)` 或类似通配命令添加 allow 规则。

Claude Code 的权限模式仍属于执行宿主安全边界，不能由聊天提示、Skill 或仓库内容自行切换，MCP Server 也不能越过这条边界。首次安装/启用 Plugin 时，用户或组织策略可能需要信任 Server；随后应在 `/permissions` 或托管策略中按 MCP Server/tool 配置权限。项目根由 `${CLAUDE_PROJECT_DIR}` 在 Server 启动时绑定，工具调用不能传入 `root`、`dogfood` 或通用 `confirmed` 参数来扩大范围。

MCP 协议工具本名是 `graph_frontier` 等 snake_case 名称；`mcp__plugin_layered-delivery_layered-delivery__graph_frontier` 只是 Claude 的权限规则名，其中插件名与 Server 名用于宿主隔离。Plugin Skill 的 `allowed-tools` 逐项预批准 32 个中段自治工具，其中包含 `record_skill_activation` 与 `record_skill_conformance`；不使用 `__*` 通配符，也不批准任意 Shell。`freeze_hierarchy`、`rebuild_graph_run`、`cancel_graph_run`、`record_human_review_acceptance` 和最终 `record_user_confirmation` 不在预批准清单内，且 Server 将它们标记为 `anthropic/requiresUserInteraction`。五个 payload 暂存工具只运输目标绑定的输入，finalize 不执行业务动作；敏感目标仍必须调用原工具并触发原 prompt。方案冻结只能紧邻用户对当前指纹方案和方式的确认调用，不能从旧对话或自然语言关键词推断。

## Claude required Skill 原生调用

frontier 的 `requiredSkills` 不是“建议读取”的文档清单。用户批准整树并选择 active/manual 时已经一次授权这些 Skill；每个名称都必须由实际执行该阶段的 Claude context 通过 Skill tool 自动调用，不得要求用户再次确认或触发。普通 Read、读取 `SKILL.md`、父 Agent 预读或只把名称写进 subagent prompt 都不合格。Skill tool 返回后，以该次 tool-use ID、Claude session ID、当前 executor 与 execution ID调用 `record_skill_activation`，`mechanism` 固定为 `HOST_NATIVE_SKILL`。一个 tool-use ID 只能绑定一个 required Skill；DEVELOPMENT 必须先完成此记录，才允许用相同 owner/operation `dispatch_task`。

Claude 可以执行由任意 Agent CLI 创建并冻结的需求。冻结状态中的 `hostRuntime` 只记录方案创建宿主和当时的自动化提示，不限制当前执行宿主；Plugin MCP 从当前 Claude client session 形成实际执行宿主凭证。遇到其他宿主创建的 frozen graph 时直接从 `graph_frontier` 接续并记录统一 `HOST_NATIVE_SKILL`，不得重新 prepare/freeze、要求用户重新确认或二次确认 required Skill。

Claude subagent 是新的 context，父会话调用过 Skill 不会替代子 Agent 的调用。若真正执行阶段的是 subagent，它必须在自己的 context 明确调用并记录凭证。完整执行 Skill 后，执行者或独立 gate/reviewer 针对真实代码、diff、测试和产物形成命名检查，以 `record_skill_conformance` 绑定原激活凭证；成功迁移要求全部检查 `PASS`。如果 Skill 不存在，记录 `BLOCKED` 激活与符合性并按阶段 artifact 形成阻断，不能把 Read 或宽松匹配记为 PASS。

`anthropic/requiresUserInteraction` 是 Claude Code 的宿主扩展，不是 MCP Server 自己弹窗；MCP 标准 annotations 也只是宿主提示，不能替代权限策略。Claude Code 至少使用 2.1.199：更早版本会忽略该扩展，因此 Server 在识别到旧版 Claude Code 时直接以 `MCP_CLIENT_UPGRADE_REQUIRED` 拒绝上述五个敏感工具。版本满足要求后，是否允许仍由 Claude Code 的权限模式、tool 规则和组织策略决定。

## 一次配置后的 active 自治

希望长任务在用户确认方案后连续执行时，应在首次 `dispatch-task` 前同时满足：

- Plugin MCP Server 已被宿主信任并正常连接；
- `layered-delivery` 所需 MCP tools 已按 tool 级策略允许；
- Claude 对冻结范围内的代码编辑、测试和构建命令已使用 Auto 或精确权限规则准备好。

用户明确确认方案并选择 `active` 后，Graph 在当前冻结契约内自动调度、调用 required Skill、开发、测试、修复、逐级门禁、预算内重试和租约恢复，不再逐 Skill、逐 Task、逐测试或逐恢复请求确认。最终 acceptance 必须进入 `WAITING_FOR_USER_CONFIRMATION`，frontier 停在 `REQUEST_USER_CONFIRMATION` 等待用户验收；Git 提交/推送/合并、迁移、发布、新增外部权限和真实不可恢复阻断仍返回用户。

`acceptEdits` 只自动接受文件编辑和有限文件系统操作，不等价于完整无人值守。项目级 `.claude/settings.json` 和 `.claude/settings.local.json` 不能设置会话默认 Auto；需要时可由用户自行在用户级 Claude settings 中配置：

```json
{
  "permissions": {
    "defaultMode": "auto"
  }
}
```

这是用户安全偏好，不由 `layered-delivery` 写入。组织策略、所选模型或提供商不支持 Auto 时，该设置不能强行启用；应改用精确的 MCP tool 与代码/测试命令权限。规划 Agent 必须在用户确认 active 前展示尚未满足的宿主前置条件，不能在认领 Task 后持有租约等待权限弹窗。

不默认使用 `bypassPermissions`。它跳过常规权限检查，只适合用户明确配置并隔离的容器或虚拟机。

## manual 新窗口接续

manual 可由 Claude 或 Codex 完成方案确认和冻结，并生成一次性 `handoffCommand`。新 Claude 运行窗口在 MCP Server 已信任且 Auto/tool 权限就绪后启动该交接，即从 `graph-frontier` 恢复同一 graph run：

- 不重新 `prepare_hierarchy` 或 `freeze_hierarchy`；
- 不重新选择开发方式，不逐 Skill 或逐 Task 请求确认；
- 自动调度、开发、测试、修复、逐级门禁和预算内恢复；
- 根门禁与独立审查通过后停在 `REQUEST_USER_CONFIRMATION`；只有用户明确验收后才写入 `USER_CONFIRMED`。

`claudeCodeAutoHandoff` 仍可提供 Desktop/IDE Auto 模式说明、`claude --permission-mode auto` 交互入口、`claude -p --permission-mode auto` 无人值守入口及对应 argv。CLI 启动参数在聊天之外选择权限模式，不依赖 Claude 自己批准自己的权限。

## CLI fallback 权限

只有 Plugin/MCP 未安装、未连接或不可用时才回退到 `hdg.py`。CLI fallback 仍可能触发 Process 授权：用户可以启用 Auto，或在认领 Task 前通过 `/permissions` 精确预批准本次需要的完整控制器、Python、测试与构建命令集合。无法满足时保持未认领并报告外部授权阻断；不要为了省事批准任意 Python/Bash 通配命令。MCP 正常连接时不要因为 frontier 的 `commandHint` 是 CLI fallback 文本而启动 Shell，应按 action 调用对应 snake_case MCP tool。

交接提示不能自行改变权限模式，也不能启用 bypass、重新准备需求或要求用户逐 Task 启动。

## 运行中阻断

Auto 和 MCP tool allow 只减少冻结范围内的重复授权，不会批准敏感、破坏性或超出用户要求的动作。如果动作不属于冻结目标，应放弃；如果确实需要新的外部权限或授权，必须在没有活动 claim 时按 `EXTERNAL_AUTHORITY` 路由返回用户。正常测试失败、代码修复、gate 重试、心跳与 `WORKER_LOST` 恢复不是新的人工授权点。

官方说明：

- [Permission modes](https://code.claude.com/docs/en/permission-modes)
- [Permission rules](https://code.claude.com/docs/en/permissions)
- [Require approval for a specific MCP tool](https://code.claude.com/docs/en/mcp#require-approval-for-a-specific-tool)
