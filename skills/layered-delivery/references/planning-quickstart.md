# 递归 Graph 规划

用于生成新的软件交付基线与关联文档、统一选择自动或手动开发，或调整和冻结后续 Revision。

## 准备结果续接

- 每次收到新的用户需求，先判断它是否明确要求继续、修改或恢复当前 `delivery.id`。**新用户需求默认属于新 Delivery**；不同工单、不同业务目标、用户明确称为“新需求/独立需求”，或没有明确引用当前 Delivery，均不得修改当前 Delivery。
- 不得仅因 `workspace_status` 返回旧 Delivery 就进入 Revision。只有用户明确要求修改或继续该 Delivery 时，才允许调用 `unfreeze_task_requirement` 或 `prepare_delivery_revision`；Agent 的语义判断不能替代这项用户连续性授权。
- 当前上下文仍保留最近一次 `preview_hierarchy` 响应和原始 hierarchy 时，复用其中的双 fingerprint、完整清单和 `executionChoice`；需求未变且尚无 `executionSelection` 时不要重复 preview，回答用户问题后重新展示 Controller 返回的同一交互。若已记录 `AUTOMATIC`，只续接 worktree 迁移与 `resume_execution_mode`，不得重新展示选择器。
- 初次开发前用户修改需求时，更新 hierarchy 并重新调用 `preview_hierarchy`；Controller 在同一 `CHOICE_READY` Delivery 中重新生成基线与关联文档，只使用新响应的 fingerprint，不复用旧值。初始自动选择统一调用 `select_execution_mode`，不得由 Skill 拆成或猜测 `prepare_hierarchy` / `freeze_hierarchy` 步骤。
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

同一需求的所有人类文件共用 `.layered-delivery/<delivery-id>/`。自动与手动开发都生成 overview、baseline、progress、acceptance、revisions 和同结构 work-items；手动开发另有 `.layered-delivery/<delivery-id>/handoff-<fingerprint>.md`，包含完整 schema v3。不得创建跨需求共享的 `.layered-delivery/handoffs/`。手动包以双 fingerprint 冻结需求内容并在 SQLite 登记为 `HANDOFF_READY`；交接阶段尚未形成 Graph Run。接收 CLI 必须在任何代码工作前以精确双 fingerprint 调用 `start_manual_handoff`，在实际工作区把同一快照启动为 manual Graph；Graph 状态仍只以 MCP 返回和 SQLite 事件链为准。

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

`preview_hierarchy` 与手动选择都不绑定 Delivery 工作区。preview 先把需求登记为 `CHOICE_READY`，生成共享 SQLite、根总览和完整投影；自动按钮由 `select_execution_mode(AUTOMATIC)` 先持久记录选择和项目授权。Claude Code 与 Codex 的自动 Git Delivery 都返回机器可消费的 `worktreeSetup.hostDispatch`，保持 `CHOICE_READY` 和 `executionSelection.state=RECORDED_PENDING_WORKTREE`，直到后台 Delivery coordinator/项目任务在稳定 linked worktree 中完成 `workspace_status → resume_execution_mode`。Claude 主会话只短暂进入宿主 worktree 以启动后台 coordinator，随后返回 primary；固定 MCP 控制根由 Hook 的真实 cwd 证明解耦，不创建新顶层会话。手动选择把快照转为 `HANDOFF_READY`，交接阶段不创建 Graph Run 或 workspace 绑定；接收 CLI 调用 `start_manual_handoff` 绑定并启动同一 Graph，成功后才允许编码。多个 Delivery 使用不同稳定 worktree；linked worktree 共享 primary checkout 的调度数据库，但 `workspaceKey` 不同。Controller 自身始终不创建或切换分支/worktree；错误地在占用工作区 prepare 时仍以 `SCHEDULER_DELIVERY_WORKSPACE_OCCUPIED` 拒绝并返回宿主后台 worktree handoff。

`preview_hierarchy`、`prepare_delivery_revision` 与 `create_manual_handoff` 的 `hierarchy` 也可改用 `hierarchy_file` 传入：当层级结构较大或 payload 详细、难以一次性内联写出正确 JSON 时，先用 Write 把 JSON 写到工作区文件（如 `.layered-delivery/staging/hierarchy.json`），用 `python -m json.tool` 校验，再调用时只传 `hierarchy_file`（工作区相对路径）。控制器在工作区沙箱内读取并解析该文件，等价于内联 `hierarchy`；两者二选一，同时给或都不给都会被拒绝，路径穿越/符号链接/跨盘也会被拒绝。

