# 递归 Graph 规划

用于生成新的软件交付基线与关联文档、统一选择自动或手动开发，或调整和冻结后续 Revision。

## 准备结果续接

- 每次收到新的用户需求，先判断它是否明确要求继续、修改或恢复当前 `delivery.id`。**新用户需求默认属于新 Delivery**；不同工单、不同业务目标、用户明确称为“新需求/独立需求”，或没有明确引用当前 Delivery，均不得修改当前 Delivery。
- 不得仅因 `workspace_status` 返回旧 Delivery 就进入 Revision。只有用户明确要求修改或继续该 Delivery 时，才允许调用 `unfreeze_task_requirement` 或 `prepare_delivery_revision`；Agent 的语义判断不能替代这项用户连续性授权。
- 当前上下文仍保留最近一次 `preview_hierarchy` 响应和原始 hierarchy 时，复用其中的双 fingerprint、完整清单和 `pendingInteraction`；需求未变且尚无 `executionSelection` 时不要重复 preview，回答用户问题后重新展示 Controller 返回的同一交互。若已记录 `AUTOMATIC`，只续接 `CURRENT_WORKSPACE_SERIAL` 的当前分支准备；不得创建新 worktree 或重新展示选择器。
- 初次开发前用户修改需求时，更新 hierarchy 并重新调用 `preview_hierarchy`；Controller 在同一 `CHOICE_READY` Delivery 中重新生成基线与关联文档，只使用新响应的 fingerprint，不复用旧值。初始自动选择统一调用 `select_execution_mode`，不得由 Skill 拆成或猜测 `prepare_hierarchy` / `freeze_hierarchy` 步骤。
- 初次冻结后、最终用户验收前用户修改依赖、项目范围、资源或拓扑时，保持相同 `delivery.id` 调用 `prepare_delivery_revision`，不得重新调用初始 prepare，也不得创建另一个 Delivery ID。用户明确要求继续同一 Delivery 时提交 `continuity_basis=USER_EXPLICIT_SAME_DELIVERY`；只有当前 Graph 已记录 `REPLAN_REQUIRED` 时提交 `ACTIVE_LOOP_REPLAN`。路径、分支和旧 Delivery 状态都不是连续性依据。
- 用户给出工单号、需求号等稳定外部标识时，将其规范化写入 `delivery.requirementKey`。同一 key 只能映射一个稳定 `delivery.id`；Controller 还会从 Delivery ID/标题识别常见 `PROJECT-123` 工单号，在 preview 与最终写入两处拒绝换 ID 后重复冻结。
- `HANDOFF_READY` 手动需求变化时不创建新 Delivery，也不调用只适用于自动 Graph 的 `prepare_delivery_revision`。保持相同 `delivery.id` 重新 preview 后，再调用 `create_manual_handoff`，同时提交 `expected_current_revision`、`continuity_basis=USER_EXPLICIT_SAME_DELIVERY` 和非空 `revision_reason`；旧手动 Revision 标记为 `SUPERSEDED`，新 handoff 与当前投影继续位于原目录。
- `prepare_delivery_revision` 只生成待确认候选，不替换当前 hierarchy 或旧 run，不应触发宿主通用确认弹窗；用户在完整范围和授权清单上选择自动执行或手动开发，才是该 Revision 唯一一次业务确认。
- 自动冻结同一 Delivery 的任意后续 Revision（`N → N+1`）时，若原物理 workspace turn 尚未释放，项目集合、checkout、分支与冻结基线未变，Controller 复用最初的 clean `workspaceTurnStart`，前序 Revision 的 tracked、staged 和 untracked 业务改动无需删除、stash 或检查点提交；冻结确认不授权 commit。若旧 Revision 已到最终用户确认边界并在 commit/clean/receiver 安全条件下释放 turn，用户提出修改时下一 Revision 重新进入队列，轮到后切回冻结分支并捕获新的 clean turn start。未解决冲突、turn 历史改写或项目/绑定变化仍 fail closed。
- 当前上下文不再持有精确 fingerprint 或原始 hierarchy 时，不从旧投影反推机器输入，也不猜测旧值；重新收集需求并 preview 后再请求执行方式确认。

## 保障档输入

`assuranceProfile` 不再由 Agent 风险分类或推荐工具决定。默认使用 `STANDARD`；
只有用户明确要求 `LIGHT`，且 hierarchy 满足一个根 TASK、无独立 Review 的结构
约束时才使用 `LIGHT`。LIGHT 的 `assuranceRationale` 记录用户的明确选择与定向
验证要求；执行中发现范围需要新增 TASK、Review、项目或数据库契约时返回
`REPLAN_REQUIRED`，以同一 `delivery.id` 准备 `STANDARD` Revision。

## 规划阶段 Skill 预触发

用户明确指定 Skill 时，将其记录到共享 `root.skillHints`。检查真实代码和初始范围后、形成 TASK 边界与 payload 前，判断该 Skill 是否能帮助把握需求方向、关键约束、已知验收、主要风险或合理切分；适用且当前宿主可用时按原生入口预触发。Codex 使用 `$skill-name`，Claude Code 使用 Skill tool，其他宿主按 catalog 名使用自己的原生入口。不适用于规划或不可用时不阻塞、不要求用户再次确认，继续把它留给后续相应 Loop；实现、生成器、测试和编码规范类 Skill 多数在 TASK 阶段才有足够上下文。

预触发只校准大方向，不要求规划 Agent 把 Skill 的完整做法提前展开。只提取需求目标、用户明确约束、已确认外部契约、验收、TASK 边界和材料风险；不要把 Skill 示例、默认目录、推荐文件名、实现类、内部方法、代码结构或详细测试组织写成冻结事实。只有需求原文明确指定，或用户确认必须兼容的外部契约明确指定某个精确标识时，才随需求冻结。

规划达到“方向、边界和验收足够清楚”即可，不追求面面俱到。实现所需但尚未明确的普通细节由 TASK Loop 读取真实代码后自主发现、选择、验证和调整。

## 选择根节点

- 一个可独立调度结果：使用根 `TASK`。
- 多个结果需要依赖、并行或汇合：使用根 `GROUP`；只有存在真实直接子项 seam 时才为该 GROUP 配置 Review。

工作项类型只有 `GROUP` 和 `TASK`。Delivery 不作为工作项层级，而是整个 Graph、最终 Acceptance/Readiness 和用户确认的顶层边界。GROUP 可递归混合包含 GROUP/TASK；TASK 是唯一执行叶子。

