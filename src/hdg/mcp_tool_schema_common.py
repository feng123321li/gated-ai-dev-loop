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
