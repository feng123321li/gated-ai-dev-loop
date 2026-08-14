from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .errors import fail
from .jsonio import canonical_json


LOOP_TERMINAL_STATUSES = (
    "SUCCEEDED",
    "BLOCKED",
    "REPLAN_REQUIRED",
    "CANCELLED",
)
LOOP_ASSURANCE_PROFILES = ("LIGHT", "STANDARD")
LOOP_KINDS = (
    "TASK_LOOP",
    "TASK_REVIEW_LOOP",
    "GROUP_REVIEW_LOOP",
    "DELIVERY_REVIEW_LOOP",
)
REVIEW_LOOP_KINDS = frozenset(LOOP_KINDS[1:])

LOOP_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/@-]{0,191}$")
RESOURCE_CLAIM = re.compile(r"^[a-z0-9][a-z0-9._:/@-]{0,255}$")
_LOOP_EXECUTION_POLICY = {
    "contextIsolation": "REQUIRED",
    "dispatch": {
        "preferredExecutor": "HOST_NATIVE_AGENT",
        "noAgentCapacityBeforeClaim": (
            "MANUAL_HANDOFF_WITHOUT_CLAIM"
        ),
    },
    "claimedLoopHandoff": {
        "trigger": "CONTEXT_PRESSURE",
        "requiresLiveLease": True,
        "action": "PAUSE_AND_HANDOFF",
        "loopOutcome": "NONE",
    },
    "unclaimedAutomaticRecovery": {
        "tool": "handoff_ready_automatic_task",
        "requiresReadyTask": True,
        "requiresNeverClaimedAttempt": True,
        "requiresNoLiveReservation": True,
        "requiresCleanWorkspace": True,
        "requiresNoCodeChangesConfirmation": True,
        "taskDispatchMode": "MANUAL",
        "graphExecutionModeRemains": "active",
        "reviewsRemain": "AUTO",
    },
    "progressReporting": {
        "tool": "report_loop_progress",
        "language": "USER_PREFERRED",
        "heartbeatRenewsLease": True,
        "heartbeatRenewalMode": "AT_RENEWAL_THRESHOLD",
        "heartbeatUpdatesLiveMonitor": True,
        "heartbeatWritesProjection": False,
        "progressRenewsLease": False,
        "initialHeartbeatRequiredBeforeWork": True,
        "shortLoopMayCompleteWithoutExplicitHeartbeat": False,
        "reportAt": [
            "LOOP_START",
            "CODE_INSPECTION_COMPLETE",
            "TEST_RUN_IF_EXECUTED",
            "ISSUE_FOUND",
            "FIX_APPLIED",
            "REREVIEW",
            "FINAL_VERIFICATION",
        ],
        "rawLogsAllowed": False,
        "hiddenReasoningAllowed": False,
    },
    "longRunningCommands": {
        "execution": "NON_BLOCKING_OR_SEPARATE_MONITOR",
        "heartbeatWhileRunning": True,
        "heartbeatIntervalSeconds": 60,
        "beforeStart": "REPORT_PROGRESS_AND_HEARTBEAT",
        "afterFinish": "HEARTBEAT_AND_REPORT_PROGRESS",
        "hostCompletionNotificationIsNotHeartbeat": True,
    },
    "providerRateLimit": {
        "softStopTrigger": (
            "KNOWN_REMAINING_CAPACITY_AT_OR_BELOW_5_PERCENT"
        ),
        "requiresLiveLease": True,
        "requiresKnownResetAt": True,
        "withResetAt": "PAUSE_UNTIL_RESET",
        "executorScopeBeforeReset": "WAIT_FOR_EXECUTOR_NATIVE_WAKE",
        "hostScopeBeforeReset": "WAIT_FOR_HOST_NATIVE_WAKE",
        "nativeWake": {
            "claudeCode": "SESSION_ONE_SHOT_CRON",
            "codexDesktop": "THREAD_SCHEDULED_TASK",
        },
        "atReset": "AGENT_RELOADS_FRONTIER_AND_REDISPATCHES",
        "sameAttempt": True,
        "loopOutcome": "NONE",
        "hard429": {
            "action": "TRIP_HOST_CAPACITY_BREAKER",
            "hostCallback": "MODEL_EXTERNAL_HOST_ADAPTER",
            "cancelRecurringMonitors": True,
            "scheduleWake": "HOST_NATIVE_ONE_SHOT_AT_RESET",
        },
    },
    "expiredLeaseRecovery": {
        "action": "ADVANCE_GRAPH",
        "pauseAllowed": False,
        "reuseOperationId": False,
    },
    "receivingContext": {
        "reuseFrozenGraph": True,
        "reloadViaMcp": True,
    },
}
_LOOP_COMPLETION_POLICY = {
    "payloadRole": (
        "REQUIREMENT_DIRECTION_CONSTRAINTS_CONTRACTS_AND_KNOWN_ACCEPTANCE_INPUT"
    ),
    "planningInputDepth": "DIRECTIONALLY_SUFFICIENT_NOT_EXHAUSTIVE",
    "graphRoleForPayload": (
        "STRUCTURE_PRESERVE_FINGERPRINT_ROUTE_SCHEDULE_AND_AGGREGATE_OPAQUELY"
    ),
    "inScopeNecessaryConditions": "DERIVE_AND_VALIDATE_AT_RUNTIME",
    "implementationPlanWithinLoop": "MAY_ADAPT_WITHOUT_REPLAN",
    "implementationDetailsOwnedByLoop": [
        "FILE_LAYOUT_AND_FILE_NAMES",
        "IMPLEMENTATION_CLASS_AND_INTERNAL_METHOD_NAMES",
        "CODE_STRUCTURE_AND_ALGORITHMS",
        "DETAILED_IMPLEMENTATION_AND_TEST_PLAN",
    ],
    "exactImplementationIdentifierBinding": (
        "ONLY_WHEN_EXPLICIT_REQUIREMENT_OR_CONFIRMED_EXTERNAL_CONTRACT"
    ),
    "frozenDatabaseContract": "APPLY_AND_VERIFY_OR_REPLAN_REQUIRED",
    "actionableFinding": "RESOLVE_AND_REEVALUATE_IN_CURRENT_LOOP",
    "reviewCycle": "FIND_RESOLVE_VERIFY_AND_REREVIEW_UNTIL_TERMINAL",
    "reviewFindings": {
        "resultField": "reviewFindings",
        "severities": ["P0", "P1", "P2"],
        "p0p1": "RESOLVE_AND_REREVIEW_BEFORE_SUCCEEDED",
        "p2": "ALWAYS_LIST_IN_ACCEPTANCE_REPORT",
    },
    "verificationEvidence": {
        "resultField": "verificationEvidence",
        "source": "LOOP_REPORTED_OPAQUE_TO_SCHEDULER",
        "recommendedFields": [
            "evidenceId",
            "kind",
            "check",
            "command",
            "scope",
            "status",
            "tests",
            "completedAt",
            "testedWorkspaceSnapshots",
        ],
        "stateBindingRequiredForReuse": True,
        "missingStateBinding": "NOT_REUSABLE",
        "controllerBindingField": "evidenceWorkspaceSnapshots",
        "controllerRelevantScopeBindingField": "evidenceScopeSnapshots",
        "unrelatedWorkspaceChanges": "DO_NOT_INVALIDATE_BOUND_SCOPE_PATHS",
        "freshnessValues": ["EXACT_MATCH", "CHANGED", "UNBOUND"],
        "reviewDecisionResultField": "validationDecision",
    },
    "workspaceChanges": {
        "resultField": "workspaceChanges",
        "source": "CONTROLLER_CAPTURED_AT_RESULT",
        "comparison": "FROZEN_BASE_COMMIT_TO_CURRENT_WORKSPACE",
        "semantics": "WORKSPACE_SNAPSHOT_NOT_EXCLUSIVE_OWNERSHIP",
    },
    "blockedOutcome": (
        "ONLY_IF_NO_IN_SCOPE_PATH_CAN_PROGRESS_WITH_CURRENT_AUTHORITY"
    ),
    "replanRequiredOutcome": (
        "ONLY_IF_FROZEN_DEPENDENCIES_RESOURCES_OR_TOPOLOGY_MUST_CHANGE"
    ),
}


