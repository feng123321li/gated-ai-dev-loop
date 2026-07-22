# 控制器调用与结构化数据传输

## 宿主无关调用契约

所有宿主都使用同一个逻辑入口：

```text
python -X utf8 <skill-root>/scripts/hdg.py <command> ... --json
```

- 从当前 Skill 元数据解析 `<skill-root>`，也就是当前已加载 `SKILL.md` 所在目录；不得根据用户名、用户主目录、`.claude`、`.codex` 或操作系统猜测安装位置。
- `<skill-root>` 只用于文档和协议表达。执行适配器在启动进程时把它解析为当前安装路径，但不得把该本机绝对路径写入 `handoffCommand`、冻结方案、治理数据库或可移植文档。
- 控制器始终从被治理项目的根目录运行，使 `.layered-delivery/governance.sqlite3` 由项目上下文定位，而不是由 Skill 安装位置定位。
- 路径解析与 stdin 连接由宿主适配器负责；治理协议不按操作系统分叉。

## 只读 JSON 输出

`graph-status`、`graph-frontier`、`graph-events`、`graph-replay`、`ready-tasks`、`task-context` 和其他只读查询把 JSON 写到 stdout。调用方必须直接消费 stdout，不得使用临时 JSON 中转只读查询结果。

manual receiving Agent 的恢复入口是 `graph-frontier`，不是 `task-context`。`task-context` 只提供未认领 Task 的诊断预览，不会 claim Task，也不授权开发；Graph 返回 `DISPATCH_TASK` 后应调用 `dispatch-task` 获取正式上下文。

控制器非零退出时，调用方必须保留 stderr、停止 JSON 解析并报告控制器错误。不得继续把空 stdout 或 stderr 文本交给下游 `json.load`；否则 `JSONDecodeError` 会遮蔽真正的控制器失败。若宿主需要二次解析，必须先确认生产者进程成功，再解析完整 stdout。

## 结构化 stdin

`prepare-hierarchy --definition -`、`record-interaction --interaction -` 和所有 `--evidence -` 命令只从标准输入读取 JSON。控制器拒绝文件路径，因此 Agent 不得创建 `_hdg_definition.json`、`.hdg-tmp/**`、系统临时 JSON 或其他中间文件。

JSON 必须直接进入控制器进程。不要使用 `echo`、命令替换或把 JSON 拼进多层 shell 引号；这些方式会改变反斜杠、换行、美元符号或引号，并可能在控制器启动前失败。

## 按 shell 能力适配

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

- `*_STDIN_REQUIRED`：调用方传入了路径；改用 `-` 和当前 shell 的直接 stdin 形式。
- `*_READ`：stdin 读取失败；检查宿主进程和管道，不创建中间文件。
- `*_PARSE`：控制器已收到内容但 JSON 无效；修正 JSON 后重新通过 stdin 提交。
- 只读查询非零退出：保留 stderr 并停止，不得解析空 stdout。
- 宿主工具确实无法直接连接 stdin 或可靠读取 stdout/stderr 时，报告传输阻断；不得绕过控制器限制或污染业务仓库。
