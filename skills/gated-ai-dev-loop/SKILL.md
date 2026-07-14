---
name: gated-ai-dev-loop
description: 将任意形式的软件需求路由为 Full、Light 或 None，通过统一协调目录、冻结开发基线、跨目录与多微服务工作区覆盖门禁、人可读总览与进度、宿主自动派遣隔离开发 Agent 或输出任意 Agent 可接收的通用手动提示词、可由任意新宿主恢复的机械门禁、能力驱动的独立或人工语义验收、P0/P1/P2 报告和用户确认来治理 AI 辅助开发。适用于任意单 Agent、多 Agent 或支持子 Agent 的宿主发起、接收或接管单仓库及跨仓库的软件功能开发、缺陷修复、重构、迁移。
---

# 门禁式 AI 开发循环

执行一套可见、可复核的开发 SOP，不限定前期需求采集方法。接受对话、PRD、Issue、截图、代码分析、原型或其他输入；只在开始写代码前统一开发授权。

## 分离角色

- 允许任意能够读取本 Skill 的宿主 Agent 采集、分析、审核和冻结需求。
- 直接运行模式由宿主自动启动其可调度的全新隔离开发 Agent，不要求开发 Agent 与宿主同类。
- 手动运行模式只输出通用后续提示词，允许用户交给任意全新开发 Agent，不预选工具或返回工具专属 CLI 命令。
- 开发 Agent 不继承前期对话，只读取冻结交接和当前轮次文件；开发完成后任意新宿主 Agent 都可从磁盘接管门禁，不绑定原宿主对话。
- 语义验收按能力选择：优先使用与开发者分离的全新只读其他 Agent；没有其他产品时允许使用宿主创建的全新只读验收子 Agent；两者都不得继承需求分析或开发上下文。
- 没有任何可证明隔离的新 Agent 或子 Agent 时继续完成开发和机械门禁，生成可见的人工验收包并返回 `NEED_HUMAN_REVIEW`；不得声称已完成独立语义验收。
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
- 需要写入多个独立工作区、仓库或微服务；
- 尚未解决的设计选项；
- 阈值、超时、重试或容量决策；
- 预计修改超过三个文件；
- 影响范围未知。

存在疑问时选择 `Full`。不得用用户指定的 Light 绕过硬条件。实现后根据真实 diff 重新分类；真实改动越界时把 Light 升级为 Full。

## 统一运行目录

所有持久化流程产物必须位于协调工作区根目录的 `.ai-dev-loop/<task-id>/`。CLI 缺失时也手工建立同一目录和等价文件；禁止改用 `.acceptance/`、临时规范目录或用户主目录。AI 分析后发现任务跨目录、跨仓库或跨微服务时，只选择一个协调工作区保存任务包，其他工作区只保存业务改动，不复制任务包。

冻结核心产物、`development-overview.md`、`progress.md` 和最新的 `final-acceptance-report.md` 放在任务目录根部；每次主动调用、手动交接、修复和验收的原始证据放在 `rounds/round-NN/`。开发代理不得修改 `.ai-dev-loop/**`，只有宿主可以写入总览、进度、轮次状态和证据。临时 runner 只能放在系统临时目录，不能进入业务仓库。

## 冻结唯一开发授权

检查仓库并保留用户已有改动。可采用任意合适的分析方法，再把结果归一化：

- Full：生成 [baselines.md](references/baselines.md) 定义的可追踪 `baseline.md`。
- Light：生成 [baselines.md](references/baselines.md) 定义的四段式简报。

让当前宿主审核归一化后的授权。开发前不要求另一个模型复审。解决占位符、缺失决策、模糊验收和不安全测试命令；展示给用户并取得明确确认后再冻结，同时如实记录宿主 Agent 标识。

安装 `gated-loop` 后优先用它完成确定性路由和冻结；否则手工建立等价文件。没有实际运行命令时不得声称已由 CLI 完成。

## 验证跨目录工作区覆盖

冻结后先判断每个会产生写入的 `T-NNN` 是否只落在一个工作区。只要需求分析发现任务跨目录、跨仓库、跨微服务，或需要同时修改提供方与消费方，就必须读取 [multi-workspace.md](references/multi-workspace.md)，并在开发方式选择前完成工作区覆盖门禁：

- 为每个写入任务列出稳定的工作区 ID、规范化绝对根路径、仓库相对允许路径、测试工作目录和依赖顺序；
- 取得用户对精确工作区和写入范围的确认，生成当前轮次 `workspace-authorization.json`；
- 验证所有任务、路径、测试和依赖均被覆盖，生成 `workspace-coverage.json`；
- 只有覆盖结论为 `PASS` 才能创建 `prompt.md`、展示 active/manual 选择或派遣开发 Agent；
- 覆盖不完整时进入 `WAITING_FOR_WORKSPACE_AUTHORIZATION`，在总览和进度中列出缺口，不得生成一个已知会 `BLOCKED` 的交接。

