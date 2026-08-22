from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path
from typing import Any

from .errors import fail
from .fs_safe import read_regular_file
from .jsonio import (
    fingerprint,
    json_structure_within_limits,
    strict_json_loads,
)


SUPERVISOR_REGISTRY_VERSION = 1
SUPERVISOR_REGISTRY_FILE = "delivery-graph.supervisors.json"
MAX_SUPERVISOR_REGISTRY_BYTES = 128 * 1024
ENTRY_INTENTS = frozenset(
    {
        "NEW_DELIVERY",
        "REPLAN",
        "RESUME_PAUSED",
        "CONTINUE_DELIVERY",
        "CONFIRM_REVISION",
        "CLOSE_DELIVERY",
        "ARCHIVE_DELIVERY",
        "QUERY_STATUS",
        "AMBIGUOUS",
    }
)
SAFE_SUPERVISOR_ID = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")


def _raw_built_in_registry() -> dict[str, Any]:
    return {
        "registryVersion": SUPERVISOR_REGISTRY_VERSION,
        "enabled": False,
        "activationMode": "AMBIGUOUS_ONLY",
        "profiles": [
            {
                "id": "requirements-supervisor",
                "handles": ["NEW_DELIVERY", "REPLAN"],
            },
            {
                "id": "execution-supervisor",
                "handles": ["RESUME_PAUSED", "CONTINUE_DELIVERY"],
            },
            {
                "id": "lifecycle-supervisor",
                "handles": [
                    "CONFIRM_REVISION",
                    "CLOSE_DELIVERY",
                    "ARCHIVE_DELIVERY",
                ],
            },
            {
                "id": "observation-supervisor",
                "handles": ["QUERY_STATUS"],
            },
            {
                "id": "entry-supervisor",
                "handles": ["AMBIGUOUS"],
            },
        ],
        "fallbackProfileId": "entry-supervisor",
    }


def _invalid(message: str, *, field: str) -> None:
    fail(
        "SUPERVISOR_REGISTRY_INVALID",
        message,
        field=field,
    )


def validate_supervisor_registry(
    value: object,
    *,
    configuration_source: str = "CALLER",
) -> dict[str, Any]:
    """Validate an optional, decision-only entry Supervisor registry."""

    required = {
        "registryVersion",
        "enabled",
        "activationMode",
        "profiles",
        "fallbackProfileId",
    }
    if not isinstance(value, dict) or set(value) != required:
        _invalid(
            "Supervisor registry must contain exactly the registry contract fields",
            field="$",
        )
    if value["registryVersion"] != SUPERVISOR_REGISTRY_VERSION:
        _invalid(
            "Unsupported Supervisor registry version",
            field="registryVersion",
        )
    if not isinstance(value["enabled"], bool):
        _invalid("enabled must be boolean", field="enabled")
    if value["activationMode"] not in {
        "AMBIGUOUS_ONLY",
        "ALWAYS_ADVISE",
    }:
        _invalid(
            "activationMode must be AMBIGUOUS_ONLY or ALWAYS_ADVISE",
            field="activationMode",
        )
    profiles_value = value["profiles"]
    if not isinstance(profiles_value, list) or not profiles_value:
        _invalid("profiles must be a nonempty array", field="profiles")
    profiles: list[dict[str, Any]] = []
    handled: list[str] = []
    for index, item in enumerate(profiles_value):
        field = f"profiles[{index}]"
        if not isinstance(item, dict) or set(item) != {"id", "handles"}:
            _invalid(
                f"{field} must contain exactly id and handles",
                field=field,
            )
        profile_id = item["id"]
        if (
            not isinstance(profile_id, str)
            or SAFE_SUPERVISOR_ID.fullmatch(profile_id) is None
        ):
            _invalid(f"{field}.id is invalid", field=f"{field}.id")
        handles = item["handles"]
        if (
            not isinstance(handles, list)
            or not handles
            or any(not isinstance(intent, str) for intent in handles)
            or len(handles) != len(set(handles))
            or any(intent not in ENTRY_INTENTS for intent in handles)
        ):
            _invalid(
                f"{field}.handles contains invalid entry intents",
                field=f"{field}.handles",
            )
        profiles.append({"id": profile_id, "handles": list(handles)})
        handled.extend(handles)
    ids = [item["id"] for item in profiles]
    if len(ids) != len(set(ids)):
        _invalid("Supervisor profile IDs must be unique", field="profiles")
    if len(handled) != len(set(handled)) or set(handled) != ENTRY_INTENTS:
        _invalid(
            "Supervisor profiles must cover every entry intent exactly once",
            field="profiles",
        )
    fallback = value["fallbackProfileId"]
    fallback_profile = next(
        (item for item in profiles if item["id"] == fallback),
        None,
    )
    if fallback_profile is None or "AMBIGUOUS" not in fallback_profile[
        "handles"
    ]:
        _invalid(
            "fallbackProfileId must select the AMBIGUOUS Supervisor",
            field="fallbackProfileId",
        )
    material = {
        "registryVersion": SUPERVISOR_REGISTRY_VERSION,
        "enabled": value["enabled"],
        "activationMode": value["activationMode"],
        "profiles": profiles,
        "fallbackProfileId": fallback,
    }
    return {
        **material,
        "registryFingerprint": fingerprint(material),
        "configurationSource": configuration_source,
    }