按调度关系拆分，不按文件数量拆分。一个 TASK Loop 可以覆盖一个模块、多个模块或多个项目，只要它能作为整体返回标准终态。GROUP 是动态、可选的协调边界，可以多层、平行，也可以完全不存在；它的 Review 边界另行按直接子项 seam 判断。不要为表现项目/模块目录，或只包裹一个 TASK，而强制增加 GROUP。

## TASK 切分完整性预检

候选 hierarchy 形成后、`preview_hierarchy` 之前执行一次阻断式预检；局部需求修订则在 `refreeze_task_requirement` 之前对受影响 TASK 重跑。精确策略来自 `hierarchy_contract.projectionGuidance.taskSplitIntegrityPreflight`，预检属于规划宿主，不把自然语言 payload 交给 Controller 猜测。

L0 对每个 TASK 做确定性边界检查：它的实现结束态只能依赖冻结 baseline 和已成功的传递前驱；TASK Review 的验收命令必须能在任何后继 TASK 开始前运行；不得写出“当前 TASK 先破坏编译，等后续 TASK 恢复”或把本层验收推迟给后继的计划。任一条件不满足，先移动、合并或延后变更，再生成可确认 baseline。

L1 只在需求明确要求删除、改名、移动类符号或修改公共字段/方法/签名，或者真实代码检查已确认该影响不可避免时，触发规划层的可插拔语言 analyzer；不要为满足预检而发明文件、类或方法变更。分析范围限制在本 Revision 授权的项目：定位当前声明，扫描主代码和测试中的剩余引用，把引用映射到负责 TASK，并确保破坏性变更与最后一个引用更新位于同一 TASK。Java 优先使用宿主提供的符号引用分析，能力不可用时回退到定向文本搜索；这里不要求全量 Maven/Gradle 构建。结果不明或仍有未归属引用时按失败处理，保守调整 TASK 边界。

预检必须在 `plan_dispatch_batch` 之前完成。reservation 创建后不继续做规划分析；发现必须修订时等待现有 reservation 到期并重新读取 frontier，再进行需求修订。

## Delivery 与 root wrapper

完整 hierarchy 的最外层字段只有 `delivery` 和 `root`。`schemaVersion` 与整张 Graph 共享的 `skillHints` 位于 root wrapper：

```json
{
  "delivery": {
    "id": "d-order",
    "requirementKey": "ORDER-123",
    "title": "交付订单能力",
    "summary": "完成订单能力并取得最终验收",
    "reviewLoop": {
      "ref": "delivery/acceptance-readiness-loop@1",
      "payload": {"goal": "确认顶层需求覆盖、整体证据、运行准备度和全局风险"},
      "resourceClaims": []
    }
  },
  "root": {
    "schemaVersion": 3,
    "skillHints": [],
    "definition": {
      "schemaVersion": 3,
      "id": "t-order",
      "kind": "TASK",
      "parentId": null,
      "title": "完成订单交付",
      "summary": "返回一个可独立验收的订单结果",
      "execution": {
        "dependsOn": [],
        "loop": {
          "ref": "project/order-task-loop@1",
          "payload": {"goal": "完成订单需求并在 Loop 内验证"},
          "resourceClaims": ["project:erp/module:order"]
        }
      }
    },
    "reviewLoop": {
      "ref": "task/independent-review-loop@1",
      "payload": {"goal": "独立审查订单 TASK 结果"},
      "resourceClaims": []
    },
    "children": []
  }
}
```

`delivery.requirementKey` 是可选但在用户提供外部工单号时必须声明的业务身份；它与 `delivery.id` 一对一绑定，归档也不释放该映射。`delivery.id` 是稳定的 Delivery/Graph 标识，也是需求投影目录的 namespace。工作区未归档 Delivery 的入口位于 `.layered-delivery/overview.md`，这里只列 Delivery 标识、标题、状态、更新时间和详情链接；该 Delivery 自己的 TASK 进度与 GROUP 数量位于 `.layered-delivery/<delivery-id>/overview.md`。`STANDARD` 的 `delivery.reviewLoop` 在根终态之后执行；`LIGHT` 将其设为 `null`，根 TASK 成功后直接进入用户确认。

同一需求的所有人类文件共用 `.layered-delivery/<delivery-id>/`。自动与手动开发都生成 overview、baseline、progress、acceptance、revisions 和同结构 work-items；手动开发另有 `.layered-delivery/<delivery-id>/handoff-<fingerprint>.md`，包含完整 schema v3。不得创建跨需求共享的 `.layered-delivery/handoffs/`。手动包以双 fingerprint 冻结需求内容并在 SQLite 登记为 `HANDOFF_READY`；交接阶段尚未形成 Graph Run。接收宿主必须在任何代码工作前以精确双 fingerprint 调用 `start_manual_handoff`，在实际工作区把同一快照启动为 manual Graph；随后由独立原生 child 以显式 receiving context 与 `operation_id` 领取 TASK，Graph 状态仍只以 MCP 返回和 SQLite 事件链为准。

一个 `delivery.id` 可以拥有多个不可变 Delivery Revision。Revision 1 是初次冻结范围；用户最终验收前的外层范围调整形成 Revision 2、3……，仍位于同一投影目录并通过 `revisions.md` 串联。旧 Revision 及其 run/event 不覆盖、不删除；新 Revision 冻结时，旧 run 变为 `SUPERSEDED`。

## 定义递归节点

root wrapper 包含 `schemaVersion`、`skillHints`、`definition`、`reviewLoop` 和 `children`。嵌套节点只包含 `definition`、`reviewLoop` 和 `children`。

TASK：

- `definition.kind` 为 `TASK`，根的 `parentId` 为 `null`，子 TASK 的 `parentId` 为直接父 GROUP ID。
- `definition.execution.dependsOn` 只引用直接同级 GROUP/TASK。
- `definition.execution.loop` 描述唯一执行 Loop。
- `STANDARD` 的 `reviewLoop` 必填，描述该 TASK 实现完成后的独立 `TASK_REVIEW_LOOP`。只有 `LIGHT` 根 TASK 将其设为 `null`；`children` 始终为空。

GROUP：

- `definition.kind` 为 `GROUP`，并在 `definition.children` 中列出直接子节点的 `id`、`kind`、`title` 摘要。
- `definition.decomposition.dependsOn` 只引用直接同级 GROUP/TASK。
- 节点 `children` 递归包含与摘要一一对应的完整 GROUP/TASK 节点，且至少一个。
- 节点 `reviewLoop` 为可空字段。直接子节点终态全部成功后，调度器到达 `GROUP_JOIN`（人类文档称“GROUP 完成点”）。没有直接子项 seam 时设为 `null`，完成点就是 GROUP 终态；存在真实接口兼容、数据/控制流、事务或错误传播 seam 时才配置 `GROUP_REVIEW_LOOP`，Review 成功后才是 GROUP 终态。
- 只有存在真实的同级协调、依赖或汇合边界时才创建 GROUP；单个可独立结果直接使用 TASK。不要为了“每层都 Review”而创建 GROUP 或伪造 seam。

