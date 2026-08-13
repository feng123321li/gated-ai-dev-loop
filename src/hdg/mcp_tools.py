from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .controller import (
    CONTROLLER_OPERATIONS,
    ControllerContext,
    DEFAULT_CONTROLLER,
    LayeredDeliveryController,
)
from .errors import GatedLoopError, fail
from .fs_safe import read_regular_file
from .hierarchy_contract import hierarchy_input_schema
from .jsonio import canonical_json
from .mcp_apps import DASHBOARD_RESOURCE_URI
from .model_core import validate_hierarchy_definition


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "description": description,
    }


def _bounded_string(
    description: str,
    *,
    maximum: int,
) -> dict[str, Any]:
    value = _string(description)
    value["maxLength"] = maximum
    return value


ROOT_ID = _string("Frozen Delivery and Graph run ID.")
BASE_REF = {
    "type": "string",
    "minLength": 1,
    "maxLength": 240,
    "description": (
        "Optional host-selected mainline branch name. It takes priority "
        "over origin/HEAD during unbound Git workspace discovery."
    ),
}
DIRTY_STATE_FINGERPRINT = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": "^[0-9a-f]{64}$",
    "description": (
        "Exact workingTree.stateFingerprint returned immediately before "
        "the user confirmed that all current changes belong to the Delivery "
        "whose current dirty branch is being adopted. A transition to another "
        "Delivery branch uses automaticHostPreparation instead."
    ),
}
NODE_ID = _string("Exact graph node ID from graph_frontier.")
OPERATION_ID = _string(
    "Globally unique Loop operation ID returned by dispatch_loop. The "
    "outer receiver must supply the exact claim value for every subsequent "
    "Loop mutation."
)
SCHEDULER_IDENTITY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 192,
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}$",
    "description": (
        "Portable current Loop executor label. Use the native agent_id "
        "when the host does not supply a separate label (for example "
        "claude-code). Only letters, digits, dot, underscore, colon, at, "
        "slash, and hyphen are accepted; spaces and # are invalid. This "
        "is not receiver_context_id."
    ),
}
FINGERPRINT = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "description": "Exact SHA-256 fingerprint returned by the controller.",
}

def _text_array(
    description: str,
    *,
    maximum: int = 128,
    minimum: int = 0,
) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": minimum,
        "maxItems": maximum,
        "items": _bounded_string(description, maximum=1024),
    }


def _review_findings_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 128,
        "items": _object(
            {
                "severity": {
                    "type": "string",
                    "enum": ["P0", "P1", "P2"],
                },
                "summary": _bounded_string(
                    "Finding summary.", maximum=1024
                ),
                "status": {
                    "type": "string",
                    "enum": ["RESOLVED", "ACCEPTED", "OPEN"],
                },
                "resolution": _bounded_string(
                    "Resolution or acceptance rationale.", maximum=2048
                ),
                "evidence": _bounded_string(
                    "Evidence supporting the final status.", maximum=2048
                ),
            },
            required=[
                "severity",
                "summary",
                "status",
                "resolution",
                "evidence",
            ],
        ),
    }


def _task_acceptance_schema() -> dict[str, Any]:
    return _object(
        {
            "acceptanceChecks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _object(
                    {
                        "acceptancePoint": _bounded_string(
                            "Frozen TASK acceptance point.", maximum=2048
                        ),
                        "status": {"const": "SATISFIED"},
                        "evidenceRefs": _text_array(
                            "TASK evidence reference.", minimum=1
                        ),
                    },
                    required=[
                        "acceptancePoint",
                        "status",
                        "evidenceRefs",
                    ],
                ),
            },
            "localBehavior": {"const": "VERIFIED"},
            "publicContract": {
                "type": "string",
                "enum": ["VERIFIED", "NOT_APPLICABLE"],
            },
            "targetedRegression": {"const": "VERIFIED"},
            "decision": {"const": "ACCEPTED"},
            "rationale": _bounded_string(
                "Why the TASK-owned boundary is accepted.", maximum=4096
            ),
        },
        required=[
            "acceptanceChecks",
            "localBehavior",
            "publicContract",
            "targetedRegression",
            "decision",
            "rationale",
        ],
    )


def _group_integration_schema() -> dict[str, Any]:
    return _object(
        {
            "seams": {
                "type": "array",
                "minItems": 1,
                "maxItems": 128,
                "items": _object(
                    {
                        "seam": _bounded_string(
                            "Direct-child seam being verified.", maximum=2048
                        ),
                        "participants": _text_array(
                            "Direct child participating in the seam.",
                            minimum=2,
                        ),
                        "status": {"const": "VERIFIED"},
                        "evidenceRefs": _text_array(
                            "GROUP seam evidence reference.", minimum=1
                        ),
                    },
                    required=[
                        "seam",
                        "participants",
                        "status",
                        "evidenceRefs",
                    ],
                ),
            },
            "decision": {"const": "INTEGRATED"},
            "rationale": _bounded_string(
                "Why the direct children compose correctly.", maximum=4096
            ),
        },
        required=["seams", "decision", "rationale"],
    )


def _delivery_readiness_schema() -> dict[str, Any]:
    return _object(
        {
            "requirementCoverage": {
                "type": "array",
                "minItems": 1,
                "maxItems": 256,
                "items": _object(
                    {
                        "acceptancePoint": _bounded_string(
                            "Top-level Delivery acceptance point.", maximum=2048
                        ),
                        "ownerRefs": _text_array(
                            "TASK or GROUP acceptance owner reference.",
                            minimum=1,
                        ),
                        "status": {"const": "COVERED"},
                        "evidenceRefs": _text_array(
                            "Final evidence reference.", minimum=1
                        ),
                    },
                    required=[
                        "acceptancePoint",
                        "ownerRefs",
                        "status",
                        "evidenceRefs",
                    ],
                ),
            },
            "integrationEvidence": {"const": "SUFFICIENT"},
            "operationalReadiness": {
                "type": "string",
                "enum": ["READY", "NOT_APPLICABLE"],
            },
            "openBlockingRisks": {"type": "array", "maxItems": 0},
            "acceptedRisks": _text_array(
                "Explicitly accepted non-blocking Delivery risk."
            ),
            "decision": {"const": "READY_FOR_USER_CONFIRMATION"},
            "rationale": _bounded_string(
                "Why the Delivery is ready for final user confirmation.",
                maximum=4096,
            ),
        },
        required=[
            "requirementCoverage",
            "integrationEvidence",
            "operationalReadiness",
            "openBlockingRisks",
            "acceptedRisks",
            "decision",
            "rationale",
        ],
    )


