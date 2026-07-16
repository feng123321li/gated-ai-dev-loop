---
name: hierarchical-delivery-governance
description: "分层治理可独立交付的软件工作单元。按最小必要深度组织为 Task、Capability→Task 或 Delivery→Capability→Task，为实际存在的每一级维护独立 baseline，按依赖调度独立上下文 Task，支持 manual/active 开发回收、分级门禁、用户可读验收报告、独立验收和最终确认；也用于继续、恢复、修订或审计已有分层交付工作。"
---

# Hierarchical Delivery Governance

把可独立交付的软件工作治理为稳定但可浅化的层级。机器权威种类仍是 `Delivery → Capability → Task`，合法根形态是独立 `Task`、`Capability → Task` 或完整 `Delivery → Capability → Task`；只创建实际承担聚合责任的层级。Delivery 可以代表完整项目、大型模块、子系统或跨服务需求，不表示必须覆盖整个代码仓库或完整产品。Delivery 与 Capability 是协调单元，Task 是唯一可执行叶子。实际存在的每一级有自己的 baseline、状态、门禁和进度，任何 Task 都能从磁盘材料恢复，不依赖创建它的对话。

## 统一概念

只使用以下三个工作项种类：

| 类型 | 权限性质 | 作用 | 子级 |
| --- | --- | --- | --- |
| 交付（`DELIVERY`） | 协调 | 多个 Capability 的交付总览、跨能力约束、交付级验收 | Capability |
| 能力（`CAPABILITY`） | 协调 | 多个 Task 的能力聚合、依赖、集成门禁、持续拆分；可作为治理根 | Task |
| 任务（`TASK`） | 执行 | 可独立开发、测试和交付的最小叶子；可作为治理根 | 无 |

`DELIVERY` 是层级中的稳定类型名，不是工作范围大小的硬编码判断。只要某项工作有独立交付目标、需要拆成多个 Capability，并需要顶层聚合验收，就可以作为 Delivery；不要为了大型模块再增加 Module/Initiative 等平行根类型。

`Micro`、`Workstream`、`M-NNN/W-NNN/T-NNN` 不进入 `kind` 枚举，但不删除其用途：Micro 描述 Task 的规模/低风险执行特征；Workstream 是可选的跨工作项规划与汇报视图；M/W/T 是可选的人类可读规划编号或别名。它们不拥有 baseline、claim 或 gate，不能取代 registry ID 和 Delivery/Capability/Task 生命周期。

门禁等级路由与工作项层级正交。`None` 是不持久化工作项的路由结果；一旦创建工作项，schema v3 的 `gateLevel` 必须是 `LIGHT` 或 `FULL`：

- `None`：只读回答，不创建工作项，因此不写入 registry；
- `LIGHT`：仅允许低风险小型 Task；baseline 字段仍完整，但说明可保持精炼，且仍执行 baseline 确认、开发方式确认和 gate；
- `FULL`：完整强度；Delivery、Capability 必须为 `FULL`。

`gateLevel` 进入 baseline/contract 指纹、registry 条目、Task 独立上下文和 Markdown 投影。缺失、未知值或协调工作项声明 `LIGHT` 都必须机械拒绝，不能只停留在路由描述中。

变更类型独立记录为 `Feature/Bugfix/Refactor/Migration/Maintenance/Docs/Test`，不改变层级。

推荐层级前必须先形成一份人可读的“层级事实卡”，记录交付对象与验收边界、所需聚合责任、可执行叶子、依赖/集成波次、为什么不是更浅一级和仍缺失的事实。文件、接口、服务数量或 Full 风险信号都不能单独推出 Delivery；事实不足时只保留草案并等待确认，不准备工作项包、不冻结 baseline。详细判定见 [routing-profiles.md](references/routing-profiles.md)。

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
        ├── development-handoff.md # Task 原子调度时生成的可直接复制提示词
        ├── acceptance-report.json # 开发结果写回后生成并持续更新的结构化验收投影
        └── acceptance-report.md   # 面向用户的验收报告
