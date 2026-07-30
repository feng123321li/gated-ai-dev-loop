# Layered Delivery

`layered-delivery` 是面向可插拔 Loop 的递归交付 Graph 调度器。

当前版本：**0.24.0**

它负责：

- 用 `GROUP` / `TASK` 递归组织多项目、多模块交付；
- 直接兄弟工作项之间的依赖与并行；
- 每个 GROUP 的确定性 Join 和独立 Review；
- Delivery 最终 Review 与用户确认；
- 多项目、多模块的精确资源声明与互斥；
- claim、heartbeat、lease 和失联恢复；
- 仅针对基础设施故障的预算内自动重试；
- 当前主机终端 Agent 与配置模型的动态发现；
- 为每个 TASK、GROUP Review 和 Delivery Review 提供带原因的非绑定 Agent + Model 建议；
- SQLite 状态、哈希事件链和可重建投影。

它不负责：

- 约定 implementation plan 或 `developmentPlan`；
- 解释文件 `scope` 或授权具体文件修改；
- 内置测试、Gate、Gate→development 修正循环；
- 规定开发 Skill、Gate Skill 或 Skill lifecycle evidence；
- 根据建议自动启动外部 Agent CLI、切换模型或派遣 Loop；
- 解析各 Loop 的 payload/result 内容。

这些实现细节都属于对应 TASK 或 Review Loop。不同节点可以使用不同的 Loop 和 Skill。用户在需求阶段给出的 Skill 只作为共享的运行时优先提示，不会预先绑定到某个工作项或阶段。

Agent/模型建议同样晚绑定：Frozen Graph 只保存工作项、依赖、资源和 Loop，不保存某台主机的 Codex、Claude Code、Cursor、OpenCode 或模型配置。`available_agents` 每次读取当前主机状态，`recommend_executors` 为已准备或冻结 Graph 的所有 TASK/Review 返回建议、备选、置信度与原因；两者都不会启动、切换或派遣。

## 运行模型

```text
Delivery（顶层交付需求与验收边界，不是 work item kind）
├─ id / title / summary / reviewLoop
├─ root wrapper: schemaVersion / skillHints
│  └─ definition: GROUP 或 TASK
│     ├─ TASK → TASK_LOOP
│     └─ GROUP
│        ├─ GROUP 或 TASK
│        ├─ GROUP_JOIN
│        └─ GROUP_REVIEW_LOOP
└─ 根工作项终态
   → DELIVERY_REVIEW_LOOP
   → USER_CONFIRMATION
```

工作项类型只有 `GROUP` 和 `TASK`。`TASK` 是唯一执行叶子；`GROUP` 可以混合包含直接子 GROUP 和 TASK。每个 GROUP 等待所有直接子节点终态，完成 `GROUP_JOIN` 后执行自己的 `GROUP_REVIEW_LOOP`，审查通过后才成为父 GROUP 可消费的成功终态。根工作项成功后，再进入 Delivery 级最终审查和一次用户确认。

外层依赖图保持 DAG；Loop 内部可以拥有自己的开发、测试、Gate 和修正循环。这样外层 Graph 只处理“何时调度哪个独立工作单元”，内部 Loop 处理“这个工作单元怎样完成”。控制器不再要求固定的 Delivery / Capability / Task 三层结构。

## Schema v3

Hierarchy 最外层只保留 Delivery 交付信息和递归根节点。schema 版本与共享 Skill Hint 属于根包装节点，嵌套节点只保留自己的 definition、Review 和 children：

