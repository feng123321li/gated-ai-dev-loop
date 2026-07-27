from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .fs_safe import read_regular_file, safe_path
from .graph_model import (
    compile_delivery_graph,
    compile_runtime_policy,
    execution_node_id,
    gate_node_id,
    graph_fingerprint,
    graph_summary,
)
from .graph_projections import (
    render_delivery_graph,
    render_runtime_policy_summary,
    render_state_transition_graph,
)
from .graph_runtime import hierarchy_root_entry, retry_budget
from .svg_graphs import render_delivery_graph_svg_assets, render_runtime_policy_svg_assets
from .model import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    raw_definition,
    render_hierarchy_plan,
    validate_hierarchy_definition,
    work_item_baseline_fingerprint,
    work_item_contract_fingerprint,
)
from .projections import (
    item_human_artifacts,
    render_claude_code_auto_handoff,
    render_host_automation,
    render_requirement_handoff,
    render_requirement_handoff_command,
)
from .repository import (
    GOVERNANCE_DIRECTORY,
    WORK_ITEMS_DIRECTORY,
    GovernanceRepository,
    entry_from_definition,
    timestamp,
)


def _state(definition: dict[str, Any], host_runtime: str, at: str) -> dict[str, Any]:
    baseline = work_item_baseline_fingerprint(definition)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": definition["id"],
        "stage": "WAITING_FOR_BASELINE_CONFIRMATION",
        "baselineFingerprint": baseline,
        "contractFingerprint": work_item_contract_fingerprint(definition),
        "parentContractFingerprint": definition["parentContractFingerprint"],
        "hostRuntime": host_runtime,
        "createdAt": at,
        "frozenAt": None,
        "baselineRevision": 1,
        "revisedAt": None,
        "review": {
            "schemaVersion": SCHEMA_VERSION,
            "status": "WAITING_FOR_HUMAN_REVIEW",
            "baselineFingerprint": baseline,
            "reviewedBy": None,
            "reviewedAt": None,
        },
    }


def _hierarchy_records(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(node: dict[str, Any], package_path: str) -> None:
        definition = node["definition"]
        records.append({
            "node": node,
            "definition": definition,
            "packagePath": package_path,
        })
        for child in node["children"]:
            visit(child, f"{package_path}/children/{child['definition']['id']}")

    root_id = hierarchy["root"]["definition"]["id"]
    visit(hierarchy["root"], f"{WORK_ITEMS_DIRECTORY}/{root_id}")
    return records


def _hierarchy_state(
    hierarchy: dict[str, Any],
    states: dict[str, dict[str, Any]],
    *,
    status: str,
    at: str | None = None,
) -> dict[str, Any]:
    root_id = hierarchy["root"]["definition"]["id"]
    hierarchy_value = hierarchy_fingerprint(hierarchy)
    graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_value,
    )
    graph_value = graph_fingerprint(graph)
    value = {
        "schemaVersion": SCHEMA_VERSION,
        "rootId": root_id,
        "stage": "BASELINE_FROZEN" if status == "APPROVED" else "WAITING_FOR_BASELINE_CONFIRMATION",
        "hierarchyFingerprint": hierarchy_value,
        "graphFingerprint": graph_value,
        "items": [
            {
                "id": record["definition"]["id"],
                "kind": record["definition"]["kind"],
                "parentId": record["definition"]["parentId"],
                "packagePath": record["packagePath"],
                "baselineFingerprint": states[record["definition"]["id"]]["baselineFingerprint"],
            }
            for record in _hierarchy_records(hierarchy)
        ],
        "review": {
            "schemaVersion": SCHEMA_VERSION,
            "status": status,
            "hierarchyFingerprint": hierarchy_value,
            "graphFingerprint": graph_value,
            "reviewedBy": "user" if status == "APPROVED" else None,
            "reviewedAt": at if status == "APPROVED" else None,
        },
    }
    return value