```

`work-item-registry.json` 是机器权威；Markdown 是可重建投影。`workspace-overview.md`、`overview.md` 和 `progress.md` 是面向用户与协作者的中文工作台，不是机器输入；`acceptance-report.md` 是正式用户验收报告。新 Skill 不读取、迁移或回写其他历史控制目录。

详细契约见 [task-registry.md](references/task-registry.md)、[baselines.md](references/baselines.md) 与 [tracking.md](references/tracking.md)。

## 每次开发消息的入口

1. 解析当前 Skill 的安装目录，并运行 `node <skill-root>/scripts/hdg.mjs --help` 做只读预检。内置控制器是主入口；全局 `hdg` 只是可选快捷别名，不是前置条件。
2. 只读检查 `.hierarchical-delivery-governance/work-item-registry.json`；不存在表示尚未持久化工作项，不是 CLI 缺失或 schema 错误。schema v3 直接恢复；若是本 Skill 早期生成、尚未执行或 gate 的单根 schema v2 Task，则展示保留状态、重新计算指纹、清除旧上下文以及选择 `LIGHT|FULL` 的迁移影响，取得明确确认后执行 `upgrade-registry`。其他 schema 或不满足安全前置条件的 v2 现场保持阻断，不得静默改写。
3. 先起草层级事实卡和门禁等级，再判断消息是继续/修订/追加/升层，还是新的根 Task、根 Capability 或 Delivery。
4. 展示完整 baseline 后，只请求一次批准；该批准必须同时覆盖具体 ID、baseline 内容、持久化和冻结。收到批准后调用 `approve-item --confirmed`，一次完成准备与冻结，不得再请求“确认冻结”。
5. 不从“升级、优化、项目、任务、治理”等词推导创建或冻结授权。

“明确批准”必须绑定刚刚唯一展示的具体 ID、完整 baseline 内容和“持久化并冻结”动作；当用户按该明确请求回复“批准/同意”时，应视为对这三个要素的一次确认。Agent 不得把用户提供的标题、建议任务名或自己添加的 `--confirmed` 当作确认。内置控制器缺失或预检失败表示 Skill 安装损坏，保持阻断并报告重装 Skill；不得要求用户另装全局 CLI，也不得用纯对话模拟硬门禁。

多个候选无法确定时请求用户选择。不得按目录时间、名称相似度或自然语言猜测当前焦点。

## 从最小治理根到独立 Task

### 1. 选择根层级

- 一个没有兄弟依赖、可直接验收的执行结果：根 Task；
- 多个 Task 需要共享能力契约和聚合门禁：根 Capability；
- 多个 Capability 需要跨能力约束和顶层交付门禁：Delivery。

不允许 `Delivery → Task` 或 `Capability → Capability`。根 Task 不声明 Task 依赖；根 Capability 不声明 Capability 依赖。出现这些依赖时升级到能承载兄弟关系的聚合根。

已经作为浅层根冻结后才发现真实聚合责任时，不通过改 `kind` 或伪造父级原地升级，而走受控升层：

1. 先按普通门禁起草目标父级 baseline；父级必须把当前根列为计划 child，并保持根形态；
2. 用户一次批准父级 ID、内容以及持久化并冻结后执行 `approve-item`；这一步不自动附着当前根；
3. 展示子、父两个当前 baseline 指纹和关系变化，取得明确升层确认；
4. 执行 `promote-item`，只允许根 `TASK → CAPABILITY` 或根 `CAPABILITY → DELIVERY`；保留工作项 ID、kind 和 gateLevel，并把旧/新/父 baseline 指纹写入 `promotionHistory`；
5. Task 升层会清除旧开发方式、上下文和 handoff，回到 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`；Capability 升层保留不受父契约变化影响的 Task 子契约。

