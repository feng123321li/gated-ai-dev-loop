# MCP 连接与协调根

正常 hierarchy、Loop payload 和 outcome 直接传给对应 MCP 工具。Agent 只从 MCP 响应取得调度数据；当前调度器不提供第二套 CLI、直接 SQLite 或 payload 暂存旁路。

生命周期字段将 Graph 阶段与 Delivery 上线状态分开：`runStatus=COMPLETED` 只表示当前 Revision 完成；`deliveryClosure=OPEN` 显示“未上线”并允许追加 Revision，`close_delivery` 后变为 `CLOSED/已上线交付` 且禁止追加。`archive_delivery` 只接受已关闭 Delivery。根级与 Delivery 级 overview 都分别展示当前阶段和上线状态。

## 连接失败

- Plugin 未安装、工具未注册或 MCP 未连接：报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 运行中断连：报告 `PLUGIN_MCP_DISCONNECTED`，保留最后已知 root、node、operation 与双 fingerprint。恢复时用显式 `workspace_status(root_id=...)` 读取同一 Delivery，继续 `CURRENT_WORKSPACE_SERIAL` 的当前分支状态；不得创建新顶层会话、猜选另一个 Delivery、创建新 worktree 或再次展示选择器。
- 响应未返回的写操作状态视为未知；重连后先调用 `workspace_status`。调用发生在当前对话工作区，已知 Delivery 时必须显式传 `root_id`。一个 `workspaceKey` 可以绑定多个 Delivery；无参查询返回 `DELIVERY_SELECTION_REQUIRED` 时只展示候选并用本会话保存的 `rootId` 重查，不按更新时间选择。当前目录即使是既有 linked checkout，也只作为普通 current workspace。仅当显式查询返回 `ACTIVE`、`BLOCKED`、`PAUSED`、`COMPLETED` 或 `CANCELLED` 且存在 `rootId` 时，再调用 `graph_status` 和 `graph_frontier`。
- Git Delivery 重连时同时核对 `gitBinding` 与 `gitWorkspace`。当前 checkout 若位于另一 Delivery 分支，必须先证明前一个 Delivery 已进入 `PAUSED`、Run 终态或最终用户确认边界，并形成冻结独立分支上的可验证业务 commit、working tree/index clean、HEAD 与冻结 binding 一致且没有在途 receiver/reservation；状态变化本身不算释放。标准顺序固定为 `quiesce → eligible state → commit/clean on frozen branch → protocol recheck/persist WORKSPACE_TURN_RELEASED → branch switch/next run`。`workspaceRelease=PENDING` 时只执行响应的 `nextAction`，不得先切分支；只有 `RELEASED` 才消费 `automaticHostPreparation` 或 `manualHostPreparation`。已持久化的 AUTOMATIC/MANUAL 选择都按 `deliveryQueue.continuation` 等待，轮到队首后宿主必要时核对精确指纹并 stash 业务改动（排除 `.layered-delivery/**`），创建或切换目标分支，再调用 `resume_execution_mode` 或 `start_manual_handoff`；Controller 自身不执行 Git 写操作，也不得重试 `select_execution_mode`。待用户确认的 Delivery 可在释放后从另一分支按旧 `rootId` 调用 `record_user_confirmation`，该控制面写入不要求恢复旧 checkout。未冻结的新工作区按宿主显式 `base_ref`、有效 `origin/HEAD`、本地 `main`、本地 `master` 的顺序发现基线。资源冲突、owner dirty、未合并、HEAD 漂移或无法证明安全释放时保持等待。

## 协议与项目根

Plugin 优先使用 MCP `2026-07-28`。现代客户端可先调用 `server/discover`，随后每次请求都携带协议版本、客户端能力和宿主提供的项目上下文，不依赖连接或初始化会话。旧客户端继续使用 `initialize`，最高协商到 `2025-11-25`。