OUTCOME = _object(
    {
        "status": {
            "type": "string",
            "enum": [
                "SUCCEEDED",
                "BLOCKED",
                "REPLAN_REQUIRED",
                "CANCELLED",
            ],
            "description": (
                "Genuine terminal status. Correctable implementation, test, "
                "Gate, or Review findings are not BLOCKED; resolve and "
                "reevaluate them inside the current Loop."
            ),
        },
        "summary": _string("Concise Loop-owned result summary."),
        "result": {
            "type": "object",
            "description": (
                "Opaque Loop-owned result payload. When the receiving "
                "Agent used internal workers, workerTelemetry reports "
                "display-only phase evidence; it never grants Graph "
                "authority or affects acceptance. On record_loop_result, "
                "the Controller replaces result.workspaceChanges with "
                "read-only snapshots captured from verified writable Git "
                "scopes; callers must not treat that snapshot as exclusive "
                "TASK or Delivery ownership. A successful Review is narrower: "
                "the independent receiver owns the technical acceptance "
                "judgment; the Controller validates only structure and "
                "declared terminal consistency. Therefore persist only "
                "validationDecision, reviewFindings, the one "
                "layer-owned acceptance field, bounded evidence metadata, and "
                "Controller-owned snapshots. Never copy upstreamLoopResults "
                "or lower-layer result bodies into a Review outcome."
            ),
            "properties": {
                "affectedScopes": {
                    "type": "array",
                    "maxItems": 64,
                    "description": (
                        "Loop-declared bounded change and risk scopes used to "
                        "explain targeted verification coverage."
                    ),
                    "items": _object(
                        {
                            "scopeId": _bounded_string(
                                "Stable scope ID within this Loop result.",
                                maximum=192,
                            ),
                            "projectId": _bounded_string(
                                "Verified project scope ID.",
                                maximum=192,
                            ),
                            "paths": {
                                "type": "array",
                                "maxItems": 256,
                                "items": _bounded_string(
                                    "Repository-relative affected path.",
                                    maximum=1024,
                                ),
                            },
                            "modules": {
                                "type": "array",
                                "maxItems": 128,
                                "items": _bounded_string(
                                    "Affected build or runtime module.",
                                    maximum=512,
                                ),
                            },
                            "contracts": {
                                "type": "array",
                                "maxItems": 128,
                                "items": _bounded_string(
                                    "Affected public or internal contract.",
                                    maximum=1024,
                                ),
                            },
                            "dependencyBasis": _bounded_string(
                                "Why these dependents and boundaries are in scope.",
                                maximum=2048,
                            ),
                            "exclusions": {
                                "type": "array",
                                "maxItems": 128,
                                "items": _bounded_string(
                                    "Checked exclusion with a concise reason.",
                                    maximum=1024,
                                ),
                            },
                        },
                        required=[
                            "scopeId",
                            "projectId",
                            "paths",
                            "modules",
                            "contracts",
                            "dependencyBasis",
                            "exclusions",
                        ],
                    ),
                },
                "verificationEvidence": {
                    "type": "array",
                    "maxItems": 128,
                    "description": (
                        "Bounded Loop-reported checks. Reviewers may reuse only "
                        "passing evidence whose scope and tested workspace "
                        "snapshots still match the relevant code state."
                    ),
                    "items": _object(
                        {
                            "evidenceId": _bounded_string(
                                "Stable reference unique within this Loop result.",
                                maximum=192,
                            ),
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "TEST",
                                    "BUILD",
                                    "STATIC",
                                    "CONTRACT",
                                    "INSPECTION",
                                    "SMOKE",
                                    "E2E",
                                ],
                            },
                            "check": _bounded_string(
                                "Auditable check or suite name.",
                                maximum=512,
                            ),
                            "command": _bounded_string(
                                "Sanitized command or invocation summary.",
                                maximum=2048,
                            ),
                            "scope": _bounded_string(
                                "Files, module, contract, or behavior covered.",
                                maximum=2048,
                            ),
                            "scopeRefs": {
                                "type": "array",
                                "maxItems": 64,
                                "items": _bounded_string(
                                    "scopeId covered by this evidence.",
                                    maximum=192,
                                ),
                            },
                            "status": {
                                "type": "string",
                                "enum": ["PASSED", "FAILED", "SKIPPED"],
                            },
                            "tests": _object(
                                {
                                    "total": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "passed": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "failed": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                    "skipped": {
                                        "type": "integer",
                                        "minimum": 0,
                                    },
                                },
                                required=[
                                    "total",
                                    "passed",
                                    "failed",
                                    "skipped",
                                ],
                            ),
                            "completedAt": _bounded_string(
                                "ISO 8601 completion timestamp.",
                                maximum=64,
                            ),
                            "testedWorkspaceSnapshots": {
                                "type": "array",
                                "maxItems": 32,
                                "items": _object(
                                    {
                                        "projectId": _bounded_string(
                                            "Verified project scope ID.",
                                            maximum=192,
                                        ),
                                        "bindingState": {"const": "BOUND"},
                                        "headCommit": _bounded_string(
                                            "Git HEAD tested by this check.",
                                            maximum=128,
                                        ),
                                        "workingTreeStateFingerprint": FINGERPRINT,
                                    },
                                    required=[
                                        "projectId",
                                        "bindingState",
                                        "headCommit",
                                        "workingTreeStateFingerprint",
                                    ],
                                ),
                            },
                        },
                        required=[
                            "evidenceId",
                            "kind",
                            "check",
                            "command",
                            "scope",
                            "status",
                            "completedAt",
                        ],
                    ),
                },
                "evidenceWorkspaceSnapshots": {
                    "type": "array",
                    "maxItems": 32,
                    "description": (
                        "Controller-owned lightweight workspace state captured "
                        "with the terminal result. Caller input is overwritten."
                    ),
                    "items": _object(
                        {
                            "projectId": _bounded_string(
                                "Verified project scope ID.",
                                maximum=192,
                            ),
                            "bindingState": {
                                "type": "string",
                                "enum": ["BOUND", "UNBOUND", "UNSTABLE"],
                            },
                            "headCommit": _bounded_string(
                                "Git HEAD captured with the result.",
                                maximum=128,
                            ),
                            "workingTreeStateFingerprint": FINGERPRINT,
                        },
                        required=["projectId", "bindingState"],
                    ),
                },
                "evidenceScopeSnapshots": {
                    "type": "array",
                    "maxItems": 64,
                    "description": (
                        "Controller-owned state of the affected scope's "
                        "declared relevant paths. Unrelated workspace edits "
                        "do not invalidate a BOUND scope fingerprint."
                    ),
                    "items": _object(
                        {
                            "scopeId": _bounded_string(
                                "affectedScopes scope ID.",
                                maximum=192,
                            ),
                            "projectId": _bounded_string(
                                "Verified project scope ID.",
                                maximum=192,
                            ),
                            "paths": {
                                "type": "array",
                                "maxItems": 256,
                                "items": _bounded_string(
                                    "Literal repository-relative relevant path.",
                                    maximum=1024,
                                ),
                            },
                            "bindingState": {
                                "type": "string",
                                "enum": ["BOUND", "UNBOUND", "UNSTABLE"],
                            },
                            "stateFingerprint": FINGERPRINT,
                            "fileCount": {
                                "type": "integer",
                                "minimum": 0,
                            },
                        },
                        required=[
                            "scopeId",
                            "projectId",
                            "paths",
                            "bindingState",
                        ],
                    ),
                },
                "validationDecision": _object(
                    {
                        "decision": {
                            "type": "string",
                            "enum": [
                                "REUSED",
                                "TARGETED_RERUN",
                                "FULL_RERUN",
                            ],
                        },
                        "reusedEvidenceRefs": {
                            "type": "array",
                            "maxItems": 128,
                            "items": _object(
                                {
                                    "nodeId": NODE_ID,
                                    "attempt": {
                                        "type": "integer",
                                        "minimum": 1,
                                    },
                                    "evidenceId": _bounded_string(
                                        "Evidence ID within the source Loop result.",
                                        maximum=192,
                                    ),
                                },
                                required=["nodeId", "attempt", "evidenceId"],
                            ),
                        },
                        "executedEvidenceRefs": {
                            "type": "array",
                            "maxItems": 128,
                            "items": _bounded_string(
                                "evidenceId from this Review result.",
                                maximum=192,
                            ),
                        },
                        "riskTriggers": {
                            "type": "array",
                            "maxItems": 64,
                            "items": _bounded_string(
                                "Risk or evidence gap that caused rerun scope.",
                                maximum=1024,
                            ),
                        },
                        "rationale": _bounded_string(
                            "Concise independent Review rationale.",
                            maximum=4096,
                        ),
                    },
                    required=[
                        "decision",
                        "reusedEvidenceRefs",
                        "executedEvidenceRefs",
                        "riskTriggers",
                        "rationale",
                    ],
                ),
                "reviewFindings": _review_findings_schema(),
                "taskAcceptance": _task_acceptance_schema(),
                "groupIntegration": _group_integration_schema(),
                "deliveryReadiness": _delivery_readiness_schema(),
                "workerTelemetry": {
                    "type": "array",
                    "maxItems": 128,
                    "items": _object(
                        {
                            "phase": _string(
                                "Loop-internal phase, such as implementation, review, or test."
                            ),
                            "agent": _string(
                                "Observed or self-reported Agent ID; use the literal unreported when unknown."
                            ),
                            "model": _string(
                                "Observed or self-reported model ID; use the literal unreported when unknown."
                            ),
                            "reasoningEffort": _string(
                                "Reported effort, for example low, medium, high, max, or ultra; use unreported when unknown."
                            ),
                            "role": _string(
                                "Optional worker role reported by the outer receiver."
                            ),
                            "provenance": {
                                "type": "string",
                                "enum": [
                                    "HOST_EVENT",
                                    "HOST_TOOL_RESULT",
                                    "WORKER_SELF_REPORT",
                                    "LOCAL_CONFIG",
                                ],
                            },
                            "status": _string(
                                "Phase status reported by the outer receiver."
                            ),
                            "summary": _string(
                                "Bounded display summary without prompts or transcripts."
                            ),
                            "displayOnly": {"const": True},
                            "nonAuthoritative": {"const": True},
                        },
                        required=[
                            "phase",
                            "agent",
                            "model",
                            "reasoningEffort",
                        ],
                    ),
                }
            },
            "additionalProperties": True,
        },
    },
    required=["status", "summary", "result"],
)


