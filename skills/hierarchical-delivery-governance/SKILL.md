---
name: hierarchical-delivery-governance
description: "治理可独立交付的软件需求。按最小必要深度组织为 Task、Capability→Task 或 Delivery→Capability→Task；每个需求只生成一个嵌套根目录，根级方案一次评审冻结整棵树，各节点保留独立 development-plan.md 和 progress.md。开发结果写回后生成 development-review，门禁后生成 acceptance report。适用于新需求规划、分层开发、恢复、审计和验收。"
---

# Hierarchical Delivery Governance

使用 Python 3.10+ 标准库控制器，把一个软件需求治理成一棵可恢复、可审查、可机械门禁的交付树。不要用纯对话代替控制器状态。

## 核心规则

- 合法结构只有：根 `Task`、`Capability → Task`、`Delivery → Capability → Task`。
- 使用满足真实聚合责任的最浅结构。Task 是唯一执行叶子；Capability 聚合多个 Task；Delivery 聚合多个 Capability。
- 每个需求只有一个顶层目录：`work-items/<root-id>/`。全部子级递归放在父级 `children/<child-id>/`，不得平铺成多个需求目录。
- 项目级 `governance.sqlite3` 是唯一机器权威；每个实际节点都有独立的 Markdown baseline、`development-plan.md`、`progress.md` 和阶段报告。
- 一次人工同意冻结整棵树。不得逐节点准备或逐节点批准。
- Skill 与 CLI 统一使用当前 Python 控制器和当前数据契约。

## 层级选择

- 一个可以独立开发和验收的结果：根 Task。
- 多个 Task 需要共享契约、依赖或集成门禁：根 Capability。
- 多个 Capability 需要跨能力约束或顶层交付门禁：Delivery。

文件数、接口数、仓库大小或风险等级都不能单独推出更深层级。事实不足时先向用户补齐交付边界、执行叶子、依赖和聚合验收责任，不创建运行包。详细判断见 [routing-profiles.md](references/routing-profiles.md) 与 [delivery-planning.md](references/delivery-planning.md)。

## 正常流程

1. 从当前 `SKILL.md` 解析 `<skill-root>`，只读运行：

   ```text
   python -X utf8 <skill-root>/scripts/hdg.py --help
   ```

2. 只读检查 `.hierarchical-delivery-governance/governance.sqlite3`。存在时从当前数据库恢复；数据库 schema、字段、层级或指纹不一致则阻断，不迁移、不猜测。只有 Markdown 投影缺失时才使用 `refresh-projections` 从数据库重建。
3. 确定最浅合法层级，并形成完整树 definition：

   ```json
   {"schemaVersion":3,"root":{"definition":{"...":"完整根节点定义"},"children":[]}}
   ```

   协调节点声明的每个 child 必须在同一次 definition 中递归物化。
4. 通过 stdin 准备整树；控制器同时生成根级聚合方案/进度和每个节点自己的方案/进度：

   ```text
   python -X utf8 <skill-root>/scripts/hdg.py prepare-hierarchy --definition - --host-runtime <agent> --json
   ```

5. 向用户展示返回的 `humanArtifacts.developmentPlan`，并概述根 ID、树形层级、开发目的、文件、接口/共享契约、依赖波次和测试映射。准备只生成待评审方案，不授权开发。
6. 用户要求修改时，重新准备同一个完整需求树。确认前请用户查看根级 `development-plan.md`，并选择一次 `active` 或 `manual` 开发方式。
7. 用户明确同意当前方案并给出开发方式后，Agent 使用 `prepare-hierarchy` 返回的 `hierarchyFingerprint` 一次提交：

   ```text
   python -X utf8 <skill-root>/scripts/hdg.py freeze-hierarchy --item <root-id> --expected-hierarchy <fingerprint> --development-mode active|manual --confirmed --json
   ```

   人不需要知道、复制或复述指纹。控制器用同一次确认冻结整树并记录根级方式；方案变化后旧指纹必须被拒绝。
8. `active` 下，当前 Agent 冻结后立即自主计算 READY Task 并决定多子 Agent、单 Agent 或当前 Agent 串行。`manual` 下，当前规划会话不开发，控制器在需求根生成一份 `requirement-handoff.md` 和同内容 `handoffPrompt`；用户只需把这份需求级交接复制到一个新会话一次，接收 Agent 随后自行计算 READY、逐 Task `dispatch-task`、开发、门禁并推进整棵树，不得要求用户逐 Task 回复启动。两种方式的执行宿主都在子 Agent 不可用或并发不足时自动降级，不请求用户重新选择方式。Agent 数量、并发度和降级策略属于运行策略，不写入冻结方案或层级指纹。
9. 开发阶段不设置额外人工门禁。Agent 在冻结目标和安全边界内循环“实现 → 回归测试 → 修复 → 复测”，逐 Task 写回 `IMPLEMENTED` 或 `BLOCKED`。同 baseline 且没有活动 claim 的 BLOCKED 由 Agent 自动执行 `retry-item`、重新计算 READY 并继续；只有冻结契约或授权必须变化时才回到人工评审。开发结果不能自行宣布 PASS。
10. 需求根 `progress.md` 使用 Markdown 表格展示整树明细：第一列保留与 `development-plan.md` 相同的工作项 ID、父子顺序和层级，其余列分别展示阶段、状态、门禁、当前执行、节点文件和阶段性产物。“当前执行”对协调节点显示“不适用”，对待执行 Task 显示“未认领”，开发中显示 owner/operationId，结果写回后显示“已释放”。每次控制器写回都会从 SQLite 自动重建该表格，不依赖 Agent 手工改表。
11. 使用 `task-result` 写回结果并生成 `development-review.md`；全部相关回归和复测通过后，使用 `accept-item` 提交门禁验收并生成 `acceptance-report.md`。结构化上下文、结果和报告只存 SQLite。父级必须在子级全部 VERIFIED 后运行自己的聚合 gate。
12. 根工作项 gate PASS 后向用户提交交付，由用户人工验收并最终确认；只有 `COMPLETED` 表示需求完成。

