# Layered Delivery

面向 AI Agent 的分层交付治理插件。它把人工评审的开发方案编译成可恢复、可调度、可机械门禁的执行图，并使用 SQLite 保存唯一机器状态。

## 安装

### Plugin-only

本项目只以完整 Plugin 交付。Codex、Claude Code 或其他兼容 MCP Plugin 的 Agent 宿主随插件启动本地 stdio MCP Server，并直接获得分层交付工具；不再提供 Skill-only 或 CLI 降级入口。运行环境需要 Python 3.10+，且 `python` 可从 PATH 启动；不需要 Node、npm 或第三方 Python 包。

仓库内置 Codex 与 Claude Code 两份宿主清单。Cursor 或其他 Agent 若不兼容其中任一 Plugin 格式，需要提供自己的 MCP 启动、项目根绑定、敏感工具人工确认和原生 Skill 调用适配；接入后可以规划新需求或接续同一 frozen graph，但不能仅靠读取 Skill 文档冒充完整接入。

从组织或公开 Marketplace 安装/更新 `layered-delivery` Plugin 后，必须新建 Agent 会话，让宿主重新加载 Skill 和 MCP Server。任意 Agent 在开发、认领 Task 或恢复 frozen graph 前都必须完成 MCP 初始化握手并成功注册工具。MCP 未连接时禁止开始或恢复治理写入，也不得先编辑业务代码；先修复 Plugin 安装、版本、Python PATH 或工具 schema 注册问题。

## MCP 架构

- 一个项目会话只启动一个 Python stdio MCP Server，由它提供 37 个窄接口工具；不为每个命令创建独立 Server。
- Claude Code 从 Plugin 根目录 `.mcp.json` 自动发现并启动 Server，不需要另行执行 `claude mcp add`；Codex 使用 `.codex-plugin/plugin.json` 中的 `mcpServers` 等价声明。两者都运行 Plugin 内的 `skills/layered-delivery/scripts/hdg_mcp.py`。
- MCP 直接进入统一应用服务、Graph 规则和 SQLite repository；Plugin 载荷不包含 CLI 控制器或独立 Python 模块入口。
- 被治理项目根在 Server 生命周期内只绑定一次，不能由普通工具参数改写。Claude 使用 `${CLAUDE_PROJECT_DIR}`；Codex 从宿主注入的 `codex/sandbox-state-meta.sandboxCwd` 绑定当前任务工作区，随后若根不一致会拒绝调用。MCP 同时从当前连接识别实际执行宿主；它可以不同于创建冻结方案的宿主。`root`、维护专用 `dogfood` 和确认布尔值 `confirmed` 均不暴露为工具参数。
- 首次调用用 MCP `workspace_status` 区分 `ABSENT`、只有暂存 payload 的 `STAGING_ONLY` 与可从 `graph_frontier` 恢复的 `ACTIVE`，不再把“SQLite 文件存在”误判为已有交付运行。
- MCP 工具使用结构化输入、输出和 tool annotations，宿主可以按工具而不是按任意 Shell 命令配置权限。
- 普通 hierarchy/evidence 直接结构化传输；真实超过 8 MiB 时，可按 1 MiB 以内的文本块无损暂存到 SQLite。Server 自动计算并校验每块和整包的 UTF-8 字节数与 SHA-256，并用 Server 生成的 generation ID 阻止删除/重建后的旧引用复用；随后仍调用原业务工具，继续执行原来的指纹、claim、evidence 和人工确认门禁。Server 不在结果中回显原文，但宿主可能保留工具参数，因此分块不是上下文压缩保证。
- MCP 协议中的工具名是 `graph_frontier` 这类 snake_case 名称。`mcp__plugin_layered-delivery_layered-delivery__graph_frontier` 只是 Claude 的宿主权限名：前两段分别隔离插件和 Server，不进入业务 schema、SQLite 或代码 API。
- Claude Skill 使用当前 MCP Server 的 `allowed-tools` 通配符。用户评审方案并选择 `active` 或 `manual` 的回复，就是该指纹方案的一次冻结确认；Agent 必须紧邻这次选择调用 `freeze_hierarchy`，不得再触发第二个工具批准弹窗。Plugin `PreToolUse` Hook 仅将 Graph 重建、Graph 取消、人工审查接受和最终用户确认这 4 个独立敏感动作强制降级为 `ask`；Codex manifest 对常规工具使用 `approve`，并把同一组 4 个工具固定为 `prompt`。Claude Code 低于 2.1.199 时 Server 仍会拒绝这 4 个工具，避免旧宿主忽略强制交互元数据。payload finalize 只完成校验，不是通用提交入口。用户或组织策略可以进一步收紧。

Server 是随项目会话存在的本地 stdio 进程，不监听端口、不启动后台 worker，也不需要常驻数据库连接。空闲资源主要是一个 Python 进程；请求期间才打开 SQLite。超限暂存限制为单包 64 MiB、每项目 16 个未过期 upload 和 256 MiB 未过期内容；finalize 的内存峰值会随 JSON 大小增长，达到资源边界时明确失败而不提交业务状态。过期内容采用逻辑过期和后续 begin 惰性清理，也可主动 abort。