```json
{
  "delivery": {
    "id": "d-order",
    "title": "订单交付",
    "summary": "完成订单服务与文档并通过最终审查",
    "reviewLoop": {
      "ref": "delivery/independent-review-loop@1",
      "payload": {
        "goal": "独立审查完整订单交付"
      },
      "resourceClaims": []
    }
  },
  "root": {
    "schemaVersion": 3,
    "skillHints": [
      {
        "name": "springboot-tdd",
        "purpose": "实际 Loop 处理 Spring Boot 开发时优先采用 TDD"
      }
    ],
    "definition": {
      "schemaVersion": 3,
      "id": "g-order",
      "kind": "GROUP",
      "parentId": null,
      "title": "订单工作组",
      "summary": "汇合并审查服务与文档结果",
      "decomposition": {
        "dependsOn": []
      },
      "children": [
        {
          "id": "t-service",
          "kind": "TASK",
          "title": "订单服务"
        },
        {
          "id": "t-docs",
          "kind": "TASK",
          "title": "订单文档"
        }
      ]
    },
    "reviewLoop": {
      "ref": "group/independent-review-loop@1",
      "payload": {
        "goal": "审查订单工作组边界"
      },
      "resourceClaims": []
    },
    "children": [
      {
        "definition": {
          "schemaVersion": 3,
          "id": "t-service",
          "kind": "TASK",
          "parentId": "g-order",
          "title": "订单服务",
          "summary": "实现并验证订单服务",
          "execution": {
            "dependsOn": [],
            "loop": {
              "ref": "project/java-service-loop@1",
              "payload": {
                "goal": "实现订单服务并完成内部验证"
              },
              "resourceClaims": [
                "project:erp/module:order"
              ]
            }
          }
        },
        "reviewLoop": null,
        "children": []
      },
      {
        "definition": {
          "schemaVersion": 3,
          "id": "t-docs",
          "kind": "TASK",
          "parentId": "g-order",
          "title": "订单文档",
          "summary": "更新订单文档",
          "execution": {
            "dependsOn": [
              "t-service"
            ],
            "loop": {
              "ref": "project/docs-loop@1",
              "payload": {
                "goal": "根据已完成的订单服务更新文档"
              },
              "resourceClaims": [
                "project:docs/module:order"
              ]
            }
          }
        },
        "reviewLoop": null,
        "children": []
      }
    ]
  }
}
```

`root.skillHints` 是建议性的晚绑定输入。每个 TASK、GROUP Review 或 Delivery Review Loop 启动后，先根据真实任务和宿主可用 Skill 判断哪些提示适用，再优先原生触发；它可以跳过不适用的提示，也可以使用其他 Skill。调度器不分配 Skill、不查询 catalog、不校验 Skill lifecycle，也不因提示未使用而判定失败。

Task 的调度定义只保留：

```json
{
  "schemaVersion": 3,
  "id": "t-order",
  "kind": "TASK",
  "parentId": "g-order",
  "title": "交付订单能力",
  "summary": "运行一个独立订单 Task Loop",
  "execution": {
    "dependsOn": [],
    "loop": {
      "ref": "project/java-service-loop@1",
      "payload": {
        "goal": "实现订单能力并完成内部验证"
      },
      "resourceClaims": [
        "project:erp/module:order"
      ]
    }
  }
}
```

`payload` 和 Loop 返回的 `result` 对调度器不透明。`resourceClaims` 是精确排他锁键，不是路径 glob，也不是文件写授权。

`dependsOn` 只能引用直接兄弟 GROUP/TASK。依赖 TASK 时等待该 Task Loop；依赖 GROUP 时等待整个 GROUP 的 Join 和 Review 成功，然后才放行依赖方子树的入口节点。

`loop_context` 同时返回直接 `predecessors` 和传递闭包中的 `upstreamLoopResults`。GROUP Join 不解释或聚合业务 result；逐层 GROUP Review 和 Delivery Review 仍能读取上游 Loop 的不透明结果。

## 标准 Loop 结果

```json
{
  "status": "SUCCEEDED",
  "summary": "内部开发、测试与 Gate 已完成",
  "result": {
    "evidence": "由 Loop 自己定义"
  }
}
```

支持四个终态：

- `SUCCEEDED`
- `BLOCKED`
- `REPLAN_REQUIRED`
- `CANCELLED`

只有 `RETRYABLE_INFRA` 和 `WORKER_LOST` 会触发外层自动重试。payload 提供目标、明确约束和已知验收点，不是完整实现规约；Loop 还要结合真实代码、契约和数据链路推导必要条件。冻结 Graph 只固定外层目标、依赖、资源声明和拓扑，不冻结 Loop 内部实现计划。当前目标内可修复的实现、测试、数据完整性、边界或 Review finding，必须由当前 TASK/Review Loop 调整方案、修正并重新验证，不进入外层事件。`BLOCKED` 仅表示当前 scope 和权限内没有继续路径，并要求显式 failure class；`REPLAN_REQUIRED` 仅用于必须改变冻结 Graph 契约的情况。

## MCP 流程

