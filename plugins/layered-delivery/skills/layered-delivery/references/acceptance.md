# 分级验收与交付

只在 frontier 返回 `RUN_GATE`、`REQUEST_REVIEW` 或 `REQUEST_USER_CONFIRMATION` 时读取本文件。

## Gate

1. `RUN_GATE` 先通过 action 的 `evidenceContractRef` 调用 `evidence_contract`，再用 `accept_item` 提交结构化 evidence；不预载所有模板，不从源码猜 schema。
2. Task gate 检查真实 diff 归属、有效精确文件授权、冻结测试的真实 argv/exit code/tests run、依赖输出，以及每个 acceptance 的 requirement 映射与独立证据。
3. GATE required Skill 由当前 gate executor 在当前 attempt 中逐项原生调用，并记录 activation/conformance。PASS 要求 `INVOKED + PASS`，且 `skillUsage` 精确说明如何用于范围、diff、测试、R/A 或 findings。
4. PASS 要求 scope 外变更为空、测试均成功、acceptance 逐项 PASS、P0/P1 为空。开发者结论或聊天摘要不能代替 gate。
5. Task PASS 后为 VERIFIED。Capability/Delivery 只有在全部直接子级 VERIFIED 后运行自己的聚合测试和 gate；子级全绿不等于父级自动 PASS。根 Task 不虚构父级 gate。
6. FAIL 按 frontier 回原节点重试。P0/P1 必须修复、回归、复测并重新提交；重试耗尽后请求人工干预。

`development-review.md` 表示结果已回收但等待 gate；`acceptance-report.md` 投影真实 gate、Skill 事件、测试、验收和 findings。两者都不是机器权威。

## 同契约修正

未完成需求若只漏列完成既有 acceptance 所需的精确文件，且目标、需求、验收、接口、数据、测试、拓扑和外部权限不变，使用原 Task 的 `remediate_task`。补充授权追加审计并使必要下游 attempt 失效；baseline 和 graph definition 不变。

来源可以是回归、Task gate、独立审查或 `USER_ACCEPTANCE`。最终验收阶段的调整仍按此增量处理，只刷新受影响需求树。契约变化、新目标、新权限或已经 `COMPLETED` 时不得使用 remediation，必须形成新的人工评审契约。

## 独立审查

优先使用与开发上下文隔离的全新只读 Agent；不可用时生成 `NEED_HUMAN_REVIEW` 的人工验收包。审查只读取冻结契约、真实 diff、测试和 evidence，不继承开发结论。

存在 `FINAL_REVIEW` required Skill 时，实际 reviewer 必须逐项原生调用并记录 activation/conformance 后再提交审查 evidence；Skill 不可用则如实记录 `BLOCKED`，不能用父会话调用或人工接受绕过。只有没有 FINAL_REVIEW Skill 且无法隔离时，才可使用 `record_human_review_acceptance`。

独立/人工审查 PASS 要求 P0/P1 为零。P2 不阻断，但必须在最终报告中展示，不能自动扩展当前需求实现。

## 用户最终确认

根 gate 和审查通过后，frontier 停在 `REQUEST_USER_CONFIRMATION`。向用户展示完整根验收报告；只有用户明确接受后，宿主才可调用 `record_user_confirmation`。不得自签，也不得把冻结确认、测试通过或审查 PASS 当作最终确认。

验收不自动提交、推送、合并、迁移、发布或公开；这些动作需要单独授权。
