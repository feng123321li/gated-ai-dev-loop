# MCP 宿主生命周期、健康与注册矩阵契约

本契约把“Plugin 配置正确但工具没有进入 Agent schema”定义为宿主注册故障，而不是 Delivery Graph 状态故障。诊断链必须在 `delivery-graph`、`delivery-graph-dispatch` 或 `delivery-graph-receiver` 任一 MCP 不可调用时仍可工作；不得用 Graph 工具、Shell/SQLite 旁路或模拟返回修补治理状态。

## 协议基线

截至 2026-08-13，最新正式 MCP 规范是 `2026-07-28`。现代路径是无状态请求：宿主 spawn stdio server 后，可调用 `server/discover`，再调用 `tools/list`；每个请求携带协议与客户端 metadata，不需要 `initialize`。旧客户端保留 `initialize → notifications/initialized → tools/list` 回退，最高协商到 `2025-11-25`。

所以“热重连”不是恢复 MCP 会话：宿主重新 spawn 进程、重新 discovery/list、验证完整工具目录，再从下一次模型 turn 起装配新目录；原 Graph 状态只能在工具重新可用后通过权威只读工具恢复。模型单次推理请求中的工具集合不能中途改变，但没有必要把 schema 固化到整个 Agent 会话。

推荐宿主把 MCP registry/supervisor 放在 Agent 之外，并在每个模型 turn 前按目录指纹装配最新的逐工具 JSON Schema。`notifications/tools/list_changed` 使在线 server 的目录缓存失效；server 已退出时由 supervisor 重启并重新 list。新 child Agent 读取 registry 的最新快照，不复制父会话启动时的旧目录。不要用通用 `mcp_call(name, arguments)` 代理代替正常工具目录，否则会削弱逐工具 schema、审批、Skill allowlist、审计和模型选工具质量。

## P0 宿主生命周期日志

每次 server 尝试都应使用稳定的 `hostSessionId + workspaceKey + mcpServerName + attempt` 关联，并按 JSONL 记录：

| 阶段 | 宿主事件 | 必需事实 |
|---|---|---|
| `SPAWN_STARTED` | `mcp.server.connect.started` | executable 摘要、attempt、启动时间、timeoutMs |
| `CONNECTED` | `mcp.server.connected` | pid（若可公开）、耗时、transport |
| `DISCOVERY_COMPLETED` | 宿主自定义 | modern/legacy、协商版本、`tools/list` 数量、目录指纹 |
| `FAILED` | `mcp.server.failed` | 失败阶段、exitCode、timeoutMs、已脱敏 stderr、诊断 ID |
| `CLOSED` | `mcp.server.closed` | 主动/异常关闭、exitCode、最后成功阶段、已脱敏 stderr |

日志不得记录工具参数、Graph payload、operation ID、reservation 或凭据。stderr 至少保留脱敏后的有界文本；向跨会话矩阵导出时可只保留 `stderrPresent + stderrSha256`。没有这些事实时应报告“不可观测”，不能把未观察到等同于 server 未启动。

## Plugin 分阶段 stderr

`hdg_mcp.py` 以单行结构化 JSON 写 stderr，供宿主与上表关联。当前阶段包括 `SERVER_STARTED`、`DISCOVERY_RESPONDED`、`TOOLS_LIST_RESPONDED`、`LEGACY_INITIALIZE_RESPONDED`、`LEGACY_INITIALIZED`、`TRANSPORT_EOF`、`RESPONSE_DELIVERY_FAILED` 与 `TRANSPORT_DISCONNECTED`。每条记录给出 transport、协议模式、工具数和人类可读 `diagnosticHint`，但不包含请求 ID、项目路径或业务 payload。

若 Plugin 已记录 `TOOLS_LIST_RESPONDED` 且工具数完整，而 Agent schema 中仍无对应工具，故障边界在宿主的 catalog 校验/注入阶段；若仅有 `SERVER_STARTED`，则检查 transport、请求发送和超时；若连启动事件都没有，则检查 manifest 发现与 spawn。

## Plugin 外健康诊断

