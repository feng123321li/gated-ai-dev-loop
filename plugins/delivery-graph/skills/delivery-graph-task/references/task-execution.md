# TASK 执行说明

## 目录

1. [身份与 claim](#身份与-claim)
2. [项目访问边界](#项目访问边界)
3. [实现循环](#实现循环)
4. [验证证据](#验证证据)
5. [终态选择](#终态选择)

## 身份与 claim

外层 receiver 是唯一控制面主体。AUTO receiver 提交 assignment 的 reservation 和 decision fingerprint；MANUAL receiver 省略 reservation。两者都必须提供自己的 `receiver_context_id`、新的 `operation_id`、可信 Adapter 和明确节点。

claim 成功后立即用相同 operation 首次 heartbeat，早于 Loop context 解读、代码检查、文件检索、依赖分析和任何命令；此后所有 heartbeat、progress、pause、resume 和 result 都携带该 operation。首次 `leaseRenewed=false / NOT_REQUIRED` 不会撤销原 `leaseExpiresAt`，也不终止后续每约 60 秒的 heartbeat。内部 Worker 可以协助分析、编码或测试，但不得看到或使用 reservation、operation、MCP 凭据，也不得调用 Loop 生命周期工具；primary dispatcher 同样不得冒充 receiver 代发 heartbeat。

## 项目访问边界

`loop_context.projectScopes` 是运行时授权边界：

- 至少一个 scope 必须已验证；为空时停止开发并报告。
- 使用每个 scope 返回的实际 workspace 路径，不从模型输入路径或 primary checkout 推断。
- 只修改 `READ_WRITE` scope；`READ_ONLY` 只能检查。
- 不创建、切换、checkout 或重绑定 Git 分支；所有 TASK 共享冻结的 Delivery 分支。
- 不把 `resourceClaims` 当文件权限。它是跨 Delivery 调度锁，文件范围仍由 projectScopes 和 TASK 目标共同约束。

## 实现循环

1. 阅读冻结 TASK 方向、显式约束、已知验收、上游输入和外部契约。
2. 检查真实实现、调用链、数据流和测试，确认根因与影响边界。
3. 形成可调整的内部计划。冻结 payload 不要求指定普通文件、类、内部方法或测试组织；这些由 TASK 决定。
4. 适用且可用时触发 `skillHints`，但不得把 Skill 默认示例提升为需求事实。
5. 实施最小完整变更。实现、生成、测试或静态审查发现的 actionable 问题留在本 Loop 内修复和复验。
6. 如果需要改变冻结的外部契约、拓扑、依赖、资源、项目 scope 或数据库 after 设计，停止扩展并提交 `REPLAN_REQUIRED`。

所有 receiver 都在 claim 后立即 heartbeat，并从 claim 到 result/claim release 持续按 `heartbeatDirective` 约每 60 秒 heartbeat；代码检查、文件检索、依赖分析、编辑、测试、rework 和最终验证没有“非租约阶段”。`STANDARD` 在这些真实里程碑另行报告 progress；`LIGHT` 可减少 progress，但不能省略 heartbeat。每个可能阻塞的单次操作都先估时，不只是命令。整文件 Write、大 patch 或批量编辑优先拆成可审查的语义小 patch，并在块间 heartbeat；无法拆分且预计超过 60 秒时，先用 `heartbeat_loop(expected_command_seconds=...)` 申请覆盖整个原子 tool call 及收尾的有界租约。长命令还应优先缩小范围（单模块、指定测试类、离线依赖解析）；按 `pom.xml/.mvn/mvnw`、Gradle wrapper、package lockfile、`pyproject.toml`、`go.mod` 或 `Cargo.toml` 选择专用命令 worker。首次依赖预热、install 或预计超过 60 秒的命令，先申请最多 1800 秒并带 120 秒收尾缓冲的有界租约，再交给不持有 reservation/operation/MCP 凭据的内部 worker 或独立监控；外层 receiver 继续按 60 秒心跳并报告 `QUEUED/STARTED/FINISHED_OR_FAILED`。progress 与 heartbeat 独立，永不续租或改变 heartbeat 截止。

## 验证证据

先声明 `affectedScopes`，再选择覆盖它的最小验证组合：

- `paths` 为仓库相对字面量路径，并包含被改实现、相关依赖、公共契约与必要测试锚点。
- 命令证据记录实际命令摘要、执行 workspace、覆盖 scope、退出结果和关键输出摘要。
- 没有运行的测试不得写成 PASSED；因环境无法运行时如实记录 gap。
- 测试通过但覆盖不到影响边界不算充分；补充契约检查、构建、静态分析或定向手工验证。
- 数据库 TASK 只验证冻结 `databaseChanges[*].after` 与迁移政策，不重新设计字段、索引或约束。

Controller 会在 `record_loop_result` 时捕获可信 workspace 的基线、HEAD、状态指纹和变更文件清单；不把源码 diff 写入 Graph，也不生成 `workspace-changes.patch`。后续 Review 通过授权 workspace 按需读取代码，并用状态/范围指纹判断证据新鲜度。它不替代 receiver 对证据充分性的判断。

## 终态选择

- `SUCCEEDED`：冻结 TASK 方向满足，必要变更完成，影响范围和验证证据真实可审计。
- `FAILED`：在当前 attempt 内完成诊断和合理 rework 后仍不满足，但不需要修改冻结 Graph。
- `BLOCKED`：存在具体外部条件，当前权限和 scope 内没有可行路径。普通测试失败或 Review finding 不是自动 BLOCKED。
- `REPLAN_REQUIRED`：必须改变冻结拓扑、依赖、资源、project scope、外部契约或 databaseChanges。

提交 `record_loop_result` 后停止本 receiver，不继续读取 frontier或领取下一节点。
