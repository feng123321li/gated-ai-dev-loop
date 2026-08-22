# 可选多 Supervisor 入口路由

Entry Router 先用确定性规则与持久化 Delivery 状态处理“新需求、继续、恢复、确认、关闭、归档、查状态”等入口。多 Supervisor 是这个稳定路由器之后的可选决策层，不是执行层。

默认 registry：

- `requirements-supervisor`：新需求与重新规划；
- `execution-supervisor`：继续与恢复；
- `lifecycle-supervisor`：Revision 确认、上线关闭与归档；
- `observation-supervisor`：状态/进度查询；
- `entry-supervisor`：没有命中高置信规则的歧义输入。

所有 Supervisor 固定遵守同一边界：

- 只读取原始入口文本、持久化状态摘要和 Router 候选决策；
- `toolAccess=NONE`，不查业务数据、不调用 MCP、不持有 reservation/operation；
- 只输出结构化分类建议，不执行目标 Skill，也不生成给用户的最终回答；
- 确定性 Entry Router 或 primary coordinator 才消费建议并决定下一步。

## 启用

默认 `enabled=false`，因此没有额外模型调用或延迟。需要启用时，把[完整示例](examples/delivery-graph.supervisors.json)复制到业务项目根目录。

- `AMBIGUOUS_ONLY`：仅没有高置信规则时调用 `entry-supervisor`，推荐用于生产；
- `ALWAYS_VERIFY`：每个入口都由对应 Supervisor 再核对一次，成本和延迟更高，适合灰度评估。

配置必须完整覆盖九种入口 intent，且每种 intent 只能归一个 profile；修改配置会改变 registry fingerprint。Supervisor 输出不会直接覆盖状态冲突、明确 `rootId` 选择、完整性门禁或生命周期授权。
