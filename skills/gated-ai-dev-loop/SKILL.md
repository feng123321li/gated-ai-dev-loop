---
name: gated-ai-dev-loop
description: 将任意形式的软件需求路由为 Full、Light 或 None，通过统一运行目录、冻结开发基线、人可读总览与进度、单 Agent 实现或自动派遣多子 Agent 并行实现、宿主同类主动开发或跨工具手动交接、确定性机械门禁、P0/P1/P2 独立验收报告和用户确认来治理 AI 辅助开发。适用于功能开发、缺陷修复、重构、迁移及其他仓库改动，也适用于需要阻止需求漂移、查看开发进度、安全拆分并行任务、生成分级验收报告、在 Codex 与 Claude 之间交接开发或独立验收的任务。
---

# 门禁式 AI 开发循环

执行一套可见、可复核的开发 SOP，不限定前期需求采集方法。接受对话、PRD、Issue、截图、代码分析、原型或其他输入；只在开始写代码前统一开发授权。

## 分离角色

- 允许当前 Codex 或 Claude 宿主采集、分析、审核和冻结需求。
- 主动模式使用当前宿主的同类全新开发上下文：Codex 宿主启动 Codex 开发，Claude 宿主启动 Claude 开发。
- 手动模式允许用户把同一冻结交接包交给全新的 Codex 或 Claude；Codex 无法调度 Claude 时优先使用该路径。
- 优先使用与开发者分离的全新只读其他 Agent 做语义验收；没有其他 Agent 时才启动宿主的全新验收子 Agent。两者都不得继承需求分析或开发上下文。
- 禁止任何开发上下文验收自己的改动。
- 基线冻结和最终完成都必须由用户明确确认。

## 写入前路由

只选择一种任务模式：

- `None`：只回答问题，不写文件。
- `Full`：命中任一硬条件。
- `Light`：所有硬条件均为假、影响明确、目标与验收具体，并且预计最多修改三个普通文件。

以下任一情况强制使用 `Full`：

- 公共契约或承重契约；
- 破坏性行为；
- 数据、数据库、配置、存储、API 版本或依赖迁移；
- 认证、授权、权限、状态机、事务、并发或幂等；
- 新增外部依赖；
- 尚未解决的设计选项；
- 阈值、超时、重试或容量决策；
- 预计修改超过三个文件；
- 影响范围未知。

存在疑问时选择 `Full`。不得用用户指定的 Light 绕过硬条件。实现后根据真实 diff 重新分类；真实改动越界时把 Light 升级为 Full。

## 统一运行目录

所有持久化流程产物必须位于项目根目录的 `.ai-dev-loop/<task-id>/`。CLI 缺失时也手工建立同一目录和等价文件；禁止改用 `.acceptance/`、临时规范目录或用户主目录。

冻结核心产物、`development-overview.md`、`progress.md` 和最新的 `final-acceptance-report.md` 放在任务目录根部；每次主动调用、手动交接、修复和验收的原始证据放在 `rounds/round-NN/`。开发代理不得修改 `.ai-dev-loop/**`，只有宿主可以写入总览、进度、轮次状态和证据。临时 runner 只能放在系统临时目录，不能进入业务仓库。

## 冻结唯一开发授权

检查仓库并保留用户已有改动。可采用任意合适的分析方法，再把结果归一化：

- Full：生成 [baselines.md](references/baselines.md) 定义的可追踪 `baseline.md`。
- Light：生成 [baselines.md](references/baselines.md) 定义的四段式简报。

让当前宿主审核归一化后的授权。开发前不要求另一个模型复审。解决占位符、缺失决策、模糊验收和不安全测试命令；展示给用户并取得明确确认后再冻结，同时如实记录宿主是 `codex` 还是 `claude`。

安装 `gated-loop` 后优先用它完成确定性路由和冻结；否则手工建立等价文件。没有实际运行命令时不得声称已由 CLI 完成。

## 维护人工可读状态

初始化任务目录后读取 [tracking.md](references/tracking.md)。在请求用户确认需求前生成 `development-overview.md`，并创建 `progress.md`。每次状态转换后以及向用户交还控制权前，由宿主更新进度；开发者和审查者保持只读。

总览和进度只是冻结基线、结构化状态与轮次证据的人可读投影，不得作为开发授权或单独证明任务完成。人工验收时先展示这两个入口以及最新门禁、独立审查证据。

## 选择开发方式并隔离实现

用户确认并冻结开发授权后，进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。开始任何代码写入前读取 [development.md](references/development.md)，向用户展示两个选项并等待明确选择：

- `active`：宿主启动同运行时的全新隔离开发上下文；`developerRuntime` 必须等于 `hostRuntime`。
- `manual`：宿主输出完整交接卡片，由用户在指定的全新 Codex 或 Claude 中执行；允许跨工具交接。

不得设置隐藏默认值，不得把需求确认视为开发方式确认。用户选择 manual 后，再确认 `developerRuntime=codex|claude`。没有明确选择时保持等待，不得开始开发。

两种方式必须使用同一冻结授权、允许范围、任务 ID、验收 ID、测试 argv 和结果格式。要求开发上下文：

- 只实现冻结任务；
- 不重新分析、澄清、设计或修改验收；
- 不修改冻结产物、`.git/**`、秘密信息，也不提交、推送、发布或改变外部状态；
- 冻结授权不完整或冲突时返回 `BLOCKED`；
- 只报告实现事实，不得报告 `PASS`。

