---
name: hierarchical-delivery-governance
description: "分层治理可独立交付的软件工作单元。用于把完整项目、大型模块、子系统或跨服务需求组织为 Delivery→Capability→Task，为每一级维护独立 baseline，按依赖调度可独立上下文的 Task，支持多人协作、分级进度、门禁重试和最终交付确认；也用于继续、恢复、修订或审计已有分层交付工作。"
---

# Hierarchical Delivery Governance

把可独立交付的软件工作治理为稳定的 `Delivery → Capability → Task` 层级。顶层 `Delivery` 是治理根，可以代表完整项目、大型模块、子系统或跨服务需求，不表示必须覆盖整个代码仓库或完整产品。Delivery 与 Capability 是协调单元，Task 是唯一可执行叶子。每一级有自己的 baseline、状态、门禁和进度，任何 Task 都能从磁盘材料恢复，不依赖创建它的对话。

## 首先判断是否在维护本 Skill

如果当前仓库 `package.json.name` 是 `hierarchical-delivery-governance`，默认进入 `SELF_HOSTING_MAINTENANCE`：

- 不创建 `.hierarchical-delivery-governance/**`；
- 不调用 `start`、`prepare-item`、`freeze-item`；
- 直接在仓库内按测试优先方式维护 Skill、CLI、文档和测试；
- 只有用户明确要求“dogfood/演练运行任务包”时，才允许使用 `--dogfood` 创建运行包。

命中该分支后立即短路普通 registry 恢复和工作项创建流程。dogfood 时重新进入标准流程；所有会写控制面的层级命令都必须显式带 `--dogfood`，并且仍要分别满足 ID/baseline 持久化批准和冻结/修订确认。三个条件是累积条件，不可互相替代。

“升级 Skill”“优化流程”“Delivery”“Capability”“governance”以及任务名称都不是 dogfood 授权，也不会触发创建或冻结任务包。规范 Skill 名是 `hierarchical-delivery-governance`，不追加 `v2`。

## 统一概念

只使用以下三个工作项种类：

| Kind | Authority | 作用 | 子级 |
| --- | --- | --- | --- |
| `DELIVERY` | `COORDINATION` | 顶层交付总览、跨能力约束、交付级验收 | Capability |
| `CAPABILITY` | `COORDINATION` | 完整业务能力、依赖、集成门禁、持续拆分 | Task |
| `TASK` | `EXECUTION` | 可独立开发、测试和交付的最小叶子 | 无 |

`DELIVERY` 是层级中的稳定类型名，不是工作范围大小的硬编码判断。只要某项工作有独立交付目标、需要拆成多个 Capability，并需要顶层聚合验收，就可以作为 Delivery；不要为了大型模块再增加 Module/Initiative 等平行根类型。

`Workstream`、`M-NNN/W-NNN/T-NNN` 不再是新流程实体。`Micro` 仅表示小型 Task 的执行特征，不是第四层。不要再让“Task”同时表示工作规模、任务包和计划行。

门禁等级 `None/Light/Full` 与工作项层级正交：

- `None`：只读回答，不创建工作项；
- `Light`：低风险小型 Task，可用精简 baseline，但仍是 Task；
- `Full`：完整 baseline 和全部适用门禁；Delivery、Capability 默认 Full。

变更类型独立记录为 `Feature/Bugfix/Refactor/Migration/Maintenance/Docs/Test`，不改变层级。

推荐层级前必须先形成一份人可读的“层级事实卡”，记录交付对象与验收边界、计划 Capability 及其聚合验收、依赖/集成波次、为什么不是更小一级和仍缺失的事实。文件、接口、服务数量或 Full 风险信号都不能单独推出 Delivery；事实不足时只保留草案并等待确认，不准备工作项包、不冻结 baseline。详细判定见 [routing-profiles.md](references/routing-profiles.md)。

## 持久化结构

新流程只写：

```text
.hierarchical-delivery-governance/
├── work-item-registry.json
├── workspace-overview.md
└── work-items/
    └── <work-item-id>/
        ├── baseline.json
        ├── baseline.md
        ├── work-item.json
        ├── state.json
        ├── overview.md
        ├── progress.md
        ├── children.json          # Delivery / Capability
        ├── execution.json         # Task
        ├── development-mode.json  # Task baseline 冻结后由显式选择生成
        ├── context-manifest.json  # Task 调度时生成
        └── development-handoff.md # Task 调度时生成
```

`work-item-registry.json` 是机器权威；Markdown 是可重建投影。新 Skill 不读取、迁移或回写其他历史控制目录。

详细契约见 [task-registry.md](references/task-registry.md)、[baselines.md](references/baselines.md) 与 [tracking.md](references/tracking.md)。

## 每次开发消息的入口

