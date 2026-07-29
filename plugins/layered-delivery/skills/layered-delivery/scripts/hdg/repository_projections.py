from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .fs_safe import (
    atomic_replace_directory,
    atomic_write,
    read_regular_file,
    safe_path,
)
from .graph_model import (
    compile_runtime_policy,
)
from .graph_projections import (
    render_delivery_graph,
    render_frontier_dashboard,
    render_runtime_policy_summary,
    render_run_timeline,
    render_state_transition_graph,
)
from .svg_graphs import render_delivery_graph_svg_assets, render_runtime_policy_svg_assets
from .jsonio import canonical_json
from .model_core import (
    validate_hierarchy_definition,
)
from .model_rendering import (
    render_development_plan,
    render_hierarchy_plan,
    render_work_item_baseline,
    raw_definition,
)
from .projections import (
    render_acceptance_report,
    render_development_review,
    render_item_overview,
    render_item_progress,
    render_interaction_log,
    render_requirement_handoff,
    render_workspace_month_overviews,
    render_workspace_overview,
    report_status,
)
from .timing import timing_metric

from .repository_contracts import (
    GOVERNANCE_DIRECTORY,
)

def _write_interaction_logs(
    self,
    registry: dict[str, Any],
    root_ids: set[str] | None = None,
) -> None:
    by_id = {item["id"]: item for item in registry["workItems"]}

    def tree_ids(entry: dict[str, Any]) -> list[str]:
        result = [entry["id"]]
        for child_id in entry["childIds"]:
            result.extend(tree_ids(by_id[child_id]))
        return result

    for root in (
        item
        for item in registry["workItems"]
        if item["parentId"] is None
        and (root_ids is None or item["id"] in root_ids)
    ):
        events = self.read_interaction_events(tree_ids(root))
        atomic_write(
            self.item_path(root) / "interaction-log.md",
            render_interaction_log(root, events),
            durable=False,
        )

def refresh_interaction_logs(self, registry: dict[str, Any]) -> None:
    self._write_interaction_logs(registry)

def refresh_interaction_projection(
    self,
    registry: dict[str, Any],
    root_id: str,
) -> None:
    by_id = {item["id"]: item for item in registry["workItems"]}
    root = by_id.get(root_id)
    if root is None or root["parentId"] is not None:
        fail(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Interaction projection requires a requirement root",
        )

    def tree_ids(entry: dict[str, Any]) -> list[str]:
        result = [entry["id"]]
        for child_id in entry["childIds"]:
            result.extend(tree_ids(by_id[child_id]))
        return result

    events = self.read_interaction_events(tree_ids(root))
    atomic_write(
        self.item_path(root) / "interaction-log.md",
        render_interaction_log(root, events),
        durable=False,
    )

def write_task_context(
    self,
    entry: dict[str, Any],
    context: dict[str, Any],
    handoff: str,
    at: str,
) -> None:
    self._active_connection().execute(
        "INSERT INTO task_contexts(work_item_id, context_json, handoff_markdown, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(work_item_id) DO UPDATE SET "
        "context_json = excluded.context_json, handoff_markdown = excluded.handoff_markdown, "
        "updated_at = excluded.updated_at",
        (entry["id"], canonical_json(context), handoff, at),
    )

