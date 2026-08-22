from __future__ import annotations

from copy import deepcopy
import re
from pathlib import Path
from typing import Any

from .dispatch_contracts import RECEIVER_SKILLS
from .errors import GatedLoopError, fail
from .fs_safe import read_regular_file
from .jsonio import (
    fingerprint,
    json_structure_within_limits,
    strict_json_loads,
)


AGENT_PROFILE_CATALOG_VERSION = 1
AGENT_PROFILE_CATALOG_FILE = "delivery-graph.agents.json"
MAX_AGENT_PROFILE_CATALOG_BYTES = 256 * 1024
LOOP_KINDS = tuple(RECEIVER_SKILLS)
SAFE_PROFILE_ID = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")
SAFE_CAPABILITY = re.compile(r"^[a-z][a-z0-9.-]{0,95}$")

_PROFILE_FIELDS = {
    "id",
    "kind",
    "loopKinds",
    "roleSkill",
    "capabilities",
    "helperProfiles",
    "outputContract",
}
_RECEIVER_PROFILE_FIELDS = _PROFILE_FIELDS | {"maxConcurrent"}


def _raw_built_in_catalog() -> dict[str, Any]:
    return {
        "catalogVersion": AGENT_PROFILE_CATALOG_VERSION,
        "profiles": [
            {
                "id": "task-implementation",
                "kind": "RECEIVER",
                "loopKinds": ["TASK_LOOP"],
                "roleSkill": "delivery-graph-task",
                "capabilities": [
                    "code.change",
                    "verification.execute",
                    "result.compose",
                ],
                "helperProfiles": [
                    "codebase-researcher",
                    "test-runner",
                    "result-checker",
                ],
                "outputContract": "task-loop-result-v1",
                "maxConcurrent": 4,
            },
            {
                "id": "task-review",
                "kind": "RECEIVER",
                "loopKinds": ["TASK_REVIEW_LOOP"],
                "roleSkill": "delivery-graph-review",
                "capabilities": [
                    "evidence.audit",
                    "finding.resolve",
                    "result.compose",
                ],
                "helperProfiles": ["result-checker"],
                "outputContract": "review-loop-result-v1",
                "maxConcurrent": 4,
            },
            {
                "id": "group-review",
                "kind": "RECEIVER",
                "loopKinds": ["GROUP_REVIEW_LOOP"],
                "roleSkill": "delivery-graph-review",
                "capabilities": [
                    "cross-task.audit",
                    "evidence.audit",
                    "result.compose",
                ],
                "helperProfiles": ["result-checker"],
                "outputContract": "review-loop-result-v1",
                "maxConcurrent": 2,
            },
            {
                "id": "delivery-review",
                "kind": "RECEIVER",
                "loopKinds": ["DELIVERY_REVIEW_LOOP"],
                "roleSkill": "delivery-graph-review",
                "capabilities": [
                    "delivery.audit",
                    "evidence.audit",
                    "result.compose",
                ],
                "helperProfiles": ["result-checker"],
                "outputContract": "review-loop-result-v1",
                "maxConcurrent": 1,
            },
            {
                "id": "codebase-researcher",
                "kind": "HELPER",
                "loopKinds": [],
                "roleSkill": None,
                "capabilities": [
                    "code.read",
                    "dependency.trace",
                    "context.summarize",
                ],
                "helperProfiles": [],
                "outputContract": "advisory-result-v1",
            },
            {
                "id": "test-runner",
                "kind": "HELPER",
                "loopKinds": [],
                "roleSkill": None,
                "capabilities": [
                    "verification.plan",
                    "verification.execute",
                    "failure.summarize",
                ],
                "helperProfiles": [],
                "outputContract": "advisory-result-v1",
            },
            {
                "id": "result-checker",
                "kind": "HELPER",
                "loopKinds": [],
                "roleSkill": None,
                "capabilities": [
                    "result.audit",
                    "evidence.coverage",
                    "omission.detect",
                ],
                "helperProfiles": [],
                "outputContract": "advisory-result-v1",
            },
        ],
        "loopRoutes": {
            "TASK_LOOP": "task-implementation",
            "TASK_REVIEW_LOOP": "task-review",
            "GROUP_REVIEW_LOOP": "group-review",
            "DELIVERY_REVIEW_LOOP": "delivery-review",
        },
    }


def _invalid(message: str, *, field: str) -> None:
    fail(
        "AGENT_PROFILE_CATALOG_INVALID",
        message,
        field=field,
    )