新 AUTOMATIC Delivery 的 `gitBinding.branchRef` 必须是一个**未被其他 worktree/Delivery 占用的新分支名**，或干脆省略 `gitBinding` 让 Controller 在 worktree setup 用 `suggestedGitBinding` 建议——**不要从已完成旧 Delivery 拷绑定**：若该分支正被 primary 占用，git 不允许两个 worktree 共用同一分支，Controller 会在 AUTOMATIC 派发前以 `SCHEDULER_GIT_BRANCH_IN_USE_BY_OTHER_WORKTREE` 拒绝。另外，单文件/小修直接在 primary 改即可，**不必套 AUTOMATIC**（它适合多 TASK/跨模块/可恢复的较大交付；小修套 AUTOMATIC 的 worktree + 协调器开销不划算）。

preview 的 `executionChoice` 会带出 `baseRef`/`integrationTarget`：若基于进行中分支（如 `feature/m_lf_protein`）修 bug，在 `workspace_status(base_ref=...)` 或 hierarchy 的 `gitBinding` 里明确指定该分支，并在选择执行方式时**与用户确认基线分支**（基于 master 还是某个进行中分支）。

### Git 工作区设置

先检查首次 `workspace_status`：

#### 自动策略与 `hostDispatch`

- 自动 Git 工作区策略统一为 `HOST_NATIVE_LINKED_WORKTREE`。`worktreeSetup.hostDispatch` 固定包含宿主操作、`environment=worktree`、确定性标题/idempotency key、基线、续接 prompt、双 fingerprint、稳定工作区、后台执行、`requiresNewTopLevelSession=false`、`manualDirectoryChangeRequired=false` 与 `coordinatorCheckoutPolicy=PRESERVE_CURRENT_CHECKOUT`。Claude 机械消费 `agentDispatch`，优先进入唯一现有 Delivery worktree，否则创建后进入，启动后台 coordinator 并返回 primary；Codex 创建后台 worktree 项目任务。Controller 不负责选择宿主 UI，也不直接调用 `git worktree add`、`switch` 或生成分支名。

#### `worktreeProvenance` 与基线发现

- 每次返回的 `worktreeProvenance` 都必须体现实际来源：`hostAdapterId`、`workspaceRoot`、`topology`、`selectionSource`、`baseRef`、`baseCommit`、`baseHeadCommit` 与 `integrationTarget`。`baseCommit` 是当前 HEAD 与所选主线的 merge-base；`baseHeadCommit` 是检查时所选本地或 remote-tracking 主线的 HEAD。无论 worktree 最初基于哪个分支创建，都不得省略或猜测这些字段。
- Delivery 冻结后，`gitBinding` 中的 `baseRef/baseCommit/integrationTarget` 是权威基线；后续 `workspace_status` 仍返回 `worktreeProvenance`，并以 `selectionSource=FROZEN_GIT_BINDING` 区分冻结事实与当次主线发现，`baseHeadCommit` 继续表示当次可见的主线 HEAD。
- 基线发现顺序固定为：调用方通过 `workspace_status(base_ref=...)` 提交的宿主显式选择（`HOST_SELECTED`）、有效的远端默认引用 `origin/HEAD`（`ORIGIN_HEAD`），再依次降级到本地 `main`、本地 `master`（对应 `LOCAL_MAIN_FALLBACK`、`LOCAL_MASTER_FALLBACK`）；全部无效时停止并要求明确选择。这里的远端引用是当前仓库已经持有的 remote-tracking ref，Controller 不执行 `fetch`。不会额外枚举或硬编码 `develop`、`origin/develop` 或其他分支名。显式 `base_ref` 必须能解析为本地分支或 `origin` tracking ref，并同时成为建议的 `baseRef` 与 `integrationTarget`。

#### 分支 adoption 与 dirty 确认

- 不能仅凭 feature 分支名判断“当前分支已是本 Delivery 的独立分支”。只有策略为 `HOST_NATIVE_LINKED_WORKTREE`、该分支未被其他 worktree checkout、未绑定其他 Delivery，且基线关系有效时，才进入 adoption 判断。`BRANCH_IN_USE_BY_OTHER_WORKTREE`、`BRANCH_BOUND_TO_OTHER_DELIVERY` 或 `BRANCH_USED_BY_HISTORICAL_DELIVERY` 均必须创建新的 Delivery feature 分支后重查，不能接管已有分支。
- Delivery linked worktree 干净且唯一时，`branchAdoption.state=READY` 并返回 `suggestedGitBinding`。把建议中的 `branchRef`、`baseRef`、`baseCommit`、`integrationTarget` 原样写入 `delivery.gitBinding`。
- Delivery linked worktree 已有业务 diff 时，只返回 `candidateGitBinding` 和 `DIRTY_CONFIRMATION_REQUIRED`。`.layered-delivery/**` 是 Controller 控制面，不计入业务 dirty 状态。宿主必须向用户展示其余变更并确认全部属于本 Delivery；确认后立即把原响应的精确 `workingTree.stateFingerprint` 作为 `confirmed_dirty_state_fingerprint` 再次调用 `workspace_status`。只有 diff 未变化时才返回 `READY_WITH_CONFIRMED_CHANGES` 与 `suggestedGitBinding`；指纹变化以 `SCHEDULER_GIT_DIRTY_STATE_CHANGED` 停止并重新确认。不得把“有 diff”本身当作可复用证明。

