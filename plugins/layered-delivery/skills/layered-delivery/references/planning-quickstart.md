# 递归 Graph 规划

用于只读预览新的软件交付 Graph、生成手动开发内容交接文件，或准备、调整和冻结自动执行 Graph。

## 准备结果续接

- 每次收到新的用户需求，先判断它是否明确要求继续、修改或恢复当前 `delivery.id`。**新用户需求默认属于新 Delivery**；不同工单、不同业务目标、用户明确称为“新需求/独立需求”，或没有明确引用当前 Delivery，均不得修改当前 Delivery。
- 不得仅因 `workspace_status` 返回旧 Delivery 就进入 Revision。只有用户明确要求修改或继续该 Delivery 时，才允许调用 `unfreeze_task_requirement` 或 `prepare_delivery_revision`；Agent 的语义判断不能替代这项用户连续性授权。
- 当前上下文仍保留最近一次 `preview_hierarchy` 响应和原始 hierarchy 时，复用其中的双 fingerprint 与完整清单；需求未变时不要重复 preview，回答用户问题后重新展示同一组选项。
- 初次开发前用户修改需求时，更新 hierarchy 并重新调用 `preview_hierarchy`；只使用新响应的 fingerprint，不复用旧值。`prepare_hierarchy` 只在用户已经选择自动执行且实际开发工作区就绪后调用。
- 初次冻结后、最终用户验收前用户修改依赖、项目范围、资源或拓扑时，保持相同 `delivery.id` 调用 `prepare_delivery_revision`，不得重新调用初始 prepare，也不得创建另一个 Delivery ID。用户明确要求继续同一 Delivery 时提交 `continuity_basis=USER_EXPLICIT_SAME_DELIVERY`；只有当前 Graph 已记录 `REPLAN_REQUIRED` 时提交 `ACTIVE_LOOP_REPLAN`。路径、分支和旧 Delivery 状态都不是连续性依据。
- 用户给出工单号、需求号等稳定外部标识时，将其规范化写入 `delivery.requirementKey`。同一 key 只能映射一个稳定 `delivery.id`；Controller 还会从 Delivery ID/标题识别常见 `PROJECT-123` 工单号，在 preview 与最终写入两处拒绝换 ID 后重复冻结。
- `HANDOFF_READY` 手动需求变化时不创建新 Delivery，也不调用只适用于自动 Graph 的 `prepare_delivery_revision`。保持相同 `delivery.id` 重新 preview 后，再调用 `create_manual_handoff`，同时提交 `expected_current_revision`、`continuity_basis=USER_EXPLICIT_SAME_DELIVERY` 和非空 `revision_reason`；旧手动 Revision 标记为 `SUPERSEDED`，新 handoff 与当前投影继续位于原目录。
- `prepare_delivery_revision` 只生成待确认候选，不替换当前 hierarchy 或旧 run，不应触发宿主通用确认弹窗；用户在完整范围和授权清单上选择自动执行或手动开发，才是该 Revision 唯一一次业务确认。
- 当前上下文不再持有精确 fingerprint 或原始 hierarchy 时，不从旧投影反推机器输入，也不猜测旧值；重新收集需求并 preview 后再请求执行方式确认。

## 先按真实改动判断保障档

保障档由规划 Agent 根据真实仓库、预计或已经存在的 diff、接口与数据边界、运行环境和验证路径判断，不由 Python Controller 解释业务语义，也不要求用户额外选择：

| 保障档 | 适用条件 | Graph |
|---|---|---|
| `LIGHT` | 单一局部内部改动；影响边界明确；定向测试可覆盖；不触及公共/跨模块接口、数据库或迁移、权限/安全/隐私、资金、并发、生产部署或不可逆副作用 | 一个根 `TASK_LOOP → USER_CONFIRMATION`，不创建 TASK/Delivery Review |
| `STANDARD` | 多 TASK/多项目、任何关键边界、影响扩大或无法可靠判断 | 完整 TASK/GROUP/Delivery Review |

`LIGHT` 必须同时满足：

- 根节点是唯一 TASK；`delivery.assuranceProfile=LIGHT`。
- `delivery.assuranceRationale` 用用户当前语言记录基于真实改动内容和影响范围的判断，不写“需求很简单”一类空泛结论。
- `delivery.reviewLoop=null` 且根 TASK 的 `reviewLoop=null`。
- TASK payload 明确定向验证；执行中发现范围扩大时返回 `REPLAN_REQUIRED`，用同一 `delivery.id` 准备 `STANDARD` Revision。

省略 `assuranceProfile` 时安全回退为 `STANDARD`。`STANDARD` 不得因为代码行数少就降级；修改认证判断、数据库字段、公共接口或生产配置，即使只有一行也不是 LIGHT。

## 选择根节点

