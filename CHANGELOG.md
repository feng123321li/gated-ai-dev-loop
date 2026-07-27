# 版本更新记录

本文档汇总 `layered-delivery` 各正式版本的主要变化。版本边界以项目清单中的版本号和对应 Git 提交为准；同一版本发布前的连续改动合并记录在该版本下。

后续发布新版本时，应在版本提交中同步更新本文档，按“最新版本在前”的顺序记录发布日期、发布提交、核心能力、兼容性或迁移影响以及主要验证结果。

## 0.14.1 — 2026-07-27

发布提交：`cca5765`

- 将硬过期 claim 从 frontier 的阻断建议升级为正式 `ADVANCE_GRAPH` 动作，使执行循环能够确定性回收 `WORKER_LOST` 并自动创建下一 attempt。
- 强化执行适配器的心跳与收尾契约：当前会话在没有独立适配器时负责续租，代码和测试完成后必须提交 `task-result` 并继续消费 gate/review。
- manual 交接明确硬过期恢复无需人工重置；新 operation 可重新认领并提交已经完成的工作。
- 增加“租约硬过期 → 自动推进 → 新 operation → `IMPLEMENTED`”端到端回归；Python 3.14 全量测试 118 项通过。

## 0.14.0 — 2026-07-27

发布提交：`b14c858`

- 将 Markdown/SVG 投影移出 SQLite `BEGIN IMMEDIATE` 写事务，数据库提交并关闭写连接后再生成投影，缩短写锁持有时间。
- 将高频 `heartbeat-task` 改为增量路径，只更新当前 Task 和必要 graph run 数据，并仅刷新 execution graph、timeline 与 frontier。
- Registry 改为只更新实际变化节点及必要祖先；内容未变化的行跳过 `UPDATE`。
- 投影文件写入增加内容比较，相同内容不再执行临时文件替换和 `fsync`。
- 投影失败时返回 `WORK_ITEM_PROJECTION_REFRESH_REQUIRED`，保留已提交机器状态，并可通过 `refresh-projections` 修复。
- 增加 revision 追赶和交互日志唯一 revision，防止并发提交时旧投影覆盖新状态。
- 新增全局 `--timing`，在 stderr 输出 SQLite、投影和文件写入的分阶段耗时，不改变 stdout JSON 契约。
- 增加性能、并发和投影恢复回归测试；Python 3.14 全量测试 117 项通过。

## 0.13.0 — 2026-07-27

发布提交：`b5bc9f9`

- 将验收模型收紧为 requirement scoped acceptance，每个需求必须拥有独立、可观察的验收条件。
- 跨需求验收只允许作为追加的集成验收，不能替代任一需求自己的通过条件。
- Gate evidence 增加需求追踪信息，验收项、工作项和证据之间保持明确绑定。
- 强化 hierarchy、remediation、runtime FSM 和 SQLite 存储中的验收一致性校验。

## 0.12.0 — 2026-07-27

发布提交：`50f15b0`

- 为 `task-result` 增加按当前 operationId 查询的 result evidence contract，提供 `IMPLEMENTED` 与 `BLOCKED` 模板及逐字段验证。
- 完整结果 artifact 通过 stdin 提交并保存在 SQLite，控制器计算并保存规范摘要。
- 引入可靠心跳、软租约、竞争宽限和硬到期语义，由执行适配器按 `nextWakeAt` 自动续租。
- 增加 `WORKER_LOST` 回收、旧 operation fencing、结构化失败分类和预算内自动重试。
- 将 attempt、租约、心跳和恢复状态纳入 graph frontier、timeline 与可视化投影。

## 0.11.1 — 2026-07-24

发布提交：`5566bcd`

- 精简 `SKILL.md` 入口，只保留核心契约、入口选择、推进流程和按动作读取规则。
- 将详细协议继续保留在按需 references 中，减少首次加载的上下文占用。
- 调整 Codex/Claude 的 Skill 元数据，并增加入口体积和内容路由回归检查。

## 0.11.0 — 2026-07-24

发布提交：`d7c93e8`

- 完善 Task gate 失败后的恢复路由：执行修复、重新认领、复测和再次门禁，不在错误状态下循环 gate。
- 为开发结果、Task gate、聚合 gate、独立审查和用户确认建立更严格的 evidence contract。
- 强化 evidence 与 run、node、attempt、graph fingerprint 和 baseline 的绑定。
- 完善 retry budget、失败分类、图事件回放及修正后的下游失效逻辑。
- 简化公开安装方式，并将 Plugin 源仓库与内部 Marketplace 版本映射拆分维护。

## 0.10.0 — 2026-07-23

发布提交：`7872d25`

