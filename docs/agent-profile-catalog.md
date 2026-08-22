# Agent Profile Catalog 与专用 Team

Delivery Graph 把三件事分开：

1. `receiverAgentId` 表示当前宿主能够可信创建的原生 Agent 家族，例如 `codex`；
2. `agentProfileId` 表示这个 Loop 使用的专用职责，例如 `task-implementation`；
3. `teamPlan.helpers` 表示 owner 可按需使用的内部辅助角色。

只有外层 owner receiver 持有 reservation、operation、lease 和结果提交权限。helper 不能调用 Loop 生命周期工具，也不能代替 owner 作最终判断。

## 默认行为

项目根目录不存在 `delivery-graph.agents.json` 时使用 Plugin 内置 catalog：

- TASK：`task-implementation`，可选 `codebase-researcher`、`test-runner`、`result-checker`；
- TASK Review：`task-review`；
- GROUP Review：`group-review`；
- Delivery Review：`delivery-review`。

`plan_dispatch_batch` 返回当前 catalog 的版本、来源与 fingerprint；每个 assignment 还包含 profile、capabilities、输出契约和 `teamPlan`。reservation 会持久化 profile/catalog/team fingerprint，因此配置文件在 reservation 创建后发生变化，也不会改变已派任务的身份；新的配置只影响后续 reservation。

## 项目配置

需要定制时，把[完整示例](examples/delivery-graph.agents.json)复制到业务项目根目录并修改。配置是完整替换，不是部分 merge；这样可以避免隐式默认值在升级后改变派遣语义。

顶层字段固定为：

| 字段 | 约束 |
|---|---|
| `catalogVersion` | 当前只能是 `1` |
| `profiles` | 非空且 ID 唯一的完整 profile 数组 |
| `loopRoutes` | 必须精确覆盖四种 Loop kind |

每个 profile 必须精确包含 `id`、`kind`、`loopKinds`、`roleSkill`、`capabilities`、`helperProfiles` 和 `outputContract`。`RECEIVER` 还必须包含 `maxConcurrent`；`HELPER` 不得包含该字段，因为 helper 是宿主内部可选协作，不由 Controller 建立 reservation 或并发计数。

- `RECEIVER` 必须绑定合法 Loop kind；`roleSkill` 只能是该 Loop 原有的 `delivery-graph-task` 或 `delivery-graph-review`，不能绕过职责边界。
- `HELPER` 不拥有 Loop，`loopKinds=[]`、`roleSkill=null`、`helperProfiles=[]`。
- `helperProfiles` 只能引用已定义的 `HELPER`，不能形成嵌套派遣。
- RECEIVER 的 `maxConcurrent` 为 `1..4`，同时受全局 executor 和资源冲突门禁约束。
- `capabilities` 是声明与提示，不扩大文件、Git、工具或控制面权限。

## 为什么配置文件使用 JSON

运行时内部使用普通 Python `dict`，并非“Python 只能使用 JSON”。项目支持 Python 3.10+ 且坚持仅标准库；JSON 在 3.10 起原生可用，并能在 Codex、Claude Code、ZCode 和 MCP 之间保持同一份严格、确定性的 canonical fingerprint。TOML 的标准库解析器从 Python 3.11 才提供，YAML 则需要第三方依赖，因此当前磁盘配置只开放版本化 JSON。