宿主支持 MCP Apps UI 时，已知存在 Graph run 的 Delivery 可调用 `open_delivery_dashboard(root_id)` 显示只读运行看板。该工具返回当前 Graph 真实 edges、节点状态、活动 Loop、告警和 Revision 元数据；不返回 Loop payload、operation ID、Revision 原因或操作者。派遣成功和每次真实心跳的响应也直接携带 `progressMonitor`，宿主先在主 Agent 输出派遣后的首次面板，再用心跳响应刷新最后心跳、剩余租期和续租状态。面板的“刷新状态”只重放这个只读工具，不能用 `graph_frontier` 代替，因为后者会先调用 `advance_graph` 并可能改变控制面状态。宿主不支持 UI 时，继续展示工具返回的文字和 `structuredContent`，不要因此改用 SQLite、投影文件或第二套接口。

Codex Desktop 的 standard MCP Apps `tools/call` 刷新会携带 modern 协议 metadata，但可能省略 sandbox metadata；`window.openai.callTool` 兼容 bridge 还可能采用 legacy 省略形式。只有同一可信 Codex MCP 连接先以有效 `codex/sandbox-state-meta` 成功读取过同一 `root_id`，Adapter 才允许这两类刷新复用当时的精确 workspace。grant 按 modern/legacy era 隔离，5 分钟内成功刷新会滑动续期，每连接最多 8 个 root；同 root 的新授权替换旧 workspace，超时、容量淘汰和连接关闭都会撤销。它不跨连接、宿主或进程传递，也不授权其他 root、其他 MCP 工具或写操作；legacy 显式空/畸形 metadata 不能伪装成 bridge 省略。过期或断连后必须由宿主重新发起一次带 sandbox metadata 的 `open_delivery_dashboard`，不能降低全局项目根策略。

所有等待用户选择的响应统一发布 `pendingInteraction`；当前 `kind` 为 `DEVELOPMENT_BASELINE` 或 `EXECUTION_MODE`。`activeHostMapping` 指向当前 Adapter 的原生问题工具；该工具在当前上下文可调用时必须直接消费 `options`，不得先输出文本问题。只有工具未暴露或当前模式不可调用时，才按 `presentationPolicy.fallback` 逐字显示 `markdown`，不得追加“回复自动”等 Agent 文案。`developmentBaseline` / `executionChoice` 暂时指向同一对象作为兼容别名，不是第二套状态机。

Claude Plugin 通过启动环境 `${CLAUDE_PROJECT_DIR}` 固定共享控制根，Codex 与 ZCode 的现代请求从每次请求 `_meta` 解析项目根；Adapter 为每次请求提供实际执行 workspace，模型输入路径不能替代它。工作区执行固定为 `CURRENT_WORKSPACE_SERIAL`：已有 owner 时，后来选择 `AUTOMATIC` 或 `MANUAL` 的 Delivery 都标记为 `QUEUED`；前一个 Delivery 到达 `PAUSED`、Run 终态或最终用户确认边界后，仍须在全部冻结独立分支完成业务 commit/clean/binding 复核、receiver/reservation 收束并持久化 `WORKSPACE_TURN_RELEASED`，宿主才执行已授权的 mode-specific stash/create-or-switch/continue 准备。同一时刻只运行一个显式 `rootId`。既有 primary 或 linked checkout 都作为当前实际 workspace，不自动创建另一个 worktree。主会话从共享控制根读取状态以及补录已释放 Delivery 的最终用户确认时只有 `MONITOR_ONLY` 权限。

Plugin 通过 Skill、Agent 描述、MCP 和宿主元数据工作，不需要额外的生命周期信任步骤。`plan_dispatch_batch` 对所有 Ready TASK 和 Review 使用同一条 AUTO 路径：在容量、资源锁、Graph attempt 和双 fingerprint 校验后创建绑定 decision fingerprint 的短租约 reservation；宿主立即为每项 assignment 创建独立 child，child 以 `dispatch_transport=HOST_NATIVE`、reservation、decision fingerprint 和新的显式 `operation_id` 调用 `dispatch_loop(AUTO)`。未知响应只按同一 reservation/operation 恢复，不生成第二个 claim。

