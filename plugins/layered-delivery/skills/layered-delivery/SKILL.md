---
name: layered-delivery
description: "治理可独立交付的软件需求。按最小必要深度组织为 Task、Capability→Task 或 Delivery→Capability→Task，由控制器编译执行图与治理图；根级方案一次评审冻结整棵树和图，graph frontier 驱动 Task、门禁、审查与确认。适用于新需求规划、分层开发、恢复、审计和验收。"
---

# Layered Delivery

使用 Python 3.10+ 标准库控制器，把一个软件需求治理成一棵可评审的交付树，并确定性编译为可恢复、可调度、可机械门禁的 Delivery Graph。不要用纯对话代替控制器状态。

## 核心规则

- 合法结构只有：根 `Task`、`Capability → Task`、`Delivery → Capability → Task`。
- 使用满足真实聚合责任的最浅结构。Task 是唯一执行叶子；Capability 聚合多个 Task；Delivery 聚合多个 Capability。
- 每个需求只有一个顶层目录：`work-items/<root-id>/`。全部子级递归放在父级 `children/<child-id>/`，不得平铺成多个需求目录。
- 开发、回归、门禁、独立审查或最终验收发现的修正，只要仍为同一冻结目标和验收契约，就必须回到原 Task；不得为修复同一需求另建根 Task。
- 项目级 `governance.sqlite3` 是唯一机器权威；每个实际节点都有独立的 Markdown baseline、开发方案、节点进度和阶段报告。需求根的整树总进度使用 `progress.md`，根节点自身进度使用 `node-progress.md`，子节点自身进度使用各自目录的 `progress.md`。
- 一次人工同意冻结整棵树。不得逐节点准备或逐节点批准。
- 人只负责前段的需求/方案讨论与冻结确认，以及末段的最终验收确认；冻结后的中间开发过程由 Graph 管理，不把选任务、定 Agent 数、失败路由或恢复决策退回给人或执行宿主。
- 用户评审层级与开发方案，控制器编译执行图与治理图；不要让用户直接定义任意节点、边或循环。
- Task execution、Task/Capability/Delivery gate、root review 和 user confirmation 是显式图节点。gate、review 和 confirmation 不能只作为对话约定。
- 冻结依赖合同保持 DAG；重试、回退、暂停、恢复和取消由控制器冻结的运行时 FSM 与 Router Policy 表达。不要把运行时循环写回依赖边。
- 带 graph fingerprint 的哈希事件链是运行事实权威；graph/node run 是可回放重建的查询快照。`graph-frontier` 是调度入口，`ready-tasks` 只是其中 `DISPATCH_TASK` 动作的 Task ID 投影。
- retry/remediation 只创建新的 node attempt 或传播失效，不能改写已冻结图定义。
- Skill 与 CLI 统一使用当前 Python 控制器和当前数据契约。

## 层级选择

- 一个可以独立开发和验收的结果：根 Task。
- 多个 Task 需要共享契约、依赖或集成门禁：根 Capability。
- 多个 Capability 需要跨能力约束或顶层交付门禁：Delivery。

文件数、接口数、仓库大小或风险等级都不能单独推出更深层级。事实不足时先向用户补齐交付边界、执行叶子、依赖和聚合验收责任，不创建运行包。详细判断见 [routing-profiles.md](references/routing-profiles.md) 与 [delivery-planning.md](references/delivery-planning.md)。

## 正常流程

1. 从当前 Skill 元数据解析 `<skill-root>`（即当前已加载 `SKILL.md` 所在目录），只读运行：

   ```text
   python -X utf8 <skill-root>/scripts/hdg.py --help
   ```

   `<skill-root>` 是宿主无关的逻辑占位符。实际执行时由宿主解析为当前安装位置，不得固化用户目录、Skill 安装位置或操作系统路径，也不得把解析后的本机绝对路径写入交接、方案或治理状态。

2. 只读检查 `.layered-delivery/governance.sqlite3`。存在时从当前数据库恢复；数据库 schema、ID、拓扑、路径、层级或指纹损坏则阻断，不迁移、不猜测。仅当历史工作项的 evidence 引用不符合当前契约、完整 artifact 仍在 SQLite 且其他结构全部有效时，控制器把该节点设为只读隔离：不迁移、不改写它，但允许其他新需求和有效兄弟节点继续。只有 Markdown 投影缺失时才使用 `refresh-projections` 从数据库重建。
3. 确定最浅合法层级，并形成完整树 definition：

   ```json
   {"schemaVersion":3,"root":{"definition":{"...":"完整根节点定义"},"children":[]}}
   ```

   协调节点声明的每个 child 必须在同一次 definition 中递归物化。
