# Review Loop 与最终确认

Review 是一个标准 Loop，不是 layered-delivery 内置 Gate。

## Review Loop

根 Join 或根 Task Loop 成功后，frontier 使 `REVIEW_LOOP` Ready：

1. 使用 `loop_context` 读取 Review Loop 的 ref、payload、直接 `predecessors` 和全部 `upstreamLoopResults`；根为 Join 时，实际 Task 结果位于后者。
2. 通过 `dispatch_loop` claim。
3. Review Loop 结合真实审查上下文，优先触发适用的共享 Skill Hint，再自行决定其他审查 Skill、隔离方式、检查项和内部 Gate；提示不预先绑定 Review。
4. 用标准 Loop outcome 返回结果。

Review Loop 返回 `BLOCKED` 时外层只记录阻断，不解释 findings；返回 `REPLAN_REQUIRED` 时回到新的外层图评审；只有 `SUCCEEDED` 才解锁最终用户确认。

## 用户最终确认

frontier 返回 `RECORD_USER_CONFIRMATION` 后：

1. 向用户展示根结果、Review Loop 摘要和重要阻断/风险。
2. 等待用户明确接受。
3. 调用 `record_user_confirmation`。

不要用冻结确认、测试通过、内部 Gate PASS 或 Review Loop 自述替代用户确认。完成 Graph 不自动授权提交、推送、合并、迁移或发布。
