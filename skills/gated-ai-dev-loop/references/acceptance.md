# 独立验收

## 选择审查者

按以下顺序选择：

1. Codex 可用时，启动全新的只读 Codex 审查上下文。
2. Codex 不可用时，启动全新的、空上下文、只读 Claude 审查上下文。
3. 无法证明上下文全新或只读隔离时，返回 `NEED_HUMAN_REVIEW`。

禁止使用任何开发会话做验收，也不得恢复或派生开发对话作为审查上下文。开发者也是 Codex 时，可以使用另一个全新只读 Codex 验收，但必须证明上下文隔离。

## 审查输入

只提供：

- 冻结基线或 Light 简报；
- 存在时提供 `acceptance.json` 和 `tasks.json`；
- 真实 diff；
- 开发事实报告；
- 机械门禁证据；
- 审查者必须只读的规则。

不得提供隐藏的宿主推理、已放弃方案、早期对话或开发者自评。

## 审查提示词

```text
根据冻结授权审查给定仓库改动。
保持只读，不得修复文件、修改验收或补猜缺失证据。
检查每个验收项并引用其 ID。
只返回 PASS、FAIL 或 NEED_HUMAN_REVIEW。
FAIL 只列出关联需求、验收或任务 ID 的具体阻断项。
PASS 不授权提交、推送、合并、发布或最终验收。
```

## 结果格式

```json
{
  "status": "PASS",
  "reviewer": "codex",
  "isolation": "fresh-read-only",
  "checkedAcceptanceIds": ["A-001"],
  "blockers": [],
  "notes": []
}
```

`reviewer` 只能是 `codex` 或 `claude`。`checkedAcceptanceIds` 必须包含全部冻结验收 ID。`FAIL` 必须包含 blocker；证据缺失、改动归属不清或无法证明隔离时使用 `NEED_HUMAN_REVIEW`。

## 修复循环

收到 `FAIL` 后，只把以下内容交给同运行时的全新隔离开发上下文，或通过明确的 manual 方式交给指定开发者：

- blocker 文本；
- 关联 ID；
- 允许路径；
- 必须重跑的测试。

完成修复后重新执行全部机械门禁和语义验收。最多修复三轮，随后请求人工处理。

审查者返回 `PASS` 后，向用户展示冻结授权、真实改动路径、测试证据和审查者身份。只有用户明确确认后才能标记完成。

进入人工验收前，由宿主按 `tracking.md` 更新 `progress.md`，并同时展示 `development-overview.md`、当前任务进度、最新机械门禁和独立审查证据。用户拒绝时记录关联 ID 和原因，再进入修复轮次。