源或父已执行 gate、源子树有活动 claim、父未冻结、父未计划该 child、指纹过期或未明确确认时必须拒绝。升层不自动创建或冻结父级，也不能把用户说“需要 Capability/Delivery”当成执行授权。

### 2. Delivery baseline（按需）

先生成顶层交付总览，至少包含目标、范围、非目标、R/A 追踪、交付级测试、风险、决策，以及计划的 Capability 子契约。总览范围可以是完整项目，也可以是可独立交付的大型模块、子系统或跨服务需求。Delivery baseline 只授权协调，不授权写业务代码。

### 3. Capability baseline（按需）

Capability 可以是治理根，也可以从 Delivery 的已冻结子契约派生；有父级时范围只能收窄。它必须定义能力目标、R/A、集成测试、计划 Task，以及 `decomposition.status=OPEN|SEALED`。只有 Delivery 下的 Capability 才能声明同 Delivery Capability `dependsOn`；提供方未 VERIFIED 时，消费方下所有 Task 都不 READY。Capability 可持续追加 Task，但必须通过显式 baseline 修订：

- 提交当前 `expectedBaselineFingerprint`；
- 用户显式确认；
- 纯追加且不改变现有子契约时，可在无关后代 Task 有活动 claim 时修订；任何会使活动 Task 父链 stale 的修订必须拒绝；
- 不得删除既有子契约；
- 新增兄弟 Task 不使未变化 Task 失效；
- 修改父稳定契约或某个 Task 的子契约，只使受影响的后代 baseline stale。

### 4. Task baseline

Task 是叶子，可以作为治理根，也可以从 Capability 的已冻结子契约派生。它必须包含：目标、精确写入范围、非目标、R/A、依赖、输入、输出、安全 argv 测试和完成定义。有父级时，Task 只能引用同一 Capability 中已计划的兄弟依赖，范围不能超出 Capability；根 Task 的 `dependsOn` 必须为空。

### 5. 开发方式机械门禁

