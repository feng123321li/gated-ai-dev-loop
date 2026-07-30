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

按调度关系拆分，不按文件数量拆分。一个 TASK Loop 可以覆盖一个模块、多个模块或多个项目，只要它能作为整体返回标准终态。不要为表现项目或模块目录而强制增加 GROUP。

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
    "reviewLoop": null,
    "children": []
  }
}
```

`delivery.id` 是稳定的 Delivery/Graph 标识，也是需求投影目录的 namespace。工作区全部 Delivery 的进度入口位于 `.layered-delivery/overview.md`，该 Delivery 的可读投影位于 `.layered-delivery/<delivery-id>/`；`delivery.reviewLoop` 在根终态之后执行。

## 定义递归节点

root wrapper 包含 `schemaVersion`、`skillHints`、`definition`、`reviewLoop` 和 `children`。嵌套节点只包含 `definition`、`reviewLoop` 和 `children`。

TASK：

- `definition.kind` 为 `TASK`，根的 `parentId` 为 `null`，子 TASK 的 `parentId` 为直接父 GROUP ID。
- `definition.execution.dependsOn` 只引用直接同级 GROUP/TASK。
- `definition.execution.loop` 描述唯一执行 Loop。
- `reviewLoop` 必须为 `null`，`children` 必须为空。

GROUP：

- `definition.kind` 为 `GROUP`，并在 `definition.children` 中列出直接子节点的 `id`、`kind`、`title` 摘要。
- `definition.decomposition.dependsOn` 只引用直接同级 GROUP/TASK。
- 节点 `children` 递归包含与摘要一一对应的完整 GROUP/TASK 节点，且至少一个。
- 节点 `reviewLoop` 必填。直接子节点终态全部成功后，调度器完成 `GROUP_JOIN`，再派发该 `GROUP_REVIEW_LOOP`；Review 成功才是 GROUP 的终态。

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
            "reviewLoop": null,
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
            "reviewLoop": null,
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
        "reviewLoop": null,
        "children": []
      }
    ]
  }
}
```

先调用 `hierarchy_contract(root_kind="GROUP")` 或 `hierarchy_contract(root_kind="TASK")`，并以返回的实时 schema/example 为最终字段依据。

## 同级启动依赖

`dependsOn` 是直接同级之间的启动屏障，允许 TASK→TASK、TASK→GROUP、GROUP→TASK 和 GROUP→GROUP：

- 来源 TASK 在 TASK Loop 成功后满足屏障。
- 来源 GROUP 在自己的 GROUP Review 成功后满足屏障。
- 目标 TASK 的 TASK Loop 等待屏障。
- 目标 GROUP 的子树入口 TASK Loops 等待屏障。

同级依赖必须无环。不要跨 GROUP 引用后代或祖先，也不要把 `dependsOn` 当作文件 scope、产物清单或实现顺序明细。

## Loop 描述

TASK、GROUP Review 和 Delivery Review 使用相同 Loop 描述协议：

- `loop.ref`：执行适配器或 Loop Skill 的稳定引用。
- `loop.payload`：原样交给 Loop 的不透明 JSON。
- `resourceClaims`：需要排他占用的精确资源锁。

不要把 `scope`、`developmentPlan`、`testCommands`、`gateLevel` 或 `requiredSkills` 放进外层 definition。Loop 需要这些内容时，由自己的 payload 规范定义和解释。资源声明是精确键；相同键互斥，不做 glob、目录包含或文件写授权判断。

## Skill Hint 晚绑定

需求阶段若用户给出 Skill，只在 `root.skillHints` 登记一次：

- 提示是共享、建议性的运行时偏好，不是 `requiredSkills`。
- 不在需求阶段把提示分配到 TASK、GROUP Review、Delivery Review、开发阶段或 Gate 阶段，也不因提示新增 Graph 节点。
- 每个 TASK/GROUP Review/Delivery Review Loop 启动后读取全部提示，结合真实任务和宿主可用 Skill 独立选择；可以跳过不适用提示，也可以使用其他 Skill。
- 调度器不查询 Skill catalog、不校验激活证据，也不因某条提示未使用而判定失败。

无合适提示时使用空数组，不要猜测 Skill。业务硬条件由对应 Loop 的 payload/验收协议表达，不要伪装成 Skill Hint。

## 准备与冻结

新建、修改或无法安全续接 `PREPARED` 结果时：

1. 调用 `hierarchy_contract(root_kind=...)`。
2. 按返回的 schema 和 example 创建完整 hierarchy。
3. 调用 `prepare_hierarchy`，依据 MCP 响应和刚提交的 hierarchy 向用户概述双指纹、状态和完整 GROUP/TASK 清单；同时提供 `humanArtifacts.workspaceOverview`、`humanArtifacts.overview` 及每个 `humanArtifacts.taskBaselines[taskId]` 路径。根 overview 汇总全部 Delivery；Delivery overview 只展示该需求的清单和进度；每个 TASK 的摘要、依赖、Loop 引用、资源声明、结构化执行输入与共享 Skill Hint 由控制器拆分到独立 baseline。人类 Markdown 使用固定中文模板和递归字段列表，不展示 JSON 代码块或机器状态枚举。不要读取投影来反推机器状态，也不要自行重演渲染器。
4. 在清单后原样提供以下交互，不添加第三个选项，也不要用“其他内容”“其他反馈”等标签描述自由输入：

   > 请选择下一步：
   >
   > **自动执行**：立即冻结并开始实现、测试和独立审查
   >
   > **手动交接**：冻结后生成交接信息
   >
   > 如需继续调整需求，请直接回复修改意见；当前方案不会冻结。

5. 按用户回复执行：
   - 用户选择**自动执行**后，立即以当前 `hierarchyFingerprint`、`execution_mode=active` 和真实确认人调用 `freeze_hierarchy`；冻结成功后直接进入 `graph_frontier` 调度循环。
   - 用户选择**手动交接**后，立即以当前 `hierarchyFingerprint`、`execution_mode=manual` 和真实确认人调用 `freeze_hierarchy`；冻结成功后只输出一次包含 `rootId` 的纯文本交接说明，接收会话从 `graph_frontier` 恢复，不重新 prepare 或 freeze。
   - 用户直接回复修改意见时，不调用 freeze；仅在需求实际变化后重新 prepare，并只使用新 fingerprint。
   - 用户询问问题或给出未改变需求的其他回复时，不调用 freeze、不重新 prepare；回答后保留当前 fingerprint 并重新展示上述两个选项。
6. 自动或手动选择本身就是一次性的完整冻结授权。只有这两个选择可以紧邻调用 `freeze_hierarchy`；该工具在宿主权限层使用自动批准，不得再询问通用 Yes/No 或触发任何冻结弹窗，也不要向 MCP 发送内部 `confirmed` 字段。适配器会在控制器边界内注入严格的布尔值 `True`；自动/手动只决定当前会话继续还是交接，不改变内部自动确认模式。
7. 冻结后不再逐 TASK 请求方案确认。自动模式继续消费 frontier；手动模式到一次性交接后停止当前会话的执行循环。
