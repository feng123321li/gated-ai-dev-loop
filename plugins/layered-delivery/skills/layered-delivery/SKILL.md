---
name: layered-delivery
description: "治理或恢复分层软件交付。当工作区存在 `.layered-delivery/` 时接管现有 SQLite/Graph 运行；没有治理状态时，用于按最浅合法的 Task、Capability→Task 或 Delivery→Capability→Task 规划并推进开发、门禁、修正、审查和验收。"
---

# Layered Delivery

使用随 Skill 提供的控制器治理可独立交付的软件需求。SQLite/Graph 保存机器状态；对话只解释结果、收集必要确认并执行控制器给出的下一步。

## 读取原则

- 首次只读取本文件。不得预读全部 references、控制器源码、memory、整树报告或全部 evidence 模板。
- 从当前 Skill 元数据解析 `<skill-root>`，不得固化用户目录、Skill 安装位置或操作系统路径。从项目根运行 `python -X utf8 <skill-root>/scripts/hdg.py --help`。
- 以控制器 `--help` 和返回的 `responseContract`、`dispatchPlan`、`evidenceContractRef` 为准；查询直接消费 stdout，非零退出时保留 stderr 并停止解析。

## 核心契约

- 只使用根 `Task`、`Capability → Task` 或 `Delivery → Capability → Task`，选择满足真实聚合责任的最浅结构；Task 是唯一执行叶子。
- 一个需求只有一个 `work-items/<root-id>/` 顶层目录，子级递归放入 `children/`；`.layered-delivery/governance.sqlite3` 是唯一机器权威，Markdown 只是投影。
- 人只评审一份根级方案并一次冻结整树。冻结后的 Task 集、Agent 数、顺序、门禁、重试和恢复由 Graph 决定，不逐节点请求人工启动。
- 每个 requirement 都必须有独立 acceptance；跨需求 acceptance 只能追加集成验收，不能代替任一需求自己的可观察通过条件。
- 同一冻结目标和验收契约内的 P0/P1、回归或审查修正回到原 Task；不得创建重复需求根或用自然语言扩大文件授权。
- Task、聚合 gate、独立审查和用户确认都是显式图节点；只有最终用户确认后的 `COMPLETED` 表示完成。
- Agent 不直接写 SQLite、baseline 或治理投影，也不自动提交、推送、合并、迁移或发布；外部动作需要单独授权。

## 选择入口

1. 存在有效 `.layered-delivery/governance.sqlite3`：读取 [workflow.md](references/workflow.md) 和 [stdin-transport.md](references/stdin-transport.md)，再查询 `graph-frontier`。恢复入口不是诊断用的 `task-context`。
2. 存在治理目录或投影，但数据库缺失、损坏或 schema 不符：保持阻断，不从 Markdown、源码或 memory 反推状态，也不创建替代运行包。
3. 不存在治理目录：仅在用户要求规划或开发新需求时进入新需求流程；只读分析、代码审查或普通问答不创建运行包。

恢复优先使用用户给出的精确 ID/路径、数据库焦点或唯一候选；多个候选才请求选择。数据库有效而 Markdown 缺失时使用 `refresh-projections`。

## 推进流程

1. 新需求读取规划类 references，选择最浅层级并形成完整 schema v3 树；通过 stdin 调用 `prepare-hierarchy`。
2. 展示返回的开发方案和图入口，说明范围、契约、依赖、测试与失败路由。每次确认提示都必须同时展示 `active` 和 `manual` 两种开发方式；修改方案时重新准备同一整树。
3. 用户明确同意方案并选择方式后，使用返回的 `hierarchyFingerprint` 一次调用 `freeze-hierarchy`。Claude Code 还须先满足 [claude-automation.md](references/claude-automation.md) 的权限前置条件。
4. 冻结后每次迁移都重新查询 `graph-frontier`，完整消费 `dispatchPlan`，不自行挑选 Task、排序或确定 Agent 数。
5. `DISPATCH_TASK`：完整消费调度计划并稳定排队，但只在 worker 真正取得执行容量时调用 `dispatch-task`，让 claim 按实际开工即时创建。执行适配器按 `nextWakeAt` 消费到期的 `HEARTBEAT_TASK`；结果写回前先按 `evidenceContractRefs.result` 查询当前 operation 的模板，再用 `task-result --evidence -` 提交。
6. `RUN_GATE`、`REQUEST_REVIEW`、`REQUEST_USER_CONFIRMATION`：先执行 `evidenceContractRef` 指向的只读 `evidence-contract`，只获取当前工作项模板，再从 stdin 提交 evidence；不得读取控制器源码或 memory 文件反推格式。
7. `RETRY_NODE` 或租约失败按 frontier 路由。Task gate 的 P0/P1 FAIL 必须回到 execution 修复、复测；预算耗尽后请求干预，不能无限重跑 gate。
8. 原契约不变但漏列必要文件时，在原 Task 使用 `remediate-task`；契约、拓扑、数据或外部授权变化才回到人工评审。
9. manual 规划会话按 `responseContract` 输出一次可复制交接；接收会话从治理目录和 `graph-frontier` 恢复，不重新选择方式或逐 Task 开工。
10. 根 gate 和独立审查通过后请求最终用户确认；未确认时保持等待。

## 按动作读取

- 规划、拆树和冻结方案：[routing-profiles.md](references/routing-profiles.md)、[delivery-planning.md](references/delivery-planning.md)、[development-plan.md](references/development-plan.md)、[baselines.md](references/baselines.md)
- frontier、开发、并行和失败恢复：[workflow.md](references/workflow.md)、[graph-engineering.md](references/graph-engineering.md)、[development.md](references/development.md)、[parallel-development.md](references/parallel-development.md)、[registry-lifecycle.md](references/registry-lifecycle.md)
- gate、审查、最终确认和同契约补充文件：[acceptance.md](references/acceptance.md)、[validation-remediation.md](references/validation-remediation.md)
- SQLite、事务、投影和传输：[task-registry.md](references/task-registry.md)、[registry-transactions.md](references/registry-transactions.md)、[tracking.md](references/tracking.md)、[stdin-transport.md](references/stdin-transport.md)
- Claude 权限、多仓库和完成后反馈：[claude-automation.md](references/claude-automation.md)、[multi-workspace.md](references/multi-workspace.md)、[post-acceptance-feedback.md](references/post-acceptance-feedback.md)

只读取当前动作需要的一组及其中直接相关文件。