递归示例：

```json
{
  "delivery": {
    "id": "d-order",
    "title": "交付订单能力",
    "summary": "完成服务实现、文档和最终验收",
    "reviewLoop": {
      "ref": "delivery/acceptance-readiness-loop@1",
      "payload": {"goal": "确认订单需求覆盖、整体证据和交付准备度"},
      "resourceClaims": []
    }
  },
  "root": {
    "schemaVersion": 3,
    "skillHints": [
      {
        "name": "springboot-tdd",
        "purpose": "实际处理 Spring Boot 开发或审查时优先采用"
      }
    ],
    "definition": {
      "schemaVersion": 3,
      "id": "g-root",
      "kind": "GROUP",
      "parentId": null,
      "title": "协调订单交付",
      "summary": "汇合并审查所有直接子结果",
      "decomposition": {"dependsOn": []},
      "children": [
        {"id": "g-service", "kind": "GROUP", "title": "完成服务"},
        {"id": "t-docs", "kind": "TASK", "title": "更新文档"}
      ]
    },
    "reviewLoop": null,
    "children": [
      {
        "definition": {
          "schemaVersion": 3,
          "id": "g-service",
          "kind": "GROUP",
          "parentId": "g-root",
          "title": "完成服务",
          "summary": "汇合并审查服务结果",
          "decomposition": {"dependsOn": []},
          "children": [
            {"id": "t-api", "kind": "TASK", "title": "实现接口"},
            {"id": "t-core", "kind": "TASK", "title": "实现核心逻辑"}
          ]
        },
        "reviewLoop": {
          "ref": "group/direct-child-seam-review-loop@1",
          "payload": {"goal": "验证 API 与核心逻辑之间的接口和数据流 seam"},
          "resourceClaims": []
        },
        "children": [
          {
            "definition": {
              "schemaVersion": 3,
              "id": "t-api",
              "kind": "TASK",
              "parentId": "g-service",
              "title": "实现接口",
              "summary": "返回可用接口结果",
              "execution": {
                "dependsOn": [],
                "loop": {
                  "ref": "project/java-task-loop@1",
                  "payload": {"goal": "实现并验证接口"},
                  "resourceClaims": ["project:erp/module:order-api"]
                }
              }
            },
            "reviewLoop": {
              "ref": "task/independent-review-loop@1",
              "payload": {"goal": "独立审查接口 TASK 结果"},
              "resourceClaims": []
            },
            "children": []
          },
          {
            "definition": {
              "schemaVersion": 3,
              "id": "t-core",
              "kind": "TASK",
              "parentId": "g-service",
              "title": "实现核心逻辑",
              "summary": "返回核心业务结果",
              "execution": {
                "dependsOn": ["t-api"],
                "loop": {
                  "ref": "project/java-task-loop@1",
                  "payload": {"goal": "实现并验证核心逻辑"},
                  "resourceClaims": ["project:erp/module:order-core"]
                }
              }
            },
            "reviewLoop": {
              "ref": "task/independent-review-loop@1",
              "payload": {"goal": "独立审查核心逻辑 TASK 结果"},
              "resourceClaims": []
            },
            "children": []
          }
        ]
      },
      {
        "definition": {
          "schemaVersion": 3,
          "id": "t-docs",
          "kind": "TASK",
          "parentId": "g-root",
          "title": "更新文档",
          "summary": "返回完整交付文档",
          "execution": {
            "dependsOn": ["g-service"],
            "loop": {
              "ref": "project/docs-task-loop@1",
              "payload": {"goal": "根据已审查服务结果更新文档"},
              "resourceClaims": ["project:erp/docs:order"]
            }
          }
        },
        "reviewLoop": {
          "ref": "task/independent-review-loop@1",
          "payload": {"goal": "独立审查文档 TASK 结果"},
          "resourceClaims": []
        },
        "children": []
      }
    ]
  }
}
```

先调用 `hierarchy_contract(root_kind="GROUP")` 或 `hierarchy_contract(root_kind="TASK")`，并以返回的实时 schema/example 为最终字段依据。

`prepare_hierarchy` 的 MCP `inputSchema` 复用同一份 schema v3 定义，并以
`oneOf` 同时约束 GROUP/TASK 根节点。宿主应在工具调用前据此拒绝未知字段、
缺失字段和错误节点结构；`loop.payload` 继续保持开放。MCP Adapter 还会在
进入 Controller 前复用完整领域校验，拦截父子关系、同级依赖和子节点摘要
等 JSON Schema 无法完整表达的契约错误。此类错误统一返回
`MCP_TOOL_ARGUMENT_INVALID`，不会创建或替换 `PREPARED` 结果；不要依赖
Controller 事后修正无效 hierarchy。

### 数据库结构基线约定

需求涉及建表、修改字段/索引/约束或删表时，数据库设计属于 baseline 规划职责，
不是 TASK Loop 的运行时设计工作。规划上下文必须在调用 `preview_hierarchy` 前读取
真实当前结构，并在负责该表的 TASK
`definition.execution.loop.payload.databaseChanges` 中逐表声明完整契约：

- 每项包含 `table`、`summary`、`changeType`、`before`、`after`、`migration`
  和 `resourceClaim`；跨项目时同时声明 `projectId`，需要定位时声明 `database` 与
  `schema`；
- `changeType` 使用 `CREATE`、`MODIFY` 或 `DELETE`；CREATE 的 before 为 null，
  DELETE 的 after 为 null，MODIFY 两侧都必须是完整表快照；
- 每个适用快照完整列出表注释、全部字段、主键、唯一约束、索引和外键；字段至少
  明确名称、数据库原生类型、可空性、默认值和注释，不得只列变化字段；
- `migration` 必须明确正向迁移、回滚、历史数据回填、滚动发布兼容与至少一个验证点；
  “无需回填”等结论也要显式记录，不能留空或写占位符；
- 每项 `resourceClaim` 必须精确出现在所属 TASK 的 `resourceClaims` 中，例如
  `db-schema-orders`，用于跨 Delivery 串行化同一数据库结构资源；
- 任何数据库结构或迁移都强制使用 `STANDARD`，不得使用 `LIGHT`。

Controller 对上述保留字段执行结构校验；缺少完整设计时不能生成可确认的 baseline。
每个有数据库变更的 TASK 生成 `database-changes.md` 索引，并在相邻
`database-changes/` 目录为每张表生成字段比较、完整 before/after 快照及迁移方案。
Delivery baseline 和 TASK baseline 只串联这些投影。

