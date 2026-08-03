# 中央编排器配置

中央编排器使用一份用户级配置协调同一台机器上的 Codex、Claude Code
及后续可信宿主 Adapter。配置位于 Plugin 安装目录之外，因此从 Marketplace
安装或升级 Plugin 不会覆盖它；同一台机器上的不同宿主和项目共享这份配置，
不需要分别设置。

## 可视化配置面板

向 Agent 说“打开中央编排器设置”，或直接调用
`open_orchestrator_settings`。MCP Server 会返回标准 MCP Apps 资源：支持 MCP
Apps 的宿主以内嵌卡片显示开关、Adapter 状态、并发上限、额度策略和 Review
策略；点击“保存设置”会调用 `update_orchestrator_settings`，经宿主审批后原子写入
下述用户配置文件，并立即更新当前 MCP 连接。

设置工具不依赖 Delivery 工作区，也不读取或创建 `.layered-delivery` 状态。Codex
以 `--project-root-from-meta` 启动 MCP 时，Graph 工具仍要求每次请求提供 sandbox
工作区 metadata；`open_orchestrator_settings` 和 `update_orchestrator_settings` 是仅有的
用户级例外，因此 MCP Apps 面板发起保存时即使没有项目 metadata 也可以完成。
该例外只提供固定用户配置路径，不能访问或改变任何 Delivery 控制面。

面板不是 Codex 的永久原生 Settings 页面。OpenAI 当前公开文档明确保证自定义
组件在 ChatGPT 中以内嵌 iframe 渲染，并要求 Codex 在不渲染组件时仍能完成同一
流程。因此不同 Codex Desktop 版本是否内嵌显示取决于宿主是否实现 MCP Apps：
支持时直接显示面板；不支持、Codex CLI 或纯终端宿主会得到同一份结构化配置摘要，
Agent 仍可在用户明确要求后调用保存工具。不要按产品名猜测 UI 能力。

Adapter 行的状态有严格含义：

- `当前宿主原生可派遣`：本次会话的可信宿主 Adapter，可进入原生 assignment。
- `仅检测到本机终端`：PATH 中发现了 CLI，只能作为外部建议，不能自动派遣。
- `未检测到`：仅为已配置或内建候选，当前没有运行能力事实。

面板不加载外部脚本、样式、字体或网络资源；不支持 UI 的宿主不会失去功能。

## 默认策略

配置文件不存在时使用以下内建默认值：

| 选项 | 默认值 | 说明 |
|---|---:|---|
| 自动编排 | 开启 | 允许活动 Delivery 调用宿主原生自动派遣计划 |
| 自动选择模型 | 开启 | 按 `ROUTINE` / `STANDARD` / `HIGH` 选择动态 inventory 中的宿主原生模型名 |
| 跨 Adapter 调度 | 关闭且只读 | 当前版本未提供可信多 Adapter 宿主桥接 |
| 允许的 Adapter | `codex`、`claude-code` | 这是允许列表，不表示对应 Adapter 已安装或可用 |
| 最大并发执行器 | `4` | 跨 Delivery 统计已预留和已认领的执行器 |
| 额度耗尽 | 暂停并恢复 | 等待可信 `resetAt` 后恢复，不默认切换 Adapter |
| Review 策略 | 优先不同 Adapter | 仅在跨 Adapter 已开启且存在可信可用候选时生效 |

等价配置如下：

```json
{
  "schemaVersion": 1,
  "automaticOrchestration": true,
  "autoSelectModel": true,
  "allowCrossAdapterDispatch": false,
  "allowedAdapters": ["codex", "claude-code"],
  "maxConcurrentExecutors": 4,
  "quotaExhaustionPolicy": "PAUSE_AND_RESUME",
  "preferDifferentAdapterForReview": true
}
```

## 配置文件位置

