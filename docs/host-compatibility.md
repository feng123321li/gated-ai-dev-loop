# 宿主兼容矩阵

兼容性分成两层，不能混为一个“支持”：

- **核心契约**：Python Controller、schema、SQLite、生成产物、Hook 单元测试和 stdio MCP 握手通过。
- **真实宿主**：实际 Codex 或 Claude Code 会话加载候选 Plugin，创建原生子 Agent，并完成 claim、progress、heartbeat、result，最后到达待用户确认门禁；冒烟程序不得代替用户确认。

## 0.34.5 发布矩阵

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动 | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动 | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动 | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | Codex 手动门禁 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | Claude Code 手动门禁 | Claude PreToolUse、StopFailure 与子 Agent |

发布管理员完成真实冒烟后，应在发布记录中填写准确宿主版本和结果；矩阵中的“CI 自动”不等于已经验证模型账户、Keyring、Hook 信任或原生 Agent 容量。

当前矩阵只验证可信外层 receiver：Claude 宿主的 claim 必须来自受认证的 `claude-code` receiver，Codex 宿主的 claim 必须来自受认证的 `codex` receiver。PATH 中存在的 CLI 或 Loop 内 Worker 不能取得 Graph 控制面权限。新增外层供应商 Adapter 后必须作为独立矩阵维度验证，不能复用内部 Worker 成功记录宣称支持。

## 当前候选基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.146.0 | 从候选 Marketplace 安装 | Controller 交互契约、manual TASK 接入、`SubagentStart`、receiver mutation Hook、当前宿主继承策略、待用户确认状态 |
| Claude Code | 2.1.220 | `--plugin-dir` 候选包及最终 Marketplace 安装 | Controller 交互契约、manual TASK 接入、receiver attestation、progress/heartbeat/result、StopFailure 兼容 |

上述版本是 0.34.5 候选基线，不是永久兼容承诺。宿主升级后若 Hook 事件字段、Plugin manifest 或 MCP 工具命名发生变化，应先在自托管 Runner 重跑真实宿主冒烟，再更新本矩阵。

## 模型与内部 Worker 兼容

外层调度不选择模型。自动 receiver 继承当前宿主模型与默认 reasoning 设置，模型不进入 reservation、decision fingerprint 或 claim 授权。CC Switch、本地配置、企业网关和其他转发器属于宿主/Loop 内部能力，不改变 Graph 身份。

receiver 可以在 Loop 内使用 Codex、Claude、Grok、DeepSeek 或其他 Worker，并在最终 `workerTelemetry` 中按 phase 非权威报告 agent/model/effort；无法权威观察时写 `unreported`。这些供应商不需要单独调度分支。只有要让某个供应商直接领取 Graph 时，才必须增加并验证可信外层 Adapter。

## 支持状态定义

| 状态 | 含义 |
|---|---|
| 已验证 | 当前版本、当前平台真实完成对应门禁 |
| 核心契约通过 | Controller 与 MCP 合约通过，但未启动真实模型宿主 |
| 候选验证中 | 已登记宿主，尚未完成发布候选真实冒烟 |
| 不支持 | 缺少所需 Plugin、Hook、MCP 或原生 Agent 能力 |

团队对外说明只能使用已经取得的状态；不得把 PATH 中存在某个 CLI 写成“真实宿主已验证”。
