# MCP 连接与协调根

正常 hierarchy、Loop payload 和 outcome 直接传给对应 MCP 工具。Agent 只从 MCP 响应取得调度数据；当前调度器不提供第二套 CLI、直接 SQLite 或 payload 暂存旁路。

## 连接失败

- Plugin 未安装、工具未注册或 MCP 未连接：报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 运行中断连：报告 `PLUGIN_MCP_DISCONNECTED`，保留最后已知 root、node 与 operation。
- 响应未返回的写操作状态视为未知；重连后先调用 `workspace_status`。仅当返回 `ACTIVE`、`BLOCKED`、`PAUSED`、`COMPLETED` 或 `CANCELLED` 且存在 `rootId` 时，再调用 `graph_status` 和 `graph_frontier`；`ABSENT` 或 `PREPARED` 按规划说明恢复，不调用尚不存在 run 的工具，也不盲目重放写操作。

## 协议与项目根

Plugin 优先使用 MCP `2026-07-28`。现代客户端可先调用 `server/discover`，随后每次请求都携带协议版本、客户端能力和宿主提供的项目上下文，不依赖连接或初始化会话。旧客户端继续使用 `initialize`，最高协商到 `2025-11-25`。

Claude Plugin 通过启动环境固定项目协调根。Codex 的现代请求从每次请求 `_meta` 解析项目根；旧版 Codex 会话则在首次合法元数据后绑定不可漂移的根。无论从哪种宿主取得，Controller 的单次 operation 都只接收一个已解析、已校验的项目根。它是存放 `.layered-delivery/` 控制面的工作区位置，不等于 hierarchy 的 `delivery.id` 或递归 `root` 节点。

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
            ├── interfaces.md  # 仅当根为接口型 TASK
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
                    └── interfaces.md  # 仅当本 TASK 声明接口
```

不要把不同 Delivery 的投影写回 `.layered-delivery/` 根目录，也不要从标题临时生成或改写 `<delivery-id>`。

SQLite 是唯一机器权威。每次合法状态变更提交后，控制器重新读取 SQLite，用内置模板生成上述中文文件，并通过原子替换刷新投影；不生成 hierarchy、Graph 或运行状态 JSON 副本。`work-items/` 从根节点开始，以 `children/<child-id>/` 递归镜像 hierarchy 的真实父子关系；GROUP 可多层、平行或不存在，根 TASK 不增加虚拟 GROUP。重新 prepare 删除或改名节点、移除接口声明时，控制器整体替换目录并清除旧文件。Agent 通过合法 MCP 输入提交的 hierarchy、summary 和 payload 会按模板成为投影中的领域数据；模板结构、固定相对文件名、序列化和文件写入不属于 MCP 输入，Agent 不得选择、拼接或执行它们。

根级 `overview.md` 只列 Delivery 标识、标题、中文状态、最近更新时间和详情链接；Delivery `overview.md` 才展示本交付的 TASK 完成度、GROUP 数量和导航。顶层 baseline/progress 串联整棵节点投影树；验收报告只完整展开当前层，GROUP 对直接子节点、Delivery 对根工作项仅展示状态、简要结果和报告链接，不复制下层输入、证据或 Review findings。progress 的节点状态、acceptance 的结果摘要、子节点验收和 P0/P1/P2 问题使用表格，当前层长输入与证据继续使用结构化列表。每个 GROUP/TASK 的 baseline 单独展示 summary、dependsOn、Loop 引用、资源声明、不透明输入、共享 Skill Hint 和双指纹。只有 TASK payload 显式声明接口时，才在该 TASK 目录生成 `interfaces.md`，确定性展示 `changeType` 与完整 before/after 调用标识、入参和出参；`protocol` 是开放字符串，通用协议可用 `identifier`，HTTP/Dubbo 仍支持结构化调用字段。无声明时不生成。代码只可辅助准备和校验显式契约，不是动态投影源。所有标明 UTC+8 的人类时间使用 `YYYY-MM-DD HH:mm:ss`，`scheduler.db` 与事件链中的机器时间继续保持 UTC。

准备阶段生成四份 Delivery 人类主投影和全部 GROUP/TASK 节点投影，有接口声明的 TASK 再生成自己的接口投影；冻结后继续从 SQLite 刷新进度与验收 Markdown。`workspace_status` 会为早期 schema v3 Delivery 幂等补建当前适用的投影树，并清理旧机器 JSON，不迁移 hierarchy、Graph、事件链或运行状态。

投影只供人类检查和进度掌控，不反向成为调度输入。投影缺失或被篡改时保留 SQLite 权威并交给控制器重建；Agent 不要直接打开数据库推断状态，也不要自由补写 Markdown。

多项目交付应选择一个可治理所有相关资源的协调根，并在 TASK/Review Loop 的 payload/ref 中描述实际目标项目；不要通过业务参数切换协调根，也不要启动第二个 Server 绕过绑定。

旧的固定 Delivery/Capability/Task hierarchy 与当前递归 GROUP/TASK 契约不兼容。发现已有状态不满足当前 `hierarchy_contract` 时，按工具返回的兼容性错误处理；不要现场改 SQLite、投影或把旧节点名称机械映射为 GROUP。

## 大 payload

保持外层 payload 简洁，只传内部 Loop 启动所需输入。若某个 Loop 需要大型设计、`developmentPlan`、文件 scope 或数据集，让该 Loop 使用自己的存储/传输协议并在 payload 中传引用；不要扩展 layered-delivery 的调度 schema 来承载实现内容。