| 平台 | 默认路径 |
|---|---|
| Windows | `%APPDATA%\layered-delivery\orchestrator.json` |
| macOS | `~/Library/Application Support/layered-delivery/orchestrator.json` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/layered-delivery/orchestrator.json` |

可将环境变量 `LAYERED_DELIVERY_ORCHESTRATOR_CONFIG` 设置为一个绝对路径，
让该宿主读取管理员下发或其他固定位置的配置。配置仍然按宿主机器生效；不同
物理机器、远程开发机或容器各自需要一份配置。不要把个人跨 Adapter 权限写入
Plugin 源码、Marketplace manifest 或 Plugin 缓存目录。

## 手动创建或修改

1. 在对应平台的用户配置目录中创建 `layered-delivery` 目录。
2. 新建 `orchestrator.json`，复制上面的完整 JSON。
3. 修改需要的选项并保存；字段必须完整，不能增加未知字段。
4. 新建 Codex 或 Claude Code 会话，使 Plugin MCP Server 重新加载配置；通过
   可视化面板保存时，当前连接会立即刷新，无需新建会话。
5. 已经签发的 reservation 和已经认领的 Loop 不会被追溯改写；新配置从后续
   派遣计划开始生效。

## 当前跨 Adapter 可用性

当前发行版只信任本次 MCP 连接声明的宿主 Adapter，并返回：

```json
{
  "featureAvailability": {
    "crossAdapterDispatch": {
      "supported": false,
      "mutable": false,
      "code": "ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE"
    }
  }
}
```

面板会显示错误说明并锁定“跨 Adapter 调度”和“自动切换到其他 Adapter”。保存
工具拒绝 `allowCrossAdapterDispatch=true` 或
`quotaExhaustionPolicy=SWITCH_ADAPTER`，且不会写入配置文件。旧文件如果已经包含
这些值，面板仍可打开并显示限制；使用受支持值再次保存可将策略恢复为安全状态。

真正开放该能力前，中央宿主必须能通过正式原生 API 创建多个 Adapter 的目标
上下文、验证容量，并为它们签发同一编排根下的 receiver attestation。PATH 中发现
的 CLI、外部进程和仅安装但未认证的 Adapter 不会被升级为自动派遣能力。

## 选项值

### `automaticOrchestration`

- `true`：允许生成自动派遣计划。
- `false`：`plan_dispatch_batch` 拒绝自动编排；手动交接不受影响。

### `autoSelectModel`

- `true`：Agent 分析产生 `ROUTINE → EFFICIENT`、`STANDARD → BALANCED`、
  `HIGH → FRONTIER` 路由。
- `false`：不选择新的原生模型名，宿主必须提供精确 `current_executor`，所有
  新节点沿用当前 Agent 和原生模型。原生调用后的本机转发不属于编排器配置。

### `allowCrossAdapterDispatch` 与 `allowedAdapters`

- `false`：只选择能够确定的当前 Adapter；多候选但无法确定当前 Adapter 时
  fail closed。
- `true`：当前保存接口以 `ORCHESTRATOR_CROSS_ADAPTER_UNAVAILABLE` 拒绝，直至
  可信多 Adapter 宿主桥接正式开放。
- `allowedAdapters` 使用宿主 inventory 的 `adapterId`；inventory 省略该字段时
  默认等于 `agentId`。允许列表中的 Adapter 未安装、未认证、无容量或不能原生
  创建上下文时仍不可派遣。

### `maxConcurrentExecutors`

接受 `1` 到 `64`。中央控制根中的所有未过期 reservation 和仍处于 CLAIMED 的
执行器共同占用该上限，防止不同 Delivery 同时超量派遣。

### `quotaExhaustionPolicy`

接受以下值：

| 值 | 行为 |
|---|---|
| `PAUSE_AND_RESUME` | 默认；暂停受影响 Loop，并在可信额度恢复时间后继续 |
| `SWITCH_ADAPTER` | 当前不可保存；与跨 Adapter 功能一同保持锁定 |
| `ASK_USER` | 不自动恢复或切换，等待用户选择 |

配置只表达宿主策略。没有结构化额度事件或真实 `resetAt` 时不能猜测恢复时间；
硬 429 仍由模型外 Adapter 回调处理，失败模型不负责报告自身暂停。

### `preferDifferentAdapterForReview`

开启后，Review 在满足权限、能力、容量和模型要求的候选中优先避开上游实际
Adapter。无法异构时仍可使用独立上下文完成 Review，但必须如实报告降级，不能
宣称跨模型独立。

## 校验与故障处理

- 配置必须是 UTF-8、严格 JSON，最大 64 KiB，且不能是符号链接。
- `schemaVersion` 当前只接受 `1`；布尔值不能写成字符串。
- Adapter ID 必须唯一，不能包含空白、命令参数、路径或凭据。
- 当前保存接口拒绝跨 Adapter 开关与切换策略；不要通过手工文件绕过该能力门禁。
- 配置文件不存在时使用安全默认值；文件存在但非法时 MCP Server fail closed，
  不会静默回退后意外开启跨 Adapter 调度。
- Plugin 升级和卸载不会主动修改或删除用户配置；需要恢复默认时，关闭所有相关
  会话后删除该文件，再新建会话。
