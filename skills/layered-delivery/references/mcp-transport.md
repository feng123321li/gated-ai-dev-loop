# Plugin MCP-only 传输

只在 payload 确实超限或 MCP 连接异常时读取。正常 hierarchy/evidence 直接传给原工具。

## 超限 JSON 的无损暂存

只有规范 JSON 连同 MCP 信封确实可能超过 8 MiB 时，才使用同一 Server 的暂存工具；暂存解决消息上限，不解决上下文成本，不得默认使用、截断或摘要：

1. 把完整顶层 JSON 对象切成不超过 1 MiB 的非空文本块；长度和哈希交给 Server 计算。
2. `begin_payload_upload` 绑定 `target_tool` 和目标参数，保存返回的 `generationId`。
3. 用相同 generation 逐块 `append_payload_chunk`，再 `finalize_payload_upload`。
4. 调用原业务工具，把大对象替换为 finalize 原样返回的 `{"payloadRef":{"uploadId":"...","generationId":"...","sha256":"...","sizeBytes":123}}`。

finalize 只完成传输，不执行业务动作；原工具仍执行全部契约、指纹、claim、审查和确认校验。

## 连接失败

- 未安装、未连接或工具注册失败：报告 `PLUGIN_MCP_UNAVAILABLE` 并停止，不得启动 `hdg.py`、第二个 Server、Shell 包装或直接 SQLite 写入。
- 运行中断连：报告 `PLUGIN_MCP_DISCONNECTED`，注明阶段、work item、operation 和最后成功动作；响应未返回的写操作状态标为 `UNKNOWN`。
- 重连后先 `workspace_status`，再 `graph_frontier`。不得重新 prepare/freeze、手工清 claim 或盲目重放提交状态未知的非幂等写。
- MCP tool error 直接按结构化错误修正；不从 stderr、源码、memory 或 Markdown 猜状态。
