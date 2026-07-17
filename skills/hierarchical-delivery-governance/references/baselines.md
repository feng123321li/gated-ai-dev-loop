# 分层 Baseline 与整树冻结

## 节点契约

所有节点只使用当前完整 schema v3，并包含 `id`、`kind`、`gateLevel`、`title`、`goal`、`scope`、`nonGoals`、`requirements`、`acceptance`、`testCommands`、`risks`、`decisions` 和 `developmentPlan`。缺少当前字段或出现未知兼容字段时不恢复、不写入。

- Delivery：额外包含 `decomposition.status`、Capability `children` 和协调层开发计划。
- Capability：额外包含 `parentId`、`decomposition.status/dependsOn`、Task `children` 和协调层开发计划。
- Task：额外包含 `parentId`、`execution {dependsOn, inputs, outputs}` 和精确文件/接口开发计划。

`gateLevel` 只能是 `LIGHT|FULL`，且只有 Task 可以为 `LIGHT`。Task `fileChanges` 必须是 scope 内精确相对路径；协调层 `childPlans`、依赖、波次、R/A 和测试映射必须覆盖全部直接子级。

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
- 不允许只规划但没有磁盘包的 child。

## 准备与统一冻结

1. `prepare-hierarchy` 校验完整 definition，计算各节点 baseline 指纹和一个绑定整树结构的 `hierarchyFingerprint`。
2. 一个需求只写 `work-items/<root-id>/` 一个顶层目录；后代包按 `children/<id>/` 递归嵌套。
3. 根级 `development-plan.md` 是唯一人工评审入口，一次展示完整层级、开发目的、文件、接口/共享契约、依赖波次和测试映射。
4. 用户只确认已经评审并同意当前文件，不抄写 SHA256。Agent 使用准备结果里的层级指纹调用 `freeze-hierarchy`。
5. `freeze-hierarchy` 重新验证层级、所有节点包和根计划文件，然后一次记录批准并冻结全部节点。

等待评审时可以用同一根 ID 重新准备整棵树；新层级指纹使旧确认自动失效。冻结后的拓扑不可用单节点命令改写。

## 冻结后的状态

- Delivery/Capability：`BASELINE_FROZEN / FROZEN`；
- Task：`BASELINE_FROZEN / WAITING_FOR_DEVELOPMENT_MODE_SELECTION`；
- `developmentPlan` 进入 Task 独立上下文；
- 用户选择 active/manual 后，`development-mode.json` 绑定 Task 当前 baseline；
- 每次生成上下文、claim 和 gate 前重新校验整条父链。

子 baseline 仍绑定父级稳定契约与自己的 child contract。无关兄弟不会进入该 child-specific 指纹，但整树层级指纹会绑定本次统一评审包含的全部节点。

## 计划、复核与验收文件

- `development-plan.json/md`：开发前；作为冻结依据。
- `development-review.json/md`：开发结果写回后；对照计划与实际，不代表门禁 PASS。
- `acceptance-report.json/md`：门禁及最终验收阶段；记录证据、结论和用户确认。

## 当前兼容边界

只写当前 schema v3 的单根嵌套结构，不扫描、迁移或解释平铺工作项包、旧 schema 或历史 CLI。
