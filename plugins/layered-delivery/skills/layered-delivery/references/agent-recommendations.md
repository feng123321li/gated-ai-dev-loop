# Agent 与模型建议

用于在不改变 Graph 执行的前提下，为每个 TASK、TASK Review、递归 GROUP Review 和 Delivery Review生成两类不会混用的建议：当前宿主 Agent 内的自动执行预览，以及显式跨 Agent 的人工开发交接建议。

## 调用

`available_agents` 与 `recommend_executors` 都是只读工具；前者服务人工候选发现，后者按所选开发方式生成路由建议。

1. hierarchy 已 `PREPARED` 或 Graph 已冻结时，总调度 Agent 先为全部 Loop 给出 `ROUTINE`、`STANDARD` 或 `HIGH` 的临时分析。
2. 默认调用 `recommend_executors(recommendation_mode=AUTOMATIC)`，同时传入当前宿主真实 `executor_inventory`、覆盖全部 Loop 的 `node_requirements`，以及必要时的 `current_executor`。该模式只接受当前执行 Agent：Codex 中只建议 Codex 原生 Luna/Terra/Sol 等 selector；Claude Code 中只建议 Claude 原生 Haiku/Sonnet/Opus 等 selector。
3. 只有用户选择手动交接时，才调用 `recommend_executors(recommendation_mode=MANUAL_HANDOFF)`。宿主可用 `manual_development_agent_id` 明确指定 TASK 的目标开发 Agent；未指定时，优先建议不同于当前宿主且声明 development 能力的本机 Agent。
4. `available_agents` 只为人工交接提供本机终端快照。它不构成宿主原生派遣 inventory。

人工交接每次调用都重新读取当前本机配置中的非敏感可见信息。配置可能由用户直接修改，也可能由任意本机工具维护；本项目不识别或依赖具体修改器，不把旧模型清单写入 Frozen Graph。人工交接中发现的 GLM、DeepSeek 等当前配置模型只用于展示，不参与 Agent 排名或自动模型选择。

推荐器不参与提供方限额恢复。软阈值暂停后由原 Agent 等待宿主原生计划提示；硬 429 由宿主适配器持久化容量熔断、取消周期监控并只安排一次 reset 后唤醒。两种情况都不临时改写推荐、不自动换 Agent。

## 自动派遣计划

`AUTOMATIC` 返回 `binding=HOST_NATIVE_DISPATCH_PREVIEW`，但 `dispatchAllowed=false`：它与 `plan_dispatch_batch` 复用同一原生路由函数和决策指纹计算，只是不预留容量、不创建 Agent、不 claim。预览固定为 `selectionScope=CURRENT_EXECUTION_AGENT_ONLY`、`crossAgentRecommendationAllowed=false`，所以当前在 Codex 就不会建议 Claude，当前在 Claude Code 也不会建议 Codex。冻结后，正式 Ready 批次第一次调用 `plan_dispatch_batch` 会返回 `HOST_NATIVE_ROUTE_REVIEW` 并开启持久化 30 秒调整窗口；主 Agent 展示中文路由表，不再次提问，在到期时自动重调并取得带预留的 `HOST_NATIVE_DISPATCH_PLAN`。派遣时若 frontier、容量、配置或 inventory 已变化，仍以新计划为准。

`MANUAL_HANDOFF` 返回 `binding=MANUAL_HANDOFF_ADVISORY`。它允许为 TASK 切换开发 Agent，但固定 `automaticDispatchAllowed=false`、`LOCAL_TERMINAL / EXTERNAL_PROCESS / hostDispatchEligible=false`，只生成交接信息。冻结后由宿主为目标开发 Agent 创建独立接收会话：Codex 目标进入新的 Codex 任务，Claude Code 目标进入新的 Claude 会话；总调度会话不执行被交接的开发工作。人工目标不能被倒填成 AUTO assignment，也不使用自动模式的 30 秒超时派遣。

自动 assignment 还服从用户级中央编排器配置。默认 `automaticOrchestration=true`、`autoSelectModel=true`、`allowCrossAdapterDispatch=false`、`allowedAdapters=[codex, claude-code]`、`maxConcurrentExecutors=4`、`quotaExhaustionPolicy=PAUSE_AND_RESUME`、`preferDifferentAdapterForReview=true`。当前跨 Adapter 与自动切换策略不可保存，直到宿主原生多 Adapter 桥接开放；同机 Codex 与 Claude Code 仍共享其他策略，Marketplace 升级不覆盖。完整路径和手动修改见 [orchestrator-configuration.md](orchestrator-configuration.md)。

