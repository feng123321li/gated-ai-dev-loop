# 分层交付工作流

## 主流程

1. 优先使用 Plugin 启动的单一 Python stdio MCP Server 及其 35 个 snake_case 结构化工具；只有 CLI fallback 使用 kebab-case 命令。Server 生命周期内只绑定一次被治理项目根：Claude 从 `${CLAUDE_PROJECT_DIR}` 启动绑定，Codex 从可信 `codex/sandbox-state-meta.sandboxCwd` 首次调用绑定，后续根不一致即拒绝；普通工具参数不得传入 `root`，维护专用 `dogfood` 和确认布尔值 `confirmed` 也不进入工具参数。只有 MCP 未安装、未连接或不可用时，才从当前 Skill 元数据解析 `<skill-root>`（当前已加载 `SKILL.md` 所在目录），并从项目根运行 `python -X utf8 <skill-root>/scripts/hdg.py --help` 进入 CLI fallback。不得根据用户名、用户主目录、Skill 宿主或操作系统猜测安装位置，也不得把解析后的本机绝对路径固化到交接、方案或治理状态。Python 3.10+ 是唯一运行时，不要求 Node、npm 或第三方包。
2. 首次路由调用 MCP `workspace_status`，或仅在 fallback 调用 CLI `workspace-status`：只有 `ACTIVE` 才查询 `graph_frontier` 恢复现有运行；`ABSENT` 和 `STAGING_ONLY` 都没有 active delivery，后者只表示同库存在暂存 payload。数据库 schema、约束、ID、拓扑、路径、指纹或普通字段不一致时状态查询必须报错并保持阻断，不迁移、不猜测。仅当历史节点只有 evidence 引用过期、完整 artifact 仍在 SQLite 且其余契约有效时，将该节点只读隔离并在总览告警；其他新需求、有效兄弟 Task 和已有 claim 继续。Markdown 缺失时可从数据库刷新。
3. 起草层级事实卡，选择最浅合法形态：独立 Task、Capability→Task 或 Delivery→Capability→Task。为每个实际节点起草自己的 baseline、`developmentPlan` 和必填但可空的 `requiredSkills`；Skill 名使用 catalog 标识，阶段只允许 `DEVELOPMENT/GATE/FINAL_REVIEW`。
4. 把整棵需求树组织成 `{"schemaVersion":3,"root":{"definition":{...},"children":[...]}}`。协调节点声明的每个 child 必须在这棵树里完整物化。
5. 通过 MCP 结构化调用 `prepare_hierarchy`；仅 CLI fallback 通过 stdin 调用 `prepare-hierarchy`。一个需求只生成 `work-items/<root-id>/` 一个顶层目录，子节点按 `children/<id>/` 递归嵌套；根级 `development-plan.md/progress.md` 聚合完整树，控制器同时编译需求级 `execution-graph.md` 与 `frontier.md`，并在 `.layered-delivery/state-transition-graph.md` 维护当前 schema v3 共享的运行时状态投影。根节点自身进度写入 `node-progress.md`，每个实际子节点生成自己的 `development-plan.md/progress.md`。
6. 人工查看根级 `development-plan.md`、`execution-graph.md` 与工作区级 `state-transition-graph.md`，同时选择 active/manual。Agent 必须消费准备结果的 `responseContract`，每次首次准备、方案修订或幂等重试后的确认提示都同时展示两种方式。需要修改就重新准备整棵树；同意时只需确认当前方案和所选方式，无需知道或复述层级/图指纹。
7. Agent 使用准备结果中的 `hierarchyFingerprint`，调用一次 MCP `freeze_hierarchy`。工具以专用操作本身表达“用户已经确认当前方案”，不接受可由模型自行填入的 `confirmed` 布尔值；CLI fallback 才调用 `freeze-hierarchy --expected-hierarchy ... --development-mode ... --confirmed`。控制器在同一事务中记录方式并冻结全部节点；指纹已变化则拒绝旧确认。
8. active 下由当前 Agent 冻结后查询 `graph_frontier` 并直接推进；manual 在当前窗口完成确认和冻结，在需求根生成完整 `requirement-handoff.md`，并返回简短 `handoffCommand`。规划会话必须按冻结结果的 `responseContract` 在首次最终回复中提供可一次复制到其他 Agent 的纯文本代码块；可以使用 `handoffCommand`，也可以生成覆盖 `requiredSemantics` 的语义等价文本，不要求逐字一致，且不能只给文件链接。新运行窗口启动这份交接后即恢复同一 graph run，不重新 `prepare_hierarchy`、不重新 `freeze_hierarchy`、不重新选择方式，也不逐 Task 请求确认；它自动调度、开发、测试、修复、逐级门禁和预算内恢复，直到 acceptance 为 `WAITING_FOR_USER_CONFIRMATION`，frontier 停在 `REQUEST_USER_CONFIRMATION`。恢复入口是 `graph_frontier` 而不是只读诊断用的 `task_context`；每个执行、门禁或审查 action 先完整应用其 `requiredSkills`，再按紧凑 `evidenceContractRef` 调用 `evidence_contract`，从 SQLite 只读取当前工作项的一个模板；成功 evidence 必须逐项记录具体 `skillUsage`，不扫描源码/memory，也不把整树模板放进上下文。两种方式都严格消费 Graph 自动计算的 `actions` 与 `dispatchPlan`：控制器决定完整安全 Task 集合、目标 Agent 数、稳定顺序和确定性恢复动作，平台容量只决定立即启动或排队，不能挑选或跳过；排队项保持未认领，只有 worker 真正启动时才调用 `dispatch_task` 创建 claim。执行适配器以 frontier 的 `nextWakeAt` 为最长等待时间，自动消费到期的 `HEARTBEAT_TASK`；没有独立宿主适配器时当前会话就是执行适配器，不能在长实现、长测试或等待子 Agent 时停止续租。硬过期返回 `ADVANCE_GRAPH`，执行循环自动推进、重新查询并用新 operation 重新认领，不请求人工重置。控制器对心跳使用窄数据库更新和轻量投影，不重建整个工作区。
9. 开发结果写回前，执行循环先按正式上下文的 `evidenceContractRefs.result` 调用 `evidence_contract`，取得绑定当前 operationId 的 `IMPLEMENTED` 与 `BLOCKED` 模板。完整 artifact 通过 MCP 结构化参数交给 `task_result`；仅 CLI fallback 使用 `task-result --evidence -` 从 stdin 提交。控制器在同一 SQLite 写事务内返回逐字段错误或计算摘要并保存 artifact 与摘要，然后生成 `development-review.md`。开发结果不代表 PASS，也不产生临时 evidence 文件。
10. 回归、门禁、独立审查或最终验收发现遗漏时，先判断是否仍为原冻结目标和验收契约。已有授权文件内直接重试；仅缺少完成原验收项所需的精确文件，且目标、需求、验收、接口行为、数据、拓扑和外部权限不变时，通过 `remediate_task` 追加到原 Task。控制器保持 baseline 与图定义不变，从该 Task execution 沿显式边失效必要后继、依赖消费者和聚合 gate，再创建新 attempt；不得用 `prepare_hierarchy` 新建重复需求根。
11. 全部相关回归和复测通过后，Graph 执行循环形成严格 gate artifact，并通过 `accept_item` 结构化提交。控制器在同一事务中按当前 baseline 和追加验证修正校验、计算摘要并保存结构化验收记录，随后生成 `acceptance-report.md`；Task 全部 VERIFIED 后依次运行 Capability、Delivery 自身聚合门禁。
12. 治理根 gate PASS 后进入最终验收阶段：独立只读审查可自动记录；需要人工审查时必须到达人；最终 `record_user_confirmation` 只有在用户明确接受完整根验收报告后才可调用。验收报告随后更新至 `COMPLETED`。提交、推送、合并、迁移、发布或新增外部权限不属于 active/manual 的中段自治，仍须单独授权。

