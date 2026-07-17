from __future__ import annotations

import json
import posixpath
from typing import Any

from .constants import SCHEMA_VERSION

GOVERNANCE_DIRECTORY = ".hierarchical-delivery-governance"
WORK_ITEMS_DIRECTORY = "work-items"


def human_status(value: object) -> str:
    return {
        "DELIVERY": "交付",
        "CAPABILITY": "能力",
        "TASK": "任务",
        "PREPARED": "等待开发方案评审",
        "WAITING_FOR_DEVELOPMENT_MODE_SELECTION": "等待选择开发方式",
        "FROZEN": "已冻结",
        "CLAIMED": "开发中",
        "IMPLEMENTED": "等待门禁验收",
        "BLOCKED": "已阻断",
        "VERIFIED": "门禁已通过",
        "NOT_READY": "尚未就绪",
        "WAITING_FOR_INDEPENDENT_REVIEW": "等待独立验收",
        "WAITING_FOR_USER_CONFIRMATION": "等待用户确认",
        "COMPLETED": "已完成",
        "NOT_RUN": "未运行",
        "PASS": "通过",
        "FAIL": "未通过",
    }.get(value, str(value) if value is not None else "无")


def item_human_artifacts(item_id: str, acceptance_report: dict[str, Any] | None = None) -> dict[str, str | None]:
    base = posixpath.join(GOVERNANCE_DIRECTORY, WORK_ITEMS_DIRECTORY, item_id)
    return {
        "overview": posixpath.join(base, "overview.md"),
        "developmentReview": posixpath.join(base, "development-review.md"),
        "baseline": posixpath.join(base, "baseline.md"),
        "progress": posixpath.join(base, "progress.md"),
        "acceptanceReport": acceptance_report.get("markdownPath") if acceptance_report else None,
    }


def next_action(entry: dict[str, Any]) -> str:
    if entry["stage"] == "WAITING_FOR_BASELINE_CONFIRMATION":
        return "人工评审 development-review.md；需要修改则重新起草，确认无误后按当前指纹执行 freeze-item。"
    if entry["status"] == "WAITING_FOR_DEVELOPMENT_MODE_SELECTION":
        return "人工选择 active 或 manual 开发方式。"
    if entry["status"] == "FROZEN" and entry["kind"] == "TASK":
        return "等待依赖满足后执行 dispatch-task。"
    if entry["status"] == "FROZEN":
        return "继续准备已计划子级，或在分解封口且子级通过后运行聚合门禁。"
    if entry["status"] == "CLAIMED":
        return "等待开发结果按 operationId 写回。"
    if entry["status"] == "IMPLEMENTED":
        return "形成严格 evidence 并执行 accept-item 门禁验收。"
    if entry["status"] == "BLOCKED":
        return "处理阻断后按当前指纹显式 retry-item。"
    if entry["status"] == "VERIFIED" and entry["parentId"] is None:
        acceptance = entry.get("acceptance")
        if acceptance and acceptance["status"] == "WAITING_FOR_INDEPENDENT_REVIEW":
            return "执行独立验收或记录人工验收接受。"
        if acceptance and acceptance["status"] == "WAITING_FOR_USER_CONFIRMATION":
            return "等待用户最终确认。"
    return "等待父级聚合门禁。" if entry["status"] == "VERIFIED" else "查看当前状态与门禁证据。"


