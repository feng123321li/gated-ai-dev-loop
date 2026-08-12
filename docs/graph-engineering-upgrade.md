# ADR：分层交付 Graph Engineering 架构

状态：**已采纳的历史架构决策**。本文解释为何采用 Delivery + 递归 GROUP/TASK + 分层 Review；它不是 0.37.0 的操作手册。Plugin identity、Git 接管、交互协议和派遣安全边界的现行事实以 [项目实现结构](project-engineering.md) 与 canonical Skill 为准。

## 结论

`delivery-graph`（展示名“分层交付 Graph 控制面”）的职责是总览与调度，不是实现流程治理。项目数据目录为兼容既有 Delivery 继续使用 `.layered-delivery/`。

```text
Delivery 顶层交付需求
└─ root: GROUP | TASK
   ├─ TASK_LOOP → TASK_REVIEW_LOOP
   └─ GROUP
      ├─ GROUP | TASK
      ├─ GROUP_JOIN
      └─ GROUP_REVIEW_LOOP

root terminal
→ DELIVERY_REVIEW_LOOP
→ USER_CONFIRMATION
```

Delivery 是一次交付的身份、总目标和最终验收边界，不是 work item kind。工作项只有 `GROUP` 和 `TASK`：TASK 是执行叶子并必须独立 Review；GROUP 按整体协调需要创建，可完全省略或多层递归组织直接子 GROUP/TASK，并在子节点完成后执行本层整体 Review。

这比固定 Delivery / Capability / Task 三层，或把 development、test、Gate、Skill activation 全部展开在一张全局图中更符合 Graph Engineering：外层节点具有清晰自治边界，只通过稳定输入、资源声明和标准终态耦合。

## 分层职责

| 层 | 负责 | 不负责 |
|---|---|---|
| Delivery 顶层边界 | Graph/run 身份、交付摘要、最终 Review、用户确认 | 充当一种可递归 work item |
| Outer Graph | GROUP/TASK 依赖、并行、资源锁、租约、基础设施重试、Join、Review | 文件、实现、测试、Gate、Skill |
| TASK Loop | 完成一个独立结果，内部校验与修正 | 决定全局依赖或抢占其他资源 |
| Review Loop | 审查一个 TASK 结果、已汇合的 GROUP 边界或完整 Delivery | 改写被冻结的层级或解释完成点 |
| Skill | 为某个 Loop 提供实现方法或领域规范 | 改写外层冻结 Graph |

Graph 节点是自治工作单元，不是每一个内部动作。若某个 Java Loop 规定 Entity/Mapper/Service 的创建方式，它可以在 payload 和自身 Skill 中定义，不会与 `delivery-graph` 的外层调度边界冲突。

## 递归 GROUP/TASK

一个 GROUP 可以同时包含 GROUP 和 TASK，例如：

```text
Delivery: d-commerce
└─ GROUP: g-release
   ├─ GROUP: g-backend
   │  ├─ TASK: t-order-api
   │  └─ TASK: t-inventory
   ├─ GROUP: g-frontend
   │  ├─ TASK: t-admin-web
   │  └─ TASK: t-customer-web
   └─ TASK: t-release-notes
```

父子关系表达分解，`dependsOn` 表达同一 GROUP 下直接兄弟之间的启动屏障。依赖源可以是 TASK 或 GROUP，依赖目标也可以是 TASK 或 GROUP：

- TASK 终态是该节点的 `TASK_REVIEW_LOOP` 终态；
- GROUP 终态是该节点的 `GROUP_REVIEW_LOOP` 终态；
- 依赖 GROUP 时，只有该 GROUP 的全部后代完成、Join 成功且 Review 通过后，依赖方入口才可运行。

`GROUP_JOIN(g)` 是调度器生成的确定性屏障，不是工作项或 Loop；人类文档称为“GROUP 完成点”。对 GROUP `g` 的每个直接子级 `c`，编译 `terminal(c) --ALL_OF--> GROUP_JOIN(g)`；完成点自动成功后再以 `REQUIRES_SUCCESS` 解锁 `GROUP_REVIEW_LOOP(g)`。它没有 payload、资源锁或业务 result。

因此父 GROUP 不越级检查孙节点，也不会在子 GROUP 尚未审查时提前完成。

## Skill Hint 晚绑定

需求阶段通常只能知道“希望优先使用哪些 Skill”，无法可靠知道未来哪个 Task/Review Loop 会适用。因此 hierarchy 在 `root.skillHints` 保存一份共享提示，不把它们编译成节点，也不分配阶段：