4. 通过 stdin 准备整树；控制器同时生成根级聚合方案、确定性 Delivery Graph、整树进度和每个节点自己的方案/节点进度：

   ```text
   python -X utf8 <skill-root>/scripts/hdg.py prepare-hierarchy --definition - --host-runtime <agent> --json
   ```

5. 向用户展示返回的 `humanArtifacts.developmentPlan`、`humanArtifacts.executionGraph` 与 `humanArtifacts.stateTransitionGraph`，并概述根 ID、树形层级、开发目的、文件、接口/共享契约、依赖波次、测试映射、图节点摘要和运行时失败路由。准备只生成待评审方案和只读图，不授权开发。最终确认提示必须消费 `responseContract`，同时展示 `active` 和 `manual` 两种开发方式，不得自行删减选择。当前宿主是 Claude Code 时，返回的 `hostAutomation` 是 active 的权限前置条件；必须在用户选择 active 并冻结前展示，不能等 Task 已认领后再处理 Process 授权。
6. 用户要求修改时，重新准备同一个完整需求树。每次重新准备后的确认提示仍必须同时展示 `active` 和 `manual` 两种开发方式；不能因为用户多次修改方案、上下文压缩或先前曾偏向某种方式而省略另一种。确认前请用户查看根级 `development-plan.md`，并选择一次开发方式。
7. 用户明确同意当前方案并给出开发方式后，Agent 使用 `prepare-hierarchy` 返回的 `hierarchyFingerprint` 一次提交。若当前宿主是 Claude Code 且选择 active，须先由用户级设置、模式选择器或启动参数满足 `hostAutomation`，再冻结并立即执行；聊天提示不能代替权限模式配置：

   ```text
   python -X utf8 <skill-root>/scripts/hdg.py freeze-hierarchy --item <root-id> --expected-hierarchy <fingerprint> --development-mode active|manual --confirmed --json
   ```

   人不需要知道、复制或复述指纹。控制器用同一次确认冻结整树并记录根级方式；方案变化后旧指纹必须被拒绝。
