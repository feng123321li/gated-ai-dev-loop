# 分层注册表事务与并发

## 单写原则

所有 registry 变更通过同一协调根和原子目录事务执行：锁内重读当前 revision，校验预期指纹，提交一次变更，然后重建投影。长时间的 Agent、测试或人工动作不能持续占锁，必须用 claim/operation 表示所有权。

## 写入顺序

1. 验证协调根、Git ignore 和目标安全路径；
2. 取得 registry 事务锁并重读；
3. 检查 registry revision、层级指纹、baseline 指纹和活动 claim；
4. 原子准备或替换整个需求根目录；
5. 更新 registry revision；
6. 从 registry 重建 workspace、overview 和 progress 投影；
7. 释放锁后再运行 Agent 或测试。

写入失败不得留下可被误认为成功的 registry 状态。发现残留事务锁或恢复描述时保持阻断；没有精确文件身份、摘要和恢复授权，不删除或覆盖。

## Claim

Task claim 包含 `owner`、`operationId` 和 `claimedAt`。正常流程用 `dispatch-task` 在返回给用户前完成 READY 校验、claim 和绑定 operationId 的 handoff 生成；`claim-task` 仅用于恢复。相同 Task 不能重复认领；写入范围与任何活动 Task 重叠时也不能认领。

Agent 返回结果时必须提交相同 operationId。成功写 `IMPLEMENTED` 和证据，失败写 `BLOCKED` 和阻断证据，然后清除 claim，并生成 `development-review.json/md`。无法确认外部 Agent 是否已启动或写入时，不重复派遣，转人工核对。正常 PASS 必须通过 `accept-item` 校验 evidence 后写 gate 与 `acceptance-report.json/md`；不能用自然语言补写 PASS。

## 整树准备与冻结并发

`prepare-hierarchy` 在锁内检查新树所有 ID 是否与其他需求冲突，并一次写入完整嵌套目录和全部 registry 条目。等待评审的同根树可以整体替换；层级指纹改变后旧确认失效。

`freeze-hierarchy` 对一个层级指纹执行 compare-and-swap，重新验证 `hierarchy.json`、全部 baseline/state、子包完整性和根级 `development-plan.md`，然后用同一次确认记录根级开发方式并更新全部节点，避免同一需求出现多个顶层目录或部分冻结状态。

## Git 边界

`.hierarchical-delivery-governance/**` 应被忽略且不被 Git 跟踪。开发 Agent 对控制目录只读。宿主不自动提交、推送、合并、发布或清理用户已有改动。