冻结 after 是开发 Loop 的唯一表结构事实源。Loop 只执行迁移、适配代码和验证，
不得重新选择字段类型、可空性、默认值、索引、约束或发布策略；如果真实实现必须
偏离冻结契约，返回 `REPLAN_REQUIRED`，按同一 Delivery 的新 Revision 重新生成、
展示和确认数据库 baseline 后再执行。

## 同级启动依赖

`dependsOn` 是直接同级之间的启动屏障，允许 TASK→TASK、TASK→GROUP、GROUP→TASK 和 GROUP→GROUP：

- 来源 TASK 在自己的 TASK Review 成功后满足屏障。
- 来源 GROUP 在自己的实际终态满足屏障：配置了 seam Review 时是该 Review 成功，否则是 GROUP 完成点成功。
- 目标 TASK 的 TASK Loop 等待屏障。
- 目标 GROUP 的子树入口 TASK Loops 等待屏障。

同级依赖必须无环。不要跨 GROUP 引用后代或祖先，也不要把 `dependsOn` 当作文件 scope、产物清单或实现顺序明细。

## Loop 描述

TASK、TASK Review、已配置的 GROUP seam Review 和 Delivery Acceptance/Readiness 使用相同 Loop 描述协议：

- `loop.ref`：执行适配器或 Loop Skill 的稳定引用。
- `loop.payload`：规划层按需生成并原样交给对应 Loop 的不透明输入，包含方向、目标、明确约束、已确认契约和已知验收；Graph 把工作项整理为 hierarchy/DAG，维护依赖、资源、全局进度和结果汇总并完成调度，但不创作业务需求或实现方案。
- `resourceClaims`：需要排他占用的精确资源锁。

不要把 `scope`、`developmentPlan`、`testCommands`、`gateLevel` 或 `requiredSkills` 放进外层 definition，也不要为了让规划显得完整而把普通文件名、实现类、内部方法、代码结构或详细测试方案塞进 payload。Loop 在运行时自行形成这些内容。仅当需求明确指定或用户确认的外部兼容契约固定了精确标识时，payload 才保留该需求事实。资源声明是精确键；相同键互斥，不做 glob、目录包含或文件写授权判断。

### 通用接口投影约定

需求涉及接口契约时，在负责该接口的 TASK
`definition.execution.loop.payload.interfaces` 中逐项显式声明：
该列表也简称为 `payload.interfaces`。

- 公共字段：`protocol`、`name`、`summary`、`changeType`、`before`、`after`；
- `protocol` 是开放字符串；HTTP、Dubbo、gRPC、GraphQL、消息等只是示例；
- `changeType` 使用 `CREATE`、`MODIFY` 或 `DELETE`；
- 每个适用的 before/after 快照都包含完整入参 `request` 与出参 `response`；
- `request`/`response` 的标准字段形状是字段列表
  `[{name,type,required?,maxLength?,description?,example?}]`，或带整体类型的
  `{type,description?,fields|properties:[...]}`；字段名必须是实际契约字段，
  不要把分类或包装元数据伪装成业务字段；
- 无入参或无出参使用空列表 `[]`，投影分别明确显示“无入参”或“无出参”；
- HTTP 请求可按 `headers`、`pathParameters`、`queryParameters`、`body` 等
  位置组织实际字段，投影会展开非空字段并忽略空容器；Controller 契约也可用
  `controllerReturnType`、`controllerReturnFields`，投影会转换成 VO 整体类型
  与实际字段；`wireType`、`frameworkEnvelope`、`wrapping` 和 `Rs` 包装信息
  一律忽略，不输出成字段或接口说明；
- 通用快照可用 `identifier` 保存稳定、可定位的调用标识；
- HTTP 快照另带 `method` 与 `path`；
- Dubbo 快照另带 `service`、`method`，并可显式提供 `signature`。

需求分析时可以从真实代码、OpenAPI、Controller/DTO、IDL 或服务定义提取
before 候选，并根据需求形成 after；确认 TASK 时必须把两者作为显式契约
评审。每个适用快照的 `request` 和 `response` 都应给出完整契约，包括可
核对的参数名称、类型、是否必填、最大长度、简介和示例值，不得只列变化字段，也不得只写
“参考代码”或“待实现”。`CREATE` 的 before、`DELETE` 的 after 使用空值。
调用标识保持可定位：HTTP 可使用 `method + path`，Dubbo 可使用
`service + method`，其他协议使用 `identifier`。规划时以 `hierarchy_contract` 返回的
`projectionGuidance.interfaces` 为实时约定。

控制器把每个 TASK 的声明确定性写入该 TASK 的
`work-items/<task-id>/interfaces.md` 索引，并在相邻 `interfaces/` 目录中为
每个接口生成一份字段级详情；TASK baseline 与 Delivery baseline 只串联索引。
完全没有声明的 TASK 不生成这些文件、目录和导航。控制器不动态扫描实现代码
或隐式推算契约；TASK/Review Loop 可用真实代码验证 after。接口详情在完整
请求和响应表中逐字段比较 before/after，标记新增、修改、删除或未变。HTTP
按 Path、Query、请求头、请求体和响应参数分区；Dubbo 按接口、方法、调用参数
和返回结果分区，并展示最大长度。入参表展示类型、必填性、说明和示例值，出参
表不展示必填性；只有真正修改的属性使用“修改前
→ 修改后”，新增或删除字段只显示存在的一侧，删除值使用 Markdown 删除线。
字段仍是 Loop 的不透明输入，不参与依赖、资源锁或 Graph 调度。

冻结 baseline 中的 after 是开发接口和后续 Torna 发布的唯一事实来源。实现与
Torna 必须保持相同的方法、路径或签名、字段层级、类型、必填、最大长度、说明
和示例值；不得在开发完成后从另一套输入生成内容不同的接口文档。

## Skill Hint 分阶段使用

需求阶段若用户明确指定 Skill，在 `root.skillHints` 登记一次：

- 明确指定表示适用且可用时应在相应阶段原生调用，但仍不是 Controller 的硬成功门禁；只有当前阶段不适用或宿主不可用时才跳过，并且不得伪造已使用。
- 规划阶段只在它能帮助方向、约束、验收、风险或 TASK 边界时预触发；实现类 Skill 多数留给 TASK。不要让规划 Agent 为 Skill 强制定义文件、方法或完整实现。
- 不把提示静态分配到 TASK、TASK Review、GROUP seam Review、Delivery Acceptance/Readiness 或 Gate，也不因提示新增 Graph 节点；各 Loop 按实际任务判断适用阶段。
- 自动 assignment、手动 TASK action、manual handoff 与 `loop_context` 会把具体 catalog 名和建议性原生触发提示传给 receiver；Codex 使用 `$skill-name`，Claude Code 使用原生 Skill tool，其他宿主使用自己的原生 Skill 入口。
- 每个 TASK、TASK Review、已配置的 GROUP seam Review 或 Delivery Acceptance/Readiness Loop 结合真实任务和宿主可用 Skill 独立判断阶段；用户明确指定的 Hint 适用且可用时应优先触发，也可以使用其他必要 Skill。
- 调度器不查询 Skill catalog、不校验激活证据，也不因某条提示未使用而判定失败。