MANUAL TASK 不进入 AUTO planning。启动 manual run 后，独立 receiver 以显式 receiving context 和新的 `operation_id` 调用 `dispatch_loop(MANUAL)`，不带 AUTO reservation、decision fingerprint、transport 或模型选择。两种模式共享 workspace/Git/project scope、Graph attempt、operation、lease、mutation 和资源锁校验；heartbeat、progress、pause 与 result 都必须显式携带当前 `operation_id`。暂停若尚未安全释放 turn，`resume_loop` 可在当前 owner 分支恢复；若已释放，则先持久化 `WORKSPACE_TURN_REQUEUED` 并排到队尾，轮到、全部冻结 binding/clean 复核通过并记录新的 `WORKSPACE_TURN_REACQUIRED` turn start 后才把节点恢复为 Ready，再按对应模式以新 operation 和新的 reservation/resource/fingerprint 门禁重新领取。

未显式声明 `delivery.projectScopes` 的单仓 Delivery 在运行时从顶层 `delivery.gitBinding` 与 Adapter 提供的 Delivery workspace 合成唯一 `primary` scope；多仓仍逐项验证显式 scope。workspace 或 scope 无法匹配时，`dispatch_loop` 在 claim 前 fail closed，reservation 只能按既有租约/恢复规则处理。Controller 只看到 Adapter 提供的 workspace、receiver 类型和 assignment 数据，不能以密码学方式证明真实 parent-child 关系、receiver 身份延续或 reviewer 独立性；宿主必须按 assignment 创建独立 child，Controller 则继续强制校验 reservation、decision fingerprint、attempt、workspace、scope、operation、lease 和资源锁。`operation_id` 严禁出现在用户输出、日志、进度、result 或 Worker 输入。

同一实际 workspace 的多个 Delivery 只共享控制面绑定，不共享文件隔离。已选择 `AUTOMATIC` 或 `MANUAL` 的后续 Delivery 进入同一串行队列；手动冻结 Delivery 内部保持 `HANDOFF_READY`，对外同样产生 `QUEUED` 与 mode-specific continuation，接收方轮到后显式调用 `start_manual_handoff`。队首只有在前一个 Delivery 到达 `PAUSED`、Run 终态或最终用户确认边界，并在所有 READ_WRITE scope 的冻结独立分支形成可验证业务 commit、工作树 clean、HEAD 未漂移、receiver/reservation 已安全收束且 Controller 已持久化 release 后才可准备；人工 Run 适用同一规则。`CANCELLED` 的安全释放与归档分离，终态查询忽略其过期 rebase advisory。`resourceClaims`、端口、数据库或 workspace 冲突、owner dirty、未合并和 HEAD 漂移都保持等待；不得跨 Delivery 并行运行同一 checkout/branch。

控制面根使用共享 `.layered-delivery/scheduler.db`。每个 `delivery.id` 是稳定的需求目录 namespace，其可读投影固定为：

```text
.layered-delivery/
├── overview.md
├── scheduler.db
└── <delivery-id>/
    ├── overview.md
    ├── baseline.md
    ├── progress.md
    ├── acceptance.md
    └── work-items/
        └── <root-id>/
            ├── baseline.md
            ├── progress.md
            ├── acceptance.md
            ├── interfaces.md  # 接口索引；仅当根为接口型 TASK
            ├── interfaces/
            │   └── 001-<接口标识>.md  # 每接口一份详情
            ├── database-changes.md  # 数据库索引；仅当根 TASK 声明表变更
            ├── database-changes/
            │   └── 001-<表标识>.md  # 每张表一份详情
            └── children/
                ├── <child-group-id>/
                │   ├── baseline.md
                │   ├── progress.md
                │   ├── acceptance.md
                │   └── children/...
                └── <child-task-id>/
                    ├── baseline.md
                    ├── progress.md
                    ├── acceptance.md
                    ├── interfaces.md  # 接口索引；仅当本 TASK 声明接口
                    ├── interfaces/
                    │   └── 001-<接口标识>.md  # 每接口一份详情
                    ├── database-changes.md  # 仅当本 TASK 声明表变更
                    └── database-changes/
                        └── 001-<表标识>.md  # 每张表一份详情
```