1. 先执行自举维护判断。
2. 只读检查 `.hierarchical-delivery-governance/work-item-registry.json`；存在时按显式 ID/路径、有效焦点、唯一活动候选的顺序恢复。
3. 先起草层级事实卡，再判断消息是继续当前工作、修订当前 baseline、追加子工作项，还是新的顶层交付单元（Delivery）。
4. 只有用户明确批准工作项 ID 和 baseline 持久化后，才准备对应包；只有 `--confirmed` 才冻结。
5. 不从“升级、优化、项目、任务、治理”等词推导创建或冻结授权。

第 2–5 步只在未命中自举短路，或用户已经明确 dogfood 时执行。“明确批准”必须是用户直接确认具体 ID、baseline 指纹/内容和将执行的动作；Agent 不得把用户提供的标题、建议任务名或自己添加的 `--confirmed` 当作确认。

多个候选无法确定时请求用户选择。不得按目录时间、名称相似度或自然语言猜测当前焦点。

## 从顶层交付单元到独立 Task

### 1. Delivery baseline

先生成顶层交付总览，至少包含目标、范围、非目标、R/A 追踪、交付级测试、风险、决策，以及计划的 Capability 子契约。总览范围可以是完整项目，也可以是可独立交付的大型模块、子系统或跨服务需求。Delivery baseline 只授权协调，不授权写业务代码。

### 2. Capability baseline

每个 Capability 从 Delivery 的已冻结子契约派生，范围只能收窄。它必须定义能力目标、R/A、集成测试、计划 Task，以及 `decomposition.status=OPEN|SEALED` 和可选的同 Delivery Capability `dependsOn`。Capability 依赖必须无环；提供方未 VERIFIED 时，消费方下所有 Task 都不 READY。Capability 可持续追加 Task，但必须通过显式 baseline 修订：

- 提交当前 `expectedBaselineFingerprint`；
- 用户显式确认；
- 纯追加且不改变现有子契约时，可在无关后代 Task 有活动 claim 时修订；任何会使活动 Task 父链 stale 的修订必须拒绝；
- 不得删除既有子契约；
- 新增兄弟 Task 不使未变化 Task 失效；
- 修改父稳定契约或某个 Task 的子契约，只使受影响的后代 baseline stale。

### 3. Task baseline

Task 是叶子，必须包含：目标、精确写入范围、非目标、R/A、依赖、输入、输出、安全 argv 测试和完成定义。Task 只能引用同一 Capability 中已计划的兄弟依赖，范围不能超出 Capability。

### 4. 开发方式机械门禁