#### 宿主承接与分支命名

- Codex 承接新 Delivery 时把 `hostOperation=CREATE_CODEX_PROJECT_TASK` 映射为新的项目任务并设置 `environment=worktree`，将 `hostDispatch.prompt` 原样作为首条任务指令；主任务不切换目录或分支。Codex 管理的 worktree 可能先处于 detached HEAD；此时 `workspace_status` 返回的 `worktreeSetup.nextAction` 为 `CREATE_DELIVERY_FEATURE_BRANCH`。取得 Git 分支写授权并创建本 Delivery 的本地 feature 分支后，重新调用 `workspace_status`，直到获得 `suggestedGitBinding` 才继续。
- Claude Code 从项目目录启动时保持 primary checkout 作为控制/监控根，主 checkout 保持 `main` 或 `master`。宿主创建或复用 Delivery linked worktree，并在同一顶层会话内启动后台 coordinator；`${CLAUDE_PROJECT_DIR}` 不漂移，执行 cwd 由 Hook 一次性证明。禁止要求用户启动第二个顶层 Claude 会话。
- feature 分支名称由宿主或用户已有分支策略决定；Layered Delivery 不生成 `feature/m_lf_` 等固定前缀，只冻结实际 checkout 的本地分支名。

#### `projectScopes` 与 `gitBinding` 归属

- `projectScopes.workspaceRoot` 在 CHOICE_READY 快照中保存 preview 时的仓库锚点。prepare/runtime 会以 Git common directory、精确 `branchRef` 和冻结基线只读解析每个 scope 的唯一实际 linked worktree，并在 `verifiedProjectScopes` 中返回实际 `workspaceRoot`，路径变化时另带 `declaredWorkspaceRoot`。Loop 启动时 `loop_context.projectScopes` 使用同一批运行时验证路径，冻结锚点另以 `projectScopeAnchors` 返回；receiver 不得对非当前有效锚点目录执行开发，也不得自行创建或切换分支。没有匹配、存在歧义或分支/基线不符时按 Controller 错误补齐工作区，不能自行改写 frozen hierarchy。
- `gitBinding` 只属于 Delivery。同一 Delivery 的全部 TASK 共享该 feature checkout/worktree 和分支；不要为 TASK 创建、声明或切换内部 Git 分支。获得相应 Git 写入授权后，各 TASK 可以只 `git add` 并 `git commit` 自身 scope 的变更，在同一 Delivery 分支上形成独立 commit；Git index/commit 写入必须串行。

#### primary checkout 与并行 Delivery

- Claude 与 Codex primary checkout 都返回 `DEDICATED_WORKTREE_REQUIRED / CREATE_INDEPENDENT_WORKTREE_TASK` 和 `hostDispatch`。linked worktree 位于主线或 detached HEAD 时返回 feature 分支动作；该动作由后台 Delivery 工作区完成，不移动 primary。
- 在某个 Active Delivery 的 feature worktree 或主监控会话中收到另一个独立 Delivery 时，立即从宿主选择的基线或上述默认发现结果创建另一稳定 Delivery worktree；不得从当前 feature HEAD 分叉。只有用户明确要求 stacked delivery 时才允许建立真实的 Delivery 间 Git 依赖。

#### 手动开发内容包

- 手动开发生成完整冻结内容包：固定写入本需求的 `.layered-delivery/<delivery-id>/`，包含与自动开发相同的 overview、baseline、progress、acceptance、revisions、work-items，以及自包含 `handoff-<fingerprint>.md`；同时必须生成共享 `.layered-delivery/scheduler.db` 与根 `overview.md`，把需求登记为 `HANDOFF_READY`。不创建共享 `handoffs` 目录；交接阶段不创建 Graph Run 或 workspace 绑定，不指定 Agent、模型或接收任务，也不创建 worktree。用户切换任意 CLI 后，在实际开发工作区调用 `start_manual_handoff`：控制器绑定 workspace、启动同一冻结 Graph，并只让 TASK 实现走 MANUAL claim；完整 Review 和最终确认继续沿用自动执行协议。

