# 递归 Graph 规划

用于创建新的软件交付 Graph，或续接、调整和冻结已有 `PREPARED` 结果。

## 准备结果续接

- 当前上下文仍保留最近一次 `prepare_hierarchy` 响应和原始 hierarchy 时，复用其中的 `hierarchyFingerprint`、完整清单与人类投影路径；需求未变时不要重复 prepare，回答用户问题后重新展示同一组冻结选项。
- 用户修改需求时，更新 hierarchy 并重新调用 `prepare_hierarchy`；只使用新响应的 fingerprint，不复用旧值。
- 当前上下文不再持有精确 fingerprint 或原始 hierarchy 时，不从 JSON/Markdown 投影反推机器输入，也不猜测旧值；重新收集需求并 prepare 后再请求冻结确认。

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

`delivery.id` 是稳定的 Delivery/Graph 标识，也是需求投影目录的 namespace。工作区全部 Delivery 的入口位于 `.layered-delivery/overview.md`，这里只列 Delivery 标识、标题、状态、更新时间和详情链接；该 Delivery 自己的 TASK 进度与 GROUP 数量位于 `.layered-delivery/<delivery-id>/overview.md`。`delivery.reviewLoop` 在根终态之后执行。

## 定义递归节点

root wrapper 包含 `schemaVersion`、`skillHints`、`definition`、`reviewLoop` 和 `children`。嵌套节点只包含 `definition`、`reviewLoop` 和 `children`。

TASK：

- `definition.kind` 为 `TASK`，根的 `parentId` 为 `null`，子 TASK 的 `parentId` 为直接父 GROUP ID。
- `definition.execution.dependsOn` 只引用直接同级 GROUP/TASK。
- `definition.execution.loop` 描述唯一执行 Loop。
- `reviewLoop` 必填，描述该 TASK 实现完成后的独立 `TASK_REVIEW_LOOP`；`children` 必须为空。

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
- 通用快照可用 `identifier` 保存稳定、可定位的调用标识；
- HTTP 快照另带 `method` 与 `path`；
- Dubbo 快照另带 `service` 与 `method`。

需求分析时可以从真实代码、OpenAPI、Controller/DTO、IDL 或服务定义提取
before 候选，并根据需求形成 after；确认 TASK 时必须把两者作为显式契约
评审。每个适用快照的 `request` 和 `response` 都应给出完整契约，包括可
核对的参数名称、类型、是否必填和简介，不得只列变化字段，也不得只写
“参考代码”或“待实现”。`CREATE` 的 before、`DELETE` 的 after 使用空值。
调用标识保持可定位：HTTP 可使用 `method + path`，Dubbo 可使用
`service + method`，其他协议使用 `identifier`。规划时以 `hierarchy_contract` 返回的
`projectionGuidance.interfaces` 为实时约定。

控制器把每个 TASK 的声明确定性写入该 TASK 的
`work-items/<task-id>/interfaces.md`，并从 TASK baseline 与 Delivery
baseline 串联；完全没有声明的 TASK 不生成该文件、路径和导航。控制器不
动态扫描实现代码或隐式推算契约；TASK/Review Loop 可用真实代码验证 after。
接口详情直接在完整请求和响应表中逐字段比较 before/after，标记新增、修改、
删除或未变；类型、必填性和简介使用“修改前 → 修改后”展示，不再拆成两份
重复清单。字段仍是 Loop 的不透明输入，不参与依赖、资源锁或 Graph 调度。

## Skill Hint 晚绑定

需求阶段若用户给出 Skill，只在 `root.skillHints` 登记一次：

- 提示是共享、建议性的运行时偏好，不是 `requiredSkills`。
- 不在需求阶段把提示分配到 TASK、TASK Review、GROUP Review、Delivery Review、开发阶段或 Gate 阶段，也不因提示新增额外 Graph 节点。
- 每个 TASK/TASK Review/GROUP Review/Delivery Review Loop 启动后读取全部提示，结合真实任务和宿主可用 Skill 独立选择；可以跳过不适用提示，也可以使用其他 Skill。
- 调度器不查询 Skill catalog、不校验激活证据，也不因某条提示未使用而判定失败。

无合适提示时使用空数组，不要猜测 Skill。业务硬条件由对应 Loop 的 payload/验收协议表达，不要伪装成 Skill Hint。

## 准备与冻结

每个对话窗口通过当前宿主工作区绑定自己的 Delivery。多个窗口要同时开发多个 Delivery 时，必须使用不同工作区；Git 场景使用“一 Delivery、一 linked worktree、一最终 feature 分支”。linked worktree 共享主 checkout 的调度数据库，但 `workspaceKey` 不同。一个工作区可以保存多个 PREPARED 方案，却只能运行一个未结束的 Active Delivery；默认 `workspace_status` 优先恢复该 Active Delivery，查看其他 PREPARED 方案时显式传 `root_id`。第二个 Delivery 冻结前必须切换到独立工作区。

Git 场景先检查首次 `workspace_status`：