最终形态应是宿主内置 `mcp_health`：它直接读取宿主生命周期管理器，不能由待诊断 Plugin 提供。仓库内的只读回归 Demo 在宿主能力落地前提供同一判定语义：

```text
python scripts/mcp_registration_probe.py --host zcode --strict

python scripts/mcp_registration_probe.py --host zcode --profile dispatch --strict

python scripts/mcp_registration_probe.py --host zcode --profile receiver --strict

python scripts/mcp_registration_probe.py --host codex \
  --model-io <model-io.jsonl> \
  --lifecycle-log <mcp-lifecycle.jsonl> \
  --strict
```

Demo 只解析 model request 的工具目录与宿主生命周期 JSONL；不会调用模型、不会调用任何 MCP 工具、不会访问 `scheduler.db`、不会写治理状态。结果按 workspace、session、Agent role 和指定 Profile 形成矩阵。默认验证 `planning`，`--profile dispatch|receiver` 分别验证另两个 server：

- `REGISTERED`：该 Agent schema 中出现当前 Profile 的完整工具目录；
- `PLUGIN_MCP_UNAVAILABLE`：schema 可观察，但没有 delivery-graph 工具；
- `PARTIAL_REGISTRATION`：只注入部分工具；
- `NOT_OBSERVABLE`：日志没有工具目录，不能下故障结论。

ZCode 默认读取 `~/.zcode/cli/rollout/model-io-*.jsonl` 与 `~/.zcode/cli/log/*.jsonl`；Codex 日志位置由宿主版本决定，因此显式传入。三个 Profile 的工具联集为 35，但单 server 只发布自己的静态子集。若宿主使用不同 namespace 或 server 名，可用 `--tool-prefix`、`--server-name` 覆盖。

仓库还提供会话外 supervisor / 每-turn 动态目录的纯参考模拟：

```text
python scripts/mcp_dynamic_catalog_demo.py
```

它输出 `EXTERNAL_SUPERVISOR_PER_TURN` 矩阵，覆盖两个 workspace 的 primary/child Agent，并演示同一 session 的首 turn 为 `PLUGIN_MCP_UNAVAILABLE`、supervisor 原子发布完整目录后下一 turn 为 `REGISTERED`。首 turn 快照保持不可变，新 child 读取最新目录，部分目录会被原子拒绝。该脚本不 spawn MCP、不调用模型或工具，只是宿主实现与回归门禁的可执行参考，不代表当前 ZCode/Codex 已具备动态装配能力。

## 跨工作区 / Agent 回归门禁

最小矩阵至少覆盖两个 workspace，并在每个 workspace 覆盖 planning primary、dispatch coordinator、TASK/Review child；三种 Profile 分别运行探针。每个可观察 case 都必须为 `REGISTERED`、`matchingToolCount` 等于该 Profile 的目录大小，且最近一次 lifecycle attempt 不能以 `FAILED` 结束。`NOT_OBSERVABLE` 只能标为证据缺失，不能算通过。

当任一 case 为 `PLUGIN_MCP_UNAVAILABLE` 或 `PARTIAL_REGISTRATION` 时，测试失败并停止该会话的治理写入。Agent 不得伪造 `workspace_status`、preview、freeze 或 dispatch 状态。

## 热重连验收

宿主热重连应满足：

1. 用户或健康策略对精确 server 发起 reconnect，不重启整个会话；
2. 宿主结束旧 attempt，创建新 attempt，并完整记录 spawn、stderr、timeout 和协议阶段；
3. modern 重新执行 discovery/list，legacy 重新 initialize/list；
4. 只有该 Profile 的完整目录通过名称/schema 校验后，才从下一模型 turn 起原子使用新目录；失败时保留明确不可用状态，不注入半套工具；
5. 主 Agent 下一 turn 与之后新建的 child Agent 都读取 registry 最新目录，并在矩阵中产生新的可观察 case；会话级固化 schema 的旧宿主明确返回 `RECONNECT_REQUIRES_AGENT_RESTART`；
6. 重连不自动重放任何未知结果的写操作。工具恢复后先由业务会话使用保存的 `rootId` 读取权威状态。
