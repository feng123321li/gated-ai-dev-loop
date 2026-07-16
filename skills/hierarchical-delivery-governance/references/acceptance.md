# 分级验收与交付

## Task gate

Task 只有在状态为 IMPLEMENTED 时可运行 gate。宿主验证：

- baseline 与实际存在的父链指纹；根 Task 无父链；
- 真实 diff 归属和 scope；
- 冻结测试 argv 与退出码；
- 依赖输出与验收项；
- 结构化 evidence 路径和摘要。

PASS 后 Task 为 VERIFIED；FAIL 后为 BLOCKED。任何 gate FAIL 后都必须用当前 baseline 指纹和显式确认执行 `retry-item`，回到 FROZEN 后才能重跑；开发 Agent 的结论不能替代 gate。

根 Task 在此 gate PASS 后达到浅层根 VERIFIED；它不需要虚构 Capability gate。

## Capability gate

decomposition 为 SEALED 且所有计划 Task VERIFIED 后，运行 Capability 集成测试和该级契约检查。Capability 需要自己的 evidence；不能因为子 Task 全绿自动 PASS。

根 Capability 在此 gate PASS 后达到浅层根 VERIFIED；需要独立审查、用户确认或跨 Capability 聚合责任时应使用 Delivery。

## Delivery gate

decomposition 为 SEALED 且所有计划 Capability VERIFIED 后，运行跨能力、端到端、兼容、性能或发布前顶层交付测试。Delivery 也需要独立 evidence 和明确 PASS。Delivery gate PASS 后记录 `VERIFIED / WAITING_FOR_INDEPENDENT_REVIEW`；这不是最终交付完成。

## 语义审查能力

优先级：

1. 与开发者分离的全新只读其他 Agent；
2. 没有其他产品时使用全新、无开发上下文的只读子 Agent；
3. 两者都不可用时生成清晰人工验收包，结论为 `NEED_HUMAN_REVIEW`。

审查者只读取 baseline、context、真实 diff、测试和 evidence，不继承开发对话。隔离审查 PASS，或用户明确接受人工审查结果后，记录 `WAITING_FOR_USER_CONFIRMATION`；只有随后独立记录 `USER_CONFIRMED`，delivery 才进入 `COMPLETED`。审查 evidence 与用户确认 evidence 必须是两个不同的真实文件，CLI 提交相对路径与 SHA-256，宿主在写回前读取文件、复算 hash 并把结构化内容快照写入 registry；每次恢复 registry 也重新核对文件存在、hash、结构和快照。不能只提交 action 标签，也不能复用同一路径或内容。

交付 evidence 使用 schemaVersion 1 JSON：独立审查必须包含 `kind=INDEPENDENT_REVIEW`、非空 reviewer、`isolation=FRESH_READ_ONLY`、`verdict=PASS` 和 `findings.p0/p1=0`；人工审查必须包含 `kind=HUMAN_REVIEW`、非空 reviewer 与 `verdict=ACCEPTED`；用户确认必须包含 `kind=USER_CONFIRMATION`、非空 confirmedBy 与 `decision=CONFIRMED`。

## 严重级别

- `P0`：安全、数据、权限、不可逆或关键服务问题，阻断；
- `P1`：需求、功能、关键边界、事务、兼容或测试问题，阻断；
- `P2`：不阻断当前交付的改进建议，必须展示，不自动实现。

PASS 要求没有 P0/P1 且证据完整。无法证明隔离、证据或改动归属时使用 `NEED_HUMAN_REVIEW`，不要伪装为普通 P1。

## 外部动作

验收不自动提交、推送、合并、迁移、发布或公开。此类动作需要单独明确授权。
