from __future__ import annotations

from .model_rendering_common import (
    ACCEPTANCE_PROJECTION_TEMPLATE,
    Any,
    KIND_TEXT,
    PROJECTION_TEMPLATE_VERSION,
    REVIEW_FINDING_STATUS_TEXT,
    WORKSPACE_CHANGE_STATUS_TEXT,
    WORK_ITEM_ACCEPTANCE_PROJECTION_TEMPLATE,
    WORK_ITEM_PROGRESS_PROJECTION_TEMPLATE,
    _markdown_code,
    _markdown_diff_block,
    _markdown_text,
    _payload_value_lines,
    _render_payload_markdown,
    _status_text,
    _table_row,
    _utc_plus_8,
    _work_item_terminal_node_id,
    confirmation_node_id,
    group_review_node_id,
    join_node_id,
    loop_node_id,
    review_node_id,
    task_review_node_id,
    work_item_projection_relative_path,
)
from .model_rendering_state import (
    _acceptance_state_table,
    _delivery_projection_status,
    _progress_state_row,
    _projection_state_values,
    _projection_states,
)
from .model_rendering_baseline import _interface_scalar


def _acceptance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "acceptance",
        "acceptanceCriteria",
        "successCriteria",
        "tests",
        "testRequirements",
    )
    return {key: payload[key] for key in keys if key in payload}

def _workspace_change_lines(result: dict[str, Any]) -> list[str]:
    raw_snapshots = result.get("workspaceChanges")
    if raw_snapshots is None:
        return []
    lines = [
        "#### 工作区变更证据",
        "",
        (
            "- 证据语义：以下内容是 Loop 提交结果时，相对冻结 "
            "`baseCommit` 的已验证 Git 工作区快照；它不声明这些变更只由"
            "当前 TASK、Loop 或 Delivery 产生。多个 Delivery 共享同一实际 "
            "workspace 时，快照可能包含其他 Delivery 的改动。"
        ),
    ]
    if not isinstance(raw_snapshots, list):
        return [
            *lines,
            "- `workspaceChanges` 格式无效，必须为 Controller 快照数组。",
        ]
    snapshots = [
        item for item in raw_snapshots if isinstance(item, dict)
    ]
    if not snapshots:
        return [
            *lines,
            "- 本 Loop 没有可采集的 READ_WRITE Git project scope。",
        ]
    for snapshot in snapshots:
        project_id = snapshot.get("projectId", "未声明")
        workspace_root = snapshot.get("workspaceRoot", "未声明")
        base_commit = snapshot.get("baseCommit", "未声明")
        head_commit = snapshot.get("headCommit", "未声明")
        snapshot_fingerprint = snapshot.get(
            "snapshotFingerprint",
            "未声明",
        )
        lines.extend(
            [
                "",
                f"##### 项目 {_markdown_code(project_id)}",
                "",
                f"- 实际工作区：{_markdown_code(workspace_root)}",
                f"- 冻结基线：{_markdown_code(base_commit)}",
                f"- 当前 HEAD：{_markdown_code(head_commit)}",
                (
                    "- 快照指纹："
                    f"{_markdown_code(snapshot_fingerprint)}"
                ),
                "",
                "###### 变更文件",
                "",
            ]
        )
        raw_files = snapshot.get("changedFiles")
        changed_files = (
            [item for item in raw_files if isinstance(item, dict)]
            if isinstance(raw_files, list)
            else []
        )
        if not changed_files:
            lines.append("- 相对冻结基线没有文件变化。")
        else:
            for item in changed_files:
                status = str(item.get("status", "UNKNOWN"))
                status_text = WORKSPACE_CHANGE_STATUS_TEXT.get(
                    status,
                    status,
                )
                previous_path = item.get("previousPath")
                path_text = _markdown_code(item.get("path", "未声明"))
                if previous_path is not None:
                    path_text = (
                        f"{_markdown_code(previous_path)} → {path_text}"
                    )
                lines.append(f"- {status_text}：{path_text}")
        diff = snapshot.get("diff")
        lines.extend(["", "###### Diff 快照", ""])
        if isinstance(diff, str) and diff:
            lines.extend(_markdown_diff_block(diff))
        else:
            lines.append("- 相对冻结基线没有可展示的文本 diff。")
        if snapshot.get("diffTruncated") is True:
            lines.extend(
                [
                    "",
                    (
                        "- Diff 已按 Controller 上限截断；截断前 UTF-8 "
                        f"字节数：{snapshot.get('diffByteCount', '未知')}。"
                    ),
                ]
            )
    return lines