## 核心契约

- 合法层级只有 `Task`、`Capability → Task`、`Delivery → Capability → Task`。
- 使用满足聚合责任的最浅结构，Task 是唯一执行叶子。
- 人只评审一份根级开发方案，并一次冻结整棵需求树。
- 每个 requirement 都有独立 acceptance；跨需求 acceptance 只能追加集成验收。
- baseline 可用 `requiredSkills` 指定任意合法 Skill catalog 名及其 `DEVELOPMENT`、`GATE`、`FINAL_REVIEW` 阶段；控制器没有硬编码 Skill。根级声明向后代继承。用户批准整树并选择 active/manual 时已经一次授权这些 Skill；执行适配器在实际阶段自动经当前 Agent 宿主的原生 Skill 入口调用，以统一 `HOST_NATIVE_SKILL` 写入 Graph 激活凭证，再对实际产物记录结构化符合性检查，不得要求用户二次确认或输入 `$skill`。Read、load、提示提名和 `skillUsage` 自述都不能单独替代。成功的开发结果、内部门禁和独立审查要求逐项通过，并在最终验收报告展示真实调用 ID、宿主机制、attempt 和检查结果。
- 冻结后由 Graph 自动选择 Task、计算 Agent 数、执行门禁并处理重试与恢复。
- 用户确认方案并选择 `active` 后，当前冻结契约内的 Skill 调用、开发、测试、门禁、预算内重试和租约恢复自动推进，不再逐 Skill、逐 Task 或逐步骤请求治理确认；manual 接收会话也在一次需求交接后遵循同一规则。
- `.layered-delivery/governance.sqlite3` 是唯一机器权威，Markdown 只是可重建投影。
- 最终 `USER_CONFIRMED` 仍由用户给出；提交、推送、合并、迁移、发布和新增外部权限也仍需单独授权。

## 在 Baseline 中指定 Skill

每个工作项 definition 都提供 `requiredSkills`，无要求时写空数组。根节点声明会应用到整个子树；子节点只能追加，不能取消祖先要求：

```json
{
  "requiredSkills": [
    {
      "name": "tdd-workflow",
      "stages": ["DEVELOPMENT", "GATE"],
      "purpose": "完整执行测试先行、最小实现、重构和复测，并在内部门禁说明实际应用。"
    },
    {
      "name": "source-command-python-review",
      "stages": ["FINAL_REVIEW"],
      "purpose": "使用完整的独立 Python 审查流程形成最终审查证据。"
    }
  ]
}
```

`requiredSkills` 可以省略或显式写为 `[]`，两者都会规范化为空数组且不触发 Skill 门禁。非空时，`name` 是需求指定的任意合法 Skill catalog 名，不带 `/` 或 `$`；控制器只做精确集合匹配，不维护 `tdd-workflow`、`erp-dubbo-api-generator` 等白名单。`FINAL_REVIEW` 只在需求根声明；根级 `DEVELOPMENT/GATE` 要求作用于整棵子树。控制器把要求带入 frontier、Task context 和 evidence contract：

1. 冻结的 `requiredSkills` 和 active/manual 方式已经构成执行授权。当前阶段执行适配器必须通过当前 Agent 宿主的原生 Skill 入口自动调用每个名称，并以统一 `HOST_NATIVE_SKILL`、当前 session/executor/execution ID 和互不复用的原生调用 ID 调用 `record_skill_activation`。不得要求用户再次输入 `$<skill-name>`、确认 Skill 或复制触发文本。同一原生调用 ID 不能覆盖两个 Skill，单独 Read 或加载文件会被拒绝。Graph 记录当前实际执行宿主，而不是方案创建宿主。
2. 完整执行 Skill 后，由同一执行宿主以 `record_skill_conformance` 绑定非空的实际检查及证据。`IMPLEMENTED`、gate `PASS`、独立审查 `PASS` 要求当前 node attempt 的所有 required Skill 都是 `INVOKED + PASS`。
3. artifact 仍必须提交精确 `skillUsage`，但它只作结果审计，不能替代前两步。`acceptance-report.md` 的“实际 Skill 原生调用与符合性”直接来自 append-only Graph 事件；“实际开发 Skill 调用”和“Skill 使用审计”继续展示 artifact 自述，二者不会混为一谈。

MCP 能机械保证名称、阶段、host 机制、node attempt、operation/owner 绑定、凭证唯一性和结构化检查完整性；任意 Skill 的语义是否真的满足，仍取决于宿主提供真实原生调用身份，以及执行者/审查者对实际代码、diff、测试和产物形成具体检查证据。标准 MCP `clientInfo.name` 与 `nativeInvocationId` 是宿主上报的会话凭证，并非密码学证明；要进一步防止恶意客户端伪报，需要对应 Agent 提供可验证的原生调用回调或签名适配器。Cursor 等新客户端因此需要配置本 MCP，并由宿主适配器真正调用 catalog Skill；若没有原生 Skill 入口，必须记录 `BLOCKED`，不能把 Read/load 伪装成 `HOST_NATIVE_SKILL`。