```text
workspace_status
→ available_agents（当前主机只读发现）
→ hierarchy_contract
→ prepare_hierarchy
→ recommend_executors（逐 TASK/Review 建议与原因；不派遣）
→ 用户选择：自动执行 / 手动交接（也可直接回复修改意见，不冻结）
→ freeze_hierarchy（自动或手动选择即为唯一一次冻结确认）
→ graph_frontier
→ 独立 Agent：loop_context / dispatch_loop / heartbeat_loop
→ 租约有效且上下文压力：pause_loop / 新 Agent resume_loop / 重新 dispatch
→ 租约过期：advance_graph 回收旧 attempt
→ record_loop_result
→ GROUP Review / Delivery Review
→ record_user_confirmation
```

当前 Plugin 注册 19 个工具：17 个既有外层调度工具，加上只读的 `available_agents` 与 `recommend_executors`。每次 Graph Controller operation 都绑定一个已校验的项目协调根；现代 MCP 从请求上下文取得，旧 MCP 从初始化式连接绑定取得。多仓库或多服务目标通过 Loop ref/payload 与资源声明表达。

## Agent 与模型建议

内置发现适配器覆盖 Codex、Claude Code、Cursor、OpenCode、Aider、Gemini CLI、Grok CLI、GLM CLI、DeepSeek CLI 和 Qwen CLI。只有对应终端命令真实存在时才返回 Agent；不把产品或模型名称伪装成可用执行者。Codex 和 Claude Code 会读取非敏感的当前模型字段，因此 CC-Switch 把 Claude Code 改为 GLM、DeepSeek 或其他模型后，下一次调用即可看到新值。

未知终端可通过用户本地 Agent Profile 扩展。设置 `LAYERED_DELIVERY_AGENT_PROFILES` 指向 JSON 文件，或使用平台用户配置目录下的 `layered-delivery/agent-profiles.json`；Profile 可定义任意安全 ID、裸命令名、模型名、能力和优先级。Plugin 不创建该文件，也不读取或返回 Token、Base URL 与认证字段。

推荐器只消费 Graph 节点角色和发现元数据，不解释 `loop.payload`。TASK 匹配开发能力；GROUP/Delivery Review 优先选择不同于上游开发建议的 Agent。只有一个合格 Agent 时，Review 仍展示可用组合，但明确标记异构 Agent 独立性未满足。所有结果固定为 `binding=ADVISORY`、`dispatchAllowed=false`，不进入 schema v3、Frozen Graph、SQLite、事件链、claim 或 owner。

## Controller / Adapter 架构

调度核心是共享的 Python Controller，协议和宿主差异不会进入 Graph、领域模型或 SQLite：

```text
Codex Plugin ──┐
               ├─ MCP Adapter
Claude Plugin ─┘  ├─ MCP 2026-07-28（优先）
                  └─ MCP 2025-11-25（Claude Code / 旧客户端兼容）
                         ↓
             Shared Python Controller
                         ↓
       Planning / Graph Runtime / Repository
                         ↓
                  SQLite + 事件链
```