Task baseline 冻结后必须持久化为 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`，此时不得计算为 READY、生成上下文或认领。宿主只展示 `active` 与 `manual` 两种方式并等待用户明确选择；baseline 确认、普通“确认”或宿主推断都不能代替开发方式确认。

只有用户明确选择后，宿主才执行绑定当前 Task baseline 指纹的命令：

```text
hdg select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
```

命令成功后写入 `development-mode.json`，并把 Task 状态推进为 `FROZEN`。该记录必须与 registry 中的快照、Task ID 和当前 baseline 指纹完全一致；Task baseline 修订会删除旧记录并回到等待状态。`hdg` 不可用或命令失败时保持阻断，不能用“等价流程”直接开始开发。

### 5. 独立上下文

验证开发方式记录后，调度 Task 前才能生成 `context-manifest.json` 和 `development-handoff.md`。上下文只包含：

- Task baseline 指纹与授权范围；
- Delivery/Capability 中与该 Task 相关的父契约快照；
- 依赖 Task 的状态、输出和证据；
- R/A、测试 argv、输入输出及执行规则。

不得继承需求分析对话、其他 Task 的对话或开发 Agent 的隐式记忆。上下文不完整、父契约漂移或依赖未验证时返回 `BLOCKED`。

## READY、认领与多人协作

`READY` 是每次调度时计算的派生谓词，不是持久状态。Task 只有同时满足以下条件才是 `READY`：

- 自己及父链 baseline 已冻结且指纹有效；
- 已有与当前 Task baseline 绑定且通过校验的 `development-mode.json`；
- 所有 `dependsOn` Task 为 `VERIFIED`；
- 所属 Capability 的所有 `dependsOn` Capability 为 `VERIFIED`；
- 没有活动 claim；
- 与其他活动 claim 的写入范围不重叠。

调度前写入 `owner + operationId` claim。重复认领、陈旧父契约或范围冲突必须拒绝。开发 Agent 只能实现一个冻结 Task，不得改 baseline、注册表、`.git/**` 或外部状态，不得自行报告 PASS。

scope 只接受精确相对路径或尾部 `/**` 的目录前缀，不接受中段 glob、文件通配符或负模式。冲突按前缀包含关系确定；共享 schema、迁移、生成清单或构建产物必须显式放进 scope，因此会被机械判为冲突。

`parallel` 只是多个 READY Task 的执行拓扑，不是工作项种类。每个并行 Agent 必须获得不同的独立上下文和互斥写入范围。详见 [development.md](references/development.md) 与 [parallel-development.md](references/parallel-development.md)。

## 分级进度和完成规则

每次写回都更新工作项自身状态、直接子级计数和全部后代计数；只记录精确数量，不写主观百分比。

- Task：`WAITING_FOR_DEVELOPMENT_MODE_SELECTION → FROZEN → CLAIMED → IMPLEMENTED/BLOCKED → VERIFIED`；
- Capability：拆分为 `SEALED` 且所有计划 Task 都 `VERIFIED` 后才能运行自己的集成门禁；门禁 PASS 后才 `VERIFIED`；
- Delivery：拆分为 `SEALED` 且所有计划 Capability 都 `VERIFIED` 后才能运行顶层交付门禁；门禁 PASS 后进入 `VERIFIED / WAITING_FOR_INDEPENDENT_REVIEW`，通过隔离审查或显式人工审查后等待用户确认，只有用户确认后 delivery 才是 `COMPLETED`。

父级不能仅凭子级完成自动 PASS，必须有自己的 gate evidence。任何层级门禁 `FAIL` 后保持 `BLOCKED`，必须提交当前 baseline 指纹并显式确认 `retry-item`，才能重新运行门禁。详见 [tracking.md](references/tracking.md) 和 [acceptance.md](references/acceptance.md)。

## CLI 对应关系

层级流程使用以下原生命令：

```text
hdg prepare-item --definition <json> --host-runtime <agent>
hdg freeze-item --item <id> --expected-baseline <sha256> --confirmed
hdg revise-item --definition <json> --expected-baseline <sha256> --confirmed
hdg select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
hdg ready-tasks --delivery <delivery-id>
hdg task-context --item <task-id>
hdg claim-task --item <task-id> --owner <owner> --operation <operation-id>
hdg task-result --item <task-id> --operation <operation-id> --status IMPLEMENTED --evidence <json>
hdg retry-item --item <id> --expected-baseline <sha256> --confirmed
hdg gate-item --item <id> --status PASS --evidence <json>
hdg delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS --evidence <json>
hdg delivery-item --item <delivery-id> --action USER_CONFIRMED --evidence <json>
```

`task-context` 和 `claim-task` 都会机械校验 registry 与 `development-mode.json`；未选择、文件缺失、内容被改动或 baseline 不匹配时必须拒绝。`hdg` 只暴露上述层级治理命令，不提供旧 `start/prepare/freeze` 入口。维护本仓库时，所有写命令都会拒绝写入；只有在每个写命令上显式传入 `--dogfood` 才绕过自举保护，且其他确认条件仍然有效。

## 验收与反馈

每一级 gate 可以包含该级契约所需的机械和局部语义检查；`VERIFIED` 只表示该级 gate 通过。Delivery gate 后必须由没有开发上下文的全新只读审查 Agent 验收，或记录显式人工审查接受，再取得用户确认；状态按 `WAITING_FOR_INDEPENDENT_REVIEW → WAITING_FOR_USER_CONFIRMATION → COMPLETED` 持久化。审查与用户确认必须分别提供真实、hash 匹配、结构合法且不可复用的 evidence 文件。没有隔离审查能力时生成清晰人工验收包，不伪造审查 PASS 或用户确认。P0/P1 阻断，P2 展示但不自动实现。任何修订、追加 Task、跟进 Delivery 或重新打开都必须先分类并取得明确授权。

不得自动提交、推送、合并、发布或更改外部状态。跨仓库工作只选择一个协调根保存注册表；每个 Task 明确工作区、路径和测试 cwd。详见 [multi-workspace.md](references/multi-workspace.md) 和 [post-acceptance-feedback.md](references/post-acceptance-feedback.md)。

## 按需读取参考

- 完整状态流：[workflow.md](references/workflow.md)
- 层级规划：[delivery-planning.md](references/delivery-planning.md)
- baseline 与修订：[baselines.md](references/baselines.md)
- 注册表、恢复与 legacy：[task-registry.md](references/task-registry.md)
- 事务和并发：[registry-transactions.md](references/registry-transactions.md)
- 生命周期：[registry-lifecycle.md](references/registry-lifecycle.md)
- 进度投影：[tracking.md](references/tracking.md)
- 路由：[routing-profiles.md](references/routing-profiles.md)
- 独立开发上下文：[development.md](references/development.md)
- 并行调度：[parallel-development.md](references/parallel-development.md)
- 多工作区：[multi-workspace.md](references/multi-workspace.md)
- 验收：[acceptance.md](references/acceptance.md)
- 验收后反馈：[post-acceptance-feedback.md](references/post-acceptance-feedback.md)
