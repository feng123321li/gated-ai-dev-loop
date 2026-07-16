# 交付验收后反馈

用户在验收或后续对话中提出修改时，先分类，未确认前不创建、修订、冻结或重新打开工作项。

## 分类

- `REPAIR_TASK`：冻结 Task 已要求但实现不满足；保留 Task baseline，新增修复执行轮次；
- `REVISE_BASELINE`：目标、范围、R/A、子契约或测试发生变化；对对应 Delivery/Capability/Task 做显式修订；
- `APPEND_CHILD`：当前 Capability/Delivery 需要新增 Task/Capability；先修订父 baseline，再准备新子项；
- `FOLLOW_UP`：不阻断当前交付的新目标；创建关联的新工作项；
- `DEFER/DISMISS`：P2 或建议暂不实现；记录决定；
- `NEED_CLASSIFICATION`：信息不足，保持只读等待。

## 修订影响

修订前展示 expected baseline 指纹、变更字段、会 stale 的后代和保留有效的兄弟。新增兄弟不应重置未变化 Task；修改父稳定契约或自己的子契约必须重新冻结受影响后代。

## 已完成工作项

VERIFIED 工作项不直接原地改写。需要修改时，用户明确选择重新打开或创建后续工作项，并保留旧 gate、证据、关系和终态历史。

## 新目标

“顺便做”“再优化一下”“升级一下”不自动成为新 Delivery，也不自动继承原 scope。先确认层级、父项、ID、范围和 baseline，再持久化。

维护本 Skill 仓库的反馈仍受 self-hosting 边界约束：除非用户明确 dogfood，不创建 `.hierarchical-delivery-governance`。
