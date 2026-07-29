# Layered Delivery

`layered-delivery` 是面向可插拔 Loop 的递归交付 Graph 调度器。

当前版本：**0.18.1**

它负责：

- 用 `GROUP` / `TASK` 递归组织多项目、多模块交付；
- 直接兄弟工作项之间的依赖与并行；
- 每个 GROUP 的确定性 Join 和独立 Review；
- Delivery 最终 Review 与用户确认；
- 多项目、多模块的精确资源声明与互斥；
- claim、heartbeat、lease 和失联恢复；
- 仅针对基础设施故障的预算内自动重试；
- SQLite 状态、哈希事件链和可重建投影。

它不负责：

- 约定 implementation plan 或 `developmentPlan`；
- 解释文件 `scope` 或授权具体文件修改；
- 内置测试、Gate、Gate→development 修正循环；
- 规定开发 Skill、Gate Skill 或 Skill lifecycle evidence；
- 解析各 Loop 的 payload/result 内容。

这些实现细节都属于对应 TASK 或 Review Loop。不同节点可以使用不同的 Loop 和 Skill。用户在需求阶段给出的 Skill 只作为共享的运行时优先提示，不会预先绑定到某个工作项或阶段。

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

只有 `RETRYABLE_INFRA` 和 `WORKER_LOST` 会触发外层自动重试。业务 Gate 失败若可在原任务内修正，应由 Task Loop 内部处理，不进入外层事件。

## MCP 流程

```text
workspace_status
→ hierarchy_contract
→ prepare_hierarchy
→ 用户选择：自动执行 / 手动交接 / 调整需求
→ freeze_hierarchy（自动或手动选择即为唯一一次冻结确认）
→ graph_frontier
→ loop_context / dispatch_loop / heartbeat_loop
→ record_loop_result
→ GROUP Review / Delivery Review
→ record_user_confirmation
```

当前 Plugin 注册 17 个外层调度工具。MCP 绑定一个项目协调根；多仓库或多服务目标通过 Loop ref/payload 与资源声明表达。

`freeze_hierarchy` 对模型只暴露 `execution_mode=active|manual`，不暴露内部 `confirmed`。自动和手动都表示完整授权并确认开发，区别只在冻结后由当前会话继续调度还是生成交接；冻结工具在宿主权限层统一走自动批准，MCP 适配器在控制器边界内注入 Python `True`，不得再为同一次冻结追加通用 Yes/No 或其他弹窗。“调整需求”及任何其他反馈均表示未确认，不调用 freeze，而是继续交互并在修改 hierarchy 后重新 prepare。

## 主要投影

递归 GROUP/TASK 是逻辑 Graph 结构，不会继续展开物理子目录。工作区共享一个 SQLite 权威，每个 Delivery 使用稳定的 `delivery.id` 作为投影目录命名空间：

```text
.layered-delivery/
├── scheduler.db
├── d-order/
│   ├── hierarchy.json
│   ├── graph.json
│   ├── state.json
│   └── overview.md
└── d-another-delivery/
    ├── hierarchy.json
    ├── graph.json
    ├── state.json
    └── overview.md
```

| 文件 | 内容 |
|---|---|
| `.layered-delivery/scheduler.db` | SQLite 机器权威 |
| `.layered-delivery/<delivery-id>/hierarchy.json` | Delivery 交付信息与递归 GROUP/TASK 层级投影 |
| `.layered-delivery/<delivery-id>/graph.json` | 编译 Graph 投影 |
| `.layered-delivery/<delivery-id>/state.json` | 冻结启动后生成的当前运行投影 |
| `.layered-delivery/<delivery-id>/overview.md` | 中文人类评审与进度总览 |

目录使用不可变的 Delivery ID，不使用可修改的标题。同一工作区可以保留多个 Delivery 需求目录；GROUP/TASK 的父子关系保存在 hierarchy 和 Graph 内，不映射成下一层文件夹。

`overview.md` 是自包含的冻结评审投影：顶部给出 Delivery 状态与双指纹，随后列出完整 GROUP/TASK 清单，并逐节点展示 summary、`dependsOn`、Loop 引用、资源锁、原始 payload、Join/Review 和运行状态。人类时间统一显示为 UTC+8；SQLite、事件链和 JSON 机器字段继续使用 UTC。

四类投影使用控制器内置的固定版本模板。控制器在状态提交后重新读取 SQLite，并通过原子替换更新固定文件。Agent 通过合法 MCP 输入提交的 hierarchy、summary 和 payload 会按模板成为投影中的领域数据；模板结构、固定相对文件名、序列化和落盘完全由控制器负责。Agent 只通过已注册的 MCP 工具读取调度状态，不直连 `scheduler.db`，也不自行创建、修补或重写投影。投影用于人类评审与进度掌控，不反向成为机器权威。

`prepare_hierarchy` 阶段已经生成 hierarchy、graph 和 overview；`state.json` 在 `freeze_hierarchy` 启动 Graph 后出现并持续刷新。

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

项目使用 Python 3.10+ 和标准库，不提供 CLI 入口，也不维护旧 schema 兼容层。

0.18.0 是 schema v3 的破坏性语义替换：固定 Delivery / Capability / Task 层级被顶层 Delivery 加递归 GROUP/TASK 模型取代。0.17.x 的 `scheduler.db` hierarchy 与更早的 `governance.sqlite3` 均不迁移；请先归档旧 `.layered-delivery` 运行包，再创建新的递归交付 Graph。

## 文档

- [Graph Engineering 架构](docs/graph-engineering-upgrade.md)
- [项目实现结构](docs/project-engineering.md)
- [Skill 调度说明](skills/layered-delivery/SKILL.md)
