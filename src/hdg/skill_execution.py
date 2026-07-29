from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .evidence_validation import FINGERPRINT, concrete_skill_evidence
from .graph_model import execution_node_id, gate_node_id, review_node_id
from .host_runtime import (
    is_agent_runtime,
    is_claude_runtime,
    require_host_runtime,
)
from .jsonio import canonical_json, sha256_bytes
from .repository import GovernanceRepository
from .repository_contracts import timestamp


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
STAGES = {"DEVELOPMENT", "GATE", "FINAL_REVIEW"}
ACTIVATION_FIELDS = {
    "sessionId",
    "executorId",
    "executionId",
    "nativeInvocationId",
    "mechanism",
    "status",
    "summary",
}
CONFORMANCE_FIELDS = {"status", "summary", "checks"}
CONFORMANCE_CHECK_FIELDS = {"name", "status", "evidence"}
ACTIVATION_EVENT = "SKILL_ACTIVATED"
CONFORMANCE_EVENT = "SKILL_CONFORMANCE_RECORDED"
NATIVE_SKILL_MECHANISM = "HOST_NATIVE_SKILL"


def _root_entry(
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in registry["workItems"]}
    current = entry
    visited: set[str] = set()
    while current["parentId"] is not None:
        if current["id"] in visited or current["parentId"] not in by_id:
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Work item hierarchy is invalid",
            )
        visited.add(current["id"])
        current = by_id[current["parentId"]]
    return current


def _node_id_for_stage(
    entry: dict[str, Any],
    root_entry: dict[str, Any],
    stage: str,
) -> str:
    if stage == "DEVELOPMENT":
        if entry["kind"] != "TASK":
            fail(
                "WORK_ITEM_SKILL_ACTIVATION_INVALID",
                "DEVELOPMENT Skill activation requires a Task",
            )
        return execution_node_id(entry["id"])
    if stage == "GATE":
        return gate_node_id(entry["id"])
    if entry["id"] != root_entry["id"]:
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            "FINAL_REVIEW Skill activation requires the requirement root",
        )
    return review_node_id(root_entry["id"])


def _current_node(
    repository: GovernanceRepository,
    root_entry: dict[str, Any],
    node_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = repository.read_graph_run(root_entry["id"])
    node = next(
        (item for item in run["nodes"] if item["nodeId"] == node_id),
        None,
    )
    if node is None:
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            "Required Skill stage does not have a current graph node",
        )
    return run, node


def _is_codex_runtime(host_runtime: object) -> bool:
    return bool(
        isinstance(host_runtime, str)
        and (
            host_runtime == "codex"
            or host_runtime.startswith("codex-")
            or host_runtime.startswith("codex.")
        )
    )


def _expected_mechanism(host_runtime: str) -> str:
    require_host_runtime(host_runtime)
    return NATIVE_SKILL_MECHANISM


def _mechanism_matches_runtime(
    host_runtime: object,
    mechanism: object,
) -> bool:
    if (
        is_agent_runtime(host_runtime)
        and mechanism == NATIVE_SKILL_MECHANISM
    ):
        return True
    # Preserve already-recorded schema-v3 receipts from pre-0.15.3 runs.
    if is_claude_runtime(host_runtime):
        return mechanism == "CLAUDE_SKILL_TOOL"
    if _is_codex_runtime(host_runtime):
        return mechanism == "CODEX_EXPLICIT_SKILL"
    return False


def _identifier(value: object) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


def _validated_activation(
    value: object,
    *,
    expected_mechanism: str,
) -> dict[str, str]:
    valid = (
        isinstance(value, dict)
        and set(value) == ACTIVATION_FIELDS
        and all(
            _identifier(value.get(field))
            for field in (
                "sessionId",
                "executorId",
                "executionId",
                "nativeInvocationId",
            )
        )
        and value.get("mechanism") == expected_mechanism
        and value.get("status") in {"INVOKED", "BLOCKED"}
        and concrete_skill_evidence(value.get("summary"))
    )
    if not valid:
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            (
                "The execution adapter must automatically invoke required "
                "Skills through the current host's native Skill mechanism; "
                "reading or loading SKILL.md alone is not an activation"
            ),
            expectedMechanism=expected_mechanism,
            authorizationSource="FROZEN_REQUIRED_SKILLS",
            userActionRequired=False,
            recoveryAction="EXECUTION_ADAPTER_AUTO_INVOKE",
        )
    return {
        field: value[field].strip() if isinstance(value[field], str) else value[field]
        for field in ACTIVATION_FIELDS
    }


