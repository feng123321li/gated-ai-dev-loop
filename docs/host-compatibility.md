# 宿主兼容矩阵

兼容性分成两层，不能混为一个“支持”：

- **核心契约**：Python Controller、schema、SQLite、生成产物、Hook 单元测试和 stdio MCP 握手通过。
- **真实宿主**：实际 Codex 或 Claude Code 会话加载候选 Plugin，创建原生子 Agent，并完成 claim、progress、heartbeat、result，最后到达待用户确认门禁；冒烟程序不得代替用户确认。

## 0.32.0 发布矩阵

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动 | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动 | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动 | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | Codex 手动门禁 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | Claude Code 手动门禁 | Claude PreToolUse、StopFailure 与子 Agent |

发布管理员完成真实冒烟后，应在发布记录中填写准确宿主版本和结果；矩阵中的“CI 自动”不等于已经验证模型账户、Keyring、Hook 信任或原生 Agent 容量。

当前矩阵只验证同宿主原生派遣：Claude 终端的全部 claim 必须是 `claude-code`，Codex 终端的全部 claim 必须是 `codex`。即使 PATH 发现另一 CLI，也不得加入 `HOST_NATIVE` inventory。可信多 Adapter 桥接尚未实现，因此 0.32.0 不建立跨 Agent 冒烟任务；未来实现后应作为独立矩阵维度增加，不能复用当前结果宣称支持。

## 当前候选基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.146.0 | 从候选 Marketplace 安装 | 29 工具、`SubagentStart`、mutation Hook、原生 modelId、待用户确认状态 |
| Claude Code | 2.1.220 | `--plugin-dir` 候选包及最终 Marketplace 安装 | 29 工具、dispatch attestation、progress/heartbeat/result、StopFailure 兼容 |

上述版本是 0.32.0 候选基线，不是永久兼容承诺。宿主升级后若 Hook 事件字段、Plugin manifest 或 MCP 工具命名发生变化，应先在自托管 Runner 重跑真实宿主冒烟，再更新本矩阵。

## 模型与转发兼容

调度契约只使用宿主原生模型角色和 `modelId`。CC Switch、本地配置文件、企业网关或其他转发器可以在原生调用之后替换实际模型；宿主能够观测时只把它记录为 `actualModelId` 展示。实际代理模型不参与路由、能力推断、reservation、attestation 或 Review 独立性。

因此兼容矩阵验证的是 Codex/Claude 的原生 selector 与 Hook 协议，不为 GLM、DeepSeek 或其他转发目标分别建立调度分支。

## 支持状态定义

| 状态 | 含义 |
|---|---|
| 已验证 | 当前版本、当前平台真实完成对应门禁 |
| 核心契约通过 | Controller 与 MCP 合约通过，但未启动真实模型宿主 |
| 候选验证中 | 已发现宿主，尚未完成发布候选真实冒烟 |
| 不支持 | 缺少所需 Plugin、Hook、MCP 或原生 Agent 能力 |

团队对外说明只能使用已经取得的状态；不得把 PATH 中发现某个 CLI 写成“真实宿主已验证”。