任何步骤都不能从“优化、开发、项目、治理”等自然语言关键词推导创建或冻结授权。宿主首次信任或 Auto 配置、契约变化、真实不可恢复阻断和新增外部权限可以返回用户；普通 Task 开工、测试、修复、门禁、预算内重试与租约恢复不能。

## 人与 Graph 的职责总流程

```mermaid
flowchart LR
    H1["用户/需求宿主<br/>讨论需求与方案"] --> H2{"确认冻结方案？"}
    H2 -->|"修改 / Revise"| H1
    H2 -->|"确认 / Confirm"| G["Graph 中段自治<br/>依赖 + 自动 Agent 调度 + 门禁 + 失败恢复"]
    G --> A["工程门禁与独立审查通过"]
    A --> H3["用户/需求宿主<br/>最终验收确认"]
    G -. "合同变化或外部授权 / Contract Change or Authority" .-> H1
```

执行平台只承载 Agent 与队列，不拥有中段任务选择、Agent 数量、调度顺序或失败路由决策权。

## 层级与目录

```mermaid
flowchart TD
    A["一个用户需求"] --> R{"最浅合法根"}
    R -->|"单一执行结果"| T["任务"]
    R -->|"多个任务需要聚合"| C["能力"]
    R -->|"多个能力需要聚合"| D["交付"]
    C --> CT["任务一至多个"]
    D --> DC["能力一至多个"]
    DC --> DT["任务一至多个"]
    T --> P["一个根目录 + 根级总览 + 节点独立方案与进度"]
    CT --> P
    DT --> P
```

