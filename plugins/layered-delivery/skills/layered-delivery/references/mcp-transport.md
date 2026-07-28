# Plugin MCP-only 传输

## MCP-only 调用契约

完整 Plugin 为 Claude Code、Codex 或其他兼容 MCP 的 Agent 宿主启动一个本地 Python stdio MCP Server，并提供 37 个结构化工具。Agent 只调用宿主已经注册的工具，不自行启动第二个 Server，不手写 JSON-RPC 行，也不经 Shell 包装 MCP 调用。

- MCP Server 直接进入统一应用服务、Graph 规则和 SQLite repository，不派生任何 CLI 子进程；不得启动 `hdg.py`。
- 宿主在启动 Server 时解析被治理项目根，Server 在整个进程生命周期内固定该路径。工具 schema 不提供 `root` 或 `project_root`，单次连接不能跨项目。
- 维护源码时使用的 `dogfood` 不是模型可选的工具参数；确认由用户事件与对应专用工具表达，工具也不接受通用 `confirmed` 布尔值。
- 工具直接接收 hierarchy、interaction 和 evidence 对象并返回结构化结果；不创建临时 JSON，不经过 shell 引号和管道。
- Server stdout 只承载 MCP 消息，诊断写入 stderr。宿主负责启动、连接、请求关联、超时和 tool 级权限。
- MCP 未安装、未注册、未连接或工具注册失败时，必须报告 `PLUGIN_MCP_UNAVAILABLE` 并停止，不得开始或恢复治理写入，也不得绕过到 CLI、源码调用或直接 SQLite 写入。

Skill 只能指导 Agent 生成合规输入；工具 schema、Server 严格解码和领域控制器才是机械边界。输入被拒绝时应按结构化错误修正并重试，不能用“已经使用 Skill”跳过校验。

## 超限 JSON 的无损暂存

普通 hierarchy/evidence 直接传给原工具。只有规范 JSON 连同 MCP 信封确实可能超过 8 MiB 时，才使用同一 Server 的暂存工具；不得为了省上下文默认暂存，也不得截断、摘要或丢弃原始字段：

1. 把完整顶层 JSON 对象切成不超过 1 MiB 的非空文本块并声明块数；不要让模型计算字节数或密码学哈希；
2. 使用不超过 128 字符的安全 `upload_id` 调用 `begin_payload_upload`，把 manifest 绑定到将来真正写业务状态的 `target_tool` 和目标参数；立即保存 begin 返回的 `generationId`；
3. 逐块调用 `append_payload_chunk`，每次都传入 begin 返回的同一 `generation_id`；Server 根据实际 UTF-8 内容计算每块长度和 SHA-256；
4. 调用 `finalize_payload_upload`，同样传入 begin 返回的 `generation_id`；保存 finalize 返回的完整 `payloadRef`；
5. 调用原业务工具，把其大对象参数替换为 `{"payloadRef":{"uploadId":"...","generationId":"...","sha256":"...","sizeBytes":123}}`。四个字段必须原样来自 finalize 结果。分块只解决传输，不表示原业务操作已执行。

分块解决的是单条 MCP 消息上限，不是宿主上下文压缩协议。Server 不在结果中回显 chunk 或完整 payload，但宿主仍可能把工具参数保留在任务历史。

## 恢复和身份

manual 接收 Agent 的恢复入口是 `graph_frontier`，不是 `task_context`。`task_context` 只提供未认领 Task 的诊断预览，不会 claim Task，也不授权开发。

用户冻结整树并选择 manual 后已经一次授权 required Skill。Graph 返回 `DISPATCH_TASK` 后，执行适配器自动逐项使用当前宿主原生入口调用 Skill，并通过 MCP `record_skill_activation` 绑定当前执行宿主、node attempt、owner/operation 和独立原生调用 ID；完整执行后由同一宿主使用 `record_skill_conformance` 绑定实际检查。MCP 从当前连接的标准 `clientInfo.name` 归一化宿主，Codex 还可由 sandbox metadata 绑定。没有可验证原生 Skill 入口的 Agent 必须记录 `BLOCKED`，不得用 Read/load 冒充。

## 失败处理

- Server 未出现或显示断连：检查 Plugin 是否安装、启用并已更新，Python 3.10+ 是否可从 PATH 启动，然后重启 Agent 会话；在恢复前不得写治理状态。
- Claude Code 的 `Connected · tools fetch failed` 表示进程已连接但工具 schema 注册失败，不应误判为 Server 未启动；保留具体 schema 错误并升级/修复 Plugin。
- 开发、门禁或审查中连接意外终止：立即停止新的代码改动和治理调用，向用户返回 `PLUGIN_MCP_DISCONNECTED`，并列出中断阶段、最近成功工具、已知 work item、owner/operationId、最后成功时间和当前写操作的提交状态。若响应未送达，提交状态必须标为 `UNKNOWN`，不能假定失败后盲目重放非幂等写工具。
- 重连恢复：新会话完成初始化和工具注册后，先调用 `workspace_status`，再调用 `graph_frontier` 核对权威状态。已有有效 claim 继续使用原 operation；租约硬过期则消费 `ADVANCE_GRAPH`，由 `WORKER_LOST` 路由回收并生成新 attempt。不得重新 `prepare_hierarchy`/`freeze_hierarchy`，也不得手工清除 claim。
- MCP tool error：直接消费结构化错误代码和详情，不从 stderr、控制器源码或治理文件猜测状态。
- MCP 输入行超限：消费该行唯一一次 `-32600`。若原对象属于支持的 hierarchy/evidence 目标，重新规范序列化后走上述无损暂存；不能重放超大行、截断或摘要替代。
- 宿主不能注册或调用 Plugin MCP：报告外部宿主阻断；不得绕过控制器限制。