方案创建宿主只作审计，不是执行约束。Claude、Codex、Cursor 或其他同时具备 Plugin MCP 和原生 Skill 入口的 Agent，都可以自动开发自己冻结的需求或接收另一 Agent 冻结的需求。Plugin MCP 以当前连接的 `clientInfo.name`（Codex 还可使用 sandbox metadata）形成实际宿主标识。接收宿主直接从 `graph_frontier` 恢复同一运行，不因宿主变化重新 prepare、freeze，不要求用户再次确认方案或 required Skill。

## 使用流程

向任意已接入 `layered-delivery` Plugin MCP 的 Agent 提出需求，并要求使用该 Plugin：

```text
使用 layered-delivery 规划并治理当前开发需求。
```

工作流：

1. Agent 选择最浅合法层级并生成根级开发方案。
2. 用户评审方案，选择 `active` 或 `manual`。
3. Agent 一次冻结整树。
4. `active` 由当前会话自动调度；`manual` 在当前窗口确认并冻结后生成一次性交接，新运行窗口从同一 graph run 自动恢复，不重新准备、冻结或逐 Task 确认。
5. Graph 驱动实现、回归、修正、分级门禁和恢复。
6. 两种方式都自动推进到 `WAITING_FOR_USER_CONFIRMATION`，frontier 停在 `REQUEST_USER_CONFIRMATION`；用户明确验收后才写入 `USER_CONFIRMED` 并完成需求。

宿主首次信任 MCP Server、确认冻结方案，或配置代码编辑/测试所需 Auto 权限可能需要用户操作。manual 冻结后，新窗口从交接启动即可自动消费同一 graph run；冻结范围内的常规开发、测试、修复、门禁和预算内恢复不重复询问。到达最终验收阶段后，人工审查接受（需要时）与最终 `USER_CONFIRMED` 必须到达人；外部 Git/发布动作和真实不可恢复阻断仍按边界返回用户。

## 关键产物

| 文件 | 用途 |
|---|---|
| `development-plan.md` | 冻结前评审完整开发方案 |
| `execution-graph.md` | 查看执行图和治理图 |
| `frontier.md` | 查看下一步、关键路径和阻断原因 |
| `development-review.md` | 对照计划检查实际开发结果 |
| `acceptance-report.md` | 查看门禁证据和验收结论 |
| `run-timeline.md` | 查看 attempt、失败与恢复记录 |

## MCP 启动诊断

Plugin 更新后需重启 Agent 会话。Claude Code 可用 `claude plugin list --json` 核对已启用版本，并用 `claude mcp list` 核对连接；`Connected · tools fetch failed` 表示 Server 进程已经连接，但宿主拒绝了工具 schema，不是 Python 进程未启动。Codex 应在 Plugin 管理界面或当前任务的可用工具中确认 Server 和工具均已注册。任一宿主未成功注册工具时都必须停止，不得通过 Shell、直接 Python API 或 SQLite 绕过门禁。

开发中 MCP 意外断开时，Agent 必须停止新的代码和治理写入，并返回 `PLUGIN_MCP_DISCONNECTED`，说明中断阶段、最近成功工具、work item/operationId 与已知提交状态。响应未送达时提交状态记为 `UNKNOWN`；重连后先调用 `workspace_status` 和 `graph_frontier` 核对 SQLite 权威状态，再继续有效 claim 或按 `ADVANCE_GRAPH/WORKER_LOST` 恢复，不能盲目重放写工具、重新冻结或改走 CLI。

## 仓库维护

修改控制器后重新构建插件载荷：

```text
python scripts/build_skill.py
```

完整验证：

```text
python scripts/build_skill.py
python <plugin-creator>/scripts/validate_plugin.py plugins/layered-delivery
claude plugin validate plugins/layered-delivery
python -m unittest discover -s tests -t . -v
python -m compileall -q src scripts tests
python -X utf8 <skill-validator>/quick_validate.py skills/layered-delivery
git diff --check
```

本仓库是 `layered-delivery` Plugin 的唯一源码；其中 Skill 只作为 Plugin 的调用说明载荷，不是独立安装产品。公司内部 Marketplace
只维护指向 `plugins/layered-delivery` 的 Git 版本映射，不复制本仓库的插件载荷。

源码维护不创建 `.layered-delivery/**` 运行包。只有明确要求 dogfood 时，控制面写命令才可执行并携带 `--dogfood`。

## 详细文档

- [版本更新记录](CHANGELOG.md)
- [Skill 入口](skills/layered-delivery/SKILL.md)
- [完整工作流](skills/layered-delivery/references/workflow.md)
- [Graph Engineering](skills/layered-delivery/references/graph-engineering.md)
- [开发方案字段](skills/layered-delivery/references/development-plan.md)
- [验收与最终确认](skills/layered-delivery/references/acceptance.md)
- [SQLite 状态与恢复](skills/layered-delivery/references/task-registry.md)
