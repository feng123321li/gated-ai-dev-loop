# 分层 Baseline 与整树冻结

## 节点契约

所有节点只使用当前完整 schema v3，并包含 `id`、`kind`、`gateLevel`、`title`、`goal`、`scope`、`nonGoals`、`requirements`、`acceptance`、`testCommands`、`risks`、`decisions` 和 `developmentPlan`。缺少当前字段或出现未知兼容字段时不恢复、不写入。

- Delivery：额外包含 `decomposition.status`、Capability `children` 和协调层开发计划。
- Capability：额外包含 `parentId`、`decomposition.status/dependsOn`、Task `children` 和协调层开发计划。
- Task：额外包含 `parentId`、`execution {dependsOn, inputs, outputs}` 和精确文件/接口开发计划。

`gateLevel` 只能是 `LIGHT|FULL`，且只有 Task 可以为 `LIGHT`。Task 冻结时的 `fileChanges` 必须是 scope 内精确相对路径；协调层 `childPlans`、依赖、波次、R/A 和测试映射必须覆盖全部直接子级。验证阶段发现原验收项所需文件漏列时，`remediate-task` 以追加审计方式形成补充授权，不改写 baseline 或冻结方案。

## 完整层级 definition

准备入口接受整棵树，而不是单个工作项：

```json
{
  "schemaVersion": 3,
  "root": {
    "definition": {"schemaVersion": 3, "id": "c-example", "kind": "CAPABILITY"},
    "children": [
      {
        "definition": {"schemaVersion": 3, "id": "t-example", "kind": "TASK", "parentId": "c-example"},
        "children": []
      }
    ]
  }
}
```

示例省略了节点的其他必填字段。控制器要求：

- 根只能是 Task、Capability 或 Delivery；
- Task 必须是叶子；
- Capability 的直接子级全部是 Task；
- Delivery 的直接子级全部是 Capability；
- 每个协调节点 baseline 声明的 child 与递归 children 一一对应；
- 所有 ID 唯一，parentId、父范围、子契约和依赖一致；
- 不允许只规划但没有数据库节点记录和 Markdown 投影的 child。

## 准备与统一冻结

1. `prepare-hierarchy` 校验完整 definition，计算各节点 baseline 指纹和一个绑定整树结构的 `hierarchyFingerprint`。
2. 完整 definition、层级和节点状态写入项目级 SQLite；一个需求只生成 `work-items/<root-id>/` 一个 Markdown 顶层目录，后代按 `children/<id>/` 递归嵌套。
3. 每个节点目录都写入自己的 `development-plan.md`；根级同名文件额外聚合整棵树，是唯一人工冻结评审入口。
4. 用户评审当前文件并选择 active/manual，不抄写 SHA256。Agent 使用准备结果里的层级指纹和所选方式调用 `freeze-hierarchy`。
5. `freeze-hierarchy` 重新验证层级、所有节点包和根计划文件，然后用同一次确认记录根级方式并冻结全部节点。

等待评审时可以用同一根 ID 重新准备整棵树；新层级指纹使旧确认自动失效。冻结后的拓扑不可用单节点命令改写。

## 冻结后的状态

- Delivery/Capability：`BASELINE_FROZEN / FROZEN`；
- Task：`BASELINE_FROZEN / FROZEN`；
- `developmentPlan` 进入 Task 独立上下文；
- 根级开发方式只记录在 SQLite，同一次冻结确认中的 active/manual 由全部 Task 继承；
- active 的 Agent 数量、并发度、调度顺序和降级路径由 Graph frontier 的 `dispatchPlan` 自动计算；执行适配器只负责启动或稳定排队，不拥有任务选择权。这些瞬时计划不进入冻结方案或指纹；
- 每次生成上下文、claim 和 gate 前重新校验整条父链。

子 baseline 仍绑定父级稳定契约与自己的 child contract。无关兄弟不会进入该 child-specific 指纹，但整树层级指纹会绑定本次统一评审包含的全部节点。

## 计划、复核与验收文件

- `development-plan.md`：开发前；各节点保留独立计划，根级文件同时作为整树冻结依据。
- `development-review.md`：开发结果写回后；对照计划与实际，不代表门禁 PASS。
- `acceptance-report.md`：门禁及最终验收阶段；记录证据、结论和用户确认。

以上文件都是 SQLite 结构化状态的人类投影，不再生成对应 JSON 文件。开发结果、门禁和最终验收的完整证据 artifact 只通过 stdin 进入控制器，由控制器在当前写事务内校验、计算规范 JSON 摘要，并将 artifact 与摘要一起存入 SQLite。

## 当前数据契约

控制器只写当前 schema v3 的项目级 SQLite 和单根嵌套 Markdown，并按严格字段集合验证工作项、层级和 evidence artifact。旧 JSON 工作区与路径式 evidence 引用不迁移、不兼容。