8. `active` 下，当前 Agent 冻结后立即查询 `graph-frontier`，严格消费控制器返回的 `dispatchPlan` 与 `DISPATCH_TASK`、`RUN_GATE`、`REQUEST_REVIEW`、`REQUEST_USER_CONFIRMATION` 动作。需要提交 evidence 的动作只携带紧凑 `evidenceContractRef`；执行前按其中的命令调用只读 `evidence-contract --item <id> --kind gate|remediation|review|confirmation --json`，由控制器从 SQLite 按需生成当前精确模板、acceptance IDs、test argv 和有效文件授权。不得读取控制器源码、memory 文件或把整树 evidence schema 预载入上下文来反推格式。Graph 自动计算本轮全部安全 Task、稳定派发顺序、目标 Agent 数和并行组；执行适配器不得挑选子集或另定顺序。平台容量不足时只把未立即启动项按原顺序排队，子 Agent 不可用时由当前 Agent 串行消费同一队列，每次状态迁移后重新计算，均不请求用户选择。`manual` 下，当前规划会话不开发，控制器在需求根生成完整 `requirement-handoff.md`、同内容 `handoffPrompt` 和简短 `handoffCommand`。manual 冻结成功后的首次最终回复必须按 `responseContract` 提供一个纯文本代码块，让用户一次复制到其他 Agent 后即可接管完整需求；允许使用返回的 `handoffCommand`，也允许生成语义等价文本，不要求逐字复述 `handoffCommand`。等价文本必须包含 `responseContract.requiredSemantics`，不得只给出 `requirement-handoff.md` 链接，也不得要求用户打开文件后补充复制。若用户明确交接到 Claude Code，还必须展示 `claudeCodeAutoHandoff`：Desktop/IDE 使用 `desktopInstruction` 后粘贴交接文本，CLI 交互式任务使用 `interactiveCommand`，无人值守任务使用 `unattendedCommand`。文件链接作为查看完整交接和冻结方案的辅助入口放在代码块之后。接收 Agent 随后从同一 graph run 的 `graph-frontier` 恢复并自动执行完整调度计划、开发、门禁和恢复，推进整棵图；`task-context` 只是诊断预览，不是恢复或开工入口。不得要求用户逐 Task 回复启动。运行时调度计划不写入冻结方案、层级指纹或图指纹。
9. 开发阶段不设置额外人工门禁。Graph 执行循环在冻结目标和安全边界内驱动 Agent 循环“实现 → 回归测试 → 修复 → 复测”，逐 Task 写回 `IMPLEMENTED` 或 `BLOCKED`。BLOCKED artifact 必须提供 `failure.class/code/summary`。`RETRYABLE` 由控制器在尝试预算内自动创建下一 attempt；第三次仍失败则写入 `RETRY_EXHAUSTED` 并阻断。Task gate 因 P0/P1 等发现 FAIL 后，执行循环按 frontier 在剩余 gate attempt 预算内调用 `retry-item`；控制器同时失效该 Task 的 execution 与 gate 并回到 `DISPATCH_TASK`，修复写回后才重新 `RUN_GATE`，预算耗尽则请求人工干预，不能无限重试。`CONTRACT_CHANGE`、`EXTERNAL_AUTHORITY`、`NON_RETRYABLE` 和 `REMEDIATION_REQUIRED` 必须按 frontier 建议路由，不能误重试。长任务用 `heartbeat-task` 续租；Graph 执行循环定期执行 `advance-graph`，让过期 claim 按 `WORKER_LOST` 自动恢复。只有显式用户意图才使用 `pause-task`、`resume-task` 或经确认的 `cancel-graph-run`。若验证发现为满足原验收项必须补充冻结方案遗漏的精确文件，但目标、需求、验收、接口行为、数据契约、拓扑和外部授权均不变，则使用 `remediate-task --evidence -` 在原 Task 下追加验证修正授权。控制器从该 Task execution 沿显式图边失效必要后继、依赖消费者和聚合门禁，再创建新 attempt；失效范围有活动 claim 时阻断。不得重新 `prepare-hierarchy` 创建重复需求根。只有上述契约或授权事实确实变化时才回到人工评审。开发结果不能自行宣布 PASS。
10. `workspace-overview.md` 只保留按最近更新时间倒序的全局需求索引，展示根类型、状态、门禁、后代进度和方案/总进度/月度明细入口；物理目录仍使用稳定根 ID，不追加日期。`workspace-overview/YYYY-MM.md` 是月度索引，每个需求的层级表格写入 `workspace-overview/YYYY-MM/<root-id>.md`；全局索引直接链接单需求文件，不依赖跨文件标题锚点。这样避免单一总览过长和 Markdown 折叠树形文本。面向人的状态报告必须把 SQLite 和控制器 JSON 中的 UTC 时间转换为当前运行环境的本机时区，并显式标注 UTC 偏移（例如 `UTC+08:00`）；SQLite、事件链和 JSON 机器字段保持 UTC 原值。工作区投影的创建时间和需求开始时间精确到分，只有最终用户确认后的 `COMPLETED` 才展示完成日期，否则显示“未完成”。需求根 `progress.md` 继续使用 Markdown 表格展示整树明细：第一列保留与 `development-plan.md` 相同的工作项 ID、父子顺序和层级，其余列分别展示阶段、状态、门禁、当前执行、节点文件和阶段性产物。根节点行的节点进度链接 `node-progress.md`，子节点行链接各自 `progress.md`，不得让根节点进度回链整树文件。“当前执行”对协调节点显示“不适用”，对待执行 Task 显示“未认领”，开发中显示 owner/operationId，结果写回后显示“已释放”。每次控制器写回都会从 SQLite 自动重建这些文件，不依赖 Agent 手工改表。
11. 使用 `task-result` 写回结果并生成 `development-review.md`；验证修正会在同一文件追加“验证修正”明细，并进入原 Task 的授权文件集合。全部相关回归和复测通过后，使用 `accept-item` 提交门禁验收并生成 `acceptance-report.md`。结构化上下文、结果、修正和报告只存 SQLite。父级必须在子级全部 VERIFIED 后运行自己的聚合 gate。
12. 根工作项 gate PASS 后向用户提交交付，由用户人工验收并最终确认；只有 `COMPLETED` 表示需求完成。

完整状态流和命令参数见 [workflow.md](references/workflow.md) 与控制器 `--help`。

## Claude Code 无人值守权限

