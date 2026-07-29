# Layered Delivery

`layered-delivery` 是面向可插拔 Task Loop 的外层交付 Graph 调度器。

当前版本：**0.17.0**

它负责：

- Task Loop 之间的依赖与并行；
- Capability / Delivery 的确定性 Join；
- 多项目、多模块的精确资源声明与互斥；
- claim、heartbeat、lease 和失联恢复；
- 仅针对基础设施故障的预算内自动重试；
- 根级 Review Loop；
- 最终用户确认；
- SQLite 状态、哈希事件链和可重建投影。

它不负责：

- 约定 implementation plan 或 `developmentPlan`；
- 解释文件 `scope` 或授权具体文件修改；
- 内置测试、Gate、Gate→development 修正循环；
- 规定开发 Skill、Gate Skill 或 Skill lifecycle evidence；
- 解析各 Loop 的 payload/result 内容。

这些实现细节都属于对应 Task Loop。不同节点可以选择不同的 Loop 和 Skill。用户在需求阶段给出的 Skill 只作为共享的运行时优先提示，不会预先绑定到某个 Task 或阶段。

## 运行模型

```text
Task Loop ─┐
Task Loop ─┼─> Capability Join ─┐
Task Loop ─┘                    ├─> Delivery Join
Capability Join ────────────────┘
                                  ↓
                            Review Loop
                                  ↓
                          User Confirmation
```

外层依赖图保持 DAG；Loop 内部可以拥有自己的开发、测试、Gate 和修正循环。这样外层 Graph 只处理“何时调度哪个独立工作单元”，内部 Loop 处理“这个工作单元怎样完成”。

## Schema v3

Hierarchy 顶层保存一次共享 Skill Hint：

```json
{
  "schemaVersion": 3,
  "skillHints": [
    {
      "name": "springboot-tdd",
      "purpose": "实际 Loop 处理 Spring Boot 开发时优先采用 TDD"
    }
  ],
  "reviewLoop": {},
  "root": {}
}
```

`skillHints` 是建议性的晚绑定输入。每个 Task/Review Loop 启动后，先根据真实任务和宿主可用 Skill 判断哪些提示适用，再优先原生触发；它可以跳过不适用的提示，也可以使用其他 Skill。调度器不分配 Skill、不查询 catalog、不校验 Skill lifecycle，也不因提示未使用而判定失败。

Task 的调度定义只保留：

```json
{
  "schemaVersion": 3,
  "id": "t-order",
  "kind": "TASK",
  "parentId": "c-order",
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

`loop_context` 同时返回直接 `predecessors` 和传递闭包中的 `upstreamLoopResults`。因此 Capability/Delivery Join 不需要解释或聚合业务 result，最终 Review Loop 仍能读取所有 Task Loop 的不透明结果。

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
→ 用户确认
→ freeze_hierarchy
→ graph_frontier
→ loop_context / dispatch_loop / heartbeat_loop
→ record_loop_result
→ Review Loop
→ record_user_confirmation
```

当前 Plugin 注册 17 个外层调度工具。MCP 绑定一个项目协调根；多仓库或多服务目标通过 Loop ref/payload 与资源声明表达。

## 主要投影

| 文件 | 内容 |
|---|---|
| `.layered-delivery/scheduler.db` | SQLite 机器权威 |
| `.layered-delivery/hierarchy.json` | 冻结层级投影 |
| `.layered-delivery/graph.json` | 编译 Graph 投影 |
| `.layered-delivery/state.json` | 当前运行投影 |
| `.layered-delivery/overview.md` | 人类可读调度总览 |

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
python <skill-creator>/scripts/quick_validate.py skills/layered-delivery
git diff --check
```

项目使用 Python 3.10+ 和标准库，不提供 CLI 入口，也不维护旧 schema 兼容层。

0.17.0 是 schema v3 的破坏性语义替换。若工作区仍有旧 `.layered-delivery/governance.sqlite3`，控制器会明确阻断；请先归档旧运行包，再创建新的 Task Loop Graph。

## 文档

- [Graph Engineering 架构](docs/graph-engineering-upgrade.md)
- [项目实现结构](docs/project-engineering.md)
- [Skill 调度说明](skills/layered-delivery/SKILL.md)
