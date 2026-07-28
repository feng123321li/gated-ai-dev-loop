# Layered Delivery

面向 AI Agent 的分层交付治理插件。它把人工评审的开发方案编译成可恢复、可调度、可机械门禁的执行图，并使用 SQLite 保存唯一机器状态。

## 安装

### MCP-first：完整插件

推荐安装完整插件（Plugin）。Codex/Claude 会随插件启动一个本地 stdio MCP Server，并直接获得分层交付工具。运行环境需要 Python 3.10+，且 `python` 可从 PATH 启动；不需要 Node、npm 或第三方 Python 包。安装后需新建会话，Codex 可从桌面 Plugins 或 CLI `/plugins` 启用已发布插件。

### CLI fallback：仅 Skill

宿主暂不支持 Plugin/MCP 时，可只安装 Skill：

```text
npx skills add feng123321li/layered-delivery --skill layered-delivery --global --agent codex --agent claude-code --yes
```

更新 Skill fallback 时重新执行上述命令。

## MCP 架构

- 一个项目会话只启动一个 Python stdio MCP Server，由它提供 37 个窄接口工具；不为每个命令创建独立 Server。
- MCP、CLI 共用同一应用服务、Graph 规则和 SQLite repository；MCP 是首选宿主适配器，`hdg.py` 只在 MCP 不可用时回退。
- 被治理项目根在 Server 生命周期内只绑定一次，不能由普通工具参数改写。Claude 使用 `${CLAUDE_PROJECT_DIR}`；Codex 从宿主注入的 `codex/sandbox-state-meta.sandboxCwd` 绑定当前任务工作区，随后若根不一致会拒绝调用。`root`、维护专用 `dogfood` 和确认布尔值 `confirmed` 均不暴露为工具参数。
- 首次调用用 MCP `workspace_status`（fallback 为 CLI `workspace-status`）区分 `ABSENT`、只有暂存 payload 的 `STAGING_ONLY` 与可从 `graph_frontier` 恢复的 `ACTIVE`，不再把“SQLite 文件存在”误判为已有交付运行。
- MCP 工具使用结构化输入、输出和 tool annotations，宿主可以按工具而不是按任意 Shell 命令配置权限。
- 普通 hierarchy/evidence 直接结构化传输；真实超过 8 MiB 时，可按 1 MiB 以内的文本块无损暂存到 SQLite。Server 自动计算并校验每块和整包的 UTF-8 字节数与 SHA-256，并用 Server 生成的 generation ID 阻止删除/重建后的旧引用复用；随后仍调用原业务工具，继续执行原来的指纹、claim、evidence 和人工确认门禁。Server 不在结果中回显原文，但宿主可能保留工具参数，因此分块不是上下文压缩保证。
- MCP 协议中的工具名是 `graph_frontier` 这类 snake_case 名称。`mcp__plugin_layered-delivery_layered-delivery__graph_frontier` 只是 Claude 的宿主权限名：前两段分别隔离插件和 Server，不进入业务 schema、SQLite 或代码 API。
- Claude Skill 只逐项预批准 32 个中段自治工具（包含 Skill 激活与符合性记录），不使用 Server 级通配符；Codex manifest 对常规工具使用 `approve`，并把方案冻结、重建、取消、人工审查接受和最终用户确认这 5 个敏感工具固定为 `prompt`。Claude Code 低于 2.1.199 时 Server 会拒绝这 5 个工具，避免旧宿主忽略强制交互元数据。payload finalize 只完成校验，不是通用提交入口。用户或组织策略可以进一步收紧。

Server 是随项目会话存在的本地 stdio 进程，不监听端口、不启动后台 worker，也不需要常驻数据库连接。空闲资源主要是一个 Python 进程；请求期间才打开 SQLite。超限暂存限制为单包 64 MiB、每项目 16 个未过期 upload 和 256 MiB 未过期内容；finalize 的内存峰值会随 JSON 大小增长，达到资源边界时明确失败而不提交业务状态。过期内容采用逻辑过期和后续 begin 惰性清理，也可主动 abort。

## 核心契约

- 合法层级只有 `Task`、`Capability → Task`、`Delivery → Capability → Task`。
- 使用满足聚合责任的最浅结构，Task 是唯一执行叶子。
- 人只评审一份根级开发方案，并一次冻结整棵需求树。
- 每个 requirement 都有独立 acceptance；跨需求 acceptance 只能追加集成验收。
- baseline 可用 `requiredSkills` 指定任意合法 Skill catalog 名及其 `DEVELOPMENT`、`GATE`、`FINAL_REVIEW` 阶段；控制器没有硬编码 Skill。根级声明向后代继承。每项 Skill 必须先经 Claude/Codex 原生入口明确调用并写入 Graph 激活凭证，再对实际产物记录结构化符合性检查；Read、load、提示提名和 `skillUsage` 自述都不能替代。成功的开发结果、内部门禁和独立审查要求逐项通过，并在最终验收报告展示真实调用 ID、宿主机制、attempt 和检查结果。
- 冻结后由 Graph 自动选择 Task、计算 Agent 数、执行门禁并处理重试与恢复。
- 用户确认方案并选择 `active` 后，当前冻结契约内的开发、测试、门禁、预算内重试和租约恢复自动推进，不再逐 Task 或逐步骤请求治理确认。
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

1. Claude 必须通过 Skill tool 明确调用每个名称，并以 `CLAUDE_SKILL_TOOL`、tool-use ID、session/executor/execution ID 调用 `record_skill_activation`；Codex 必须通过显式 `$<skill-name>` 原生触发，并以 `CODEX_EXPLICIT_SKILL` 和当前 task/session 调用 ID 记录。同一原生调用 ID 不能覆盖两个 Skill，Read 或加载文件会被拒绝。
2. 完整执行 Skill 后，以 `record_skill_conformance` 绑定非空的实际检查及证据。`IMPLEMENTED`、gate `PASS`、独立审查 `PASS` 要求当前 node attempt 的所有 required Skill 都是 `INVOKED + PASS`。
3. artifact 仍必须提交精确 `skillUsage`，但它只作结果审计，不能替代前两步。`acceptance-report.md` 的“实际 Skill 原生调用与符合性”直接来自 append-only Graph 事件；“实际开发 Skill 调用”和“Skill 使用审计”继续展示 artifact 自述，二者不会混为一谈。

MCP 能机械保证名称、阶段、host 机制、node attempt、operation/owner 绑定、凭证唯一性和结构化检查完整性；任意 Skill 的语义是否真的满足，仍取决于宿主提供真实原生调用身份，以及执行者/审查者对实际代码、diff、测试和产物形成具体检查证据。

## 使用流程

向 Codex 或 Claude 提出需求，并要求使用 `layered-delivery`：

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

## 性能诊断

MCP 不可用或需要直接排查控制器时，CLI fallback 仍保持纯净的 stdout JSON 契约。需要定位本地 `hdg.py` 耗时时，在任一命令上添加全局 `--timing`：

```text
# 在被治理项目根目录运行
python -X utf8 <skill-root>/scripts/hdg.py --timing graph-frontier --item <root-id> --json
```

stderr 会额外输出一行 `HDG_TIMING` JSON，按阶段列出 SQLite 锁等待、提交、投影与文件写入耗时，并报告实际更新或跳过的 registry 行和文件。0.14.0 起，Markdown 投影在 SQLite 提交后执行；高频心跳只刷新 graph/timeline/frontier，相同内容的投影不再重复替换或 `fsync`。

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

本仓库是 `layered-delivery` Plugin 与 Skill 的唯一源码。公司内部 Marketplace
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
