# 中央编排器配置

中央编排器配置只约束 Layered Delivery 外层 receiver 的并发和额度恢复。模型、
reasoning effort、Worker 供应商及 Loop 内部并发均由执行 Loop 和当前宿主管理，
不属于该配置。

## Schema v2

配置文件使用严格 schema v2。除版本字段外，只允许
`maxConcurrentExecutors` 与 `quotaExhaustionPolicy`：

```json
{
  "schemaVersion": 2,
  "maxConcurrentExecutors": 4,
  "quotaExhaustionPolicy": "PAUSE_AND_RESUME"
}
```

- `maxConcurrentExecutors`：整数 `1..64`。所有未过期 reservation 与仍处于
  `CLAIMED` 的外层 receiver 共同占用该上限。
- `quotaExhaustionPolicy`：schema v2 只接受 `PAUSE_AND_RESUME`。宿主必须使用
  结构化容量事实和真实 `resetAt` 暂停并一次性恢复；不得自动切换 Adapter、模型
  或内部 Worker。

配置不再包含自动编排、自动选模、Adapter allowlist、跨 Adapter、Review 多样性
或模型偏好开关。自动/手动由 Delivery execution mode 决定；可领取的 Adapter 由
当前 MCP 连接注册并通过宿主生命周期证明，不由用户 JSON 扩权。

## 配置文件位置

| 平台 | 默认路径 |
|---|---|
| Windows | `%APPDATA%\layered-delivery\orchestrator.json` |
| macOS | `~/Library/Application Support/layered-delivery/orchestrator.json` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/layered-delivery/orchestrator.json` |

环境变量 `LAYERED_DELIVERY_ORCHESTRATOR_CONFIG` 可以指向一个绝对路径。配置位于
Plugin 安装目录之外；Marketplace 安装、升级或卸载不修改它。手动编辑后新建宿主
会话以重新加载；通过配置面板保存时当前连接可以立即刷新。

## 配置面板

`open_orchestrator_settings` 只展示并发上限、固定额度策略、配置来源和当前可信宿主
Adapter 状态；`update_orchestrator_settings` 只保存 schema v2 的两个策略字段。
设置工具不读取或创建 `.layered-delivery` 状态，也不能改变任何 Delivery 控制面。

Adapter 状态仅说明当前连接是否具有可信外层 receiver 通道。PATH 中存在的 CLI、
本地 Worker 配置或模型列表只能用于宿主/Loop 内部执行，不能由面板升级为可领取
Graph 的 Adapter。

## 校验与故障处理

- 配置必须是 UTF-8 严格 JSON、最大 64 KiB，且不能是符号链接。
- `schemaVersion` 必须为 `2`；字段必须完整，未知字段一律拒绝。
- 文件不存在时使用内建默认值 `4 / PAUSE_AND_RESUME`。
- 文件存在但版本、字段或类型非法时 MCP Server fail closed，不静默回退。
- 已签发 reservation 和已 claim receiver 不被配置修改追溯重写；新并发上限从后续
  计划与容量判断开始生效。