```text
.layered-delivery/
├── governance.sqlite3
├── workspace-overview.md
├── state-transition-graph.md # 工作区共享的开发流程、FSM 与失败路由
├── assets/
│   ├── development-flow.svg
│   └── node-state-machine.svg
├── workspace-overview/
│   ├── YYYY-MM.md  # 月度需求索引
│   └── YYYY-MM/
│       └── <root-id>.md  # 可直接打开的单需求层级明细
└── work-items/
    └── <root-id>/
        ├── development-plan.md
        ├── execution-graph.md  # 嵌入 SVG 的执行图 + 治理图
        ├── assets/
        │   ├── execution-graph.svg
        │   └── governance-graph.svg
        ├── frontier.md         # 关键路径、动作与阻断看板
        ├── run-timeline.md     # graph run、attempt 与事件
        ├── progress.md         # 整树总进度
        ├── node-progress.md    # 根节点自身进度
        ├── interaction-log.md
        ├── requirement-handoff.md  # 仅 manual 冻结后生成
        ├── baseline.md
        └── children/
            └── <child-id>/
                ├── baseline.md
                ├── development-plan.md
                ├── progress.md
                └── children/...
```

不允许 `Delivery → Task`、`Capability → Capability`、平铺子包或只声明不物化的 child。

## 生命周期

生命周期按职责拆成三段阅读，不把整条状态机挤进一张横向图。

### 1. 整树准备与冻结

```mermaid
flowchart TD
    A["准备完整需求树"] --> B["生成根级开发方案"]
    B --> C["人工评审方案并选择开发方式"]
    C --> D{"是否确认当前方案？"}
    D -->|"需要修改"| A
    D -->|"明确同意"| E["一次记录开发方式并冻结整棵树"]
    E --> F{"已选择的开发方式"}
    F -->|"主动开发"| G["所有任务进入已冻结状态"]
    G --> H["Graph 自动计算 Agent 数并循环开发与测试"]
    F -->|"手动开发"| I["所有任务进入已冻结状态"]
    I --> J["生成一份根级需求交接"]
    J --> K["人工一次复制到新会话"]
    K --> H
```

人工在同一次前期评审中确认当前根级计划和开发方式；层级指纹由 Agent 和控制器绑定，无需人工复述。manual 的“一次交接”不表示一次认领全部 Task：接收会话恢复已经冻结的同一 graph run，读取 Graph 自动生成的完整调度计划，并自动推进到 `WAITING_FOR_USER_CONFIRMATION`，停在 `REQUEST_USER_CONFIRMATION`，不重新准备/冻结或让人逐个转交、逐个确认。目标 Agent 数、并行组、调度顺序和降级路径由 Graph 在每次迁移后计算，不进入冻结方案；执行平台只负责立即启动或排队。

### 2. 单个任务的开发与门禁

```mermaid
flowchart TD
    A["任务已冻结"] --> B{"Graph 判定当前是否可调度？"}
    B -->|"否"| W["等待依赖、解除范围冲突或补全配置"]
    W --> B
    B -->|"是"| C["Graph 计划自动派发并认领任务"]
    C --> D["实现并运行回归测试"]
    D --> E{"是否仍有失败？"}
    E -->|"是"| F["修复并复测"]
    F --> D
    E -->|"否"| G["写回开发结果并生成开发复核"]
    G --> R{"开发结果"}
    R -->|"已阻断"| X["任务已阻断"]
    X -->|"自动重试"| A
    R -->|"已实现"| M["执行任务门禁"]
    M --> N["生成验收报告"]
    N --> O{"门禁结果"}
    O -->|"未通过且无需补充文件"| X
    O -->|"原验收项遗漏精确文件"| Y["原任务追加验证修正"]
    Y --> A
    O -->|"通过"| V["任务已验证"]
```