无合适提示时使用空数组，不要猜测 Skill。业务硬条件由对应 Loop 的 payload/验收协议表达，不要伪装成 Skill Hint。

## 准备与冻结

`preview_hierarchy` 与手动选择都不绑定 Delivery 工作区。preview 先把需求登记为 `CHOICE_READY`，生成共享 SQLite、根总览和完整投影；自动按钮由 `select_execution_mode(AUTOMATIC)` 立即记录选择与项目授权，工作区策略固定为 `CURRENT_WORKSPACE_SERIAL`。同一实际 workspace 可以绑定多个 Delivery，但每个 Delivery 使用独立分支，同一物理 checkout 一次只运行一个 Delivery。已有 owner 时，只有已选择 `AUTOMATIC` 的后续 Delivery 标记为 `QUEUED` 并保存自动 continuation；前一个 Delivery 进入 Run 终态，或到达 `RECORD_USER_CONFIRMATION`，并形成可验证业务 commit、working tree/index clean、HEAD 与冻结 binding 一致且所有 receiver/reservation 安全释放后，宿主自动消费 `automaticHostPreparation`，必要时 stash 既有业务改动、创建或切换目标分支，并以明确 `rootId` 和双 fingerprint 调用 `resume_execution_mode`。待用户确认只释放物理 turn，不标记完成；人工 Graph 也适用相同边界。手动冻结同样持久化 Delivery、不可变 Revision、完整 hierarchy、双 fingerprint 和投影，但保持 `HANDOFF_READY`，不进入自动队列或创建 Run/workspace binding。资源冲突、owner dirty、未合并状态、HEAD 漂移或无法证明释放时保持排队。现有 linked checkout 只作为普通 current workspace，不自动创建新 worktree。所有继续调用都显式传创建响应中的 `rootId`；控制状态隔离不承诺文件、index 或 HEAD 隔离。Controller 自身始终不执行 Git 写操作。

`preview_hierarchy`、`prepare_delivery_revision` 与 `create_manual_handoff` 的 `hierarchy` 也可改用 `hierarchy_file` 传入：当层级结构较大或 payload 详细、难以一次性内联写出正确 JSON 时，先用 Write 把 JSON 写到工作区文件（如 `.layered-delivery/staging/hierarchy.json`），用 `python -m json.tool` 校验，再调用时只传 `hierarchy_file`（工作区相对路径）。控制器在工作区沙箱内读取并解析该文件，等价于内联 `hierarchy`；两者二选一，同时给或都不给都会被拒绝，路径穿越/符号链接/跨盘也会被拒绝。

新 AUTOMATIC Delivery 的 `gitBinding.branchRef` 必须是**该 Delivery 自己的分支名**，不得从其他活动或已完成 Delivery 复制 binding。宿主只在前一个 Delivery 已形成可验证 commit、当前 checkout clean、HEAD 未漂移且 receiver 安全释放后创建或切换该分支；若目标分支已被其他 worktree checkout，则停止并要求用户先安全释放，不自动创建另一个 worktree。

preview 会先通过 `pendingInteraction.kind=DEVELOPMENT_BASELINE` 确认基线，再在 `EXECUTION_MODE` 交互中回显冻结后的 `baseRef`/`integrationTarget`。若当前 checkout 位于干净的进行中 feature（如 `feature/m_lf_protein`），Controller 可提供 `NEW_FROM_CURRENT_BRANCH`：用户提供新的子分支名（如 `feature/m_lf_mprotein_409`），冻结结果以父 feature 当前 HEAD 为 `baseCommit`，父 feature 同时作为 `baseRef` 与 `integrationTarget`。宿主在上述串行释放边界把当前 checkout 切到该子分支；该选择本身就是显式 stacked Delivery 授权。工作区存在业务 dirty 时不提供，也不得通过创建 worktree 绕过。其他“基于进行中分支”的情况必须通过 `workspace_status(base_ref=...)`、基线交互或 hierarchy 的 `gitBinding` 明确。

### Git 工作区设置

先检查首次 `workspace_status`：

#### `CURRENT_WORKSPACE_SERIAL`

- 自动 Git Delivery 只使用 `CURRENT_WORKSPACE_SERIAL`。`select_execution_mode(AUTOMATIC)` 立即持久化选择。非队首返回 `status=QUEUED`、队列位置和无需再次确认的 continuation；不得重选模式。轮到队首后读取 `automaticHostPreparation.actions`：无 dirty 时创建或切换冻结分支；有既存业务 dirty 且无冲突时先核对精确指纹并 stash tracked/staged/untracked 内容，pathspec 必须排除 `.layered-delivery/**`，再准备分支；最后以明确 `rootId` 与双 fingerprint 调用 `resume_execution_mode`。同一物理 checkout 一次只运行一个 Delivery，不能以多个 `rootId` 的控制状态隔离为由并行写文件、index 或 HEAD。
- Controller 不创建、复用或预留新 worktree。当前目录本身是既有 linked checkout 时，也只按普通 current workspace 校验和调度。多项目 Delivery 的所有 `READ_WRITE` scope 必须同时满足 commit、clean、HEAD 与释放条件；任一 scope 冲突或漂移就停止切换。

#### `workspaceProvenance` 与基线发现

