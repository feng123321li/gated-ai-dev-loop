# MCP 连接与协调根

正常 hierarchy、Loop payload 和 outcome 直接传给对应 MCP 工具。Agent 只从 MCP 响应取得调度数据；当前调度器不提供第二套 CLI、直接 SQLite 或 payload 暂存旁路。

## 连接失败

- Plugin 未安装、工具未注册或 MCP 未连接：报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 运行中断连：报告 `PLUGIN_MCP_DISCONNECTED`，保留最后已知 root、node 与 operation。
- 响应未返回的写操作状态视为未知；重连后先调用 `workspace_status`、`graph_status`、`graph_frontier`，不要盲目重放。

## 项目根

MCP Server 在会话中绑定一个不可漂移的项目协调根。它是存放 `.layered-delivery/` 控制面的工作区位置，不等于 hierarchy 的 `delivery.id` 或递归 `root` 节点。

控制面根使用共享 `.layered-delivery/scheduler.db`。每个 `delivery.id` 是稳定的需求目录 namespace，其可读投影固定为：

```text
.layered-delivery/
├── scheduler.db
└── <delivery-id>/
    ├── hierarchy.json
    ├── graph.json
    ├── state.json
    ├── overview.md
    └── task-baselines/
        ├── <task-id>.md
        └── ...
```

不要把不同 Delivery 的投影写回 `.layered-delivery/` 根目录，也不要从标题临时生成或改写 `<delivery-id>`。

SQLite 是唯一机器权威。每次合法状态变更提交后，控制器重新读取 SQLite，用内置的固定版本模板生成上述固定文件，并通过原子替换刷新投影。`task-baselines/` 是控制器拥有的平面目录，文件名只取已校验的稳定 TASK ID；重新 prepare 删除或改名 TASK 时，控制器整体替换目录并清除旧 baseline。Agent 通过合法 MCP 输入提交的 hierarchy、summary 和 payload 会按模板成为投影中的领域数据；模板结构、模板版本、固定相对文件名、序列化和文件写入不属于 MCP 输入，Agent 不得选择、拼接或执行它们。

`overview.md` 用中文和 UTC+8 时间展示 Delivery 状态、完整 GROUP/TASK 清单及当前进度，不再聚合 TASK 的详细调度基线。每个 `task-baselines/<task-id>.md` 单独展示该 TASK 的 summary、dependsOn、Loop 引用、资源声明、原始 payload、共享 Skill Hint 和双指纹；`scheduler.db`、事件链和 JSON 中的机器时间继续保持 UTC。

准备阶段先生成 hierarchy、graph、overview 和全部 TASK baseline；`state.json` 在冻结并启动 Graph 后生成。

投影只供人类检查和进度掌控，不反向成为调度输入。投影缺失或被篡改时保留 SQLite 权威并交给控制器重建；Agent 不要直接打开数据库推断状态，也不要自由补写 Markdown/JSON。

多项目交付应选择一个可治理所有相关资源的协调根，并在 TASK/Review Loop 的 payload/ref 中描述实际目标项目；不要通过业务参数切换协调根，也不要启动第二个 Server 绕过绑定。

旧的固定 Delivery/Capability/Task hierarchy 与当前递归 GROUP/TASK 契约不兼容。发现已有状态不满足当前 `hierarchy_contract` 时，按工具返回的兼容性错误处理；不要现场改 SQLite、投影或把旧节点名称机械映射为 GROUP。

## 大 payload

保持外层 payload 简洁，只传内部 Loop 启动所需输入。若某个 Loop 需要大型设计、`developmentPlan`、文件 scope 或数据集，让该 Loop 使用自己的存储/传输协议并在 payload 中传引用；不要扩展 layered-delivery 的调度 schema 来承载实现内容。