TOOL_OUTPUT_SCHEMA = _object(
    {
        "ok": {"type": "boolean"},
        "result": {
            "type": "object",
            "additionalProperties": True,
        },
        "error": _object(
            {
                "code": _string("Stable Delivery Graph error code."),
                "message": _string("Human-readable error summary."),
                "details": {
                    "type": "object",
                    "additionalProperties": True,
                },
            },
            required=["code", "message", "details"],
        ),
    },
    required=["ok"],
)

READ_ONLY_TOOLS = frozenset(
    {
        "workspace_status",
        "recommend_assurance_profile",
        "hierarchy_contract",
        "delivery_revision_history",
        "graph_frontier",
        "graph_status",
        "open_delivery_dashboard",
        "graph_events",
        "loop_context",
    }
)

DESTRUCTIVE_TOOLS = frozenset(
    {
        "archive_delivery",
        "cancel_graph_run",
        "rebuild_graph_run",
        "unfreeze_task_requirement",
    }
)


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    human: bool = False,
    title: str | None = None,
    annotations: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_value = deepcopy(schema)
    result: dict[str, Any] = {
        "name": name,
        "title": title or name.replace("_", " ").title(),
        "description": description,
        "inputSchema": schema_value,
        "outputSchema": deepcopy(TOOL_OUTPUT_SCHEMA),
    }
    default_annotations = {
        "readOnlyHint": name in READ_ONLY_TOOLS,
        "destructiveHint": name in DESTRUCTIVE_TOOLS,
        "idempotentHint": name in READ_ONLY_TOOLS,
        "openWorldHint": False,
    }
    if annotations is not None:
        default_annotations.update(deepcopy(annotations))
    result["annotations"] = default_annotations
    tool_meta = deepcopy(meta) if meta is not None else {}
    if human:
        tool_meta["anthropic/requiresUserInteraction"] = True
    if tool_meta:
        result["_meta"] = tool_meta
    return result


def _prepare_hierarchy_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {
            "hierarchy": hierarchy_schema,
            "hierarchy_file": _string(
                "Path (workspace-relative preferred) to a UTF-8 JSON file "
                "containing the hierarchy object. Mutually exclusive with "
                "hierarchy; the controller reads and validates it. Use when "
                "the hierarchy is too large to emit inline."
            ),
        },
        required=[],
    )
    tool_schema["$defs"] = definitions
    return tool_schema


def _manual_handoff_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {
            "hierarchy": hierarchy_schema,
            "hierarchy_file": _string(
                "Path (workspace-relative preferred) to a UTF-8 JSON file "
                "containing the hierarchy object. Mutually exclusive with "
                "hierarchy; the controller reads and validates it. Use when "
                "the hierarchy is too large to emit inline."
            ),
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "authorized_project_ids": {
                "type": "array",
                "items": ROOT_ID,
                "uniqueItems": True,
                "description": (
                    "Exact project IDs authorized for the handoff; use an "
                    "empty array when projectScopes is absent."
                ),
            },
            "expected_current_revision": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Current HANDOFF_READY revision when explicitly "
                    "revising the same manual Delivery. Omit for Revision 1."
                ),
            },
            "continuity_basis": {
                "type": "string",
                "enum": ["USER_EXPLICIT_SAME_DELIVERY"],
                "description": (
                    "Required with changed manual content to prove the user "
                    "explicitly continued the same Delivery."
                ),
            },
            "revision_reason": _string(
                "Required explanation of the changed manual requirement."
            ),
            "confirmed_by": _string("Human confirmer identity."),
        },
        required=[
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
            "authorized_project_ids",
            "confirmed_by",
        ],
    )
    tool_schema["$defs"] = definitions
    return tool_schema


