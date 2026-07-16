# 分层交付治理工作流

## 入口顺序

1. 判断是否为 `hierarchical-delivery-governance` 实现仓库；是则进入 `SELF_HOSTING_MAINTENANCE`，默认不创建运行包。
2. 未命中自举短路，或用户已明确 dogfood 时，才只读恢复 `.hierarchical-delivery-governance/work-item-registry.json`。
3. 对新工作生成顶层交付单元的 Delivery 总览草案，不先写磁盘；该单元可以是完整项目或可独立交付的大型模块、子系统、跨服务需求。
4. 用户批准 Delivery ID 和 baseline 持久化后准备并冻结 Delivery。
5. 逐个准备、确认和冻结 Capability。
6. Capability 持续拆分并冻结 Task；需要新增 Task 时显式修订 Capability baseline。Task 冻结后进入 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`。
7. 宿主展示 `active/manual`，等待用户明确选择，再用当前 Task baseline 指纹和 `--confirmed` 持久化 `development-mode.json`；baseline 确认不能兼作开发方式确认。
8. 只有开发方式记录通过机械校验后，才计算 READY Task、生成独立上下文并原子认领。
9. 开发 Agent 返回实现事实或 BLOCKED；宿主运行 Task 门禁。
10. 全部 Task 验证后运行 Capability 门禁；全部 Capability 验证后运行 Delivery 门禁。
11. Delivery gate PASS 后持久化待独立审查；隔离审查 PASS 或显式接受人工审查结果后，再由用户确认完成交付。

任何步骤都不能从自然语言关键词推导“创建、冻结、修订或 dogfood”授权。

## 状态流

```text
Delivery:    PREPARED → FROZEN → VERIFIED → REVIEWED → USER_CONFIRMED
                         │
Capability:              └→ PREPARED → FROZEN → VERIFIED
                                              │
Task:                                         └→ PREPARED → WAITING_FOR_DEVELOPMENT_MODE_SELECTION
                                                               ↓ explicit active/manual confirmation
                                                             FROZEN
                                                               ↓
                                  [READY 派生谓词] → CLAIMED → IMPLEMENTED → VERIFIED
                                                         └────→ BLOCKED
```

Delivery/Capability 的 `VERIFIED` 不是子级状态的简单别名：子级全部 VERIFIED 只是运行父级 gate 的前置条件。

Delivery/Capability 的 decomposition 必须先从 `OPEN` 显式变为 `SEALED`。READY 不写入 lifecycle；依赖或 claim 变化后立即重算。

任何 gate FAIL 都进入 BLOCKED。必须用 `retry-item` 提交当前 baseline 指纹并显式确认，才能回到 FROZEN 重跑；不能直接用下一次 PASS 覆盖失败记录。

## 恢复

恢复优先级固定为：

1. 用户给出的精确 work item ID 或包路径；
2. 注册表中仍有效的 `currentFocus.workItemId`；
3. 唯一非终态候选；
4. 多个候选时请用户选择。

恢复后验证包指纹、父链指纹、依赖、claim 和工作区授权。任何一项不确定都保持只读并报告阻断，不重新创建同名包。

## 修订

修订必须携带当前 baseline 指纹和显式确认。新增兄弟子项不影响现有子项；父稳定契约或该子项契约变化会使对应后代 stale。已 VERIFIED 的工作项不能直接修订，应创建后续工作项或按反馈流程明确重新打开。

## 失败关闭

- 基线不完整：不准备包；
- 未确认：不冻结；
- 未明确选择开发方式：保持 `WAITING_FOR_DEVELOPMENT_MODE_SELECTION`，不生成上下文、不认领；
- `hdg` 不可用或 `development-mode.json` 校验失败：保持阻断，不用对话内“等价流程”绕过；
- 父链漂移：不生成上下文、不认领；
- 依赖未验证：Task 不 READY；
- 范围冲突：不并行；
- 测试或证据缺失：gate 不 PASS；
- 无独立语义审查能力：保持 `WAITING_FOR_INDEPENDENT_REVIEW` 并生成 `NEED_HUMAN_REVIEW` 验收包；
- 未取得用户确认：不得把 Delivery 标为 `COMPLETED`；
- 外部状态变更未授权：停止并请求授权。
