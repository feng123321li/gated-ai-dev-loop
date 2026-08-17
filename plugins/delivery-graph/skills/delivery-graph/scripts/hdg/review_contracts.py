from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from .errors import fail
from .jsonio import canonical_json


REVIEW_LAYER_RESULT_FIELDS = {
    "TASK_REVIEW_LOOP": "taskAcceptance",
    "GROUP_REVIEW_LOOP": "groupIntegration",
    "DELIVERY_REVIEW_LOOP": "deliveryReadiness",
}
REVIEW_RESULT_SUPPORT_FIELDS = frozenset(
    {
        "validationDecision",
        "reviewFindings",
        "affectedScopes",
        "verificationEvidence",
        "evidenceWorkspaceSnapshots",
        "evidenceScopeSnapshots",
        "workspaceChanges",
    }
)


def _contract_error(message: str, *, field: str) -> None:
    fail(
        "LOOP_REVIEW_RESULT_INVALID",
        message,
        field=field,
    )


def _json_object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _contract_error(f"{field} must be a JSON object", field=field)
    try:
        canonical_json(value)
    except (TypeError, ValueError):
        _contract_error(
            f"{field} must contain canonical JSON values",
            field=field,
        )
    return deepcopy(value)


def _object(
    value: object,
    *,
    field: str,
    required: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        _contract_error(
            f"{field} fields must be {', '.join(sorted(required))}",
            field=field,
        )
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _contract_error(f"{field} must be non-empty text", field=field)
    return value


def _texts(
    value: object,
    *,
    field: str,
    minimum: int = 0,
) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        _contract_error(
            f"{field} must contain at least {minimum} text item(s)",
            field=field,
        )
    for index, item in enumerate(value):
        _text(item, field=f"{field}[{index}]")
    return value


def _validate_validation_decision(value: object) -> None:
    field = "loopOutcome.result.validationDecision"
    decision = _object(
        value,
        field=field,
        required={
            "decision",
            "reusedEvidenceRefs",
            "executedEvidenceRefs",
            "riskTriggers",
            "rationale",
        },
    )
    if decision["decision"] not in {
        "REUSED",
        "TARGETED_RERUN",
        "FULL_RERUN",
    }:
        _contract_error(
            "validationDecision.decision is invalid",
            field=f"{field}.decision",
        )
    reused = decision["reusedEvidenceRefs"]
    if not isinstance(reused, list):
        _contract_error(
            "reusedEvidenceRefs must be an array",
            field=f"{field}.reusedEvidenceRefs",
        )
    for index, raw in enumerate(reused):
        reference_field = f"{field}.reusedEvidenceRefs[{index}]"
        reference = _object(
            raw,
            field=reference_field,
            required={"nodeId", "attempt", "evidenceId"},
        )
        _text(reference["nodeId"], field=f"{reference_field}.nodeId")
        if not isinstance(reference["attempt"], int) or reference["attempt"] < 1:
            _contract_error(
                "Evidence attempt must be a positive integer",
                field=f"{reference_field}.attempt",
            )
        _text(
            reference["evidenceId"],
            field=f"{reference_field}.evidenceId",
        )
    _texts(
        decision["executedEvidenceRefs"],
        field=f"{field}.executedEvidenceRefs",
    )
    _texts(
        decision["riskTriggers"],
        field=f"{field}.riskTriggers",
    )
    _text(decision["rationale"], field=f"{field}.rationale")


def _validate_review_findings(value: object) -> None:
    field = "loopOutcome.result.reviewFindings"
    if not isinstance(value, list):
        _contract_error("reviewFindings must be an array", field=field)
    for index, raw in enumerate(value):
        finding_field = f"{field}[{index}]"
        finding = _object(
            raw,
            field=finding_field,
            required={
                "severity",
                "summary",
                "status",
                "resolution",
                "evidence",
            },
        )
        if finding["severity"] not in {"P0", "P1", "P2"}:
            _contract_error(
                "Review finding severity is invalid",
                field=f"{finding_field}.severity",
            )
        if finding["status"] not in {"RESOLVED", "ACCEPTED", "OPEN"}:
            _contract_error(
                "Review finding status is invalid",
                field=f"{finding_field}.status",
            )
        for name in ("summary", "resolution", "evidence"):
            _text(finding[name], field=f"{finding_field}.{name}")
        if (
            finding["severity"] in {"P0", "P1"}
            and finding["status"] != "RESOLVED"
        ):
            _contract_error(
                "A successful Review requires RESOLVED P0/P1 findings",
                field=finding_field,
            )


def _validate_task_acceptance(value: object) -> None:
    field = "loopOutcome.result.taskAcceptance"
    acceptance = _object(
        value,
        field=field,
        required={
            "acceptanceChecks",
            "localBehavior",
            "publicContract",
            "targetedRegression",
            "decision",
            "rationale",
        },
    )
    checks = acceptance["acceptanceChecks"]
    if not isinstance(checks, list) or not checks:
        _contract_error(
            "taskAcceptance.acceptanceChecks must not be empty",
            field=f"{field}.acceptanceChecks",
        )
    for index, raw in enumerate(checks):
        check_field = f"{field}.acceptanceChecks[{index}]"
        check = _object(
            raw,
            field=check_field,
            required={"acceptancePoint", "status", "evidenceRefs"},
        )
        _text(check["acceptancePoint"], field=f"{check_field}.acceptancePoint")
        if check["status"] != "SATISFIED":
            _contract_error(
                "A successful TASK Review requires SATISFIED acceptance checks",
                field=f"{check_field}.status",
            )
        _texts(
            check["evidenceRefs"],
            field=f"{check_field}.evidenceRefs",
            minimum=1,
        )
    for name in ("localBehavior", "targetedRegression"):
        if acceptance[name] != "VERIFIED":
            _contract_error(
                f"taskAcceptance.{name} must be VERIFIED",
                field=f"{field}.{name}",
            )
    if acceptance["publicContract"] not in {"VERIFIED", "NOT_APPLICABLE"}:
        _contract_error(
            "taskAcceptance.publicContract is invalid",
            field=f"{field}.publicContract",
        )
    if acceptance["decision"] != "ACCEPTED":
        _contract_error(
            "taskAcceptance.decision must be ACCEPTED",
            field=f"{field}.decision",
        )
    _text(acceptance["rationale"], field=f"{field}.rationale")


def _validate_group_integration(value: object) -> None:
    field = "loopOutcome.result.groupIntegration"
    integration = _object(
        value,
        field=field,
        required={"seams", "decision", "rationale"},
    )
    seams = integration["seams"]
    if not isinstance(seams, list) or not seams:
        _contract_error(
            "A configured GROUP Review must verify at least one seam",
            field=f"{field}.seams",
        )
    for index, raw in enumerate(seams):
        seam_field = f"{field}.seams[{index}]"
        seam = _object(
            raw,
            field=seam_field,
            required={
                "seam",
                "participants",
                "status",
                "evidenceRefs",
            },
        )
        _text(seam["seam"], field=f"{seam_field}.seam")
        participants = _texts(
            seam["participants"],
            field=f"{seam_field}.participants",
            minimum=2,
        )
        if len(set(participants)) != len(participants):
            _contract_error(
                "GROUP seam participants must be unique",
                field=f"{seam_field}.participants",
            )
        if seam["status"] != "VERIFIED":
            _contract_error(
                "A successful GROUP Review requires VERIFIED seams",
                field=f"{seam_field}.status",
            )
        _texts(
            seam["evidenceRefs"],
            field=f"{seam_field}.evidenceRefs",
            minimum=1,
        )
    if integration["decision"] != "INTEGRATED":
        _contract_error(
            "groupIntegration.decision must be INTEGRATED",
            field=f"{field}.decision",
        )
    _text(integration["rationale"], field=f"{field}.rationale")


def _validate_delivery_readiness(value: object) -> None:
    field = "loopOutcome.result.deliveryReadiness"
    readiness = _object(
        value,
        field=field,
        required={
            "requirementCoverage",
            "integrationEvidence",
            "operationalReadiness",
            "openBlockingRisks",
            "acceptedRisks",
            "decision",
            "rationale",
        },
    )
    coverage = readiness["requirementCoverage"]
    if not isinstance(coverage, list) or not coverage:
        _contract_error(
            "deliveryReadiness.requirementCoverage must not be empty",
            field=f"{field}.requirementCoverage",
        )
    for index, raw in enumerate(coverage):
        coverage_field = f"{field}.requirementCoverage[{index}]"
        entry = _object(
            raw,
            field=coverage_field,
            required={
                "acceptancePoint",
                "ownerRefs",
                "status",
                "evidenceRefs",
            },
        )
        _text(
            entry["acceptancePoint"],
            field=f"{coverage_field}.acceptancePoint",
        )
        _texts(
            entry["ownerRefs"],
            field=f"{coverage_field}.ownerRefs",
            minimum=1,
        )
        _texts(
            entry["evidenceRefs"],
            field=f"{coverage_field}.evidenceRefs",
            minimum=1,
        )
        if entry["status"] != "COVERED":
            _contract_error(
                "A successful Delivery acceptance requires COVERED requirements",
                field=f"{coverage_field}.status",
            )
    if readiness["integrationEvidence"] != "SUFFICIENT":
        _contract_error(
            "deliveryReadiness.integrationEvidence must be SUFFICIENT",
            field=f"{field}.integrationEvidence",
        )
    if readiness["operationalReadiness"] not in {"READY", "NOT_APPLICABLE"}:
        _contract_error(
            "deliveryReadiness.operationalReadiness is invalid",
            field=f"{field}.operationalReadiness",
        )
    if readiness["openBlockingRisks"] != []:
        _contract_error(
            "A successful Delivery acceptance cannot retain blocking risks",
            field=f"{field}.openBlockingRisks",
        )
    _texts(
        readiness["acceptedRisks"],
        field=f"{field}.acceptedRisks",
    )
    if readiness["decision"] != "READY_FOR_USER_CONFIRMATION":
        _contract_error(
            "deliveryReadiness.decision must be READY_FOR_USER_CONFIRMATION",
            field=f"{field}.decision",
        )
    _text(readiness["rationale"], field=f"{field}.rationale")


_LAYER_VALIDATORS: dict[str, Callable[[object], None]] = {
    "taskAcceptance": _validate_task_acceptance,
    "groupIntegration": _validate_group_integration,
    "deliveryReadiness": _validate_delivery_readiness,
}


def validate_review_result_contract(
    loop_kind: str,
    result: object,
) -> dict[str, Any]:
    """Mechanically validate a receiver-declared successful Review result.

    This Controller-side check establishes only schema and declared terminal
    consistency. The independent Review receiver owns the technical acceptance
    judgment represented by the result.
    """

    if loop_kind not in REVIEW_LAYER_RESULT_FIELDS:
        _contract_error(
            "Review result validation requires a Review Loop kind",
            field="loopKind",
        )
    normalized = _json_object(result, field="loopOutcome.result")
    required_layer_field = REVIEW_LAYER_RESULT_FIELDS[loop_kind]
    allowed_fields = REVIEW_RESULT_SUPPORT_FIELDS | {required_layer_field}
    unexpected_fields = sorted(set(normalized) - allowed_fields)
    if unexpected_fields:
        _contract_error(
            "A successful Review result may only persist its layer-owned "
            "conclusion, findings, evidence, and Controller-owned snapshots",
            field=f"loopOutcome.result.{unexpected_fields[0]}",
        )
    for layer_field in REVIEW_LAYER_RESULT_FIELDS.values():
        if layer_field != required_layer_field and layer_field in normalized:
            _contract_error(
                f"{loop_kind} cannot submit {layer_field}",
                field=f"loopOutcome.result.{layer_field}",
            )
    for common_field in ("validationDecision", "reviewFindings"):
        if common_field not in normalized:
            _contract_error(
                f"A successful Review requires {common_field}",
                field=f"loopOutcome.result.{common_field}",
            )
    if required_layer_field not in normalized:
        _contract_error(
            f"{loop_kind} requires {required_layer_field}",
            field=f"loopOutcome.result.{required_layer_field}",
        )
    _validate_validation_decision(normalized["validationDecision"])
    _validate_review_findings(normalized["reviewFindings"])
    _LAYER_VALIDATORS[required_layer_field](normalized[required_layer_field])
    return normalized


__all__ = ("validate_review_result_contract",)