- Claude Code 的权限模式不能由聊天提示切换。只能由用户级或托管设置、Desktop/IDE 模式选择器，或 CLI `--permission-mode auto` 启动参数设置；项目级 `.claude/settings*.json` 不能把会话切换到 Auto。
- `acceptEdits` 不是无人值守模式：它会自动接受编辑，但 Python、测试和控制器等 Process 仍可能请求授权。自动交接和 Claude active 都优先使用 `auto`。
- Claude active 必须在冻结前满足 `hostAutomation.claimPrecondition`。manual 交接到 Claude 优先使用控制器返回的 `claudeCodeAutoHandoff`。不得先 `dispatch-task` 占用租约，再等待用户处理权限弹窗。
- 不自动修改用户的 Claude 权限设置，不把 `bypassPermissions` 作为默认方案；只有用户明确配置的隔离容器或虚拟机才可考虑该模式。Auto 对敏感或越权动作仍可阻断，这类阻断按外部授权或 Graph 失败路由处理。

完整配置与两类入口见 [claude-automation.md](references/claude-automation.md)。

## 冻结前 development plan

`developmentPlan` 必须让人能在开工前知道“为什么改、改什么、怎么验收”，不能只复述需求标题。

- Task：开发目的、场景、精确文件动作、接口/函数契约、实现逻辑、数据与事务、兼容性、测试映射、人工评审重点。
- Capability：每个 Task 的目的与交付物、依赖、共享接口/契约、集成流程、开发波次、Capability 级测试。
- Delivery：每个 Capability 的目的与交付物、跨能力依赖与契约、交付波次、顶层测试。

Task 的 `fileChanges` 必须是 scope 内精确路径；不适用的接口或数据内容明确写“无”，不得虚构。父级 `childPlans` 必须覆盖全部直接子级，且不能把同一段目标复制到三层。字段说明和示例见 [development-plan.md](references/development-plan.md)。

## 核心可读文件

```text
冻结前：development-plan.md
图结构：execution-graph.md
运行状态与失败路由：.layered-delivery/state-transition-graph.md（工作区共享）
下一步与关键路径：frontier.md
运行过程：run-timeline.md
开发结果写回后：development-review.md
门禁执行后：acceptance-report.md
```

- 根级 `development-plan.md`：整树唯一冻结评审入口，描述完整层级计划。各子节点同名文件保留该节点的独立开发内容。
- 根级 `execution-graph.md`：优先嵌入控制器确定性生成的中文 / English SVG 执行图与治理图；Mermaid 源图和节点表折叠保留用于兼容与审计。只读投影，不是机器权威。
- 工作区级 `.layered-delivery/state-transition-graph.md`：当前 schema v3 的 runtime 策略由控制器统一定义，所有需求共享这一份中文 / English 开发执行流程和节点 FSM 投影；完整 Mermaid、迁移表折叠保留，继续展示失败分类、重试预算、暂停恢复与取消。
- 工作区级 `.layered-delivery/assets/*.svg`：共享 `development-flow.svg` 与 `node-state-machine.svg`；需求根 `assets/*.svg` 只保存任务相关的 `execution-graph.svg` 与 `governance-graph.svg`。`refresh-projections` 可统一重建。
- 根级 `frontier.md`：中文 / English 展示当前关键路径、下一个汇聚点、允许动作和阻断原因；只读投影。
- 根级 `run-timeline.md`：展示 graph run、node attempt、状态、owner 和不可变事件序列。
- `development-review.md`：对照冻结计划与实际文件、接口、测试和偏差；只表示等待门禁，不表示 PASS。
- `acceptance-report.md`：门禁证据、验收项、测试结果、范围偏差、P0/P1/P2 和结论；根报告持续更新到最终确认。

## 输入与路径

所有 CLI 调用都必须从当前 Skill 元数据解析 `<skill-root>`。只读查询（包括 `graph-status`、`graph-frontier`、`graph-events`、`graph-replay`、`task-context` 和 `evidence-contract`）的 JSON 直接消费 stdout，不得使用临时 JSON 中转；控制器非零退出时先保留 stderr 并停止解析，不能让下游 JSON 解析器用空 stdout 或错误文本遮蔽真实错误。`evidence-contract` 从 SQLite 动态读取当前合同，只返回指定工作项和 kind 的一个模板；frontier、Task context 和 handoff 只保存紧凑引用，避免整树模板造成上下文膨胀。

`task-result`、`remediate-task`、`gate-item`、`accept-item` 和 `acceptance-item` 的完整证据 artifact 必须使用 `--evidence -` 从 stdin 直接提交；文件路径输入会被拒绝，不生成 `.hdg-tmp`、`%TEMP%` 或其他临时 JSON。控制器在 SQLite 写事务内按当前工作项、operationId、baseline 和动作校验 artifact，计算规范 JSON 的 SHA-256，并把完整 artifact 与摘要一起写入 SQLite；Agent 不直接写数据库，也不自行提交路径或摘要。

