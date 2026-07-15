# 机械自检与能力驱动验收

## 目录

- [验收产物](#验收产物)
- [机械自检报告](#机械自检报告)
- [选择验收能力](#选择验收能力)
- [人工语义审查证据](#人工语义审查证据)
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
- `review-plan.json`：本轮实际验收路由、选择原因、隔离能力和结果状态；
- `review.json`：独立审查的机器可读结论，或人工语义验收待办状态；
- `acceptance-report.md`：宿主根据已校验的 `review.json` 渲染的人可读验收报告；
- `human-semantic-review.json`：仅人工路径完成语义审查后由宿主 create-new 写入的用户结论证据；独立 Agent 路径不生成。

每次验收路由后，以及人工语义审查或最终用户确认变化后，都要在任务目录根部覆盖刷新 `final-acceptance-report.md`。它是给人工查看的最新汇总入口，不替代上述轮次原始证据。

开发 Agent 不得直接写这些文件。独立验收 Agent 负责形成 findings 和结构化结论；没有隔离 Agent 能力时，宿主只生成明确的人工待办，不得代替独立审查填写 PASS。宿主保存、校验并确定性渲染 Markdown，不得改写审查语义。报告不得替代原始证据。

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

指纹不匹配、越界或未归属改动、受保护文件写入、测试失败、超时、未运行或证据不完整都不能进入语义验收。证据不完整或无法证明归属时使用 `NEED_HUMAN_REVIEW`，不要伪造成 P1。

## 选择验收能力

按以下顺序选择：

1. 优先启动一个与开发者分离的其他 Agent，产品不限，但必须是全新、只读且不继承开发上下文，记录 `reviewerKind=independent-agent`。
2. 没有其他产品但宿主支持子 Agent 时，启动当前宿主的全新验收子 Agent，只传入本节允许的审查输入，记录 `reviewerKind=fresh-subagent`。
3. 没有任何可证明隔离的新 Agent 或子 Agent 时，记录 `reviewerKind=human-review`、`isolation=not-available`，生成完整人工验收包并返回 `NEED_HUMAN_REVIEW`。

第二种 Agent 产品从来不是硬前提。只要是全新、只读、无开发上下文且不是开发 Agent，同一宿主产品创建的子 Agent 就是有效的独立验收者。多 Agent 能力不可用也不得阻止需求冻结、开发、机械门禁或报告生成；唯一限制是不能声称独立语义验收已通过。

独立 Agent 路线把注册表写为 `semanticReviewRoute=INDEPENDENT`、`needHumanReviewReason=null`，再根据审查结论更新 `semanticAcceptance`。人工路线则写 `semanticReviewRoute=HUMAN`、`needHumanReviewReason=ISOLATED_REVIEWER_UNAVAILABLE`；用户随后基于验收包完成语义审查，宿主按[人工语义审查证据](#人工语义审查证据)在锁内把结论、已检查 A ID、findings 和用户确认来源写入当前轮次的 `human-semantic-review.json`。通过映射为 `humanSemanticReviewOutcome=PASS`，不通过映射为 `FAIL`，而 `semanticAcceptance` 始终保持 `NEED_HUMAN_REVIEW`。这一步只决定能否进入最终确认，不得同时改写 `manualConfirmation`；用户仍需在下一阶段明确接受或拒绝整个交付。

每次验收先写入 `review-plan.json`，至少包含：`requested`、`route`、`status`、`selectedReviewer`、`reviewerKind`、`isolation` 和 `reason`。`route` 使用 `provided-result`、`host-agent`、`external-cli`、`external-cli-auto` 或 `human`。计划状态使用 `PLANNED`、`COMPLETED`、`UNAVAILABLE` 或 `BLOCKED`。这样用户无需猜测宿主是否启动了外部进程或子 Agent。

禁止使用、恢复、派生或 fork 任何需求分析和开发对话做验收。子 Agent 必须从空任务上下文启动；“是另一个会话”本身不足以证明隔离。

## 人工语义审查证据

只有机械门禁已经 `PASS`，且 `review-plan.json` 明确记录 `route=human`、`reviewerKind=human-review`、`isolation=not-available`，唯一缺口是没有可证明隔离的 reviewer 时，用户才能据完整验收包执行人工语义审查。测试、改动归属、证据完整性、工作区覆盖或注册表完整性仍有缺口时保持 `NEED_HUMAN_REVIEW` / `BLOCKED`，不得用人工 `PASS` 绕过。

人工结论固定 create-new 写到 `rounds/round-NN/human-semantic-review.json`。该文件由宿主写入，不是当前辅助 CLI 的原生产物，至少使用以下结构：

```json
{
  "schemaVersion": 1,
  "taskId": "account-export-full",
  "round": "round-02",
  "status": "PASS",
  "checkedAcceptanceIds": ["A-001"],
  "counts": { "p0": 0, "p1": 0, "p2": 1 },
  "findings": [
    {
      "id": "HF-001",
      "severity": "P2",
      "title": "可维护性建议",
      "relatedIds": ["A-001"],
      "file": "src/example.ext",
      "line": 42,
      "evidence": "用户基于最终 diff 与验收包确认不阻断交付",
      "impact": "不影响当前冻结验收",
      "remediation": "可作为后续建议处理"
    }
  ],
  "reviewPlan": { "path": "review-plan.json", "sha256": "<sha256>" },
  "review": { "path": "review.json", "sha256": "<sha256>" },
  "gateEvidence": { "path": "gate-evidence.json", "sha256": "<sha256>" },
  "userConfirmation": {
    "kind": "HUMAN_SEMANTIC_REVIEW",
    "confirmedBy": "user",
    "confirmedAt": "2026-07-15T18:00:00+08:00",
    "source": "<可审计的用户消息或交互引用>"
  },
  "createdAt": "2026-07-15T18:00:00+08:00"
}
```

`status` 只能是 `PASS` 或 `FAIL`。`checkedAcceptanceIds` 必须精确覆盖全部冻结 A ID；counts 必须与 findings 一致。`PASS` 不得含 P0/P1，`FAIL` 必须至少含一个 P0/P1；位置不适用时使用 `null`，不能虚构。三个输入摘要必须匹配当前轮次已落盘文件，用户确认来源必须可审计。

宿主取得根级锁并复核 registry / task revision 后，才以 create-new 写本文件和对应 lifecycle event，再更新 `humanSemanticReviewOutcome` 并按完整 projection 事务刷新报告。`PASS` 后另写一次“请求最终确认”迁移，把 `manualConfirmation` 从 `NOT_READY` 改为 `WAITING`；不能把两次用户决定合并成一个字段或一条 event。文件已存在时不得覆盖：字节和摘要完全一致可续提中断事务；缺失、摘要冲突、registry 已声称结论但证据不存在时停止并报告一致性错误。恢复任务时必须同时读取本文件、对应 lifecycle event 和摘要。

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

`status` 只能是 `PASS`、`FAIL` 或 `NEED_HUMAN_REVIEW`。Agent 路径的 `reviewer` 使用安全的小写 Agent 标识，`reviewerKind` 是 `independent-agent` 或 `fresh-subagent`，`isolation` 是 `fresh-read-only-no-development-context`。人工路径只能使用 `status=NEED_HUMAN_REVIEW`、`reviewer=null`、`reviewerKind=human-review`、`isolation=not-available`，不得输出 PASS/FAIL 冒充独立审查。finding ID 必须唯一，severity 只能是 P0/P1/P2，counts 必须与 findings 精确一致。P0/P1 至少关联一个冻结 R/A/T ID 或明确的 `SAFETY`；文件或行号不适用时使用 `null`，不得虚构位置。

## acceptance-report.md 模板

宿主从校验通过的 `review.json` 渲染：

```markdown
# <task-id> round-NN 独立语义验收报告 / 人工语义验收待办报告

## 结论
PASS / FAIL / NEED_HUMAN_REVIEW

## 审查身份
- reviewer: <agent-id> / 未启动（人工验收）
- reviewerKind: independent-agent / fresh-subagent / human-review
- isolation: fresh-read-only-no-development-context / not-available

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

`gated-loop accept` 根据当前轮次机械证据和 `review.json` 生成首次汇总；宿主随后从相同证据、可选的 `human-semantic-review.json`、注册表中的人工/最终确认状态及对应 lifecycle event 确定性重渲染。每轮覆盖旧汇总，但不得删除旧轮次报告：

```markdown
# <task-id> 最终验收报告

> task record revision: **<recordRevision>**
> 当前验收结论：**PASS / FAIL / NEED_HUMAN_REVIEW**
> 当前验收轮次：**round-NN**
> 人工语义审查：**NOT_RUN / PASS / FAIL**
> 最终用户确认：**NOT_READY / WAITING / ACCEPTED / REJECTED**

## 验收摘要
| 项目 | 结果 |
| --- | --- |
| 任务模式 | Full / Light |
| 机械门禁 | PASS / UNVERIFIED |
| 独立审查者 | <agent-id> / 未启动（人工验收） |
| 审查者类型 | independent-agent / fresh-subagent / human-review |
| 上下文隔离 | fresh-read-only-no-development-context / not-available |
| 人工语义审查 | NOT_RUN / PASS / FAIL；证据链接 |
| 最终用户确认 | NOT_READY / WAITING / ACCEPTED / REJECTED |
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
- 当前轮次 self-check-report.md、gate-evidence.json、review-plan.json、acceptance-report.md 和 review.json
- 人工路径完成后：[human-semantic-review.json](rounds/round-NN/human-semantic-review.json)
- 最新人工语义审查与最终确认 lifecycle event
```

独立路线的 `PASS` 对应注册表 `semanticReviewRoute=INDEPENDENT`、`needHumanReviewReason=null` 与 `WAITING_USER / WAITING_FOR_MANUAL_ACCEPTANCE`；`FAIL` 对应 `BLOCKED / BLOCKED_BY_P0_P1`。证据或归属不足使用相应 `needHumanReviewReason` 并进入 `BLOCKED / NEED_HUMAN_REVIEW`，不能误标成缺少 reviewer。只有“机械门禁 PASS，但没有隔离 reviewer”时，才使用 `semanticReviewRoute=HUMAN`、`needHumanReviewReason=ISOLATED_REVIEWER_UNAVAILABLE`，且人工语义审查 `PASS` 才能进入同一个 `WAITING_FOR_MANUAL_ACCEPTANCE`；它更新 `humanSemanticReviewOutcome` 而不是 `manualConfirmation`，`semanticAcceptance` 仍保持 `NEED_HUMAN_REVIEW`。证据、归属或机械门禁本身未解决时不得用人工语义 PASS 绕过。报告必须包含完整 findings，不能只给数量或链接，确保人工先看这一份即可理解结论。这些验收状态都不是任务终态。

机械自检 PASS 后运行：

```text
gated-loop accept --task <task-id> --round <NN>
```

CLI 默认不扫描或启动外部 Agent，而是写入人工验收计划和待办报告。宿主具备 Agent API 时，应按上面的优先级启动其他 Agent 或全新子 Agent，再把其完整 JSON 通过 `--review-result <file>` 或 `--review-result -` 交给 CLI 校验和落盘。只有用户明确指定 `--reviewer codex`、`--reviewer claude` 或 `--reviewer auto` 时，CLI 才使用可选外部适配器；`auto` 按 Codex、Claude 的顺序探测，选择和失败原因都写入 `review-plan.json`。外部 reviewer 在系统临时目录启动，不把项目目录设为工作目录，只接收经过门禁筛选的冻结文件、证据和 diff。无论哪条路径，`gated-loop accept` 都会先重新核对 self-check 指纹和真实 diff；仓库状态变化时返回 `NEED_HUMAN_REVIEW`。

## 结论判定

- 任一 P0 或 P1：`FAIL`。
- 没有 P0/P1，全部验收 ID 已检查且证据完整：`PASS`；可以包含 P2。
- 隔离、证据、测试、归属或检查覆盖无法证明：`NEED_HUMAN_REVIEW`。
- 没有隔离 Agent 或子 Agent 能力：`NEED_HUMAN_REVIEW`，但机械门禁结果继续有效，人工可据完整验收包审查；不得写成独立 PASS。
- `FAIL` 必须至少有一个 P0/P1；`PASS` 的 P0/P1 计数必须为零；`NEED_HUMAN_REVIEW` 必须说明缺失证据或隔离原因。

## 修复与人工验收

收到 `FAIL` 后，只把 P0/P1 finding、关联 ID、允许路径和必须重跑的测试交给新的隔离开发上下文，或通过明确 manual 方式交接。P2 默认不进入自动修复，除非用户明确授权。修复后重新运行全部机械门禁和独立验收，最多三轮。

进入 `WAITING_FOR_MANUAL_ACCEPTANCE` 前，先取得根级锁并复核 revision，以 create-new 持久化验收 evidence 与 lifecycle event，更新 `task-registry.json` 的 phase、验收计数、周期和下一动作并把投影标为 `PENDING`；随后刷新工作区总纲、`progress.md` 与任务根 `final-acceptance-report.md`，核对摘要后提交 projection ack 与最终总纲。用户需要追溯时再展示 `development-overview.md`、轮次级报告和原始 JSON 证据。只有用户明确完成最终确认后，且全部适用任务与 SOP 有完成证据，才能写 `TERMINAL / COMPLETED`。

用户没有直接接受，而是要求修改、调整目标、采纳 P2、补充建议或开始另一个需求时，不得把所有反馈一律视为修复，也不得立即创建新任务。先按 [post-acceptance-feedback.md](post-acceptance-feedback.md) 恢复原任务并区分：冻结授权未满足的同任务修复、需要重新冻结的当前任务修订、建议处置或明确的新任务。展示分类和原任务处置并取得用户确认后，才写入 `manual-feedback.json`；分类不明时保持 `WAITING_FOR_FEEDBACK_CONFIRMATION`。