单工作区继续使用原有契约。多工作区使用 schema v2 开发快照，并逐工作区执行机械门禁后再聚合。缺少的只是已冻结服务的物理路径或权限时可在新轮次补充授权；如果新增目录会扩大需求、任务或验收范围，则回到需求确认并重新冻结。

## 维护人工可读状态

初始化任务目录后读取 [tracking.md](references/tracking.md)。在请求用户确认需求前生成 `development-overview.md`，并创建 `progress.md`。每次状态转换后以及向用户交还控制权前，由宿主更新进度；开发者和审查者保持只读。

总览和进度只是冻结基线、结构化状态与轮次证据的人可读投影，不得作为开发授权或单独证明任务完成。人工验收时先展示这两个入口以及最新门禁、独立审查证据。

## 选择开发方式并隔离实现

用户确认并冻结开发授权、且所需工作区覆盖门禁通过后，进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。开始任何代码写入前读取 [development.md](references/development.md)，向用户展示两个选项并等待明确选择：

- `active`（直接运行）：宿主自动启动其可调度的全新隔离开发 Agent。
- `manual`（手动运行）：宿主输出完整交接卡片和一份任意开发 Agent 可接收的通用后续提示词。

不得设置隐藏默认值，不得把需求确认视为开发方式确认。用户可在当前宿主对话输入“直接运行”选择 active，或输入“手动运行”选择 manual。manual 后不再询问接收 Agent；通用提示词必须包含 `development-handoff.md`、当前轮次 `prompt.md`、禁止二次分析、越界限制和 `BLOCKED` 条件。同时生成 `gate-continuation.md`，使任意新宿主 Agent 能结合开发结果继续机械门禁，不要求返回原对话。详细模板见 [development.md](references/development.md#手动模式)。

两种方式必须使用同一冻结授权、允许范围、任务 ID、验收 ID、测试 argv 和结果格式。要求开发上下文：

- 只实现冻结任务；
- 不重新分析、澄清、设计或修改验收；
- 不修改冻结产物、`.git/**`、秘密信息，也不提交、推送、发布或改变外部状态；
- 冻结授权不完整或冲突时返回 `BLOCKED`；
- 只报告实现事实，不得报告 `PASS`。

## 选择单 Agent 或并行开发

把 `active/manual` 视为开发方式，把 `single/parallel` 视为执行拓扑。确定开发方式后，读取 [parallel-development.md](references/parallel-development.md)：

- Light 固定使用 `single`；
- Full 只有任务组、验收 ID、写入路径、依赖、隔离和聚合测试都明确时才可提供 `parallel`；
- Full 符合并行资格时，先展示分组计划，再等待用户明确选择 `single` 或 `parallel`。

选择 parallel 后，同一轮所有子 Agent 必须使用相同冻结契约、全新上下文和互斥写入范围；可以由同一或不同 Agent 产品执行。先执行每个 Agent 的归属门禁，再机械集成无冲突结果，最后对聚合 diff 执行完整机械门禁和一次能力驱动的语义验收。无法证明开发隔离或归属时退回 single 或返回 `NEED_HUMAN_REVIEW`。

用户确认 `active + parallel` 及完整并行计划后，宿主自动按波次派遣子 Agent，不再逐个请求确认。自动派遣不能代替拓扑选择，也不能擅自改变已确认的任务分组、路径或并发数；计划变化时必须重新展示并确认。宿主不支持创建全新子 Agent 时停止派遣，展示能力限制并让用户改选 single 或 manual。

主动调用遇到 `429/529`、容量不可用或等价外部错误时不得连续隐藏重试。single 确认没有写入后重新进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`，展示失败事实并推荐用户改选 manual；不得自行切换。parallel 按失败 Agent 单独判断：该 Agent 零写入且其他改动归属明确时停止后续波次并让用户选择重新分配、manual 或 single；该 Agent 已写入或归属不明时返回 `NEED_HUMAN_REVIEW`。不得复用需求分析上下文作为开发上下文。

## 执行机械门禁

单个开发上下文结束、所有并行 Agent 返回，或任意宿主 Agent 根据 `gate-continuation.md` 接管后，依次执行：

1. 验证所有冻结产物及指纹未改变。
2. 对比开发前快照与真实仓库 diff；多工作区时逐个验证根路径、HEAD、已有改动与真实 diff 归属。
3. 并行时先验证每个 Agent 的改动归属，再拒绝重叠、受保护、敏感、无关或越界改动。
4. 根据真实 diff 重新分类；必要时把 Light 升级为 Full。
5. 用 `shell:false` 或宿主等价的直接进程 API，在每条命令已授权的工作目录中执行冻结测试 argv；多工作区完成逐仓库测试后再执行跨服务集成检查。
6. 记录命令、退出码以及通过、失败、错误、跳过数量；测试未运行即门禁失败。
7. 根据机械证据生成当前轮次 `self-check-report.md`；只有结论 PASS 才能进入语义验收路由。
8. 禁止自动提交、推送、合并、迁移、发布或公开。

严格区分开发前已存在的脏改动。无法确定文件或代码行归属时停止，并返回 `NEED_HUMAN_REVIEW`。

CLI 可用时执行 `gated-loop self-check --task <id> --round <NN>`；它要求当前轮次已有 `development-snapshot.json`，并写入 `gate-evidence.json` 和 `self-check-report.md`。命令返回非 PASS 时不得继续验收。

CLI `self-check` 原生支持单工作区 schema v1 和多工作区 schema v2。schema v2 会校验用户确认的 `workspace-authorization.json`、`workspace-coverage.json`、冻结测试命令分配和无环任务依赖图，再按依赖波次逐工作区验证分支、HEAD、已有改动、范围与测试并聚合证据；前后端或上下游的构建、契约和消费依赖必须以冻结命令与 `dependsOn` 明确表达，前置工作区失败时必须阻断后置工作区测试。`accept` 必须重新读取全部工作区并验证聚合 diff 未变化后才允许语义验收。

## 能力驱动的语义验收

语义验收前读取 [acceptance.md](references/acceptance.md)。先生成 `review-plan.json`，按能力而不是产品名称选择且公开记录原因：

1. 使用与开发者分离的全新只读其他 Agent，记录 `independent-agent`；
2. 没有其他产品但宿主能创建全新子 Agent 时，使用无开发上下文的只读验收子 Agent，记录 `fresh-subagent`；
3. 两者都不可用时转入人工语义验收，记录 `human-review`，继续保留机械门禁结果但不得输出独立验收 `PASS`。

不同 Agent 产品不是前提，新的同产品子 Agent 可以完成独立验收；同一个开发 Agent、开发会话或继承开发上下文的 fork 不可以。Agent 验收只接收冻结授权、验收项、任务、真实 diff、开发事实和机械证据。宿主校验结构化结论后写入 `review.json`，渲染轮次级 `acceptance-report.md`，并刷新任务根目录的 `final-acceptance-report.md`。CLI 可用时优先通过 `--review-result` 传入宿主取得的 Agent 结果；未提供隔离能力时 `gated-loop accept` 默认生成清晰的人工验收待办，不扫描或启动外部 Agent。只接受：

- `PASS`：所有验收项满足、证据完整且没有 P0/P1；允许存在必须展示的 P2；
- `FAIL`：存在至少一个关联需求、验收、任务或安全边界的 P0/P1；
- `NEED_HUMAN_REVIEW`：无法证明隔离、证据、改动归属或只读保证，或当前只有人工语义验收能力。

收到 `FAIL` 后只基于 P0/P1 建立最小修复交接，交给任意新的隔离开发 Agent，或输出通用手动提示词；P2 不自动修复，除非用户授权。重新运行全部机械门禁和语义验收，最多三轮。独立审查者 `PASS` 后，先向用户展示根级 `final-acceptance-report.md`，再按需展开轮次报告和 JSON 证据，并取得明确验收。人工路径必须明确展示“尚未完成独立语义验收”，由用户查看验收包后决定后续动作。

## 保持安全与可见

- 展示任务模式、开发方式、宿主 Agent、实际开发 Agent、冻结文件、通用交接提示词、证据和审查者身份。
- 保持 `development-overview.md` 和 `progress.md` 与权威状态一致，让用户可随时查看当前阶段、完成任务、阻断项和下一步。
- 只读取用户授权的仓库来源；不得为获取上下文扫描凭据存储或用户主目录。
- 保留无关改动和开发前已有改动。
- 未获明确授权时不得创建外部状态。
- 无法证明安全时关闭流程，不使用隐藏降级。

## 按阶段读取参考资料

- 需要理解、展示或解释完整流程时读取 [workflow.md](references/workflow.md)。
- 路由、起草或冻结时读取 [baselines.md](references/baselines.md)。
- AI 分析后发现跨目录、跨仓库、跨微服务或提供方/消费方联动时读取 [multi-workspace.md](references/multi-workspace.md)。
- 创建开发总览、更新进度或进入人工验收时读取 [tracking.md](references/tracking.md)。
- 选择开发方式、生成交接或执行机械门禁时读取 [development.md](references/development.md)。
- 评估并行资格、拆分任务、启动多子 Agent 或集成结果时读取 [parallel-development.md](references/parallel-development.md)。
- 选择验收能力、执行独立或人工语义验收、准备修复轮次时读取 [acceptance.md](references/acceptance.md)。