- feature worktree 返回 `gitWorkspace.branchRef/headCommit` 和 `suggestedGitBinding`。把建议中的 `branchRef`、`baseRef`、`baseCommit`、`integrationTarget` 原样写入 `delivery.gitBinding`。控制器优先选择本地 `main`，不存在时回退 `master`，并以 feature HEAD 与主线的 merge-base 作为不可变创建基线。
- `gitBinding` 只属于 Delivery。同一 Delivery 的全部 TASK 共享该 feature worktree 和分支；不要为 TASK 创建、声明或切换内部 Git 分支。获得相应 Git 写入授权后，各 TASK 可以只 `git add` 并 `git commit` 自身 scope 的变更，在同一 Delivery 分支上形成独立 commit；Git index/commit 写入必须串行。
- 当前 worktree 位于 `main` / `master` 时，不在这里 prepare Delivery。先由宿主从该主线当前提交创建新的 feature 分支和 linked worktree，再在新对话工作区重新调用 `workspace_status`。
- 在某个 Active Delivery 的 feature worktree 中要求启动另一个独立 Delivery 时，也先从主线创建另一个 worktree；不得从当前 feature HEAD 分叉。只有用户明确要求 stacked delivery 时才允许建立真实的 Delivery 间 Git 依赖，而当前 Graph 不把它伪装成两个独立 Delivery。
- Git worktree 缺少 `gitBinding`、当前分支不匹配、HEAD 不继承 `baseCommit`，或主线不再包含该基线时，`prepare_hierarchy` / 运行工具必须停止。控制器不代替宿主运行 `git worktree add`、`switch`、`commit`、`merge` 或 `push`。

新建、修改或无法安全续接 `PREPARED` 结果时：

1. 调用 `hierarchy_contract(root_kind=...)`。
2. 按返回的 schema 和 example 创建完整 hierarchy。
3. 调用 `prepare_hierarchy`，依据 MCP 响应和刚提交的 hierarchy 向用户概述双指纹、`workspaceIsolation.workspaceKey`、`gitBinding`、当前 `gitWorkspace.headCommit`、状态和完整 GROUP/TASK 清单；同时提供 Delivery 的 `humanArtifacts.workspaceOverview`、`overview`、`baseline`、`progress`、`acceptance`，以及 `humanArtifacts.workItems[nodeId]` 中每个 GROUP/TASK 的 `baseline`、`progress`、`acceptance` 路径；这些字段分别对应固定的 `overview.md`、`baseline.md`、`progress.md` 和 `acceptance.md`。TASK 的 `taskBaselines` 继续作为其 baseline 便捷映射。只有节点映射实际包含 `interfaces` 时才提供该 TASK 的 `interfaces.md` 路径。Delivery `overview.md` 只负责状态与导航；Delivery baseline 串联 Git binding 与全部节点 baseline，GROUP baseline 串联直接子节点，TASK baseline 保存当前冻结 requirement revision 和 Loop 输入。验收报告遵守 `projectionGuidance.acceptanceReports`：每份报告只完整展开当前层，GROUP 以摘要和链接串联直接子节点，Delivery 以摘要和链接串联根工作项，不向上复制下层 payload、evidence 或 reviewFindings。人类 Markdown 使用固定中文模板和递归字段列表，不展示 JSON 代码块或机器状态枚举。不要读取投影来反推机器状态，也不要自行重演渲染器。
4. 调用 `available_agents`，再以本次 `prepare_hierarchy` 返回的 `rootId` 调用 `recommend_executors`。在完整清单后展示每个 TASK、TASK Review、GROUP Review 和 Delivery Review 的建议 Agent、当前模型、置信度、备选及 `reasons`；若 Review 的 `independence.satisfied` 为 false，明确说明当前主机无法满足异构 Agent 审查。该结果不会启动、切换或派遣任何 Agent/模型，也不修改 fingerprint、hierarchy 或 Graph。
5. 在建议后原样提供以下交互，不添加第三个选项，也不要用“其他内容”“其他反馈”等标签描述自由输入：

   > 请选择下一步：
   >
   > **自动执行**：立即冻结并开始实现、测试和独立审查
   >
   > **手动交接**：冻结后生成交接信息
   >
   > 如需继续调整需求，请直接回复修改意见；当前方案不会冻结。

6. 按用户回复执行：
   - 用户选择**自动执行**后，立即以当前 `hierarchyFingerprint`、`execution_mode=active` 和真实确认人调用 `freeze_hierarchy`；冻结成功后直接进入 `graph_frontier` 调度循环。
   - 用户选择**手动交接**后，立即以当前 `hierarchyFingerprint`、`execution_mode=manual` 和真实确认人调用 `freeze_hierarchy`；冻结成功后只输出一次包含 `rootId` 的纯文本交接说明，接收会话从 `graph_frontier` 恢复，不重新 prepare 或 freeze。
   - 用户直接回复修改意见时，不调用 freeze；仅在需求实际变化后重新 prepare，并只使用新 fingerprint。
   - 用户询问问题或给出未改变需求的其他回复时，不调用 freeze、不重新 prepare；回答后保留当前 fingerprint 并重新展示上述两个选项。
6. 自动或手动选择本身就是一次性的完整冻结授权。只有这两个选择可以紧邻调用 `freeze_hierarchy`；该工具在宿主权限层使用自动批准，不得再询问通用 Yes/No 或触发任何冻结弹窗，也不要向 MCP 发送内部 `confirmed` 字段。适配器会在控制器边界内注入严格的布尔值 `True`；自动/手动只决定当前会话继续还是交接，不改变内部自动确认模式。
7. 初次冻结后 `delivery.gitBinding`、依赖、资源和拓扑固定，所有 TASK requirement 都是 revision 1、`FROZEN`，不再逐 TASK 请求方案确认。开发期间仅在用户主动要求修改尚未开始 TASK 时，才按执行说明单独解冻/再冻结该 TASK；该操作不能改变 Git binding。自动模式继续消费 frontier；手动模式到一次性交接后停止当前会话的执行循环。
