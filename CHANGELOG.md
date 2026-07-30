# 版本更新记录

本文档汇总 `layered-delivery` 各正式版本的主要变化。版本边界以项目清单中的版本号和对应 Git 提交为准；同一版本发布前的连续改动合并记录在该版本下。

后续发布新版本时，应在版本提交中同步更新本文档，按“最新版本在前”的顺序记录发布日期、发布提交、核心能力、兼容性或迁移影响以及主要验证结果。

## 0.21.0 — 2026-07-30

发布提交：`9c1e371`

- 将入口正式收口为共享 Python Controller、Host Policy、MCP Adapter 与 stdio Transport：Graph、schema v3、SQLite 和事件链不依赖 MCP/Codex/Claude，双宿主继续复用同一 Controller 与权威状态。
- MCP 优先支持稳定版 `2026-07-28`：新增 `server/discover`，按请求校验协议版本与客户端能力，所有现代成功结果携带 `resultType` 和 server info，`tools/list`/discovery 携带缓存提示；不支持的版本返回标准 `-32022`。
- 保留 `2025-11-25` 初始化式兼容，正式收口为 `2026-07-28` 与 `2025-11-25` 双栈双版本；legacy `initialize` 不会协商出无会话的 `2026-07-28` 语义。Codex 现代项目根按请求解析，Claude/Codex 的审批与兼容策略位于 Adapter 边界。
- Tasks 保持为未声明的可选扩展；现有 Graph/Loop 长任务继续通过显式 `root_id`、`node_id`、`operation_id`、lease 和 SQLite 状态管理，不引入第二套异步状态。

## 0.20.1 — 2026-07-29

发布提交：`a644bf1`

- 修复用户已经明确接受完整验收报告后，`record_user_confirmation` 仍被 Codex manifest、Claude `PreToolUse` Hook 与 MCP `requiresUserInteraction` 重复触发权限弹窗的问题；用户的明确接受现在直接授权控制器写入最终确认事件。
- 保留最终用户确认边界、`confirmed: true` 严格布尔校验和 Review 成功前置条件；本次调整不会自动接受交付，也不授权提交、推送、合并、迁移或发布。
- 收口终态行为：Graph `COMPLETED` / `CANCELLED` 后只返回简短摘要，不擅自更新宿主记忆、触发持续学习或保留 schema v1/v2 旧操作笔记；可移植 ASCII 调度身份作为正式 Skill 契约说明。

## 0.20.0 — 2026-07-29

发布提交：`1393a14`

- 新增 `.layered-delivery/overview.md` 工作区总览，由控制器从 SQLite 汇总全部 Delivery 的中文状态、TASK 完成数量、GROUP 数量、更新时间和详情链接；任一 Delivery 状态变化时同步刷新。
- TASK baseline、GROUP Review 和 Delivery Review 的不透明 payload 改为固定模板的递归 Markdown：常用字段映射为中文标题，对象和数组展开为层级列表，未知字段保留原名，不再向人类投影输出 JSON 代码块。
- Delivery 总览中的状态、节点类型、依赖、资源、运行结果和审查信息统一使用中文标签，不再附带 `PREPARED`、`ACTIVE` 等机器枚举；SQLite、事件链及三类 JSON 文件继续保留完整机器字段。
- 人类投影中的领域文本统一进行 Markdown 转义，MCP 输入只能提供领域数据，不能改变模板结构；根总览、Delivery 总览和全部 TASK baseline 均可从 SQLite 确定性重建。
- 收敛 `loop_context.executionPolicy`：未 claim 且无 Agent 容量时人工交接，已 claim 且租约有效的上下文/Hook 压力才使用 pause/handoff，租约过期固定交给 `advance_graph`；删除 `rules` 中重复的 Capacity 布尔字段，避免宿主将 Capacity 与 lease 错误合并为同一运行提示。

## 0.19.0 — 2026-07-29

发布提交：`df2955c`

