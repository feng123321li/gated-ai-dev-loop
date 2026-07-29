from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from .errors import GatedLoopError, fail
from .evidence_validation import (
    evidence_record,
    valid_evidence_record,
    valid_gate_artifact,
    valid_review_artifact,
    valid_task_result_artifact,
    valid_validation_remediation_artifact,
)
from .graph_model import (
    confirmation_node_id,
    execution_node_id,
    gate_node_id,
    review_node_id,
)
from .jsonio import canonical_json, sha256_bytes, strict_json_loads
from .model_core import (
    WORK_ITEM_SKILL_STAGES,
    work_item_baseline_fingerprint,
)

from .repository_contracts import (
    _plain_int,
)

@staticmethod
def _automatic_event_summary(purpose: str) -> str:
    return {
        "HIERARCHY_PLAN_AND_MODE_CONFIRMATION": "层级方案与方式确认",
        "ACTIVE_REQUIREMENT_DISPATCH": "主动开发调度",
        "MANUAL_REQUIREMENT_HANDOFF": "需求级开发交接",
        "EXECUTION": "任务执行状态更新",
        "GATE": "门禁验收状态更新",
        "ACCEPTANCE": "交付验收状态更新",
        "RETRY": "阻断任务重试",
        "VALIDATION_REMEDIATION_RETRY": "原任务验证修正重试",
        "GRAPH_REPLAY_REBUILD": "按图事件回放重建运行快照",
        "TASK_HEARTBEAT": "任务认领心跳续租",
        "TASK_PAUSED": "任务执行显式暂停",
        "TASK_RESUMED": "任务执行恢复",
        "GRAPH_ADVANCED": "图控制器自动推进与恢复",
        "GRAPH_RUN_CANCELLED": "图运行已确认取消",
    }.get(purpose, purpose)

def append_interaction_event(
    self,
    *,
    work_item_id: str,
    session_id: str,
    actor: str,
    event_type: str,
    summary: str,
    operation_id: str | None,
    host_runtime: str | None,
    payload: dict[str, Any],
    registry_revision: int | None,
    recorded_at: str,
) -> dict[str, Any]:
    connection = self._active_connection()
    previous = connection.execute(
        "SELECT event_hash FROM interaction_events ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous["event_hash"] if previous else None
    event_uuid = uuid.uuid4().hex
    material = {
        "eventUuid": event_uuid,
        "workItemId": work_item_id,
        "sessionId": session_id,
        "actor": actor,
        "eventType": event_type,
        "summary": summary,
        "operationId": operation_id,
        "hostRuntime": host_runtime,
        "payload": payload,
        "registryRevision": registry_revision,
        "recordedAt": recorded_at,
        "previousHash": previous_hash,
    }
    event_hash = sha256_bytes(canonical_json(material).encode("utf-8"))
    cursor = connection.execute(
        "INSERT INTO interaction_events("
        "event_uuid, work_item_id, session_id, actor, event_type, summary, operation_id, "
        "host_runtime, payload_json, registry_revision, recorded_at, previous_hash, event_hash"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_uuid,
            work_item_id,
            session_id,
            actor,
            event_type,
            summary,
            operation_id,
            host_runtime,
            canonical_json(payload),
            registry_revision,
            recorded_at,
            previous_hash,
            event_hash,
        ),
    )
    return {"eventId": cursor.lastrowid, **material, "eventHash": event_hash}