def _validated_conformance(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != CONFORMANCE_FIELDS
        or value.get("status") not in {"PASS", "BLOCKED"}
        or not concrete_skill_evidence(value.get("summary"))
        or not isinstance(value.get("checks"), list)
        or not value["checks"]
    ):
        fail(
            "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
            "Skill conformance must contain a concrete summary and nonempty checks",
        )
    checks: list[dict[str, str]] = []
    for index, check in enumerate(value["checks"]):
        if (
            not isinstance(check, dict)
            or set(check) != CONFORMANCE_CHECK_FIELDS
            or not _identifier(check.get("name"))
            or check.get("status") not in {"PASS", "FAIL", "BLOCKED"}
            or not concrete_skill_evidence(check.get("evidence"))
        ):
            fail(
                "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
                f"Skill conformance check {index} is invalid",
            )
        checks.append({
            "name": check["name"].strip(),
            "status": check["status"],
            "evidence": check["evidence"].strip(),
        })
    if value["status"] == "PASS" and any(
        check["status"] != "PASS" for check in checks
    ):
        fail(
            "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
            "PASS conformance requires every check to pass",
        )
    if value["status"] == "BLOCKED" and all(
        check["status"] == "PASS" for check in checks
    ):
        fail(
            "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
            "BLOCKED conformance requires a failed or blocked check",
        )
    return {
        "status": value["status"],
        "summary": value["summary"].strip(),
        "checks": checks,
    }


def _required_skill(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    stage: str,
    skill_name: str,
) -> dict[str, Any]:
    required = repository.effective_required_skills(
        registry,
        entry,
        stage=stage,
    )
    match = next(
        (item for item in required if item["name"] == skill_name),
        None,
    )
    if match is None:
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            "Skill activation is not required by the frozen stage contract",
            skillName=skill_name,
            stage=stage,
        )
    return match


def _activation_payload_valid(payload: object) -> bool:
    expected = {
        "schemaVersion",
        "kind",
        "workItemId",
        "skillName",
        "stage",
        "hostRuntime",
        *ACTIVATION_FIELDS,
    }
    return bool(
        isinstance(payload, dict)
        and set(payload) == expected
        and payload.get("schemaVersion") == SCHEMA_VERSION
        and payload.get("kind") == "SKILL_ACTIVATION"
        and isinstance(payload.get("workItemId"), str)
        and isinstance(payload.get("skillName"), str)
        and payload.get("stage") in STAGES
        and isinstance(payload.get("hostRuntime"), str)
        and all(
            _identifier(payload.get(field))
            for field in (
                "sessionId",
                "executorId",
                "executionId",
                "nativeInvocationId",
            )
        )
        and _mechanism_matches_runtime(
            payload.get("hostRuntime"),
            payload.get("mechanism"),
        )
        and payload.get("status") in {"INVOKED", "BLOCKED"}
        and concrete_skill_evidence(payload.get("summary"))
    )


def _conformance_payload_valid(payload: object) -> bool:
    return bool(
        isinstance(payload, dict)
        and set(payload)
        == {
            "schemaVersion",
            "kind",
            "workItemId",
            "skillName",
            "stage",
            "activationReceiptId",
            "conformance",
            "conformanceSha256",
        }
        and payload.get("schemaVersion") == SCHEMA_VERSION
        and payload.get("kind") == "SKILL_CONFORMANCE"
        and isinstance(payload.get("workItemId"), str)
        and isinstance(payload.get("skillName"), str)
        and payload.get("stage") in STAGES
        and isinstance(payload.get("activationReceiptId"), str)
        and bool(FINGERPRINT.fullmatch(payload["activationReceiptId"]))
        and isinstance(payload.get("conformance"), dict)
        and isinstance(payload.get("conformanceSha256"), str)
        and bool(FINGERPRINT.fullmatch(payload["conformanceSha256"]))
        and sha256_bytes(
            canonical_json(payload["conformance"]).encode("utf-8")
        )
        == payload["conformanceSha256"]
    )


