# 5 分钟 LIGHT Quickstart

这条路径面向一个项目、一个根 TASK、局部低风险且有明确定向验证的短任务。目标是只保留基线冻结、真实终态验收和轻量工作区变更索引，不创建 GROUP 或独立 Review receiver；receiver 仍执行统一的租约 heartbeat 协议。

## 0:00—1:00 检查注册

先看宿主 MCP 列表：三个 Profile 的联集应为 33 个工具，其中包括 `workspace_status`、`preview_hierarchy`、`confirm_development_baseline`、`freeze_hierarchy`、`record_loop_result` 和 `close_delivery`。stdio server 显示 `Auth: Unsupported` 是正常的，它不使用 HTTP/OAuth。

若工具未注册，报告 `PLUGIN_MCP_UNAVAILABLE` 并停止治理写入；不要尝试模拟 `workspace_status`，也不要读写 `scheduler.db`。需要跨会话证据时运行只读矩阵 Demo：

```text
python scripts/mcp_registration_probe.py --host zcode --strict
```

## 1:00—2:00 确认 LIGHT 是用户输入

系统默认使用 `STANDARD`，不再让 Agent 根据风险、改动规模或模型判断自动推荐档位。
只有用户明确要求 `LIGHT`，且本次交付能够建模为一个根 TASK、零 GROUP、零 Review
时才继续本路径；否则使用 `STANDARD`。

## 2:00—3:00 建模并确认基线

调用 `hierarchy_contract(root_kind=TASK)`，建立一个根 TASK：

- `delivery.assuranceProfile=LIGHT`；
- Delivery 和 TASK 的 `reviewLoop=null`；
- 不创建 GROUP；
- payload 写清目标、边界、验收点和唯一的定向验证命令。

调用 `preview_hierarchy`。Git 项目先按 Controller 返回的 `DEVELOPMENT_BASELINE` 确认基线，再选择 `AUTOMATIC` 或 `MANUAL`；Controller 只读冻结 binding，不替用户执行 commit、push 或发布。

## 3:00—4:30 执行唯一 TASK

AUTOMATIC 由 primary 调用 `plan_dispatch_batch` 并启动一个独立 TASK receiver；receiver 用 assignment 调用 `dispatch_loop`，立即调用首次 `heartbeat_loop`，再解读 claim 已返回的 Loop context，只在验证过的项目 scope 内修改。

所有 claim 都必须立即 heartbeat。首次 heartbeat 返回 `leaseRenewed=false / NOT_REQUIRED` 时保留原 `leaseExpiresAt`，receiver 仍按 `heartbeatDirective` 每约 60 秒继续 heartbeat，直到 `record_loop_result` 或 claim release。短任务可以减少中间 progress，但 progress 不续租且不改变 heartbeat 计划；primary 不得代发。任何影响扩大都返回 `REPLAN_REQUIRED`，在同一 Delivery 的下一 Revision 升级为 `STANDARD`。

单次整文件 Write、大 patch、批量编辑或命令也可能阻塞 receiver。修改既有大文件时优先拆成语义小 patch 并在块间 heartbeat；无法拆分且预计超过 60 秒时，在调用前用 `heartbeat_loop(expected_command_seconds=...)` 申请有上限的覆盖租约。

## 4:30—5:00 验收

TASK 成功后直接进入 `RECORD_USER_CONFIRMATION`，没有独立 Review receiver。用户检查：

- 定向验证证据和真实结果；
- `acceptance.md`；
- 由 Controller 从受验证 Git scope 采集并持久化的变更文件清单、base/HEAD 和状态指纹；源码 diff 不进入 Graph。

只有真实用户接受后才记录当前 Revision 完成；Delivery 此时仍是 `OPEN/未上线`。测试、业务验收和生产上线完成后再单独调用 `close_delivery`，进入 `CLOSED/已上线交付`。提交、推送、合并、发布与迁移仍分别需要自己的授权。

## 五分钟成功标准

- 一个根 TASK、零 GROUP、零 Review；
- 基线和需求已冻结；
- 至少有 claim 后立即 heartbeat；短任务可减少 progress；
- 有定向验证证据与 patch 快照；
- Revision 完成确认来自用户，不由 Agent 代填；本快速演练不自动关闭或归档 Delivery。
