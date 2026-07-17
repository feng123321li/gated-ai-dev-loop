# Hierarchical Work Item Registry

## 权威文件

`.hierarchical-delivery-governance/work-item-registry.json` 是机器权威，关键字段为：

```json
{
  "schemaVersion": 3,
  "coordinationRoot": "/absolute/project/root",
  "revision": 12,
  "currentFocus": {
    "workItemId": "t-issue-token",
    "purpose": "EXECUTION"
  },
  "workItems": [],
  "promotionHistory": []
}
```

每个条目记录 `id/kind/gateLevel/authorityKind/parentId/childIds/packagePath/stage/status`、baseline 与 contract 指纹、父契约指纹、`developmentMode`、gate、claim、开发结果、`acceptance`、验收报告入口、record revision、时间和分级 progress；Task 的 `gateLevel` 为 `LIGHT|FULL`，Delivery/Capability 固定为 `FULL`。Task 的 `developmentMode` 是 `development-mode.json` 的结构化快照，非 Task 必须为 null。每个治理根记录 acceptance 状态，以及已经复算 hash 的审查/用户确认 evidence 引用和内容快照；Delivery 同步保留 delivery 兼容投影。

`promotionHistory` 是追加式升层审计记录。每条保存源/父 ID 和 kind、升层前源 baseline 指纹、升层后源 baseline 指纹、父 baseline 指纹及时间；只允许 `TASK→CAPABILITY` 或 `CAPABILITY→DELIVERY`。恢复时结构或指纹格式不合法即拒绝 registry。

Delivery 的 `parentId` 固定为 null；Capability 和 Task 的 `parentId` 可以为 null，分别表示根 Capability 和根 Task。非空父级仍必须遵守 Delivery→Capability→Task 种类、计划 child 和范围包含关系。

## 确定性恢复

恢复只使用显式 ID/路径、有效焦点或唯一候选。候选多于一个时请求用户选择。不得用目录 mtime、名称相似度、描述关键词或“最近看起来像”进行恢复。

恢复时检查：

- registry coordination root 与当前根一致；
- ID 唯一、父子种类合法、无环；
- `gateLevel` 存在，只有 Task 可以为 `LIGHT`；
- packagePath 位于 `work-items/<id>`；
- baseline/state/registry 指纹一致；
- 父链 child-specific 指纹一致；
- 已选开发方式的 `development-mode.json` 存在，且内容与 registry 快照、Task ID 和当前 baseline 指纹一致；
- claim 和 operation 状态可解释；
- 投影可由 registry 重建。

## 焦点

`currentFocus` 只帮助恢复，不授予冻结、修订或开发权限。准备、冻结、认领、阻断和 gate 后可更新焦点；并行 Task 仍各自依赖 claim，不能把单一焦点当成全局锁。

新 Skill 不扫描或解释其他历史控制目录。Skill 内置 `scripts/hdg.mjs` 的正常闭环入口是 `prepare-item → 人工评审 development-review.md → freeze-item → select-development-mode → dispatch-task → task-result → accept-item → acceptance-item`；CLI 不提供原子 `approve-item`。另提供恢复/诊断用 `task-context/claim-task/gate-item/delivery-item`，以及 `revise-item/promote-item/retry-item/ready-tasks/refresh-projections/upgrade-registry`。`ready-tasks --item` 接受任意根或子树 ID；`refresh-projections` 不改变 revision 或状态，只重建中文 Markdown 工作台及评审入口。`upgrade-registry` 是同一控制目录内的显式兼容入口，不是新的流程层：仅接受无 claim、未运行 gate、包与 registry 指纹一致的单根 schema v2 Task，并要求用户确认 `LIGHT|FULL`。成功后保留 ID、冻结状态和开发方式，重新绑定 v3 指纹，清除旧 context/handoff，并在 `migrationHistory` 记录旧/新指纹、旧 registry hash 与时间。其他 v2 形态失败关闭。早期 v3 包可恢复和收尾，但下一次修订必须补齐 `developmentPlan`。控制器不打包历史 `route/start/prepare/freeze` 入口及其 YAML 配置实现。
