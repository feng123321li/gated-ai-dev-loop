# 外层 Graph 规划

只在用户要求创建新的软件交付 Graph 时读取。

## 选择最浅层级

- 一个可独立调度结果：根 `Task`。
- 多个 Task Loop 需要依赖或 Join：`Capability → Task`。
- 多组 Capability 需要依赖或总 Join：`Delivery → Capability → Task`。

按调度关系拆分，不按文件数量拆分。一个 Task Loop 可以覆盖一个模块、多个模块或多个项目；只要它能作为一个整体返回标准终态即可。

## 定义 Task Loop

Task 的外层字段只说明：

- 稳定 ID、标题和调度摘要；
- 同级 Task 依赖；
- `loop.ref`：执行适配器或 Loop Skill 的稳定引用；
- `loop.payload`：原样交给 Loop 的不透明 JSON；
- `resourceClaims`：需要排他占用的精确资源锁。

不要把 `scope`、`developmentPlan`、`testCommands`、`gateLevel` 或 `requiredSkills` 放进外层 definition。Loop 需要这些内容时，由它自己的 payload 规范定义和解释。

多项目、多模块示例：

```json
{
  "ref": "project/java-service-loop@1",
  "payload": {
    "goal": "实现订单创建并完成 Loop 内部验证",
    "acceptance": ["返回可供下游消费的订单接口结果"]
  },
  "resourceClaims": [
    "project:erp/module:order",
    "project:erp/database:order-schema"
  ]
}
```

资源声明是精确键：相同键互斥，不做 glob、目录包含或文件写授权判断。只声明确实不能并行占用的资源。

## Skill Hint 晚绑定

需求阶段若用户给出一个或多个 Skill，只在 hierarchy 顶层登记一次：

```json
{
  "skillHints": [
    {
      "name": "springboot-tdd",
      "purpose": "后续 Loop 实际处理 Spring Boot 开发时优先采用 TDD"
    }
  ]
}
```

- `skillHints` 是共享、建议性的运行时偏好，不是 `requiredSkills`。
- 需求阶段不把提示分配到 Task、开发阶段、Gate 阶段或 Review 阶段，也不因提示内容新增 Graph 节点。
- 尚无合适提示时使用空数组。不要为填充该字段猜测 Skill。
- 每个 Task/Review Loop 启动后读取全部提示，结合当前任务和宿主实际可用 Skill 独立判断；优先原生触发适用提示，可以跳过不适用或不可用提示，也可以使用其他更合适的 Skill。
- 调度器不查询 Skill catalog、不校验激活证据，也不因某条提示未使用而判定 Graph 失败。

如果某项要求确实是业务完成的硬条件，应由对应 Loop 的 payload/验收协议表达；不要把硬条件伪装成调度器 Skill Hint。

## Review Loop

在 hierarchy 顶层定义一个 `reviewLoop`。它与 Task Loop 使用相同描述协议，但职责是独立评估整个根结果。它收到同一组 `skillHints`，并在运行时独立选择适用的审查 Skill 和内部 Gate 规范。

## 准备与冻结

1. 调用 `hierarchy_contract(root_kind=...)`。
2. 按返回的 schema 和 example 创建完整 hierarchy。
3. 调用 `prepare_hierarchy`，向用户展示 `overview.md` 中的节点、依赖、Loop 引用和资源声明。
4. 用户要求修改时重新 prepare；不要复用旧 fingerprint。
5. 用户明确同意后，使用当前 `hierarchyFingerprint` 调用 `freeze_hierarchy`。
6. 冻结后立即转到 `graph_frontier`，不再逐 Task 请求方案确认。