def _execution_choice_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "selection": {
                "type": "string",
                "enum": ["AUTOMATIC", "MANUAL"],
                "description": (
                    "Exact option ID from the EXECUTION_MODE "
                    "pendingInteraction.options."
                ),
            },
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "authorized_project_ids": {
                "type": "array",
                "items": ROOT_ID,
                "uniqueItems": True,
                "description": (
                    "Exact project IDs authorized for the selected mode."
                ),
            },
            "confirmed_by": _string("Human selector identity."),
        },
        required=[
            "root_id",
            "selection",
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
            "authorized_project_ids",
            "confirmed_by",
        ],
    )


def _development_baseline_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "selection": {
                "type": "string",
                "description": (
                    "A local branch_ref from the DEVELOPMENT_BASELINE "
                    "pendingInteraction.options, NEW_FROM_MAINLINE, or "
                    "NEW_FROM_CURRENT_BRANCH."
                ),
            },
            "branch_name": {
                "type": "string",
                "description": (
                    "Required for NEW_FROM_MAINLINE and "
                    "NEW_FROM_CURRENT_BRANCH: the new Delivery branch name "
                    "the host creates from the frozen base."
                ),
            },
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "expected_delivery_revision": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Required when reconfirming a blocked manual handoff."
                ),
            },
            "baseline_context_fingerprint": FINGERPRINT,
            "confirmed_dirty_state_fingerprint": (
                DIRTY_STATE_FINGERPRINT
            ),
            "confirmed_by": _string("Human confirmer identity."),
        },
        required=[
            "root_id",
            "selection",
            "expected_hierarchy_fingerprint",
            "confirmed_by",
        ],
    )


def _execution_resume_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
        },
        required=[
            "root_id",
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
        ],
    )


def _manual_start_tool_schema() -> dict[str, Any]:
    return _object(
        {
            "root_id": ROOT_ID,
            "expected_hierarchy_fingerprint": FINGERPRINT,
            "expected_graph_fingerprint": FINGERPRINT,
            "started_by": SCHEDULER_IDENTITY,
        },
        required=[
            "root_id",
            "expected_hierarchy_fingerprint",
            "expected_graph_fingerprint",
            "started_by",
        ],
    )


def _prepare_revision_tool_schema() -> dict[str, Any]:
    hierarchy_schema = hierarchy_input_schema()
    definitions = hierarchy_schema.pop("$defs")
    tool_schema = _object(
        {
            "root_id": ROOT_ID,
            "expected_current_revision": {
                "type": "integer",
                "minimum": 1,
            },
            "hierarchy": hierarchy_schema,
            "hierarchy_file": _string(
                "Path (workspace-relative preferred) to a UTF-8 JSON file "
                "containing the hierarchy object. Mutually exclusive with "
                "hierarchy; the controller reads and validates it. Use when "
                "the hierarchy is too large to emit inline."
            ),
            "reason": _string(
                "Why the active, not-yet-accepted Delivery scope changed."
            ),
            "continuity_basis": {
                "type": "string",
                "enum": [
                    "USER_EXPLICIT_SAME_DELIVERY",
                    "ACTIVE_LOOP_REPLAN",
                ],
                "description": (
                    "Explicit evidence that this is the same logical "
                    "Delivery. Workspace/path reuse is never continuity."
                ),
            },
            "requested_by": _string("Human requester identity."),
        },
        required=[
            "root_id",
            "expected_current_revision",
            "reason",
            "continuity_basis",
            "requested_by",
        ],
    )
    tool_schema["$defs"] = definitions
    return tool_schema


