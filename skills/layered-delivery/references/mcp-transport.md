# MCP 连接与根绑定

正常 hierarchy、Loop payload 和 outcome 直接传给对应 MCP 工具。当前调度器不提供第二套 CLI、直接 SQLite 或 payload 暂存旁路。

## 连接失败

- Plugin 未安装、工具未注册或 MCP 未连接：报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入。
- 运行中断连：报告 `PLUGIN_MCP_DISCONNECTED`，保留最后已知 root、node 与 operation。
- 响应未返回的写操作状态视为未知；重连后先调用 `workspace_status`、`graph_status`、`graph_frontier`，不要盲目重放。

## 项目根

MCP Server 在会话中绑定一个不可漂移的项目根。多项目交付应选择一个可治理所有相关资源的协调根，并在 Loop payload/ref 中描述目标项目；不要通过业务参数切换根，也不要启动第二个 Server 绕过绑定。

## 大 payload

保持外层 payload 简洁，只传内部 Loop 启动所需输入。若某个 Loop 需要大型设计或数据集，让该 Loop 使用自己的存储/传输协议并在 payload 中传引用；不要扩展 layered-delivery 的调度 schema 来承载实现内容。
