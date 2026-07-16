# 多工作区、多仓库与多微服务

跨仓库交付只选择一个协调根保存 `.hierarchical-delivery-governance/work-item-registry.json` 和 work item 包。其他工作区只保存业务改动，不复制控制面。

## 授权

每个 Task 必须绑定全部需要写入的工作区、允许路径、测试 cwd 和访问方式。覆盖不完整时状态为 `WAITING_FOR_WORKSPACE_AUTHORIZATION`，不得生成一个宿主已知必然阻塞的交接。

示例授权：

```json
{
  "schemaVersion": 2,
  "coordinationRoot": "C:/projects/platform",
  "workspaces": [
    {
      "workspaceId": "provider-service",
      "root": "C:/projects/provider",
      "access": "read-write",
      "allowedPaths": ["src/**", "tests/**"]
    }
  ]
}
```

覆盖结果：

```json
{
  "schemaVersion": 2,
  "taskId": "t-provider-contract",
  "status": "PASS",
  "workspaceIds": ["provider-service"],
  "missing": []
}
```

## 依赖波次

提供方契约 Task 必须先完成并机械验证，再启动消费方 Task：

```json
{
  "schemaVersion": 2,
  "tasks": [
    {
      "taskId": "t-provider-contract",
      "workspaceIds": ["provider-service"],
      "dependsOn": []
    },
    {
      "taskId": "t-consumer-integration",
      "workspaceIds": ["consumer-service"],
      "dependsOn": ["t-provider-contract"]
    }
  ]
}
```

测试分配：

```json
{
  "schemaVersion": 2,
  "tests": [
    {
      "workspaceId": "provider-service",
      "cwd": "C:/projects/provider",
      "argv": ["npm", "test"]
    },
    {
      "workspaceId": "consumer-service",
      "cwd": "C:/projects/consumer",
      "argv": ["npm", "test"]
    }
  ]
}
```

机械门禁先逐工作区，再整体聚合。前置工作区失败时，后置工作区测试记录为 `BLOCKED`，不能继续运行后假装独立成功。

## 交接规则

只有覆盖结论 `PASS`、父链有效且依赖满足时才生成 Task context。接收 Agent 无法访问任一授权工作区时，必须在写入前 BLOCKED。宿主不得为了绕过权限把控制包复制到其他仓库。