- 每次返回的 `workspaceProvenance` 都必须体现当前实际 workspace 的来源：`hostAdapterId`、`workspaceRoot`、`topology`、`selectionSource`、`baseRef`、`baseCommit`、`baseHeadCommit` 与 `integrationTarget`。`topology` 仅区分 `PRIMARY_CHECKOUT` / `LINKED_CHECKOUT` 以便诊断，不改变 `CURRENT_WORKSPACE_SERIAL` 调度、基线或 adoption 规则。`baseCommit` 是当前 HEAD 与所选主线的 merge-base；`baseHeadCommit` 是检查时所选本地或 remote-tracking 主线的 HEAD。无论当前目录最初如何创建，都不得省略或猜测这些字段。
- Delivery 冻结后，`gitBinding` 中的 `baseRef/baseCommit/integrationTarget` 是权威基线；后续 `workspace_status` 仍返回 `workspaceProvenance`，并以 `selectionSource=FROZEN_GIT_BINDING` 区分冻结事实与当次主线发现，`baseHeadCommit` 继续表示当次可见的主线 HEAD。
- 基线发现顺序固定为：调用方通过 `workspace_status(base_ref=...)` 提交的宿主显式选择（`HOST_SELECTED`）、有效的远端默认引用 `origin/HEAD`（`ORIGIN_HEAD`），再依次降级到本地 `main`、本地 `master`（对应 `LOCAL_MAIN_FALLBACK`、`LOCAL_MASTER_FALLBACK`）；全部无效时停止并要求明确选择。这里的远端引用是当前仓库已经持有的 remote-tracking ref，Controller 不执行 `fetch`。不会额外枚举或硬编码 `develop`、`origin/develop` 或其他分支名。显式 `base_ref` 必须能解析为本地分支或 `origin` tracking ref，并同时成为建议的 `baseRef` 与 `integrationTarget`。

#### 分支 adoption 与 dirty 确认

- 不能仅凭 feature 分支名判断“当前分支已是本 Delivery 的独立分支”。该分支不得绑定其他 Delivery 或被其他 checkout 使用，且基线关系必须有效。`BRANCH_BOUND_TO_OTHER_DELIVERY`、`BRANCH_USED_BY_HISTORICAL_DELIVERY` 或 `BRANCH_IN_USE_BY_OTHER_CHECKOUT` 都停止切换；不得接管已有分支，也不得自动创建另一个 worktree 绕过。
- 当前实际 checkout/worktree 干净且唯一归属该 Delivery 时，`branchAdoption.state=READY` 并返回 `suggestedGitBinding`。把建议中的 `branchRef`、`baseRef`、`baseCommit`、`integrationTarget` 原样写入 `delivery.gitBinding`。
- 当前 checkout/worktree 已有业务 diff 时，通用 adoption 探测只返回 `candidateGitBinding` 和 `DIRTY_CONFIRMATION_REQUIRED`。`.layered-delivery/**` 是 Controller 控制面，不计入业务 dirty 状态。只有选择当前分支作为新 Delivery binding 时，才向用户展示其余变更并确认全部属于本 Delivery，再把精确 `workingTree.stateFingerprint` 作为 `confirmed_dirty_state_fingerprint` 回传。选择 `NEW_FROM_MAINLINE` 或另一个本地分支时不做归属确认，由队首 `automaticHostPreparation` 精确 stash 后切换；指纹变化必须停止并重新探测。

#### 宿主承接与分支命名

- Codex、Claude Code 或其他宿主只在当前实际 checkout 承接：确认前一个 Delivery 已形成可验证 commit、没有在途 receiver、working tree/index clean 且 HEAD 未漂移后，按冻结 `branchRef/gitBinding` 创建或切换分支，再以显式 `rootId` 重查。宿主不能在同一 checkout 同时启动第二个 Delivery receiver，也不能自动创建新 worktree。
- feature 分支名称由宿主或用户已有分支策略决定；Delivery Graph 不生成 `feature/m_lf_` 等固定前缀，只冻结实际 checkout 的本地分支名。

#### `projectScopes` 与 `gitBinding` 归属

- `projectScopes.workspaceRoot` 在 CHOICE_READY 快照中保存 preview 时的仓库锚点。多仓 prepare/runtime 会以 Git common directory、精确 `branchRef` 和冻结基线只读解析每个显式 scope 的当前实际 workspace，并在 `verifiedProjectScopes` 中返回实际 `workspaceRoot`。普通单仓 Delivery 可以省略数组；runtime 从顶层 `delivery.gitBinding` 与已绑定 Delivery workspace 合成一个 `id=primary`、`access=READ_WRITE` 的验证 scope。receiver 不得对非当前有效目录执行开发，也不得自行创建或切换分支。
- `gitBinding` 只属于 Delivery。同一 Delivery 的全部 TASK 共享该 feature workspace 和分支；不要为 TASK 创建、声明或切换内部 Git 分支。获得相应 Git 写入授权后，各 TASK 可以只 `git add` 并 `git commit` 自身 scope 的变更，在同一 Delivery 分支上形成独立 commit；Git index/commit 写入必须串行。

#### Delivery turn 切换

- 在某个 Delivery 运行期间收到另一个独立 Delivery 时，登记/规划新控制状态、记录 AUTOMATIC 选择并保留其 `rootId`；Controller 把它标记为 `QUEUED`，不让它在该 checkout 开始代码执行。当前 Delivery 进入 Run 终态，或到达 `RECORD_USER_CONFIRMATION`，并已形成可验证业务 commit、工作树与 index clean、HEAD 未漂移且 receiver/reservation 安全释放时，宿主才自动续调队首。待用户确认的最终业务验收可稍后按旧 `rootId` 补录；若用户要求修改，下一 Revision 重新排队。资源冲突、owner dirty、HEAD 漂移或释放状态不明时保持排队，不能以 stash owner 改动、新 worktree或另一个独立执行任务绕过。

#### 手动开发内容包

- 手动开发生成完整冻结内容包：固定写入本需求的 `.layered-delivery/<delivery-id>/`，包含与自动开发相同的 overview、baseline、progress、acceptance、revisions、work-items，以及自包含 `handoff-<fingerprint>.md`；同时必须生成共享 `.layered-delivery/scheduler.db` 与根 `overview.md`，把需求登记为 `HANDOFF_READY`。不创建共享 `handoffs` 目录；交接阶段不创建 Graph Run 或 workspace 绑定，不指定 Agent 或接收任务，也不创建 worktree。用户切换任意 CLI 后，在实际开发工作区调用 `start_manual_handoff`：基线未漂移时控制器绑定 workspace、启动同一冻结 Graph；漂移时先返回待确认基线且不写运行状态，binding 改变才生成下一不可变手动 Revision。启动后只让 TASK 实现走不带 AUTO reservation/decision fingerprint 的 MANUAL claim，完整 Review 和最终确认继续沿用统一 AUTO 协议。

#### 停止条件

- 当前 workspace 缺少 `gitBinding`、当前分支不匹配、HEAD 不继承 `baseCommit`，本地/`origin` 同名主线不再包含该基线，存在业务 dirty、资源冲突，或前一个 Delivery 尚未形成可验证 commit/安全释放时，`prepare_hierarchy` 与运行工具必须停止。Controller 不代替宿主运行 `git worktree add`、`switch`、`commit`、`merge` 或 `push`，宿主也不得自动创建新 worktree 绕过停止条件。

### 跨本地仓库的同一 Delivery

