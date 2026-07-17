# Hierarchical Work Item Registry

## 权威文件

`.hierarchical-delivery-governance/work-item-registry.json` 记录全部节点状态；每个需求根目录的 `hierarchy.json` 记录根 ID、层级指纹、完整节点路径和统一人工评审状态。

Registry 每个条目记录 `id/kind/gateLevel/authorityKind/parentId/childIds/packagePath/stage/status`、baseline 与 contract 指纹、父契约指纹、`developmentMode`、gate、claim、开发结果、根级 acceptance、验收报告入口、record revision、时间和分级 progress。

## 单根嵌套路径

- 根：`work-items/<root-id>`；
- 直接子级：`<parent-packagePath>/children/<child-id>`；
- 更深节点继续递归；
- Registry 中每个 childId 必须有真实条目和真实包；
- parentId、childIds、kind 与 packagePath 必须互相一致。

因此每个用户需求在 `work-items/` 下只有一个顶层目录。Registry 可以使用平铺数组方便 ID 查询，但磁盘包和人类投影必须保持树形。

## 确定性恢复

恢复只使用显式 ID/路径、有效焦点或唯一候选。候选多于一个时请求用户选择。不得依赖目录时间、名称相似度或描述关键词。

恢复时检查：

- coordination root 与当前工作区一致；
- 当前完整 schema v3，字段无缺失、无未知兼容字段；
- ID 唯一，父子种类合法，无环；
- 所有计划 child 已物化；
- packagePath 满足单根递归路径；
- hierarchy/baseline/state/registry 指纹一致；
- 根级 `development-plan.md` 与当前整树可重建内容完全一致；
- `development-mode.json`、claim、operation 和 evidence 可解释；
- Markdown 投影可由机器状态重建。

## 焦点与命令

`currentFocus` 只帮助恢复，不授予冻结或开发权限。正常闭环是：

```text
prepare-hierarchy
→ 人工查看 development-plan.md
→ freeze-hierarchy
→ select-development-mode
→ dispatch-task
→ task-result / development-review
→ accept-item / acceptance-report
→ acceptance-item
```

CLI 不提供逐节点 `prepare-item/freeze-item`、旧 schema 升级、兼容别名或历史 Node 入口。诊断/恢复命令包括 `task-context`、`claim-task`、`gate-item`、`retry-item`、`ready-tasks` 和 `refresh-projections`。