def _non_empty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail("LOOP_CONTRACT_INVALID", f"{field} must be a non-empty string")
    return value.strip()


def _json_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("LOOP_CONTRACT_INVALID", f"{field} must be a JSON object")
    try:
        canonical_json(value)
    except (TypeError, ValueError):
        fail("LOOP_CONTRACT_INVALID", f"{field} must contain canonical JSON values")
    return deepcopy(value)


def validate_loop_descriptor(
    value: object,
    *,
    field: str = "loop",
) -> dict[str, Any]:
    """Validate one scheduler-visible Loop descriptor.

    The payload is intentionally opaque. The scheduler validates only the
    stable Loop reference and exclusive resource-claim keys that affect
    dispatch safety.
    """

    if not isinstance(value, dict) or set(value) != {
        "ref",
        "payload",
        "resourceClaims",
    }:
        fail(
            "LOOP_DESCRIPTOR_INVALID",
            "Loop descriptor fields must be ref, payload, and resourceClaims",
        )
    reference = _non_empty_text(value.get("ref"), f"{field}.ref")
    if not LOOP_REFERENCE.fullmatch(reference):
        fail(
            "LOOP_DESCRIPTOR_INVALID",
            "loop.ref contains unsupported characters",
        )
    claims_value = value.get("resourceClaims")
    if not isinstance(claims_value, list):
        fail(
            "LOOP_DESCRIPTOR_INVALID",
            "loop.resourceClaims must be an array",
        )
    claims: list[str] = []
    for index, raw in enumerate(claims_value):
        claim = _non_empty_text(
            raw,
            f"{field}.resourceClaims[{index}]",
        )
        if (
            not RESOURCE_CLAIM.fullmatch(claim)
            or ".." in claim.split("/")
        ):
            fail(
                "LOOP_DESCRIPTOR_INVALID",
                "Loop resource claim contains unsupported characters",
                claim=raw,
            )
        claims.append(claim)
    if len(set(claims)) != len(claims):
        fail(
            "LOOP_DESCRIPTOR_INVALID",
            "Loop resource claims must be unique",
        )
    return {
        "ref": reference,
        "payload": _json_object(
            value.get("payload"),
            f"{field}.payload",
        ),
        "resourceClaims": sorted(claims),
    }


