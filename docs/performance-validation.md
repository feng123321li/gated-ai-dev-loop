# 性能量化与真实项目验收

性能验证分成两层。合成基准是每次提交都能重复的 Controller 回归门禁；真实项目验收用于判断用户实际等待时间。前者不能替代后者，后者也不适合成为每次单元测试的稳定门禁。

## 合成 Controller 基准

运行：

```text
python scripts/benchmark_controller.py --iterations 10 --warmup 2
```

脚本仅使用 Python 标准库，在系统临时目录创建最小 schema v3 Delivery 和 SQLite/Markdown 投影，结束后自动清理，不在维护仓库创建 `.layered-delivery/`。它固定测量：

- `entryRouter`：确定性入口分类；
- `prepareAndFreeze`：层级校验、Graph 编译、SQLite 和初始投影；
- `workspaceStatus`：冻结 Delivery 的状态读取；
- `graphFrontier`：Graph 推进与 frontier 组装。

输出是版本化 JSON，每项包含 `iterations`、`totalMs`、`meanMs`、`p95Ms`、`maxMs`、`budgetMs` 与 `passed`。任一 P95 超预算时进程返回非零；CI 在 Python 3.10、3.12、3.14 上执行 5 次测量和 1 次预热。预算用于发现数量级回退，不用于比较不同硬件的细小波动。需要诊断单次 Controller 操作时设置 `HDG_TIMING=1`，stderr 会输出脱敏的单行结构化计时，默认不改变任何业务响应。

比较优化前后时应使用同一机器、同一 Python 版本、相同 `iterations`/`warmup`，至少重复三轮并比较 P95。不要用单次最小值证明优化，也不要把 `--budget-scale` 调大后当作通过证据；该参数只用于校准新执行环境。

## 真实业务仓库验收

模型推理、宿主建 Agent、MCP 往返、依赖下载和业务构建不在合成基准范围内。涉及这些路径的优化，应在代表性真实业务仓库创建一个独立性能验收 Delivery，而不是在本维护仓库开启 dogfood。固定以下条件后再比较：

- 同一需求文本、Graph 规模、Agent Profile/Team 和宿主版本；
- 相同模型、上下文起点、网络条件与冷/热依赖缓存状态；
- 分别记录入口路由、规划冻结、派遣等待、每个 Loop、Review、Result Assembler 和最终回答耗时；
- 同时记录结果账本完整率、重试次数、丢失 attempt、工具调用数与输出漏项数，避免用更快换取不完整。

建议至少执行三轮冷启动和五轮热启动，分别报告端到端 P50/P95、最慢阶段和完整性门禁结果。只有合成基准未回退、真实项目端到端改善且结果完整性不下降，才把性能优化判定为闭环完成。