TOOLS = (
    _tool(
        "workspace_status",
        (
            "Inspect Deliveries bound to this actual workspace, or select "
            "one by root ID. More than one unfinished bound Delivery returns "
            "DELIVERY_SELECTION_REQUIRED and candidateDeliveries; callers "
            "must retry with their retained root ID. Unbound CHOICE_READY or "
            "HANDOFF_READY drafts are discoverable only by explicit root ID. "
            "Every existing checkout is treated as the current workspace; "
            "an existing linked checkout receives no special scheduling "
            "behavior. CURRENT_WORKSPACE_SERIAL permits one Delivery turn at "
            "a time. A later Delivery is returned as QUEUED only after its "
            "AUTOMATIC selection is recorded, and carries an automatic resume "
            "continuation until the previous turn has a "
            "verifiable commit, the work tree and index are clean, HEAD still "
            "matches its frozen binding, and all receivers are safely "
            "released. Resource conflicts, dirty state, HEAD drift, or "
            "uncertain release return a stop or wait state instead of "
            "switching. With explicit root ID, CHOICE_READY restores "
            "pendingInteraction before frozen-binding runtime verification."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "base_ref": BASE_REF,
                "confirmed_dirty_state_fingerprint": (
                    DIRTY_STATE_FINGERPRINT
                ),
            }
        ),
    ),
    _tool(
        "recommend_assurance_profile",
        (
            "Deterministically recommend LIGHT or STANDARD from explicit task "
            "classification facts. This read-only advisor does not parse the "
            "task summary heuristically and does not create or mutate a Graph."
        ),
        _object(
            {
                "task_summary": _string(
                    "Short human-readable task summary retained for audit context."
                ),
                "root_task_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Expected number of root implementation TASKs.",
                },
                "project_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Number of project workspaces affected.",
                },
                "change_scope": {
                    "type": "string",
                    "enum": ["LOCAL", "MULTI_MODULE", "MULTI_PROJECT"],
                    "description": "Smallest truthful structural impact scope.",
                },
                "risk_factors": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "DATABASE_SCHEMA",
                            "PUBLIC_CONTRACT",
                            "AUTHORIZATION",
                            "SENSITIVE_DATA",
                            "DEPLOYMENT_INFRASTRUCTURE",
                            "DATA_MIGRATION",
                            "UNKNOWN_IMPACT",
                        ],
                    },
                    "uniqueItems": True,
                    "maxItems": 7,
                    "description": (
                        "High-impact facts present in the task; use an empty "
                        "array only when none apply."
                    ),
                },
                "verification_plan": {
                    "type": "string",
                    "enum": ["TARGETED", "BROAD", "UNKNOWN"],
                    "description": "How specifically the result can be verified.",
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"],
                    "description": "Truthful overall risk classification.",
                },
            },
            required=[
                "task_summary",
                "root_task_count",
                "project_count",
                "change_scope",
                "risk_factors",
                "verification_plan",
                "risk_level",
            ],
        ),
        annotations={"idempotentHint": True},
    ),
    _tool(
        "hierarchy_contract",
        "Return the exact schema-v3 outer Graph contract and one example.",
        _object(
            {
                "root_kind": {
                    "type": "string",
                    "enum": ["GROUP", "TASK"],
                }
            },
            required=["root_kind"],
        ),
    ),
    _tool(
        "preview_hierarchy",
        (
            "Validate and fingerprint a proposed hierarchy, register its "
            "CHOICE_READY snapshot, and generate scheduler.db, root overview, "
            "baseline, progress, acceptance, revisions, and work-item "
            "artifacts before returning one controller-owned "
            "pendingInteraction with a host-native-selector-first policy. A "
            "Git Delivery without a frozen binding first returns "
            "DEVELOPMENT_BASELINE, including for a dirty workspace; a confirmed "
            "non-Git workspace proceeds to EXECUTION_MODE. Git discovery "
            "errors fail closed. "
            "The mapped native question tool is mandatory whenever callable; "
            "exact Markdown is only a capability fallback. It does not bind "
            "a workspace, freeze a Graph, create a run, or change the current "
            "checkout."
        ),
        _prepare_hierarchy_tool_schema(),
    ),
    _tool(
        "confirm_development_baseline",
        (
            "Apply one DEVELOPMENT_BASELINE pendingInteraction option before "
            "the execution-mode choice, or after a manual-start Git drift "
            "block: record the per-Delivery preference, "
            "compute the Git binding read-only, re-stage the hierarchy with "
            "the binding frozen in, and return the updated "
            "hierarchyFingerprint plus the next pendingInteraction. During "
            "manual reconfirmation, a changed binding creates the next "
            "immutable manual revision; an unchanged binding restores the "
            "existing revision. The Controller computes the binding read-only "
            "and never changes the checkout; NEW_FROM_MAINLINE pins baseCommit "
            "to the mainline HEAD, while NEW_FROM_CURRENT_BRANCH pins a "
            "stacked child to the clean current feature HEAD and makes that "
            "parent feature the integration target. The host may create or "
            "switch to the required branch only at a clean, safely released "
            "CURRENT_WORKSPACE_SERIAL boundary. The choice is remembered and "
            "not re-asked on subsequent revisions."
        ),
        _development_baseline_tool_schema(),
    ),
    _tool(
        "select_execution_mode",
        (
            "Apply one exact option returned by an EXECUTION_MODE "
            "pendingInteraction for the retained root ID. AUTOMATIC records "
            "the human choice immediately and fixes execution to "
            "CURRENT_WORKSPACE_SERIAL: the actual workspace runs one Delivery "
            "turn at a time. A later AUTOMATIC Delivery waits for a verifiable "
            "commit, a clean work tree and index, unchanged frozen HEAD "
            "binding, and safe release of every receiver before any branch "
            "transition. A frozen MANUAL handoff remains HANDOFF_READY and is "
            "not part of this automatic queue. "
            "At the queue head, the recorded AUTOMATIC choice authorizes the "
            "host to verify the exact dirty fingerprint, stash business "
            "changes while excluding .layered-delivery/**, create or switch "
            "the frozen Delivery branch, and resume without reconfirmation. "
            "Unmerged changes, resource conflicts, HEAD drift, or uncertain "
            "release stop the transition. Existing linked checkouts are "
            "ordinary current workspaces. If branch preparation is required, "
            "the persisted selection returns automaticHostPreparation; after "
            "performing its stash/create-or-switch actions, call "
            "resume_execution_mode and never retry the "
            "selection. No additional checkout or separate workspace task is "
            "scheduled. MANUAL creates the handoff "
            "and returns the exact "
            "receiver prompt embedded in that file. Direct dialog text is "
            "not a tool option and continues requirement discussion."
        ),
        _execution_choice_tool_schema(),
    ),
    _tool(
        "resume_execution_mode",
        (
            "Continue a previously recorded AUTOMATIC selection after the "
            "queued Delivery becomes the workspace turn owner and the trusted "
            "host completes automaticHostPreparation, reaching the required "
            "feature branch at a clean, "
            "safely released CURRENT_WORKSPACE_SERIAL boundary. Revalidate "
            "the exact fingerprints and Git/project bindings, then prepare, "
            "freeze, and dispatch only that Delivery without asking the user "
            "to select or confirm again. It never creates another checkout "
            "and never changes MANUAL into AUTOMATIC."
        ),
        _execution_resume_tool_schema(),
    ),
    _tool(
        "create_manual_handoff",
        (
            "Create a later explicit manual revision, or serve the "
            "controller-owned selection operation internally. For the "
            "initial execution choice, hosts must call "
            "select_execution_mode(MANUAL), not this low-level tool. Freeze "
            "the confirmed requirement snapshot as a portable bundle under "
            ".layered-delivery/<delivery-id>/. The bundle contains one "
            "self-contained .layered-delivery/<delivery-id>/"
            "handoff-<fingerprint>.md plus the same overview, baseline, "
            "progress, acceptance, revisions, and work-items projections "
            "used by automatic development. It also registers the frozen "
            "HANDOFF_READY snapshot in the shared scheduler.db and refreshes "
            "the root overview.md. Never create a shared handoffs directory. "
            "If the user explicitly changes an existing HANDOFF_READY "
            "requirement, retain its delivery.id and provide the current "
            "revision, USER_EXPLICIT_SAME_DELIVERY continuity, and a reason; "
            "the controller creates the next immutable manual revision in "
            "the same directory. A requirementKey already mapped to another "
            "delivery.id is rejected. "
            "This does not prepare, freeze, or start a Graph run; do not "
            "choose an Agent/model, create a receiving task, bind a workspace, "
            "or initialize another checkout. The user may open the bundle in "
            "any "
            "CLI, but that receiver must call start_manual_handoff before "
            "code work and then complete the full governed Graph."
        ),
        _manual_handoff_tool_schema(),
    ),
    _tool(
        "start_manual_handoff",
        (
            "Start the exact HANDOFF_READY snapshot in the receiving CLI's "
            "actual development workspace before any implementation work. "
            "If the frozen Git binding drifted, return a DEVELOPMENT_BASELINE "
            "pendingInteraction without binding the workspace or creating a "
            "run; the receiver confirms it and retries with the returned "
            "fingerprints. Otherwise this binds the workspace and creates one "
            "governed manual Graph run. TASK implementation Loops must be "
            "claimed with MANUAL provenance; TASK Reviews, configured GROUP "
            "seam Reviews, and Delivery Acceptance/Readiness remain independent "
            "host-native automatic Loops, followed by final user confirmation. "
            "It never weakens or skips configured STANDARD Review nodes."
        ),
        _manual_start_tool_schema(),
    ),
    _tool(
        "prepare_hierarchy",
        (
            "Validate and prepare an outer scheduling graph for an explicit "
            "revision or controller-owned selection. For the initial "
            "execution choice, hosts call select_execution_mode(AUTOMATIC) "
            "instead of this low-level tool. Shared Skill "
            "hints remain advisory, Loop payloads stay opaque to scheduling; "
            "the reserved databaseChanges contract is validated and projected "
            "before dispatch. A Git Delivery feature-branch binding is "
            "verified read-only. One physical workspace may bind multiple "
            "Delivery control states, routed by root ID, while actual "
            "execution remains CURRENT_WORKSPACE_SERIAL."
        ),
        _prepare_hierarchy_tool_schema(),
    ),
    _tool(
        "prepare_delivery_revision",
        (
            "Prepare the next immutable revision of the same active "
            "Delivery after its frozen scope changes. The Delivery ID stays "
            "stable, completed unchanged TASKs are candidates for "
            "carry-forward, and every project scope is reauthorized at "
            "freeze."
        ),
        _prepare_revision_tool_schema(),
    ),
    _tool(
        "delivery_revision_history",
        (
            "Read every immutable revision and run status for one logical "
            "Delivery."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "plan_dispatch_batch",
        (
            "Plan one concurrent batch for the current DISPATCH_LOOP "
            "frontier when an independent receiver is required, including "
            "every Review Loop. It reserves each assignment before the "
            "trusted current host creates an independent outer receiver. The "
            "receiver inherits the current host model; Delivery Graph "
            "does not inspect model inventory, recommend a model, or "
            "control Loop-internal workers. Returns receiver identities "
            "and decision fingerprints; never starts Agents or claims Loops. "
            "After consuming every assignment, obey postActionWait: wait for "
            "a receiver event or the earliest reservation deadline, then call "
            "graph_frontier once; never busy-poll."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_graph_fingerprint": FINGERPRINT,
            },
            required=[
                "root_id",
                "expected_graph_fingerprint",
            ],
        ),
    ),
    _tool(
        "freeze_hierarchy",
        (
            "Freeze an explicitly prepared later revision. The initial "
            "automatic button calls select_execution_mode(AUTOMATIC), not "
            "this low-level tool. Manual revisions use "
            "create_manual_handoff; their receiving CLI later creates the "
            "governed manual run through start_manual_handoff."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_delivery_revision": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Exact Delivery revision returned by prepare."
                    ),
                },
                "expected_hierarchy_fingerprint": _string(
                    "Fingerprint returned by prepare_hierarchy."
                ),
                "authorized_project_ids": {
                    "type": "array",
                    "items": ROOT_ID,
                    "uniqueItems": True,
                    "description": (
                        "Exact project IDs explicitly authorized by the "
                        "user for this revision; use an empty array when "
                        "projectScopes is absent."
                    ),
                },
                "confirmed_by": _string("Human confirmer identity."),
            },
            required=[
                "root_id",
                "expected_delivery_revision",
                "expected_hierarchy_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        ),
    ),
    _tool(
        "graph_frontier",
        (
            "Advance scheduler bookkeeping and return the next Graph actions. "
            "Consume every returned immediate action, then follow "
            "progressMonitor.waitDirective. Never call it back-to-back; use "
            "graph_status for any permitted periodic observation."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "unfreeze_task_requirement",
        (
            "Unfreeze one not-yet-started TASK requirement so it can be "
            "revised without changing Delivery topology, dependencies, or "
            "resource locks."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "task_id": _string("Exact TASK work-item ID."),
                "expected_revision": {
                    "type": "integer",
                    "minimum": 1,
                },
                "authorized_by": _string("Human authorizer identity."),
                "reason": _string("Reason for revising the TASK requirement."),
            },
            required=[
                "root_id",
                "task_id",
                "expected_revision",
                "authorized_by",
                "reason",
            ],
        ),
        human=True,
    ),
    _tool(
        "refreeze_task_requirement",
        (
            "Replace and refreeze one previously unfrozen, unstarted TASK "
            "requirement. The replacement may change only title, summary, "
            "and opaque Loop payload."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "task_id": _string("Exact TASK work-item ID."),
                "expected_revision": {
                    "type": "integer",
                    "minimum": 1,
                },
                "requirement": _object(
                    {
                        "title": _string("Revised TASK title."),
                        "summary": _string("Revised TASK summary."),
                        "payload": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    required=["title", "summary", "payload"],
                ),
                "confirmed_by": _string("Human confirmer identity."),
            },
            required=[
                "root_id",
                "task_id",
                "expected_revision",
                "requirement",
                "confirmed_by",
            ],
        ),
        human=True,
    ),
    _tool(
        "graph_status",
        (
            "Read the current materialized Graph and Loop states. Use it only "
            "for read-only periodic observation at or after "
            "progressMonitor.waitDirective.pollNotBefore, never back-to-back. "
            "Call graph_frontier only for returned actions, receiver events, "
            "nextWakeAt, or ADVANCE_REQUIRED."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "open_delivery_dashboard",
        (
            "Open a read-only MCP Apps dashboard for the current Delivery. "
            "It returns a data-minimized snapshot of Graph nodes, active "
            "Loops, alerts, and Revision history without advancing the "
            "scheduler or changing control-plane state."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
        title="Open Delivery Dashboard",
        meta={
            "ui": {
                "resourceUri": DASHBOARD_RESOURCE_URI,
                "visibility": ["model", "app"],
            },
            "openai/outputTemplate": DASHBOARD_RESOURCE_URI,
            "openai/widgetAccessible": True,
            "openai/toolInvocation/invoking": "正在读取 Delivery 运行状态…",
            "openai/toolInvocation/invoked": "Delivery 运行看板已更新",
        },
    ),
    _tool(
        "graph_events",
        "Read the tamper-evident scheduler event stream.",
        _object(
            {
                "root_id": ROOT_ID,
                "after_event_id": {
                    "type": "integer",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            required=["root_id"],
        ),
    ),
    _tool(
        "advance_graph",
        "Advance lease expiry, infrastructure retry, joins, and readiness.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "rebuild_graph_run",
        "Rebuild mutable node projections from the verified event stream.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "loop_context",
        (
            "Read one opaque Loop descriptor, shared late-bound Skill hints, "
            "direct predecessors, transitive upstream results, TASK baseline "
            "path, runtime-verified project workspace roots, frozen project "
            "scope anchors, completion policy for internal adaptation and "
            "rework, and the execution policy separating pre-claim capacity, "
            "live-lease handoff, and expired-lease recovery. Loops use the "
            "verified roots as-is and never create, switch, or check out Git "
            "branches."
        ),
        _object(
            {"root_id": ROOT_ID, "node_id": NODE_ID},
            required=["root_id", "node_id"],
        ),
    ),
    _tool(
        "dispatch_loop",
        (
            "Claim one ready TASK, TASK Review, configured GROUP seam Review, "
            "or Delivery Acceptance/Readiness Loop for its orchestrated outer "
            "receiver. The claim binds "
            "the configured trusted Adapter and request workspace to a "
            "caller-declared receiving context; it does not authenticate a "
            "real host session or development model. The caller must guard "
            "the returned Loop operation ID as a bearer capability."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "owner": SCHEDULER_IDENTITY,
                "agent_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Caller-declared receiving Agent ID, such as codex or "
                        "claude-code. Used for execution attribution, not "
                        "authenticated identity or executor recommendation."
                    ),
                },
                "actual_model_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Optional model actually observed by the host after "
                        "native dispatch. It is display-only evidence: the "
                        "controller never routes, authorizes, fingerprints, "
                        "or evaluates capability from this value. Do not "
                        "guess it."
                    ),
                },
                "dispatch_mode": {
                    "type": "string",
                    "enum": ["AUTO", "MANUAL"],
                    "description": (
                        "AUTO is required for every automatically routed Loop. "
                        "MANUAL is allowed for TASK implementation Loops in "
                        "a Graph started by start_manual_handoff, or for one "
                        "READY automatic TASK explicitly recovered through "
                        "handoff_ready_automatic_task. Review Loops remain "
                        "AUTO and independent."
                    ),
                },
                "receiver_context_id": _string(
                    "Caller-declared host-native receiving Agent context ID. "
                    "Orchestration must provide the actual native context; "
                    "Review Loops must differ from every upstream receiving "
                    "context."
                ),
                "dispatch_transport": {
                    "type": "string",
                    "enum": ["HOST_NATIVE"],
                    "description": (
                        "Required with dispatch_mode=AUTO. It expresses the "
                        "orchestration requirement to use the current host's "
                        "native Agent API, never a CLI, subprocess, or "
                        "companion script; it is not process/session proof."
                    ),
                },
                "dispatch_reservation_id": _string(
                    "Required with dispatch_mode=AUTO. Use the exact "
                    "dispatchReservationId returned for this assignment."
                ),
                "dispatch_decision_fingerprint": {
                    **FINGERPRINT,
                    "description": (
                        "Exact automatic dispatch decision fingerprint. "
                        "Only valid with dispatch_mode=AUTO."
                    ),
                },
                "operation_id": OPERATION_ID,
            },
            required=[
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "dispatch_mode",
                "receiver_context_id",
                "operation_id",
            ],
        ),
    ),
    _tool(
        "handoff_ready_automatic_task",
        (
            "Recover one active AUTOMATIC TASK after native receiver startup "
            "has failed. This explicit mutation is allowed only while the "
            "TASK Loop is READY, its current attempt has never been claimed, "
            "the Delivery workspace and index are clean, no automatic "
            "reservation is "
            "live, and the user confirms no code changes were made. It "
            "switches only that TASK to MANUAL receipt without changing the "
            "Graph execution mode, baseline, fingerprints, or Revision. "
            "Automatic dispatch remains disabled for that TASK; every Review "
            "Loop stays host-native AUTOMATIC. Reuse the same "
            "handoff_request_id only to recover an unknown response."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "expected_graph_fingerprint": FINGERPRINT,
                "handoff_request_id": _string(
                    "Unique idempotency key for this explicit recovery."
                ),
                "confirmed_no_code_changes": {
                    "type": "boolean",
                    "const": True,
                    "description": (
                        "Explicit confirmation that no implementation change "
                        "was made for the unclaimed TASK attempt."
                    ),
                },
                "confirmed_by": SCHEDULER_IDENTITY,
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1024,
                    "description": (
                        "Why native automatic receiver startup cannot safely "
                        "continue, such as repeated startup failure."
                    ),
                },
            },
            required=[
                "root_id",
                "node_id",
                "expected_graph_fingerprint",
                "handoff_request_id",
                "confirmed_no_code_changes",
                "confirmed_by",
                "reason",
            ],
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        human=True,
    ),
    _tool(
        "heartbeat_loop",
        "Renew the lease of one claimed Loop.",
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
            },
            required=["root_id", "node_id", "operation_id"],
        ),
    ),
    _tool(
        "report_loop_progress",
        (
            "Report bounded, user-visible business progress for one claimed "
            "Loop without renewing its lease. Human-facing text follows the "
            "user's current language; raw terminal logs and hidden reasoning "
            "are not accepted."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "phase": {
                    "type": "string",
                    "enum": [
                        "STARTING",
                        "INSPECTING",
                        "TESTING",
                        "INVESTIGATING",
                        "FIXING",
                        "REVIEWING",
                        "VERIFYING",
                        "WAITING",
                    ],
                    "description": (
                        "Current user-visible Loop phase; the controller "
                        "renders it as a Chinese label."
                    ),
                },
                "summary_zh": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": (
                        "Concise current progress written for the main Agent "
                        "window in the user's current language."
                    ),
                },
                "completed_zh": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                    },
                    "maxItems": 8,
                    "description": (
                        "Completed milestones in the user's current language."
                    ),
                },
                "next_step_zh": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "description": (
                        "Next action in the user's current language."
                    ),
                },
                "progress_percent": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
                "tests": _object(
                    {
                        "passed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                        "failed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                        "skipped": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                        "total": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                    },
                    required=["passed", "failed", "skipped", "total"],
                ),
            },
            required=["root_id", "node_id", "operation_id", "phase", "summary_zh"],
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    ),
    _tool(
        "pause_loop",
        (
            "Pause one claimed Loop with a live lease while preserving its "
            "current attempt and frozen Graph. Provide resume_at for a "
            "known provider soft-stop window and identify whether the "
            "limited capacity belongs to the executor or the native host. "
            "Use capacity_scope=HOST when the native host observes a hard "
            "429 and provide its structured reset time."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "resume_at": _string(
                    "Optional known provider quota reset time as an ISO "
                    "8601 timestamp. Before it, the same Agent waits for a "
                    "host-native scheduled prompt or manual resume. The "
                    "first frontier call at or after it makes the same Loop "
                    "attempt ready for redispatch."
                ),
                "capacity_scope": {
                    "type": "string",
                    "enum": ["EXECUTOR", "HOST"],
                    "description": (
                        "Required with resume_at. EXECUTOR waits for the "
                        "same Loop Agent; HOST means the native orchestrator "
                        "itself is quota-limited. Both wait for a host-native "
                        "scheduled prompt or manual Agent resume."
                    ),
                },
            },
            required=["root_id", "node_id", "operation_id"],
        ),
    ),
    _tool(
        "resume_loop",
        (
            "Resume one paused Loop in a receiving independent context and "
            "return it to Graph readiness."
        ),
        _object(
            {"root_id": ROOT_ID, "node_id": NODE_ID},
            required=["root_id", "node_id"],
        ),
    ),
    _tool(
        "record_loop_result",
        (
            "Record a genuine terminal outcome returned by a claimed Loop. "
            "Do not call for a correctable finding or internal Gate failure; "
            "adapt the Loop plan, resolve it, and reevaluate first."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "outcome": OUTCOME,
                "failure_class": {
                    "type": "string",
                    "enum": [
                        "RETRYABLE_INFRA",
                        "WORKER_LOST",
                        "LOOP_BLOCKED",
                        "REPLAN_REQUIRED",
                        "EXTERNAL_AUTHORITY",
                        "NON_RETRYABLE",
                    ],
                    "description": (
                        "Required when outcome.status is BLOCKED. Select it "
                        "only after the current Loop has no in-scope path to "
                        "progress with its existing authority."
                    ),
                },
            },
            required=[
                "root_id",
                "node_id",
                "operation_id",
                "outcome",
            ],
        ),
    ),
    _tool(
        "record_user_confirmation",
        "Complete the graph after its Review Loop succeeds and the user accepts.",
        _object(
            {
                "root_id": ROOT_ID,
                "confirmed": {
                    "type": "boolean",
                    "const": True,
                    "description": (
                        "JSON Boolean true after explicit user acceptance."
                    ),
                },
                "confirmed_by": _string("Human confirmer identity."),
                "summary": _string("Human completion summary."),
            },
            required=[
                "root_id",
                "confirmed",
                "confirmed_by",
                "summary",
            ],
        ),
    ),
    _tool(
        "cancel_graph_run",
        "Cancel all unfinished nodes in a non-terminal scheduler run.",
        _object(
            {
                "root_id": ROOT_ID,
                "cancelled_by": _string("Human canceller identity."),
                "reason": _string("Cancellation reason."),
            },
            required=["root_id", "cancelled_by", "reason"],
        ),
        human=True,
    ),
    _tool(
        "archive_delivery",
        (
            "Archive a completed Delivery from default workspace discovery "
            "while retaining its SQLite history and detail projections."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
        human=True,
        annotations={"idempotentHint": True},
    ),
)