def _task_workspace_change_sections(
    node: dict[str, Any],
    states: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    definition = node["definition"]
    if definition["kind"] != "TASK":
        return []
    stage_nodes = [
        ("TASK_LOOP", loop_node_id(definition["id"])),
    ]
    if node["reviewLoop"] is not None:
        stage_nodes.append(
            (
                "TASK_REVIEW_LOOP",
                task_review_node_id(definition["id"]),
            )
        )
    sections: list[tuple[str, dict[str, Any]]] = []
    for stage, node_id in stage_nodes:
        state = states.get(node_id)
        outcome = state.get("outcome") if state is not None else None
        result = outcome.get("result") if isinstance(outcome, dict) else None
        raw_snapshots = (
            result.get("workspaceChanges")
            if isinstance(result, dict)
            else None
        )
        if not isinstance(raw_snapshots, list):
            continue
        sections.extend(
            (stage, snapshot)
            for snapshot in raw_snapshots
            if isinstance(snapshot, dict)
        )
    return sections

def _patch_header_value(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")

def _render_task_workspace_changes_patch(
    node: dict[str, Any],
    states: dict[str, dict[str, Any]],
) -> str | None:
    sections = _task_workspace_change_sections(node, states)
    if not sections:
        return None
    lines = [
        "# delivery-graph workspace change snapshots",
        "#",
        (
            "# Snapshot evidence only: shared workspaces may contain changes "
            "from other Deliveries."
        ),
        "# This bundle does not assert exclusive TASK/Loop/Delivery ownership.",
    ]
    for position, (stage, snapshot) in enumerate(sections, start=1):
        lines.extend(
            [
                "",
                "# ============================================================",
                f"# Snapshot: {position}",
                f"# Stage: {_patch_header_value(stage)}",
                (
                    "# Project: "
                    f"{_patch_header_value(snapshot.get('projectId', 'unknown'))}"
                ),
                (
                    "# Workspace: "
                    f"{_patch_header_value(snapshot.get('workspaceRoot', 'unknown'))}"
                ),
                (
                    "# Frozen baseCommit: "
                    f"{_patch_header_value(snapshot.get('baseCommit', 'unknown'))}"
                ),
                (
                    "# Current HEAD: "
                    f"{_patch_header_value(snapshot.get('headCommit', 'unknown'))}"
                ),
                (
                    "# Snapshot fingerprint: "
                    + _patch_header_value(
                        snapshot.get("snapshotFingerprint", "unknown")
                    )
                ),
                (
                    "# Attribution: "
                    f"{_patch_header_value(snapshot.get('attribution', 'unknown'))}"
                ),
            ]
        )
        if snapshot.get("diffTruncated") is True:
            lines.append(
                "# Diff truncated by Controller; original UTF-8 bytes: "
                + _patch_header_value(
                    snapshot.get("diffByteCount", "unknown")
                )
            )
        diff = snapshot.get("diff")
        if isinstance(diff, str) and diff:
            lines.extend(["", diff.rstrip()])
        else:
            lines.extend(["", "# No displayable text diff in this snapshot."])
    return "\n".join(lines).rstrip() + "\n"

def _review_boundary_result_lines(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    task = result.pop("taskAcceptance", None)
    if isinstance(task, dict):
        lines.extend(
            [
                "#### TASK 验收结论",
                "",
                "| 验收点 | 状态 | 证据引用 |",
                "|---|---|---|",
            ]
        )
        for item in task.get("acceptanceChecks", []):
            if not isinstance(item, dict):
                continue
            lines.append(
                _table_row(
                    [
                        item.get("acceptancePoint", "未声明"),
                        item.get("status", "未声明"),
                        "、".join(item.get("evidenceRefs", [])) or "无",
                    ]
                )
            )
        lines.extend(
            [
                "",
                f"- 局部行为：{_markdown_text(task.get('localBehavior', '未声明'))}",
                f"- 公共契约：{_markdown_text(task.get('publicContract', '未声明'))}",
                f"- 定向回归：{_markdown_text(task.get('targetedRegression', '未声明'))}",
                f"- 结论：{_markdown_text(task.get('decision', '未声明'))}",
                f"- 理由：{_markdown_text(task.get('rationale', '未声明'))}",
            ]
        )
    group = result.pop("groupIntegration", None)
    if isinstance(group, dict):
        lines.extend(
            [
                "#### GROUP seam 验收结论",
                "",
                "| seam | 直接参与方 | 状态 | 证据引用 |",
                "|---|---|---|---|",
            ]
        )
        for item in group.get("seams", []):
            if not isinstance(item, dict):
                continue
            lines.append(
                _table_row(
                    [
                        item.get("seam", "未声明"),
                        "、".join(item.get("participants", [])) or "无",
                        item.get("status", "未声明"),
                        "、".join(item.get("evidenceRefs", [])) or "无",
                    ]
                )
            )
        lines.extend(
            [
                "",
                f"- 结论：{_markdown_text(group.get('decision', '未声明'))}",
                f"- 理由：{_markdown_text(group.get('rationale', '未声明'))}",
            ]
        )
    readiness = result.pop("deliveryReadiness", None)
    if isinstance(readiness, dict):
        lines.extend(
            [
                "#### Delivery Acceptance/Readiness 结论",
                "",
                "| 顶层验收点 | 责任节点 | 状态 | 证据引用 |",
                "|---|---|---|---|",
            ]
        )
        for item in readiness.get("requirementCoverage", []):
            if not isinstance(item, dict):
                continue
            lines.append(
                _table_row(
                    [
                        item.get("acceptancePoint", "未声明"),
                        "、".join(item.get("ownerRefs", [])) or "无",
                        item.get("status", "未声明"),
                        "、".join(item.get("evidenceRefs", [])) or "无",
                    ]
                )
            )
        accepted_risks = readiness.get("acceptedRisks", [])
        lines.extend(
            [
                "",
                "- 整体集成证据："
                + _markdown_text(
                    readiness.get("integrationEvidence", "未声明")
                ),
                "- 运行准备度："
                + _markdown_text(
                    readiness.get("operationalReadiness", "未声明")
                ),
                "- 阻断风险：无",
                "- 已接受风险："
                + (
                    "、".join(_markdown_text(item) for item in accepted_risks)
                    if isinstance(accepted_risks, list) and accepted_risks
                    else "无"
                ),
                f"- 结论：{_markdown_text(readiness.get('decision', '未声明'))}",
                f"- 理由：{_markdown_text(readiness.get('rationale', '未声明'))}",
            ]
        )
    validation = result.pop("validationDecision", None)
    if isinstance(validation, dict):
        reused = validation.get("reusedEvidenceRefs", [])
        reused_labels = [
            (
                f"{item.get('nodeId', '?')}@{item.get('attempt', '?')}:"
                f"{item.get('evidenceId', '?')}"
            )
            for item in reused
            if isinstance(item, dict)
        ]
        executed = validation.get("executedEvidenceRefs", [])
        triggers = validation.get("riskTriggers", [])
        lines.extend(
            [
                "",
                "#### 验证决策",
                "",
                f"- 决策：{_markdown_text(validation.get('decision', '未声明'))}",
                "- 复用证据："
                + (
                    "、".join(_markdown_text(item) for item in reused_labels)
                    or "无"
                ),
                "- 新执行证据："
                + (
                    "、".join(_markdown_text(item) for item in executed)
                    if isinstance(executed, list) and executed
                    else "无"
                ),
                "- 风险触发："
                + (
                    "、".join(_markdown_text(item) for item in triggers)
                    if isinstance(triggers, list) and triggers
                    else "无"
                ),
                f"- 理由：{_markdown_text(validation.get('rationale', '未声明'))}",
            ]
        )
    return lines

def _acceptance_result_lines(
    states: dict[str, dict[str, Any]],
    node_id: str,
    *,
    include_review_findings: bool = False,
) -> list[str]:
    lines = _acceptance_state_table(states, node_id)
    state = states.get(node_id)
    outcome = state.get("outcome") if state is not None else None
    result = outcome.get("result") if isinstance(outcome, dict) else None
    result_payload = dict(result) if isinstance(result, dict) else {}
    if include_review_findings:
        lines.extend(
            [
                "",
                *_review_finding_lines(result_payload),
            ]
        )
        result_payload.pop("reviewFindings", None)
    boundary_lines = _review_boundary_result_lines(result_payload)
    if boundary_lines:
        lines.extend(["", *boundary_lines])
    workspace_change_lines = _workspace_change_lines(result_payload)
    result_payload.pop("workspaceChanges", None)
    if workspace_change_lines:
        lines.extend(["", *workspace_change_lines])
    if result_payload:
        lines.extend(
            [
                "",
                "#### 结果证据",
                "",
                *_payload_value_lines(result_payload),
            ]
        )
    return lines

def _review_finding_lines(result: dict[str, Any]) -> list[str]:
    raw_findings = result.get("reviewFindings")
    if raw_findings is None:
        return [
            "#### Review 问题分级",
            "",
            "- 尚未提交结构化 P0/P1/P2 问题清单。",
        ]
    if not isinstance(raw_findings, list):
        return [
            "#### Review 问题分级",
            "",
            "- `reviewFindings` 格式无效，必须为问题数组。",
        ]
    findings = [
        item for item in raw_findings if isinstance(item, dict)
    ]
    counts = {severity: 0 for severity in ("P0", "P1", "P2")}
    unresolved = {severity: 0 for severity in ("P0", "P1")}
    rows: list[str] = []
    for finding in findings:
        severity = _interface_scalar(
            finding.get("severity"),
            fallback="未声明",
        ).upper()
        status = _interface_scalar(
            finding.get("status"),
            fallback="未声明",
        ).upper()
        if severity in counts:
            counts[severity] += 1
        if severity in unresolved and status != "RESOLVED":
            unresolved[severity] += 1
        rows.append(
            _table_row(
                [
                    severity,
                    _interface_scalar(
                        finding.get("summary"),
                        fallback="未提供问题说明",
                    ),
                    REVIEW_FINDING_STATUS_TEXT.get(status, "未声明"),
                    _interface_scalar(
                        finding.get("resolution"),
                        fallback="未提供处置说明",
                    ),
                    _interface_scalar(
                        finding.get("evidence"),
                        fallback="未提供证据",
                    ),
                ]
            )
        )
    lines = [
        "#### Review 问题分级",
        "",
        f"- P0：{counts['P0']} 项，未关闭 {unresolved['P0']} 项",
        f"- P1：{counts['P1']} 项，未关闭 {unresolved['P1']} 项",
        f"- P2：{counts['P2']} 项（必须逐项列示）",
        "",
    ]
    if not findings:
        return [*lines, "- 无 P0/P1/P2 问题。"]
    return [
        *lines,
        "| 级别 | 问题 | 状态 | 处置 | 证据 |",
        "|---|---|---|---|---|",
        *rows,
    ]

def render_delivery_acceptance(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    states = _projection_states(run)
    root_node = hierarchy["root"]
    root_definition = root_node["definition"]
    root_values = _projection_state_values(
        states,
        _work_item_terminal_node_id(root_node),
    )
    root_acceptance_path = work_item_projection_relative_path(
        hierarchy,
        root_definition["id"],
        "acceptance.md",
    )
    root_acceptance = "\n".join(
        [
            (
                "| 节点类型 | 节点标识 | 标题 | 当前进度 | "
                "结果摘要 | 验收记录 |"
            ),
            "|---|---|---|---|---|---|",
            _table_row(
                [
                    KIND_TEXT[root_definition["kind"]],
                    root_definition["id"],
                    root_definition["title"],
                    root_values["status"],
                    root_values["summary"],
                    f"[查看]({root_acceptance_path})",
                ],
                raw_indices={5},
            ),
        ]
    )
    delivery = hierarchy["delivery"]
    if delivery["reviewLoop"] is None:
        delivery_lines = [
            "### Delivery Acceptance/Readiness",
            "",
            "- LIGHT 保障档不创建 Delivery Acceptance/Readiness；TASK 定向验证"
            "完成后直接进入用户确认。",
        ]
    else:
        delivery_lines = [
            "### Delivery Acceptance/Readiness",
            "",
            "#### 验收与准备度输入",
            "",
            _render_payload_markdown(
                delivery["reviewLoop"]["payload"],
                heading_level=5,
            ),
            "",
            "#### 验收与准备度结果",
            "",
            *_acceptance_result_lines(
                states,
                review_node_id(delivery["id"]),
                include_review_findings=True,
            ),
        ]
    confirmation_lines = _acceptance_result_lines(
        states,
        confirmation_node_id(delivery["id"]),
    )
    return ACCEPTANCE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        acceptance_status="\n".join(
            _delivery_projection_status(
                hierarchy,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
                run=run,
            )
        ),
        root_acceptance=root_acceptance,
        delivery_acceptance="\n".join(delivery_lines).rstrip() + "\n",
        confirmation="\n".join(confirmation_lines),
    )

def _work_item_status_lines(
    definition: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None,
    graph_fingerprint: str | None,
    hierarchy_status: str | None,
    updated_at: str | None,
) -> list[str]:
    return [
        f"- 节点类型：{KIND_TEXT[definition['kind']]}",
        f"- 节点标识：{_markdown_text(definition['id'])}",
        f"- 标题：{_markdown_text(definition['title'])}",
        f"- 上级：{_markdown_text(definition['parentId'] or '无')}",
        f"- 层级状态：{_status_text(hierarchy_status or 'UNKNOWN')}",
        f"- 层级指纹：{hierarchy_fingerprint or '不可用'}",
        f"- 调度图指纹：{graph_fingerprint or '不可用'}",
        f"- 更新时间（UTC+8）：{_utc_plus_8(updated_at)}",
    ]

def render_work_item_progress(
    node: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    definition = node["definition"]
    states = _projection_states(run)
    progress_header = (
        "| 阶段 | 当前进度 | 执行代理 | 宿主观测模型 | "
        "认领身份 | 执行轮次 | "
        "最近更新时间（UTC+8） | 结果摘要 |"
    )
    progress_separator = "|---|---|---|---|---|---:|---|---|"
    if definition["kind"] == "TASK":
        task_progress_rows = [
            _progress_state_row(
                states,
                loop_node_id(definition["id"]),
                prefix=["TASK"],
            )
        ]
        if node["reviewLoop"] is not None:
            task_progress_rows.append(
                _progress_state_row(
                    states,
                    task_review_node_id(definition["id"]),
                    prefix=["TASK Review"],
                )
            )
        sections = "\n".join(
            [
                "## TASK Loop 状态",
                "",
                progress_header,
                progress_separator,
                *task_progress_rows,
            ]
        )
    else:
        child_rows = []
        for child in node["children"]:
            child_definition = child["definition"]
            child_id = child_definition["id"]
            terminal_id = _work_item_terminal_node_id(child)
            values = _projection_state_values(states, terminal_id)
            child_rows.append(
                _table_row(
                    [
                        KIND_TEXT[child_definition["kind"]],
                        child_id,
                        child_definition["title"],
                        values["status"],
                        values["updatedAt"],
                        f"[查看](children/{child_id}/progress.md)",
                    ],
                    raw_indices={5},
                )
            )
        group_stage_rows = [
            _progress_state_row(
                states,
                join_node_id(definition["id"]),
                prefix=["GROUP 完成点"],
            )
        ]
        if node["reviewLoop"] is not None:
            group_stage_rows.append(
                _progress_state_row(
                    states,
                    group_review_node_id(definition["id"]),
                    prefix=["GROUP seam Review"],
                )
            )
        sections = "\n".join(
            [
                "## 直接子节点进展",
                "",
                (
                    "| 节点类型 | 节点标识 | 标题 | 当前进度 | "
                    "最近更新时间（UTC+8） | 执行进展 |"
                ),
                "|---|---|---|---|---|---|",
                *child_rows,
                "",
                "## GROUP 阶段进展",
                "",
                progress_header,
                progress_separator,
                *group_stage_rows,
            ]
        )
    return WORK_ITEM_PROGRESS_PROJECTION_TEMPLATE.substitute(
        kind_text=KIND_TEXT[definition["kind"]],
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        item_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        progress_sections=sections,
    )

def render_work_item_acceptance(
    node: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
) -> str:
    definition = node["definition"]
    states = _projection_states(run)
    if definition["kind"] == "TASK":
        acceptance = _acceptance_payload(
            definition["execution"]["loop"]["payload"]
        )
        task_sections = []
        if _task_workspace_change_sections(node, states):
            task_sections.extend(
                [
                    "## 工作区变更附件",
                    "",
                    (
                        "- [打开工作区变更补丁]"
                        "(workspace-changes.patch)"
                    ),
                    (
                        "- 附件与下方 inline diff 均为 Controller 持久化的"
                        "提交时 workspace 快照，不代表当前 TASK/Delivery 的"
                        "独占归属。"
                    ),
                    "",
                ]
            )
        task_sections.extend([
            "## 已知验收输入",
            "",
            (
                _render_payload_markdown(
                    acceptance,
                    heading_level=3,
                )
                if acceptance
                else "- 未显式提供"
            ),
            "",
            "## TASK 结果与证据",
            "",
            *_acceptance_result_lines(
                states,
                loop_node_id(definition["id"]),
            ),
        ])
        if node["reviewLoop"] is None:
            task_sections.extend(
                [
                    "",
                    "## 独立 Review",
                    "",
                    "- LIGHT 保障档不创建 TASK Review Loop；由用户直接确认结果。",
                ]
            )
        else:
            task_sections.extend(
                [
                    "",
                    "## TASK Review 输入",
                    "",
                    _render_payload_markdown(
                        node["reviewLoop"]["payload"],
                        heading_level=3,
                    ),
                    "",
                    "## TASK Review 结果与证据",
                    "",
                    *_acceptance_result_lines(
                        states,
                        task_review_node_id(definition["id"]),
                        include_review_findings=True,
                    ),
                ]
            )
        sections = "\n".join(task_sections)
    else:
        child_rows = []
        for child in node["children"]:
            child_definition = child["definition"]
            child_id = child_definition["id"]
            terminal_id = _work_item_terminal_node_id(child)
            values = _projection_state_values(states, terminal_id)
            child_rows.append(
                _table_row(
                    [
                        KIND_TEXT[child_definition["kind"]],
                        child_id,
                        child_definition["title"],
                        values["status"],
                        values["summary"],
                        f"[查看](children/{child_id}/acceptance.md)",
                    ],
                    raw_indices={5},
                )
            )
        group_sections = [
            "## 直接子节点验收",
            "",
            (
                "| 节点类型 | 节点标识 | 标题 | 当前进度 | "
                "结果摘要 | 验收记录 |"
            ),
            "|---|---|---|---|---|---|",
            *child_rows,
            "",
            "## GROUP 完成点结果",
            "",
            *_acceptance_result_lines(
                states,
                join_node_id(definition["id"]),
            ),
            "",
        ]
        if node["reviewLoop"] is None:
            group_sections.extend(
                [
                    "- 本层没有独立 seam 验收边界；GROUP 完成点即本 GROUP 终态。",
                ]
            )
        else:
            group_sections.extend(
                [
                    "## GROUP seam Review 输入",
                    "",
                    _render_payload_markdown(
                        node["reviewLoop"]["payload"],
                        heading_level=3,
                    ),
                    "",
                    "## GROUP seam Review 结果与证据",
                    "",
                    *_acceptance_result_lines(
                        states,
                        group_review_node_id(definition["id"]),
                        include_review_findings=True,
                    ),
                ]
            )
        sections = "\n".join(group_sections)
    return WORK_ITEM_ACCEPTANCE_PROJECTION_TEMPLATE.substitute(
        kind_text=KIND_TEXT[definition["kind"]],
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        item_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        acceptance_sections=sections,
    )