- 每个 TASK、GROUP Review 和 Delivery Review Loop 默认路由到独立接收上下文；宿主支持原生 Agent 时优先自动派遣，没有可用容量时才人工交接，总调度上下文不再内联执行 Loop。
- 上下文容量压力或高轮次 Hook 摩擦统一走 `pause_loop → 新接收上下文 resume_loop → 重新 dispatch`；frontier 新增暂停 Loop 与恢复 action，这类执行容量问题不再误报为 `BLOCKED`、`WORKER_LOST` 或 `REPLAN_REQUIRED`。
- 将 TASK 详细调度基线从 `overview.md` 拆分为 `.layered-delivery/<delivery-id>/task-baselines/<task-id>.md`；每份 baseline 通过固定模板展示双指纹、summary、dependsOn、Loop、资源锁、原始 payload 和共享 Skill Hint。
- `task-baselines/` 由控制器从 SQLite 权威状态整体原子替换；重新 prepare 删除或改名 TASK 时自动移除旧文件。overview 只保留 Delivery 状态、GROUP/TASK 清单、TASK 运行快照及 Review/最终进度。

## 0.18.1 — 2026-07-29

发布提交：`f9ef70b`

- 修复 `freeze_hierarchy.confirmed` 在 MCP 宿主间被序列化成 `"true"` 或 `1` 后触发严格身份校验失败的问题：冻结工具不再暴露内部确认布尔值，由适配器在已验证的用户方式选择后注入 Python `True`。
- 恢复单次冻结交互：自动执行与手动交接都是完整授权并确认开发，选择本身即为冻结确认；调整需求及其他反馈不确认、不冻结，继续交互并重新 prepare。冻结工具在宿主权限层统一自动批准，不再追加通用 Yes/No 或任何冻结弹窗。
- `record_user_confirmation.confirmed` 的 JSON Schema 显式声明为 boolean，控制器验证器拒绝字符串和整数伪布尔值。
- 新增 MCP 实际 prepare→freeze 回归测试与双宿主权限配置测试；本次修复不改变 schema v3、SQLite 数据格式或既有 PREPARED hierarchy。

## 0.18.0 — 2026-07-29

发布提交：`ea1f109`

- 用递归 `GROUP` / `TASK` 模型替换固定 Delivery / Capability / Task 三层：TASK 是唯一执行叶子，GROUP 可混合包含直接子 GROUP/TASK；Delivery 保留为顶层 Graph/run 与最终验收边界，不属于 work item kind。
- hierarchy 最外层收敛为 `delivery` 与 `root`；schema 版本和共享 `skillHints` 归入根包装节点，嵌套节点只保存自己的 definition、Review 与 children。
- 每个 GROUP 编译为 `GROUP_JOIN → GROUP_REVIEW_LOOP`；子 GROUP 只有在自己的 Review 成功后才向父层贡献终态，根工作项最终进入 `DELIVERY_REVIEW_LOOP → USER_CONFIRMATION`。
- `dependsOn` 改为直接兄弟 GROUP/TASK 的启动屏障，支持 TASK→TASK、TASK→GROUP、GROUP→TASK 与 GROUP→GROUP；GROUP 依赖会阻止目标子树入口，直到来源 GROUP Review 成功。
- 保留外层调度边界：TASK、GROUP Review 与 Delivery Review Loop 各自负责实现方法、测试、Gate、修正和实际 Skill 选择；共享 Skill Hint 只作为晚绑定优先提示。
- 递归 GROUP/TASK 结构仅存在于 hierarchy 与编译 Graph；工作区共享 `.layered-delivery/scheduler.db`，并按稳定 `delivery.id` 写入 `.layered-delivery/<delivery-id>/{hierarchy.json,graph.json,state.json,overview.md}`，允许多份需求交付目录并存且不继续展开 GROUP/TASK 目录。
- 恢复可核对的人类投影：`overview.md` 绑定 hierarchy/graph 指纹和冻结/运行状态，包含完整 GROUP/TASK 清单及每个节点的摘要、依赖、Loop、资源锁、原始 payload 和进度；文案使用中文，时间统一显示为 UTC+8。
- 投影收敛为控制器从 SQLite 权威状态通过固定版本模板原子生成的四类文件；Agent 只能通过 MCP 读取调度数据，不能直连 SQLite、选择模板或直接创建、修补投影。
- 投影刷新、事件快照重放和物化状态重建纳入统一 scheduler lock；所有运行变更在锁内取得单调提交时间，避免并发请求让 SQLite、`state.json` 或 `overview.md` 回退到旧状态。
- 写操作在事务内重新校验 Delivery namespace、hierarchy 指纹及 hierarchy→graph 精确编译绑定，避免成对篡改图和指纹后产生部分提交；COMPLETED/CANCELLED Graph 的 frontier 与审计时间保持稳定终态。
- 此版本是 schema v3 的破坏性语义替换，不提供 0.17.x hierarchy 或更早运行包的迁移/兼容入口；升级前需要归档旧 `.layered-delivery` 运行包。