```text
用户 Skill Hint
       │
       ├────────────┬────────────────┬──────────────────┐
       ▼            ▼                ▼                  ▼
 TASK Loop A   TASK Review   GROUP Review Loop   Delivery Review Loop
 运行时选择     运行时选择       运行时选择            运行时选择
```

每个 Loop 读取当前任务、工作区和宿主 Skill catalog 后，优先原生触发适用提示。某个提示对 Loop 不适用或当前不可用时可以跳过；Loop 也可以发现并使用其他 Skill。外层 scheduler 只负责传递，不验证 Skill 激活、顺序或生命周期。

这保持了两个边界：用户偏好不会丢失，Loop 自治也不会被 requirement 阶段的错误猜测锁死。

## Receiver 与 Worker 晚绑定

预览 hierarchy 表达开发内容；Frozen Graph 只表达哪个 TASK/Review 何时可运行。自动
派遣把 Ready Loop 绑定到当前配置宿主的独立外层 receiver，并固定
`modelPolicy=CURRENT_HOST_INHERIT`；不表达或推荐具体模型与 reasoning effort。

```text
自动执行：当前配置宿主 Adapter → receiver reservation → 独立 receiver claim
手动开发：冻结内容包 → 接收 CLI 启动同一 Graph → 独立 MANUAL receiver
Loop 内部：receiver → 按成本/任务自主使用 Codex、Claude、Grok、DeepSeek 等 Worker
```

只有外层 receiver 持有 Graph mutation bearer，能够 claim、heartbeat、progress、pause、
resume 和提交 result。内部 Worker 不获得 operation 或 reservation，只把结果返回 receiver。
新增 Worker 供应商不改变外层 Graph；只有要让供应商成为 receiver 时才新增 Adapter，并
提供 workspace 映射和独立 child 编排。独立性是宿主编排契约，不是 Controller 的密码学证明。

内部 Worker 的 agent/model/effort 可以由 receiver 在最终
`outcome.result.workerTelemetry` 中按 phase 报告。未知值写 `unreported`；该数据只用于
展示、成本分析和后续 Review，不参与授权、路由、指纹、重试或独立性判断。

运行中的容量故障不写回 Frozen Graph。额度策略固定 `PAUSE_AND_RESUME`：执行侧使用
`EXECUTOR` 暂停，总调度宿主使用 `HOST` 暂停，均等待带真实 `resetAt` 的一次性恢复
提示，不静默切换 Adapter、模型或 Worker。

## 图模型

节点类型：

- `TASK_LOOP`
- `TASK_REVIEW_LOOP`
- `GROUP_JOIN`
- `GROUP_REVIEW_LOOP`
- `DELIVERY_REVIEW_LOOP`
- `USER_CONFIRMATION`

边类型：

- `REQUIRES_SUCCESS`
- `ALL_OF`

同级依赖与递归 GROUP Join/Review 构成 DAG。Loop 内部允许有受控循环，因此“整个系统支持循环”与“外层依赖图无环”并不矛盾。

## Review 递归收敛

Review 沿层级逐层向上收敛，但不会把同一个 Review 节点重复传播：

```text
直接子节点终态
→ GROUP_JOIN
→ GROUP_REVIEW_LOOP
→ 父 GROUP 将其视为一个子节点终态
→ 父 GROUP_JOIN
→ 父 GROUP_REVIEW_LOOP
→ …
→ 根工作项终态
→ DELIVERY_REVIEW_LOOP
→ USER_CONFIRMATION
```

若根本身是 TASK，则其 TASK Review 成功后直接进入 Delivery Review。若根是 GROUP，则根 GROUP Review 成功后才进入 Delivery Review。最终用户确认只发生一次。

## Loop 边界

输入：

```json
{
  "ref": "project/java-service-loop@1",
  "payload": {},
  "resourceClaims": ["project:erp/module:order"]
}
```

输出：

```json
{
  "status": "SUCCEEDED",
  "summary": "完成",
  "result": {}
}
```

调度器只验证 JSON 可持久化、ref 合法、资源键安全唯一、终态受支持。它不读取 payload/result 里的业务字段。

## 资源模型

取消文件 scope 的全局包含/重叠算法，改用精确资源锁：

- `project:erp/module:order`
- `project:erp/database:order-schema`
- `project:portal/environment:test`

相同键互斥，无交集即可并行。Loop 可以跨项目或跨模块，但必须声明真正需要排他的共享资源。

## 失败路由

外层自动重试只面向运行基础设施：

- `RETRYABLE_INFRA`
- `WORKER_LOST`

以下情况不自动重跑：

- Loop 的业务 Gate 未解决；
- 需要外部权限；
- 需要改变依赖、资源或拓扑；
- Loop 主动取消。