- 项目和 Skill 正式更名为 `layered-delivery`。
- 将执行模型升级为 Graph Engineering：编译执行图、治理图、关键路径、frontier、attempt 和事件回放。
- 将 Task 选择、并行数量、调度顺序、重试和失败恢复收归控制器管理。
- 增加 graph runtime 的暂停、恢复、取消、重建和可观察性，并生成 SVG 图形投影。
- 将 evidence artifact 改为通过 stdin 写入 SQLite，隔离仅 evidence 引用过期的历史节点。
- 增加同 Task 验证修正、根节点独立进度、月度 workspace overview、直接导航和可复制 manual handoff。
- 统一工作区级状态迁移图，增强 Claude 自动执行和跨宿主可移植调用。
- 增加交付响应契约以及 Codex/Claude 双宿主 Plugin 载荷和 Marketplace 清单。

## 0.9.0 — 2026-07-17

发布提交：`225f078`

- 将 `.layered-delivery/governance.sqlite3` 确立为唯一机器权威，Markdown 降为可重建的人类可读投影。
- 增加事务化 registry、定义、状态、上下文、报告和交互审计存储。
- 增加层级进度投影、表格化整树进度和明确的当前执行状态。
- 增加一次性 manual requirement handoff，并保留 active/manual 两种开发方式。
- 强化数据库损坏、schema 不符、投影丢失和并发写入时的恢复边界。

## 0.8.0 — 2026-07-17

发布提交：`78a1bc9`

- 精简冻结后的自治交付循环，由控制器持续返回下一步动作和响应契约。
- 统一 active/manual 的 graph 推进语义，减少逐 Task 人工确认。
- 将 schema version 保持为控制器输入和机器契约，不要求用户在自然语言中维护版本信息。
- 完善门禁、审查、用户确认和失败恢复的端到端路由。

## 0.7.0 — 2026-07-17

发布提交：`8607170`

- 改为从一份根级 `development-plan.md` 评审并一次冻结完整需求树。
- 完整物化 Task、Capability 和 Delivery 的 definition、state、baseline 与目录结构。
- 增加 hierarchy fingerprint compare-and-swap，方案变化后旧确认自动失效。
- 统一整树准备、冻结、开发、门禁和最终验收入口。

## 0.6.0 — 2026-07-17

发布提交：`f93282a`

- 将治理控制器从 Node.js 全面迁移到 Python 3.10+ 标准库实现。
- 建立 `pyproject.toml`、`hdg` Python CLI、源码包、Skill 内嵌载荷和构建脚本。
- 移除 Node/npm 运行时和旧安装脚本依赖。
- 将模型、规划、执行、验收、证据、投影和安全文件操作迁移为 Python 测试体系。

## 0.5.0 — 2026-07-17

发布提交：`53332ae`

- 简化 Skill 方案审批和 Agent handoff，减少重复人工确认。
- 补全 Task、Capability、Delivery 的分层验收闭环和根级最终确认。
- 结构化 CLI 输入统一改为 stdin，避免大 JSON 经命令行参数传输。
- 增加冻结前开发方案复核，强化文件、接口、测试和验收项的可评审性。

## 0.4.0 — 2026-07-16

发布提交：`f723c24`

- 引入当前完整 schema v3 层级模型。
- 支持最浅合法的根 Task、Capability→Task 和 Delivery→Capability→Task。
- 恢复 active/manual 开发方式的机械门禁，禁止从自然语言推断执行授权。
- 将控制器构建为随 Skill 分发的内嵌 CLI，减少宿主安装耦合。

## 0.3.0 — 2026-07-16

发布提交：`2b54245`

- 从 gated workflow 升级为 hierarchical delivery governance。
- 增加确定性的 workspace task registry、生命周期恢复和项目规划。
- 根据真实工作规模选择 Task、Capability 或 Delivery 层级。
- 增加分层 work-item 模型、运行时状态和端到端层级流程测试。
- 将长工作流拆为分阶段图示和按需参考文档。

## 0.2.0 — 2026-07-14

发布提交：`2c95ceb`

- 增加可视化 gated workflow、显式开发方式门禁和人类可读进度跟踪。
- 增加自动并行 Agent 调度、自检、分级验收和根级最终验收报告。
- 泛化 Agent handoff 与验收能力路由，支持多工作区开发交接。
- 引入原生 schema v2 workspace gate，并加强跨工作区证据校验。

## 0.1.0 — 2026-07-13

发布提交：`1c7c9d3`

- 首次发布中文 gated AI development Skill。
- 提供 light/full 工作模式、baseline 冻结、开发交接和验收流程。
- 建立安全文件操作、命令校验、模式识别、CLI、安装脚本和完整测试基础。
- 提供最初的 Skill references、配置模板和 Codex UI 元数据。