## 0.17.0 — 2026-07-29

发布提交：`55b13ad`

- 将 `layered-delivery` 收敛为外层 Graph Scheduler：Task 和最终审查统一为可插拔 Loop，Capability/Delivery 只保留 Join，最终仍由用户确认。
- 删除外层 `scope`、`developmentPlan`、test command、Gate level、required Skill stage、文件授权、evidence hydration、remediation 和 Gate→development 路由；实现、测试、Gate、修正及 Skill 调用由各 Task Loop 内部协议负责。
- schema v3 新增不透明 `loop.ref/payload/resourceClaims` 与标准 `SUCCEEDED/BLOCKED/REPLAN_REQUIRED/CANCELLED` outcome；资源声明改为多项目、多模块可用的精确排他锁键。
- schema v3 在 hierarchy 顶层新增共享 `skillHints`：用户给出的 Skill 只作为建议性的运行时优先提示，需求阶段不分配到 Task/阶段、不编译进 Graph 节点；每个 Task/Review Loop 根据真实上下文与宿主可用 Skill 独立选择，调度器不校验激活或生命周期。
- MCP 面收敛为 17 个调度工具，覆盖层级准备/冻结、frontier、Loop claim/heartbeat/pause/resume/result、租约推进、事件、重建、取消与最终确认。
- SQLite 权威收敛为 scheduler hierarchy/run/node attempt/event；基础设施故障预算内自动重试，业务阻断不自动重跑；可从哈希事件链重建物化状态。
- 强化 Loop 边界：过期 lease 的旧 operation 不得 pause 或提交结果；`loop_context` 额外返回传递上游 Task Loop 结果，使根 Join 后的 Review Loop 能审查实际 Loop evidence，而不要求 Join 解释业务内容。
- 重写 Skill、references、Codex/Claude Plugin 描述和敏感工具策略；删除旧控制器模块与旧协议测试，不提供兼容入口。
- 此版本是 schema v3 的破坏性语义替换：检测到旧 `governance.sqlite3` 时明确阻断，不迁移、不并存；创建新 Graph 前需要先归档旧运行包。

## 0.16.6 — 2026-07-29

发布提交：`207046e`

- 修复 Codex MCP 初始化后立即断开：兼容 `notifications/initialized` 的 `params=null`，并允许 `tools/list` 携带标准对象型 `_meta`，避免工具目录请求被误判为 `Invalid params` 后出现 `has_cached_tools=false`。
- 新增 Codex 真实 stdio 握手回归，覆盖空参数初始化通知、带请求元数据的工具列表以及非法 `_meta` 类型；schema v3、38 个 MCP 工具和权限边界保持不变，全量 249 项测试通过。

## 0.16.5 — 2026-07-29

发布提交：`6e1d16e`

- 新增只读 `hierarchy_contract` MCP 工具，按根类型与输入模式返回完整 schema v3 JSON Schema、可直接提交的有效示例和核心不变量；规划 Skill 在 `prepare_hierarchy` 前按需读取契约，不再从失败响应或控制器源码试探内部类型。
- 新增根 `compactTask` 输入，以显式 `gateLevel` 同时覆盖 `LIGHT` 与 `FULL` 单 Task；控制器仍只持久化完整 schema v3，原 `compactLightTask` 行为保持不变。
- hierarchy、node、definition、execution、development plan 及其嵌套记录的结构错误统一返回字段路径、必需/可选/实际/缺失/未知键和允许枚举；MCP 工具数由 37 增至 38，全量 248 项测试通过。

## 0.16.4 — 2026-07-29

发布提交：`2f1d7ef`

- 将内部依赖从聚合 façade 全部改为直接指向职责模块，44 处间接实现导入降为 0；`evidence`、`model`、`graph_runtime`、`repository` 与 `operations` 仅以显式 `__all__` 保留合计 29 个稳定公共入口。
- 将 `repository.py` 从 46,271 字符压缩到 3,766 字符，并继续分离工作区、层级查询、registry 契约校验、SQLite 事务/投影调度和 package 物化职责；各职责模块都有独立源码上下文预算，避免把大型聚合文件简单搬家。
- 新增公共 API、内部导入方向与职责模块体积回归；源码、canonical Skill 和双宿主 Plugin 运行包保持逐文件一致。schema v3、37 个 MCP 工具及权限边界保持不变，Python 全量 239 项测试及 83% 分支覆盖率通过。