“可调度”是实时计算结果，不写入持久化生命周期。“已实现”只表示开发结果已回收，必须继续执行门禁。同契约修正必须使用原 Task 的 `remediate_task`，不能创建新根。MCP 机械工具依次为 `dispatch_task`、`task_result`、`remediate_task`（仅验证修正需要）、`accept_item` 和 `retry_item`；CLI fallback 才使用对应 kebab-case 命令。

### 3. 父级聚合与最终完成

```mermaid
flowchart TD
    A["叶子任务已验证"] --> B{"该任务是治理根？"}
    B -->|"是"| R["治理根已验证"]
    B -->|"否"| C["当前协调节点的全部直接子级已验证"]
    C --> D["当前协调节点运行聚合门禁"]
    D --> E["生成该级验收报告"]
    E --> F{"聚合门禁结果"}
    F -->|"未通过"| X["当前协调节点已阻断"]
    X -->|"修复并自动重试"| D
    F -->|"通过"| V["当前协调节点已验证"]
    V --> Q{"当前节点是治理根？"}
    Q -->|"否"| P["上移到它的父级"]
    P --> C
    Q -->|"是"| R
    R --> S{"独立审查或人工审查"}
    S -->|"发现同契约文件遗漏"| T["回到原任务追加验证修正"]
    T --> T2["原任务重新开发并通过任务门禁"]
    T2 --> A
    S -->|"无法隔离或契约需变化"| N["保持等待独立审查或回到需求评审"]
    S -->|"通过"| U{"用户是否最终确认？"}
    U -->|"尚未确认"| W["保持等待用户确认"]
    U -->|"确认"| Z["需求已完成"]
```

能力和交付都必须在全部直接子级已验证后运行自己的聚合门禁，不能把子级完成等同于父级通过。同契约验证修正必须回到原 Task，并沿显式图边失效必要后继、依赖消费者和聚合 gate。Task 门禁失败时，`retry-item` 在剩余 gate attempt 预算内同时重开 Task execution 与 Task gate，frontier 必须先回到 `DISPATCH_TASK`，不得在 Task 为 FROZEN 时反复给出 `RUN_GATE`；协调节点门禁失败只重开当前聚合 gate。第三次仍失败时转人工干预。两者都不进入最终确认，也不新建重复需求根。

## 恢复与失败关闭

Graph 执行循环按 `nextWakeAt` 调度心跳，每 5 分钟由执行适配器调用 `heartbeat_task`。心跳只更新当前 Task 及 graph run 的必要行，并刷新 execution graph、timeline 和 frontier；仅在 CLI fallback 排查宿主耗时时，可为控制器命令添加全局 `--timing`，从 stderr 的单行 `HDG_TIMING` JSON 查看锁等待、提交、投影和文件写入阶段。30 分钟软租约后有 2 分钟竞争宽限；宽限内当前 operation 可补心跳或写回结果，宽限结束后 frontier 返回 `ADVANCE_GRAPH`，执行循环调用 `advance_graph` 将其归类为 `WORKER_LOST`，重新查询并用新 operation 继续，旧 operation 永久失效且不能复用。控制器只对结构化 `RETRYABLE` 与 `WORKER_LOST` 做预算内自动重试；第三次失败后写入 `RETRY_EXHAUSTED`。代码和测试完成后必须先提交当前 Task 结果并继续消费 gate/review，不能直接输出最终总结，也不能把正常租约恢复表述为手动重置。暂停和恢复使用显式工具及事件；取消整个运行必须由用户明确确认。

- 恢复优先使用用户给出的精确 ID/路径、有效焦点或唯一候选；多个候选时请求选择。
- 只读隔离项不能作为命令目标，也不能被事务修改或删除；它不阻断其他有效工作项。隔离集合在写事务中发生变化时必须回滚。
- 冻结前 `development-plan.md` 被篡改，或 SQLite 中任一 hierarchy/baseline/state 记录不一致时拒绝冻结或调度。
- 人工未在冻结确认中明确选择开发方式时拒绝冻结，不生成可执行上下文。
- 父链漂移、依赖未验证或范围冲突时 Task 不 READY。
- 测试或证据不足时 gate 不 PASS。
- 没有独立审查能力时根保持等待人工审查。
- 未取得用户确认时不得标记 `COMPLETED`。
- 未完成需求的验证发现若映射到已有验收项，不得另建根 Task；使用 `retry_item` 或 `remediate_task` 回到原 Task。已 `COMPLETED` 的需求保持不可变。