- 一个可独立调度结果：使用根 `TASK`。
- 多个结果需要依赖、并行、汇合或分组审查：使用根 `GROUP`。

工作项类型只有 `GROUP` 和 `TASK`。Delivery 不作为工作项层级，而是整个 Graph、最终 Review 和用户验收的顶层边界。GROUP 可递归混合包含 GROUP/TASK；TASK 是唯一执行叶子。

按调度关系拆分，不按文件数量拆分。一个 TASK Loop 可以覆盖一个模块、多个模块或多个项目，只要它能作为整体返回标准终态。GROUP 是动态、可选的协调与 Review 边界，可以多层、平行，也可以完全不存在；不要为表现项目/模块目录，或只包裹一个 TASK，而强制增加 GROUP。

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
      "ref": "delivery/independent-review-loop@1",
      "payload": {"goal": "独立审查完整 Delivery"},
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

`delivery.requirementKey` 是可选但在用户提供外部工单号时必须声明的业务身份；它与 `delivery.id` 一对一绑定。`delivery.id` 是稳定的 Delivery/Graph 标识，也是需求投影目录的 namespace。工作区全部 Delivery 的入口位于 `.layered-delivery/overview.md`，这里只列 Delivery 标识、标题、状态、更新时间和详情链接；该 Delivery 自己的 TASK 进度与 GROUP 数量位于 `.layered-delivery/<delivery-id>/overview.md`。`STANDARD` 的 `delivery.reviewLoop` 在根终态之后执行；`LIGHT` 将其设为 `null`，根 TASK 成功后直接进入用户确认。

同一需求的所有人类文件共用 `.layered-delivery/<delivery-id>/`。自动与手动开发都生成 overview、baseline、progress、acceptance、revisions 和同结构 work-items；手动开发另有 `.layered-delivery/<delivery-id>/handoff-<fingerprint>.md`，包含完整 schema v3。不得创建跨需求共享的 `.layered-delivery/handoffs/`。手动包以双 fingerprint 冻结需求内容并在 SQLite 登记为 `HANDOFF_READY`，但不构成 Graph Run 状态；Graph 是否 prepare、freeze 或运行仍只以 MCP 返回和 SQLite 事件链为准。

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
- 节点 `reviewLoop` 必填。直接子节点终态全部成功后，调度器到达 `GROUP_JOIN`（人类文档称“GROUP 完成点”），再派发该层 `GROUP_REVIEW_LOOP`；Review 成功才是 GROUP 的终态。
- 只有存在真实的同级协调、汇合或独立分层审查边界时才创建；单个可独立结果直接使用 TASK。

递归示例：