## 选择单 Agent 或并行开发

把 `active/manual` 视为开发方式，把 `single/parallel` 视为执行拓扑。确定开发方式和运行时后，读取 [parallel-development.md](references/parallel-development.md)：

- Light 固定使用 `single`；
- Full 只有任务组、验收 ID、写入路径、依赖、隔离和聚合测试都明确时才可提供 `parallel`；
- Full 符合并行资格时，先展示分组计划，再等待用户明确选择 `single` 或 `parallel`。

选择 parallel 后，同一轮所有子 Agent 必须使用相同运行时、全新上下文和互斥写入范围。先执行每个 Agent 的归属门禁，再机械集成无冲突结果，最后对聚合 diff 执行完整机械门禁和一次独立验收。无法证明隔离或归属时退回 single 或返回 `NEED_HUMAN_REVIEW`。

用户确认 `active + parallel` 及完整并行计划后，宿主自动按波次派遣子 Agent，不再逐个请求确认。自动派遣不能代替拓扑选择，也不能擅自改变已确认的任务分组、路径或并发数；计划变化时必须重新展示并确认。宿主不支持创建全新子 Agent 时停止派遣，展示能力限制并让用户改选 single 或 manual。

主动调用遇到 `429/529`、容量不可用或等价外部错误时不得连续隐藏重试。single 确认没有写入后重新进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`，展示失败事实并推荐用户改选 manual；不得自行切换。parallel 按失败 Agent 单独判断：该 Agent 零写入且其他改动归属明确时停止后续波次并让用户选择重新分配、manual 或 single；该 Agent 已写入或归属不明时返回 `NEED_HUMAN_REVIEW`。不得复用需求分析上下文作为开发上下文。

## 执行机械门禁

单个开发上下文结束、所有并行 Agent 返回，或用户从手动开发返回后，依次执行：

1. 验证所有冻结产物及指纹未改变。
2. 对比开发前快照与真实仓库 diff。
3. 并行时先验证每个 Agent 的改动归属，再拒绝重叠、受保护、敏感、无关或越界改动。
4. 根据真实 diff 重新分类；必要时把 Light 升级为 Full。
5. 用 `shell:false` 或宿主等价的直接进程 API 执行冻结测试 argv。
6. 记录命令、退出码以及通过、失败、错误、跳过数量；测试未运行即门禁失败。
7. 根据机械证据生成当前轮次 `self-check-report.md`；只有结论 PASS 才能进入独立验收。
8. 禁止自动提交、推送、合并、迁移、发布或公开。

严格区分开发前已存在的脏改动。无法确定文件或代码行归属时停止，并返回 `NEED_HUMAN_REVIEW`。

CLI 可用时执行 `gated-loop self-check --task <id> --round <NN>`；它要求当前轮次已有 `development-snapshot.json`，并写入 `gate-evidence.json` 和 `self-check-report.md`。命令返回非 PASS 时不得继续验收。

## 独立验收

语义验收前读取 [acceptance.md](references/acceptance.md)。优先启动与开发者分离的全新只读其他 Agent；没有其他 Agent 时才启动宿主的全新验收子 Agent。两者都不能继承需求分析或开发上下文，只提供冻结授权、验收项、任务、真实 diff、开发事实和机械证据。宿主校验结构化结论后写入 `review.json`，渲染轮次级 `acceptance-report.md`，并刷新任务根目录的 `final-acceptance-report.md`。CLI 可用时运行 `gated-loop accept --task <id> --round <NN>`；宿主 Agent 的结果通过 `--review-result` 传入。只接受：

- `PASS`：所有验收项满足、证据完整且没有 P0/P1；允许存在必须展示的 P2；
- `FAIL`：存在至少一个关联需求、验收、任务或安全边界的 P0/P1；
- `NEED_HUMAN_REVIEW`：无法证明隔离、证据、改动归属或只读保证。

收到 `FAIL` 后只基于 P0/P1 建立最小修复交接，返回同类隔离开发上下文或明确的手动交接；P2 不自动修复，除非用户授权。重新运行全部机械门禁和独立验收，最多三轮。审查者 `PASS` 后，先向用户展示根级 `final-acceptance-report.md`，再按需展开轮次报告和 JSON 证据，并取得明确验收。

## 保持安全与可见

- 展示任务模式、开发方式、宿主、开发运行时、冻结文件、交接命令、证据和审查者身份。
- 保持 `development-overview.md` 和 `progress.md` 与权威状态一致，让用户可随时查看当前阶段、完成任务、阻断项和下一步。
- 只读取用户授权的仓库来源；不得为获取上下文扫描凭据存储或用户主目录。
- 保留无关改动和开发前已有改动。
- 未获明确授权时不得创建外部状态。
- 无法证明安全时关闭流程，不使用隐藏降级。

## 按阶段读取参考资料

- 需要理解、展示或解释完整流程时读取 [workflow.md](references/workflow.md)。
- 路由、起草或冻结时读取 [baselines.md](references/baselines.md)。
- 创建开发总览、更新进度或进入人工验收时读取 [tracking.md](references/tracking.md)。
- 选择开发方式、生成交接或执行机械门禁时读取 [development.md](references/development.md)。
- 评估并行资格、拆分任务、启动多子 Agent 或集成结果时读取 [parallel-development.md](references/parallel-development.md)。
- 独立验收或准备修复轮次时读取 [acceptance.md](references/acceptance.md)。
