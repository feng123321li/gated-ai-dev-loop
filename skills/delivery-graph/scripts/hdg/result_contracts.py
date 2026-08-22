from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import fail


_AFFECTED_SCOPE_FIELDS = frozenset(
    {
        "scopeId",
        "projectId",
        "paths",
        "modules",
        "contracts",
        "dependencyBasis",
        "exclusions",
    }
)
_EVIDENCE_REQUIRED_FIELDS = frozenset(
    {
        "evidenceId",
        "kind",
        "check",
        "command",
        "scope",
        "status",
        "completedAt",
    }
)
_EVIDENCE_KINDS = frozenset(
    {"TEST", "BUILD", "STATIC", "CONTRACT", "INSPECTION", "SMOKE", "E2E"}
)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _texts(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(_text(item) for item in value)
        and len(set(value)) == len(value)
    )


def _gap(code: str, message: str, field: str) -> dict[str, str]:
    return {"code": code, "message": message, "field": field}


def task_result_completeness_gaps(result: object) -> list[dict[str, str]]:
    """Return deterministic structural gaps in a successful TASK result.

    The Controller does not judge whether a check is technically sufficient.
    It does require the receiver to name each affected scope and bind at least
    one passing, auditable evidence record to every declared scope. This keeps
    result assembly and user confirmation from silently accepting an opaque
    success claim.
    """

    if not isinstance(result, dict):
        return [
            _gap(
                "TASK_RESULT_INVALID",
                "A successful TASK result must be a JSON object.",
                "loopOutcome.result",
            )
        ]

    gaps: list[dict[str, str]] = []
    raw_scopes = result.get("affectedScopes")
    valid_scope_ids: set[str] = set()
    if not isinstance(raw_scopes, list) or not raw_scopes:
        gaps.append(
            _gap(
                "TASK_AFFECTED_SCOPES_MISSING",
                "A successful TASK must declare at least one affected scope.",
                "loopOutcome.result.affectedScopes",
            )
        )
        raw_scopes = []
    else:
        seen_scope_ids: set[str] = set()
        for index, raw_scope in enumerate(raw_scopes):
            field = f"loopOutcome.result.affectedScopes[{index}]"
            valid = isinstance(raw_scope, dict) and set(raw_scope) == set(
                _AFFECTED_SCOPE_FIELDS
            )
            if valid:
                scope_id = raw_scope["scopeId"]
                valid = (
                    _text(scope_id)
                    and _text(raw_scope["projectId"])
                    and _texts(raw_scope["paths"])
                    and _texts(raw_scope["modules"])
                    and _texts(raw_scope["contracts"])
                    and _text(raw_scope["dependencyBasis"])
                    and _texts(raw_scope["exclusions"])
                    and bool(
                        raw_scope["paths"]
                        or raw_scope["modules"]
                        or raw_scope["contracts"]
                    )
                    and scope_id not in seen_scope_ids
                )
            if not valid:
                gaps.append(
                    _gap(
                        "TASK_AFFECTED_SCOPE_INVALID",
                        "Each affected scope must be unique, bounded, and structurally complete.",
                        field,
                    )
                )
                continue
            seen_scope_ids.add(scope_id)
            valid_scope_ids.add(scope_id)

    raw_evidence = result.get("verificationEvidence")
    covered_scope_ids: set[str] = set()
    if not isinstance(raw_evidence, list) or not raw_evidence:
        gaps.append(
            _gap(
                "TASK_VERIFICATION_EVIDENCE_MISSING",
                "A successful TASK must record at least one verification evidence item.",
                "loopOutcome.result.verificationEvidence",
            )
        )
        raw_evidence = []
    else:
        seen_evidence_ids: set[str] = set()
        for index, raw_item in enumerate(raw_evidence):
            field = f"loopOutcome.result.verificationEvidence[{index}]"
            valid = isinstance(raw_item, dict) and _EVIDENCE_REQUIRED_FIELDS <= set(
                raw_item
            )
            if valid:
                evidence_id = raw_item["evidenceId"]
                scope_refs = raw_item.get("scopeRefs", [])
                valid = (
                    _text(evidence_id)
                    and evidence_id not in seen_evidence_ids
                    and raw_item["kind"] in _EVIDENCE_KINDS
                    and _text(raw_item["check"])
                    and _text(raw_item["command"])
                    and _text(raw_item["scope"])
                    and raw_item["status"] in {"PASSED", "FAILED", "SKIPPED"}
                    and _text(raw_item["completedAt"])
                    and _texts(scope_refs)
                    and set(scope_refs) <= valid_scope_ids
                )
            if not valid:
                gaps.append(
                    _gap(
                        "TASK_EVIDENCE_INVALID",
                        "Each verification evidence item must be unique, auditable, and reference only declared scopes.",
                        field,
                    )
                )
                continue
            seen_evidence_ids.add(evidence_id)
            if raw_item["status"] != "PASSED":
                gaps.append(
                    _gap(
                        "TASK_EVIDENCE_NOT_PASSED",
                        "A successful TASK may only rely on PASSED verification evidence.",
                        f"{field}.status",
                    )
                )
                continue
            covered_scope_ids.update(scope_refs)

    for scope_id in sorted(valid_scope_ids - covered_scope_ids):
        gaps.append(
            _gap(
                "TASK_SCOPE_NOT_VERIFIED",
                f"Affected scope {scope_id!r} has no PASSED verification evidence.",
                "loopOutcome.result.verificationEvidence",
            )
        )
    return gaps


def assert_successful_task_result_complete(result: object) -> dict[str, Any]:
    gaps = task_result_completeness_gaps(result)
    if gaps:
        fail(
            "LOOP_TASK_RESULT_INCOMPLETE",
            "A successful TASK result must bind PASSED evidence to every affected scope",
            gaps=deepcopy(gaps),
        )
    return deepcopy(result)


__all__ = (
    "assert_successful_task_result_complete",
    "task_result_completeness_gaps",
)