Task baseline 冻结后必须持久化为 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`，此时不得计算为 READY、生成上下文或认领。宿主只展示 `active` 与 `manual` 两种方式并等待用户明确选择；baseline 确认、普通“确认”或宿主推断都不能代替开发方式确认。

只有用户明确选择后，宿主才执行绑定当前 Task baseline 指纹的命令：

```text
node <skill-root>/scripts/hdg.mjs select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
```

命令成功后写入 `development-mode.json`，并把 Task 状态推进为 `FROZEN`。该记录必须与 registry 中的快照、Task ID 和当前 baseline 指纹完全一致；Task baseline 修订会删除旧记录并回到等待状态。Skill 内置控制器不可用或命令失败时保持阻断，不能用“等价流程”直接开始开发。

### 6. 独立上下文

验证开发方式记录后，才能生成 `context-manifest.json` 和 `development-handoff.md`。正常流程由 `dispatch-task --json` 返回与文件完全一致、绑定 operationId 的 `handoffPrompt`；`task-context --json` 只生成明确标记“尚未认领、不得开始开发”的诊断预览。上下文只包含：

- Task baseline 指纹与授权范围；
- 实际存在的 Capability/Delivery 父契约快照；根 Task 为空数组；
- 依赖 Task 的状态、输出和证据；
- R/A、测试 argv、输入输出及执行规则。

不得继承需求分析对话、其他 Task 的对话或开发 Agent 的隐式记忆。上下文不完整、父契约漂移或依赖未验证时返回 `BLOCKED`。

选择 `manual` 后，宿主必须立即生成唯一 operationId，运行 `dispatch-task --owner manual-user --operation <operation-id> --json` 原子认领并生成上下文，并在同一回复中原样输出 `handoffPrompt` 供复制；文件链接只能作为补充。提示词必须携带 operationId、结果返回契约，并明确 `IMPLEMENTED` 不是完成状态，开发结果写回后仍需门禁、独立验收、验收报告和用户确认。不得先输出一个没有 operationId 的预览提示词再让用户开始开发。

## READY、认领与多人协作

`READY` 是每次调度时计算的派生谓词，不是持久状态。Task 只有同时满足以下条件才是 `READY`：

- 自己及实际存在的父链 baseline 已冻结且指纹有效；
- 已有与当前 Task baseline 绑定且通过校验的 `development-mode.json`；
- 所有 `dependsOn` Task 为 `VERIFIED`；
- 有所属 Capability 时，其所有 `dependsOn` Capability 为 `VERIFIED`；
- 没有活动 claim；
- 与其他活动 claim 的写入范围不重叠。

正常调度使用 `dispatch-task`，在同一控制流程中写入 `owner + operationId` claim 并生成绑定该 operation 的上下文和 handoff；`task-context`、`claim-task` 仅用于只读诊断或恢复。重复认领、陈旧父契约或范围冲突必须拒绝。开发 Agent 只能实现一个冻结 Task，不得改 baseline、注册表、`.git/**` 或外部状态，不得自行报告 PASS。

scope 只接受精确相对路径或尾部 `/**` 的目录前缀，不接受中段 glob、文件通配符或负模式。冲突按前缀包含关系确定；共享 schema、迁移、生成清单或构建产物必须显式放进 scope，因此会被机械判为冲突。

`parallel` 只是多个 READY Task 的执行拓扑，不是工作项种类。每个并行 Agent 必须获得不同的独立上下文和互斥写入范围。详见 [development.md](references/development.md) 与 [parallel-development.md](references/parallel-development.md)。

## 分级进度和完成规则

每次写回都更新工作项自身状态、直接子级计数和全部后代计数；只记录精确数量，不写主观百分比。

- Task：`WAITING_FOR_DEVELOPMENT_MODE_SELECTION → FROZEN → CLAIMED → IMPLEMENTED/BLOCKED → VERIFIED`；
- Capability：拆分为 `SEALED` 且所有计划 Task 都 `VERIFIED` 后才能运行自己的集成门禁；门禁 PASS 后才 `VERIFIED`；
- Delivery：拆分为 `SEALED` 且所有计划 Capability 都 `VERIFIED` 后才能运行顶层交付门禁；门禁 PASS 后根工作项进入最终验收。

每个实际治理根都承担最终交付责任。根 Task、根 Capability、Delivery 在自身 gate PASS 后先达到 `VERIFIED / WAITING_FOR_INDEPENDENT_REVIEW`，随后通过隔离审查或显式人工审查进入 `WAITING_FOR_USER_CONFIRMATION`，只有用户确认后才是 `COMPLETED`。子工作项只生成该级验收报告并达到 `VERIFIED`，不重复请求用户确认。不要为了获得最终验收而补空父级。

父级不能仅凭子级完成自动 PASS，必须有自己的 gate evidence。任何层级门禁 `FAIL` 后保持 `BLOCKED`，必须提交当前 baseline 指纹并显式确认 `retry-item`，才能重新运行门禁。详见 [tracking.md](references/tracking.md) 和 [acceptance.md](references/acceptance.md)。

## CLI 对应关系

Skill 自带单文件机械控制器。宿主从当前 `SKILL.md` 所在目录解析 `<skill-root>`，以 `node <skill-root>/scripts/hdg.mjs` 调用；全局 `hdg` 若存在，只能作为同版本控制器的可选快捷别名。

层级流程使用以下命令：

```text
node <skill-root>/scripts/hdg.mjs approve-item --definition <json> --host-runtime <agent> --confirmed
node <skill-root>/scripts/hdg.mjs prepare-item --definition <json> --host-runtime <agent> # 仅恢复/诊断低级入口
node <skill-root>/scripts/hdg.mjs freeze-item --item <id> --expected-baseline <sha256> --confirmed # 仅恢复/诊断低级入口
node <skill-root>/scripts/hdg.mjs revise-item --definition <json> --expected-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs promote-item --item <root-id> --parent <frozen-parent-id> --expected-baseline <sha256> --expected-parent-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs select-development-mode --item <task-id> --development-mode active|manual --expected-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs ready-tasks --item <root-or-subtree-id>
node <skill-root>/scripts/hdg.mjs task-context --item <task-id>
node <skill-root>/scripts/hdg.mjs dispatch-task --item <task-id> --owner <owner> --operation <operation-id>
node <skill-root>/scripts/hdg.mjs claim-task --item <task-id> --owner <owner> --operation <operation-id>
node <skill-root>/scripts/hdg.mjs task-result --item <task-id> --operation <operation-id> --status IMPLEMENTED --evidence <json>
node <skill-root>/scripts/hdg.mjs retry-item --item <id> --expected-baseline <sha256> --confirmed
node <skill-root>/scripts/hdg.mjs accept-item --item <id> --evidence <json>
node <skill-root>/scripts/hdg.mjs gate-item --item <id> --status PASS --evidence <json>
node <skill-root>/scripts/hdg.mjs acceptance-item --item <root-id> --action INDEPENDENT_REVIEW_PASS --evidence <json>
node <skill-root>/scripts/hdg.mjs acceptance-item --item <root-id> --action USER_CONFIRMED --evidence <json>
node <skill-root>/scripts/hdg.mjs delivery-item --item <delivery-id> --action INDEPENDENT_REVIEW_PASS --evidence <json>
node <skill-root>/scripts/hdg.mjs delivery-item --item <delivery-id> --action USER_CONFIRMED --evidence <json>
node <skill-root>/scripts/hdg.mjs refresh-projections
node <skill-root>/scripts/hdg.mjs upgrade-registry --task-gate-level LIGHT|FULL --confirmed
```

正常交互必须使用 `approve-item`，让一次用户批准同时完成准备与冻结；`prepare-item`、`freeze-item` 仅用于恢复、诊断和向后兼容。正常开发调度必须使用 `dispatch-task`；`task-context`、`claim-task` 是恢复/诊断低级入口。正常 gate 必须使用 `accept-item`，由控制器读取真实 evidence、复算 hash、校验 baseline、Scope、验收项、测试退出码和 P0/P1 后生成 `acceptance-report.json/md`；`gate-item` 仅用于恢复旧记录，不作为正常 PASS 路径。根工作项用 `acceptance-item` 完成独立验收与用户确认；`delivery-item` 只保留 Delivery 向后兼容。安装更新后可用 `refresh-projections` 在不改变 revision 和状态的情况下重建中文工作台。`upgrade-registry` 只处理受支持的单根 schema v2 Task：保留 ID、冻结状态和已确认开发方式，显式补入 gateLevel，重算 baseline/contract 指纹，记录 `migrationHistory`，并删除绑定旧指纹的 context/handoff；它不是普通 baseline 修订，也不扫描其他历史控制目录。内置控制器不导入历史 `route/start/prepare/freeze` CLI 或 YAML 配置链。纯 Markdown 负责指引，真正硬门禁由控制器和磁盘状态执行。

## 验收与反馈

开发结果写回后立即生成状态为“等待门禁验收”的用户报告；不能在 `IMPLEMENTED` 停止并宣称完成。每一级 gate 使用结构化 evidence，`accept-item` 成功或失败后更新报告中的需求覆盖、实际改动、范围偏差、测试结果、P0/P1/P2 和结论。`VERIFIED` 只表示该级 gate 通过。根工作项 gate 后必须由没有开发上下文的全新只读审查 Agent 验收，或记录显式人工审查接受，再取得用户确认；审查与确认都必须提交真实、hash 匹配、结构合法且不可复用的 evidence。状态按 `WAITING_FOR_INDEPENDENT_REVIEW → WAITING_FOR_USER_CONFIRMATION → COMPLETED` 持久化，并持续更新同一份用户报告。没有隔离审查能力时报告保持 `NEED_HUMAN_REVIEW`，不伪造 PASS 或用户确认。

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
