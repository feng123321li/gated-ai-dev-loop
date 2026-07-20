# 分级验收与交付

## Task gate

Task 只有在状态为 IMPLEMENTED 时可运行 gate。`task-result` 写回后，控制器把结构化结果保存到 SQLite，并立即生成状态为“等待门禁”的 `development-review.md`，用于复核冻结计划与实际开发结果；此时尚未生成验收报告，宿主不能在此状态结束工作。正常 gate 使用 `accept-item`，并在门禁执行后生成 `acceptance-report.md`。控制器验证：

- baseline 与实际存在的父链指纹；根 Task 无父链；
- 真实 diff 归属和 Scope；
- Task 实际变更文件全部在人工评审并冻结的 `developmentPlan.fileChanges` 或控制器已记录的验证修正补充文件中；未经 `remediate-task` 记录的文件即使位于原 scope 内也不能 PASS；
- 冻结测试 argv、退出码和适用的 Tests run；
- 依赖输出和全部验收项；
- 从 stdin 收到的完整 evidence artifact 覆盖当前工作项和当前 baseline；控制器在当前 SQLite 写事务内完成校验与摘要计算；
- PASS evidence 中 Scope 外变更为空、全部测试退出码为 0、全部验收项为 PASS、P0/P1 为空。

PASS 后 Task 为 VERIFIED；FAIL 后为 BLOCKED，并把范围、测试、验收项和 findings 写入用户报告。Agent 修复后使用当前 baseline 指纹执行 `retry-item`，自动回到 FROZEN 并继续回归与复测；只有冻结需求或授权需要变化时才回到人工评审。开发 Agent 的结论不能替代 gate，正常 PASS 路径使用 `accept-item`。

如果失败只是暴露原验收项所需文件被开发方案漏列，且目标、需求、验收、接口行为、数据、拓扑和外部权限不变，Agent 不创建新的根 Task。它通过 `remediate-task --evidence -` 在原 Task 下记录修正原因、验收项和补充文件；控制器保持 baseline 不变，失效该 Task 与已通过的祖先 gate，修正后重新执行完整门禁。具体证据见 [validation-remediation.md](validation-remediation.md)。

根 Task 在此 gate PASS 后达到浅层根 VERIFIED 并进入最终验收；它不需要虚构 Capability gate。

## Capability gate

decomposition 为 SEALED 且所有计划 Task VERIFIED 后，运行 Capability 集成测试和该级契约检查。Capability 需要自己的 evidence；不能因为子 Task 全绿自动 PASS。

根 Capability 在此 gate PASS 后达到浅层根 VERIFIED 并进入最终验收；不需要为了独立审查和用户确认而虚构 Delivery。

## Delivery gate

decomposition 为 SEALED 且所有计划 Capability VERIFIED 后，运行跨能力、端到端、兼容、性能或发布前顶层交付测试。Delivery 也需要独立 evidence 和明确 PASS。Delivery gate PASS 后记录 `VERIFIED / WAITING_FOR_INDEPENDENT_REVIEW`；这不是最终交付完成。

## 用户验收报告

每个实际执行过门禁的工作项都在 SQLite 保存结构化报告，并维护一份面向用户的 `acceptance-report.md`：

- Task result 后由 `development-review.md` 显示开发摘要、变更文件、开发侧测试事实和“等待门禁”，不提前创建验收报告；
- gate 后显示冻结开发目的与接口/子级契约、计划文件与实际文件差异、验收项逐条结论、测试 argv/退出码/Tests run、Scope 外变更、P0/P1/P2 和门禁结论；
- 根工作项继续显示独立/人工审查结论与用户确认，直到最终状态为“已完成”；
- 同一 Task 存在验证修正时，开发复核和验收报告必须显示发现阶段、关联验收项、修正原因、补充授权文件和记录时间，并把这些文件纳入有效授权集合；
- 子工作项报告在该级 VERIFIED 后结束，不重复请求用户确认。

开发复核与验收报告都是 SQLite 和 evidence 快照的可重建人类投影，不取代机器权威。`workspace-overview.md` 必须按实际阶段提供对应入口。

## 语义审查能力

优先级：

1. 与开发者分离的全新只读其他 Agent；
2. 没有其他产品时使用全新、无开发上下文的只读子 Agent；
3. 两者都不可用时生成清晰人工验收包，结论为 `NEED_HUMAN_REVIEW`。

审查者只读取 baseline、context、真实 diff、测试和 evidence，不继承开发对话。隔离审查 PASS，或用户明确接受人工审查结果后，记录 `WAITING_FOR_USER_CONFIRMATION`；只有随后独立记录 `USER_CONFIRMED`，治理根才进入 `COMPLETED`。审查 evidence 与用户确认 evidence 必须是两个不同的完整 artifact，分别通过 `--evidence -` 从 stdin 提交。控制器在各自 SQLite 写事务内校验内容、计算规范 JSON 的 SHA-256，并把 artifact 与摘要一起写入 SQLite；CLI 拒绝文件路径和调用方提供的摘要。恢复只依赖 SQLite 快照，不依赖外部 JSON。不能只提交 action 标签，也不能复用同一内容。

最终验收 evidence 使用当前 schemaVersion 3 JSON：独立审查必须包含 `kind=INDEPENDENT_REVIEW`、非空 reviewer、`isolation=FRESH_READ_ONLY`、`verdict=PASS` 和 `findings.p0/p1=0`；人工审查必须包含 `kind=HUMAN_REVIEW`、非空 reviewer 与 `verdict=ACCEPTED`；用户确认必须包含 `kind=USER_CONFIRMATION`、非空 confirmedBy 与 `decision=CONFIRMED`。

## 严重级别

- `P0`：安全、数据、权限、不可逆或关键服务问题，阻断；
- `P1`：需求、功能、关键边界、事务、兼容或测试问题，阻断；
- `P2`：不阻断当前交付的改进建议，必须展示，不自动实现。

PASS 要求没有 P0/P1 且证据完整。无法证明隔离、证据或改动归属时使用 `NEED_HUMAN_REVIEW`，不要伪装为普通 P1。

## 外部动作

验收不自动提交、推送、合并、迁移、发布或公开。此类动作需要单独明确授权。
