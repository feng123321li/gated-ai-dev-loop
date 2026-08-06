# MCP 连接与协调根

正常 hierarchy、Loop payload 和 outcome 直接传给对应 MCP 工具。Agent 只从 MCP 响应取得调度数据；当前调度器不提供第二套 CLI、直接 SQLite 或 payload 暂存旁路。

## 连接失败

- Plugin 未安装、工具未注册或 MCP 未连接：报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 运行中断连：报告 `PLUGIN_MCP_DISCONNECTED`，保留最后已知 root、node 与 operation。AUTOMATIC 已选择但尚待 worktree 时同时保留双 fingerprint；新会话从 `workspace_status(root_id)` 的 `executionSelection` 恢复并调用 `resume_execution_mode`，不得再次展示选择器。
- 响应未返回的写操作状态视为未知；重连后先调用 `workspace_status`。调用发生在当前对话工作区，已知 Delivery 时显式传 `root_id`。linked Git worktree 会映射到主 checkout 的共享控制根，但通过 `workspaceKey` 只访问本对话绑定的 Delivery。仅当返回 `ACTIVE`、`BLOCKED`、`PAUSED`、`COMPLETED` 或 `CANCELLED` 且存在 `rootId` 时，再调用 `graph_status` 和 `graph_frontier`；`ABSENT` 或 `PREPARED` 按规划说明恢复，不调用尚不存在 run 的工具，也不盲目重放写操作。
- Git Delivery 重连时同时核对 `gitBinding` 与 `gitWorkspace`。如果只是临时切到其他分支，切回绑定的 feature 分支后重新调用 `workspace_status`；不要让控制器自动 `git switch`。HEAD 可以随本 Delivery commit 前进，但必须继续继承冻结的 `baseCommit`，且本地或 `origin` 的同名 `baseRef` / `integrationTarget` 必须仍包含该基线。未冻结的新工作区按宿主显式 `base_ref`、有效 `origin/HEAD`、本地 `main`、本地 `master` 的顺序发现基线，并从 `worktreeProvenance` 读取实际的 `selectionSource`、`baseRef`、`baseCommit`、`baseHeadCommit` 与 `integrationTarget`；不要从分支名前缀推断来源。脏 linked worktree 必须由用户确认当前全部 diff，并以原响应的精确 `workingTree.stateFingerprint` 作为 `confirmed_dirty_state_fingerprint` 重查；指纹变化或分支已被其他 worktree/Delivery 使用时不得复用。

## 协议与项目根

Plugin 优先使用 MCP `2026-07-28`。现代客户端可先调用 `server/discover`，随后每次请求都携带协议版本、客户端能力和宿主提供的项目上下文，不依赖连接或初始化会话。旧客户端继续使用 `initialize`，最高协商到 `2025-11-25`。

`preview_hierarchy.executionChoice` 使用独立 schema v2 交互契约。`activeHostMapping` 指向当前可信 Adapter 的原生问题工具；该工具在当前上下文可调用时必须直接消费 `options`，不得先输出文本问题。只有工具未暴露或当前模式不可调用时，才按 `presentationPolicy.fallback` 逐字显示 `markdown`，不得追加“回复自动”等 Agent 文案。

Claude Plugin 通过启动环境 `${CLAUDE_PROJECT_DIR}` 固定项目协调根。裸 CLI 从 primary checkout 启动时使用 `EXCLUSIVE_PRIMARY_CHECKOUT`：有效 feature 分支可直接绑定一个未结束 Delivery；主线或 detached 状态在取得 Git 授权后于当前 checkout 建立 Delivery feature 分支，同一会话即可用 `workspace_status → resume_execution_mode` 续接，`${CLAUDE_PROJECT_DIR}` 无需漂移。只有并行/占用场景才需要新的 linked worktree 会话；此时必须从目标 worktree 启动新会话，不能只在旧会话调用 `EnterWorktree` 后沿用旧 MCP。Codex 的现代请求从每次请求 `_meta` 解析项目根；旧版 Codex 会话则在首次合法元数据后绑定不可漂移的根。Codex primary 使用 `HOST_NATIVE_LINKED_WORKTREE` 和 `hostDispatch`，宿主缺少创建项目任务的 API 时报告 `HOST_NATIVE_WORKTREE_LAUNCH_UNAVAILABLE`。无论从哪种宿主取得，Controller 的单次 operation 都只接收一个已解析、已校验的项目根；该根是 `.layered-delivery/` 控制面位置，不等于 hierarchy 的 `delivery.id` 或递归 `root` 节点。

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
                    └── interfaces/
                        └── 001-<接口标识>.md  # 每接口一份详情