完整状态流和命令参数见 [workflow.md](references/workflow.md) 与控制器 `--help`。

## 冻结前 development plan

`developmentPlan` 必须让人能在开工前知道“为什么改、改什么、怎么验收”，不能只复述需求标题。

- Task：开发目的、场景、精确文件动作、接口/函数契约、实现逻辑、数据与事务、兼容性、测试映射、人工评审重点。
- Capability：每个 Task 的目的与交付物、依赖、共享接口/契约、集成流程、开发波次、Capability 级测试。
- Delivery：每个 Capability 的目的与交付物、跨能力依赖与契约、交付波次、顶层测试。

Task 的 `fileChanges` 必须是 scope 内精确路径；不适用的接口或数据内容明确写“无”，不得虚构。父级 `childPlans` 必须覆盖全部直接子级，且不能把同一段目标复制到三层。字段说明和示例见 [development-plan.md](references/development-plan.md)。

## 三阶段可读文件

```text
冻结前：development-plan.md
开发结果写回后：development-review.md
门禁执行后：acceptance-report.md
```

- 根级 `development-plan.md`：整树唯一冻结评审入口，描述完整层级计划。各子节点同名文件保留该节点的独立开发内容。
- `development-review.md`：对照冻结计划与实际文件、接口、测试和偏差；只表示等待门禁，不表示 PASS。
- `acceptance-report.md`：门禁证据、验收项、测试结果、范围偏差、P0/P1/P2 和结论；根报告持续更新到最终确认。

## 输入与路径

`--definition`、`--evidence` 的一次性 JSON 使用 `-` 从 stdin 读取。不要先写入 `%TEMP%`、`$TMPDIR` 或工作区外文件；跨卷会触发 `PATH_CROSS_VOLUME`。只有宿主不能提供 stdin 时，才可使用工作区内普通临时文件并在同一轮清理，不得把临时输入放进治理控制面。

## SQLite 与交互记录

- 每个项目只有一个 `.hierarchical-delivery-governance/governance.sqlite3`；多个需求根通过 ID 隔离，不为每个 `<root-id>` 建库。
- `<root-id>` 目录只有 Markdown 投影。手工删除目录不会删除需求状态，后续刷新还会重建；不得用手删目录代替控制器状态操作。
- 需要保留人机协作事实时，用 `record-interaction` 写入简短的指令、决策或状态摘要；`interaction-log` 查询结构化事件，需求根 `interaction-log.md` 供人工审计。不得保存隐藏思考过程、密钥或不必要的原始对话。
- 旧 JSON 控制目录不迁移、不兼容；发现后明确阻断并要求使用新的治理目录。

## 开发与安全边界

- Task 只写冻结的 `developmentPlan.fileChanges`；scope 是外边界，不代表 scope 内任意文件都已授权。
- 不继承需求分析或其他 Task 对话；上下文必须由控制器从 SQLite 重建。
- 开发 Agent 不修改 SQLite、baseline、治理投影或 `.git/**`。
- 不自动提交、推送、合并、迁移、发布或执行其他外部动作；需要用户单独明确授权。
- 控制器实现仓库只有在用户明确要求 dogfood 时才能创建运行包，且所有写控制面的命令必须带 `--dogfood`。
- 控制器缺失、版本不符或机械校验失败时保持阻断，不用“等价流程”绕过。

## 按需参考

- registry、包结构与恢复：[task-registry.md](references/task-registry.md)
- 生命周期与自动重试：[registry-lifecycle.md](references/registry-lifecycle.md)
- baseline、指纹与一次冻结：[baselines.md](references/baselines.md)
- 事务、claim 与并发：[registry-transactions.md](references/registry-transactions.md)
- 独立开发上下文：[development.md](references/development.md)
- 并行调度：[parallel-development.md](references/parallel-development.md)
- 进度与父级聚合：[tracking.md](references/tracking.md)
- 门禁、独立审查与最终确认：[acceptance.md](references/acceptance.md)
- 多工作区：[multi-workspace.md](references/multi-workspace.md)
- 验收后反馈：[post-acceptance-feedback.md](references/post-acceptance-feedback.md)
