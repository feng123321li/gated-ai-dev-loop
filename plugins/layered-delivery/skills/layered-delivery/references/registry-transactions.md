# SQLite 事务、认领与并发

## 单写原则

所有机器状态变更都通过项目级 `governance.sqlite3` 执行。写命令使用 SQLite `BEGIN IMMEDIATE`：等待当前短事务完成、在事务内重读 revision、校验预期指纹并提交一次状态变更。Markdown 投影在提交并关闭写连接后重建，不占用 SQLite 写锁。控制器不创建额外文件锁。

长时间的 Agent、测试或人工动作不能持有数据库事务，必须用持久化 claim/operation 表示所有权。claim 只在 worker 实际启动时创建，包含 30 分钟软租约与最近心跳；执行适配器按 5 分钟 `nextWakeAt` 调用 `heartbeat-task`。软租约后有 2 分钟竞争宽限，宽限内同一 operation 的心跳或结果可与失联回收在 SQLite 写事务中确定性竞争；硬到期后控制器用 `advance-graph` 把 claim 归类为 `WORKER_LOST` 并按尝试预算恢复。数据库连接设置有限等待；无法取得写锁时明确失败，不绕过事务另写文件。

## 写入顺序

1. 验证协调根、目标安全路径和当前数据库 schema；
2. 开启 `BEGIN IMMEDIATE` 并重读 workspace、工作项和层级状态；
3. 检查 revision、层级指纹、baseline 指纹和活动 claim；仅 evidence 引用过期且完整 artifact 仍在库内的历史节点进入只读隔离，其他结构错误阻断；
4. 对执行证据，在事务内校验完整 artifact 与当前工作项、operationId、baseline 或动作，计算规范 JSON 的 SHA-256；
5. 在同一事务写 definition/state、证据 artifact 与摘要、上下文、报告和交互事件；registry 只更新本次实际变化的工作项及其祖先，内容未变的行不执行 `UPDATE`；
6. 更新 workspace revision 并提交；
7. 关闭写连接后从已提交数据库重建投影；普通状态迁移执行完整投影，心跳只刷新 execution graph、timeline 和 frontier，交互记录只刷新对应需求根的 interaction log；
8. 事务结束后再运行 Agent 或测试。

事务内写入失败必须回滚 SQLite，不留下可被恢复为成功的机器状态。Markdown 是可重建投影；提交后的投影失败会明确返回 `WORK_ITEM_PROJECTION_REFRESH_REQUIRED`，机器状态仍保持已提交，后续使用 `refresh-projections` 修复。投影刷新会检查 workspace revision；刷新期间若出现更新，会追赶最新 revision 后再完成，避免旧投影覆盖新状态。相同内容的文件不替换、不重复 `fsync`；这些可重建文件使用关闭临时文件后的原子替换，SQLite 仍保留完整持久化保证。不能把残缺 Markdown 当作权威。

事务可以读取隔离节点的稳定层级与状态，以维持依赖、进度和 claim 判断，但不能把它作为当前命令目标，也不能更新、删除或规范化其 SQLite 行。提交前控制器再次确认隔离集合和每条隔离记录均未变化。这样一个历史 evidence 节点不会阻断无关新需求或有效兄弟 Task，也不会成为绕过当前数据契约的兼容入口。

## Claim

Task claim 包含 `owner`、`operationId` 和 `claimedAt`。正常流程用 `dispatch-task` 在短事务中完成 READY 校验、graph run 内 operationId 唯一性校验、claim、结构化上下文入库和绑定 operationId 的 Markdown handoff；`claim-task` 仅用于恢复。相同 Task 不能重复认领，写入范围与活动 Task 重叠时也不能认领，历史 operationId 不能在新 attempt 复用。

Agent 返回结果时必须提交相同 operationId，并将完整结果 artifact 通过 `--evidence -` 从 stdin 交给控制器。成功写 `IMPLEMENTED`、artifact 和控制器计算的摘要，失败写 `BLOCKED`、阻断 artifact 和摘要，然后清除 claim，并生成 `development-review.md`。无法确认外部 Agent 是否已启动或写入时，不重复派遣，转人工核对。正常 PASS 必须以同样的 stdin 方式通过 `accept-item` 校验 gate artifact 后写 gate 与 `acceptance-report.md`，不能用自然语言补写 PASS，也不能用临时文件或 Agent 直写 SQLite 绕过事务。

同契约验证修正使用 `remediate-task --evidence -`。命令只接受没有活动 claim、需求未完成且状态为 IMPLEMENTED/BLOCKED/VERIFIED 的 Task；事务内校验当前 baseline、关联验收项、契约不变断言、精确文件和活动范围冲突，随后把 artifact、摘要与修正前状态快照追加到 `interaction_events`。补充文件不会改写 definition、baseline 或 graph definition，而是与冻结 `fileChanges` 合并成后续执行和 gate 的有效授权集合。控制器从目标 Task execution 沿显式边计算下游闭包；若闭包中存在活动 claim 则阻断，否则失效已推进的依赖消费者、聚合 gate 和根验收状态，并为受影响节点创建新 attempt。整个过程仍使用原 Task 和原根目录。

## 整树准备与冻结并发

`prepare-hierarchy` 在事务内检查新树所有 ID 是否与其他需求冲突，并一次写入完整层级记录。等待评审的同根树可以整体替换；层级指纹改变后旧确认失效。

`freeze-hierarchy` 对数据库中的层级指纹执行 compare-and-swap，重新验证全部 definition/state、父子关系和根级 `development-plan.md`，然后用同一次确认记录根级开发方式并冻结全部节点，避免同一需求出现多个顶层目录或部分冻结状态。

## Git 与删除边界

`.layered-delivery/**` 应被忽略且不被 Git 跟踪。开发 Agent 对数据库和投影只读。宿主不自动提交、推送、合并、发布或清理用户已有改动。

手工删除 `<root-id>` Markdown 目录不会删除 SQLite 状态。当前控制器没有获得明确删除授权时不得清理数据库记录；未来的删除能力必须在一个事务中按根 ID 删除整棵树，再清理对应投影，并明确交互审计记录的保留策略。
