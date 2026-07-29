# 递归 Review 与最终确认

Review 是一个标准 Loop，不是 layered-delivery 内置 Gate。

## GROUP Review

每个 GROUP 都有独立 `GROUP_REVIEW_LOOP`：

1. 等待该 GROUP 的所有直接子节点成功。直接子 TASK 的终态是 TASK Loop 成功；直接子 GROUP 的终态是其 GROUP Review 成功。
2. 调度器自动完成 `GROUP_JOIN`，再使本级 GROUP Review Ready。
3. 使用 `loop_context` 读取 Review Loop 的 ref、payload、直接 `predecessors` 和全部 `upstreamLoopResults`；实际 TASK 和下层 Review 结果位于后者。
4. 通过 `dispatch_loop` claim。
5. Review Loop 结合真实审查上下文，优先触发适用的共享 Skill Hint，再自行决定其他审查 Skill、隔离方式、检查项和内部 Gate。
6. 用标准 Loop outcome 返回结果。

只有本级 GROUP Review `SUCCEEDED`，该 GROUP 才成功并向父 GROUP 传播。任一级返回 `BLOCKED` 都停止向上收敛；返回 `REPLAN_REQUIRED` 则回到新的外层图评审。TASK 节点没有外层 Review Loop。

## Delivery Review

根终态成功后，frontier 使 `DELIVERY_REVIEW_LOOP` Ready：

1. 使用 `loop_context` 读取 `delivery.reviewLoop` 的 ref、payload、根前驱和全部 `upstreamLoopResults`。
2. 通过 `dispatch_loop` claim。
3. 独立评估完整 Delivery，并在运行时选择适用的共享 Skill Hint 和其他审查 Skill。
4. 用标准 Loop outcome 返回结果。

Delivery Review 返回 `BLOCKED` 时外层只记录阻断，不解释 findings；返回 `REPLAN_REQUIRED` 时回到新的外层图评审；只有 `SUCCEEDED` 才解锁最终用户确认。

GROUP Review 和 Delivery Review 都自主管理内部 Gate、复审与 Skill 生命周期。调度器只观察标准 Loop 结果，不把 findings 转换成新外层节点。

## 用户最终确认

frontier 返回 `RECORD_USER_CONFIRMATION` 后：

1. 向用户展示根结果、递归 Review 链、Delivery Review 摘要和重要阻断/风险。
2. 等待用户明确接受。
3. 调用 `record_user_confirmation`。

不要用冻结确认、测试通过、内部 Gate PASS 或 Review Loop 自述替代用户确认。完成 Graph 不自动授权提交、推送、合并、迁移或发布。