正式计划分两步返回。`HOST_NATIVE_ROUTE_REVIEW` 只包含 `reviewing`，没有 reservation，固定显示 30 秒调整窗口；相同决策到期后再次调用才返回 `HOST_NATIVE_DISPATCH_PLAN`。用户在窗口内提交不同 `preferredNativeModelId` 时，变化节点重新计时。预留与已 claim Loop 都占用跨 Delivery Agent 槽位。assignment 的 `model.id` 始终是 Claude/Codex 原生 Agent API 接受的模型名或角色选择器。Claude Code 通过 dispatch PreToolUse Hook 签发目标级凭证，接收方以原生 Agent/模型选择器、attested context、HOST_NATIVE、预留 ID 与决策指纹调用 `dispatch_loop`。Codex assignment 返回 reservation 派生的唯一 `hostTaskName`，`SubagentStart` Hook 用 Codex transcript 校验 child/parent/task，在 bearer 进入模型上下文前完成唯一 AUTO 预留的 host-side claim，只向 child 注入非秘密 assignment。宿主若能观测原生调用被本机配置转发后的模型，则另记 `actualModelId` 供展示；该值不参与 claim。外部进程仍以 `UNSAFE_EXECUTOR_TRANSPORT` deferred；计划工具本身不启动 Agent、不切换当前会话模型，也不会在 Hook 之外 claim。

### 计算顺序

派遣计划不用模型自由打分，而是执行可复现的字典序路由：

1. 用户策略：关闭 `automaticOrchestration` 时拒绝自动计划；Adapter 必须进入 `allowedAdapters`。跨 Adapter 关闭时只保留能够确定的当前 Adapter，无法唯一确定时 fail closed。
2. 路由基准：`autoSelectModel=true` 且存在节点 Agent 分析时使用 `AGENT_ANALYSIS`；缺失分析且宿主报告当前执行器时，只为缺失节点使用 `CURRENT_EXECUTOR_FALLBACK`。关闭自动选模时所有新节点都要求并沿用精确 `current_executor`。
3. 硬过滤：两条路径都要求 `dispatchTransport=HOST_NATIVE`、角色能力匹配和 `availableSlots > 0`；`AGENT_ANALYSIS` 另要求 `nativeModelSelectionSupported=true`，表示宿主原生 Agent API 能选择 inventory 中声明的原生模型名。回退路径要求当前 Agent/原生模型与 inventory 精确匹配。终端发现结果不能直接复制为宿主 inventory。
4. 推理等级：总调度 Agent 在派遣前从 `loop_context` 按固定风险规则为当前 Ready TASK/Review 自动判为 `ROUTINE`/`STANDARD`/`HIGH`，并通过临时 `node_requirements` 提交来源与原因。明确、低歧义、可重复且有确定验证路径的节点使用 `ROUTINE`；无法可靠判断时使用 `HIGH`。Controller 不做本地语义识别，也不接受 payload 自带的模型路由指令。回退节点保持 `UNCLASSIFIED`。
5. 模型匹配：`ROUTINE` 目标为 `EFFICIENT`；`STANDARD` 目标为 `BALANCED`；`HIGH` 必须存在 `FRONTIER` 模型，否则节点以 `NO_HIGH_REASONING_MODEL` deferred。候选内再按 tier 距离、较高 tier、模型优先级、稳定模型 ID 排序；回退节点直接使用当前模型。
   - 用户不认可默认建议时，可在尚未 claim 的节点要求精确原生模型。总调度 Agent 在 30 秒调整窗口内重新提交 `source=USER_POLICY` 与 `preferredNativeModelId`，预览和正式计划共同按该 selector 路由，不重新 freeze，也不增加第二次确认；变化节点从新表展示时重新计时 30 秒。`HIGH` 仍只能选择 `FRONTIER`；GLM、DeepSeek 等转发后模型不在原生 inventory 中，返回 `PREFERRED_NATIVE_MODEL_UNAVAILABLE`，不得把它们冒充 selector。
6. Agent 匹配：当前版本的自动建议只使用当前宿主 Agent，不做跨 Agent 建议；Review 仍使用独立上下文，并在当前 Agent 的原生模型档位内匹配。`preferDifferentAdapterForReview` 只为未来可信宿主原生多 Adapter 桥接保留，当前不据此制造不可执行的异构建议。展示用 `actualModelId` 不参与多样性判断；回退节点不改换 Agent。
7. 容量分配：每选中一个 assignment 就扣减对应 Agent 的临时槽位；写预留时再次在 SQLite 事务内扣减跨 Delivery 共享槽位，并原子校验 `maxConcurrentExecutors`，槽位耗尽的后续节点保持未 claim。

因此“高推理 Agent”不是另一个隐藏 Agent 类型，而是“具备所需角色能力的宿主原生 Agent + 可显式选择的 `FRONTIER` 原生模型 + `HIGH` 推理决策”。控制器只认识宿主声明的原生 selector 与 tier，不认识、探测或推断 selector 后面的实际提供方模型：Codex inventory 可以把 luna 标为 `EFFICIENT`、terra 标为 `BALANCED`、sol 标为 `FRONTIER`；Claude inventory 可以使用 `haiku`、`sonnet`、`opus` 等宿主原生角色并声明对应 tier。