def _string_array(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) for item in value
    ):
        _invalid(f"{field} must be an array of strings", field=field)
    result = list(value)
    if len(result) != len(set(result)):
        _invalid(f"{field} must not contain duplicates", field=field)
    if pattern is not None and any(
        pattern.fullmatch(item) is None for item in result
    ):
        _invalid(f"{field} contains an invalid identifier", field=field)
    return result


def _normalized_profile(value: object, *, index: int) -> dict[str, Any]:
    field = f"profiles[{index}]"
    if not isinstance(value, dict):
        _invalid(
            f"{field} must contain exactly the profile contract fields",
            field=field,
        )
    kind = value.get("kind")
    if kind not in {"RECEIVER", "HELPER"}:
        _invalid(
            f"{field}.kind must be RECEIVER or HELPER",
            field=f"{field}.kind",
        )
    expected_fields = (
        _RECEIVER_PROFILE_FIELDS
        if kind == "RECEIVER"
        else _PROFILE_FIELDS
    )
    if set(value) != expected_fields:
        _invalid(
            f"{field} fields do not match its {kind} contract",
            field=field,
        )
    profile_id = value["id"]
    if (
        not isinstance(profile_id, str)
        or SAFE_PROFILE_ID.fullmatch(profile_id) is None
    ):
        _invalid(f"{field}.id is invalid", field=f"{field}.id")
    loop_kinds = _string_array(
        value["loopKinds"],
        field=f"{field}.loopKinds",
    )
    if any(loop_kind not in LOOP_KINDS for loop_kind in loop_kinds):
        _invalid(
            f"{field}.loopKinds contains an unsupported Loop kind",
            field=f"{field}.loopKinds",
        )
    capabilities = _string_array(
        value["capabilities"],
        field=f"{field}.capabilities",
        pattern=SAFE_CAPABILITY,
    )
    if not capabilities:
        _invalid(
            f"{field}.capabilities must not be empty",
            field=f"{field}.capabilities",
        )
    helper_profiles = _string_array(
        value["helperProfiles"],
        field=f"{field}.helperProfiles",
        pattern=SAFE_PROFILE_ID,
    )
    output_contract = value["outputContract"]
    if (
        not isinstance(output_contract, str)
        or SAFE_PROFILE_ID.fullmatch(output_contract) is None
    ):
        _invalid(
            f"{field}.outputContract is invalid",
            field=f"{field}.outputContract",
        )
    role_skill = value["roleSkill"]
    if kind == "RECEIVER":
        max_concurrent = value["maxConcurrent"]
        if (
            not isinstance(max_concurrent, int)
            or isinstance(max_concurrent, bool)
            or not 1 <= max_concurrent <= 4
        ):
            _invalid(
                f"{field}.maxConcurrent must be an integer from 1 to 4",
                field=f"{field}.maxConcurrent",
            )
        if not loop_kinds:
            _invalid(
                f"{field}.loopKinds must not be empty for a receiver",
                field=f"{field}.loopKinds",
            )
        expected_skills = {RECEIVER_SKILLS[item] for item in loop_kinds}
        if len(expected_skills) != 1 or role_skill not in expected_skills:
            _invalid(
                f"{field}.roleSkill does not match its Loop boundary",
                field=f"{field}.roleSkill",
            )
    elif loop_kinds or role_skill is not None or helper_profiles:
        _invalid(
            f"{field} helper profiles cannot own Loops or other helpers",
            field=field,
        )
    normalized = {
        "id": profile_id,
        "kind": kind,
        "loopKinds": loop_kinds,
        "roleSkill": role_skill,
        "capabilities": capabilities,
        "helperProfiles": helper_profiles,
        "outputContract": output_contract,
    }
    if kind == "RECEIVER":
        normalized["maxConcurrent"] = max_concurrent
    return normalized