冻结 Revision 只固定当前外层目标、依赖、资源声明、项目范围和拓扑，不固定 Loop 内部实现计划；显式 payload 也不是工程正确性的穷举清单。Gate 失败、普通实现缺陷或 Review finding 只要能在当前 scope 和权限内修正，就由当前 Loop 调整方案、修正并重新验证。`BLOCKED` 仅用于当前 Loop 已无可行的 scope 内路径，并要求显式 failure class；只有冻结的依赖、资源、项目范围或拓扑必须改变时才返回 `REPLAN_REQUIRED`。最终用户验收前的 replan 保持同一 `delivery.id` 并生成下一不可变 Revision，不再用取消旧 run 加新 Delivery ID 表达同一需求。

Revision 连续性必须来自用户明确说明，而不是来自工作区恰好恢复了哪个 Active Delivery。不同工单或独立业务目标默认建立新 Delivery。现行 Git 宿主策略统一为 `CURRENT_WORKSPACE_SERIAL`；历史版本使用过的 `EXCLUSIVE_PRIMARY_CHECKOUT` 与 `HOST_NATIVE_LINKED_WORKTREE` 均已废止，不是现行规范。Controller 不执行 Git 写入，只通过 `worktreeProvenance` 和冻结 binding 描述当前实际 checkout、拓扑与基线。

`preview_hierarchy` 只登记不绑定工作区的 `CHOICE_READY`，并返回当前唯一 `pendingInteraction`：缺 binding 时先处理 `DEVELOPMENT_BASELINE`，之后才是 `EXECUTION_MODE`；兼容字段 `developmentBaseline` / `executionChoice` 指向同一对象。干净和脏工作树都遵循该顺序；dirty 指纹覆盖 porcelain、变化路径的 worktree blob 与 index state，`.layered-delivery/**` 不计入业务 dirty。宿主能调用原生选择器时必须使用原生交互，不能自行改写为自由文本确认。

AUTOMATIC 由 `select_execution_mode` 持久记录后进入当前 workspace 的串行队列。宿主仅在前序 Delivery 已提交、clean、HEAD 未漂移且 receiver 安全释放后，按冻结 binding 在当前 checkout 创建或切换目标分支，再用原 `rootId` 与双 fingerprint 调用 `resume_execution_mode`；不创建新的 linked worktree，也不启动专用后台 coordinator。手动接管若发生单仓 Git 漂移，则在任何控制状态写入前返回基线重确认：确认原 binding 保持当前 Revision，确认新 binding 创建下一不可变 Revision。多仓漂移 fail closed，要求完整 project bindings，不能由单仓选择器局部修订。

## 逻辑递归与物理布局

GROUP/TASK 的递归存在于冻结 hierarchy 和编译 Graph 中，并镜像到可重建的人类投影目录。GROUP 可多层、平行或不存在；根 TASK 不创建虚拟 GROUP。工作区共享一份 SQLite 权威，并按稳定的 `delivery.id` 隔离各次需求交付：

```text
.layered-delivery/
├── overview.md
├── scheduler.db
├── d-commerce/
│   ├── handoff-<fingerprint>.md  # 手动交接时按需
│   ├── overview.md
│   ├── baseline.md
│   ├── progress.md
│   ├── acceptance.md
│   └── work-items/
│       └── <root-id>/
│           ├── baseline.md
│           ├── progress.md
│           ├── acceptance.md
│           └── children/
│               ├── <group-id>/
│               │   ├── baseline.md
│               │   ├── progress.md
│               │   ├── acceptance.md
│               │   └── children/...
│               └── <task-id>/
│                   ├── baseline.md
│                   ├── progress.md
│                   ├── acceptance.md
│                   ├── interfaces.md  # 接口索引；按需
│                   └── interfaces/
│                       └── 001-<接口标识>.md  # 每接口一份详情
└── d-maintenance/
    ├── overview.md
    ├── baseline.md
    ├── progress.md
    └── acceptance.md
```