不要把不同 Delivery 的文件写回 `.layered-delivery/` 根目录，也不要从标题临时生成或改写 `<delivery-id>`。用户提供外部工单号时使用 `delivery.requirementKey` 固定业务身份；同一 key 只能对应一个 `<delivery-id>`，常见 `PROJECT-123` 标识即使只出现在 ID/标题中也会被 Controller 识别，换 ID 重复冻结将在 preview 和最终写入时被拒绝。自动与手动开发均使用稳定的 `.layered-delivery/<delivery-id>/` 和同结构标准投影；手动开发另含自包含 handoff 文件。不得另建共享 `handoffs` 目录。

SQLite 是需求与调度状态的机器权威。手动开发包把双 fingerprint 锁定的需求以 `HANDOFF_READY` 登记到 `hierarchies` 与 `delivery_revisions`，并原子记录 MANUAL 选择与当前 workspace 队列绑定；`requirementSnapshotStatus=FROZEN` 仍不表示 Graph 已 prepare、freeze 或运行，且不创建 `runs` 或事件链。其标准投影从 SQLite 登记内容重建，并保留 `handoff-<fingerprint>.md`；以后若同一 fingerprint 进入运行 Graph，控制器在原目录刷新标准投影并保留 handoff。合法的冻结/派遣、显式业务进度、暂停/恢复、结果、重试和终态等关键变更提交后，控制器重新读取 SQLite，用同一套内置模板生成上述中文投影，并通过原子替换刷新；高频 heartbeat 只更新 SQLite 运行态、审计事件和 Agent 实时面板，不重写 Markdown 投影。不生成 hierarchy、Graph 或运行状态 JSON 副本。`work-items/` 从根节点开始，以 `children/<child-id>/` 递归镜像 hierarchy 的真实父子关系；GROUP 可多层、平行或不存在，根 TASK 不增加虚拟 GROUP。重新 prepare 删除或改名节点、移除接口声明时，控制器整体替换目录并清除旧文件。Agent 通过合法 MCP 输入提交的 hierarchy、summary 和 payload 会按模板成为投影中的领域数据；模板结构、固定相对文件名、序列化和文件写入不属于 MCP 输入，Agent 不得选择、拼接或执行它们。

