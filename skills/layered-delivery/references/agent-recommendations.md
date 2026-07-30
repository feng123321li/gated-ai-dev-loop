# Agent 与模型建议

用于在不改变 Graph 执行的前提下，发现当前主机可用的终端 Agent，并为每个 TASK、TASK Review、递归 GROUP Review 和 Delivery Review 展示 Agent + 当前模型建议。

## 调用

1. 调用无参数 `available_agents`，取得当前主机快照。
2. hierarchy 已 `PREPARED` 或 Graph 已冻结时，调用 `recommend_executors`，传入对应 `root_id`。
3. 按 `nodeId` 展示 `recommended`、`alternatives`、`confidence`、`reasons` 和 `independence`。

每次调用都重新读取当前本机配置。Claude Code 经 CC-Switch 切换 GLM、DeepSeek 或其他模型后，下一次发现应展示新模型；不把旧模型清单写入 Frozen Graph。

推荐器不参与提供方限额恢复。软阈值暂停后由原 Agent 等待宿主原生计划提示，直接收到 429 时由人工恢复；两种情况都不临时改写推荐、不自动换 Agent。

## 强制边界

- 返回值固定为建议性绑定，`binding=ADVISORY`、`dispatchAllowed=false`。
- 推荐工具本身不创建接收 Agent、不调用外部开发 CLI、不切换当前会话模型、不 claim Loop，也不改变 `dispatch_loop.owner`。自动执行模式的总调度器可在工具返回后通过宿主原生 Agent 机制执行派遣。
- 建议和临时不可用列表都不持久化到 schema v3、hierarchy、Graph、SQLite、事件链或人类投影；配置或容量改变后重新发现。
- 推荐器不解析 `loop.payload` 或 `loop.result`。TASK Loop 只按开发角色匹配；TASK/GROUP/Delivery Review 额外优先选择不同于上游开发建议的 Agent。
- 只有一个合格 Agent 时仍可展示它，但 Review 的 `independence.satisfied=false` 且置信度降低；独立上下文要求继续由实际 Loop 执行机制保证。
- `model.id=null` 表示终端可用但无法安全确定当前模型，只能展示为 current/default，不能猜测模型。

## 通用发现

内置探针只检查常见终端命令是否存在并读取 `--version`，当前包括 Codex、Claude Code、Cursor、OpenCode、Aider、Gemini CLI、Grok CLI、GLM CLI、DeepSeek CLI 和 Qwen CLI。探针是可移植适配器，不包含个人绝对路径、Token、Base URL、模型 allowlist 或固定用户 Profile。

Codex 当前模型从本机 Codex 配置的非敏感模型字段读取；Claude Code 当前模型从环境或 Claude settings 的非敏感模型字段读取，因此兼容 CC-Switch 的任意模型替换。输出不返回认证字段或代理地址。

## 本地 Agent Profile

未知或团队自定义终端通过用户本地 JSON Profile 扩展。使用环境变量 `LAYERED_DELIVERY_AGENT_PROFILES` 指向文件；未设置时读取平台用户配置目录下的 `layered-delivery/agent-profiles.json`。Plugin 不创建或修改该文件。

```json
{
  "profiles": [
    {
      "id": "team-terminal",
      "displayName": "Team Terminal",
      "command": "team-agent",
      "model": "any-model-id",
      "reasoningEffort": "high",
      "capabilities": ["development", "review"],
      "priority": 20
    }
  ]
}
```

`id` 与模型名由用户定义，不要求属于内置厂商。`command` 只接受不带路径和参数的可执行文件名，并且只执行安全的 `--version` 探针。Profile 不得包含密钥、Token、Base URL 或命令参数。

## 展示

每个 Loop 至少展示：

- 节点与角色；
- 推荐 Agent、命令和当前模型；
- 置信度；
- 选择原因；
- 最多三个备选；
- Review 异构 Agent 独立性是否满足；
- “仅建议，不会自动调用”的固定说明。