## 0.16.3 — 2026-07-29

发布提交：`051564c`

- 将 repository、Graph runtime、evidence、model 和 MCP operations 的聚合实现拆分为职责单一的内部模块，同时保留原公开导入面，降低 Agent 按文件检索和 MCP 精确处理时需要加载的无关上下文。
- 精简 37 个 MCP 工具的注册 schema，移除重复顶层展示元数据并压缩 `payloadRef` 声明；工具目录紧凑 JSON 从 40,928 字节降至 31,078 字节，约减少 24.1% 的注册上下文。
- 新增模块体积、公开兼容面、MCP schema 大小和源/Skill/Plugin 镜像一致性预算回归；schema v3、工具数量、运行时严格校验、MCP-only 与权限边界保持不变。Python 全量 236 项测试及 83% 分支覆盖率通过。

## 0.16.2 — 2026-07-28

发布提交：`ab183cc`

- 面向用户生成的 Markdown、SVG、进度、警告和验收报告默认统一使用简体中文；双语标题、表头和内部英文状态码不再直接进入普通展示，技术标识仅保留在必要的审计位置。
- 新增集中式展示时间渲染，用户文档默认使用东八区，并在字段名或文档说明中统一标注，时间值不再重复附加 `UTC+08:00`；MCP、SQLite、事件链和 JSON 的机器字段继续使用英文，机器时间继续使用 UTC。
- Skill 增加中文展示与结构化 `userPrompt` 转述规则；schema v3、MCP-only、固定项目根、图调度和权限边界保持不变。Python 全量 229 项测试通过。

## 0.16.1 — 2026-07-28

发布提交：`f2aaf07`

- 从运行时 `layered-delivery` Skill 移除维护专用 `dogfood` 说明；该授权边界只保留在仓库级 `AGENTS.md`、控制器自托管保护与相关回归测试中。
- Plugin MCP-only、固定项目根和通用确认参数限制保持不变；canonical Skill 与双宿主 Plugin 载荷继续一致。

## 0.16.0 — 2026-07-28

发布提交：`65ac6bd`

- 新增根 Task `compactLightTask` 快速输入，由控制器扩展并只保存完整 schema v3；模块级 Scope 与 ADD-only `generatedFileRoots` 在降低规划成本的同时继续保持精确修改/删除授权。
- Graph frontier、Task context、handoff 与 MCP 响应默认使用紧凑模式；迁移结果携带 `nextFrontier`，等待轮询支持 revision 去重，详细 blocked 状态只在诊断时按需读取。
- result、gate、review 与 confirmation 支持 `evidenceDelta`，由控制器从冻结契约补齐测试 argv、需求追踪和授权信息，再保存完整 canonical evidence。
- SQLite 投影改为按实际变化节点及受影响需求树增量刷新；最终用户验收阶段的同契约修正继续回到原 Task，不再全量重建无关需求投影。
- 压缩 MCP output schema 和调度上下文，减少工具注册、长任务恢复、Agent handoff 与多轮门禁的上下文占用，并新增上下文预算和增量投影性能回归。
- Skill 入口与 references 从 23 个相关文件、1,636 行精简为 6 个文件、226 行；删除由工具 schema、Graph、SQLite 和 Plugin 权限机械保证的重复说明，只保留规划、执行、验收与异常传输核心边界。
- schema v3 是唯一标准，不增加旧 schema 兼容入口；Plugin 继续保持双宿主、MCP-only、一次冻结确认和最终用户确认边界。Python 全量 227 项测试通过。

## 0.15.5 — 2026-07-28

发布提交：`78d18f7`