主需求位于 `project-api`，但需要同时修改 `project-provider`、`project-consumer` 等本地仓库时，不拆成多个 Delivery。使用 `delivery.projectScopes` 声明精确范围：

MCP 根固定只限制当前会话的主工作区锚点，不把同一 Delivery 限制为单仓库。只要这些仓库属于同一业务目标，就在初始 hierarchy 中一次声明完整 `projectScopes`，再让不同 TASK 通过 payload/resource claim 指向各自项目；不得为第二仓库另起 Delivery，也不得把“无法在当前会话切换 MCP 根”误报成跨仓库交付失败。只有第二仓库属于独立业务目标，或用户明确要求拆分交付时，才创建另一个 Delivery。

```json
{
  "projectScopes": [
    {
      "id": "project-api",
      "workspaceRoot": "/workspace/project-api",
      "access": "READ_WRITE",
      "gitBinding": {
        "branchRef": "feature/cross-project-contract",
        "baseRef": "main",
        "baseCommit": "<project-api 的完整基线提交>",
        "integrationTarget": "main"
      }
    },
    {
      "id": "project-provider",
      "workspaceRoot": "/workspace/project-provider",
      "access": "READ_WRITE",
      "gitBinding": {
        "branchRef": "feature/cross-project-contract",
        "baseRef": "master",
        "baseCommit": "<project-provider 的完整基线提交>",
        "integrationTarget": "master"
      }
    },
    {
      "id": "project-consumer",
      "workspaceRoot": "/workspace/project-consumer",
      "access": "READ_WRITE",
      "gitBinding": {
        "branchRef": "feature/cross-project-contract",
        "baseRef": "main",
        "baseCommit": "<project-consumer 的完整基线提交>",
        "integrationTarget": "main"
      }
    }
  ]
}
```

- scope 必须包含当前 Delivery 工作区；每个 `workspaceRoot` 必须唯一且存在。
- `READ_WRITE` Git 项目的 `branchRef` 必须完全同名；不同仓库可使用各自的 `main`/`master`、基线提交和最终集成目标。
- prepare 只读校验每个仓库的分支与基线。响应中的 `requiredProjectAuthorizations` 是本 Revision 的完整授权清单。
- freeze 必须提交完全相同的项目 ID 集合；缺失、额外或重复 ID 都拒绝。该授权只限定调度 scope，不授权 Git commit、push、merge 或发布。
- 后续 Revision 新增 `project-consumer` 等项目时，必须重新 prepare 并授权完整新清单；不能沿用旧 Revision 的授权。

新建、修改或无法安全续接 `PREPARED` 结果时：

1. 调用 `hierarchy_contract(root_kind=...)`。
2. 按返回的 schema 和 example 创建完整 hierarchy。
3. 调用 `preview_hierarchy`。该调用先登记 `CHOICE_READY`，生成共享 `.layered-delivery/scheduler.db`、根 `overview.md`、Delivery 的 `overview.md`、`baseline.md`、`progress.md`、`acceptance.md`、`revisions.md`，以及 `humanArtifacts.workItems` 指向的全部节点投影；响应的 `controlStateCreated=true` 只表示这些选择前控制状态已创建。它不绑定 workspace、不生成 Graph Run 或 worktree。只有响应同时满足 `status=CHOICE_READY` 和 `artifactsReady=true`，才可向用户概述计划并进入选择。
4. 完整清单后直接进入执行方式确认，不追加 receiver 路由建议。
5. Controller 是交互文案的唯一所有者。宿主统一读取 `pendingInteraction`，并优先把其 `options` 机械映射到 `AskUserQuestion`（Claude Code、ZCode）或 `request_user_input`（Codex），保留顺序、ID、默认项、推荐项、标签和说明。只有映射工具在当前上下文不可调用时，才允许逐字显示该对象的 `markdown`。`developmentBaseline` 与 `executionChoice` 是当前兼容别名，宿主不得把它们当成两个并存问题。
6. `pendingInteraction.kind=DEVELOPMENT_BASELINE` 时，选择本地分支、`NEW_FROM_MAINLINE`，或 Controller 实际返回的 `NEW_FROM_CURRENT_BRANCH`，把交互给出的 hierarchy/Graph/Revision/context 指纹和真实确认人传给 `confirm_development_baseline`。两个 NEW 选项都提交新 `branch_name`；stacked 选项必须原样使用交互冻结的父 feature HEAD，不自行换父分支。只有 adoption 当前脏分支时，才在用户确认全部现有改动属于本 Delivery 后回传精确 `workingTree.stateFingerprint`；选择另一个分支时延迟到队首自动 stash，且 dirty 当前 feature workspace 仍不允许创建 stacked 子分支。探测 Git HEAD、基线、权限或实现异常时必须 fail closed；非 Git 工作区才正常跳过。多 Git 项目不会从顶层偏好自动推断 secondary scope，所有 Git scope 必须显式提供完整 binding。
7. `pendingInteraction.kind=EXECUTION_MODE` 时，原生对话框仍允许直接输入文字，但不为它创建“其他”选项。自由输入按 `freeformInput.nextAction=CONTINUE_REQUIREMENT_DISCUSSION` 继续需求沟通；需求发生变化后重新调用 preview。用户只是提问且需求未变时，回答后保留当前 fingerprint，并再次展示同一待处理交互，不得主动退回文本交互。`AUTOMATIC` 是默认和推荐项，`MANUAL` 是第二项。
8. 用户点选执行方式后，把选项 ID、双 fingerprint、`requiredProjectAuthorizations` 的精确项目 ID 和真实确认人一次性传给 `select_execution_mode`：
   - `AUTOMATIC`：`select_execution_mode` 先记录业务确认，并只采用 `CURRENT_WORKSPACE_SERIAL`。非队首返回 `QUEUED` 与 `deliveryQueue.continuation`；前序 Delivery 安全释放后，宿主按 `workspacePreparation.automaticHostPreparation` 机械执行 stash（需要时）、创建/切换当前分支，再用明确 `rootId` 和原双 fingerprint 调用 `resume_execution_mode`，不再次询问用户。未合并、owner dirty、资源冲突、HEAD 漂移或释放状态不明时保持排队/等待；不得创建新 worktree 或独立工作区任务绕过。
   - `MANUAL`：生成同结构开发包和自包含 handoff，登记 `HANDOFF_READY`；交接阶段不创建 Graph Run、workspace 绑定、任务或 worktree。宿主展示 `manualHandoff.receiverPrompt`，不得改写；同一提示词已经嵌入 `manualHandoff.path` 指向的文件。接收宿主必须先调用 `start_manual_handoff`，再创建独立原生 child；child 以显式 receiving context 和新 `operation_id` 按 frontier MANUAL claim TASK，不携带 AUTO reservation 或 decision fingerprint。Review 不允许 MANUAL，必须走统一 AUTO 预留与派遣。
