# 多仓库与多服务边界

当前控制器只维护一个协调根。需求涉及多个仓库或服务时，所有计划文件必须能用协调根下的安全相对路径表示；控制面仍只存在于协调根的 `.hierarchical-delivery-governance/`，不得复制到各子仓库。

## 规划要求

- 每个 Task 的 `scope` 和 `developmentPlan.fileChanges` 明确写出包含仓库目录的相对路径；
- 测试命令从协调根执行，并通过命令参数定位对应仓库或模块；
- 提供方与消费方使用 Task/Capability `dependsOn` 表达先后关系；
- 跨服务接口、schema、事件或配置写入父级 `sharedContracts`；
- 任何目标路径无法从协调根安全访问时，在准备需求树前保持阻断，不虚构工作区授权状态。

## 调度与验收

Agent 只调度依赖已验证、路径不冲突且实际可访问的 Task。资源允许时可把不同仓库的互斥 Task 交给隔离子 Agent；否则串行或由当前 Agent 处理。每个 Task 仍使用独立 claim、operationId、上下文、结果和 gate。

机械门禁先验证各 Task 的真实改动和测试，再运行 Capability/Delivery 聚合测试。前置仓库或服务失败时，消费方保持不可调度，不能绕过依赖后宣称整体成功。
