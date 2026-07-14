# 多开发 Agent 并行契约

## 目录

- [定位](#定位)
- [并行资格](#并行资格)
- [选择执行拓扑](#选择执行拓扑)
- [并行计划](#并行计划)
- [目录与职责](#目录与职责)
- [写入隔离](#写入隔离)
- [启动与返回](#启动与返回)
- [集成和机械门禁](#集成和机械门禁)
- [失败处理](#失败处理)

## 定位

把 `active/manual` 作为开发方式，把 `single/parallel` 作为执行拓扑。两者正交：开发方式决定谁启动开发者，执行拓扑决定启动一个还是多个开发上下文。

同一轮的所有开发 Agent 必须使用同一冻结契约和结果格式，但可以来自不同 Agent 产品。不得让任一开发 Agent 验收聚合结果。

## 并行资格

Light 固定使用 `single`。Full 只有同时满足以下条件才可提供 `parallel`：

- 至少存在两个可独立交付的冻结任务组；
- 每组绑定明确且不重叠的 `T-NNN`、`A-NNN` 和写入路径；
- 并发组之间没有接口先后、共享状态、数据库迁移、生成文件或语义依赖；
- 能证明工作区隔离或同一仓库内写入集合严格互斥；
- 已定义集成顺序和聚合测试；
- 开发前已有脏改动可以可靠归属。
- 跨目录或跨仓库时，`workspace-coverage.json` 已通过，且每组绑定明确的工作区集合。

任一条件不满足时使用 `single`。不要为了并行而拆分强耦合任务；公共契约、共享文件和跨组集成改动必须由一个任务组独占，依赖它的任务进入后续波次。提供方在本轮新建或修改公共契约时，消费方默认进入后续波次，直到提供方已完成机械验证；只有双方依赖的契约已冻结且可读取时才能同波并行。

## 选择执行拓扑

确定 `active/manual` 后再判断并行资格：

- Light：展示“固定 single”及原因，直接记录；
- Full 但不合格：展示阻断并记录 `single`；
- Full 且合格：先展示任务分组、路径、依赖、波次和最大并发数，再等待用户明确选择 `single` 或 `parallel`。

存在可用的 `parallel` 选项时不得隐藏默认。用户未选择前保持 `WAITING_FOR_EXECUTION_TOPOLOGY_SELECTION`，不得启动开发。

用户选择 `active + parallel` 即授权宿主按照已展示计划自动派遣；不再对每个 Agent 重复询问。用户没有授权自动改变计划，任何 assignment、允许路径、波次或最大并发数变化都必须重新确认。

## 并行计划

把用户确认的计划保存为当前轮次的 `parallel-plan.json`：

```json
{
  "topology": "parallel",
  "developerAgentPolicy": "any-isolated-agent",
  "maxConcurrency": 2,
  "assignments": [
    {
      "agentId": "agent-01",
      "wave": 1,
      "taskIds": ["T-001"],
      "acceptanceIds": ["A-001"],
      "workspaces": [
        {
          "workspaceId": "service-a",
          "allowedPaths": ["src/a/**", "tests/a/**"]
        }
      ],
      "dependsOn": [],
      "isolation": "shared-disjoint"
    }
  ]
}
```

`maxConcurrency` 不得超过独立任务组数量或宿主可证明的运行容量。只有同一波次且相互之间没有未完成依赖的分组可以并发。宿主按 `workspaceId + allowedPaths` 验证路径集合不重叠后再启动。单工作区计划也使用相同结构，避免日后增加工作区时产生歧义。

## 目录与职责

```text
rounds/round-NN/
├── development-mode.json
├── parallel-plan.json
├── agents/
│   └── agent-01/
│       ├── assignment.md
│       ├── prompt.md
│       ├── result.json
│       └── scope-evidence.json
├── integration-result.json
├── gate-evidence.json
└── review.json
```

这些证据全部由宿主写入。开发 Agent 只修改分配的业务路径并返回事实，不得直接写任务目录。

## 写入隔离

每个 Agent 可以读取完成任务所需的仓库内容，但只能写自己的精确 `workspaceId + 路径` 集合。采用以下一种可证明策略：

- `shared-disjoint`：共享项目目录，但所有写入路径严格互斥，且开发阶段不运行会生成未限定文件的命令；
- `isolated-workspace`：宿主使用运行时原生隔离或系统临时目录创建独立工作区，Agent 返回补丁和事实，由宿主集成。

无法证明路径互斥、工具会写共享缓存或生成文件、或隔离工作区来源不一致时，不得并行。宿主只能机械集成无冲突结果；不得自行解决语义冲突。

## 启动与返回

`active + parallel`：宿主把状态更新为 `DISPATCHING_PARALLEL_AGENTS`，自动按波次启动可调度的全新开发子 Agent，每个上下文只收到自己的 assignment、冻结基线、授权工作区、允许路径和结果契约。Agent 产品可以不同，但契约、隔离和归属规则必须相同。派遣完成后进入 `WAITING_FOR_PARALLEL_AGENTS`，无需逐个等待用户批准。

`manual + parallel`：宿主为每个 Agent 输出独立交接卡片，用户在同一种运行时中分别启动全新会话。所有结果返回当前宿主后才能集成。

自动派遣前必须确认宿主确实支持创建全新隔离子 Agent。运行时缺少该能力、无法限制写入范围或无法观察调用状态时，不得用当前会话模拟子 Agent；停止并让用户改选 single 或 manual。

每个 Agent 的结果增加 `agentId`，其余沿用开发结果契约。宿主将结果保存到对应 `agents/<agent-id>/result.json`，并依据真实改动生成 `scope-evidence.json`。Agent 声明不能替代实际路径检查。

## 集成和机械门禁

按以下顺序执行：

1. 验证每个 Agent 的冻结授权、assignment 和工作区来源。
2. 分别检查每个工作区的真实改动是否只落在该 Agent 的允许路径。
3. 拒绝重叠路径、未归属改动和被保护文件。
4. 机械应用无冲突结果；记录 `integration-result.json`。
5. 对集成后的完整 diff 重新分类，逐工作区执行冻结测试，再执行跨服务集成检查并生成聚合 `self-check-report.md`。
6. 只有聚合机械门禁通过后，才按能力路由语义验收：优先把完整 diff、并行计划、归属证据和集成结果交给与开发者分离的全新只读其他 Agent；没有其他产品时使用同宿主全新验收子 Agent；两者都不得继承任何并行开发上下文。两者均不可用时生成完整人工验收包，不得声称独立 PASS。

不得按 Agent 分别获得 `PASS` 后拼接为整体通过；独立验收必须针对最终聚合状态。

## 失败处理

任一 Agent 返回 `BLOCKED`、越界、冲突或外部调用失败时，停止派遣尚未启动的后续波次，暂停集成并更新 `progress.md`。已安全完成的其他 Agent 结果可以保留，但不能进入整体门禁或验收。

确认失败 Agent 零写入时，向用户展示事实并选择重新分配、改为 manual 或降级为 single；不得隐藏重试。已有写入或无法确认归属时返回 `NEED_HUMAN_REVIEW`。语义冲突需要创建全新的集成开发 assignment；超过三轮仍不能集成时请求人工处理。
