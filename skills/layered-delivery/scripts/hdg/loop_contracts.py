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

LOOP_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/@-]{0,191}$")
RESOURCE_CLAIM = re.compile(r"^[a-z0-9][a-z0-9._:/@-]{0,255}$")


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


__all__ = (
    "LOOP_TERMINAL_STATUSES",
    "resource_claims_overlap",
    "validate_loop_descriptor",
    "validate_loop_outcome",
)