9. 自动或手动按钮选择本身就是该初始 Revision 的一次业务授权，并由 `select_execution_mode` 立即持久化，不接受第二个用户确认。`CURRENT_WORKSPACE_SERIAL` 若返回 `PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION`，宿主只在可验证 commit、clean、HEAD 未漂移与 receiver 安全释放边界完成分支动作，再以明确 `rootId` 与双 fingerprint 调用 `resume_execution_mode`。不得重试选择，也不得重放用户交互、初始 `prepare_hierarchy`、`freeze_hierarchy` 或 `create_manual_handoff`。需求内容改变会清除未执行的 AUTOMATIC 选择并重新生成交互。
10. 自动初次冻结后当前 Delivery Revision 的 Git/project binding、依赖、资源和拓扑固定，所有 TASK requirement revision 1 均为 `FROZEN`。手动开发把相同需求内容冻结为 SQLite 已登记的 `HANDOFF_READY` 可移植快照，返回 `requirementSnapshotStatus=FROZEN`；这不等于 Graph `FROZEN`。接收方调用 `start_manual_handoff` 时若 Git binding 已漂移，响应以 `manualStartState=BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION` 返回 `pendingInteraction`，且不创建 Run/绑定 workspace；宿主用精确上下文调用 `confirm_development_baseline`。binding 改变时取得同一 Delivery 的新 Revision 和新双指纹，未改变时恢复原 Revision，然后重试。基线未漂移时，同一 Revision 进入 `executionMode=manual` 的 Graph Run，所有 TASK requirement 同样冻结并接受完整调度、Review 与最终验收。

## 多 Delivery 工作区与资源串行化

同一个实际 workspace 可以绑定多个 Delivery，Graph 状态、Revision、run 和验收始终按 `rootId` 路由，但执行策略只有 `CURRENT_WORKSPACE_SERIAL`。每个 Delivery 保持独立分支；只有已选择 `AUTOMATIC` 的后启动或后发现 Delivery 标记 `QUEUED`，前一个 Delivery 进入 Run 终态，或到达最终用户确认边界，并形成可验证业务 commit、working tree/index clean、HEAD 未漂移且 receiver/reservation 安全释放后自动续调队首。`CANCELLED` 的 owner 在安全边界独立释放，不需要归档；终态状态不继续返回过期 `workspaceRebase`。手动冻结 Delivery 保持 `HANDOFF_READY` 并等待接收方显式启动，手动 Run 到达相同安全边界后也可 commit 并让出 checkout。资源冲突、owner dirty、未合并状态或 HEAD 漂移时保持排队，不创建新 worktree，也不允许跨 Delivery 并行。

### 同文件/同区域：声明式串行化（`resourceClaims`）

要让"改同一文件或同一逻辑区域"的 TASK 跨 Delivery **串行**（后一个等前一个完成），在相关 TASK 的 `execution.loop.resourceClaims` 里声明**同一个锁键**。`resourceClaims` 是**精确匹配的排他锁键，跨所有 Delivery 全局生效**，三层强制：frontier 对有重叠 claim 的 Ready Loop 不发 `DISPATCH_LOOP`（其 `resourceConflicts` 列出持有方 `<rootId>/<nodeId>`）；`plan_dispatch_batch` 预留返回 `DISPATCH_RESERVATION_CONFLICT`；`dispatch_loop` claim 返回 `SCHEDULER_RESOURCE_CONFLICT`。后启动或后发现者等待，必要时先暂停；工作区冲突即使没有相同 claim 也不能绕过默认串行边界。

**锁键命名约定**：稳定的小写 token，**不要用原始路径**（Controller 不做前缀/路径推导）。例如同改订单服务用 `orders-service`；同改某文件用 `file-src-orders-service-py`；同改库表结构用 `db-schema-orders`。键是精确相等匹配。

**没有运行时同文件检测**：Controller 不从路径推断资源冲突。串行策略只保证同一 checkout 不同时运行两个 Delivery，不代表它能识别跨分支逻辑冲突。若两个 TASK 可能改同一处，就在两者都声明同一个 claim，并在最终集成时处理基线差异。

示例：Delivery A 和 Delivery B 各有一个 TASK 改订单服务，两者都在 `execution.loop.resourceClaims` 写 `"orders-service"` → A 的 TASK 先 claim，B 的 TASK 在 frontier 显示 `resourceConflicts: ["<A-rootId>/<A-nodeId>"]` 并等待，A 完成释放后 B 才派发。被串行的 TASK 不是失败，是排队；持续读 frontier 即可看到它的 `resourceConflicts` 清空后变为可派发。

### 基线陈旧 rebase 恢复（0.35.0 起）

某 Delivery 冻结的 `baseCommit` 落后于其 `integrationTarget`（例如别人已把 main 合并前进）时，`workspace_status` 会在顶层 `workspaceRebase` 带出**可恢复 advisory**：

```json
{"workspaceRebase": {"required": true, "frozenBaseCommit": "<冻结基线>", "currentBaseCommit": "<当前 integrationTarget HEAD>", "integrationTarget": "main", "nextAction": "REBASE_DELIVERY_BRANCH_ONTO_CURRENT_BASE_THEN_PREPARE_DELIVERY_REVISION"}}
```

宿主/协调器看到它后的恢复流程（Controller 不执行 git）：

1. 先暂停该 Delivery 在当前 workspace 里在途的 claimed Loop（`pause_loop`），避免 rebase 干扰运行中的工作；
2. 在当前 workspace 内把该 Delivery 的 `branchRef` rebase 到 `currentBaseCommit`（如 `git rebase --onto <integrationTarget HEAD> <frozenBaseCommit> <branchRef>`，或 merge）；冲突由宿主解决，无法解决则相关 Loop 按 `BLOCKED`(`LOOP_BLOCKED`/`EXTERNAL_AUTHORITY`) 上报，不要静默换分支；
3. 用新的 `gitBinding`（`baseCommit` = rebase 后 HEAD 与主线的 merge-base）调用 `prepare_delivery_revision`（连续性 `ACTIVE_LOOP_REPLAN` 或 `USER_EXPLICIT_SAME_DELIVERY`）重锚基线；`preparing=True` 重验 fork-point，旧 run 被 supersede；
4. 在新 Revision 上恢复执行（`resume_execution_mode` / 继续 frontier）。

约束：Controller 永不做 git 写；rebase 由宿主执行；重锚走 Revision；绑定 workspace 不可变；rebase 期间在途 Loop 须 pause/释放。`workspace_status` 只检测并**发出 advisory**，不自动 rebase。