根级 `overview.md` 只列未归档 Delivery 的标识、标题、中文状态、最近更新时间和详情链接；Delivery `overview.md` 才展示本交付的 TASK 完成度、GROUP 数量和导航。归档不释放 `requirementKey`，也不删除 SQLite、事件链或详情投影；显式 `root_id` 仍可读取 `ARCHIVED` 状态与历史。生成根总览时各未归档 Delivery 独立校验；某个无关 Delivery 的 SQLite 定义损坏时，该行显示“调度状态异常”和错误代码，但不得阻断健康 Delivery 的 frontier、状态查询或投影刷新。其他 Delivery 的投影目录被人为改坏或暂时不可写时，显式 `workspace_status(rootId=...)` 通过 `projectionIssues` 报告该 Delivery，也不阻断当前 Delivery。直接查询损坏 Delivery 时仍 fail closed，并在错误 details 中返回实际 `rootId`。顶层 baseline/progress 串联整棵节点投影树；验收报告只完整展开当前层，GROUP 对直接子节点、Delivery 对根工作项仅展示状态、简要结果和报告链接，不复制下层输入、证据或 Review findings。progress 的节点状态表只展示外层 receiver、认领身份和执行轮次。acceptance 的结果摘要、子节点验收和 P0/P1/P2 问题使用表格，当前层长输入与证据继续使用结构化列表。每个 GROUP/TASK 的 baseline 单独展示 summary、dependsOn、Loop 引用、资源声明、不透明输入、共享 Skill Hint 和双指纹。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md` 索引和 `interfaces/` 下每接口一份详情，确定性比较完整 before/after 契约。入参表逐字段展示类型、必填、说明和示例值，出参表不展示必填；空字段列表明确显示“无入参”或“无出参”。HTTP 请求位置容器只用于组织实际字段，Controller 返回类型/字段只用于还原 VO 契约；`wireType`、`frameworkEnvelope`、`wrapping` 和 `Rs` 包装信息一律忽略，容器和元数据本身不得成为字段行。HTTP 详情按 Path、Query、请求头、请求体和响应参数分区，Dubbo 详情按接口、方法、调用参数和返回结果分区，并展示必填、最大长度、说明和示例值。删除值使用 Markdown 删除线，新增或删除字段只显示存在的一侧，真正修改的属性才使用“修改前 → 修改后”。`protocol` 是开放字符串，通用协议可用 `identifier`，HTTP/Dubbo 仍支持结构化调用字段。冻结 baseline 的 after 是开发接口与后续 Torna 发布的唯一事实来源，两者的方法、路径或签名以及字段层级和属性必须一致；无声明时不生成。代码只可辅助准备和校验显式契约，不是动态投影源。所有标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`，`scheduler.db` 与事件链中的机器时间继续保持 UTC。

Review 投影进一步按层收敛：TASK 只展开 `taskAcceptance`，GROUP 只在配置了直接子项 seam Review 时展开 `groupIntegration`，Delivery 只展开 `deliveryReadiness`。跨层只保留状态、摘要、证据引用和报告链接，不复制下层 result body 或 workspace snapshot。未配置的 GROUP Review 不生成 Graph 节点、SQLite `node_runs`/event/outcome 或空投影段落；`upstreamLoopResults` 只作为运行时 context，不写回 Review outcome。

TASK 显式声明 `databaseChanges` 时，同目录生成 `database-changes.md` 和每表详情，完整展示字段、主键、唯一约束、索引、外键、before/after 结构及迁移方案。冻结 after 是数据库 Loop 的唯一结构事实源，执行中需要偏离时必须返回 `REPLAN_REQUIRED` 并形成新 Revision；没有完整设计时不能进入 baseline 确认。

`preview_hierarchy` 的 `CHOICE_READY` 阶段就在展示 Controller 选项前生成四份 Delivery 人类主投影、revisions 和全部 GROUP/TASK 节点投影，有接口或数据库声明的 TASK 再生成自己的契约投影。`workspace_status` 会在待选择状态恢复同一 `pendingInteraction`，并在返回它之前避免把尚未确认的 binding 当成运行时漂移。自动 Graph 冻结后继续从 SQLite 刷新进度与验收 Markdown；手动接收宿主在任何代码工作前调用 `start_manual_handoff`，再由独立 child 以显式 context/operation 领取 TASK，也由 SQLite 事件链刷新同一套 progress/acceptance，不能人工维护或用 Markdown 替代 Review。若实际 Git 已偏离 handoff binding，启动操作只返回 `BLOCKED_DEVELOPMENT_BASELINE_CONFIRMATION` 与精确上下文，不创建 Run；binding 改变时 `confirm_development_baseline` 生成同一 Delivery 的下一不可变手动 Revision，未改变时恢复原 Revision，接收方按响应双指纹重试。已有 `HANDOFF_READY` 内容变化时，`create_manual_handoff` 必须携带当前 Revision、`USER_EXPLICIT_SAME_DELIVERY` 和修订原因，在原目录追加新 handoff 并把旧 Revision 标为 `SUPERSEDED`，不得换 ID 新建目录。`workspace_status` 会为当前 schema v3 Delivery 幂等补建适用的投影树，并清理旧机器 JSON，不从 Markdown 迁移 hierarchy、Graph、事件链或运行状态。