def validate_loop_outcome(value: object) -> dict[str, Any]:
    """Validate the only implementation result understood by the scheduler."""

    if not isinstance(value, dict) or set(value) != {
        "status",
        "summary",
        "result",
    }:
        fail(
            "LOOP_OUTCOME_INVALID",
            "Loop outcome fields must be status, summary, and result",
        )
    status = value.get("status")
    if status not in LOOP_TERMINAL_STATUSES:
        fail(
            "LOOP_OUTCOME_INVALID",
            "Loop outcome status is invalid",
            allowed=list(LOOP_TERMINAL_STATUSES),
        )
    return {
        "status": status,
        "summary": _non_empty_text(value.get("summary"), "loopOutcome.summary"),
        "result": _json_object(value.get("result"), "loopOutcome.result"),
    }


def resource_claims_overlap(
    left: list[str],
    right: list[str],
) -> bool:
    """Return whether two opaque exclusive claim sets conflict."""

    return bool(set(left) & set(right))


def validate_loop_assurance_profile(profile: object) -> str:
    if profile not in LOOP_ASSURANCE_PROFILES:
        fail(
            "LOOP_POLICY_INVALID",
            "Loop assurance profile is invalid",
            allowed=list(LOOP_ASSURANCE_PROFILES),
        )
    return str(profile)


def loop_execution_policy(
    assurance_profile: str = "STANDARD",
) -> dict[str, Any]:
    """Return dispatch, observability, handoff, and recovery rules."""

    profile = validate_loop_assurance_profile(assurance_profile)
    policy = deepcopy(_LOOP_EXECUTION_POLICY)
    policy["assuranceProfile"] = profile
    policy["reviewTopology"] = (
        "TASK_REVIEWS_OPTIONAL_GROUP_SEAM_REVIEWS_AND_DELIVERY_ACCEPTANCE"
    )
    policy["responsibilityBoundaries"] = {
        "controller": {
            "owns": [
                "GRAPH_STATE_TRANSITIONS",
                "PREDECESSOR_SUCCESS_GATING",
                "RESULT_CONTRACT_VALIDATION",
                "EVENT_AND_PROJECTION_PERSISTENCE",
            ],
            "mustNotPerform": [
                "TECHNICAL_ACCEPTANCE",
                "EVIDENCE_SUFFICIENCY_JUDGMENT",
                "OPERATIONAL_READINESS_JUDGMENT",
            ],
        },
        "loopReceiver": {
            "owns": [
                "LOOP_EXECUTION",
                "LOOP_OWNED_JUDGMENT",
                "EVIDENCE_SELECTION_AND_VERIFICATION",
                "FINDING_CLOSURE",
            ],
            "mustNotPerform": [
                "GRAPH_READINESS_TRANSITION",
                "UPSTREAM_COMPLETION_GATING",
                "USER_CONFIRMATION",
            ],
        },
        "user": {
            "owns": ["FINAL_BUSINESS_CONFIRMATION"],
            "mustNotReplace": [
                "GRAPH_PRECONDITION_GATING",
                "DELIVERY_TECHNICAL_ACCEPTANCE",
            ],
        },
    }
    if profile == "LIGHT":
        policy["reviewTopology"] = "NO_INDEPENDENT_REVIEW_LOOPS"
        policy["progressReporting"]["reportAt"] = [
            "ISSUE_FOUND",
            "FINAL_VERIFICATION",
        ]
        policy["progressReporting"]["shortLoopMayReportOnlyFinal"] = True
        policy["progressReporting"][
            "initialHeartbeatRequiredBeforeWork"
        ] = False
        policy["progressReporting"][
            "shortLoopMayCompleteWithoutExplicitHeartbeat"
        ] = True
    return policy