- 收敛用户显式开发 Skill 的规划语义：不在需求分析阶段预读或递归展开 Skill，不从 Skill 内容派生业务需求、Task 或门禁；直接按用户给出的 catalog 名登记为仅含 `DEVELOPMENT` 的执行约束，并在实际 worker 开发时原生调用。只有用户另行明确指定其他阶段时才进入 GATE/FINAL_REVIEW。
- `prepare_hierarchy` 新增宿主级 root 与项目级 project 双来源 `available_skills` 预检；自定义 required Skill 不存在或疑似拼错时在写入治理状态前阻断，同时返回机器可处理的 `skillOptions` 和可直接展示的中文 `userPrompt`，其中包含带来源的近似候选与修正、安装兜底指引。
- 调整 Scope 规划粒度：按最小可用模块边界使用 `module/**`，为同模块必要文件生成保留空间，同时继续由 `developmentPlan.fileChanges` 冻结精确写授权；禁止全仓库 `**`，并明确重叠 Scope 会限制 Graph 并行。
- 瘦身 Task 开发交接：`development-handoff.md` 不再复制完整 `dispatch_task` 上下文、父级开发计划、完整 Skill policy、lease policy 和后续 evidence 模板，只保留开发方案链接与 worker 开工所需字段；完整机器上下文继续由 SQLite/MCP 权威保存。
- 明确速度优先的 LIGHT 策略：低风险单目标需求默认使用根 Task，允许简洁说明、定向测试和按需读取，同时保留独立验收、精确文件授权、真实测试、P0/P1 与最终用户确认。
- 将方案确认、`active|manual` 方式选择与 `freeze_hierarchy` 合并为一次用户授权：用户选择方式后 Agent 必须紧邻调用冻结工具，不再出现第二个工具批准弹窗，也不得从旧对话推断或重放选择。
- Claude `PreToolUse` Hook、Codex manifest prompt 和 `anthropic/requiresUserInteraction` 仅保留 Graph 重建、Graph 取消、人工审查接受和最终用户确认这 4 个独立敏感动作；旧版 Claude Code 的服务端拒绝范围同步收敛到这 4 个工具。
- 冻结仍由专用 MCP 操作注入领域确认并以层级指纹 compare-and-swap，工具参数继续不暴露通用 `confirmed` 布尔值；单次确认减少重复交互，不放宽方案指纹、最终验收或外部权限边界。

## 0.15.4 — 2026-07-28

发布提交：`d611faf`

- Claude Plugin 新增 `hooks/hooks.json` 与失败关闭的 `PreToolUse` Hook；Skill 可用一个 MCP Server 通配符预批准常规调用，同时对方案冻结、Graph 重建、Graph 取消、人工审查接受和最终用户确认继续逐次强制 `ask`。Codex 仍由自身 Plugin manifest 对同一组工具保持 `prompt`。
- Claude Hook 对非对象事件、非字符串工具名、JSON 解码错误和内部输出异常统一以退出码 2 失败关闭，不会因异常退出码 1 被宿主当作非阻断故障继续执行。
- 修复 Claude Code 已连接 MCP 后工具获取失败：所有工具的 `outputSchema` 根节点显式声明 `type: object`，兼容当前 MCP schema 和 Claude 工具注册校验；诊断文档区分“进程未启动”与 `Connected · tools fetch failed`。
- 将交付形态收敛为 Plugin-only：单个 `layered-delivery` Plugin 同时携带一个 Skill 和一个 MCP Server；移除全部 Python console scripts、`bin/hdg.py`、Skill `scripts/hdg.py` 与 `python -m hdg` 入口，Plugin 运行包不再包含 `cli.py` 或 `__main__.py`。宿主直接运行 Plugin 内的 `hdg_mcp.py`，用户不需安装 Python package。
- MCP 未安装、未注册、未连接或工具注册失败时立即返回 `PLUGIN_MCP_UNAVAILABLE` 并停止，不开始或恢复治理写入，不允许 Shell、直接 Python API 或 SQLite 降级绕过。
- 开发中 stdio 连接意外终止时明确报告 `PLUGIN_MCP_DISCONNECTED`；响应未送达的写操作标记为提交状态未知，重连后从 `workspace_status`、`graph_frontier` 核对 SQLite 权威状态，再继续 claim 或按 `WORKER_LOST` 自动恢复。
- Graph frontier、租约策略、evidence contract 和生成的 handoff 全部改为结构化 `mcpCall`/`submitMcpCalls`，不再返回已删除的 CLI `commandHint`；新增回归扫描，阻止旧 kebab-case CLI 提示重新进入源码、Plugin 载荷或交接文档。
- Claude、Codex、Cursor 或其他 Agent 仍可跨宿主规划和接续同一 frozen graph，但接收宿主必须同时提供兼容 Plugin MCP 与真实原生 Skill 调用入口；`requiredSkills` 继续支持任意 catalog 名，也继续兼容省略或空数组。
- 移除遗留 CLI harness 与 CLI 专属测试，将图查询、心跳性能和独立审查回归改为 MCP/应用服务路径；新增 Claude Hook 权限、双宿主真实 stdio 握手和 MCP-only 提示回归后全量 216 项测试通过。

