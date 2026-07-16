# Hierarchical Work Item Registry

## 权威文件

`.hierarchical-delivery-governance/work-item-registry.json` 是机器权威，关键字段为：

```json
{
  "schemaVersion": 2,
  "coordinationRoot": "/absolute/project/root",
  "revision": 12,
  "currentFocus": {
    "workItemId": "t-issue-token",
    "purpose": "EXECUTION"
  },
  "workItems": []
}
```

每个条目记录 `id/kind/authorityKind/parentId/childIds/packagePath/stage/status`、baseline 与 contract 指纹、父契约指纹、`developmentMode`、gate、claim、证据、record revision、时间和分级 progress；Task 的 `developmentMode` 是 `development-mode.json` 的结构化快照，非 Task 必须为 null。Delivery 还记录独立的 delivery 状态，以及已经复算 hash 的审查/用户确认 evidence 引用和结构化内容快照。

## 确定性恢复

恢复只使用显式 ID/路径、有效焦点或唯一候选。候选多于一个时请求用户选择。不得用目录 mtime、名称相似度、描述关键词或“最近看起来像”进行恢复。

恢复时检查：

- registry coordination root 与当前根一致；
- ID 唯一、父子种类合法、无环；
- packagePath 位于 `work-items/<id>`；
- baseline/state/registry 指纹一致；
- 父链 child-specific 指纹一致；
- 已选开发方式的 `development-mode.json` 存在，且内容与 registry 快照、Task ID 和当前 baseline 指纹一致；
- claim 和 operation 状态可解释；
- 投影可由 registry 重建。

## 焦点

`currentFocus` 只帮助恢复，不授予冻结、修订或开发权限。准备、冻结、认领、阻断和 gate 后可更新焦点；并行 Task 仍各自依赖 claim，不能把单一焦点当成全局锁。

新 Skill 不扫描或解释其他历史控制目录。`hdg` 只使用 `prepare-item/freeze-item/revise-item/select-development-mode/retry-item/gate-item/delivery-item`、`ready-tasks`、`task-context`、`claim-task` 和 `task-result`。