def validate_agent_profile_catalog(
    value: object,
    *,
    configuration_source: str = "CALLER",
) -> dict[str, Any]:
    """Validate and fingerprint one complete specialist Agent catalog."""

    if not isinstance(value, dict) or set(value) != {
        "catalogVersion",
        "profiles",
        "loopRoutes",
    }:
        _invalid(
            "Agent profile catalog must contain exactly catalogVersion, "
            "profiles, and loopRoutes",
            field="$",
        )
    if value["catalogVersion"] != AGENT_PROFILE_CATALOG_VERSION:
        _invalid(
            "Unsupported Agent profile catalog version",
            field="catalogVersion",
        )
    profiles_value = value["profiles"]
    if not isinstance(profiles_value, list) or not profiles_value:
        _invalid("profiles must be a nonempty array", field="profiles")
    profiles = [
        _normalized_profile(item, index=index)
        for index, item in enumerate(profiles_value)
    ]
    by_id = {item["id"]: item for item in profiles}
    if len(by_id) != len(profiles):
        _invalid("Profile IDs must be unique", field="profiles")
    for index, profile in enumerate(profiles):
        for helper_id in profile["helperProfiles"]:
            helper = by_id.get(helper_id)
            if helper is None or helper["kind"] != "HELPER":
                _invalid(
                    "Receiver helperProfiles must reference HELPER profiles",
                    field=f"profiles[{index}].helperProfiles",
                )
    routes_value = value["loopRoutes"]
    if not isinstance(routes_value, dict) or set(routes_value) != set(
        LOOP_KINDS
    ):
        _invalid(
            "loopRoutes must route every supported Loop kind exactly once",
            field="loopRoutes",
        )
    routes: dict[str, str] = {}
    for loop_kind in LOOP_KINDS:
        profile_id = routes_value[loop_kind]
        profile = by_id.get(profile_id) if isinstance(profile_id, str) else None
        if (
            profile is None
            or profile["kind"] != "RECEIVER"
            or loop_kind not in profile["loopKinds"]
        ):
            _invalid(
                "loopRoutes must select a compatible RECEIVER profile",
                field=f"loopRoutes.{loop_kind}",
            )
        routes[loop_kind] = profile_id
    material = {
        "catalogVersion": AGENT_PROFILE_CATALOG_VERSION,
        "profiles": profiles,
        "loopRoutes": routes,
    }
    return {
        **material,
        "catalogFingerprint": fingerprint(material),
        "configurationSource": configuration_source,
    }


def built_in_agent_profile_catalog() -> dict[str, Any]:
    return validate_agent_profile_catalog(
        _raw_built_in_catalog(),
        configuration_source="PLUGIN_BUILT_IN",
    )


def load_agent_profile_catalog(
    workspace_root: str | Path,
) -> dict[str, Any]:
    """Load the fixed-name project JSON catalog, or the built-in catalog."""

    try:
        content = read_regular_file(
            workspace_root,
            AGENT_PROFILE_CATALOG_FILE,
        )
    except FileNotFoundError:
        return built_in_agent_profile_catalog()
    except GatedLoopError:
        raise
    if len(content) > MAX_AGENT_PROFILE_CATALOG_BYTES:
        _invalid(
            "Agent profile catalog exceeds the size limit",
            field="$",
        )
    try:
        text = content.decode("utf-8")
        if not json_structure_within_limits(text):
            raise ValueError("JSON structure exceeds safe limits")
        value = strict_json_loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        _invalid(
            f"Agent profile catalog JSON is invalid: {error}",
            field="$",
        )
    return validate_agent_profile_catalog(
        value,
        configuration_source="PROJECT_JSON",
    )


def profile_for_loop(
    catalog: dict[str, Any],
    loop_kind: str,
) -> dict[str, Any]:
    try:
        profile_id = catalog["loopRoutes"][loop_kind]
        profile = next(
            item for item in catalog["profiles"] if item["id"] == profile_id
        )
    except (KeyError, StopIteration):
        _invalid(
            "Agent profile catalog cannot route the requested Loop",
            field=f"loopRoutes.{loop_kind}",
        )
    return deepcopy(profile)


def team_plan_for_profile(
    catalog: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    by_id = {item["id"]: item for item in catalog["profiles"]}
    helpers = [by_id[item] for item in profile["helperProfiles"]]
    material = {
        "owner": {
            "profileId": profile["id"],
            "roleSkill": profile["roleSkill"],
            "capabilities": list(profile["capabilities"]),
            "outputContract": profile["outputContract"],
            "controlPlaneAccess": "RESERVATION_OWNER",
        },
        "helpers": [
            {
                "profileId": item["id"],
                "capabilities": list(item["capabilities"]),
                "outputContract": item["outputContract"],
                "controlPlaneAccess": "NONE",
            }
            for item in helpers
        ],
        "maxParallelHelpers": len(helpers),
        "coordination": {
            "ownerSubmitsLoopResult": True,
            "helpersUseLifecycleTools": False,
            "helpersReceiveControlPlaneCredentials": False,
        },
    }
    return {
        **material,
        "teamPlanFingerprint": fingerprint(material),
    }


__all__ = (
    "AGENT_PROFILE_CATALOG_FILE",
    "AGENT_PROFILE_CATALOG_VERSION",
    "built_in_agent_profile_catalog",
    "load_agent_profile_catalog",
    "profile_for_loop",
    "team_plan_for_profile",
    "validate_agent_profile_catalog",
)