def _hierarchy_packages(
    repository: GovernanceRepository,
    hierarchy: dict[str, Any],
    states: dict[str, dict[str, Any]],
    hierarchy_state: dict[str, Any],
) -> list[tuple[Path, dict[str, str]]]:
    records = _hierarchy_records(hierarchy)
    root_path = records[0]["packagePath"]
    graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_state["hierarchyFingerprint"],
    )
    root_plan = (
        render_hierarchy_plan(hierarchy, states, hierarchy_state)
        + "\n"
        + render_runtime_policy_summary(graph)
    )
    packages: list[tuple[Path, dict[str, str]]] = []
    for index, record in enumerate(records):
        relative = Path(record["packagePath"]).relative_to(root_path)
        files = repository.package_files(
            record["definition"],
            states[record["definition"]["id"]],
            human_plan=root_plan if index == 0 else None,
        )
        if index == 0:
            files["execution-graph.md"] = render_delivery_graph(
                graph,
                graph_fingerprint=hierarchy_state["graphFingerprint"],
            )
            files.update(render_delivery_graph_svg_assets(graph))
        packages.append((relative, files))
    return packages


def _hierarchy_from_registry(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    root_entry: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], Path]:
    if root_entry["parentId"] is not None:
        fail("WORK_ITEM_HIERARCHY_ROOT_REQUIRED", "Hierarchy operations require a root work item")
    root_target = repository.item_path(root_entry)
    hierarchy_state = repository.read_hierarchy_state(root_entry["id"])
    expected_keys = {
        "schemaVersion", "rootId", "stage", "hierarchyFingerprint", "graphFingerprint",
        "items", "review",
    }
    if (
        set(hierarchy_state) != expected_keys
        or hierarchy_state.get("schemaVersion") != SCHEMA_VERSION
        or hierarchy_state.get("rootId") != root_entry["id"]
        or not isinstance(hierarchy_state.get("items"), list)
        or not isinstance(hierarchy_state.get("review"), dict)
    ):
        fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy state is invalid")

    states: dict[str, dict[str, Any]] = {}

    def build(entry: dict[str, Any]) -> dict[str, Any]:
        definition, state, _ = repository.read_package(registry, entry)
        states[entry["id"]] = state
        return {
            "definition": raw_definition(definition),
            "children": [
                build(repository.item_by_id(registry, child_id))
                for child_id in sorted(entry["childIds"])
            ],
        }

    hierarchy = validate_hierarchy_definition({
        "schemaVersion": SCHEMA_VERSION,
        "root": build(root_entry),
    })
    records = _hierarchy_records(hierarchy)
    expected_items = [
        {
            "id": record["definition"]["id"],
            "kind": record["definition"]["kind"],
            "parentId": record["definition"]["parentId"],
            "packagePath": record["packagePath"],
            "baselineFingerprint": states[record["definition"]["id"]]["baselineFingerprint"],
        }
        for record in records
    ]
    review = hierarchy_state["review"]
    review_valid = (
        set(review) == {
            "schemaVersion", "status", "hierarchyFingerprint", "graphFingerprint",
            "reviewedBy", "reviewedAt",
        }
        and review.get("schemaVersion") == SCHEMA_VERSION
        and review.get("hierarchyFingerprint") == hierarchy_state.get("hierarchyFingerprint")
        and review.get("graphFingerprint") == hierarchy_state.get("graphFingerprint")
        and (
            (
                hierarchy_state.get("stage") == "WAITING_FOR_BASELINE_CONFIRMATION"
                and review.get("status") == "WAITING_FOR_HUMAN_REVIEW"
                and review.get("reviewedBy") is None
                and review.get("reviewedAt") is None
            )
            or (
                hierarchy_state.get("stage") == "BASELINE_FROZEN"
                and review.get("status") == "APPROVED"
                and review.get("reviewedBy") == "user"
                and isinstance(review.get("reviewedAt"), str)
            )
        )
    )
    if (
        hierarchy_state["items"] != expected_items
        or hierarchy_state["hierarchyFingerprint"] != hierarchy_fingerprint(hierarchy)
        or hierarchy_state["graphFingerprint"] != graph_fingerprint(
            compile_delivery_graph(
                hierarchy,
                hierarchy_fingerprint=hierarchy_state["hierarchyFingerprint"],
            )
        )
        or not review_valid
    ):
        fail("WORK_ITEM_HIERARCHY_CHANGED", "Hierarchy package changed after preparation")
    expected_graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_state["hierarchyFingerprint"],
    )
    expected_plan = (
        render_hierarchy_plan(hierarchy, states, hierarchy_state)
        + "\n"
        + render_runtime_policy_summary(expected_graph)
    ).encode("utf-8")
    try:
        actual_plan = read_regular_file(root_target, root_target / "development-plan.md")
    except Exception:
        fail("WORK_ITEM_HIERARCHY_PLAN_CHANGED", "Hierarchy development plan is missing or unreadable")
    if actual_plan != expected_plan:
        fail("WORK_ITEM_HIERARCHY_PLAN_CHANGED", "Hierarchy development plan changed after preparation")
    stored_graph = repository.read_graph_definition(root_entry["id"])
    if (
        stored_graph["graphFingerprint"] != hierarchy_state["graphFingerprint"]
        or stored_graph["graph"]["hierarchyFingerprint"] != hierarchy_state["hierarchyFingerprint"]
    ):
        fail("DELIVERY_GRAPH_CHANGED", "Delivery graph changed after preparation")
    graph_projection = root_target / "execution-graph.md"
    try:
        actual_graph_projection = read_regular_file(root_target, graph_projection).decode("utf-8")
    except Exception:
        fail("DELIVERY_GRAPH_PROJECTION_CHANGED", "Delivery graph projection is missing or unreadable")
    expected_graph_projection = render_delivery_graph(
        stored_graph["graph"],
        graph_fingerprint=stored_graph["graphFingerprint"],
        run=repository.read_graph_run(root_entry["id"], allow_missing=True),
    )
    if actual_graph_projection != expected_graph_projection:
        fail("DELIVERY_GRAPH_PROJECTION_CHANGED", "Delivery graph projection changed after preparation")
    runtime_policy = compile_runtime_policy()
    state_projection = repository.governance_root / "state-transition-graph.md"
    try:
        actual_state_projection = read_regular_file(
            repository.governance_root,
            state_projection,
        ).decode("utf-8")
    except Exception:
        fail(
            "DELIVERY_GRAPH_PROJECTION_CHANGED",
            "State transition graph projection is missing or unreadable",
        )
    expected_state_projection = render_state_transition_graph(runtime_policy)
    if actual_state_projection != expected_state_projection:
        fail(
            "DELIVERY_GRAPH_PROJECTION_CHANGED",
            "State transition graph projection changed after preparation",
        )
    for relative_path, expected_asset in render_delivery_graph_svg_assets(
        stored_graph["graph"]
    ).items():
        asset_path = root_target / relative_path
        try:
            actual_asset = read_regular_file(root_target, asset_path).decode("utf-8")
        except Exception:
            fail(
                "DELIVERY_GRAPH_PROJECTION_CHANGED",
                f"Graph visual projection is missing or unreadable: {relative_path}",
            )
        if actual_asset != expected_asset:
            fail(
                "DELIVERY_GRAPH_PROJECTION_CHANGED",
                f"Graph visual projection changed after preparation: {relative_path}",
            )
    for relative_path, expected_asset in render_runtime_policy_svg_assets(
        runtime_policy
    ).items():
        asset_path = repository.governance_root / relative_path
        try:
            actual_asset = read_regular_file(
                repository.governance_root,
                asset_path,
            ).decode("utf-8")
        except Exception:
            fail(
                "DELIVERY_GRAPH_PROJECTION_CHANGED",
                f"Runtime policy visual projection is missing or unreadable: {relative_path}",
            )
        if actual_asset != expected_asset:
            fail(
                "DELIVERY_GRAPH_PROJECTION_CHANGED",
                f"Runtime policy visual projection changed after preparation: {relative_path}",
            )
    return hierarchy, states, hierarchy_state, root_target