```json
{
  "delivery": {
    "id": "d-order",
    "title": "交付订单能力",
    "summary": "完成服务实现、文档和最终验收",
    "reviewLoop": {
      "ref": "delivery/independent-review-loop@1",
      "payload": {"goal": "独立审查完整订单 Delivery"},
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
    "reviewLoop": {
      "ref": "group/independent-review-loop@1",
      "payload": {"goal": "审查根 GROUP"},
      "resourceClaims": []
    },
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
          "ref": "group/service-review-loop@1",
          "payload": {"goal": "独立审查服务 GROUP"},
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

## 同级启动依赖

`dependsOn` 是直接同级之间的启动屏障，允许 TASK→TASK、TASK→GROUP、GROUP→TASK 和 GROUP→GROUP：

- 来源 TASK 在自己的 TASK Review 成功后满足屏障。
- 来源 GROUP 在自己的 GROUP Review 成功后满足屏障。
- 目标 TASK 的 TASK Loop 等待屏障。
- 目标 GROUP 的子树入口 TASK Loops 等待屏障。

同级依赖必须无环。不要跨 GROUP 引用后代或祖先，也不要把 `dependsOn` 当作文件 scope、产物清单或实现顺序明细。

## Loop 描述

TASK、TASK Review、GROUP Review 和 Delivery Review 使用相同 Loop 描述协议：

- `loop.ref`：执行适配器或 Loop Skill 的稳定引用。
- `loop.payload`：原样交给 Loop 的不透明 JSON。
- `resourceClaims`：需要排他占用的精确资源锁。

不要把 `scope`、`developmentPlan`、`testCommands`、`gateLevel` 或 `requiredSkills` 放进外层 definition。Loop 需要这些内容时，由自己的 payload 规范定义和解释。资源声明是精确键；相同键互斥，不做 glob、目录包含或文件写授权判断。

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

## Skill Hint 晚绑定

需求阶段若用户给出 Skill，只在 `root.skillHints` 登记一次：

- 提示是共享、建议性的运行时偏好，不是 `requiredSkills`。
- 不在需求阶段把提示分配到 TASK、TASK Review、GROUP Review、Delivery Review、开发阶段或 Gate 阶段，也不因提示新增额外 Graph 节点。
- 每个 TASK/TASK Review/GROUP Review/Delivery Review Loop 启动后读取全部提示，结合真实任务和宿主可用 Skill 独立选择；可以跳过不适用提示，也可以使用其他 Skill。
- 调度器不查询 Skill catalog、不校验激活证据，也不因某条提示未使用而判定失败。

无合适提示时使用空数组，不要猜测 Skill。业务硬条件由对应 Loop 的 payload/验收协议表达，不要伪装成 Skill Hint。

## 准备与冻结

`preview_hierarchy` 与 `create_manual_handoff` 不绑定 Delivery 工作区。自动开发在调用 `prepare_hierarchy` 时绑定当前宿主工作区；手动开发把冻结快照以 `HANDOFF_READY` 登记到共享 SQLite 并生成根总览，但不创建 Graph Run 或 workspace 绑定，用户切换到接收 CLI 并真正开始编码时才选择工作区。多个窗口同时开发多个 Delivery 时必须使用不同工作区；Git 场景使用“一 Delivery、一 linked worktree、一最终 feature 分支”。linked worktree 共享主 checkout 的调度数据库，但 `workspaceKey` 不同。没有未结束 Delivery 时，一个工作区可以保存多个 PREPARED 方案；一旦工作区已有未结束 Delivery，新的 `prepare_hierarchy` 就以 `SCHEDULER_DELIVERY_WORKSPACE_OCCUPIED` 在写入前拒绝，并返回 `CREATE_INDEPENDENT_WORKTREE_TASK`。该错误只属于自动 Graph 开发路径，不得让预览或手动内容冻结提前创建 worktree。

Git 场景先检查首次 `workspace_status`：

- feature worktree 返回 `gitWorkspace.branchRef/headCommit` 和 `suggestedGitBinding`。把建议中的 `branchRef`、`baseRef`、`baseCommit`、`integrationTarget` 原样写入 `delivery.gitBinding`。控制器优先选择本地 `main`，不存在时回退 `master`，并以 feature HEAD 与主线的 merge-base 作为不可变创建基线。
- `gitBinding` 只属于 Delivery。同一 Delivery 的全部 TASK 共享该 feature worktree 和分支；不要为 TASK 创建、声明或切换内部 Git 分支。获得相应 Git 写入授权后，各 TASK 可以只 `git add` 并 `git commit` 自身 scope 的变更，在同一 Delivery 分支上形成独立 commit；Git index/commit 写入必须串行。
- 当前工作区位于 `main` / `master` 时，可以 preview 或生成手动开发包；只有用户选择自动执行，或手动包接收方明确开始实际开发时，才从主线创建 feature 分支和 linked worktree。自动路径随后调用 `workspace_status` 与 `prepare_hierarchy`；手动路径直接按冻结内容开发，不隐式创建 Graph。
- 在某个 Active Delivery 的 feature worktree 中规划另一个独立 Delivery 时，仍可 preview 或生成手动开发包。若选择自动执行，再从主线创建另一个 worktree；不得从当前 feature HEAD 分叉。只有用户明确要求 stacked delivery 时才允许建立真实的 Delivery 间 Git 依赖。
- 手动开发生成完整冻结内容包：固定写入本需求的 `.layered-delivery/<delivery-id>/`，包含与自动开发相同的 overview、baseline、progress、acceptance、revisions、work-items，以及自包含 `handoff-<fingerprint>.md`；同时必须生成共享 `.layered-delivery/scheduler.db` 与根 `overview.md`，把需求登记为 `HANDOFF_READY`。不创建共享 `handoffs` 目录，不创建 Graph Run 或 workspace 绑定。冻结内容时不指定 Agent、模型或接收任务，也不创建 worktree。用户切换任意 CLI 后，开发工作区或 worktree 在开始实际开发时才创建或选择，并直接按冻结内容开发。
- 宿主创建独立 worktree 任务是异步操作时，只返回 `clientThreadId`/排队标识代表 `WORKTREE_SETUP_QUEUED`，不代表已有可跟踪 `threadId`，更不代表 Delivery 已 prepare、freeze 或运行。对同一 Delivery、分支和确定性任务标题只发起一次；排队期间不重试创建。宿主返回真实 `threadId` 后才可跟踪任务并继续 prepare/freeze。
- Git worktree 缺少 `gitBinding`、当前分支不匹配、HEAD 不继承 `baseCommit`，或主线不再包含该基线时，`prepare_hierarchy` / 运行工具必须停止。控制器不代替宿主运行 `git worktree add`、`switch`、`commit`、`merge` 或 `push`。

### 跨本地仓库的同一 Delivery

主需求位于 `project-api`，但需要同时修改 `project-provider`、`project-consumer` 等本地仓库时，不拆成多个 Delivery。使用 `delivery.projectScopes` 声明精确范围：

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
3. 调用 `preview_hierarchy`，依据响应和刚提交的 hierarchy 向用户概述双指纹、项目范围和完整 GROUP/TASK 清单。该调用不创建 `.layered-delivery/scheduler.db` 状态、不绑定 workspace、不生成 run。
4. 完整清单后先不要展示 Agent 或模型建议。开发方式尚未选择时，宿主 Agent、人工交接目标和原生模型目录都还不是有效执行输入；提前展示会把人工候选与自动派遣能力混在一起。
5. 在完整清单后原样提供以下交互，不添加第三个选项，也不要用“其他内容”“其他反馈”等标签描述自由输入：

   > 请选择下一步：
   >
   > **自动执行**：创建或选择实际开发工作区，准备并冻结后开始实现、测试和独立审查
   >
   > **手动开发**：冻结同结构开发内容包，不创建 Graph、任务或工作区；可切换任意 CLI 直接开发
   >
   > 如需继续调整需求，请直接回复修改意见；当前方案不会冻结。

6. 按用户回复执行：
   - 用户选择**自动执行**后，先创建或选择实际开发工作区，并按该工作区重新校准 `projectScopes`/`gitBinding`；调用 `prepare_hierarchy` 后，向用户提供 `humanArtifacts.workspaceOverview`、Delivery 的 `overview.md`、`baseline.md`、`progress.md`、`acceptance.md`、`revisions.md`，以及 `humanArtifacts.workItems[nodeId]` 中每个节点的 `baseline.md`、`progress.md`、`acceptance.md`（存在接口声明时还包括 `interfaces.md`）。再按当前宿主真实原生 inventory 为全部 Loop 生成临时 `node_requirements`，调用 `recommend_executors(recommendation_mode=AUTOMATIC)`。只展示当前执行 Agent 内的原生模型分档，不因本机发现另一 CLI 而给出跨 Agent 建议。把结果转成中文表格：`节点 | 模式 | 执行 Agent | 原生模型角色 | 原生 modelId | 实际代理模型 | 状态`，执行前实际模型写“未报告”。展示后立即以 prepare 返回的 Revision、fingerprint、精确项目授权和真实确认人调用 `freeze_hierarchy`；MCP 不再接受 `execution_mode`。冻结成功后进入 `graph_frontier` 调度循环，并把同一节点分析交给 `plan_dispatch_batch`。正式 Ready 批次首次返回 `HOST_NATIVE_ROUTE_REVIEW` 时，增加 `剩余时间` 列展示 30 秒调整窗口，不再询问；到期自动重调并派遣。
   - 用户选择**手动开发**后，不调用 `available_agents`、`recommend_executors`、`prepare_hierarchy` 或 `freeze_hierarchy`。直接把原 hierarchy、`preview_hierarchy` 返回的双 fingerprint、精确 `authorized_project_ids` 和真实确认人传给 `create_manual_handoff`。展示返回的 `.layered-delivery/<delivery-id>/`、`manualHandoff.path` 与包含根 `workspaceOverview` 的 `humanArtifacts`，明确 `requirementSnapshotStatus=FROZEN` 只表示内容冻结；`controlStateCreated=true` 表示 SQLite 已登记 `HANDOFF_READY`，同时 `graphRunCreated=false`、`workspaceCreated=false`。不要创建任何接收任务、会话或 worktree；具体 Agent 和模型只有用户切换到接收 CLI、选择实际开发宿主后才知道。接收 CLI 校验双 fingerprint，按实际工作区校准路径和 Git 绑定，然后直接按冻结的 baseline/work-items 开发并维护 progress/acceptance；不要重新规划或隐式启动 Graph。
   - 用户直接回复修改意见时，不调用 freeze；仅在需求实际变化后重新 preview，并只使用新 fingerprint。
   - 用户询问问题或给出未改变需求的其他回复时，不调用 freeze、不重新 preview；回答后保留当前 fingerprint 并重新展示上述两个选项。
7. 自动或手动选择本身就是一次性的业务授权。自动选择紧邻 `freeze_hierarchy`，手动选择紧邻 `create_manual_handoff`；两者都不得再询问通用 Yes/No，也不要向 MCP 发送内部 `confirmed` 字段，适配器会注入严格布尔值 `True`。
8. 自动初次冻结后当前 Delivery Revision 的 Git/project binding、依赖、资源和拓扑固定，所有 TASK requirement revision 1 均为 `FROZEN`。手动开发把相同需求内容冻结为 SQLite 已登记的 `HANDOFF_READY` 可移植快照，返回 `requirementSnapshotStatus=FROZEN`；这不等于 Graph `FROZEN`，不创建 Graph Run 或 workspace 绑定，当前会话生成文件后停止，接收方可直接开发。
