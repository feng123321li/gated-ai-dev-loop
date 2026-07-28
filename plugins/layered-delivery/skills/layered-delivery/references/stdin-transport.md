# MCP 传输与 CLI fallback

## MCP-first 调用契约

完整 Plugin 为 Codex/Claude 启动一个本地 Python stdio MCP Server，并提供 37 个结构化工具。Agent 调用宿主已经发现的工具，不自行启动第二个 Server，不手写 JSON-RPC 行，也不经 Shell 包装 MCP 调用。

- MCP 与 CLI 进入同一应用服务、Graph 规则和 SQLite repository；MCP Server 不派生 `hdg.py` 子进程。
- 宿主在启动 Server 时解析被治理项目根，Server 在整个进程生命周期内固定该路径。工具 schema 不提供 `root` 或 `project_root`，单次调用不能跨项目。
- 维护源码时使用的 `dogfood` 不是模型可选的工具参数；确认由用户事件与对应专用工具表达，工具也不接受通用 `confirmed` 布尔值。
- 工具直接接收 hierarchy、interaction 和 evidence 对象，并返回结构化结果；不创建临时 JSON，不经过 shell 引号和管道。
- Server stdout 只承载 MCP 消息，诊断写入 stderr。宿主负责请求关联、超时、启动失败和 tool 级权限。
- Server 对每条换行分隔消息执行有界读取、严格 UTF-8 解码和大小检查。非法 UTF-8、孤立代理项或非严格 JSON 只使当前行返回一次解析错误；超出 8 MiB 的一行只返回一次 JSON-RPC `-32600`，随后以固定大小块排空到该行换行符或 EOF。失败行不调用工具、不写治理状态，连接仍可继续处理下一条完整消息。

Skill 只能指导 Agent 生成合规输入，不能保证模型永不出错；工具 schema、Server 严格解码和领域控制器才是机械边界。输入被拒绝时应按结构化错误修正并重试，不能假定“已经使用 Skill”即可跳过校验。

## 超限 JSON 的无损暂存

普通 hierarchy/evidence 仍直接传给原工具。只有规范 JSON 连同 MCP 信封确实可能超过 8 MiB 时，才使用同一 Server 的暂存工具；不得为了省上下文默认暂存，也不得截断、摘要或丢弃原始字段：

1. 把完整顶层 JSON 对象切成不超过 1 MiB 的非空文本块并声明块数；不要让模型计算字节数或密码学哈希；
2. 使用不超过 128 字符的安全 `upload_id` 调用 `begin_payload_upload`，把 manifest 绑定到将来真正写业务状态的 `target_tool`。保存 Server 返回的 32 字符 `generationId`；同一有效 manifest 的相同 begin 可幂等重试；
3. 按从 0 开始的索引调用 `append_payload_chunk`，每次同时提交 `upload_id` 和 `generation_id`。Server 自动计算每块的 UTF-8 字节数和 SHA-256；同一代的相同块可安全重试，冲突内容会被拒绝；
4. 使用同一对 ID 调用 `finalize_payload_upload`。Server 逐块验证连续性、字节数、哈希、严格 JSON、重复键、非有限数字、Unicode 和结构上限，只返回计数、状态及紧凑 `payloadRef`，不回传原文或顶层键名；
5. 把 `{"payloadRef":{"uploadId":"...","generationId":"...","sha256":"...","sizeBytes":123}}` 作为原 `hierarchy` 或 `evidence` 参数调用原业务工具。原工具流式重验暂存块、代际、摘要、长度和目标绑定后，才在既有事务与权限门禁下写业务状态。

暂存单包上限 64 MiB、单块上限 1 MiB、最多 1024 块；每个项目同时最多 16 个未过期 upload，未过期暂存内容合计最多 256 MiB。manifest 一小时后逻辑过期；过期内容在后续 begin 时惰性清理，也可使用带同一 `generation_id` 的 `abort_payload_upload` 主动删除。SQLite 连接启用 `secure_delete`，但文件空间回收仍由 SQLite/文件系统决定，不能把它宣称为介质级安全擦除。`payload_upload_status` 也要求 generation，只返回进度、缺块计数和 READY 引用。upload ID 被删除、过期并重建后会得到新 generation，旧调用和旧引用不能命中新内容。

暂存写入同一 SQLite，但不增加 domain revision、不生成投影，也不代表 prepare/result/gate/review/confirmation 已执行。特别是最终确认等敏感目标仍必须调用对应原工具并经过宿主授权；不存在通用 `commit_payload` 绕过入口。

分块解决的是单条 MCP 消息上限、Shell 引号/换行转义和传输重试，不是宿主上下文压缩协议。Server 不在结果中回显 chunk 或完整 payload，但 Codex/Claude 等宿主仍可能把工具参数保留在任务历史；如果目标是减少模型上下文，应使用宿主提供的文件/resource 按需读取或上下文压缩能力，不能靠 payload 暂存作保证。

## CLI fallback 的宿主无关调用契约

只有 MCP 未安装、未连接或不可用时，所有宿主才使用同一个 CLI 逻辑入口：

```text
python -X utf8 <skill-root>/scripts/hdg.py <command> ... --json
```