#### 异步排队与停止条件

- 宿主创建独立 worktree 任务是异步操作时，只返回 `clientThreadId`/排队标识代表 `WORKTREE_SETUP_QUEUED`，不代表已有可跟踪 `threadId`，更不代表 Delivery 已 prepare、freeze 或运行。按 `hostDispatch.idempotencyKey` 对同一 Delivery 和 fingerprint 只发起一次；排队期间不重试创建。宿主返回真实 `threadId` 后才可跟踪任务并继续 prepare/freeze。
- Git worktree 缺少 `gitBinding`、当前分支不匹配、HEAD 不继承 `baseCommit`，或本地/`origin` 同名主线均不再包含该基线时，`prepare_hierarchy` / 运行工具必须停止。控制器不代替宿主运行 `git worktree add`、`switch`、`commit`、`merge` 或 `push`。

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
4. 完整清单后不展示模型建议。Layered Delivery 无论在选择前后都不推荐派遣模型；自动 receiver 继承当前宿主，内部 Worker 由 Loop 自主管理。
5. Controller 是交互文案的唯一所有者。宿主必须优先调用原生选择器：把 `executionChoice.options` 机械映射到 `AskUserQuestion`（Claude Code）、机械映射到 `request_user_input`（Codex），并保留顺序、ID、默认项、推荐项、标签和说明。只有映射工具在当前上下文不可调用时，才允许逐字显示 `executionChoice.markdown`；不得由 Skill 或 Agent 改写降级文案、交换顺序、增加第三个按钮，也不得要求用户回复选项文字。`AUTOMATIC` 是默认和推荐项，`MANUAL` 是第二项。
6. 原生对话框仍允许直接输入文字，但不为它创建“其他”选项。自由输入按 `freeformInput.nextAction` = `CONTINUE_REQUIREMENT_DISCUSSION` 继续需求沟通；需求发生变化后重新调用 preview，让 Controller 重新生成基线、关联文档和交互。用户只是提问且需求未变时，回答后保留当前 fingerprint，并再次按 `presentationPolicy` 展示同一 `executionChoice`，不得主动退回文本交互。
7. 用户点选按钮后，把选项 ID、双 fingerprint、`requiredProjectAuthorizations` 的精确项目 ID 和真实确认人一次性传给 `select_execution_mode`：
   - `AUTOMATIC`：先记录业务确认。Git Delivery 返回 `selectionRecorded=true`、`automaticDispatchRequested=false` 和 `worktreeSetup.hostDispatch` 时，Claude/Codex 按 `launchPolicy=IMMEDIATE` 创建或复用后台 Delivery worktree 执行单元并发送内置 prompt；后台方用原双 fingerprint 调用 `resume_execution_mode`。主会话只监控，不追加第二次确认；成功后按 frontier 间隔持续读取进度。所有路径都不插入模型推荐或调整窗口，也不显示人工新会话命令。
   - `MANUAL`：生成同结构开发包和自包含 handoff，登记 `HANDOFF_READY`；交接阶段不创建 Graph Run、workspace 绑定、任务或 worktree。宿主展示 `manualHandoff.receiverPrompt`，不得改写；同一提示词已经嵌入 `manualHandoff.path` 指向的文件。接收 CLI 必须先调用 `start_manual_handoff`，再按 frontier 在独立上下文 MANUAL claim TASK；Review 不允许 MANUAL，必须正常自动派遣。
8. 自动或手动按钮选择本身就是该初始 Revision 的一次业务授权。`select_execution_mode` 不接受第二个确认步骤；自动 feature 分支/worktree 准备后只调用 `resume_execution_mode`，宿主不得再次调用选择器，也不得调用初始 `prepare_hierarchy`、`freeze_hierarchy` 或 `create_manual_handoff` 重放选择。需求内容改变会清除未执行的 AUTOMATIC 选择并重新生成交互。后续显式 Revision 继续使用各自的 Revision 工具和既有确认规则。
9. 自动初次冻结后当前 Delivery Revision 的 Git/project binding、依赖、资源和拓扑固定，所有 TASK requirement revision 1 均为 `FROZEN`。手动开发把相同需求内容冻结为 SQLite 已登记的 `HANDOFF_READY` 可移植快照，返回 `requirementSnapshotStatus=FROZEN`；这不等于 Graph `FROZEN`，交接阶段不创建 Graph Run 或 workspace 绑定。接收方启动 `start_manual_handoff` 后，同一 Revision 进入 `executionMode=manual` 的 Graph Run，所有 TASK requirement 同样冻结并接受完整调度、Review 与最终验收。

