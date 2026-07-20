# 结构化输入的 stdin 传输

## 强制规则

`prepare-hierarchy --definition -`、`record-interaction --interaction -` 和所有 `--evidence -` 命令只从标准输入读取 JSON。控制器拒绝文件路径，因此 Agent 不得创建 `_hdg_definition.json`、`.hdg-tmp/**`、系统临时 JSON 或其他中间文件。

JSON 必须直接进入控制器进程。不要使用 `echo`、命令替换或把 JSON拼进多层 shell 引号；这些方式会改变反斜杠、换行、美元符号或引号，并可能在控制器启动前失败。

## Claude Code Bash

Claude Code 的 Bash 工具已经提供当前 shell。直接调用控制器，不要再包一层 `bash -lc`、`sh -c`、`cmd /c` 或 `powershell -Command`。

```bash
python -X utf8 "/absolute/skill/root/scripts/hdg.py" prepare-hierarchy --definition - --host-runtime claude-code --json <<'HDG_DEFINITION'
{"schemaVersion":3,"root":{"definition":{"...":"完整根节点定义"},"children":[]}}
HDG_DEFINITION
```

- `<<'HDG_DEFINITION'` 的分隔符必须带单引号，禁止 shell 展开 JSON。
- JSON 从下一行开始；结束标记必须单独占一行且顶格书写。
- 不要把整条命令再放进字符串参数或另一层引号。
- heredoc 语法失败说明 shell 尚未启动控制器；修正命令并重新执行，不得改用 Write 工具落盘。

interaction 和 evidence 使用相同方式，只替换命令参数与分隔符名称。

## PowerShell

在 PowerShell 中使用单引号 here-string，直接通过管道送入控制器：

```powershell
$hdgDefinition = @'
{"schemaVersion":3,"root":{"definition":{"...":"完整根节点定义"},"children":[]}}
'@
$hdgDefinition | python -X utf8 "C:\absolute\skill\root\scripts\hdg.py" prepare-hierarchy --definition - --host-runtime codex --json
```

结束标记 `'@` 必须单独占一行并顶格书写。不要先把 here-string 写入文件。

## 失败处理

- `*_STDIN_REQUIRED`：调用方传入了路径；改用 `-` 和当前 shell 的直接 stdin 形式。
- `*_READ`：stdin 读取失败；检查宿主进程和管道，不创建中间文件。
- `*_PARSE`：控制器已收到内容但 JSON 无效；修正 JSON 后重新通过 stdin 提交。
- 宿主工具确实无法传递 stdin 时，报告输入传输阻断；不得绕过控制器限制或污染业务仓库。