## 0.15.3 — 2026-07-28

发布提交：`f3ebf4f`

- 修复 active/manual 的 required Skill 二次确认缺陷：用户批准整树与开发方式时已完成一次授权，frontier action 改为执行适配器自动原生调用指令；策略与缺失激活错误明确 `userActionRequired=false`，禁止要求用户再次输入 `$skill`、确认 Skill 或复制触发文本，同时保留逐 attempt/operation 的激活、符合性和真实产物审计。
- 分离方案创建宿主与当前阶段执行宿主：frozen `hostRuntime` 只保留规划审计和宿主自动化提示，不再限制 required Skill 的实际执行宿主；Claude、Codex、Cursor 或其他 Agent CLI 均可恢复同一 frozen graph，无需重新 prepare/freeze。
- required Skill 新激活统一使用 `HOST_NATIVE_SKILL`，不再硬编码 Claude/Codex 机制分支。Plugin MCP 从当前连接的 sandbox metadata 或标准 `clientInfo.name` 生成安全的实际 Agent 标识；CLI fallback 的 Skill activation/conformance 显式要求任意合法 `--host-runtime`。既有 schema v3 的 Claude/Codex 激活事件仍可验证和投影。
- MCP/CLI/直接 Python 生命周期入口均要求明确的当前执行宿主，不再回退到 frozen planning host。会话身份和 native invocation ID 属于宿主上报凭证；控制器验证绑定、唯一性与符合性，但不宣称在缺少宿主签名/回调时提供密码学调用证明。
- `record_skill_conformance` 要求由原 activation 的同一执行宿主写入；门禁从当前 node attempt 的有效 Graph 事件判断，不再按方案创建宿主过滤真实凭证。既有 0.15.1/0.15.2 frozen delivery 可直接由另一宿主接续。
- 增加 Claude/Codex/Cursor/其他 Agent 的规划开发组合、错误原生机制、跨宿主 conformance、防伪事件、MCP 客户端归一化、CLI fallback 和 manual 交接回归；全量 231 项测试通过。

## 0.15.2 — 2026-07-28

发布提交：`4ff9304`

- 精简 manual 冻结返回的 `handoffCommand`，只保留需求 ID、`graph_frontier` 恢复、完整 Graph 执行、开发测试门禁、禁止重复冻结和最终确认边界，降低人工复制与理解成本。
- Claude Auto、MCP/CLI fallback、required Skill 激活与符合性、时区展示和权限约束继续由结构化返回、`requirement-handoff.md` 与 Skill 契约承载，不削弱现有治理门禁。
- 增加交接命令固定文案长度与实现细节隔离回归；Python 3.14 全量 220 项测试通过。

## 0.15.1 — 2026-07-28

发布提交：`01af219`

- required Skill 不设控制器白名单：需求冻结的任意合法 catalog 名都逐项执行。Claude 必须以 Skill tool-use、Codex 必须以显式 `$skill` 原生触发形成 `SKILL_ACTIVATED` Graph 凭证；Read/load 不算激活，同一原生调用 ID 不得复用。
- MCP 工具由 35 个增至 37 个，新增 `record_skill_activation` 与 `record_skill_conformance`。result、gate 和 review 成功前须提交绑定当前 node attempt 的非空检查并全部 PASS，artifact 中的 `skillUsage` 自述不能替代原生激活与实际符合性。
- 开发复核与验收报告新增“实际 Skill 原生调用与符合性”，直接投影 Graph 中的 host/mechanism、attempt、native invocation ID、调用/符合性状态、命名检查和凭证 hash，不从 baseline 或文件读取记录推断；恢复时重新校验已存 artifact 对应的 activation/conformance。
- schema v3 的 `requiredSkills` 可省略或显式传 `[]`，两者都规范化为空数组并保持无门禁兼容；不新增旧 schema 迁移入口。
- 增加任意 Skill 名、Claude/Codex 原生调用、load 拒绝、凭证唯一性、符合性门禁、报告真实性和恢复校验回归；Python 3.12 全量 219 项测试通过。