## 并行 Delivery 与同资源串行化

多个 Delivery 可在同一仓库并行：每个 Delivery 有独立 linked worktree 和分支，共享同一个 `.layered-delivery/scheduler.db` 控制面，但用不同 `workspaceKey` 隔离。隔离是结构性的——**Controller 从不跨 Delivery 比较文件路径**，两个 Delivery 改同一文件在各自 worktree 里执行期并不冲突（看不到对方未提交改动），冲突只在各自合回 `integrationTarget` 时才可能出现。

### 同文件/同区域：声明式串行化（`resourceClaims`）

要让"改同一文件或同一逻辑区域"的 TASK 跨 Delivery **串行**（后一个等前一个完成），在相关 TASK 的 `execution.loop.resourceClaims` 里声明**同一个锁键**。`resourceClaims` 是**精确匹配的排他锁键，跨所有 Delivery 全局生效**，三层强制：frontier 对有重叠 claim 的 Ready Loop 不发 `DISPATCH_LOOP`（其 `resourceConflicts` 列出持有方 `<rootId>/<nodeId>`）；`plan_dispatch_batch` 预留返回 `DISPATCH_RESERVATION_CONFLICT`；`dispatch_loop` claim 返回 `SCHEDULER_RESOURCE_CONFLICT`。**声明即串行，无需额外开发。**

**锁键命名约定**：稳定的小写 token，**不要用原始路径**（Controller 不做前缀/路径推导）。例如同改订单服务用 `orders-service`；同改某文件用 `file-src-orders-service-py`；同改库表结构用 `db-schema-orders`。键是精确相等匹配。

**没有运行时同文件检测**：隔离 worktree 决定了冲突靠**声明预防**，不是在 Loop 执行中被发现。若两个 TASK 可能改同一处，就在两者都声明同一个 claim。

示例：Delivery A 和 Delivery B 各有一个 TASK 改订单服务，两者都在 `execution.loop.resourceClaims` 写 `"orders-service"` → A 的 TASK 先 claim，B 的 TASK 在 frontier 显示 `resourceConflicts: ["<A-rootId>/<A-nodeId>"]` 并等待，A 完成释放后 B 才派发。被串行的 TASK 不是失败，是排队；持续读 frontier 即可看到它的 `resourceConflicts` 清空后变为可派发。

### 基线陈旧 rebase 恢复（0.35.0 起）

某 Delivery 冻结的 `baseCommit` 落后于其 `integrationTarget`（例如别人已把 main 合并前进）时，`workspace_status` 会在 `gitWorkspace.worktreeRebase` 带出**可恢复 advisory**：

```json
{"worktreeRebase": {"required": true, "frozenBaseCommit": "<冻结基线>", "currentBaseCommit": "<当前 integrationTarget HEAD>", "integrationTarget": "main", "nextAction": "REBASE_DELIVERY_WORKTREE_ONTO_CURRENT_BASE_THEN_PREPARE_DELIVERY_REVISION"}}
```

宿主/协调器看到它后的恢复流程（Controller 不执行 git）：

1. 先暂停该 Delivery 在该 worktree 里在途的 claimed Loop（`pause_loop`），避免 rebase 干扰运行中的工作；
2. 在该 Delivery worktree 内把 `branchRef` rebase 到 `currentBaseCommit`（如 `git rebase --onto <integrationTarget HEAD> <frozenBaseCommit> <branchRef>`，或 merge）；冲突由宿主解决，无法解决则相关 Loop 按 `BLOCKED`(`LOOP_BLOCKED`/`EXTERNAL_AUTHORITY`) 上报，不要静默换分支；
3. 用新的 `gitBinding`（`baseCommit` = rebase 后 HEAD 与主线的 merge-base）调用 `prepare_delivery_revision`（连续性 `ACTIVE_LOOP_REPLAN` 或 `USER_EXPLICIT_SAME_DELIVERY`）重锚基线；`preparing=True` 重验 fork-point，旧 run 被 supersede；
4. 在新 Revision 上恢复执行（`resume_execution_mode` / 继续 frontier）。

约束：Controller 永不做 git 写；rebase 由宿主执行；重锚走 Revision；绑定 workspace 不可变；rebase 期间在途 Loop 须 pause/释放。`workspace_status` 只检测并**发出 advisory**，不自动 rebase。
