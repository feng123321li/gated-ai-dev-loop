# 分层注册表事务与并发

## 单写原则

所有 registry 变更通过同一协调根和原子目录事务执行：锁内重读当前 revision，校验预期指纹，提交一次变更，然后重建投影。长时间的 Agent、测试或人工动作不能持续占锁，必须用 claim/operation 表示所有权。

## 写入顺序

1. 验证协调根、Git ignore 和目标安全路径；
2. 取得 registry 事务锁并重读；
3. 检查 record revision、baseline 指纹和活动 claim；
4. 原子准备或替换 work item package；
5. 更新 registry revision；
6. 从 registry 重建 workspace、overview 和 progress 投影；
7. 释放锁后再运行 Agent 或测试。

写入失败不得留下可被误认为成功的 registry 状态。发现残留事务锁或恢复描述时保持阻断；没有精确文件身份、摘要和恢复授权，不删除或覆盖。

## Claim

Task claim 包含 `owner`、`operationId` 和 `claimedAt`。claim 前重新计算 READY。相同 Task 不能重复认领；写入范围与任何活动 Task 重叠时也不能认领。

Agent 返回结果时必须提交相同 operationId。成功写 `IMPLEMENTED` 和证据，失败写 `BLOCKED` 和阻断证据，然后清除 claim。无法确认外部 Agent 是否已启动或写入时，不重复派遣，转人工核对。

## 修订并发

`revise-item` 使用 expected baseline 指纹进行 compare-and-swap。指纹已变化、修订会使活动后代 claim stale、工作项已 VERIFIED 或试图删除 child 时拒绝。纯追加且不改变现有子契约时允许与无关 claim 并存。修订父 baseline 后，不主动改写子 baseline；子项在下次上下文/claim 校验中自行证明仍有效或进入 stale。

## Git 边界

`.hierarchical-delivery-governance/**` 应被忽略且不被 Git 跟踪。开发 Agent 对控制目录只读。宿主不自动提交、推送、合并、发布或清理用户已有改动。