```

不要把不同 Delivery 的文件写回 `.layered-delivery/` 根目录，也不要从标题临时生成或改写 `<delivery-id>`。用户提供外部工单号时使用 `delivery.requirementKey` 固定业务身份；同一 key 只能对应一个 `<delivery-id>`，常见 `PROJECT-123` 标识即使只出现在 ID/标题中也会被 Controller 识别，换 ID 重复冻结将在 preview 和最终写入时被拒绝。自动与手动开发均使用稳定的 `.layered-delivery/<delivery-id>/` 和同结构标准投影；手动开发另含自包含 handoff 文件。不得另建共享 `handoffs` 目录。

SQLite 是需求与调度状态的机器权威。手动开发包把双 fingerprint 锁定的需求以 `HANDOFF_READY` 登记到 `hierarchies` 与 `delivery_revisions`，创建共享 `scheduler.db` 和根 `overview.md`；`requirementSnapshotStatus=FROZEN` 仍不表示 Graph 已 prepare、freeze 或运行，且不创建 `runs`、事件链或 workspace 绑定。其标准投影从 SQLite 登记内容重建，并保留 `handoff-<fingerprint>.md`；以后若同一 fingerprint 进入自动 Graph，控制器在原目录刷新标准投影并保留 handoff。每次合法需求或 Graph 状态变更提交后，控制器重新读取 SQLite，用同一套内置模板生成上述中文投影，并通过原子替换刷新；不生成 hierarchy、Graph 或运行状态 JSON 副本。`work-items/` 从根节点开始，以 `children/<child-id>/` 递归镜像 hierarchy 的真实父子关系；GROUP 可多层、平行或不存在，根 TASK 不增加虚拟 GROUP。重新 prepare 删除或改名节点、移除接口声明时，控制器整体替换目录并清除旧文件。Agent 通过合法 MCP 输入提交的 hierarchy、summary 和 payload 会按模板成为投影中的领域数据；模板结构、固定相对文件名、序列化和文件写入不属于 MCP 输入，Agent 不得选择、拼接或执行它们。

根级 `overview.md` 只列 Delivery 标识、标题、中文状态、最近更新时间和详情链接；Delivery `overview.md` 才展示本交付的 TASK 完成度、GROUP 数量和导航。生成根总览时各 Delivery 独立校验；某个无关 Delivery 的 SQLite 定义损坏时，该行显示“调度状态异常”和错误代码，但不得阻断健康 Delivery 的 frontier、状态查询或投影刷新。其他 Delivery 的投影目录被人为改坏或暂时不可写时，显式 `workspace_status(rootId=...)` 通过 `projectionIssues` 报告该 Delivery，也不阻断当前 Delivery。直接查询损坏 Delivery 时仍 fail closed，并在错误 details 中返回实际 `rootId`。顶层 baseline/progress 串联整棵节点投影树；验收报告只完整展开当前层，GROUP 对直接子节点、Delivery 对根工作项仅展示状态、简要结果和报告链接，不复制下层输入、证据或 Review findings。progress 的节点状态表展示外层 receiver、认领身份和执行轮次；内部 Worker 的 agent/model/effort 仅从最终 `workerTelemetry` 非权威展示，未知值为 `unreported`。acceptance 的结果摘要、子节点验收和 P0/P1/P2 问题使用表格，当前层长输入与证据继续使用结构化列表。每个 GROUP/TASK 的 baseline 单独展示 summary、dependsOn、Loop 引用、资源声明、不透明输入、共享 Skill Hint 和双指纹。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md` 索引和 `interfaces/` 下每接口一份详情，确定性比较完整 before/after 契约。入参表逐字段展示类型、必填、说明和示例值，出参表不展示必填；空字段列表明确显示“无入参”或“无出参”。HTTP 请求位置容器只用于组织实际字段，Controller 返回类型/字段只用于还原 VO 契约；`wireType`、`frameworkEnvelope`、`wrapping` 和 `Rs` 包装信息一律忽略，容器和元数据本身不得成为字段行。HTTP 详情按 Path、Query、请求头、请求体和响应参数分区，Dubbo 详情按接口、方法、调用参数和返回结果分区，并展示必填、最大长度、说明和示例值。删除值使用 Markdown 删除线，新增或删除字段只显示存在的一侧，真正修改的属性才使用“修改前 → 修改后”。`protocol` 是开放字符串，通用协议可用 `identifier`，HTTP/Dubbo 仍支持结构化调用字段。冻结 baseline 的 after 是开发接口与后续 Torna 发布的唯一事实来源，两者的方法、路径或签名以及字段层级和属性必须一致；无声明时不生成。代码只可辅助准备和校验显式契约，不是动态投影源。所有标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`，`scheduler.db` 与事件链中的机器时间继续保持 UTC。

`preview_hierarchy` 的 `CHOICE_READY` 阶段就在展示 Controller 选项前生成四份 Delivery 人类主投影、revisions 和全部 GROUP/TASK 节点投影，有接口声明的 TASK 再生成自己的接口投影。自动 Graph 冻结后继续从 SQLite 刷新进度与验收 Markdown；手动接收 CLI 在任何代码工作前调用 `start_manual_handoff` 后，也由 SQLite 事件链刷新同一套 progress/acceptance，不能人工维护或用 Markdown 替代 Review。已有 `HANDOFF_READY` 内容变化时，`create_manual_handoff` 必须携带当前 Revision、`USER_EXPLICIT_SAME_DELIVERY` 和修订原因，在原目录追加新 handoff 并把旧 Revision 标为 `SUPERSEDED`，不得换 ID 新建目录。`workspace_status` 会为当前 schema v3 Delivery 幂等补建适用的投影树，并清理旧机器 JSON，不从 Markdown 迁移 hierarchy、Graph、事件链或运行状态。

投影只供人类检查和进度掌控，不反向成为调度输入。投影缺失或被冻结输入被篡改时，应保留 SQLite 权威并交给控制器重建；Agent 不要直接打开数据库推断状态，也不要自由补写控制器拥有的 Markdown。`scheduler_metadata.state_contract` 固定当前 schema v3 Graph 生成契约，不兼容生成器必须拒绝共同写入同一个数据库，不能通过重算指纹或直接改库绕过。`HANDOFF_READY` 手动冻结内容包只用于接收和启动；接收 CLI 通过 `start_manual_handoff → graph_frontier → Loop 工具` 报告实际进展与验证结果。handoff、overview、baseline、progress、acceptance、revisions、接口契约和双 fingerprint 均由 SQLite 冻结输入与事件链重建，不得直接修改。需求变化时回到需求会话生成新快照。

多项目交付应选择一个可治理所有相关资源的协调根，并在 TASK/Review Loop 的 payload/ref 中描述实际目标项目；不要通过业务参数切换协调根，也不要启动第二个 Server 绕过绑定。

旧的固定 Delivery/Capability/Task hierarchy 与当前递归 GROUP/TASK 契约不兼容。发现已有状态不满足当前 `hierarchy_contract` 时，按工具返回的兼容性错误处理；不要现场改 SQLite、投影或把旧节点名称机械映射为 GROUP。

## 大 payload

保持外层 payload 简洁，只传内部 Loop 启动所需输入。若某个 Loop 需要大型设计、`developmentPlan`、文件 scope 或数据集，让该 Loop 使用自己的存储/传输协议并在 payload 中传引用；不要扩展 layered-delivery 的调度 schema 来承载实现内容。