def loop_completion_policy(
    assurance_profile: str = "STANDARD",
    *,
    loop_kind: str = "TASK_LOOP",
) -> dict[str, Any]:
    """Return the boundary between internal rework and terminal outcomes."""

    profile = validate_loop_assurance_profile(assurance_profile)
    if loop_kind not in LOOP_KINDS:
        fail(
            "LOOP_POLICY_INVALID",
            "Loop kind is invalid for completion policy",
            allowed=list(LOOP_KINDS),
        )
    policy = deepcopy(_LOOP_COMPLETION_POLICY)
    policy["assuranceProfile"] = profile
    policy["verificationScope"] = (
        "AFFECTED_SCOPE_SUFFICIENT_FOR_DECLARED_ACCEPTANCE"
    )
    policy["verificationStrategy"] = {
        "mode": "AFFECTED_SCOPE_FIRST",
        "scopeBasis": [
            "CHANGED_FILES",
            "DIRECT_DEPENDENCIES",
            "PUBLIC_CONTRACTS",
            "FAILURE_IMPACT",
        ],
        "default": "RUN_MINIMUM_SUFFICIENT_CHECKS",
        "fullRerunTriggers": [
            "AFFECTED_SCOPE_CANNOT_BE_BOUNDED",
            "CRITICAL_CROSS_BOUNDARY_RISK_LACKS_ISOLATED_CHECKS",
            "FROZEN_TASK_PAYLOAD_EXPLICITLY_REQUIRES_FULL_RERUN",
        ],
        "recordResultField": "verificationEvidence",
        "recordAffectedScopesField": "affectedScopes",
    }
    if loop_kind in REVIEW_LOOP_KINDS:
        policy["verificationScope"] = (
            "FULL_DECLARED_ACCEPTANCE_WITH_EVIDENCE_REUSE"
        )
        policy["verificationStrategy"] = {
            "mode": "EVIDENCE_FIRST_TARGETED_RERUN",
            "independence": (
                "INDEPENDENT_JUDGMENT_NOT_AUTOMATIC_FULL_RERUN"
            ),
            "reuseSources": [
                "validationEvidenceIndex",
                "upstreamLoopResults.outcome.result.verificationEvidence",
                "upstreamLoopResults.outcome.result.workspaceChanges",
            ],
            "reuseRequires": [
                "UPSTREAM_CHECK_PASSED",
                "CHECK_SCOPE_COVERS_CURRENT_RISK",
                "WORKSPACE_SNAPSHOT_UNCHANGED_FOR_RELEVANT_FILES",
                "COMMAND_AND_RESULT_ARE_AUDITABLE",
            ],
            "rerunDefault": (
                "TARGET_GAPS_FINDINGS_AND_HIGH_RISK_BOUNDARIES"
            ),
            "evidenceInvalidationTriggers": [
                "UPSTREAM_EVIDENCE_MISSING_OR_FAILED",
                "RELEVANT_WORKSPACE_CHANGED_AFTER_EVIDENCE",
                "REVIEW_FIX_APPLIED",
            ],
            "onEvidenceInvalidation": (
                "RERUN_AFFECTED_SCOPE_AND_DEPENDENTS"
            ),
            "fullRerunTriggers": [
                "AFFECTED_SCOPE_CANNOT_BE_BOUNDED",
                "CRITICAL_CROSS_BOUNDARY_RISK_LACKS_ISOLATED_CHECKS",
                "FROZEN_REVIEW_PAYLOAD_EXPLICITLY_REQUIRES_FULL_RERUN",
            ],
        }
        policy["verificationStrategy"]["layerDefault"] = {
            "TASK_REVIEW_LOOP": (
                "INSPECT_TASK_AND_TARGET_UNCOVERED_OR_RISKY_BEHAVIOR"
            ),
            "GROUP_REVIEW_LOOP": (
                "VERIFY_DIRECT_CHILD_SEAMS_AND_GROUP_INTEGRATION"
            ),
            "DELIVERY_REVIEW_LOOP": (
                "VERIFY_TOP_LEVEL_REQUIREMENT_COVERAGE_SYSTEM_EVIDENCE_"
                "OPERATIONAL_READINESS_AND_GLOBAL_RISK"
            ),
        }[loop_kind]
        policy["reviewResultPersistence"] = {
            "scope": "CURRENT_LAYER_ONLY",
            "contractValidator": "CONTROLLER",
            "controllerValidationScope": (
                "STRUCTURE_AND_DECLARED_TERMINAL_CONSISTENCY_ONLY"
            ),
            "acceptanceDecisionOwner": "INDEPENDENT_LOOP_RECEIVER",
            "requiredCommonFields": [
                "validationDecision",
                "reviewFindings",
            ],
            "upstreamLoopResults": "CONTEXT_ONLY_NEVER_PERSIST",
            "lowerLayerResultBodies": "NEVER_COPY",
        }
        policy["reviewBoundary"] = {
            "TASK_REVIEW_LOOP": {
                "layer": "TASK",
                "owns": [
                    "FROZEN_TASK_ACCEPTANCE",
                    "LOCAL_BEHAVIOR",
                    "PUBLIC_CONTRACT",
                    "TARGETED_REGRESSION",
                ],
                "mustNotRepeat": [
                    "SIBLING_TASK_INTERNALS",
                    "GROUP_INTEGRATION",
                    "DELIVERY_READINESS",
                ],
                "requiredResultField": "taskAcceptance",
            },
            "GROUP_REVIEW_LOOP": {
                "layer": "GROUP",
                "owns": [
                    "DIRECT_CHILD_SEAMS",
                    "INTERFACE_COMPATIBILITY",
                    "DATA_AND_CONTROL_FLOW",
                    "TRANSACTION_AND_ERROR_PROPAGATION",
                ],
                "mustNotRepeat": [
                    "TASK_INTERNAL_IMPLEMENTATION",
                    "CHILD_UNIT_TEST_SUITES",
                    "DELIVERY_READINESS",
                ],
                "requiredResultField": "groupIntegration",
            },
            "DELIVERY_REVIEW_LOOP": {
                "layer": "DELIVERY",
                "owns": [
                    "TOP_LEVEL_REQUIREMENT_COVERAGE",
                    "CROSS_GROUP_OR_SYSTEM_EVIDENCE",
                    "OPERATIONAL_READINESS",
                    "EVIDENCE_FRESHNESS_AND_GLOBAL_RISK",
                ],
                "mustNotRepeat": [
                    "LOWER_LAYER_CODE_REREVIEW",
                    "CHILD_UNIT_TEST_SUITES",
                    "CLOSED_LOWER_LAYER_FINDINGS",
                ],
                "requiredResultField": "deliveryReadiness",
            },
        }[loop_kind]
    if profile == "LIGHT":
        policy["verificationScope"] = "TARGETED_FOR_DECLARED_CHANGE"
        policy["reviewCycle"] = (
            "FOCUSED_REVIEW_RESOLVE_VERIFY_AND_REREVIEW_IF_NEEDED"
        )
        policy["expandedImpact"] = "REPLAN_REQUIRED_TO_STANDARD"
    return policy


__all__ = (
    "LOOP_ASSURANCE_PROFILES",
    "LOOP_KINDS",
    "LOOP_TERMINAL_STATUSES",
    "REVIEW_LOOP_KINDS",
    "loop_completion_policy",
    "loop_execution_policy",
    "resource_claims_overlap",
    "validate_loop_assurance_profile",
    "validate_loop_descriptor",
    "validate_loop_outcome",
)
