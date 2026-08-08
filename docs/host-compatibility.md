# 宿主兼容矩阵

兼容性分成两层，不能混为一个“支持”：

- **核心契约**：Python Controller、schema、SQLite、生成产物、Hook 单元测试和 stdio MCP 握手通过。
- **真实宿主**：实际 Codex 或 Claude Code 会话加载候选 Plugin，创建原生子 Agent，并完成 claim、progress、heartbeat、result，最后到达待用户确认门禁；冒烟程序不得代替用户确认。

当前 canonical Plugin/Skill 名为 `delivery-graph`，展示名为“分层交付 Graph 控制面”。`.layered-delivery/` 只是稳定的项目数据目录，不随 Plugin identity 更名。

## 0.37.0 发布候选矩阵

0.37.0 保持 29 个 MCP 工具。Python 全量测试、`compileall`、Skill/Plugin 镜像一致性、协议元数据、发布校验和 diff 检查是核心候选门禁；实际结果以对应候选提交和 CI 为准，不可替代真实宿主验证。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动 | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动 | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动 | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex 结果待回填 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Claude Code 结果待回填 | Claude PreToolUse、StopFailure 与子 Agent |

### 0.37.0 真实宿主验证基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.147.0 | 从待验证的 `delivery-graph` 0.37.0 Marketplace 包安装 | `pendingInteraction` 的 `DEVELOPMENT_BASELINE → EXECUTION_MODE` 顺序；dirty 内容或 index 变化使旧指纹失效；primary checkout 创建独立 worktree 项目任务；manual TASK、单仓手动漂移双分支、多仓漂移 fail closed；`SubagentStart`/`PreToolUse`；待用户确认状态 |
| Claude Code | 2.1.226 | `delivery-graph` 0.37.0 `--plugin-dir` 包及最终 Marketplace 安装 | 相同交互与 Git 漂移边界；普通 MCP 工具自动放行且敏感工具仍询问；`delivery-graph:delivery-coordinator` 在稳定 linked worktree 后台运行；Claude 专用 `PreToolUse`/`StopFailure`；receiver attestation 与 progress/heartbeat/result |

两个宿主都必须确认 Controller 不执行 Git 写操作；Codex 默认 Hook 清单不得包含 `StopFailure`，Claude manifest 必须指向独立的 `claude-hooks.json`。上述版本是候选目标，不是永久兼容承诺。

## 0.36.0 历史发布矩阵

以下内容记录 0.36.0 当时的发布与候选状态，不是 0.37.0 的现行能力说明。

源码发布事实：`main` 与 tag `v0.36.0` 指向提交 `ad19c33`；本地核心契约已完成 258 项 Python 测试、`compileall`、Skill/Plugin 镜像一致性和 `validate_release`（29 个 MCP 工具）校验。上述事实不包含模型账户、Keyring、Hook 信任、Marketplace 安装或原生子 Agent 的真实宿主验证。

| 环境 | Python | 核心契约 | 真实宿主 | 发布用途 |
|---|---:|---|---|---|
| Linux Runner | 3.10 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 最低 Python 兼容 |
| Linux Runner | 3.12 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 常用 Python 兼容 |
| Linux Runner | 3.14 | CI 自动（结果以对应 pipeline 为准） | 不适用 | 最新 Python 兼容 |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Codex 结果待回填 | Codex 原生 Hook 与子 Agent |
| Windows 自托管 Runner | 3.10+ | 发布任务 | 候选验证中：Claude Code 结果待回填 | Claude PreToolUse、StopFailure 与子 Agent |

发布管理员完成真实冒烟后，应在发布记录中填写准确宿主版本和结果；矩阵中的“CI 自动”不等于已经验证模型账户、Keyring、Hook 信任或原生 Agent 容量。

当前矩阵只验证可信外层 receiver：Claude 宿主的 claim 必须来自受认证的 `claude-code` receiver，Codex 宿主的 claim 必须来自受认证的 `codex` receiver。PATH 中存在的 CLI 或 Loop 内 Worker 不能取得 Graph 控制面权限。新增外层供应商 Adapter 后必须作为独立矩阵维度验证，不能复用内部 Worker 成功记录宣称支持。

## 0.36.0 历史真实宿主验证基线

| 宿主 | 候选验证版本 | Plugin 加载方式 | 必须验证 |
|---|---|---|---|
| Codex | codex-cli 0.146.0 | 从待验证的 0.36.0 Marketplace 包安装 | `DEVELOPMENT_BASELINE → EXECUTION_MODE` 原生选择器顺序与同 Delivery Revision 偏好复用、primary checkout 自动创建 worktree 项目任务且不切换 `main`/`master`、manual TASK 接入、`SubagentStart`、receiver mutation Hook、当前宿主继承策略、待用户确认状态 |
| Claude Code | 2.1.220 | 0.36.0 `--plugin-dir` 发布包及最终 Marketplace 安装 | `DEVELOPMENT_BASELINE → EXECUTION_MODE` 原生选择器顺序与同 Delivery Revision 偏好复用、普通 MCP 工具由 Skill `allowed-tools` 自动放行且敏感 Hook 仍询问、自动 Delivery 在稳定 linked worktree 启动后台 coordinator 且主会话仅监控、PreToolUse Hook 注入工作区 attestation 并同会话续接、manual TASK 接入、receiver attestation、progress/heartbeat/result、StopFailure 兼容 |

上述版本是 0.36.0 当时的真实宿主验证目标，不是永久兼容承诺；文档未记录它们对 0.36.0 的实测通过结果。该版本尚未把脏工作树纳入基线前置交互，`start_manual_handoff` 的 Git 漂移阻断重确认也仍是后续 Phase 2；这些限制已由 0.37.0 的现行契约取代。宿主升级后若 Hook 事件字段、Plugin manifest 或 MCP 工具命名发生变化，应先在自托管 Runner 重跑真实宿主冒烟，再更新矩阵。

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
