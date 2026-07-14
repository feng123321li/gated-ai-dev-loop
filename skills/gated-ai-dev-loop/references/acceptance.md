# 机械自检与独立验收

## 目录

- [验收产物](#验收产物)
- [机械自检报告](#机械自检报告)
- [选择独立审查者](#选择独立审查者)
- [严重级别](#严重级别)
- [独立审查输入](#独立审查输入)
- [审查提示词](#审查提示词)
- [review.json 契约](#reviewjson-契约)
- [acceptance-report.md 模板](#acceptance-reportmd-模板)
- [final-acceptance-report.md 模板](#final-acceptance-reportmd-模板)
- [结论判定](#结论判定)
- [修复与人工验收](#修复与人工验收)

## 验收产物

每轮在 `rounds/round-NN/` 生成并保留：

- `gate-evidence.json`：机械门禁原始结构化证据；
- `self-check-report.md`：宿主根据机械证据生成的人可读自检报告；
- `review.json`：独立审查的机器可读结论和 P0/P1/P2 findings；
- `acceptance-report.md`：宿主根据已校验的 `review.json` 渲染的人可读验收报告。

每次验收后还要在任务目录根部覆盖刷新 `final-acceptance-report.md`。它是给人工查看的最新汇总入口，不替代上述轮次原始证据。

开发 Agent 不得直接写这些文件。独立验收 Agent 负责形成 findings 和结构化结论；宿主只负责保存、校验并从该结论确定性渲染 Markdown，不得改写审查语义。报告不得替代原始证据。

## 机械自检报告

机械门禁完成后立即生成 `self-check-report.md`：

```markdown
# <task-id> round-NN 机械自检报告

## 结论
PASS / FAIL / NEED_HUMAN_REVIEW

## 冻结完整性
- 基线指纹：匹配 / 不匹配
- 冻结产物改动：无 / <路径>

## 改动归属与范围
- 开发前已有改动：<摘要>
- 本轮真实改动：<路径列表>
- 越界或未归属改动：无 / <路径与原因>
- parallel 集成：不适用 / <parallel-plan 和 integration-result 链接>

## 保护项检查
- .git/**：通过 / 失败
- .ai-dev-loop/** 开发者写入：无 / 发现
- 敏感文件：无 / 发现

## 测试证据
| argv | exitCode | passed | failed | errors | skipped | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| <JSON argv> | 0 | 1 | 0 | 0 | 0 | PASS |

## 阻断项
- 无；或列出确定性的机械失败。
```

指纹不匹配、越界或未归属改动、受保护文件写入、测试失败、超时、未运行或证据不完整都不能进入独立审查。证据不完整或无法证明归属时使用 `NEED_HUMAN_REVIEW`，不要伪造成 P1。

## 选择独立审查者

按以下顺序选择：

1. 优先启动一个与开发者分离的其他 Agent，产品不限，但必须是全新、只读且不继承开发上下文，记录 `reviewerKind=independent-agent`。
2. 没有可用的其他 Agent 时，启动当前宿主的全新验收子 Agent，只传入本节允许的审查输入，记录 `reviewerKind=fresh-subagent`。
3. 无法证明没有继承需求分析/开发上下文，或无法保证只读时，返回 `NEED_HUMAN_REVIEW`。

禁止使用、恢复、派生或 fork 任何需求分析和开发对话做验收。子 Agent 必须从空任务上下文启动；“是另一个会话”本身不足以证明隔离。

## 严重级别

- `P0`：可能导致数据破坏或泄露、权限绕过、严重安全漏洞、不可逆破坏、关键服务不可用或等价灾难性后果。必须 `FAIL`。
- `P1`：冻结需求或验收项未满足、明显功能错误、关键边界或异常处理错误、事务/并发/兼容性缺陷、关键测试缺失或失败。必须 `FAIL`。
- `P2`：不阻断当前冻结验收的可维护性、可读性、轻微性能或补充测试建议。允许 `PASS`，但必须展示给用户。

不要为了填充报告而制造 finding。隔离、证据或改动归属无法证明时使用 `NEED_HUMAN_REVIEW`，不归类为 P0/P1/P2。

## 独立审查输入

只提供：

- 冻结基线或 Light 简报；
- `acceptance.json` 和 `tasks.json`；
- 最终真实 diff；
- 开发事实报告；
- `gate-evidence.json` 和 `self-check-report.md`；
- 审查者必须只读的规则。

parallel 任务同时提供 `parallel-plan.json`、每个 Agent 的范围证据和 `integration-result.json`，只验收最终聚合状态。不得提供隐藏推理、已放弃方案、早期对话或开发者自评。

## 审查提示词

```text
你是全新、只读且没有需求分析或开发上下文的验收 Agent。根据冻结授权审查最终仓库改动，不得修改文件、修复代码、改变验收或补猜缺失证据。
逐项检查全部冻结验收 ID，并检查边界、异常、权限、安全、数据、兼容性、并发和测试充分性。
按 P0/P1/P2 输出具体 findings；每项必须包含证据、影响、关联 ID 和可执行修复建议。
P0 或 P1 存在时返回 FAIL；只有 P2 时允许 PASS；证据、隔离或归属不足时返回 NEED_HUMAN_REVIEW。
PASS 不授权提交、推送、合并、发布或最终验收。
```

## review.json 契约

审查者返回结构化内容，宿主校验后保存：

```json
{
  "status": "FAIL",
  "reviewer": "codex",
  "reviewerKind": "independent-agent",
  "isolation": "fresh-read-only-no-development-context",
  "checkedAcceptanceIds": ["A-001"],
  "counts": { "p0": 0, "p1": 1, "p2": 1 },
  "findings": [
    {
      "id": "F-001",
      "severity": "P1",
      "title": "批量操作未保持原子性",
      "relatedIds": ["R-001", "A-001", "T-001"],
      "file": "src/example.ext",
      "line": 42,
      "evidence": "部分失败后已写入记录未回滚",
      "impact": "验收项 A-001 不满足",
      "remediation": "在同一事务中执行并补充回滚测试"
    }
  ],
  "suggestedTests": ["补充部分失败时的回滚用例"],
  "repairInstructions": ["修复 F-001 后重跑全部冻结测试"]
}
```

`status` 只能是 `PASS`、`FAIL` 或 `NEED_HUMAN_REVIEW`；`reviewer` 使用安全的小写 Agent 标识；`reviewerKind` 只能是 `independent-agent` 或 `fresh-subagent`；`isolation` 必须是 `fresh-read-only-no-development-context`。finding ID 必须唯一，severity 只能是 P0/P1/P2，counts 必须与 findings 精确一致。P0/P1 至少关联一个冻结 R/A/T ID或明确的 `SAFETY`；文件或行号不适用时使用 `null`，不得虚构位置。

## acceptance-report.md 模板

宿主从校验通过的 `review.json` 渲染：

```markdown
# <task-id> round-NN 独立验收报告

## 结论
PASS / FAIL / NEED_HUMAN_REVIEW

## 审查身份
- reviewer: <agent-id>
- reviewerKind: independent-agent / fresh-subagent
- isolation: fresh-read-only-no-development-context

## 严重级别汇总
| P0 | P1 | P2 |
| ---: | ---: | ---: |
| 0 | 1 | 1 |

## 已检查内容
- 验收 ID：A-001
- 真实 diff：<摘要或链接>
- 机械自检：[self-check-report.md](self-check-report.md)
- 测试证据：[gate-evidence.json](gate-evidence.json)

## P0 严重问题
- 无；或逐项列出 ID、关联 R/A/T、位置、证据、影响和修复建议。

## P1 阻断问题
- 无；或逐项列出 ID、关联 R/A/T、位置、证据、影响和修复建议。

## P2 非阻断建议
- 无；或逐项列出 ID、位置、证据和建议。

## 建议补充测试
- 无；或列出具体场景。

## 给开发 Agent 的修复指令
- 无需修复；或列出关联 finding ID 的最小修复清单。
```

报告不得包含“允许合并”、自动提交或 Git commit message 建议；最终操作仍由用户决定。

## final-acceptance-report.md 模板

宿主从当前轮次已校验的机械证据和 `review.json` 确定性渲染；每轮覆盖旧汇总，但不得删除旧轮次报告：

```markdown
# <task-id> 最终验收报告

> 当前验收结论：**PASS / FAIL / NEED_HUMAN_REVIEW**
> 当前验收轮次：**round-NN**
> 人工确认状态：**WAITING_FOR_MANUAL_ACCEPTANCE / BLOCKED_BY_P0_P1 / NEED_HUMAN_REVIEW**

## 验收摘要
| 项目 | 结果 |
| --- | --- |
| 任务模式 | Full / Light |
| 机械门禁 | PASS / UNVERIFIED |
| 独立审查者 | codex / claude |
| 审查者类型 | independent-agent / fresh-subagent |
| 上下文隔离 | fresh-read-only-no-development-context |
| P0 / P1 / P2 | 0 / 0 / 1 |
| 已检查验收 ID | A-001 |

## P0 严重问题
- 无；或完整列出 finding、关联 ID、位置、证据、影响和修复。

## P1 阻断问题
- 无；或完整列出 finding、关联 ID、位置、证据、影响和修复。

## P2 非阻断建议
- 无；或完整列出 finding、位置、证据、影响和建议。

## 建议补充测试
- 无；或列出具体场景。

## 修复指令
- 无需修复；或列出最小修复清单。

## 人工操作结论
<说明是否可进入人工确认；PASS 也不授权自动提交、推送、合并或发布。>

## 证据导航
- 冻结授权、开发总览和开发进度
- 当前轮次 self-check-report.md、gate-evidence.json、acceptance-report.md 和 review.json
```

`PASS` 对应 `WAITING_FOR_MANUAL_ACCEPTANCE`；`FAIL` 对应 `BLOCKED_BY_P0_P1`；证据或隔离不足对应 `NEED_HUMAN_REVIEW`。报告必须包含完整 findings，不能只给数量或链接，确保人工先看这一份即可理解结论。

机械自检 PASS 后运行：

```text
gated-loop accept --task <task-id> --round <NN>
```

CLI 默认调用配置中的独立 Codex，命令不可用时才调用独立 Claude；不会在一次 reviewer 执行失败后静默换模型。外部 reviewer 在系统临时目录启动，不把项目目录设为工作目录，只接收经过门禁筛选的冻结文件、证据和 diff。宿主具备 Agent API 时，应先按上面的优先级启动其他 Agent 或全新子 Agent，再把其完整 JSON 通过 `--review-result <file>` 或 `--review-result -` 交给 CLI 校验和落盘。无论哪条路径，`gated-loop accept` 都会先重新核对 self-check 指纹和真实 diff；仓库状态变化时返回 `NEED_HUMAN_REVIEW`。

## 结论判定

- 任一 P0 或 P1：`FAIL`。
- 没有 P0/P1，全部验收 ID 已检查且证据完整：`PASS`；可以包含 P2。
- 隔离、证据、测试、归属或检查覆盖无法证明：`NEED_HUMAN_REVIEW`。
- `FAIL` 必须至少有一个 P0/P1；`PASS` 的 P0/P1 计数必须为零；`NEED_HUMAN_REVIEW` 必须说明缺失证据或隔离原因。

## 修复与人工验收

收到 `FAIL` 后，只把 P0/P1 finding、关联 ID、允许路径和必须重跑的测试交给新的隔离开发上下文，或通过明确 manual 方式交接。P2 默认不进入自动修复，除非用户明确授权。修复后重新运行全部机械门禁和独立验收，最多三轮。

进入 `WAITING_FOR_MANUAL_ACCEPTANCE` 前，更新 `progress.md`，刷新并优先展示任务根目录的 `final-acceptance-report.md`。用户需要追溯时再展示 `development-overview.md`、轮次级报告和原始 JSON 证据。只有用户明确确认后才能完成；用户拒绝时记录关联 finding 或验收 ID 并进入修复轮次。