def read_interaction_events(
    self,
    item_ids: list[str] | None = None,
    *,
    after_event_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if (after_event_id is None) != (limit is None) or (
        after_event_id is not None
        and (
            not _plain_int(after_event_id)
            or not _plain_int(limit, minimum=1)
        )
    ):
        fail(
            "WORK_ITEM_INTERACTION_PAGE_INVALID",
            "Interaction event cursor and limit must be supplied together",
        )
    selected_item_ids = set(item_ids) if item_ids is not None else None
    if selected_item_ids is not None and not selected_item_ids:
        return []
    result = []
    previous_hash = None
    with self._read_connection() as connection:
        rows = connection.execute(
            "SELECT event_id, event_uuid, work_item_id, session_id, actor, "
            "event_type, summary, operation_id, host_runtime, "
            "payload_json, registry_revision, recorded_at, "
            "previous_hash, event_hash "
            "FROM interaction_events ORDER BY event_id"
        )
        for row in rows:
            try:
                payload = strict_json_loads(row["payload_json"])
            except (
                TypeError,
                ValueError,
                UnicodeError,
                RecursionError,
            ):
                fail(
                    "WORK_ITEM_INTERACTION_INVALID",
                    "Stored interaction payload is invalid",
                )
            material = {
                "eventUuid": row["event_uuid"],
                "workItemId": row["work_item_id"],
                "sessionId": row["session_id"],
                "actor": row["actor"],
                "eventType": row["event_type"],
                "summary": row["summary"],
                "operationId": row["operation_id"],
                "hostRuntime": row["host_runtime"],
                "payload": payload,
                "registryRevision": row["registry_revision"],
                "recordedAt": row["recorded_at"],
                "previousHash": row["previous_hash"],
            }
            expected_hash = sha256_bytes(
                canonical_json(material).encode("utf-8")
            )
            if (
                row["previous_hash"] != previous_hash
                or row["event_hash"] != expected_hash
            ):
                fail(
                    "WORK_ITEM_INTERACTION_INVALID",
                    "Stored interaction event chain is invalid",
                )
            previous_hash = row["event_hash"]
            if (
                selected_item_ids is not None
                and row["work_item_id"] not in selected_item_ids
            ):
                continue
            if (
                after_event_id is not None
                and row["event_id"] <= after_event_id
            ):
                continue
            result.append({
                "eventId": row["event_id"],
                **material,
                "eventHash": row["event_hash"],
            })
            if limit is not None and len(result) >= limit:
                break
    return result

def read_validation_remediations(
    self,
    item_id: str,
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read validated append-only remediation records for one frozen Task."""
    acceptance_ids = {item["id"] for item in definition["acceptance"]}
    result: list[dict[str, Any]] = []
    for event in self.read_interaction_events([item_id]):
        if event["eventType"] != "VALIDATION_REMEDIATION":
            continue
        payload = event["payload"]
        if not isinstance(payload, dict) or set(payload) != {"remediation", "previousState"}:
            fail("WORK_ITEM_REMEDIATION_INVALID", f"Stored validation remediation is invalid: {item_id}")
        record = payload["remediation"]
        previous_state = payload["previousState"]
        if not (
            isinstance(record, dict)
            and set(record) == {"evidence", "artifact", "recordedAt"}
            and isinstance(previous_state, dict)
            and set(previous_state) == {
                "status", "gate", "acceptance", "latestEvidence", "latestResult",
            }
            and valid_evidence_record(record.get("evidence"))
            and record.get("recordedAt") == event["recordedAt"]
            and valid_validation_remediation_artifact(
                record.get("artifact"),
                item_id=item_id,
                baseline_fingerprint=work_item_baseline_fingerprint(definition),
                acceptance_ids=acceptance_ids,
            )
            and record["evidence"] == evidence_record(record["artifact"])
        ):
            fail("WORK_ITEM_REMEDIATION_INVALID", f"Stored validation remediation is invalid: {item_id}")
        result.append(deepcopy(record))
    return result

def effective_task_file_changes(
    self,
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return frozen file changes plus validated remediation additions."""
    changes = deepcopy(definition["developmentPlan"].get("fileChanges", []))
    for record in self.read_validation_remediations(definition["id"], definition):
        changes.extend(deepcopy(record["artifact"]["fileChanges"]))
    return sorted(changes, key=lambda item: item["path"])

def effective_required_skills(
    self,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    stage: str | None = None,
) -> list[dict[str, Any]]:
    """Resolve inherited Skill requirements for one node and optional stage."""

    if stage is not None and stage not in WORK_ITEM_SKILL_STAGES:
        fail(
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
            f"Unsupported required Skill stage: {stage}",
        )
    by_id = {item["id"]: item for item in registry["workItems"]}
    lineage: list[dict[str, Any]] = []
    current: dict[str, Any] | None = entry
    visited: set[str] = set()
    while current is not None:
        if current["id"] in visited:
            fail(
                "WORK_ITEM_HIERARCHY_CYCLE",
                "Work item hierarchy contains a cycle",
            )
        visited.add(current["id"])
        lineage.append(current)
        parent_id = current["parentId"]
        current = by_id.get(parent_id) if parent_id is not None else None
    lineage.reverse()

    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for lineage_entry in lineage:
        definition = self.read_package(registry, lineage_entry)[0]
        for requirement in definition["requiredSkills"]:
            for requirement_stage in requirement["stages"]:
                if stage is not None and requirement_stage != stage:
                    continue
                key = (requirement["name"], requirement_stage)
                effective = aggregated.setdefault(key, {
                    "name": requirement["name"],
                    "stage": requirement_stage,
                    "declaredBy": [],
                    "purposes": [],
                })
                if lineage_entry["id"] not in effective["declaredBy"]:
                    effective["declaredBy"].append(lineage_entry["id"])
                if requirement["purpose"] not in effective["purposes"]:
                    effective["purposes"].append(requirement["purpose"])
    stage_order = {
        value: index
        for index, value in enumerate(WORK_ITEM_SKILL_STAGES)
    }
    return sorted(
        aggregated.values(),
        key=lambda item: (stage_order[item["stage"]], item["name"]),
    )

def actual_development_skill_usage(
    self,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return Task result Skill usage for this work item subtree."""

    by_id = {
        candidate["id"]: candidate
        for candidate in registry["workItems"]
    }
    records: list[dict[str, Any]] = []

    def visit(current: dict[str, Any], path: set[str]) -> None:
        if current["id"] in path:
            fail(
                "WORK_ITEM_HIERARCHY_CYCLE",
                "Work item hierarchy contains a cycle",
            )
        next_path = path | {current["id"]}
        if current["kind"] == "TASK":
            result = current.get("latestResult")
            artifact = (
                result.get("artifact")
                if isinstance(result, dict)
                else None
            )
            skill_usage = (
                artifact.get("skillUsage")
                if isinstance(artifact, dict)
                else None
            )
            if isinstance(skill_usage, list) and skill_usage:
                definition = self.read_package(
                    registry,
                    current,
                )[0]
                records.append({
                    "taskId": current["id"],
                    "taskTitle": definition["title"],
                    "operationId": artifact["operationId"],
                    "resultStatus": artifact["status"],
                    "recordedAt": result["recordedAt"],
                    "resultEvidence": deepcopy(result["evidence"]),
                    "skillUsage": deepcopy(skill_usage),
                })
        for child_id in current["childIds"]:
            child = by_id.get(child_id)
            if child is None:
                fail(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    f"Work item child is missing: {child_id}",
                )
            visit(child, next_path)

    visit(entry, set())
    return records

@staticmethod
def _stored_evidence_error(
    entry: dict[str, Any],
    record_kind: str,
    reason: str,
) -> None:
    fail(
        "WORK_ITEM_STORED_EVIDENCE_INVALID",
        (
            f"Stored {record_kind} evidence is invalid for "
            f"{entry['id']}: {reason}"
        ),
        itemId=entry["id"],
        recordKind=record_kind,
        reason=reason,
    )

def _validated_stored_artifact(
    self,
    entry: dict[str, Any],
    record: object,
    *,
    record_kind: str,
    expected_node_id: str,
    bound_artifacts: dict[str, set[tuple[str, str, str]]],
) -> dict[str, Any]:
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("artifact"), dict)
        or not valid_evidence_record(record.get("evidence"))
    ):
        self._stored_evidence_error(
            entry,
            record_kind,
            "the evidence record or artifact is missing",
        )
    artifact = record["artifact"]
    actual_reference = evidence_record(artifact)
    if record["evidence"] != actual_reference:
        self._stored_evidence_error(
            entry,
            record_kind,
            "the saved evidence hash does not match the artifact",
        )
    artifact_json = canonical_json(artifact)
    matching_bindings = bound_artifacts.get(
        actual_reference["sha256"],
        set(),
    )
    if not any(
        bound_json == artifact_json
        and node_id == expected_node_id
        and (
            "recordedAt" not in record
            or recorded_at == record["recordedAt"]
        )
        for bound_json, node_id, recorded_at in matching_bindings
    ):
        self._stored_evidence_error(
            entry,
            record_kind,
            "the artifact is not bound to the current graph evidence",
        )
    return artifact

def validate_stored_evidence(
    self,
    registry: dict[str, Any],
) -> None:
    """Strictly revalidate current evidence artifacts during recovery."""

    from .skill_execution import assert_required_skill_conformance

    by_id = {
        entry["id"]: entry
        for entry in registry["workItems"]
    }

    def root_id(entry: dict[str, Any]) -> str:
        current = entry
        visited: set[str] = set()
        while current["parentId"] is not None:
            if (
                current["id"] in visited
                or current["parentId"] not in by_id
            ):
                fail(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    "Work item hierarchy is invalid",
                )
            visited.add(current["id"])
            current = by_id[current["parentId"]]
        return current["id"]

    bound_by_root: dict[
        str,
        dict[str, set[tuple[str, str, str]]],
    ] = {}
    for entry in registry["workItems"]:
        if entry["parentId"] is not None:
            continue
        bound: dict[str, set[tuple[str, str, str]]] = {}
        for record in self.read_graph_evidence(entry["id"]):
            bound_artifact = record["boundArtifact"]
            artifact = bound_artifact["artifact"]
            artifact_sha256 = bound_artifact["binding"][
                "artifactSha256"
            ]
            bound.setdefault(artifact_sha256, set()).add((
                canonical_json(artifact),
                bound_artifact["binding"]["nodeId"],
                record["recordedAt"],
            ))
        bound_by_root[entry["id"]] = bound

    for entry in registry["workItems"]:
        if entry["id"] in self._isolated_entry_ids:
            continue
        definition = self.assert_current_lineage(registry, entry)[0]
        bound_artifacts = bound_by_root[root_id(entry)]

        latest_result = entry.get("latestResult")
        if latest_result is not None:
            artifact = self._validated_stored_artifact(
                entry,
                latest_result,
                record_kind="Task result",
                expected_node_id=execution_node_id(entry["id"]),
                bound_artifacts=bound_artifacts,
            )
            status = artifact.get("status")
            if (
                entry["kind"] != "TASK"
                or status not in {"IMPLEMENTED", "BLOCKED"}
                or not valid_task_result_artifact(
                    artifact,
                    item_id=entry["id"],
                    operation_id=artifact.get("operationId"),
                    status=status,
                    required_skills=self.effective_required_skills(
                        registry,
                        entry,
                        stage="DEVELOPMENT",
                    ),
                    generated_file_roots=definition[
                        "developmentPlan"
                    ].get("generatedFileRoots", []),
                )
            ):
                self._stored_evidence_error(
                    entry,
                    "Task result",
                    (
                        "the artifact does not match the frozen "
                        "DEVELOPMENT Skill contract"
                    ),
                )
            try:
                assert_required_skill_conformance(
                    self,
                    registry,
                    entry,
                    stage="DEVELOPMENT",
                    skill_usage=artifact.get("skillUsage", []),
                    operation_id=artifact.get("operationId"),
                    require_pass=status == "IMPLEMENTED",
                )
            except GatedLoopError as error:
                self._stored_evidence_error(
                    entry,
                    "Task result",
                    (
                        "the native Skill activation or conformance "
                        f"evidence is invalid: {error.code}"
                    ),
                )

        gate = entry["gate"]
        if gate["status"] in {"PASS", "FAIL"}:
            artifact = self._validated_stored_artifact(
                entry,
                gate,
                record_kind="gate",
                expected_node_id=gate_node_id(entry["id"]),
                bound_artifacts=bound_artifacts,
            )
            additional_planned_files: set[str] = set()
            if entry["kind"] == "TASK":
                frozen_files = {
                    item["path"]
                    for item in definition["developmentPlan"].get(
                        "fileChanges",
                        [],
                    )
                }
                effective_files = {
                    item["path"]
                    for item in self.effective_task_file_changes(
                        definition
                    )
                }
                additional_planned_files = (
                    effective_files - frozen_files
                )
            if (
                artifact.get("verdict") != gate["status"]
                or not valid_gate_artifact(
                    artifact,
                    entry,
                    definition,
                    additional_planned_files=additional_planned_files,
                    required_skills=self.effective_required_skills(
                        registry,
                        entry,
                        stage="GATE",
                    ),
                )
            ):
                self._stored_evidence_error(
                    entry,
                    "gate",
                    (
                        "the artifact does not match the frozen GATE "
                        "Skill contract"
                    ),
                )
            try:
                assert_required_skill_conformance(
                    self,
                    registry,
                    entry,
                    stage="GATE",
                    skill_usage=artifact.get("skillUsage", []),
                    require_pass=gate["status"] == "PASS",
                )
            except GatedLoopError as error:
                self._stored_evidence_error(
                    entry,
                    "gate",
                    (
                        "the native Skill activation or conformance "
                        f"evidence is invalid: {error.code}"
                    ),
                )

        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, dict):
            continue
        required_review_skills = self.effective_required_skills(
            registry,
            entry,
            stage="FINAL_REVIEW",
        )
        review = acceptance.get("review")
        if review is not None:
            artifact = self._validated_stored_artifact(
                entry,
                review,
                record_kind="review",
                expected_node_id=review_node_id(entry["id"]),
                bound_artifacts=bound_artifacts,
            )
            action = review.get("action")
            if (
                action not in {
                    "INDEPENDENT_REVIEW_PASS",
                    "HUMAN_REVIEW_ACCEPTED",
                    "REVIEW_BLOCKED",
                }
                or (
                    action == "HUMAN_REVIEW_ACCEPTED"
                    and required_review_skills
                )
                or not valid_review_artifact(
                    action,
                    artifact,
                    required_skills=(
                        required_review_skills
                        if action in {
                            "INDEPENDENT_REVIEW_PASS",
                            "REVIEW_BLOCKED",
                        }
                        else None
                    ),
                )
            ):
                self._stored_evidence_error(
                    entry,
                    "review",
                    (
                        "the artifact does not match the frozen "
                        "FINAL_REVIEW Skill contract"
                    ),
                )
            if action in {
                "INDEPENDENT_REVIEW_PASS",
                "REVIEW_BLOCKED",
            }:
                try:
                    assert_required_skill_conformance(
                        self,
                        registry,
                        entry,
                        stage="FINAL_REVIEW",
                        skill_usage=artifact.get("skillUsage", []),
                        require_pass=(
                            action == "INDEPENDENT_REVIEW_PASS"
                        ),
                    )
                except GatedLoopError as error:
                    self._stored_evidence_error(
                        entry,
                        "review",
                        (
                            "the native Skill activation or conformance "
                            f"evidence is invalid: {error.code}"
                        ),
                    )
        confirmation = acceptance.get("userConfirmation")
        if confirmation is not None:
            artifact = self._validated_stored_artifact(
                entry,
                confirmation,
                record_kind="user confirmation",
                expected_node_id=confirmation_node_id(entry["id"]),
                bound_artifacts=bound_artifacts,
            )
            if (
                confirmation.get("action") != "USER_CONFIRMED"
                or not valid_review_artifact(
                    "USER_CONFIRMED",
                    artifact,
                )
            ):
                self._stored_evidence_error(
                    entry,
                    "user confirmation",
                    "the artifact does not match the confirmation contract",
                )
