# 同一 Task 的验证修正

## 适用边界

开发回归、Task 门禁、独立审查或用户最终验收可能发现：冻结目标和验收契约没有变化，但开发方案漏列了实现该契约所需的精确文件。此时修正仍属于原需求、原 Task 和原验收项，不创建新的根 Task，也不重新请求开发方式或整树冻结。

只有同时满足以下条件才使用验证修正：

- 原需求尚未 `COMPLETED`；
- 目标、需求、验收标准和接口行为不变；
- 数据契约、层级拓扑和外部权限不变；
- 补充内容只是完成已有验收项所需的精确文件；
- Task 没有活动 claim，且补充文件不与其他活动 Task 的写入范围冲突。

已有冻结文件内的普通失败直接使用 MCP `retry_item`；需求已经完成、出现新目标、改变接口行为、数据契约、层级或外部权限时，才规划新的完整需求。不得把“验证时发现的原需求缺陷”包装为新的根 Task 来绕过文件门禁。

## MCP 工具

验证 Agent 先让当前执行结果落到 `IMPLEMENTED`、`BLOCKED` 或 `VERIFIED`，再调用 MCP `evidence_contract(kind="remediation")` 获取绑定模板，并以结构化 evidence 调用 `remediate_task`。

第一条是只读按需查询：控制器从 SQLite 返回该 Task 当前 baseline、冻结 acceptance IDs、已有授权文件、允许 source/action、全部必须为 true 的 assertions 和可直接填充模板。frontier 与 Task context 只携带这条查询的紧凑引用，不内联整树 remediation 模板。不得通过读取 Python 源码或 memory 文件猜格式。

证据结构：

```json
{
  "schemaVersion": 3,
  "kind": "VALIDATION_REMEDIATION",
  "taskId": "t-example",
  "baselineFingerprint": "<sha256>",
  "source": "REGRESSION",
  "summary": "补齐验证发现的公开说明遗漏。",
  "acceptanceIds": ["A-001"],
  "fileChanges": [
    {
      "path": "src/example_api.py",
      "action": "MODIFY",
      "purpose": "使公开说明与已冻结行为一致。"
    }
  ],
  "assertions": {
    "goalUnchanged": true,
    "requirementsUnchanged": true,
    "acceptanceUnchanged": true,
    "interfacesUnchanged": true,
    "dataContractUnchanged": true,
    "testCommandsUnchanged": true,
    "topologyUnchanged": true,
    "externalAuthorityUnchanged": true
  }
}
```

`source` 只能是 `REGRESSION`、`TASK_GATE`、`INDEPENDENT_REVIEW` 或 `USER_ACCEPTANCE`。`fileChanges` 只能追加此前未授权的精确安全路径，不能使用 glob，不能重复冻结方案或既有修正中的文件。

## 状态与审计

控制器在同一 SQLite 事务内：

1. 校验 Task、当前 baseline、验收项、完成状态、活动 claim 和范围冲突；
2. 校验所有“不改变契约”断言，计算 artifact 摘要；
3. 把 artifact、摘要、记录时间和修正前状态快照追加到 `interaction_events`；
4. 保持原 baseline、层级指纹、graph fingerprint、根目录和 `development-plan.md` 不变；
5. 从原 Task execution 节点沿显式边计算下游闭包；若闭包内存在活动 claim，则保持原状态并阻断；
6. 把原 Task 与闭包中已推进的依赖消费者、Task/Capability/Delivery gate、review 和 confirmation 失效，重置相应工作项汇总状态；
7. 为需要重新运行的节点创建下一 attempt，并追加 `GRAPH_INVALIDATED` 图事件；
8. 在 Task context 中生成 `authorizedFileChanges = 冻结 fileChanges + 全部验证修正补充文件`；
9. 在 `development-review.md`、`acceptance-report.md`、`interaction-log.md` 和 `run-timeline.md` 展示修正原因、验收项、文件、节点和时间。

修正后的开发继续使用原 Task ID：重新查询 `graph_frontier`、`dispatch_task`、回归、复测、`task_result` 和 `accept_item`。Gate 的 baseline 仍是原冻结 baseline，但实际变更文件可来自 `authorizedFileChanges`。因此审计能同时回答“人工最初冻结了什么”“验证阶段为什么补充了哪些文件”和“哪些下游节点因此重新运行”，不会制造第二个需求根。