def tool_definitions() -> list[dict[str, Any]]:
    return deepcopy(list(TOOLS))


def _validate_schema(
    value: object,
    schema: dict[str, Any],
    field: str,
) -> None:
    expected_type = schema.get("type")
    if expected_type == "boolean" and not isinstance(value, bool):
        fail(
            "MCP_TOOL_ARGUMENT_INVALID",
            f"{field} must be a JSON boolean",
        )
    if "const" in schema:
        if value != schema["const"]:
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must equal {schema['const']!r}",
            )
        return
    if expected_type == "object":
        if not isinstance(value, dict):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be an object",
            )
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = required - set(value)
        if missing:
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} is missing required fields",
                missingFields=sorted(missing),
            )
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                fail(
                    "MCP_TOOL_ARGUMENT_INVALID",
                    f"{field} contains unknown fields",
                    unknownFields=sorted(unknown),
                )
        for key, child in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                _validate_schema(
                    child,
                    child_schema,
                    f"{field}.{key}",
                )
        return
    if expected_type == "string":
        if (
            not isinstance(value, str)
            or len(value) < schema.get("minLength", 0)
            or len(value) > schema.get("maxLength", len(value))
            or (
                "enum" in schema
                and value not in schema["enum"]
            )
        ):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be a supported string",
            )
        return
    if expected_type == "array":
        if (
            not isinstance(value, list)
            or len(value) < schema.get("minItems", 0)
            or len(value) > schema.get("maxItems", len(value))
        ):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be a supported array",
            )
        if schema.get("uniqueItems") and len(
            {
                canonical_json(item)
                for item in value
            }
        ) != len(value):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must contain unique items",
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(
                    item,
                    item_schema,
                    f"{field}[{index}]",
                )
        return
    if expected_type == "integer":
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < schema.get("minimum", value)
            or value > schema.get("maximum", value)
        ):
            fail(
                "MCP_TOOL_ARGUMENT_INVALID",
                f"{field} must be a supported integer",
            )