def prepare_hierarchy(
    *,
    root: str,
    hierarchy: dict[str, Any],
    host_runtime: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Prepare one complete requirement tree and its single human plan."""
    from .host_runtime import require_host_runtime

    normalized = validate_hierarchy_definition(hierarchy)
    runtime = require_host_runtime(host_runtime)
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    records = _hierarchy_records(normalized)
    root_id = records[0]["definition"]["id"]
    with repository.transaction() as registry:
        existing_by_id = {item["id"]: item for item in registry["workItems"]}
        existing_root = existing_by_id.get(root_id)
        replace = existing_root is not None
        old_ids: set[str] = set()
        if replace:
            if existing_root["parentId"] is not None:
                fail("WORK_ITEM_HIERARCHY_ROOT_REQUIRED", f"{root_id} is not a hierarchy root")
            old_hierarchy, old_states, old_state, old_target = _hierarchy_from_registry(
                repository,
                registry,
                existing_root,
            )
            if old_state["stage"] != "WAITING_FOR_BASELINE_CONFIRMATION":
                fail("WORK_ITEM_SOURCE_CHANGED", "A frozen hierarchy cannot be prepared again; plan a complete new requirement")
            old_ids = {node["definition"]["id"] for node in iter_hierarchy_nodes(old_hierarchy)}
            if hierarchy_fingerprint(old_hierarchy) == hierarchy_fingerprint(normalized):
                stored_graph = repository.read_graph_definition(root_id)
                return {
                    "created": False,
                    "revised": False,
                    "idempotent": True,
                    "rootId": root_id,
                    "itemIds": [record["definition"]["id"] for record in records],
                    "stage": old_state["stage"],
                    "hierarchyFingerprint": old_state["hierarchyFingerprint"],
                    "graphFingerprint": stored_graph["graphFingerprint"],
                    "graphSummary": graph_summary(stored_graph["graph"]),
                    "baselineFingerprints": {
                        item_id: state["baselineFingerprint"] for item_id, state in old_states.items()
                    },
                    "artifactDir": str(old_target),
                    "humanArtifacts": {
                        "developmentPlan": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/development-plan.md",
                        "executionGraph": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/execution-graph.md",
                        "stateTransitionGraph": f"{GOVERNANCE_DIRECTORY}/state-transition-graph.md",
                        "frontier": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/frontier.md",
                        "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
                    },
                    "hostAutomation": render_host_automation(old_states[root_id]["hostRuntime"]),
                    "nextAction": _prepared_next_action(old_states[root_id]["hostRuntime"]),
                    "responseContract": _prepared_response_contract(
                        old_states[root_id]["hostRuntime"]
                    ),
                }
        new_ids = {record["definition"]["id"] for record in records}
        conflicts = sorted(item_id for item_id in new_ids if item_id in existing_by_id and item_id not in old_ids)
        if conflicts:
            fail("WORK_ITEM_ID_CONFLICT", "Hierarchy contains IDs already owned by another requirement", ids=conflicts)

        states = {record["definition"]["id"]: _state(record["definition"], runtime, at) for record in records}
        hierarchy_state = _hierarchy_state(
            normalized,
            states,
            status="WAITING_FOR_HUMAN_REVIEW",
        )
        target = safe_path(root, f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}")
        entries = [
            entry_from_definition(
                record["definition"],
                states[record["definition"]["id"]],
                at,
                package_path=record["packagePath"],
            )
            for record in records
        ]
        registry["workItems"] = [item for item in registry["workItems"] if item["id"] not in old_ids] + entries
        registry["currentFocus"] = {"workItemId": root_id, "purpose": "HIERARCHY_PLAN_AND_MODE_CONFIRMATION"}
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.recompute_progress(registry)
        repository.validate_operational_registry(registry)
        repository.store_hierarchy(records, states, hierarchy_state)
        graph = compile_delivery_graph(
            normalized,
            hierarchy_fingerprint=hierarchy_state["hierarchyFingerprint"],
        )
        repository.store_graph_definition(
            graph,
            graph_fingerprint_value=hierarchy_state["graphFingerprint"],
            created_at=at,
        )
        repository.write_hierarchy_package(
            target,
            _hierarchy_packages(repository, normalized, states, hierarchy_state),
            replace=replace,
        )
        repository.write_registry(registry)
        return {
            "created": not replace,
            "revised": replace,
            "idempotent": False,
            "rootId": root_id,
            "itemIds": [record["definition"]["id"] for record in records],
            "stage": hierarchy_state["stage"],
            "hierarchyFingerprint": hierarchy_state["hierarchyFingerprint"],
            "graphFingerprint": hierarchy_state["graphFingerprint"],
            "graphSummary": graph_summary(graph),
            "baselineFingerprints": {
                item_id: state["baselineFingerprint"] for item_id, state in states.items()
            },
            "artifactDir": str(target),
            "humanArtifacts": {
                "developmentPlan": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/development-plan.md",
                "executionGraph": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/execution-graph.md",
                "stateTransitionGraph": f"{GOVERNANCE_DIRECTORY}/state-transition-graph.md",
                "frontier": f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}/frontier.md",
                "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
            },
            "hostAutomation": render_host_automation(runtime),
            "nextAction": _prepared_next_action(runtime),
            "responseContract": _prepared_response_contract(runtime),
        }


def _prepared_response_contract(host_runtime: str) -> dict[str, Any]:
    prompt = "确认当前方案后，请回复 `active 开发` 或 `manual 开发`。"
    if render_host_automation(host_runtime) is not None:
        prompt += "选择 `active` 前须先满足 `hostAutomation`。"
    return {
        "kind": "PLAN_CONFIRMATION",
        "requiredChoices": [
            {"developmentMode": "active", "reply": "active 开发"},
            {"developmentMode": "manual", "reply": "manual 开发"},
        ],
        "prompt": prompt,
    }


def _prepared_next_action(host_runtime: str) -> str:
    if render_host_automation(host_runtime) is not None:
        return (
            "人工评审 development-plan.md 并同时提供 active/manual；"
            "选择 active 前满足 hostAutomation，再一次确认冻结，无需复述指纹。"
        )
    return (
        "人工评审 development-plan.md 并同时提供 active/manual；"
        "同意后一次确认冻结，无需复述指纹。"
    )


def _manual_requirement_handoff(
    root_entry: dict[str, Any],
    registry: dict[str, Any],
) -> str | None:
    if (root_entry.get("developmentMode") or {}).get("mode") != "manual":
        return None
    by_id = {entry["id"]: entry for entry in registry["workItems"]}
    return render_requirement_handoff(root_entry, by_id)


def _frozen_human_artifacts(root_id: str, handoff: str | None) -> dict[str, str | None]:
    base = f"{GOVERNANCE_DIRECTORY}/{WORK_ITEMS_DIRECTORY}/{root_id}"
    return {
        "developmentPlan": f"{base}/development-plan.md",
        "progress": f"{base}/progress.md",
        "executionGraph": f"{base}/execution-graph.md",
        "stateTransitionGraph": f"{GOVERNANCE_DIRECTORY}/state-transition-graph.md",
        "frontier": f"{base}/frontier.md",
        "runTimeline": f"{base}/run-timeline.md",
        "requirementHandoff": f"{base}/requirement-handoff.md" if handoff is not None else None,
        "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
    }


def _frozen_next_action(development_mode: str, host_runtime: str) -> str:
    if development_mode == "active":
        if render_host_automation(host_runtime) is not None:
            return "确认 hostAutomation 已满足，再查询 graph-frontier 并在首次 dispatch-task 前保持 Auto；随后完整消费自动调度计划。"
        return "查询 graph-frontier 并完整消费 Graph 自动计算的 Agent 调度计划；容量不足时按原顺序排队。"
    return "把 handoffCommand 一次复制到新会话；交接到 Claude Code 时使用 claudeCodeAutoHandoff，随后按 Graph 自动推进整棵需求树。"


def _frozen_response_contract(
    development_mode: str,
    handoff_command: str | None,
) -> dict[str, Any]:
    if development_mode == "active":
        return {
            "kind": "ACTIVE_EXECUTION",
            "resumeFromGraphFrontier": True,
            "askDevelopmentModeAgain": False,
        }
    if handoff_command is None:
        fail(
            "WORK_ITEM_HANDOFF_MISSING",
            "Manual development mode requires a copyable handoff prompt",
        )
    return {
        "kind": "MANUAL_HANDOFF",
        "mustProvideCopyablePrompt": True,
        "codeBlockLanguage": "text",
        "suggestedPrompt": handoff_command,
        "equivalentPromptAllowed": True,
        "linkOnlyAllowed": False,
        "requiredSemantics": [
            "rootId",
            "resumeFromGraphFrontier",
            "consumeCompleteDispatchPlan",
            "doNotPrepareOrFreezeAgain",
            "completeDevelopmentTestsAndGates",
        ],
    }


def freeze_hierarchy(
    *,
    root: str,
    root_id: str,
    expected_hierarchy_fingerprint: str,
    development_mode: str,
    confirmed: bool = False,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Atomically record one human approval for every node in a requirement tree."""
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not confirmed:
        fail("CONFIRMATION_REQUIRED", "Hierarchy freeze requires explicit human confirmation")
    if development_mode not in {"active", "manual"}:
        fail("WORK_ITEM_DEVELOPMENT_MODE_INVALID", "Development mode must be active or manual")
    at = timestamp(now)
    with repository.transaction() as registry:
        root_entry = repository.item_by_id(registry, root_id)
        hierarchy, states, hierarchy_state, target = _hierarchy_from_registry(repository, registry, root_entry)
        if hierarchy_state["hierarchyFingerprint"] != expected_hierarchy_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The confirmed hierarchy fingerprint is not current")
        records = _hierarchy_records(hierarchy)
        if hierarchy_state["stage"] == "BASELINE_FROZEN":
            if (root_entry.get("developmentMode") or {}).get("mode") != development_mode:
                fail("WORK_ITEM_DEVELOPMENT_MODE_LOCKED", "Development mode is fixed by the requirement freeze")
            repository.write_registry(registry, changed_item_ids=set())
            handoff = _manual_requirement_handoff(root_entry, registry)
            handoff_command = render_requirement_handoff_command(root_id) if handoff is not None else None
            claude_auto_handoff = render_claude_code_auto_handoff(root_id) if handoff is not None else None
            stored_graph = repository.read_graph_definition(root_id)
            graph_run = repository.read_graph_run(root_id)
            return {
                "created": False,
                "idempotent": True,
                "rootId": root_id,
                "hierarchyFingerprint": hierarchy_state["hierarchyFingerprint"],
                "graphFingerprint": stored_graph["graphFingerprint"],
                "graphRun": {
                    key: graph_run[key]
                    for key in ("runId", "status", "startedAt", "updatedAt", "recordRevision")
                },
                "frozenItemIds": [record["definition"]["id"] for record in records],
                "rootBaselineFingerprint": states[root_id]["baselineFingerprint"],
                "developmentMode": root_entry["developmentMode"],
                "humanArtifacts": _frozen_human_artifacts(root_id, handoff),
                "handoffPrompt": handoff,
                "handoffCommand": handoff_command,
                "claudeCodeAutoHandoff": claude_auto_handoff,
                "hostAutomation": render_host_automation(states[root_id]["hostRuntime"]),
                "nextAction": _frozen_next_action(development_mode, states[root_id]["hostRuntime"]),
                "responseContract": _frozen_response_contract(
                    development_mode,
                    handoff_command,
                ),
            }
        if any(states[record["definition"]["id"]]["stage"] != "WAITING_FOR_BASELINE_CONFIRMATION" for record in records):
            fail("WORK_ITEM_STAGE_INVALID", "Every hierarchy node must be waiting for the same freeze")

        frozen_states = {
            item_id: {
                **state,
                "stage": "BASELINE_FROZEN",
                "frozenAt": at,
                "review": {
                    **state["review"],
                    "status": "APPROVED",
                    "reviewedBy": "user",
                    "reviewedAt": at,
                },
            }
            for item_id, state in states.items()
        }
        frozen_hierarchy_state = _hierarchy_state(
            hierarchy,
            frozen_states,
            status="APPROVED",
            at=at,
        )
        repository.store_hierarchy(records, frozen_states, frozen_hierarchy_state)
        repository.write_hierarchy_package(
            target,
            _hierarchy_packages(repository, hierarchy, frozen_states, frozen_hierarchy_state),
            replace=True,
        )
        development_mode_record = {
            "schemaVersion": SCHEMA_VERSION,
            "rootId": root_id,
            "baselineFingerprint": frozen_states[root_id]["baselineFingerprint"],
            "mode": development_mode,
            "confirmedBy": "user",
            "confirmedAt": at,
        }
        for record in records:
            entry = repository.item_by_id(registry, record["definition"]["id"])
            entry["stage"] = "BASELINE_FROZEN"
            entry["status"] = "FROZEN"
            entry["recordRevision"] += 1
            entry["updatedAt"] = at
        root_entry["developmentMode"] = development_mode_record
        repository.freeze_graph_definition(
            root_id,
            expected_graph_fingerprint=frozen_hierarchy_state["graphFingerprint"],
            frozen_at=at,
        )
        graph_run = repository.start_graph_run(root_id, started_at=at)
        repository.append_graph_event(
            root_id=root_id,
            node_id=None,
            event_type="GRAPH_RUN_STARTED",
            actor="AGENT",
            operation_id=None,
            payload={"developmentMode": development_mode},
            recorded_at=at,
        )
        registry["currentFocus"] = {
            "workItemId": root_id,
            "purpose": "ACTIVE_REQUIREMENT_DISPATCH" if development_mode == "active" else "MANUAL_REQUIREMENT_HANDOFF",
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.write_registry(registry)
        graph_run = repository.read_graph_run(root_id)
        handoff = _manual_requirement_handoff(root_entry, registry)
        handoff_command = render_requirement_handoff_command(root_id) if handoff is not None else None
        claude_auto_handoff = render_claude_code_auto_handoff(root_id) if handoff is not None else None
        return {
            "created": True,
            "idempotent": False,
            "rootId": root_id,
            "stage": "BASELINE_FROZEN",
            "hierarchyFingerprint": frozen_hierarchy_state["hierarchyFingerprint"],
            "graphFingerprint": frozen_hierarchy_state["graphFingerprint"],
            "graphRun": {
                key: graph_run[key]
                for key in ("runId", "status", "startedAt", "updatedAt", "recordRevision")
            },
            "frozenItemIds": [record["definition"]["id"] for record in records],
            "rootBaselineFingerprint": frozen_states[root_id]["baselineFingerprint"],
            "developmentMode": development_mode_record,
            "taskBaselines": {
                record["definition"]["id"]: frozen_states[record["definition"]["id"]]["baselineFingerprint"]
                for record in records
                if record["definition"]["kind"] == "TASK"
            },
            "humanArtifacts": _frozen_human_artifacts(root_id, handoff),
            "handoffPrompt": handoff,
            "handoffCommand": handoff_command,
            "claudeCodeAutoHandoff": claude_auto_handoff,
            "hostAutomation": render_host_automation(frozen_states[root_id]["hostRuntime"]),
            "nextAction": _frozen_next_action(development_mode, frozen_states[root_id]["hostRuntime"]),
            "responseContract": _frozen_response_contract(
                development_mode,
                handoff_command,
            ),
        }


def retry_work_item(
    *,
    root: str,
    item_id: str,
    expected_baseline_fingerprint: str,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        if entry["status"] != "BLOCKED" or entry.get("claim"):
            fail("WORK_ITEM_RETRY_INVALID", "Only an unclaimed BLOCKED work item can be retried")
        if entry["baselineFingerprint"] != expected_baseline_fingerprint:
            fail("WORK_ITEM_REVISION_CONFLICT", "The retry baseline fingerprint is not current")
        definition = repository.assert_current_lineage(registry, entry)[0]
        root_id = hierarchy_root_entry(registry, entry)["id"]
        gate_failed = entry["gate"]["status"] == "FAIL"
        retry_node_id = (
            gate_node_id(item_id)
            if entry["kind"] != "TASK" or gate_failed
            else execution_node_id(item_id)
        )
        stored_graph = repository.read_graph_definition(root_id)
        graph_run = repository.read_graph_run(root_id)
        current_node = next(
            node for node in graph_run["nodes"] if node["nodeId"] == retry_node_id
        )
        budget = retry_budget(stored_graph["graph"], current_node["attempt"])
        if budget["retryExhausted"]:
            fail(
                "WORK_ITEM_RETRY_EXHAUSTED",
                f"{item_id} has exhausted its retry budget",
            )
        task_gate_remediation = entry["kind"] == "TASK" and gate_failed
        failed_gate_artifact = (
            entry["gate"]["artifact"] if task_gate_remediation else None
        )
        retry_node_ids = (
            [execution_node_id(item_id), gate_node_id(item_id)]
            if task_gate_remediation
            else [retry_node_id]
        )
        graph_attempts = repository.begin_graph_attempts(
            root_id,
            retry_node_ids,
            at=at,
        )
        entry["status"] = "FROZEN"
        entry["gate"] = {"status": "NOT_RUN", "evidence": None}
        if entry["parentId"] is None:
            entry["acceptance"] = {"status": "NOT_READY", "review": None, "userConfirmation": None}
        entry["recordRevision"] += 1
        entry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": (
                "GATE_REMEDIATION_RETRY"
                if task_gate_remediation
                else "EXECUTION_RETRY"
                if entry["kind"] == "TASK"
                else "AGGREGATE_GATE_RETRY"
            ),
        }
        registry["revision"] += 1
        registry["updatedAt"] = at
        repository.append_graph_event(
            root_id=root_id,
            node_id=execution_node_id(item_id) if task_gate_remediation else retry_node_id,
            event_type="GRAPH_INVALIDATED" if task_gate_remediation else "NODE_RETRY_SCHEDULED",
            actor="AGENT",
            operation_id=None,
            payload=(
                {
                    "invalidatedNodeIds": retry_node_ids,
                    "attempts": graph_attempts,
                    "failureClass": "GATE_FAILURE",
                }
                if task_gate_remediation
                else {"attempts": graph_attempts}
            ),
            recorded_at=at,
            evidence_artifact=failed_gate_artifact,
        )
        if entry.get("acceptanceReport"):
            repository.write_acceptance_report(entry, definition, at)
        repository.write_registry(registry)
        return {
            "id": item_id,
            "status": entry["status"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "graphAttempts": graph_attempts,
        }


def refresh_work_item_projections(*, root: str, explicit_dogfood: bool = False) -> dict[str, Any]:
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.transaction() as registry:
        repository.write_registry(registry, changed_item_ids=set())
        by_id = {entry["id"]: entry for entry in registry["workItems"]}

        def hierarchy_root(entry: dict[str, Any]) -> dict[str, Any]:
            current = entry
            while current["parentId"] is not None:
                current = by_id[current["parentId"]]
            return current

        return {
            "revision": registry["revision"],
            "workspaceOverview": f"{GOVERNANCE_DIRECTORY}/workspace-overview.md",
            "workItems": [
                {
                    "id": entry["id"],
                    "acceptanceReport": entry["acceptanceReport"]["markdownPath"] if entry.get("acceptanceReport") else None,
                    "humanArtifacts": item_human_artifacts(
                        entry,
                        entry.get("acceptanceReport"),
                        root_package_path=hierarchy_root(entry)["packagePath"],
                    ),
                }
                for entry in registry["workItems"]
            ],
        }