[MCP `2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28) 的本地 Tools-over-stdio profile 使用无会话请求：客户端可先调用 `server/discover`，后续每个请求都在 `_meta` 携带协议版本和客户端能力；所有成功结果包含 `resultType`，工具目录包含缓存提示。Plugin 把 `2026-07-28` 放在支持版本首位。

Claude Code 和旧 Codex 仍可走 `2025-11-25` 的 `initialize → notifications/initialized`。Adapter 只维护 `2026-07-28` 与 `2025-11-25` 双版本，不会把新版无会话语义伪装成旧版会话。新版 Tasks 属于可选扩展；当前外层调度已使用显式 `root_id`、`node_id` 和 `operation_id` 保存长任务状态，因此本版本不声明 Tasks 扩展。

`freeze_hierarchy` 对模型只暴露 `execution_mode=active|manual`，不暴露内部 `confirmed`。冻结前只展示“自动执行 / 手动交接”两个确认选项；自动和手动都表示完整授权并确认开发，区别只在冻结后由当前会话继续调度还是生成交接。需要调整时，用户直接回复修改意见，当前方案不冻结；只有需求实际变化才重新 prepare，单纯询问或其他未改变需求的回复保留当前 `PREPARED` 结果。冻结工具在宿主权限层统一走自动批准，MCP 适配器在控制器边界内注入 Python `True`，不得再为同一次冻结追加通用 Yes/No 或其他弹窗。

总调度上下文只消费 frontier。每个 TASK、GROUP Review 和 Delivery Review Loop 默认路由到独立接收上下文；宿主支持原生 Agent 时优先自动派遣。Review 的独立性用于独立发现与复核，不阻止它在同一 Loop 内自行修正或派遣内部修正上下文。未 claim 且没有 Agent 容量时只生成人工交接，不提前 claim；已 claim、租约有效且出现上下文压力或高轮次 Hook 摩擦时，使用 `pause_loop → 新上下文 resume_loop → 重新 dispatch`，不提交业务 outcome；租约过期时由 `advance_graph` 回收旧 attempt，禁止调用 `pause_loop`。接收方始终继续同一冻结 Graph。

`recommend_executors` 不改变上述执行机制。即使建议显示另一个外部 Agent/模型，v0.22.0 也不会据此调用其 CLI、切换当前宿主模型或改变实际接收上下文；它只提供人类可审查的运行时建议。

## 主要投影

递归 GROUP/TASK 同时决定人类投影的物理父子目录；调度权威仍只在 SQLite。每个 Delivery 使用稳定的 `delivery.id` 作为投影目录命名空间，GROUP 可多层、平行或完全不存在：

```text
.layered-delivery/
├── scheduler.db
├── overview.md
├── d-order/
│   ├── overview.md
│   ├── baseline.md
│   ├── progress.md
│   ├── acceptance.md
│   └── work-items/
│       └── g-order/
│           ├── baseline.md
│           ├── progress.md
│           ├── acceptance.md
│           └── children/
│               ├── t-api/
│               │   ├── baseline.md
│               │   ├── progress.md
│               │   ├── acceptance.md
│               │   └── interfaces.md  # 仅当本 TASK 声明接口
│               └── g-core/
│                   ├── baseline.md
│                   ├── progress.md
│                   ├── acceptance.md
│                   └── children/
│                       └── t-core/
│                           ├── baseline.md
│                           ├── progress.md
│                           └── acceptance.md
└── d-another-delivery/
    ├── overview.md
    ├── baseline.md
    ├── progress.md
    └── acceptance.md
```

| 文件 | 内容 |
|---|---|
| `.layered-delivery/scheduler.db` | SQLite 机器权威 |
| `.layered-delivery/overview.md` | 全部 Delivery 的标识、标题、状态、更新时间和详情入口 |
| `.layered-delivery/<delivery-id>/overview.md` | 本 Delivery 的 TASK 进度、GROUP 数量、状态与投影导航 |
| `.layered-delivery/<delivery-id>/baseline.md` | 需求、GROUP/TASK 层级、依赖与 Review 输入基线 |
| `.layered-delivery/<delivery-id>/progress.md` | TASK、GROUP 与 Delivery Review 的执行进展 |
| `.layered-delivery/<delivery-id>/acceptance.md` | 已知验收输入、Loop 结果、证据与最终用户确认 |
| `.layered-delivery/<delivery-id>/work-items/<root-id>/children/.../<node-id>/baseline.md` | 单个 GROUP/TASK 的冻结需求与 Loop 输入基线 |
| `.layered-delivery/<delivery-id>/work-items/<root-id>/children/.../<node-id>/progress.md` | 单个 GROUP/TASK 的执行、汇合或 Review 进展 |
| `.layered-delivery/<delivery-id>/work-items/<root-id>/children/.../<node-id>/acceptance.md` | 单个 GROUP/TASK 的验收输入、结果与证据 |
| `.layered-delivery/<delivery-id>/work-items/<root-id>/children/.../<task-id>/interfaces.md` | 接口型 TASK 按需生成的修改前后完整契约 |

目录使用不可变的 Delivery ID 和节点 ID，不使用可修改的标题。同一工作区可以保留多个 Delivery 需求目录；`work-items/<root-id>/children/...` 只镜像父子关系，兄弟 `dependsOn` 仍由 Graph 控制执行顺序。根为 TASK 时直接生成 `work-items/<task-id>/`，不创建虚拟 GROUP。

根级 `overview.md` 只汇总全部 Delivery 的标识、标题、中文状态、最近更新时间和详情链接。每个 Delivery 自己的 `overview.md` 展示该交付的 TASK 完成度、GROUP 数量、状态与导航；需求、执行和验收分别进入顶层 `baseline.md`、`progress.md` 与 `acceptance.md`。Delivery baseline 是整棵基线树的入口，链接所有 GROUP/TASK 节点投影但不复制其 Loop 输入；GROUP baseline 保存自身需求与 Review 输入并链接直接子节点；TASK baseline 保存执行叶子的冻结输入。progress 的节点状态以及 acceptance 的状态摘要、子节点结果和 P0/P1/P2 问题使用表格，长输入与证据保留结构化列表。

Loop 输入是冻结后交给对应 TASK 或 Review 执行上下文的 `loop.ref`、不透明 `payload` 与精确 `resourceClaims`。它属于执行前确认的契约，因此只展开在对应节点 baseline；运行状态、attempt 和结果分别进入 progress 与 acceptance。

人类 Markdown 不展示 JSON 代码块或原始状态枚举。控制器只对不透明 payload 做确定性的结构展开：固定栏目、状态、说明和空值提示保持中文；HTTP 方法、URL、Dubbo 服务名、gRPC 标识、字段名与类型名等技术标识保留原值。需求新增、修改或删除接口时，负责接口的 TASK 在 `payload.interfaces` 显式声明 `changeType`、协议、名称、简介以及完整 `before` / `after` 快照；`protocol` 是开放字符串，HTTP、Dubbo、gRPC、GraphQL、消息等只是示例。适用快照包含完整入参与出参，并使用通用 `identifier` 或协议专用调用字段定位接口；HTTP 可使用 `method + path`，Dubbo 可使用 `service + method`。控制器只在该 TASK 目录生成 `interfaces.md`，TASK baseline 与 Delivery baseline 分别链接它；无接口声明时不生成。Agent 可以从真实代码、OpenAPI、Controller/DTO、IDL 或服务定义提取候选 before 并校验 after，但控制器不动态扫描代码或隐式推算契约。

根总览、Delivery 投影和整棵 `work-items/` 节点投影树使用控制器内置模板，内部修订号不写入人类正文。控制器在状态提交后重新读取 SQLite，原子更新根总览和主文件，并整体替换 `work-items/`，确保 GROUP/TASK 删除、改名或接口声明移除后不遗留旧文件；升级后也会清理旧 `hierarchy.json`、`graph.json`、`state.json` 和 `task-baselines/`。`workspace_status` 会从 SQLite 为早期 schema v3 Delivery 补建当前适用的中文投影树，不迁移或修改 hierarchy、Graph、事件链和运行状态；旧 hierarchy 没有接口声明时不会从代码反推接口契约。所有标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`，机器权威时间仍保持 ISO 8601 UTC。Agent 通过合法 MCP 输入提交的 hierarchy、summary 和 payload 会按模板成为投影中的领域数据；模板结构、固定相对文件名、序列化和落盘完全由控制器负责。Agent 只通过已注册的 MCP 工具读取调度状态，不直连 `scheduler.db`，也不自行创建、修补或重写投影。投影用于人类评审与进度掌控，不反向成为机器权威。

`prepare_hierarchy` 阶段生成根总览、四份 Delivery 人类主投影，以及所有 GROUP/TASK 的 baseline、progress 和 acceptance；接口型 TASK 再生成自己的 `interfaces.md`。冻结后的运行状态继续从 SQLite 确定性刷新 Markdown，不生成机器 JSON 副本。

不再生成 `development-plan.md`、Task Gate 报告、Skill activation 记录或文件 scope 授权投影。

## 支持的宿主

仓库构建一个双宿主 Plugin payload：

- Codex：`.codex-plugin/plugin.json`
- Claude Code：`.claude-plugin/plugin.json`、`.mcp.json` 和敏感操作 Hook

安装或更新 Plugin 后应新建 Agent 会话，使宿主重新加载 Skill、MCP Server 和工具权限。

## 开发验证

```text
python -m unittest
python -m compileall -q src tests
python scripts/build_skill.py
python -X utf8 <skill-creator>/scripts/quick_validate.py skills/layered-delivery
python -X utf8 <plugin-creator>/scripts/validate_plugin.py plugins/layered-delivery
git diff --check
```

项目使用 Python 3.10+ 和标准库，不提供 CLI 入口，也不维护旧 schema 兼容层。MCP wire protocol 保留双时代兼容不等于恢复旧业务 schema；Controller 始终只接受当前完整 schema v3。

0.18.0 是 schema v3 的破坏性语义替换：固定 Delivery / Capability / Task 层级被顶层 Delivery 加递归 GROUP/TASK 模型取代。0.17.x 的 `scheduler.db` hierarchy 与更早的 `governance.sqlite3` 均不迁移；请先归档旧 `.layered-delivery` 运行包，再创建新的递归交付 Graph。

## 文档

- [Graph Engineering 架构](docs/graph-engineering-upgrade.md)
- [项目实现结构](docs/project-engineering.md)
- [Skill 调度说明](skills/layered-delivery/SKILL.md)