def is_skill_lifecycle_event_valid(event: dict[str, Any]) -> bool:
    """Validate one no-state-change Skill lifecycle graph event."""

    if event["eventType"] == ACTIVATION_EVENT:
        return (
            event.get("nodeId") is not None
            and event.get("attempt") is not None
            and event.get("operationId")
            == (event.get("payload") or {}).get("executionId")
            and _activation_payload_valid(event.get("payload"))
        )
    if event["eventType"] == CONFORMANCE_EVENT:
        return (
            event.get("nodeId") is not None
            and event.get("attempt") is not None
            and _conformance_payload_valid(event.get("payload"))
        )
    return False


def record_skill_activation(
    *,
    root: str,
    item_id: str,
    stage: str,
    skill_name: str,
    activation: object,
    execution_host_runtime: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Record an execution-adapter native invocation for one required Skill."""

    if stage not in STAGES or not isinstance(skill_name, str) or not SKILL_NAME.fullmatch(skill_name):
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_INVALID",
            "Skill activation stage or canonical name is invalid",
        )
    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        root_entry = _root_entry(registry, entry)
        _required_skill(
            repository,
            registry,
            entry,
            stage=stage,
            skill_name=skill_name,
        )
        node_id = _node_id_for_stage(entry, root_entry, stage)
        _, node = _current_node(repository, root_entry, node_id)
        if node["status"] != "READY":
            fail(
                "WORK_ITEM_SKILL_ACTIVATION_STAGE_INVALID",
                "Required Skill activation must occur before the stage starts",
                nodeId=node_id,
                nodeStatus=node["status"],
            )
        host_runtime = require_host_runtime(execution_host_runtime)
        value = _validated_activation(
            activation,
            expected_mechanism=_expected_mechanism(host_runtime),
        )
        reused = next(
            (
                event
                for event in repository.read_graph_events(
                    root_entry["id"]
                )
                if (
                    event["eventType"] == ACTIVATION_EVENT
                    and is_skill_lifecycle_event_valid(event)
                    and event["payload"]["hostRuntime"] == host_runtime
                    and event["payload"]["sessionId"]
                    == value["sessionId"]
                    and event["payload"]["nativeInvocationId"]
                    == value["nativeInvocationId"]
                )
            ),
            None,
        )
        if reused is not None:
            fail(
                "WORK_ITEM_SKILL_ACTIVATION_REUSED",
                (
                    "One native Skill invocation cannot satisfy multiple "
                    "required Skill activation records"
                ),
                activationReceiptId=reused["eventHash"],
                skillName=reused["payload"]["skillName"],
            )
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "SKILL_ACTIVATION",
            "workItemId": item_id,
            "skillName": skill_name,
            "stage": stage,
            "hostRuntime": host_runtime,
            **value,
        }
        event = repository.append_graph_event(
            root_id=root_entry["id"],
            node_id=node_id,
            event_type=ACTIVATION_EVENT,
            actor="AGENT",
            operation_id=value["executionId"],
            payload=payload,
            recorded_at=at,
        )
        registry["revision"] += 1
        registry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "SKILL_ACTIVATION",
        }
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(
                registry,
                item_id,
            ),
        )
        return {
            "activationReceiptId": event["eventHash"],
            "workItemId": item_id,
            "nodeId": node_id,
            "attempt": node["attempt"],
            "skillName": skill_name,
            "stage": stage,
            "hostRuntime": host_runtime,
            "mechanism": value["mechanism"],
            "status": value["status"],
            "sessionId": value["sessionId"],
            "executorId": value["executorId"],
            "executionId": value["executionId"],
            "nativeInvocationId": value["nativeInvocationId"],
            "activatedAt": at,
        }


def _activation_event(
    repository: GovernanceRepository,
    root_entry: dict[str, Any],
    receipt_id: str,
) -> dict[str, Any]:
    if not isinstance(receipt_id, str) or not FINGERPRINT.fullmatch(receipt_id):
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_RECEIPT_INVALID",
            "Skill activation receipt identifier is invalid",
        )
    event = next(
        (
            item
            for item in repository.read_graph_events(root_entry["id"])
            if item["eventHash"] == receipt_id
        ),
        None,
    )
    if (
        event is None
        or event["eventType"] != ACTIVATION_EVENT
        or not is_skill_lifecycle_event_valid(event)
    ):
        fail(
            "WORK_ITEM_SKILL_ACTIVATION_RECEIPT_INVALID",
            "Skill activation receipt is missing or invalid",
        )
    return event


def record_skill_conformance(
    *,
    root: str,
    item_id: str,
    activation_receipt_id: str,
    conformance: object,
    execution_host_runtime: str | None = None,
    explicit_dogfood: bool = False,
    now: object = None,
) -> dict[str, Any]:
    """Record actual Skill completion checks bound to a native invocation."""

    repository = GovernanceRepository(root, now=now)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    at = timestamp(now)
    with repository.transaction() as registry:
        entry = repository.item_by_id(registry, item_id)
        root_entry = _root_entry(registry, entry)
        activation_event = _activation_event(
            repository,
            root_entry,
            activation_receipt_id,
        )
        activation_payload = activation_event["payload"]
        current_host_runtime = require_host_runtime(
            execution_host_runtime
        )
        if current_host_runtime != activation_payload["hostRuntime"]:
            fail(
                "WORK_ITEM_SKILL_CONFORMANCE_HOST_MISMATCH",
                (
                    "Skill conformance must be recorded by the same "
                    "execution host as its native activation"
                ),
                activationHostRuntime=activation_payload["hostRuntime"],
                executionHostRuntime=current_host_runtime,
            )
        if activation_payload["workItemId"] != item_id:
            fail(
                "WORK_ITEM_SKILL_ACTIVATION_RECEIPT_INVALID",
                "Skill activation receipt belongs to another work item",
            )
        node_id = _node_id_for_stage(
            entry,
            root_entry,
            activation_payload["stage"],
        )
        _, node = _current_node(repository, root_entry, node_id)
        if (
            activation_event["nodeId"] != node_id
            or activation_event["attempt"] != node["attempt"]
        ):
            fail(
                "WORK_ITEM_SKILL_ACTIVATION_RECEIPT_INVALID",
                "Skill activation receipt belongs to an obsolete node attempt",
            )
        if activation_payload["stage"] == "DEVELOPMENT":
            if (
                node["status"] != "CLAIMED"
                or node["operationId"]
                != activation_payload["executionId"]
            ):
                fail(
                    "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
                    "Development conformance requires the matching active operation",
                )
        elif node["status"] != "READY":
            fail(
                "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
                "Gate or review conformance requires the current ready stage",
            )
        value = _validated_conformance(conformance)
        if any(
            event["eventType"] == CONFORMANCE_EVENT
            and is_skill_lifecycle_event_valid(event)
            and event["payload"]["activationReceiptId"]
            == activation_receipt_id
            for event in repository.read_graph_events(root_entry["id"])
        ):
            fail(
                "WORK_ITEM_SKILL_CONFORMANCE_ALREADY_RECORDED",
                "A Skill activation receipt already has a conformance result",
                activationReceiptId=activation_receipt_id,
            )
        if (
            value["status"] == "PASS"
            and activation_payload["status"] != "INVOKED"
        ):
            fail(
                "WORK_ITEM_SKILL_CONFORMANCE_INVALID",
                "A blocked native invocation cannot produce PASS conformance",
            )
        conformance_sha256 = sha256_bytes(
            canonical_json(value).encode("utf-8")
        )
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "SKILL_CONFORMANCE",
            "workItemId": item_id,
            "skillName": activation_payload["skillName"],
            "stage": activation_payload["stage"],
            "activationReceiptId": activation_receipt_id,
            "conformance": value,
            "conformanceSha256": conformance_sha256,
        }
        event = repository.append_graph_event(
            root_id=root_entry["id"],
            node_id=node_id,
            event_type=CONFORMANCE_EVENT,
            actor="AGENT",
            operation_id=activation_payload["executionId"],
            payload=payload,
            recorded_at=at,
        )
        registry["revision"] += 1
        registry["updatedAt"] = at
        registry["currentFocus"] = {
            "workItemId": item_id,
            "purpose": "SKILL_CONFORMANCE",
        }
        repository.write_registry(
            registry,
            changed_item_ids=repository.lineage_item_ids(
                registry,
                item_id,
            ),
        )
        return {
            "conformanceReceiptId": event["eventHash"],
            "activationReceiptId": activation_receipt_id,
            "workItemId": item_id,
            "nodeId": node_id,
            "attempt": node["attempt"],
            "skillName": activation_payload["skillName"],
            "stage": activation_payload["stage"],
            "status": value["status"],
            "conformanceSha256": conformance_sha256,
            "recordedAt": at,
        }


def _skill_execution_records(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    stage: str,
    operation_id: str | None = None,
    executor_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root_entry = _root_entry(registry, entry)
    node_id = _node_id_for_stage(entry, root_entry, stage)
    _, node = _current_node(repository, root_entry, node_id)
    events = repository.read_graph_events(root_entry["id"])
    activations = [
        event
        for event in events
        if (
            event["eventType"] == ACTIVATION_EVENT
            and event["nodeId"] == node_id
            and event["attempt"] == node["attempt"]
            and is_skill_lifecycle_event_valid(event)
            and event["payload"]["workItemId"] == entry["id"]
            and event["payload"]["stage"] == stage
            and (
                operation_id is None
                or event["payload"]["executionId"] == operation_id
            )
            and (
                executor_id is None
                or event["payload"]["executorId"] == executor_id
            )
        )
    ]
    conformances = [
        event
        for event in events
        if (
            event["eventType"] == CONFORMANCE_EVENT
            and event["nodeId"] == node_id
            and event["attempt"] == node["attempt"]
            and is_skill_lifecycle_event_valid(event)
            and event["payload"]["workItemId"] == entry["id"]
            and event["payload"]["stage"] == stage
        )
    ]
    return activations, conformances


def assert_required_skill_activations(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    stage: str,
    operation_id: str | None = None,
    executor_id: str | None = None,
) -> list[dict[str, Any]]:
    """Require graph-bound native invocation receipts for all frozen Skills."""

    required = repository.effective_required_skills(
        registry,
        entry,
        stage=stage,
    )
    if not required:
        return []
    activations, _ = _skill_execution_records(
        repository,
        registry,
        entry,
        stage=stage,
        operation_id=operation_id,
        executor_id=executor_id,
    )
    records: list[dict[str, Any]] = []
    for requirement in required:
        matches = [
            event
            for event in activations
            if event["payload"]["skillName"] == requirement["name"]
        ]
        if not matches:
            fail(
                "WORK_ITEM_REQUIRED_SKILL_ACTIVATION_MISSING",
                (
                    "A frozen required Skill has no graph-bound native "
                    "invocation receipt; the execution adapter must invoke "
                    "it automatically and record activation without asking "
                    "the user to authorize or trigger the Skill again"
                ),
                skillName=requirement["name"],
                stage=stage,
                requiredMechanism=NATIVE_SKILL_MECHANISM,
                authorizationSource="FROZEN_REQUIRED_SKILLS",
                userActionRequired=False,
                recoveryAction="EXECUTION_ADAPTER_AUTO_INVOKE",
            )
        records.append(matches[-1])
    return records


def assert_required_skill_conformance(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
    *,
    stage: str,
    skill_usage: list[dict[str, Any]],
    operation_id: str | None = None,
    executor_id: str | None = None,
    require_pass: bool,
) -> list[dict[str, Any]]:
    """Require native activation plus actual conformance for every Skill."""

    activations = assert_required_skill_activations(
        repository,
        registry,
        entry,
        stage=stage,
        operation_id=operation_id,
        executor_id=executor_id,
    )
    if not activations:
        return []
    _, conformances = _skill_execution_records(
        repository,
        registry,
        entry,
        stage=stage,
        operation_id=operation_id,
        executor_id=executor_id,
    )
    usage_by_name = {
        usage["name"]: usage
        for usage in skill_usage
        if isinstance(usage, dict)
    }
    records: list[dict[str, Any]] = []
    for activation in activations:
        payload = activation["payload"]
        matching = [
            event
            for event in conformances
            if (
                event["payload"]["activationReceiptId"]
                == activation["eventHash"]
                and event["payload"]["skillName"]
                == payload["skillName"]
            )
        ]
        if not matching:
            fail(
                "WORK_ITEM_REQUIRED_SKILL_CONFORMANCE_MISSING",
                "A required Skill has no graph-bound conformance result",
                skillName=payload["skillName"],
                stage=stage,
                activationReceiptId=activation["eventHash"],
            )
        conformance_event = matching[-1]
        conformance = conformance_event["payload"]["conformance"]
        usage = usage_by_name.get(payload["skillName"], {})
        expected_pass = require_pass or usage.get("status") == "APPLIED"
        if expected_pass and (
            payload["status"] != "INVOKED"
            or conformance["status"] != "PASS"
        ):
            fail(
                "WORK_ITEM_REQUIRED_SKILL_CONFORMANCE_FAILED",
                "A required Skill did not pass its actual conformance checks",
                skillName=payload["skillName"],
                stage=stage,
                activationStatus=payload["status"],
                conformanceStatus=conformance["status"],
            )
        if not expected_pass and conformance["status"] != "BLOCKED":
            fail(
                "WORK_ITEM_REQUIRED_SKILL_CONFORMANCE_FAILED",
                "Blocked Skill usage must have BLOCKED conformance",
                skillName=payload["skillName"],
                stage=stage,
            )
        records.append({
            "workItemId": entry["id"],
            "nodeId": activation["nodeId"],
            "attempt": activation["attempt"],
            "skillName": payload["skillName"],
            "stage": stage,
            "hostRuntime": payload["hostRuntime"],
            "mechanism": payload["mechanism"],
            "activationStatus": payload["status"],
            "sessionId": payload["sessionId"],
            "executorId": payload["executorId"],
            "executionId": payload["executionId"],
            "nativeInvocationId": payload["nativeInvocationId"],
            "activationReceiptId": activation["eventHash"],
            "activatedAt": activation["recordedAt"],
            "conformanceStatus": conformance["status"],
            "conformanceSummary": conformance["summary"],
            "conformanceChecks": deepcopy(conformance["checks"]),
            "conformanceReceiptId": conformance_event["eventHash"],
            "conformanceRecordedAt": conformance_event["recordedAt"],
        })
    return records


def skill_execution_audit(
    repository: GovernanceRepository,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return actual invocation and conformance records for a subtree."""

    by_id = {item["id"]: item for item in registry["workItems"]}
    subtree: set[str] = set()

    def visit(current: dict[str, Any]) -> None:
        if current["id"] in subtree:
            fail(
                "WORK_ITEM_HIERARCHY_CYCLE",
                "Work item hierarchy contains a cycle",
            )
        subtree.add(current["id"])
        for child_id in current["childIds"]:
            child = by_id.get(child_id)
            if child is None:
                fail(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    f"Work item child is missing: {child_id}",
                )
            visit(child)

    visit(entry)
    root_entry = _root_entry(registry, entry)
    events = repository.read_graph_events(root_entry["id"])
    conformance_by_activation = {
        event["payload"]["activationReceiptId"]: event
        for event in events
        if (
            event["eventType"] == CONFORMANCE_EVENT
            and is_skill_lifecycle_event_valid(event)
        )
    }
    records: list[dict[str, Any]] = []
    for activation in events:
        if (
            activation["eventType"] != ACTIVATION_EVENT
            or not is_skill_lifecycle_event_valid(activation)
            or activation["payload"]["workItemId"] not in subtree
        ):
            continue
        payload = activation["payload"]
        conformance_event = conformance_by_activation.get(
            activation["eventHash"]
        )
        if conformance_event is not None and (
            conformance_event["nodeId"] != activation["nodeId"]
            or conformance_event["attempt"] != activation["attempt"]
            or conformance_event["payload"]["workItemId"]
            != payload["workItemId"]
            or conformance_event["payload"]["skillName"]
            != payload["skillName"]
            or conformance_event["payload"]["stage"] != payload["stage"]
        ):
            conformance_event = None
        conformance = (
            conformance_event["payload"]["conformance"]
            if conformance_event is not None
            else None
        )
        records.append({
            "workItemId": payload["workItemId"],
            "nodeId": activation["nodeId"],
            "attempt": activation["attempt"],
            "skillName": payload["skillName"],
            "stage": payload["stage"],
            "hostRuntime": payload["hostRuntime"],
            "mechanism": payload["mechanism"],
            "activationStatus": payload["status"],
            "sessionId": payload["sessionId"],
            "executorId": payload["executorId"],
            "executionId": payload["executionId"],
            "nativeInvocationId": payload["nativeInvocationId"],
            "activationReceiptId": activation["eventHash"],
            "activatedAt": activation["recordedAt"],
            "conformanceStatus": (
                conformance["status"]
                if conformance is not None
                else "NOT_RECORDED"
            ),
            "conformanceSummary": (
                conformance["summary"]
                if conformance is not None
                else None
            ),
            "conformanceChecks": (
                deepcopy(conformance["checks"])
                if conformance is not None
                else []
            ),
            "conformanceReceiptId": (
                conformance_event["eventHash"]
                if conformance_event is not None
                else None
            ),
            "conformanceRecordedAt": (
                conformance_event["recordedAt"]
                if conformance_event is not None
                else None
            ),
        })
    return records