## 0.15.0 — 2026-07-28

发布提交：`64879e4`

- 新增 Python 标准库实现的单进程 stdio MCP Server，以 35 个结构化工具覆盖工作区状态识别、分层规划、Graph 推进、执行、门禁、审查、确认、恢复和超限 payload 暂存；CLI 保留为 MCP 不可用时的 fallback。
- MCP 与 CLI 改为共用应用服务和 SQLite repository，不通过 MCP 包装或解析 CLI 子进程。
- 项目根在 Server 生命周期内绑定一次：Claude 使用项目环境变量，Codex 使用宿主注入的可信 sandbox cwd；`root`、维护专用 `dogfood` 和确认布尔值 `confirmed` 不进入工具参数，根发生漂移时拒绝调用。
- Codex/Claude Plugin 增加各自的 MCP 配置与内嵌启动器，权限从任意 Bash/Python 通配规则收窄为 MCP tool 级控制；30 个中段工具可自动执行，冻结、重建、取消、人工审查接受和最终确认保持人工 prompt。识别到低于 2.1.199 的 Claude Code 时，Server 拒绝可能被旧宿主忽略强制交互元数据的敏感调用。
- 明确 active/manual 契约：用户确认开发方案后，当前窗口或新运行窗口从同一 graph run 自动完成范围内开发、测试、门禁、预算内重试和租约恢复，直到最终验收阶段；`USER_CONFIRMED` 及 Git、发布、迁移和新增外部权限仍需用户授权。
- schema v3 baseline 新增 `requiredSkills`：按 `DEVELOPMENT/GATE/FINAL_REVIEW` 冻结可移植 Skill 名和使用目的，根级要求向后代继承；frontier、Task context 和 evidence contract 持续投影，成功迁移必须逐项提交具体 `skillUsage`。最终验收报告按 Task、operation 和 result 状态聚合实际开发调用，并另列 gate/review 使用审计。
- required Skill evidence 拒绝控制器模板占位符；存在只读隔离后代时机械阻断祖先聚合 gate、根 review 和最终用户确认，同时保持无关需求与有效兄弟 Task 可继续。
- 冻结的 `FINAL_REVIEW` Skill 不可用时可用 `REVIEW_BLOCKED` 持久化具体阻断 evidence；Graph 明确路由到人工干预，并可在问题消除后通过 `retry-item` 创建新的 review attempt，不能绕过为 PASS。
- 补齐 MCP 生命周期、请求 ID、已知工具参数错误、输入深度/复杂度限制和自由文本脱敏；超出 8 MiB 的输入行只报错一次、限块排空后继续，环境变量凭据、常见服务 token 和宿主/容器绝对路径不回传模型。
- 新增目标绑定的无损 payload 暂存：64 MiB 单包、1 MiB 分块、每项目 16 个未过期 upload / 256 MiB 配额、128 字符 upload ID 上限和 Server 生成的 generation fencing；逐块及整包 SHA-256、严格 JSON/UTF-8、重复键/孤立代理项/非有限数字拒绝、紧凑无键名回显状态与一小时逻辑过期。finalize 不修改业务状态，仍须调用原业务工具并经过原权限与事务门禁；分块解决传输，不宣称宿主上下文压缩。
- 新增无参数 MCP `workspace_status` 与 CLI `workspace-status`，机械区分 `ABSENT`、`STAGING_ONLY` 与 `ACTIVE`；Graph/interaction MCP 日志改为最多 200 项的 cursor 分页，并把查询分页下推到 SQLite/事件流，避免把整份历史保留在响应内存。
- 强化数据库与输出边界：治理库拒绝符号链接、跨路径硬链接、缺失 payload `CHECK`/复合键/级联外键/过期索引的伪 schema v3；dogfood 检测覆盖源码仓库子目录和带 TOML 行内注释的项目名；自由文本脱敏覆盖带空格 Windows 路径及常见容器路径。
- 增加 MCP/CLI、严格 JSON、payload 并发与配额、权限、schema、分页和 required Skill 审计回归；Python 3.14 全量测试 213 项通过。
- 只读查询改用 SQLite `mode=ro`，不再持久改写数据库日志模式；提交后投影增加可重入的跨线程、跨进程轻量锁，并在锁内追赶最新 revision，消除并行写入时的旧投影覆盖和 Windows 文件替换竞争。

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
