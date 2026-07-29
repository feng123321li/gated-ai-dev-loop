# Layered Delivery 的 Graph Engineering 架构

## 结论

`layered-delivery` 的职责是总览与调度，不是实现流程治理。

```text
Outer Graph
  ├─ Task Loop A
  ├─ Task Loop B
  ├─ Join
  ├─ Review Loop
  └─ User Confirmation

Task Loop A
  └─ 自己的理解 → 开发 → 测试 → Gate → 修正循环

Task Loop B
  └─ 另一套 Skill、规范和内部循环
```

这比把 Task、development、test、Gate、Skill activation 全部展开在一张全局图中更符合 Graph Engineering：外层节点具有清晰自治边界，只通过稳定输入、资源声明和标准终态耦合。

## 分层职责

| 层 | 负责 | 不负责 |
|---|---|---|
| Outer Graph | 依赖、并行、资源锁、租约、基础设施重试、Join、Review、用户确认 | 文件、实现、测试、Gate、Skill |
| Task Loop | 完成一个独立结果，内部校验与修正 | 决定全局依赖或抢占其他资源 |
| Skill | 为某个 Loop 提供实现方法或领域规范 | 改写外层冻结 Graph |

Graph 节点是自治工作单元，不是每一个内部动作。若某个 Java Loop 规定 Entity/Mapper/Service 的创建方式，它可以在 payload 和自身 Skill 中定义，不会与 layered-delivery 的文件 scope 冲突。

## Skill Hint 晚绑定

需求阶段通常只能知道“希望优先使用哪些 Skill”，无法可靠知道未来哪个 Task/Review Loop 会适用。因此 hierarchy 只在顶层保存共享 `skillHints`，不把它们编译成节点，也不分配阶段：

```text
用户 Skill Hint
       │
       ├──────────┬──────────┐
       ▼          ▼          ▼
 Task Loop A  Task Loop B  Review Loop
 运行时选择    运行时选择    运行时选择
```

每个 Loop 读取当前任务、工作区和宿主 Skill catalog 后，优先原生触发适用提示。某个提示对 Loop 不适用或当前不可用时可以跳过；Loop 也可以发现并使用其他 Skill。外层 scheduler 只负责传递，不验证 Skill 激活、顺序或生命周期。

这保持了两个边界：用户偏好不会丢失，Loop 自治也不会被 requirement 阶段的错误猜测锁死。

## 图模型

节点类型：

- `TASK_LOOP`
- `CAPABILITY_JOIN`
- `DELIVERY_JOIN`
- `REVIEW_LOOP`
- `USER_CONFIRMATION`

边类型：

- `REQUIRES_SUCCESS`
- `ALL_OF`

Task 依赖与层级 Join 构成 DAG。Loop 内部允许有受控循环，因此“整个系统支持循环”与“外层依赖图无环”并不矛盾。

## Loop 边界

输入：

```json
{
  "ref": "project/java-service-loop@1",
  "payload": {},
  "resourceClaims": ["project:erp/module:order"]
}
```

输出：

```json
{
  "status": "SUCCEEDED",
  "summary": "完成",
  "result": {}
}
```

调度器只验证 JSON 可持久化、ref 合法、资源键安全唯一、终态受支持。它不读取 payload/result 里的业务字段。

## 资源模型

取消文件 scope 的全局包含/重叠算法，改用精确资源锁：

- `project:erp/module:order`
- `project:erp/database:order-schema`
- `project:portal/environment:test`

相同键互斥，无交集即可并行。Loop 可以跨项目或跨模块，但必须声明真正需要排他的共享资源。

## 失败路由

外层自动重试只面向运行基础设施：

- `RETRYABLE_INFRA`
- `WORKER_LOST`

以下情况不自动重跑：

- Loop 的业务 Gate 未解决；
- 需要外部权限；
- 需要改变依赖、资源或拓扑；
- Loop 主动取消。

Gate 失败后的同任务修正由 Loop 内部继续；只有外层契约不再适用时才返回 `REPLAN_REQUIRED`。

## 可恢复性

每次迁移写入带前序哈希的事件。`node_runs` 是高效查询的物化状态，必要时由 `rebuild_graph_run` 从事件重建。MCP 断连后先读权威状态，operation ID 不复用。

## 为什么更符合 Graph Engineering

1. 节点边界按自治能力划分，而不是按实现步骤划分。
2. 外层只持有调度信息和共享 Skill 偏好，降低跨 Skill 耦合。
3. 不同 Loop 可以在运行时独立演进和选择 Skill。
4. 失败域清晰：内部质量失败留在 Loop，基础设施失败进入 scheduler，拓扑变化进入 replan。
5. DAG、FSM、事件链和资源锁各自表达一种关系，不把所有语义塞进一个 scope 或 Gate 模型。