def render_workspace_overview(registry: dict[str, Any]) -> str:
    lines = [
        "# 工作项总览",
        "",
        "> 本文件是面向用户和协作者的可读投影；机器权威为 `work-item-registry.json`。",
        f"> 注册表版本：{registry['revision']}",
        f"> 当前焦点：{registry['currentFocus']['workItemId'] or '无'}",
        "",
        "| 工作项 | 类型 | 门禁等级 | 父级 | 当前状态 | 开发评审 | 开发方式 | 最终验收 | 直接子级 | 全部后代 | 门禁 | 认领者 | 验收报告 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in sorted(registry["workItems"], key=lambda value: value["id"]):
        report = (
            f"[查看]({posixpath.relpath(item['acceptanceReport']['markdownPath'], GOVERNANCE_DIRECTORY)})"
            if item.get("acceptanceReport")
            else "尚未生成"
        )
        acceptance = item.get("acceptance") if item["parentId"] is None else None
        item_link = f"[{item['id']}]({posixpath.join(WORK_ITEMS_DIRECTORY, item['id'], 'overview.md')})"
        review = f"[查看]({posixpath.join(WORK_ITEMS_DIRECTORY, item['id'], 'development-review.md')})"
        mode = item["developmentMode"]["mode"] if item.get("developmentMode") else "不适用"
        lines.append(
            f"| {item_link} | {human_status(item['kind'])} | {item['gateLevel']} | {item['parentId'] or '无'} | "
            f"{human_status(item['status'])} | {review} | {mode} | "
            f"{human_status(acceptance['status']) if acceptance else '不适用'} | "
            f"{item['progress']['directChildren']['verified']}/{item['progress']['directChildren']['total']} | "
            f"{item['progress']['descendants']['verified']}/{item['progress']['descendants']['total']} | "
            f"{human_status(item['gate']['status'])} | {item['claim']['owner'] if item.get('claim') else '无'} | {report} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_item_overview(entry: dict[str, Any]) -> str:
    return "\n".join([
        f"# {entry['id']} 工作项概览",
        "",
        f"- 类型：{entry['kind']}",
        f"- 门禁等级：{entry['gateLevel']}",
        f"- 权限性质：{entry['authorityKind']}",
        f"- 父级：{entry['parentId'] or '无'}",
        "- 基线：[baseline.md](baseline.md)",
        "- 开发评审：[development-review.md](development-review.md)",
        "- 结构化开发计划：[development-plan.json](development-plan.json)",
        "- 进度：[progress.md](progress.md)",
        f"- 父契约指纹：{entry['parentContractFingerprint'] or '无'}",
        f"- 子级：{', '.join(entry['childIds']) or '无'}",
        f"- 验收报告：{'[acceptance-report.md](acceptance-report.md)' if entry.get('acceptanceReport') else '尚未生成'}",
        f"- 下一步：{next_action(entry)}",
        "",
    ])


def render_item_progress(entry: dict[str, Any]) -> str:
    acceptance = entry.get("acceptance") if entry["parentId"] is None else None
    mode = entry["developmentMode"]["mode"] if entry.get("developmentMode") else "未选择"
    claim = f"{entry['claim']['owner']} / {entry['claim']['operationId']}" if entry.get("claim") else "无"
    return "\n".join([
        f"# {entry['id']} 进度",
        "",
        f"- 记录版本：{entry['recordRevision']}",
        f"- 阶段：{entry['stage']}",
        f"- 当前状态：{human_status(entry['status'])}",
        f"- 门禁等级：{entry['gateLevel']}",
        f"- 最终验收：{human_status(acceptance['status']) if acceptance else '不适用'}",
        f"- 门禁：{human_status(entry['gate']['status'])}",
        f"- 开发方式：{mode}",
        f"- 认领：{claim}",
        f"- 直接子级：{entry['progress']['directChildren']['verified']}/{entry['progress']['directChildren']['total']} 已验证；"
        f"{entry['progress']['directChildren']['blocked']} 阻断；{entry['progress']['directChildren']['active']} 活动",
        f"- 全部后代：{entry['progress']['descendants']['verified']}/{entry['progress']['descendants']['total']} 已验证；"
        f"{entry['progress']['descendants']['blocked']} 阻断；{entry['progress']['descendants']['active']} 活动",
        f"- 验收报告：{'[acceptance-report.md](acceptance-report.md)' if entry.get('acceptanceReport') else '尚未生成'}",
        f"- 下一步：{next_action(entry)}",
        f"- 更新时间：{entry['updatedAt']}",
        "",
    ])


def report_status(entry: dict[str, Any]) -> str:
    acceptance = entry.get("acceptance") if entry["parentId"] is None else None
    if acceptance and acceptance["status"] != "NOT_READY":
        return acceptance["status"]
    if entry["status"] == "IMPLEMENTED":
        return "WAITING_FOR_GATE"
    if entry["status"] in {"BLOCKED", "VERIFIED"}:
        return entry["status"]
    return "NOT_READY"


def render_acceptance_report(report: dict[str, Any]) -> str:
    status_text = {
        "NOT_READY": "尚未就绪",
        "WAITING_FOR_GATE": "等待门禁验收",
        "BLOCKED": "已阻断",
        "VERIFIED": "门禁已通过",
        "WAITING_FOR_INDEPENDENT_REVIEW": "等待独立验收",
        "WAITING_FOR_USER_CONFIRMATION": "等待用户确认",
        "COMPLETED": "已完成",
    }
    gate_text = {"NOT_RUN": "未运行", "PASS": "通过", "FAIL": "未通过"}
    gate_artifact = report["gate"].get("artifact")
    lines = [
        f"# 验收报告：{report['workItem']['title']}",
        "",
        f"- 工作项：{report['workItem']['id']}",
        f"- 类型：{report['workItem']['kind']}",
        f"- 门禁等级：{report['workItem']['gateLevel']}",
        f"- 基线指纹：{report['workItem']['baselineFingerprint']}",
        f"- 最终状态：{status_text.get(report['status'], report['status'])}",
        f"- 门禁结论：{gate_text.get(report['gate']['status'], report['gate']['status'])}",
        f"- 生成时间：{report['generatedAt']}",
        "",
        "## 验收项",
        "",
        "| 编号 | 预期结果 | 结论 | 证据 |",
        "| --- | --- | --- | --- |",
    ]
    results = {item["id"]: item for item in (gate_artifact or {}).get("acceptance", [])}
    for item in report["criteria"]:
        result = results.get(item["id"])
        conclusion = gate_text.get(result["status"], result["status"]) if result else "待验收"
        lines.append(f"| {item['id']} | {item['expectedResult']} | {conclusion} | {result['evidence'] if result else '无'} |")
    plan = report["developmentPlan"]
    lines.extend(["", "## 冻结开发方案", ""])
    if "interfaces" in plan:
        contracts = "；".join(f"{item['action']} {item['kind']} {item['name']}" for item in plan["interfaces"])
        lines.extend([f"- 开发目的：{plan['purpose']}", f"- 接口契约：{contracts or '无接口改动'}"])
    else:
        children = "；".join(f"{item['id']}：{item['purpose']}" for item in plan["childPlans"])
        lines.extend([f"- 协调目的：{plan['purpose']}", f"- 子级内容：{children}"])
    tests = (gate_artifact or {}).get("tests") or ((report.get("development") or {}).get("artifact") or {}).get("tests", [])
    lines.extend(["", "## 测试结果", ""])
    if not tests:
        lines.append("- 尚无测试证据。")
    for result in tests:
        argv = json.dumps(result["argv"], ensure_ascii=False, separators=(",", ":"))
        summary = result.get("summary", f"Tests run: {result.get('testsRun', '未记录')}")
        lines.append(f"- `{argv}`：退出码 {result['exitCode']}；{summary}")
    scope = (gate_artifact or {}).get("scope", {})
    lines.extend(["", "## 变更范围", ""])
    if "fileChanges" in plan:
        planned = [item["path"] for item in plan["fileChanges"]]
        actual = scope.get("changedFiles") or (((report.get("development") or {}).get("artifact") or {}).get("changedFiles", []))
        actual_portable = [item.replace("\\", "/") for item in actual]
        lines.extend([
            f"- 冻结计划文件：{'、'.join(planned) or '无'}",
            f"- 计划外文件：{'、'.join(item for item, portable in zip(actual, actual_portable) if portable not in planned) or '无'}",
            f"- 计划中尚未观察到的文件：{'、'.join(item for item in planned if item not in actual_portable) or '无'}",
        ])
    else:
        lines.extend([
            f"- 冻结子级计划：{'、'.join(item['id'] for item in plan['childPlans'])}",
            f"- 冻结共享契约：{'、'.join(item['name'] for item in plan['sharedContracts']) or '无'}",
        ])
    development_files = (((report.get("development") or {}).get("artifact") or {}).get("changedFiles", []))
    lines.extend([
        f"- 已记录变更：{'、'.join(scope.get('changedFiles') or development_files) or '无'}",
        f"- 范围外变更：{'、'.join(scope.get('outOfScopeFiles', [])) or '无'}",
        "",
        "## 问题与建议",
        "",
    ])
    findings = (gate_artifact or {}).get("findings", {})
    lines.extend([
        f"- P0：{len(findings.get('p0', []))}",
        f"- P1：{len(findings.get('p1', []))}",
        f"- P2：{len(findings.get('p2', []))}",
        "",
        "## 独立验收",
        "",
        f"- {report['review']['artifact']['reviewer']}：{report['review']['artifact']['verdict']}" if report.get("review") else "- 尚未完成。",
        "",
        "## 用户确认",
        "",
        f"- {report['userConfirmation']['artifact']['confirmedBy']}：已确认" if report.get("userConfirmation") else "- 尚未确认。",
        "",
    ])
    return "\n".join(lines)


def render_task_handoff(context: dict[str, Any]) -> str:
    operation_id = context["operation"]["operationId"] if context.get("operation") else "<claim-required>"
    result_template = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "TASK_RESULT",
        "taskId": context["task"]["id"],
        "operationId": operation_id,
        "status": "IMPLEMENTED|BLOCKED",
        "summary": "<development facts>",
        "changedFiles": [],
        "tests": [{"argv": ["<exact frozen argv>"], "exitCode": 0, "testsRun": 0}],
        "blockers": [],
    }
    display_operation = context["operation"]["operationId"] if context.get("operation") else "尚未认领；不得开始开发"
    pretty = lambda value: json.dumps(value, ensure_ascii=False, indent=2, separators=(",", ": "))
    return "\n".join([
        "请在一个全新的开发会话中实现以下已冻结 Task。",
        "",
        f"Task：{context['task']['id']}",
        f"Baseline fingerprint：{context['task']['baselineFingerprint']}",
        f"Gate level：{context['gateLevel']}",
        f"Development mode：{context['developmentMode']}",
        f"Operation ID：{display_operation}",
        "",
        "以下冻结上下文是完整权威。不要重新分析原始需求、改变验收标准或继承其他会话的隐含假设。",
        "",
        "执行规则：",
        "- 只实现这个冻结的叶子 Task，并且只写入 Scope 中的路径。",
        "- 不修改 baseline、registry、进度投影、`.git/**` 或外部状态。",
        "- 运行列出的测试命令，只报告真实存在的证据。",
        "- 不提交、推送、发布，也不得自行报告 PASS。",
        "- 最终只返回 IMPLEMENTED 或 BLOCKED，并携带当前 Operation ID、变更文件和测试事实。",
        "- 宿主必须用 task-result 回收结果；返回开发结果后必须继续验收，IMPLEMENTED 不是完成状态。",
        "- 门禁通过后仍需独立验收、生成用户验收报告并取得用户确认。",
        "",
        "结果返回格式（由治理宿主保存为 evidence，并用相同 Operation ID 执行 task-result）：",
        "```json",
        pretty(result_template),
        "```",
        "",
        "冻结上下文：",
        "```json",
        pretty(context),
        "```",
        "",
    ])