- 从当前 Skill 元数据解析 `<skill-root>`，也就是当前已加载 `SKILL.md` 所在目录；不得根据用户名、用户主目录、`.claude`、`.codex` 或操作系统猜测安装位置。
- `<skill-root>` 只用于文档和协议表达。执行适配器在启动进程时把它解析为当前安装路径，但不得把该本机绝对路径写入 `handoffCommand`、冻结方案、治理数据库或可移植文档。
- 控制器始终从被治理项目的根目录运行，使 `.layered-delivery/governance.sqlite3` 由项目上下文定位，而不是由 Skill 安装位置定位。
- 路径解析与 stdin 连接由宿主适配器负责；治理协议不按操作系统分叉。

## CLI fallback 的只读 JSON 输出

`workspace-status`、`graph-status`、`graph-frontier`、`graph-events`、`graph-replay`、`ready-tasks`、`task-context` 和其他只读查询把 JSON 写到 stdout。调用方必须直接消费 stdout，不得使用临时 JSON 中转只读查询结果。MCP 的 `graph_events` 与 `interaction_log` 必须提供 `after_event_id` 和 `limit`（1–200）：首屏 cursor 为 0，`hasMore=true` 时把 `nextCursor` 用于下一页；Server 只保留当前页。CLI fallback 的旧日志命令仍输出完整列表，诊断大型历史时优先使用 MCP 分页。

manual receiving Agent 的恢复入口是 `graph-frontier`，不是 `task-context`。`task-context` 只提供未认领 Task 的诊断预览，不会 claim Task，也不授权开发。Graph 返回 `DISPATCH_TASK` 后，实际 worker 先逐项原生调用 required Skill，并通过 `record-skill-activation --activation -` 从 stdin 绑定当前 owner/operation；全部 activation 合格后才调用 `dispatch-task` 获取正式上下文。阶段结束前使用 `record-skill-conformance --conformance -` 绑定实际检查。

控制器非零退出时，调用方必须保留 stderr、停止 JSON 解析并报告控制器错误。不得继续把空 stdout 或 stderr 文本交给下游 `json.load`；否则 `JSONDecodeError` 会遮蔽真正的控制器失败。若宿主需要二次解析，必须先确认生产者进程成功，再解析完整 stdout。

## CLI fallback 的结构化 stdin

`prepare-hierarchy --definition -`、`record-interaction --interaction -`、`record-skill-activation --activation -`、`record-skill-conformance --conformance -` 和所有 `--evidence -` 命令只从标准输入读取 JSON。控制器拒绝文件路径，因此 Agent 不得创建 `_hdg_definition.json`、`.hdg-tmp/**`、系统临时 JSON 或其他中间文件。

JSON 必须直接进入控制器进程。不要使用 `echo`、命令替换或把 JSON 拼进多层 shell 引号；这些方式会改变反斜杠、换行、美元符号或引号，并可能在控制器启动前失败。

## CLI fallback 按 shell 能力适配

以下只是 stdin 连接方式，不是操作系统分支。宿主选择当前 shell 原生支持的一种方式，且必须使用运行时已经解析出的 Skill 根目录替换 `<resolved-skill-root>`。

### 支持带引号 heredoc 的 shell

直接在当前 shell 调用控制器，不再嵌套 `bash -lc`、`sh -c`、`cmd /c` 或另一个 shell 包装：

```bash
python -X utf8 "<resolved-skill-root>/scripts/hdg.py" prepare-hierarchy --definition - --host-runtime <agent> --json <<'HDG_DEFINITION'
{"schemaVersion":3,"root":{"definition":{"...":"完整根节点定义"},"children":[]}}
HDG_DEFINITION
```

- `<<'HDG_DEFINITION'` 的分隔符必须带单引号，禁止 shell 展开 JSON。
- JSON 从下一行开始；结束标记必须单独占一行且顶格书写。
- heredoc 失败说明 shell 尚未启动控制器；修正当前调用，不得改用临时文件。

### 支持单引号 here-string 的 shell

使用当前 shell 的 here-string 直接连接控制器 stdin：

```powershell
$hdgDefinition = @'
{"schemaVersion":3,"root":{"definition":{"...":"完整根节点定义"},"children":[]}}
'@
$hdgDefinition | python -X utf8 "<resolved-skill-root>/scripts/hdg.py" prepare-hierarchy --definition - --host-runtime <agent> --json
```

结束标记 `'@` 必须单独占一行并顶格书写。不要先把 here-string 写入文件。

interaction 和 evidence 使用相同机制，只替换命令参数与输入内容。

## 失败处理

- MCP Server 未安装、未连接或启动失败：报告 MCP 状态并进入上述 CLI fallback；不能在 MCP 正常可用时绕过 tool 级权限改走 Shell。
- MCP tool error：直接消费结构化错误代码和详情，不从 stderr 或控制器源码猜测状态。
- MCP 输入行超限：消费该行唯一一次 `-32600`；Server 已排空本行，发送方可继续下一条请求。若原对象属于支持的 hierarchy/evidence 目标，重新规范序列化后走上述无损暂存；不能重放超大行、截断或摘要替代。若超大行在 EOF 结束，Server 报错一次后正常退出。
- `*_STDIN_REQUIRED`：调用方传入了路径；改用 `-` 和当前 shell 的直接 stdin 形式。
- `*_READ`：stdin 读取失败；检查宿主进程和管道，不创建中间文件。
- `*_PARSE`：控制器已收到内容但 JSON 无效；修正 JSON 后重新通过 stdin 提交。
- 只读查询非零退出：保留 stderr 并停止，不得解析空 stdout。
- 宿主工具确实无法直接连接 stdin 或可靠读取 stdout/stderr 时，报告传输阻断；不得绕过控制器限制或污染业务仓库。