`--definition` 和 `--interaction` 的一次性 JSON 也必须使用 `-` 从 stdin 读取；控制器拒绝任何文件路径。宿主使用自身提供的 stdin 直连能力，不嵌套额外 shell；传输失败时修正当前调用，不得降级为仓库文件、系统临时文件或跨运行时路径。完整的宿主无关契约与按 shell 能力区分的适配示例见 [stdin-transport.md](references/stdin-transport.md)。

## SQLite 与交互记录

- 每个项目只有一个 `.layered-delivery/governance.sqlite3`；多个需求根通过 ID 隔离，不为每个 `<root-id>` 建库。
- SQLite 同时保存 graph definition、可重建 graph/node run、带图指纹和前序哈希的 graph event，以及绑定 `runId/nodeId/attempt/graphFingerprint` 的完整 evidence artifact。`graph-status`、`graph-frontier`、`graph-events` 和 `graph-replay` 是只读查询入口。
- 控制器自动创建 evidence binding；Agent 只提交原始 artifact，不得自行填充摘要或图坐标。若 `graph-replay` 证明事件链有效但快照不一致，正常查询保持阻断；只有用户明确确认恢复时才执行 `rebuild-graph-run --item <root-id> --confirmed`，该命令不改写图、事件或 evidence。
- `workspace-overview.md` 会列出只读隔离的历史 evidence 节点。不能直接操作隔离节点；其他有效需求、同树兄弟 Task 和已有 claim 不因它被连带阻断。
- `<root-id>` 目录只有 Markdown 投影。手工删除目录不会删除需求状态，后续刷新还会重建；不得用手删目录代替控制器状态操作。
- 需要保留人机协作事实时，用 `record-interaction` 写入简短的指令、决策或状态摘要；`interaction-log` 查询结构化事件，需求根 `interaction-log.md` 供人工审计。不得保存隐藏思考过程、密钥或不必要的原始对话。
- `remediate-task` 把验证修正 artifact、摘要、原状态快照和补充文件授权追加到 SQLite 交互审计链；原 baseline、层级指纹和 `development-plan.md` 不被改写。
- 旧 JSON 控制目录不迁移、不兼容；发现后明确阻断并要求使用新的治理目录。

## 开发与安全边界

- Task 只写冻结的 `developmentPlan.fileChanges` 与控制器校验通过的验证修正补充文件；scope 不是任意写授权，Agent 不能用自然语言自行扩展文件集合。
- 不继承需求分析或其他 Task 对话；上下文必须由控制器从 SQLite 重建。
- 开发 Agent 不修改 SQLite、baseline、治理投影或 `.git/**`。
- 不自动提交、推送、合并、迁移、发布或执行其他外部动作；需要用户单独明确授权。
- 控制器实现仓库只有在用户明确要求 dogfood 时才能创建运行包，且所有写控制面的命令必须带 `--dogfood`。
- 控制器缺失、版本不符或机械校验失败时保持阻断，不用“等价流程”绕过。

## 按需参考

- 图编译、frontier、attempt 与事件：[graph-engineering.md](references/graph-engineering.md)
- registry、包结构与恢复：[task-registry.md](references/task-registry.md)
- 生命周期与自动重试：[registry-lifecycle.md](references/registry-lifecycle.md)
- baseline、指纹与一次冻结：[baselines.md](references/baselines.md)
- 事务、claim 与并发：[registry-transactions.md](references/registry-transactions.md)
- 独立开发上下文：[development.md](references/development.md)
- 并行调度：[parallel-development.md](references/parallel-development.md)
- 进度与父级聚合：[tracking.md](references/tracking.md)
- 门禁、独立审查与最终确认：[acceptance.md](references/acceptance.md)
- 同一 Task 的验证修正：[validation-remediation.md](references/validation-remediation.md)
- 控制器入口解析、查询输出与结构化 stdin 的宿主无关传输：[stdin-transport.md](references/stdin-transport.md)
- 多工作区：[multi-workspace.md](references/multi-workspace.md)
- 验收后反馈：[post-acceptance-feedback.md](references/post-acceptance-feedback.md)
- Claude active 与 manual 自动交接权限：[claude-automation.md](references/claude-automation.md)