def _graph_projection_snapshot(
    self,
    registry: dict[str, Any],
    root: dict[str, Any],
    stored_graph: dict[str, Any],
    graph_run: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    from .graph_frontier import build_graph_frontier
    from .graph_state import (
        critical_path,
        derive_node_states,
        materialized_graph_states,
    )

    graph_events = self.read_graph_events(root["id"])
    if graph_run is not None:
        graph_nodes = materialized_graph_states(
            stored_graph["graph"],
            graph_run,
            registry,
        )
    else:
        graph_nodes = [
            {
                **state,
                "attempt": None,
                "owner": None,
                "operationId": None,
                "claimedAt": None,
                "finishedAt": None,
                "latestEvidenceHash": None,
                "leaseExpiresAt": None,
                "lastHeartbeatAt": None,
                "failureClass": None,
                "lastTransition": None,
                "retryExhausted": False,
                "recordRevision": None,
            }
            for state in derive_node_states(stored_graph["graph"], registry)
        ]
    graph_status = {
        "rootId": root["id"],
        "graphFingerprint": stored_graph["graphFingerprint"],
        "run": graph_run,
        "nodes": graph_nodes,
        "criticalPath": critical_path(stored_graph["graph"], graph_nodes),
    }
    frontier = build_graph_frontier(
        self,
        registry,
        root,
        stored_graph,
        graph_run,
        graph_nodes,
    )
    frontier["frontierSource"] = (
        "SNAPSHOT" if graph_run is not None else "DERIVED"
    )
    return graph_events, graph_status, frontier

def refresh_markdown_projections(
    self,
    registry: dict[str, Any],
    *,
    root_ids: set[str] | None = None,
    include_shared: bool = True,
) -> None:
    """Rebuild human artifacts for all or selected requirement roots."""
    rendered_files = 0
    if include_shared:
        runtime_policy = compile_runtime_policy()
        atomic_write(
            self.governance_root / "state-transition-graph.md",
            render_state_transition_graph(runtime_policy),
            durable=False,
        )
        rendered_files += 1
        for relative_path, contents in render_runtime_policy_svg_assets(
            runtime_policy
        ).items():
            atomic_write(
                self.governance_root / relative_path,
                contents,
                durable=False,
            )
            rendered_files += 1

    by_id = {item["id"]: item for item in registry["workItems"]}
    roots = [
        item
        for item in registry["workItems"]
        if item["parentId"] is None
        and (root_ids is None or item["id"] in root_ids)
    ]
    if root_ids is not None and {item["id"] for item in roots} != root_ids:
        fail(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Projection contains an unknown requirement root",
        )

    selected_item_ids: set[str] = set()

    def select_tree(entry: dict[str, Any]) -> None:
        selected_item_ids.add(entry["id"])
        for child_id in entry["childIds"]:
            select_tree(by_id[child_id])

    for root in roots:
        select_tree(root)

    definitions: dict[str, dict[str, Any]] = {}
    states: dict[str, dict[str, Any]] = {}
    for entry in registry["workItems"]:
        if entry["id"] not in selected_item_ids:
            continue
        definition, state, _ = self.read_package(registry, entry)
        definitions[entry["id"]] = definition
        states[entry["id"]] = state

    def build(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "definition": raw_definition(definitions[entry["id"]]),
            "children": [build(by_id[child_id]) for child_id in entry["childIds"]],
        }

    for root in roots:
        hierarchy = validate_hierarchy_definition({
            "schemaVersion": SCHEMA_VERSION,
            "root": build(root),
        })
        hierarchy_state = self.read_hierarchy_state(root["id"])
        stored_graph = self.read_graph_definition(root["id"])
        root_plan = (
            render_hierarchy_plan(hierarchy, states, hierarchy_state)
            + "\n"
            + render_runtime_policy_summary(stored_graph["graph"])
        )

        def project(entry: dict[str, Any], *, is_root: bool) -> None:
            target = self.item_path(entry)
            atomic_write(
                target / "baseline.md",
                render_work_item_baseline(definitions[entry["id"]]),
                durable=False,
            )
            nonlocal rendered_files
            rendered_files += 1
            atomic_write(
                target / "development-plan.md",
                root_plan if is_root else render_development_plan(definitions[entry["id"]], states[entry["id"]]),
                durable=False,
            )
            rendered_files += 1
            for child_id in entry["childIds"]:
                project(by_id[child_id], is_root=False)

        project(root, is_root=True)
        graph_run = self.read_graph_run(root["id"], allow_missing=True)
        atomic_write(
            self.item_path(root) / "execution-graph.md",
            render_delivery_graph(
                stored_graph["graph"],
                graph_fingerprint=stored_graph["graphFingerprint"],
                run=graph_run,
            ),
            durable=False,
        )
        rendered_files += 1
        for relative_path, contents in render_delivery_graph_svg_assets(
            stored_graph["graph"]
        ).items():
            atomic_write(
                self.item_path(root) / relative_path,
                contents,
                durable=False,
            )
            rendered_files += 1
        for relative_path in (
            "state-transition-graph.md",
            "assets/development-flow.svg",
            "assets/node-state-machine.svg",
        ):
            legacy_projection = safe_path(self.item_path(root), relative_path)
            try:
                read_regular_file(self.item_path(root), legacy_projection)
            except FileNotFoundError:
                continue
            legacy_projection.unlink()
        graph_events, graph_status, frontier = self._graph_projection_snapshot(
            registry,
            root,
            stored_graph,
            graph_run,
        )
        atomic_write(
            self.item_path(root) / "run-timeline.md",
            render_run_timeline(graph_status, graph_events),
            durable=False,
        )
        rendered_files += 1
        atomic_write(
            self.item_path(root) / "frontier.md",
            render_frontier_dashboard(graph_status, frontier),
            durable=False,
        )
        rendered_files += 1

    with self._read_connection() as connection:
        context_rows = connection.execute(
            "SELECT work_item_id, context_json, handoff_markdown FROM task_contexts"
        ).fetchall()
        report_rows = connection.execute(
            "SELECT work_item_id, report_kind, report_json FROM reports"
        ).fetchall()
    for row in context_rows:
        entry = by_id.get(row["work_item_id"])
        if entry is not None and entry["id"] in selected_item_ids:
            atomic_write(
                self.item_path(entry) / "development-handoff.md",
                row["handoff_markdown"],
                durable=False,
            )
            rendered_files += 1
    for row in report_rows:
        entry = by_id.get(row["work_item_id"])
        if entry is None or entry["id"] not in selected_item_ids:
            continue
        try:
            report = json.loads(row["report_json"])
        except (TypeError, json.JSONDecodeError):
            fail("WORK_ITEM_REPORT_INVALID", f"Stored report is invalid: {row['work_item_id']}")
        if row["report_kind"] == "DEVELOPMENT_REVIEW":
            atomic_write(
                self.item_path(entry) / "development-review.md",
                render_development_review(report),
                durable=False,
            )
            rendered_files += 1
        elif row["report_kind"] == "ACCEPTANCE":
            atomic_write(
                self.item_path(entry) / "acceptance-report.md",
                render_acceptance_report(report),
                durable=False,
            )
            rendered_files += 1
        else:
            fail("WORK_ITEM_REPORT_INVALID", f"Unknown stored report kind: {row['report_kind']}")
    timing_metric("projectionRootCount", len(roots))
    timing_metric("projectionItemCount", len(selected_item_ids))
    timing_metric("projectionFilesRendered", rendered_files)

def refresh_heartbeat_projections(
    self,
    registry: dict[str, Any],
    root_id: str,
) -> None:
    by_id = {item["id"]: item for item in registry["workItems"]}
    root = by_id.get(root_id)
    if root is None or root["parentId"] is not None:
        fail(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Heartbeat projection requires a requirement root",
        )
    stored_graph = self.read_graph_definition(root_id)
    graph_run = self.read_graph_run(root_id, allow_missing=True)
    graph_events, graph_status, frontier = self._graph_projection_snapshot(
        registry,
        root,
        stored_graph,
        graph_run,
    )
    atomic_write(
        self.item_path(root) / "execution-graph.md",
        render_delivery_graph(
            stored_graph["graph"],
            graph_fingerprint=stored_graph["graphFingerprint"],
            run=graph_run,
        ),
        durable=False,
    )
    atomic_write(
        self.item_path(root) / "run-timeline.md",
        render_run_timeline(graph_status, graph_events),
        durable=False,
    )
    atomic_write(
        self.item_path(root) / "frontier.md",
        render_frontier_dashboard(graph_status, frontier),
        durable=False,
    )

def write_registry(
    self,
    registry: dict[str, Any],
    *,
    changed_item_ids: set[str] | None = None,
    projection_mode: str = "full",
    projection_root_id: str | None = None,
) -> None:
    self.recompute_progress(registry)
    self.validate_operational_registry(registry)
    registry["workItems"] = sorted(registry["workItems"], key=lambda item: item["id"])
    by_id = {item["id"]: item for item in registry["workItems"]}
    connection = self._active_connection()
    previous = connection.execute(
        "SELECT revision FROM workspace WHERE singleton = 1"
    ).fetchone()
    previous_revision = previous["revision"] if previous else None
    current_ids = set(by_id)
    stored_entries = {
        row["id"]: row["entry_json"]
        for row in connection.execute("SELECT id, entry_json FROM work_items")
    }
    stored_ids = set(stored_entries)
    if changed_item_ids is None:
        for stale_id in stored_ids - current_ids:
            connection.execute("DELETE FROM task_contexts WHERE work_item_id = ?", (stale_id,))
            connection.execute("DELETE FROM reports WHERE work_item_id = ?", (stale_id,))
            connection.execute("DELETE FROM work_items WHERE id = ?", (stale_id,))
        root_ids = {item["id"] for item in registry["workItems"] if item["parentId"] is None}
        for row in connection.execute("SELECT root_id FROM hierarchies").fetchall():
            if row["root_id"] not in root_ids:
                connection.execute("DELETE FROM hierarchies WHERE root_id = ?", (row["root_id"],))
        for row in connection.execute("SELECT root_id FROM graph_definitions").fetchall():
            if row["root_id"] not in root_ids:
                connection.execute("DELETE FROM graph_definitions WHERE root_id = ?", (row["root_id"],))
        candidate_ids = current_ids
    else:
        if current_ids != stored_ids or not changed_item_ids <= current_ids:
            fail(
                "WORK_ITEM_INCREMENTAL_WRITE_INVALID",
                "Incremental registry writes require an unchanged work-item set",
            )
        candidate_ids = changed_item_ids
    rows_updated = 0
    bytes_written = 0
    for item_id in sorted(candidate_ids):
        if item_id in self._isolated_entry_ids:
            continue
        serialized = canonical_json(by_id[item_id])
        if stored_entries.get(item_id) == serialized:
            continue
        cursor = connection.execute(
            "UPDATE work_items SET entry_json = ? WHERE id = ?",
            (serialized, item_id),
        )
        if cursor.rowcount != 1:
            fail("WORK_ITEM_PACKAGE_INVALID", f"{item_id} has no stored definition")
        rows_updated += 1
        bytes_written += len(serialized.encode("utf-8"))
    timing_metric("registryRowsConsidered", len(candidate_ids))
    timing_metric("registryRowsUpdated", rows_updated)
    timing_metric("registryRowsSkipped", len(candidate_ids) - rows_updated)
    timing_metric("registryBytesWritten", bytes_written)
    connection.execute(
        "INSERT INTO workspace(singleton, schema_version, coordination_root, revision, current_focus_json, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(singleton) DO UPDATE SET "
        "schema_version = excluded.schema_version, coordination_root = excluded.coordination_root, "
        "revision = excluded.revision, current_focus_json = excluded.current_focus_json, "
        "updated_at = excluded.updated_at",
        (
            registry["schemaVersion"],
            registry["coordinationRoot"],
            registry["revision"],
            canonical_json(registry["currentFocus"]),
            registry["updatedAt"],
        ),
    )
    focus = registry["currentFocus"]
    if previous_revision != registry["revision"] and focus["workItemId"] is not None:
        focused = by_id[focus["workItemId"]]
        state = connection.execute(
            "SELECT state_json FROM work_items WHERE id = ?",
            (focused["id"],),
        ).fetchone()
        host_runtime = None
        if state:
            try:
                host_runtime = json.loads(state["state_json"]).get("hostRuntime")
            except (TypeError, json.JSONDecodeError):
                pass
        self.append_interaction_event(
            work_item_id=focused["id"],
            session_id="controller",
            actor="AGENT",
            event_type=focus["purpose"],
            summary=self._automatic_event_summary(focus["purpose"]),
            operation_id=(focused.get("claim") or {}).get("operationId"),
            host_runtime=host_runtime,
            payload={"status": focused["status"], "stage": focused["stage"]},
            registry_revision=registry["revision"],
            recorded_at=registry["updatedAt"],
        )
    effective_projection_mode = projection_mode
    incremental_root_ids: set[str] = set()
    if (
        projection_mode == "full"
        and changed_item_ids is not None
        and changed_item_ids
    ):
        effective_projection_mode = "incremental"
        for item_id in changed_item_ids:
            current = by_id[item_id]
            while current["parentId"] is not None:
                current = by_id[current["parentId"]]
            incremental_root_ids.add(current["id"])
    if effective_projection_mode == "heartbeat" and projection_root_id is not None:
        graph_root_ids: set[str] | None = {projection_root_id}
    elif effective_projection_mode == "interaction":
        graph_root_ids = set()
    elif effective_projection_mode == "incremental":
        graph_root_ids = incremental_root_ids
    else:
        graph_root_ids = None
    self.sync_graph_runs(registry, root_ids=graph_root_ids)
    self.schedule_projection(
        registry,
        mode=effective_projection_mode,
        root_id=projection_root_id,
        root_ids=incremental_root_ids,
        changed_item_ids=changed_item_ids,
    )

def refresh_registry_projections(self, registry: dict[str, Any]) -> None:
    self.refresh_markdown_projections(registry)
    by_id = {item["id"]: item for item in registry["workItems"]}
    atomic_write(
        self.governance_root / "workspace-overview.md",
        render_workspace_overview(
            registry,
            isolated_item_ids=self._isolated_entry_ids,
        ),
        durable=False,
    )
    monthly_overviews = render_workspace_month_overviews(registry)
    monthly_root = self.governance_root / "workspace-overview"
    if monthly_root.exists() and (
        not monthly_root.is_dir() or monthly_root.is_symlink()
    ):
        fail(
            "WORKSPACE_OVERVIEW_DIRECTORY_INVALID",
            "Monthly workspace overview path must be a regular directory",
        )

    def populate_monthly_overviews(staging: Path) -> None:
        for relative_path, content in monthly_overviews.items():
            atomic_write(staging / relative_path, content, durable=False)

    atomic_replace_directory(monthly_root, populate_monthly_overviews)
    for entry in registry["workItems"]:
        target = self.item_path(entry)
        if not target.exists():
            continue
        if not target.is_dir() or target.is_symlink():
            fail("WORK_ITEM_PACKAGE_INVALID", f"{entry['id']} package path is invalid")
        atomic_write(
            target / "overview.md",
            render_item_overview(entry, by_id),
            durable=False,
        )
        if entry["parentId"] is None:
            atomic_write(
                target / "node-progress.md",
                render_item_progress(entry, by_id),
                durable=False,
            )
            atomic_write(
                target / "progress.md",
                render_item_progress(entry, by_id, include_hierarchy=True),
                durable=False,
            )
        else:
            atomic_write(
                target / "progress.md",
                render_item_progress(entry, by_id),
                durable=False,
            )
        if (
            entry["parentId"] is None
            and entry["stage"] == "BASELINE_FROZEN"
            and (entry.get("developmentMode") or {}).get("mode") == "manual"
        ):
            atomic_write(
                target / "requirement-handoff.md",
                render_requirement_handoff(entry, by_id),
                durable=False,
            )
    self._write_interaction_logs(registry)

def refresh_incremental_projections(
    self,
    registry: dict[str, Any],
    root_ids: set[str],
    changed_item_ids: set[str],
) -> None:
    """Refresh only projections whose source belongs to affected roots."""
    self.refresh_markdown_projections(
        registry,
        root_ids=root_ids,
        include_shared=False,
    )
    by_id = {item["id"]: item for item in registry["workItems"]}
    atomic_write(
        self.governance_root / "workspace-overview.md",
        render_workspace_overview(
            registry,
            isolated_item_ids=self._isolated_entry_ids,
        ),
        durable=False,
    )
    monthly_overviews = render_workspace_month_overviews(registry)
    affected_months = {
        next(
            key.split("/", 1)[0]
            for key in monthly_overviews
            if key.endswith(f"/{root_id}.md")
        )
        for root_id in root_ids
    }
    monthly_root = self.governance_root / "workspace-overview"
    if monthly_root.exists() and (
        not monthly_root.is_dir() or monthly_root.is_symlink()
    ):
        fail(
            "WORKSPACE_OVERVIEW_DIRECTORY_INVALID",
            "Monthly workspace overview path must be a regular directory",
        )
    monthly_root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in monthly_overviews.items():
        if (
            relative_path in {f"{month}.md" for month in affected_months}
            or any(
                relative_path == f"{month}/{root_id}.md"
                for month in affected_months
                for root_id in root_ids
            )
        ):
            atomic_write(
                monthly_root / relative_path,
                content,
                durable=False,
            )

    selected_item_ids: set[str] = set()

    def select_tree(entry: dict[str, Any]) -> None:
        selected_item_ids.add(entry["id"])
        for child_id in entry["childIds"]:
            select_tree(by_id[child_id])

    for root_id in root_ids:
        select_tree(by_id[root_id])

    for item_id in selected_item_ids:
        entry = by_id[item_id]
        target = self.item_path(entry)
        atomic_write(
            target / "overview.md",
            render_item_overview(entry, by_id),
            durable=False,
        )
        if entry["parentId"] is None:
            atomic_write(
                target / "node-progress.md",
                render_item_progress(entry, by_id),
                durable=False,
            )
            atomic_write(
                target / "progress.md",
                render_item_progress(
                    entry,
                    by_id,
                    include_hierarchy=True,
                ),
                durable=False,
            )
            if (
                entry["stage"] == "BASELINE_FROZEN"
                and (entry.get("developmentMode") or {}).get("mode")
                == "manual"
            ):
                atomic_write(
                    target / "requirement-handoff.md",
                    render_requirement_handoff(entry, by_id),
                    durable=False,
                )
        else:
            atomic_write(
                target / "progress.md",
                render_item_progress(entry, by_id),
                durable=False,
            )
    self._write_interaction_logs(registry, root_ids=root_ids)
    timing_metric(
        "projectionChangedItemCount",
        len(changed_item_ids),
    )

def write_acceptance_report(
    self,
    registry: dict[str, Any],
    entry: dict[str, Any],
    definition: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    from .skill_execution import skill_execution_audit

    acceptance = entry.get("acceptance") if entry["parentId"] is None else None
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "workItem": {
            "id": entry["id"],
            "title": definition["title"],
            "kind": entry["kind"],
            "gateLevel": entry["gateLevel"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "parentId": entry["parentId"],
        },
        "status": report_status(entry),
        "development": entry.get("latestResult"),
        "developmentSkillUsage": (
            self.actual_development_skill_usage(
                registry,
                entry,
            )
        ),
        "skillExecutionAudit": skill_execution_audit(
            self,
            registry,
            entry,
        ),
        "gate": entry["gate"],
        "criteria": definition["acceptance"],
        "developmentPlan": definition["developmentPlan"],
        "validationRemediations": self.read_validation_remediations(entry["id"], definition)
        if entry["kind"] == "TASK"
        else [],
        "review": acceptance.get("review") if acceptance else None,
        "userConfirmation": acceptance.get("userConfirmation") if acceptance else None,
        "generatedAt": at,
    }
    self._active_connection().execute(
        "INSERT INTO reports(work_item_id, report_kind, report_json, generated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(work_item_id, report_kind) DO UPDATE SET "
        "report_json = excluded.report_json, generated_at = excluded.generated_at",
        (entry["id"], "ACCEPTANCE", canonical_json(report), at),
    )
    base = f"{GOVERNANCE_DIRECTORY}/{entry['packagePath']}"
    entry["acceptanceReport"] = {
        "schemaVersion": SCHEMA_VERSION,
        "status": report["status"],
        "markdownPath": f"{base}/acceptance-report.md",
        "generatedAt": at,
    }
    return report

def write_development_review(
    self,
    registry: dict[str, Any],
    entry: dict[str, Any],
    definition: dict[str, Any],
    at: str,
) -> dict[str, Any]:
    from .skill_execution import skill_execution_audit

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "workItem": {
            "id": entry["id"],
            "title": definition["title"],
            "kind": entry["kind"],
            "gateLevel": entry["gateLevel"],
            "baselineFingerprint": entry["baselineFingerprint"],
            "parentId": entry["parentId"],
        },
        "status": entry["status"],
        "developmentPlan": definition["developmentPlan"],
        "validationRemediations": self.read_validation_remediations(entry["id"], definition),
        "result": entry.get("latestResult"),
        "skillExecutionAudit": skill_execution_audit(
            self,
            registry,
            entry,
        ),
        "generatedAt": at,
    }
    self._active_connection().execute(
        "INSERT INTO reports(work_item_id, report_kind, report_json, generated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(work_item_id, report_kind) DO UPDATE SET "
        "report_json = excluded.report_json, generated_at = excluded.generated_at",
        (entry["id"], "DEVELOPMENT_REVIEW", canonical_json(report), at),
    )
    return report