例如总调度 Agent 当前使用 `gpt-5.6-sol`，但 Codex 原生 Agent API 允许子 Agent 选择 sol/terra，且有两个空闲槽：

```json
{
  "root_id": "d-service",
  "expected_graph_fingerprint": "<graphFingerprint>",
  "executor_inventory": [
    {
      "agentId": "codex",
      "adapterId": "codex",
      "displayName": "Codex",
      "dispatchTransport": "HOST_NATIVE",
      "capabilities": ["development", "review"],
      "availableSlots": 2,
      "priority": 20,
      "nativeModelSelectionSupported": true,
      "models": [
        {
          "id": "gpt-5.6-luna",
          "family": "gpt-5.6",
          "tier": "EFFICIENT",
          "reasoningEffort": "low",
          "priority": 30
        },
        {
          "id": "gpt-5.6-terra",
          "family": "gpt-5.6",
          "tier": "BALANCED",
          "reasoningEffort": "medium",
          "priority": 20
        },
        {
          "id": "gpt-5.6-sol",
          "family": "gpt-5.6",
          "tier": "FRONTIER",
          "reasoningEffort": "high",
          "priority": 10
        }
      ]
    }
  ]
}
```

在上述 Codex inventory 示例中，`ROUTINE` TASK 得到 Luna、`STANDARD` TASK 得到 Terra、`HIGH` Review 得到 Sol；换成 Claude inventory 时选择对应 tier 的原生 Claude 角色。Review 仍进入新的独立上下文，但当前不虚构跨 Agent 异构审查。模型 ID 只是宿主 inventory 示例，控制器不内置厂商 allowlist，也不会猜测 selector 最终被转发到哪个实际模型。

## 强制边界

- 两种返回都固定 `dispatchAllowed=false`。自动预览为 `HOST_NATIVE_DISPATCH_PREVIEW`，人工交接为 `MANUAL_HANDOFF_ADVISORY`，不得混写。
- 推荐工具本身不创建接收 Agent、不调用外部开发 CLI、不切换当前会话模型、不 claim Loop，也不改变 `dispatch_loop.owner`。自动执行模式的总调度器只能通过宿主原生 Agent 机制执行派遣，禁止调用 `codex --write`、`codex-companion` 或等价外部自治命令；真正的接收方必须把原生 `agent_id` / `model_id` 与 `dispatch_transport=HOST_NATIVE` 交给 `dispatch_loop`，未被采用的建议不得写成执行事实。
- 自动决策指纹同时绑定 Graph、节点、Agent、原生 `modelId`、推理等级和派遣通道。宿主观测到的 `actualModelId` 不进入 fingerprint、reservation、tier、能力、Review 多样性或授权比较；它只形成 `原生 modelId → 实际 actualModelId` 的展示关系，未知时必须省略或显示“未报告”，不得猜测。
- 建议和临时不可用列表都不持久化到 schema v3、hierarchy、Graph、SQLite、事件链或人类投影；配置或容量改变后重新发现。
- 推荐器不解析 `loop.payload` 或 `loop.result`。自动模式的 TASK/Review 都固定当前执行 Agent，只按推理等级选择原生模型档位；人工模式才允许 TASK 切换开发 Agent。
- 自动模式只有一个当前宿主 Agent 时，Review 使用独立上下文并报告真实的 `diversityLevel`，不能因为本机发现了另一个 CLI 就宣称异构 Agent 审查已满足。
- `model.id=null` 表示终端可用但无法安全确定当前模型，只能展示为 current/default，不能猜测模型。

## 通用发现

内置探针只检查常见终端命令是否存在并读取 `--version`，当前包括 Codex、Claude Code、Cursor、OpenCode、Aider、Gemini CLI、Grok CLI、GLM CLI、DeepSeek CLI 和 Qwen CLI。探针是可移植适配器，不包含个人绝对路径、Token、Base URL、模型 allowlist 或固定用户 Profile。

Codex 当前模型从本机 Codex 配置的非敏感模型字段读取；Claude Code 当前模型从环境或 Claude settings 的非敏感模型字段读取。配置来自手工编辑还是任意本机修改器都不影响该只读边界，输出不返回认证字段或代理地址。该发现结果用于展示/建议，不得作为 AUTO inventory 中原生 selector 的替代品。

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

自动建议使用中文表格，至少包含：`节点 | 模式 | 执行 Agent | 原生模型角色 | 原生 modelId | 实际代理模型 | 状态`。执行前实际代理模型显示“未报告”；领取后宿主若有观测，显示“原生 modelId → actualModelId”，但不回写建议。

人工交接使用另一张中文表格，至少包含：`节点 | 模式 | 当前宿主 | 目标开发 Agent | 当前配置模型（仅展示） | 动作`。动作固定说明“等待人工交接，不自动派遣”。普通用户输出不直接展开英文 JSON、fingerprint 或内部 reason code；需要解释时把 `reasons` 转成简体中文。
