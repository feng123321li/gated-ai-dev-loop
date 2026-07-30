# 递归 Review 与最终确认

Review 是一个标准 Loop，不是 layered-delivery 内置 Gate。

## GROUP Review

每个 GROUP 都有独立 `GROUP_REVIEW_LOOP`：

1. 等待该 GROUP 的所有直接子节点成功。直接子 TASK 的终态是 TASK Loop 成功；直接子 GROUP 的终态是其 GROUP Review 成功。
2. 调度器自动完成 `GROUP_JOIN`，再使本级 GROUP Review Ready。
3. 使用 `loop_context` 读取 Review Loop 的 ref、payload、直接 `predecessors` 和全部 `upstreamLoopResults`；实际 TASK 和下层 Review 结果位于后者。
4. 通过 `dispatch_loop` claim。
5. Review Loop 结合真实审查上下文，优先触发适用的共享 Skill Hint，再自行决定其他审查 Skill、隔离方式、检查项和内部 Gate。payload 只提供目标、明确约束和已知验收点；Review 必须结合真实代码、契约和数据链路推导必要条件。字段来源、数据完整性、静默丢失、边界和回归风险即使未逐条写明，也仍属于当前目标内的 Review 范围。
6. 将每项问题显式分类为 P0、P1 或 P2。P0/P1 是成功前必须关闭的问题：不退出 Loop，调整内部方案，自行修正或派遣内部修正上下文，补充必要验证，再由独立 Review 重新检查。P2 不阻断成功，但必须逐项列示并说明接受理由或后续处置。
7. P0/P1 全部修复、验证并独立复审后，才可用 `SUCCEEDED` 返回结果；同时在 `result.reviewFindings` 提交完整问题清单，P2 不得因非阻断而省略。

只有本级 GROUP Review `SUCCEEDED`，该 GROUP 才成功并向父 GROUP 传播。`BLOCKED` 不是 Review 失败状态，只能在当前 scope 和权限内已经没有继续路径时使用并显式分类；`REPLAN_REQUIRED` 只用于必须改变冻结依赖、资源声明或拓扑的情况。任一级真实 `BLOCKED` 都停止向上收敛；`REPLAN_REQUIRED` 由 frontier 进入 `REPLAN_HIERARCHY`，按执行说明等待取消授权和替代图评审。TASK 节点没有外层 Review Loop。

## Delivery Review

根终态成功后，frontier 使 `DELIVERY_REVIEW_LOOP` Ready：

1. 使用 `loop_context` 读取 `delivery.reviewLoop` 的 ref、payload、根前驱和全部 `upstreamLoopResults`。
2. 通过 `dispatch_loop` claim。
3. 独立评估完整 Delivery，并在运行时选择适用的共享 Skill Hint 和其他审查 Skill。
4. 对问题分类为 P0/P1/P2。P0/P1 在当前 Loop 内完成修正、验证和独立复审；冻结 Graph 不要求沿用已经证明不完整的实现方案。P2 可不阻断成功，但必须逐项列入验收结果。
5. 只有 P0/P1 全部关闭或遇到真实外部阻断时，才用标准 Loop outcome 返回结果，并在 `result.reviewFindings` 提交完整清单。

Delivery Review 对普通实现缺陷、测试失败或未显式列入需求的工程正确性问题不得返回 `BLOCKED`。真实 `BLOCKED` 时外层只记录阻断，不解释 findings；返回 `REPLAN_REQUIRED` 时由 frontier 进入 `REPLAN_HIERARCHY`；只有 `SUCCEEDED` 才解锁最终用户确认。

GROUP Review 和 Delivery Review 都自主管理内部 Gate、修正闭环、复审与 Skill 生命周期。独立性要求 Review 独立发现和重新验证，不禁止它推动或执行当前授权范围内的修正。`result.reviewFindings` 是确定性验收投影约定，不是新的调度状态；调度器不解释问题、不把问题转换成外层节点，也不创建 Graph 环。

Review 成功时使用以下结果约定；没有问题也提交空数组：

```json
{
  "reviewFindings": [
    {
      "severity": "P0 | P1 | P2",
      "summary": "问题摘要",
      "status": "RESOLVED | ACCEPTED | OPEN",
      "resolution": "修正或接受说明",
      "evidence": "验证或复审证据"
    }
  ]
}
```

`SUCCEEDED` 不得携带状态为 `OPEN` 的 P0/P1；P2 必须始终出现在验收报告的问题表中。问题的内部修正过程仍留在 Review Loop，不新增 Graph 节点或状态。

## 用户最终确认

frontier 返回 `RECORD_USER_CONFIRMATION` 后：

1. 向用户展示根结果、递归 Review 链、Delivery Review 摘要和重要阻断/风险。
2. 等待用户明确接受。
3. 用户明确接受本身就是写入最终验收的授权；使用控制器接受的可移植 ASCII `confirmed_by` 调用 `record_user_confirmation`，不要再请求通用 Yes/No，也不要触发宿主权限弹窗。
4. Graph 进入 `COMPLETED` 后只返回简短终态摘要；不要自行写入宿主记忆、触发持续学习、维护旧 schema 笔记或更新任何项目文件。

不要用冻结确认、测试通过、内部 Gate PASS 或 Review Loop 自述替代用户确认。完成 Graph 不自动授权提交、推送、合并、迁移或发布。