`record_loop_result` 的受保护 MCP 路径会从 Adapter 提供并由 Controller 验证的
`READ_WRITE` Git project scopes 自动采集轻量工作区变更索引，将其作为
`outcome.result.workspaceChanges` 与事件一同持久化，再生成当前层 acceptance
投影。后续投影刷新只读取 SQLite outcome，不动态扫描已移动或已删除的执行目录。
该索引只含变更文件清单、base/HEAD 和状态指纹，不含源码 diff；它不替代默认串行
策略的干净切换边界或代码归属判断。TASK 投影只在 `acceptance.md` 展示索引，
需要代码内容时从已授权 workspace 或对应提交读取。

已激活 AUTOMATIC Graph 的 TASK 与 Review 都只走 `plan_dispatch_batch → 独立 child → dispatch_loop(AUTO)`。每个 assignment 必须带 live reservation 和匹配的 decision fingerprint，child 使用新的显式 `operation_id`；不得由当前协调会话直领。`handoff_ready_automatic_task` 只用于满足其显式安全条件后的 MANUAL 接管，Review 继续自动独立派遣。

投影只供人类检查和进度掌控，不反向成为调度输入。投影缺失或被冻结输入被篡改时，应保留 SQLite 权威并交给控制器重建；Agent 不要直接打开数据库推断状态，也不要自由补写控制器拥有的 Markdown。`scheduler_metadata.state_contract` 固定当前 schema v3 存储契约，不兼容控制器必须拒绝共同写入同一个数据库，不能通过重算指纹或直接改库绕过。Graph 内另带版本化编译协议；旧 `HANDOFF_READY` 只有在无 Run 且 hierarchy、节点和边完全一致时，才可在 `start_manual_handoff` 前刷新 runtime policy 与 graph fingerprint，ACTIVE/FROZEN Graph 仍精确拒绝。`HANDOFF_READY` 手动冻结内容包只用于排队、接收和启动；接收宿主通过 `start_manual_handoff → graph_frontier → 独立 child 的显式 operation Loop 工具` 报告实际进展与验证结果。handoff、overview、baseline、progress、acceptance、revisions、接口契约和双 fingerprint 均由 SQLite 冻结输入与事件链重建，不得直接修改。需求变化时回到需求会话生成新快照。

`graph_events` 使用整数 `after_event_id` 做 keyset 分页。非零 cursor 调用会校验 cursor 对应的当前 Run 锚点、页边界和当前页事件；需要审计完整哈希链时必须从 `after_event_id=0` 开始连续消费各页。`rebuild_graph_run` 始终在调度锁内从零连续扫描，因此会校验完整事件链后才重建物化状态。

多项目交付应选择一个可治理所有相关资源的协调根，并在每个 Git `projectScopes[*]` 显式冻结完整 `gitBinding`。当前偏好记忆只负责单一顶层仓库，不会猜测 secondary 仓库；多 Git scope 缺少 binding 时 preview 提前以 `SCHEDULER_PROJECT_BASELINE_INCOMPLETE` fail closed。不要通过业务参数切换协调根，也不要启动第二个 Server 绕过绑定。

旧的固定 Delivery/Capability/Task hierarchy 与当前递归 GROUP/TASK 契约不兼容。发现已有状态不满足当前 `hierarchy_contract` 时，按工具返回的兼容性错误处理；不要现场改 SQLite、投影或把旧节点名称机械映射为 GROUP。

## 大 payload

保持外层 payload 简洁，只传内部 Loop 启动所需的需求方向、目标、明确约束、已确认外部契约和已知验收。大型设计、`developmentPlan`、文件 scope、实现类/方法选择和详细测试组织由 Loop 在运行时使用自己的存储/传输协议生成和维护，不在规划阶段先写引用再冻结；不要扩展 delivery-graph 的调度 schema 来承载实现内容。只有需求明确指定或用户确认的外部兼容契约固定了精确标识时，才把该事实放入 payload。