_HIERARCHY_FILE_TOOLS = frozenset(
    {
        "preview_hierarchy",
        "prepare_hierarchy",
        "create_manual_handoff",
        "prepare_delivery_revision",
    }
)


def validate_tool_arguments(
    name: str,
    arguments: object,
) -> dict[str, Any]:
    tool = next(
        (entry for entry in TOOLS if entry["name"] == name),
        None,
    )
    if tool is None or name not in CONTROLLER_OPERATIONS:
        fail("MCP_TOOL_UNKNOWN", f"Unknown scheduler tool: {name}")
    _validate_schema(arguments, tool["inputSchema"], "arguments")
    validated = dict(arguments)
    if name in _HIERARCHY_FILE_TOOLS:
        has_inline = "hierarchy" in validated
        has_file = "hierarchy_file" in validated
        if has_inline and has_file:
            fail(
                "SCHEDULER_HIERARCHY_INPUT_CONFLICT",
                "Provide exactly one of hierarchy or hierarchy_file, not both",
            )
        if not has_inline and not has_file:
            fail(
                "SCHEDULER_HIERARCHY_INPUT_REQUIRED",
                "Provide the hierarchy object or a hierarchy_file path",
            )
        if has_inline:
            try:
                validate_hierarchy_definition(validated["hierarchy"])
            except GatedLoopError as error:
                fail(
                    "MCP_TOOL_ARGUMENT_INVALID",
                    "arguments.hierarchy does not match schema v3",
                    schemaError={
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    },
                )
    return validated