`scheduler.db` 是需求与调度状态的机器权威；一个需求的自动与手动开发都进入同一个稳定 Delivery 目录并使用同结构人类投影，不再使用共享 `handoffs` 目录。手动包另含 `handoff-<fingerprint>.md`，并以 `HANDOFF_READY` 登记到 SQLite、刷新根 `overview.md`；交接阶段不创建 Graph Run、事件链或 workspace 绑定。接收 CLI 在任何代码工作前调用 `start_manual_handoff` 后，同一快照进入 `execution_mode=manual` 的 run：TASK 实现只允许 MANUAL claim，所有 Review 只允许正常 AUTO claim。Graph 投影只保留分离的需求基线、执行进展和验收记录，不再生成 hierarchy、编译 Graph 或当前状态 JSON 副本。Delivery baseline 链接全部节点 baseline；每个 GROUP/TASK 在 `work-items/<root-id>/children/...` 的对应节点目录拥有 baseline、progress 和 acceptance。只有 TASK 显式声明接口时才在自己的目录增加接口契约投影；协议字段保持开放，HTTP、Dubbo、gRPC、GraphQL、消息等均可表达。接口投影展开实际 request/response 字段，空字段列表明确标记无入参或无出参；HTTP 位置容器和 Controller 返回元数据只参与规范化，不作为业务字段输出，`wireType`、`frameworkEnvelope`、`wrapping` 与 `Rs` 包装信息一律忽略。目录名使用不可变 ID，不使用可修改标题；物理递归只镜像父子关系，不重新引入文件 scope，兄弟执行顺序仍由 `dependsOn` 控制。

外部工单号通过 `delivery.requirementKey` 与一个稳定 `delivery.id` 一对一绑定；未显式声明时，Controller 仍从 Delivery ID/标题识别常见 `PROJECT-123` 引用。不同 ID 命中同一 key 时，preview 与最终事务写入都返回连续性冲突，要求复用已有 ID。`HANDOFF_READY` 内容变化使用 `create_manual_handoff` 的显式当前 Revision、`USER_EXPLICIT_SAME_DELIVERY` 和修订原因，在原目录追加不可变手动 Revision；旧 Revision 标记为 `SUPERSEDED`，不再通过改名生成重复目录。

人类投影集合必须足以完成冻结前评审、运行中跟踪和最终验收：工作区 `overview.md` 只列 Delivery 入口，Delivery `overview.md` 展示本交付状态与内部统计；Delivery 投影负责聚合与串联；节点投影覆盖双指纹、summary、`dependsOn`、Loop 引用、资源锁、不透明 payload、运行状态、Loop 结果和证据。验收内容按层归属：TASK 只完整展开本 TASK 与 TASK Review，GROUP 只完整展开本层完成点与 GROUP Review，Delivery 只完整展开 Delivery Review 和用户确认；向上一层只提供直接下层或根工作项的状态、简要结果和报告链接，不复制下层输入、证据或 findings。进度表展示外层 receiver、认领身份和执行轮次；内部 Worker 的 agent/model/effort 仅从最终 `workerTelemetry` 非权威展示，未知值为 `unreported`。验收摘要、子节点结果和 P0/P1/P2 问题使用表格。P0/P1 只在修复、验证和独立复审后关闭，P2 非阻断但必须列示。新增、调整或删除接口的 TASK 通过 `payload.interfaces` 显式提供 `changeType`、协议、名称、简介以及完整 before/after 快照；控制器生成 `interfaces.md` 索引，并在 `interfaces/` 下为每个接口生成一份详情。HTTP 按 Path、Query、请求头、请求体和响应参数展示，Dubbo 按接口、方法、调用参数和返回结果展示；字段表覆盖类型、必填、最大长度、说明和示例值。冻结 baseline 的 after 是开发接口与后续 Torna 发布的唯一事实来源，方法、路径或签名以及字段层级和属性必须一致。数据库变更由规划上下文在 preview 前通过 `payload.databaseChanges` 提供完整表级 before/after、字段、主键、约束、索引、外键和迁移/回滚/回填/兼容/验证方案；控制器拒绝不完整契约与 LIGHT 保障档，生成 `database-changes.md` 及每表详情，TASK Loop 只允许应用和验证冻结 after，偏离时必须重新规划为同一 Delivery 的新 Revision。删除值使用 Markdown 删除线，新增或删除字段只显示存在的一侧。代码可辅助准备和验证契约，但不成为动态投影源，接口和数据库内容也不参与 Graph 调度决策。固定展示使用中文，标明 UTC+8 的时间使用 `YYYY-MM-DD HH:mm:ss`；SQLite 继续保持机器 UTC。

## 可恢复性

每次迁移写入带前序哈希的事件。`node_runs` 是高效查询的物化状态，必要时由 `rebuild_graph_run` 从事件重建。MCP 断连后先读权威状态，operation ID 不复用。

## 为什么更符合 Graph Engineering

1. 节点边界按自治能力划分，而不是按实现步骤划分。
2. 外层只持有调度信息和共享 Skill 偏好，降低跨 Skill 耦合。
3. 不同 Loop 可以在运行时独立演进和选择 Skill。
4. 失败域清晰：内部质量失败留在 Loop，基础设施失败进入 scheduler，拓扑变化进入 replan。
5. DAG、FSM、事件链和资源锁各自表达一种关系，不把所有语义塞进一个 scope 或 Gate 模型。