def built_in_supervisor_registry() -> dict[str, Any]:
    return validate_supervisor_registry(
        _raw_built_in_registry(),
        configuration_source="PLUGIN_BUILT_IN",
    )


def load_supervisor_registry(
    workspace_root: str | Path,
) -> dict[str, Any]:
    try:
        content = read_regular_file(
            workspace_root,
            SUPERVISOR_REGISTRY_FILE,
        )
    except FileNotFoundError:
        return built_in_supervisor_registry()
    if len(content) > MAX_SUPERVISOR_REGISTRY_BYTES:
        _invalid("Supervisor registry exceeds the size limit", field="$")
    try:
        text = content.decode("utf-8")
        if not json_structure_within_limits(text):
            raise ValueError("JSON structure exceeds safe limits")
        value = strict_json_loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        _invalid(f"Supervisor registry JSON is invalid: {error}", field="$")
    return validate_supervisor_registry(
        value,
        configuration_source="PROJECT_JSON",
    )


def build_supervisor_routing(
    registry: dict[str, Any],
    *,
    explicit_intent: str,
    route_decision: dict[str, Any],
) -> dict[str, Any]:
    profile = next(
        item
        for item in registry["profiles"]
        if explicit_intent in item["handles"]
    )
    should_invoke = bool(
        registry["enabled"]
        and (
            registry["activationMode"] == "ALWAYS_ADVISE"
            or explicit_intent == "AMBIGUOUS"
        )
    )
    return {
        "enabled": registry["enabled"],
        "registryVersion": registry["registryVersion"],
        "registryFingerprint": registry["registryFingerprint"],
        "configurationSource": registry["configurationSource"],
        "activationMode": registry["activationMode"],
        "selectedSupervisorId": profile["id"],
        "shouldInvoke": should_invoke,
        "enforcement": "HOST_ADVISORY_NO_DECISION_RECEIPT",
        "decisionReceiptRequired": False,
        "invocationReason": (
            "AMBIGUOUS_ENTRY_REQUIRES_CLASSIFICATION"
            if should_invoke and explicit_intent == "AMBIGUOUS"
            else "OPTIONAL_ROUTE_VERIFICATION"
            if should_invoke
            else "DISABLED_OR_DETERMINISTIC_ROUTE_SUFFICIENT"
        ),
        "candidateDecision": {
            "intent": route_decision.get("intent"),
            "targetSkill": route_decision.get("targetSkill"),
            "requiresClarification": route_decision.get(
                "requiresClarification"
            ),
        },
        "boundary": {
            "role": "DECISION_ONLY",
            "inputAccess": "ENTRY_TEXT_AND_PERSISTED_STATE_SUMMARY",
            "toolAccess": "NONE",
            "executesRoute": False,
            "queriesBusinessData": False,
            "generatesUserResponse": False,
        },
        "outputContract": {
            "format": "JSON_OBJECT",
            "fields": ["intent", "targetSkill", "confidence", "reasonCodes"],
            "consumer": "ENTRY_ROUTER_OR_PRIMARY_COORDINATOR",
        },
    }


__all__ = (
    "SUPERVISOR_REGISTRY_FILE",
    "SUPERVISOR_REGISTRY_VERSION",
    "build_supervisor_routing",
    "built_in_supervisor_registry",
    "load_supervisor_registry",
    "validate_supervisor_registry",
)