def _apply_hierarchy_file(
    arguments: dict[str, Any],
    workspace_root: str,
) -> None:
    """Load the hierarchy from a workspace file when hierarchy_file is set."""
    if "hierarchy_file" not in arguments:
        return
    raw = read_regular_file(workspace_root, arguments["hierarchy_file"])
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        fail(
            "SCHEDULER_HIERARCHY_FILE_INVALID",
            f"hierarchy_file is not valid JSON: {error}",
        )
    if not isinstance(loaded, dict):
        fail(
            "SCHEDULER_HIERARCHY_FILE_INVALID",
            "hierarchy_file must contain a JSON object",
        )
    arguments["hierarchy"] = loaded
    del arguments["hierarchy_file"]


def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    root: str,
    workspace_root: str | None = None,
    explicit_dogfood: bool = False,
    controller: LayeredDeliveryController = DEFAULT_CONTROLLER,
    client_info: dict[str, Any] | None = None,
    trusted_host_adapter: str | None = None,
) -> dict[str, Any]:
    internal_arguments = validate_tool_arguments(name, arguments)
    _apply_hierarchy_file(internal_arguments, workspace_root or root)
    if name in {"create_manual_handoff", "freeze_hierarchy"}:
        internal_arguments["confirmed"] = True
    result = controller.execute(
        name,
        internal_arguments,
        context=ControllerContext(
            project_root=root,
            workspace_root=workspace_root or root,
            explicit_dogfood=explicit_dogfood,
            host_native_agent_ids=_host_native_agent_ids(
                trusted_host_adapter
            ),
            host_adapter_id=trusted_host_adapter,
        ),
    )
    return result


def _host_native_agent_ids(
    trusted_host_adapter: str | None,
) -> tuple[str, ...]:
    if trusted_host_adapter == "claude-code":
        return ("claude-code",)
    if trusted_host_adapter == "codex":
        return ("codex",)
    if trusted_host_adapter == "zcode":
        return ("zcode",)
    return ()


__all__ = (
    "call_tool",
    "tool_definitions",
    "validate_tool_arguments",
)
