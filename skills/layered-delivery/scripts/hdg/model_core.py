from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


from .constants import MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION
from .errors import fail
from .jsonio import canonical_json, fingerprint
from .test_commands import normalize_test_argv


WORK_ITEM_SCHEMA_VERSION = SCHEMA_VERSION

WORK_ITEM_KINDS = ("DELIVERY", "CAPABILITY", "TASK")

WORK_ITEM_GATE_LEVELS = ("LIGHT", "FULL")

WORK_ITEM_CHANGE_SCENARIOS = (
    "API", "DOMAIN", "DATA", "MIGRATION", "CONFIG", "UI", "INTEGRATION", "REFACTOR",
    "TEST", "DOCS", "SECURITY", "PERFORMANCE", "BUILD", "OTHER",
)

WORK_ITEM_INTERFACE_KINDS = (
    "HTTP_ENDPOINT", "RPC", "FUNCTION", "METHOD", "CLASS", "EVENT", "SCHEMA", "CONFIG",
    "CLI", "UI", "FILE_FORMAT", "OTHER",
)

WORK_ITEM_AUTHORITIES = {
    "DELIVERY": "COORDINATION",
    "CAPABILITY": "COORDINATION",
    "TASK": "EXECUTION",
}

WORK_ITEM_SKILL_STAGES = (
    "DEVELOPMENT",
    "GATE",
    "FINAL_REVIEW",
)

ITEM_ID = re.compile(
    rf"^[a-z0-9][a-z0-9._-]{{0,{MAX_IDENTIFIER_LENGTH - 1}}}$"
)

SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")

TRACE_ID = re.compile(r"^(?:R|A)-(?:00[1-9]|0[1-9]\d|[1-9]\d{2})$")

PLACEHOLDER = re.compile(r"\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?", re.I)

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

WILDCARD = re.compile(r"[?*{}\[\]]")

KIND_TEXT = {
    "DELIVERY": "交付",
    "CAPABILITY": "能力",
    "TASK": "任务",
}

GATE_LEVEL_TEXT = {
    "LIGHT": "轻量",
    "FULL": "完整",
}

AUTHORITY_TEXT = {
    "COORDINATION": "协调",
    "EXECUTION": "执行",
}

SKILL_STAGE_TEXT = {
    "DEVELOPMENT": "开发",
    "GATE": "门禁",
    "FINAL_REVIEW": "最终审查",
}

def _exact_keys(value: object, expected: list[str] | tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(expected)


def _fail_shape(
    code: str,
    message: str,
    value: object,
    *,
    field: str,
    required: list[str] | tuple[str, ...] | set[str],
    optional: list[str] | tuple[str, ...] | set[str] = (),
) -> None:
    """Raise one stable error with the complete object-key difference."""

    required_keys = set(required)
    optional_keys = set(optional)
    actual_keys = set(value) if isinstance(value, dict) else set()
    fail(
        code,
        message,
        field=field,
        requiredKeys=sorted(required_keys),
        optionalKeys=sorted(optional_keys),
        expectedKeys=sorted(required_keys | optional_keys),
        actualKeys=sorted(actual_keys),
        missingKeys=sorted(required_keys - actual_keys),
        unknownKeys=sorted(
            actual_keys - required_keys - optional_keys,
        ),
        actualType=type(value).__name__,
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or PLACEHOLDER.search(value) or CONTROL.search(value):
        fail("WORK_ITEM_VALUE_INVALID", f"{field} must be nonempty text without placeholders", field=field)
    return value.strip()

def safe_id(value: object, field: str = "id") -> str:
    reserved = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)")
    if not isinstance(value, str) or not ITEM_ID.fullmatch(value) or value.endswith(".") or reserved.match(value):
        fail(
            "WORK_ITEM_ID_INVALID",
            f"{field} must be a safe lowercase identifier",
            field=field,
            maxLength=MAX_IDENTIFIER_LENGTH,
        )
    return value

def _gate_level(
    value: object,
    kind: str,
    field: str = "definition.gateLevel",
) -> str:
    if value not in WORK_ITEM_GATE_LEVELS or (kind != "TASK" and value != "FULL"):
        fail(
            "WORK_ITEM_GATE_LEVEL_INVALID",
            "gateLevel must be LIGHT or FULL, and coordination work items must be FULL",
            field=field,
            allowed=(
                list(WORK_ITEM_GATE_LEVELS)
                if kind == "TASK"
                else ["FULL"]
            ),
        )
    return str(value)

def _strings(values: object, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(values, list) or (not allow_empty and not values):
        qualifier = "an" if allow_empty else "a nonempty"
        fail("WORK_ITEM_VALUE_INVALID", f"{field} must be {qualifier} array", field=field)
    normalized = [_text(value, f"{field}[{index}]") for index, value in enumerate(values)]
    if len(set(normalized)) != len(normalized):
        fail("WORK_ITEM_VALUE_INVALID", f"{field} contains duplicate values", field=field)
    return normalized

def normalize_scope_pattern(value: object) -> str:
    normalized = _text(value, "scope").replace("\\", "/")
    segments = normalized.split("/")
    supported = not WILDCARD.search(normalized) or (
        normalized.endswith("/**") and not WILDCARD.search(normalized[:-3])
    )
    invalid = (
        PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(normalized).is_absolute()
        or ".." in segments
        or ":" in normalized
        or normalized == ".layered-delivery"
        or normalized.startswith(".layered-delivery/")
        or not supported
    )
    if invalid:
        fail("WORK_ITEM_SCOPE_INVALID", "Scope contains an unsafe path pattern", pattern=value)
    return normalized[2:] if normalized.startswith("./") else normalized

def _normalize_scope(values: object) -> list[str]:
    return sorted(set(normalize_scope_pattern(value) for value in _strings(values, "scope")))

def _trace_records(values: object, prefix: str, field: str) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        fail("WORK_ITEM_TRACE_INVALID", f"{field} must be a nonempty array", field=field)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(values):
        expected = ["id", "text"] if prefix == "R" else ["id", "requirementIds", "expectedResult"]
        record_field = f"{field}[{index}]"
        if not _exact_keys(entry, expected):
            _fail_shape(
                "WORK_ITEM_TRACE_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=expected,
            )
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if (
            not isinstance(entry_id, str)
            or not TRACE_ID.fullmatch(entry_id)
            or not entry_id.startswith(prefix + "-")
            or entry_id in seen
        ):
            fail(
                "WORK_ITEM_TRACE_INVALID",
                f"{record_field} has an invalid or duplicate ID",
                field=f"{record_field}.id",
                index=index,
                pattern=TRACE_ID.pattern,
            )
        seen.add(entry_id)
        if prefix == "R":
            result.append({"id": entry_id, "text": _text(entry["text"], f"{field}.{entry_id}")})
        else:
            result.append({
                "id": entry_id,
                "requirementIds": sorted(_strings(entry["requirementIds"], f"{field}.{entry_id}.requirementIds")),
                "expectedResult": _text(entry["expectedResult"], f"{field}.{entry_id}"),
            })
    return sorted(result, key=lambda item: item["id"])

def _validate_trace(requirements: list[dict[str, Any]], acceptance: list[dict[str, Any]]) -> None:
    requirement_ids = {item["id"] for item in requirements}
    independently_accepted: set[str] = set()
    for entry in acceptance:
        for requirement_id in entry["requirementIds"]:
            if requirement_id not in requirement_ids:
                fail("WORK_ITEM_TRACE_INVALID", f"{entry['id']} references unknown requirement {requirement_id}")
        if len(entry["requirementIds"]) == 1:
            independently_accepted.add(entry["requirementIds"][0])
    missing = sorted(requirement_ids - independently_accepted)
    if missing:
        fail(
            "WORK_ITEM_TRACE_INVALID",
            "Every requirement must have an independent acceptance criterion; "
            "cross-requirement criteria may only add integration coverage. Missing: "
            + ", ".join(missing),
            requirementIds=missing,
        )

def _child_records(
    values: object,
    kind: str,
    requirements: list[dict[str, Any]],
    acceptance: list[dict[str, Any]],
    *,
    field: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        fail("WORK_ITEM_CHILDREN_INVALID", f"{kind} must declare at least one child work item")
    expected_kind = "CAPABILITY" if kind == "DELIVERY" else "TASK"
    requirement_ids = {item["id"] for item in requirements}
    acceptance_ids = {item["id"] for item in acceptance}
    seen: set[str] = set()
    result = []
    for index, entry in enumerate(values):
        keys = ["id", "kind", "title", "requirementIds", "acceptanceIds"]
        record_field = f"{field}[{index}]"
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_CHILDREN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        if entry["kind"] != expected_kind:
            fail(
                "WORK_ITEM_CHILDREN_INVALID",
                f"{kind} children must be {expected_kind} records",
                field=f"{record_field}.kind",
                index=index,
                allowed=[expected_kind],
            )
        entry_id = safe_id(entry["id"], f"{record_field}.id")
        if entry_id in seen:
            fail("WORK_ITEM_CHILDREN_INVALID", f"Duplicate child ID: {entry_id}")
        seen.add(entry_id)
        linked_requirements = sorted(_strings(entry["requirementIds"], f"{entry_id}.requirementIds"))
        linked_acceptance = sorted(_strings(entry["acceptanceIds"], f"{entry_id}.acceptanceIds"))
        if any(item not in requirement_ids for item in linked_requirements) or any(
            item not in acceptance_ids for item in linked_acceptance
        ):
            fail("WORK_ITEM_TRACE_INVALID", f"{entry_id} references unknown parent trace IDs")
        result.append({
            "id": entry_id,
            "kind": expected_kind,
            "title": _text(entry["title"], f"{entry_id}.title"),
            "requirementIds": linked_requirements,
            "acceptanceIds": linked_acceptance,
        })
    return sorted(result, key=lambda item: item["id"])

def _execution_record(
    value: object,
    item_id: str,
    *,
    field: str,
) -> dict[str, Any]:
    keys = ["dependsOn", "inputs", "outputs"]
    if not _exact_keys(value, keys):
        _fail_shape(
            "WORK_ITEM_EXECUTION_INVALID",
            "Task execution contains missing or unknown fields",
            value,
            field=field,
            required=keys,
        )
    if not isinstance(value["dependsOn"], list):
        fail("WORK_ITEM_DEPENDENCY_INVALID", "Task dependsOn must be an array")
    depends_on = [safe_id(item, f"dependsOn[{index}]") for index, item in enumerate(value["dependsOn"])]
    if item_id in depends_on or len(set(depends_on)) != len(depends_on):
        fail("WORK_ITEM_DEPENDENCY_INVALID", "Task dependencies must be unique and cannot reference the Task itself")
    return {
        "dependsOn": sorted(depends_on),
        "inputs": _strings(value["inputs"], "execution.inputs", allow_empty=True),
        "outputs": _strings(value["outputs"], "execution.outputs"),
    }

def _decomposition_record(
    value: object,
    kind: str,
    item_id: str,
    parent: dict[str, Any] | None,
    *,
    field: str,
) -> dict[str, Any]:
    expected = ["status", "dependsOn"] if kind == "CAPABILITY" else ["status"]
    if not _exact_keys(value, expected):
        _fail_shape(
            "WORK_ITEM_DECOMPOSITION_INVALID",
            "Coordination decomposition contains missing or unknown fields",
            value,
            field=field,
            required=expected,
        )
    if value["status"] not in {"OPEN", "SEALED"}:
        fail(
            "WORK_ITEM_DECOMPOSITION_INVALID",
            "Coordination work items require decomposition status OPEN or SEALED",
            field=f"{field}.status",
            allowed=["OPEN", "SEALED"],
        )
    if kind == "DELIVERY":
        return {"status": value["status"]}
    if not isinstance(value["dependsOn"], list):
        fail("WORK_ITEM_DEPENDENCY_INVALID", "Capability dependsOn must be an array")
    depends_on = [safe_id(item, f"decomposition.dependsOn[{index}]") for index, item in enumerate(value["dependsOn"])]
    sibling_ids = {
        child["id"] for child in (parent or {}).get("children", []) if child["kind"] == "CAPABILITY"
    }
    if item_id in depends_on or len(set(depends_on)) != len(depends_on) or any(
        item not in sibling_ids for item in depends_on
    ):
        fail("WORK_ITEM_DEPENDENCY_INVALID", "Capability dependencies must be unique planned siblings and cannot reference itself")
    return {"status": value["status"], "dependsOn": sorted(depends_on)}

def _test_commands(values: object) -> list[list[str]]:
    if not isinstance(values, list) or not values:
        fail("WORK_ITEM_TEST_COMMAND_INVALID", "At least one test command is required")
    commands = [normalize_test_argv(value) for value in values]
    if any(value is None for value in commands):
        fail("WORK_ITEM_TEST_COMMAND_INVALID", "Test commands must be safe argv arrays")
    normalized = [value for value in commands if value is not None]
    canonical = [canonical_json(value) for value in normalized]
    if len(set(canonical)) != len(canonical):
        fail("WORK_ITEM_TEST_COMMAND_INVALID", "Duplicate test command")
    return normalized

def _required_skills(
    values: object,
    *,
    field: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        fail(
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
            "requiredSkills must be an array",
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    stage_order = {
        stage: index for index, stage in enumerate(WORK_ITEM_SKILL_STAGES)
    }
    for index, entry in enumerate(values):
        record_field = f"{field}[{index}]"
        keys = ["name", "stages", "purpose"]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_REQUIRED_SKILL_INVALID",
                f"{record_field} contains missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        name = entry["name"]
        if (
            not isinstance(name, str)
            or not SKILL_NAME.fullmatch(name)
            or name in seen
        ):
            fail(
                "WORK_ITEM_REQUIRED_SKILL_INVALID",
                f"{record_field}.name must be a unique portable Skill catalog name",
                field=f"{record_field}.name",
            )
        stages = entry["stages"]
        if (
            not isinstance(stages, list)
            or not stages
            or any(stage not in WORK_ITEM_SKILL_STAGES for stage in stages)
            or len(set(stages)) != len(stages)
        ):
            fail(
                "WORK_ITEM_REQUIRED_SKILL_INVALID",
                f"{record_field}.stages must contain unique supported stages",
                field=f"{record_field}.stages",
                allowed=list(WORK_ITEM_SKILL_STAGES),
            )
        seen.add(name)
        result.append({
            "name": name,
            "stages": sorted(stages, key=stage_order.__getitem__),
            "purpose": _text(entry["purpose"], f"{record_field}.purpose"),
        })
    return sorted(result, key=lambda item: item["name"])

def validate_skill_catalog(values: object) -> dict[str, list[str]]:
    """Validate root- and project-scoped Skill names from the current host."""

    if not _exact_keys(values, ["root", "project"]):
        _fail_shape(
            "WORK_ITEM_SKILL_CATALOG_INVALID",
            "available_skills contains missing or unknown fields",
            values,
            field="available_skills",
            required=["root", "project"],
        )
    result: dict[str, list[str]] = {}
    for scope in ("root", "project"):
        entries = values[scope]
        if not isinstance(entries, list):
            fail(
                "WORK_ITEM_SKILL_CATALOG_INVALID",
                (
                    f"available_skills.{scope} must be an array of exact "
                    "Skill catalog names"
                ),
                field=f"available_skills.{scope}",
            )
        names: list[str] = []
        seen: set[str] = set()
        for index, value in enumerate(entries):
            if (
                not isinstance(value, str)
                or not SKILL_NAME.fullmatch(value)
                or value in seen
            ):
                fail(
                    "WORK_ITEM_SKILL_CATALOG_INVALID",
                    (
                        f"available_skills.{scope} must contain unique "
                        "portable Skill catalog names"
                    ),
                    field=f"available_skills.{scope}[{index}]",
                )
            seen.add(value)
            names.append(value)
        result[scope] = sorted(names)
    return result

def required_skill_policy() -> dict[str, str]:
    """Return the portable execution policy bound to required Skill records."""

    return {
        "authorization": "FROZEN_REQUIRED_SKILLS",
        "activation": "CURRENT_EXECUTOR_NATIVE_SKILL_INVOCATION_REQUIRED",
        "invocation": "EXECUTION_ADAPTER_AUTOMATIC",
        "repeatUserPrompt": "FORBIDDEN_AFTER_FREEZE",
        "identity": "CANONICAL_CATALOG_NAME_WITHOUT_HOST_COMMAND_PREFIX",
        "mechanism": "HOST_NATIVE_SKILL",
        "hostBinding": "CURRENT_STAGE_EXECUTION_HOST",
        "planningHost": "AUDIT_ONLY_NOT_EXECUTION_CONSTRAINT",
        "loadingOnly": "REJECTED",
        "compliance": "GRAPH_BOUND_CONFORMANCE_PASS_REQUIRED",
        "unavailable": "BLOCK_STAGE_AND_REPORT",
    }

def _linked_trace_ids(values: object, allowed: set[str], field: str, *, allow_empty: bool = False) -> list[str]:
    linked = sorted(_strings(values, field, allow_empty=allow_empty))
    if any(item not in allowed for item in linked):
        fail("WORK_ITEM_TRACE_INVALID", f"{field} references an unknown trace ID", field=field)
    return linked

def _development_test_plan(
    values: object,
    acceptance: list[dict[str, Any]],
    test_command_count: int,
    *,
    field: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        fail(
            "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
            f"{field} must be a nonempty array",
            field=field,
        )
    acceptance_ids = {item["id"] for item in acceptance}
    covered: set[str] = set()
    result = []
    for index, entry in enumerate(values):
        record_field = f"{field}[{index}]"
        keys = ["acceptanceIds", "approach", "commandIndexes"]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        linked = _linked_trace_ids(
            entry["acceptanceIds"],
            acceptance_ids,
            f"{record_field}.acceptanceIds",
        )
        covered.update(linked)
        indexes = entry["commandIndexes"]
        if (
            not isinstance(indexes, list)
            or not indexes
            or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 or item >= test_command_count for item in indexes)
            or len(set(indexes)) != len(indexes)
        ):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                (
                    f"{record_field}.commandIndexes must reference frozen "
                    "test commands"
                ),
                field=f"{record_field}.commandIndexes",
            )
        result.append({
            "acceptanceIds": linked,
            "approach": _text(
                entry["approach"],
                f"{record_field}.approach",
            ),
            "commandIndexes": sorted(indexes),
        })
    if any(item["id"] not in covered for item in acceptance):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Every acceptance criterion must be covered by developmentPlan.testPlan")
    return result

def _task_development_plan(
    value: object,
    normalized: dict[str, Any],
    *,
    field: str,
) -> dict[str, Any]:
    keys = [
        "purpose", "scenarios", "fileChanges", "interfaces", "logic", "dataAndTransactions",
        "compatibility", "testPlan", "reviewPoints",
    ]
    if (
        not isinstance(value, dict)
        or not set(keys).issubset(value)
        or not set(value).issubset(set(keys) | {"generatedFileRoots"})
    ):
        _fail_shape(
            "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
            "Task developmentPlan contains missing or unknown fields",
            value,
            field=field,
            required=keys,
            optional=["generatedFileRoots"],
        )
    requirement_ids = {item["id"] for item in normalized["requirements"]}
    covered: set[str] = set()
    if not isinstance(value["scenarios"], list) or not value["scenarios"]:
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.scenarios must be nonempty")
    scenarios = []
    for index, entry in enumerate(value["scenarios"]):
        record_field = f"{field}.scenarios[{index}]"
        keys = ["kind", "title", "description", "requirementIds"]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        if entry["kind"] not in WORK_ITEM_CHANGE_SCENARIOS:
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field}.kind is invalid",
                field=f"{record_field}.kind",
                allowed=list(WORK_ITEM_CHANGE_SCENARIOS),
            )
        linked = _linked_trace_ids(
            entry["requirementIds"],
            requirement_ids,
            f"{record_field}.requirementIds",
        )
        covered.update(linked)
        scenarios.append({
            "kind": entry["kind"],
            "title": _text(entry["title"], f"{record_field}.title"),
            "description": _text(
                entry["description"],
                f"{record_field}.description",
            ),
            "requirementIds": linked,
        })
    if any(item["id"] not in covered for item in normalized["requirements"]):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Every requirement must be covered by a development scenario")

    generated_roots_value = value.get("generatedFileRoots", [])
    if not isinstance(generated_roots_value, list):
        fail(
            "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
            "developmentPlan.generatedFileRoots must be an array",
        )
    generated_file_roots: list[dict[str, str]] = []
    seen_generated_roots: set[str] = set()
    for index, entry in enumerate(generated_roots_value):
        record_field = f"{field}.generatedFileRoots[{index}]"
        keys = ["path", "purpose"]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        generated_path = normalize_scope_pattern(entry["path"])
        if (
            generated_path == "**"
            or not generated_path.endswith("/**")
            or generated_path in seen_generated_roots
            or not scope_contains(normalized["scope"], [generated_path])
            or any(
                _scope_covers(existing, generated_path)
                or _scope_covers(generated_path, existing)
                for existing in seen_generated_roots
            )
        ):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                (
                    f"{record_field}.path must be a unique, non-overlapping /** "
                    "subtree inside Task scope"
                ),
                field=f"{record_field}.path",
            )
        seen_generated_roots.add(generated_path)
        generated_file_roots.append({
            "path": generated_path,
            "purpose": _text(
                entry["purpose"],
                f"{record_field}.purpose",
            ),
        })
    generated_file_roots.sort(key=lambda item: item["path"])

    if (
        not isinstance(value["fileChanges"], list)
        or (
            not value["fileChanges"]
            and not generated_file_roots
        )
    ):
        fail(
            "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
            (
                "Task developmentPlan requires exact fileChanges or "
                "ADD-only generatedFileRoots"
            ),
        )
    seen_paths: set[str] = set()
    file_changes = []
    for index, entry in enumerate(value["fileChanges"]):
        record_field = f"{field}.fileChanges[{index}]"
        keys = ["path", "action", "purpose"]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        if entry["action"] not in {"ADD", "MODIFY", "REMOVE"}:
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field}.action is invalid",
                field=f"{record_field}.action",
                allowed=["ADD", "MODIFY", "REMOVE"],
            )
        planned_path = normalize_scope_pattern(entry["path"])
        if (
            WILDCARD.search(planned_path)
            or planned_path in seen_paths
            or not scope_contains(normalized["scope"], [planned_path])
            or any(
                _scope_covers(root["path"], planned_path)
                for root in generated_file_roots
            )
        ):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                (
                    f"{record_field}.path must be a unique exact path "
                    "inside Task scope"
                ),
                field=f"{record_field}.path",
            )
        seen_paths.add(planned_path)
        file_changes.append({
            "path": planned_path,
            "action": entry["action"],
            "purpose": _text(
                entry["purpose"],
                f"{record_field}.purpose",
            ),
        })
    file_changes.sort(key=lambda item: item["path"])

    if not isinstance(value["interfaces"], list):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.interfaces must be an array")
    interfaces = []
    interface_keys = ["name", "kind", "action", "location", "currentContract", "targetContract", "requirementIds"]
    for index, entry in enumerate(value["interfaces"]):
        record_field = f"{field}.interfaces[{index}]"
        if not _exact_keys(entry, interface_keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=interface_keys,
            )
        if entry["kind"] not in WORK_ITEM_INTERFACE_KINDS:
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field}.kind is invalid",
                field=f"{record_field}.kind",
                allowed=list(WORK_ITEM_INTERFACE_KINDS),
            )
        if entry["action"] not in {"ADD", "MODIFY", "REMOVE"}:
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field}.action is invalid",
                field=f"{record_field}.action",
                allowed=["ADD", "MODIFY", "REMOVE"],
            )
        interfaces.append({
            "name": _text(entry["name"], f"{record_field}.name"),
            "kind": entry["kind"],
            "action": entry["action"],
            "location": _text(
                entry["location"],
                f"{record_field}.location",
            ),
            "currentContract": _text(
                entry["currentContract"],
                f"{record_field}.currentContract",
            ),
            "targetContract": _text(
                entry["targetContract"],
                f"{record_field}.targetContract",
            ),
            "requirementIds": _linked_trace_ids(
                entry["requirementIds"],
                requirement_ids,
                f"{record_field}.requirementIds",
            ),
        })
    return {
        "purpose": _text(value["purpose"], f"{field}.purpose"),
        "scenarios": scenarios,
        "fileChanges": file_changes,
        "generatedFileRoots": generated_file_roots,
        "interfaces": interfaces,
        "logic": _strings(value["logic"], f"{field}.logic"),
        "dataAndTransactions": _strings(
            value["dataAndTransactions"],
            f"{field}.dataAndTransactions",
            allow_empty=True,
        ),
        "compatibility": _strings(
            value["compatibility"],
            f"{field}.compatibility",
        ),
        "testPlan": _development_test_plan(
            value["testPlan"],
            normalized["acceptance"],
            len(normalized["testCommands"]),
            field=f"{field}.testPlan",
        ),
        "reviewPoints": _strings(
            value["reviewPoints"],
            f"{field}.reviewPoints",
        ),
    }

def _coordination_development_plan(
    value: object,
    normalized: dict[str, Any],
    *,
    field: str,
) -> dict[str, Any]:
    keys = [
        "purpose", "childPlans", "sharedContracts", "integrationFlow", "deliveryWaves",
        "testPlan", "reviewPoints",
    ]
    if not _exact_keys(value, keys):
        _fail_shape(
            "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
            "Coordination developmentPlan contains missing or unknown fields",
            value,
            field=field,
            required=keys,
        )
    requirements = {item["id"] for item in normalized["requirements"]}
    acceptance = {item["id"] for item in normalized["acceptance"]}
    child_by_id = {item["id"]: item for item in normalized["children"]}
    if not isinstance(value["childPlans"], list) or len(value["childPlans"]) != len(normalized["children"]):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.childPlans must cover every direct child exactly once")
    seen: set[str] = set()
    child_plans = []
    for index, entry in enumerate(value["childPlans"]):
        record_field = f"{field}.childPlans[{index}]"
        keys = [
            "id",
            "purpose",
            "deliverables",
            "requirementIds",
            "acceptanceIds",
            "dependsOn",
        ]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        child = child_by_id.get(entry_id)
        if (
            child is None
            or entry_id in seen
        ):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} does not match a unique planned child",
                field=f"{record_field}.id",
            )
        seen.add(entry_id)
        linked_requirements = _linked_trace_ids(
            entry["requirementIds"],
            requirements,
            f"{record_field}.requirementIds",
        )
        linked_acceptance = _linked_trace_ids(
            entry["acceptanceIds"],
            acceptance,
            f"{record_field}.acceptanceIds",
        )
        if linked_requirements != child["requirementIds"] or linked_acceptance != child["acceptanceIds"]:
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} trace mapping must match the child contract",
                field=record_field,
            )
        if not isinstance(entry["dependsOn"], list):
            fail(
                "WORK_ITEM_DEPENDENCY_INVALID",
                (
                    f"{record_field}.dependsOn must reference unique "
                    "sibling children"
                ),
                field=f"{record_field}.dependsOn",
            )
        depends_on = [
            safe_id(
                item,
                f"{record_field}.dependsOn[{dependency_index}]",
            )
            for dependency_index, item in enumerate(entry["dependsOn"])
        ]
        if entry_id in depends_on or len(set(depends_on)) != len(depends_on) or any(item not in child_by_id for item in depends_on):
            fail(
                "WORK_ITEM_DEPENDENCY_INVALID",
                (
                    f"{record_field}.dependsOn must reference unique "
                    "sibling children"
                ),
                field=f"{record_field}.dependsOn",
            )
        child_plans.append({
            "id": entry_id,
            "purpose": _text(
                entry["purpose"],
                f"{record_field}.purpose",
            ),
            "deliverables": _strings(
                entry["deliverables"],
                f"{record_field}.deliverables",
            ),
            "requirementIds": linked_requirements,
            "acceptanceIds": linked_acceptance,
            "dependsOn": sorted(depends_on),
        })
    child_plans.sort(key=lambda item: item["id"])

    graph = {item["id"]: item["dependsOn"] for item in child_plans}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visiting:
            fail("WORK_ITEM_DEPENDENCY_CYCLE", "developmentPlan child dependencies contain a cycle")
        if item_id in visited:
            return
        visiting.add(item_id)
        for dependency in graph.get(item_id, []):
            visit(dependency)
        visiting.remove(item_id)
        visited.add(item_id)

    for child_id in graph:
        visit(child_id)

    if not isinstance(value["sharedContracts"], list):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.sharedContracts must be an array")
    shared_contracts = []
    contract_keys = ["name", "kind", "description", "providerChildIds", "consumerChildIds", "requirementIds"]
    for index, entry in enumerate(value["sharedContracts"]):
        record_field = f"{field}.sharedContracts[{index}]"
        if not _exact_keys(entry, contract_keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=contract_keys,
            )
        if entry["kind"] not in WORK_ITEM_INTERFACE_KINDS:
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field}.kind is invalid",
                field=f"{record_field}.kind",
                allowed=list(WORK_ITEM_INTERFACE_KINDS),
            )
        providers = sorted(_strings(
            entry["providerChildIds"],
            f"{record_field}.providerChildIds",
        ))
        consumers = sorted(_strings(
            entry["consumerChildIds"],
            f"{record_field}.consumerChildIds",
        ))
        if any(item not in child_by_id for item in providers + consumers):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} references an unknown child",
                field=record_field,
            )
        shared_contracts.append({
            "name": _text(entry["name"], f"{record_field}.name"),
            "kind": entry["kind"],
            "description": _text(
                entry["description"],
                f"{record_field}.description",
            ),
            "providerChildIds": providers,
            "consumerChildIds": consumers,
            "requirementIds": _linked_trace_ids(
                entry["requirementIds"],
                requirements,
                f"{record_field}.requirementIds",
            ),
        })

    if not isinstance(value["deliveryWaves"], list) or not value["deliveryWaves"]:
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.deliveryWaves must be nonempty")
    wave_by_child: dict[str, int] = {}
    wave_orders: set[int] = set()
    delivery_waves = []
    for index, entry in enumerate(value["deliveryWaves"]):
        record_field = f"{field}.deliveryWaves[{index}]"
        keys = ["order", "name", "childIds", "exitCriteria"]
        if not _exact_keys(entry, keys):
            _fail_shape(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} has missing or unknown fields",
                entry,
                field=record_field,
                required=keys,
            )
        order = entry.get("order") if isinstance(entry, dict) else None
        if (
            not isinstance(order, int)
            or isinstance(order, bool)
            or order < 1
            or order in wave_orders
        ):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field}.order is invalid",
                field=f"{record_field}.order",
                minimum=1,
            )
        wave_orders.add(order)
        child_ids = sorted(
            safe_id(item, f"{record_field}.childIds")
            for item in _strings(
                entry["childIds"],
                f"{record_field}.childIds",
            )
        )
        if any(item not in child_by_id or item in wave_by_child for item in child_ids):
            fail(
                "WORK_ITEM_DEVELOPMENT_PLAN_INVALID",
                f"{record_field} must contain unique planned children",
                field=f"{record_field}.childIds",
            )
        for child_id in child_ids:
            wave_by_child[child_id] = order
        delivery_waves.append({
            "order": order,
            "name": _text(entry["name"], f"{record_field}.name"),
            "childIds": child_ids,
            "exitCriteria": _text(
                entry["exitCriteria"],
                f"{record_field}.exitCriteria",
            ),
        })
    delivery_waves.sort(key=lambda item: item["order"])
    if len(wave_by_child) != len(child_by_id) or any(
        wave_by_child[dependency] >= wave_by_child[item["id"]]
        for item in child_plans
        for dependency in item["dependsOn"]
    ):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Delivery waves must cover every child and order dependencies before consumers")
    return {
        "purpose": _text(value["purpose"], f"{field}.purpose"),
        "childPlans": child_plans,
        "sharedContracts": shared_contracts,
        "integrationFlow": _strings(
            value["integrationFlow"],
            f"{field}.integrationFlow",
        ),
        "deliveryWaves": delivery_waves,
        "testPlan": _development_test_plan(
            value["testPlan"],
            normalized["acceptance"],
            len(normalized["testCommands"]),
            field=f"{field}.testPlan",
        ),
        "reviewPoints": _strings(
            value["reviewPoints"],
            f"{field}.reviewPoints",
        ),
    }

def _scope_covers(parent_pattern: str, child_pattern: str) -> bool:
    if parent_pattern == "**":
        return True
    if not parent_pattern.endswith("/**"):
        return parent_pattern == child_pattern
    prefix = parent_pattern[:-3]
    return child_pattern == prefix or child_pattern.startswith(prefix + "/")

def scope_contains(parent_scope: list[str], child_scope: list[str]) -> bool:
    return all(any(_scope_covers(parent, child) for parent in parent_scope) for child in child_scope)

def scope_patterns_overlap(left: list[str], right: list[str]) -> bool:
    return any(_scope_covers(a, b) or _scope_covers(b, a) for a in left for b in right)

def _normalize_parent(definition: dict[str, Any], parent: dict[str, Any] | None) -> dict[str, Any]:
    if definition["kind"] == "DELIVERY":
        if definition.get("parentId") is not None:
            fail("WORK_ITEM_PARENT_INVALID", "Delivery cannot have a parent work item")
        return {"parentId": None, "parentContractFingerprint": None}
    if definition["parentId"] is None:
        if parent:
            fail("WORK_ITEM_PARENT_INVALID", f"Root {definition['kind']} cannot receive a parent contract")
        if definition["kind"] == "TASK" and definition["execution"]["dependsOn"]:
            fail("WORK_ITEM_DEPENDENCY_INVALID", "A root Task cannot depend on sibling Tasks; use a Capability root")
        if definition["kind"] == "CAPABILITY" and definition["decomposition"]["dependsOn"]:
            fail("WORK_ITEM_DEPENDENCY_INVALID", "A root Capability cannot depend on sibling Capabilities; use a Delivery root")
        return {"parentId": None, "parentContractFingerprint": None}
    if not parent or definition["parentId"] != parent["id"]:
        fail("WORK_ITEM_PARENT_INVALID", f"{definition['kind']} must reference its supplied parent")
    expected_parent_kind = "DELIVERY" if definition["kind"] == "CAPABILITY" else "CAPABILITY"
    if parent["kind"] != expected_parent_kind:
        fail("WORK_ITEM_PARENT_INVALID", f"{definition['kind']} parent must be {expected_parent_kind}")
    planned = next((item for item in parent.get("children", []) if item["id"] == definition["id"] and item["kind"] == definition["kind"]), None)
    if not planned:
        fail("WORK_ITEM_PARENT_PLAN_MISMATCH", f"{definition['id']} is not declared by its parent baseline")
    if not scope_contains(parent["scope"], definition["scope"]):
        fail("WORK_ITEM_SCOPE_EXPANDED", f"{definition['id']} scope expands beyond its parent baseline")
    return {
        "parentId": parent["id"],
        "parentContractFingerprint": work_item_child_contract_fingerprint(parent, definition["id"]),
    }

def validate_work_item_definition(
    definition: object,
    *,
    parent: dict[str, Any] | None = None,
    field: str = "definition",
) -> dict[str, Any]:
    if not isinstance(definition, dict):
        fail(
            "WORK_ITEM_DEFINITION_INVALID",
            "Work item definition must be an object",
            field=field,
            actualType=type(definition).__name__,
        )
    kind = definition.get("kind")
    if kind not in WORK_ITEM_KINDS:
        fail(
            "WORK_ITEM_KIND_INVALID",
            "Work item kind must be DELIVERY, CAPABILITY, or TASK",
            field=f"{field}.kind",
            allowed=list(WORK_ITEM_KINDS),
        )
    if definition.get("schemaVersion") != WORK_ITEM_SCHEMA_VERSION:
        fail(
            "WORK_ITEM_SCHEMA_INVALID",
            (
                "Work item schemaVersion must be "
                f"{WORK_ITEM_SCHEMA_VERSION}"
            ),
            field=f"{field}.schemaVersion",
            allowed=[WORK_ITEM_SCHEMA_VERSION],
        )
    if kind == "TASK" and "children" in definition:
        fail("WORK_ITEM_TASK_NOT_LEAF", "Task is an executable leaf and cannot contain children")
    if kind != "TASK" and "execution" in definition:
        fail("WORK_ITEM_EXECUTION_INVALID", "Only Task work items can contain execution metadata")
    common = [
        "schemaVersion", "id", "kind", "gateLevel", "title", "goal", "scope", "nonGoals",
        "requirements", "acceptance", "testCommands", "requiredSkills", "risks",
        "decisions",
    ]
    plan_keys = ["developmentPlan"]
    expected = (
        common + plan_keys + ["decomposition", "children"]
        if kind == "DELIVERY"
        else common + plan_keys + ["parentId"] + (["execution"] if kind == "TASK" else ["decomposition", "children"])
    )
    expected_keys = set(expected)
    actual_keys = set(definition)
    required_keys = expected_keys - {"requiredSkills"}
    if not required_keys.issubset(actual_keys) or not actual_keys.issubset(expected_keys):
        _fail_shape(
            "WORK_ITEM_DEFINITION_INVALID",
            "Work item definition contains missing or unknown fields",
            definition,
            field=field,
            required=required_keys,
            optional=["requiredSkills"],
        )
    normalized: dict[str, Any] = {
        "schemaVersion": WORK_ITEM_SCHEMA_VERSION,
        "id": safe_id(definition["id"], f"{field}.id"),
        "kind": kind,
        "gateLevel": _gate_level(
            definition["gateLevel"],
            kind,
            f"{field}.gateLevel",
        ),
        "authorityKind": WORK_ITEM_AUTHORITIES[kind],
        "title": _text(definition["title"], f"{field}.title"),
        "goal": _text(definition["goal"], f"{field}.goal"),
        "scope": _normalize_scope(definition["scope"]),
        "nonGoals": _strings(
            definition["nonGoals"],
            f"{field}.nonGoals",
        ),
        "requirements": _trace_records(
            definition["requirements"],
            "R",
            f"{field}.requirements",
        ),
        "acceptance": _trace_records(
            definition["acceptance"],
            "A",
            f"{field}.acceptance",
        ),
        "testCommands": _test_commands(definition["testCommands"]),
        "requiredSkills": _required_skills(
            definition.get("requiredSkills", []),
            field=f"{field}.requiredSkills",
        ),
        "risks": _strings(definition["risks"], f"{field}.risks"),
        "decisions": _strings(
            definition["decisions"],
            f"{field}.decisions",
        ),
    }
    if parent is not None and any(
        "FINAL_REVIEW" in requirement["stages"]
        for requirement in normalized["requiredSkills"]
    ):
        fail(
            "WORK_ITEM_REQUIRED_SKILL_INVALID",
            "FINAL_REVIEW required Skills must be declared on the hierarchy root",
        )
    _validate_trace(normalized["requirements"], normalized["acceptance"])
    if kind == "TASK":
        normalized["execution"] = _execution_record(
            definition["execution"],
            normalized["id"],
            field=f"{field}.execution",
        )
    else:
        normalized["decomposition"] = _decomposition_record(
            definition["decomposition"],
            kind,
            normalized["id"],
            parent,
            field=f"{field}.decomposition",
        )
        normalized["children"] = _child_records(
            definition["children"],
            kind,
            normalized["requirements"],
            normalized["acceptance"],
            field=f"{field}.children",
        )
    if "developmentPlan" in definition:
        normalized["developmentPlan"] = (
            _task_development_plan(
                definition["developmentPlan"],
                normalized,
                field=f"{field}.developmentPlan",
            )
            if kind == "TASK"
            else _coordination_development_plan(
                definition["developmentPlan"],
                normalized,
                field=f"{field}.developmentPlan",
            )
        )
    normalized.update(_normalize_parent({**definition, **normalized}, parent))
    if parent and parent.get("developmentPlan"):
        planned = next((item for item in parent["developmentPlan"]["childPlans"] if item["id"] == normalized["id"]), None)
        actual_dependencies = (
            normalized["execution"]["dependsOn"] if kind == "TASK" else normalized["decomposition"]["dependsOn"]
        )
        if not planned or planned["dependsOn"] != actual_dependencies:
            fail("WORK_ITEM_PARENT_PLAN_MISMATCH", f"{normalized['id']} dependencies do not match the frozen parent development plan")
    return normalized

def validate_hierarchy_definition(hierarchy: object) -> dict[str, Any]:
    """Validate and normalize one complete requirement hierarchy."""
    if not _exact_keys(hierarchy, ["schemaVersion", "root"]):
        _fail_shape(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Hierarchy definition contains missing or unknown fields",
            hierarchy,
            field="hierarchy",
            required=["schemaVersion", "root"],
        )
    if hierarchy["schemaVersion"] != WORK_ITEM_SCHEMA_VERSION:
        fail(
            "WORK_ITEM_SCHEMA_INVALID",
            f"Hierarchy schemaVersion must be {WORK_ITEM_SCHEMA_VERSION}",
        )

    seen: set[str] = set()

    def normalize_node(
        value: object,
        parent: dict[str, Any] | None,
        *,
        field: str,
    ) -> dict[str, Any]:
        if not _exact_keys(value, ["definition", "children"]):
            _fail_shape(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Hierarchy node contains missing or unknown fields",
                value,
                field=field,
                required=["definition", "children"],
            )
        if not isinstance(value["children"], list):
            fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy node children must be an array")
        definition = validate_work_item_definition(
            value["definition"],
            parent=parent,
            field=f"{field}.definition",
        )
        if definition["id"] in seen:
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                f"Hierarchy contains duplicate work item ID: {definition['id']}",
            )
        seen.add(definition["id"])
        if definition["kind"] == "TASK":
            if value["children"]:
                fail("WORK_ITEM_TASK_NOT_LEAF", "Task hierarchy nodes cannot contain children")
            return {"definition": definition, "children": []}

        expected = {(item["id"], item["kind"]) for item in definition["children"]}
        declared: set[tuple[str, str]] = set()
        for index, child in enumerate(value["children"]):
            child_field = f"{field}.children[{index}]"
            if not isinstance(child, dict):
                _fail_shape(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    "Hierarchy child node must be an object",
                    child,
                    field=child_field,
                    required=["definition", "children"],
                )
            if not isinstance(child, dict) or not isinstance(child.get("definition"), dict):
                fail(
                    "WORK_ITEM_HIERARCHY_INVALID",
                    "Hierarchy child definition must be an object",
                    field=f"{child_field}.definition",
                    actualType=type(child.get("definition")).__name__,
                )
            child_definition = child["definition"]
            child_id = child_definition.get("id")
            child_kind = child_definition.get("kind")
            if isinstance(child_id, str) and isinstance(child_kind, str):
                declared.add((child_id, child_kind))
        if declared != expected or len(value["children"]) != len(expected):
            fail(
                "WORK_ITEM_HIERARCHY_INCOMPLETE",
                f"{definition['id']} must materialize every planned child exactly once",
                expected=sorted(item[0] for item in expected),
                actual=sorted(item[0] for item in declared),
            )
        children = [
            normalize_node(
                child,
                definition,
                field=f"{field}.children[{index}]",
            )
            for index, child in enumerate(value["children"])
        ]
        children.sort(key=lambda item: item["definition"]["id"])
        return {"definition": definition, "children": children}

    root = normalize_node(hierarchy["root"], None, field="root")
    return {"schemaVersion": WORK_ITEM_SCHEMA_VERSION, "root": root}

def iter_hierarchy_nodes(hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """Return hierarchy nodes in deterministic pre-order."""
    result: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        result.append(node)
        for child in node["children"]:
            visit(child)

    visit(hierarchy["root"])
    return result

def hierarchy_fingerprint(hierarchy: dict[str, Any]) -> str:
    return fingerprint(hierarchy)

def _contract(definition: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "schemaVersion": definition["schemaVersion"],
        "id": definition["id"],
        "kind": definition["kind"],
        "gateLevel": definition["gateLevel"],
        "goal": definition["goal"],
        "scope": sorted(definition["scope"]),
        "requirements": sorted(definition["requirements"], key=lambda item: item["id"]),
        "acceptance": sorted(definition["acceptance"], key=lambda item: item["id"]),
        "testCommands": definition["testCommands"],
        "requiredSkills": definition["requiredSkills"],
    }
    for key in ("children", "decomposition", "execution", "developmentPlan"):
        if key in definition:
            normalized[key] = sorted(definition[key], key=lambda item: item["id"]) if key == "children" else definition[key]
    return normalized

def work_item_contract_fingerprint(definition: dict[str, Any]) -> str:
    return fingerprint(_contract(definition))

def work_item_child_contract_fingerprint(parent: dict[str, Any], child_id: str) -> str:
    child = next((item for item in parent.get("children", []) if item["id"] == child_id), None)
    if not child:
        fail("WORK_ITEM_PARENT_PLAN_MISMATCH", f"{child_id} is not declared by its parent baseline")
    stable_parent = _contract(parent)
    stable_parent.pop("children", None)
    stable_parent.pop("decomposition", None)
    child_plan = None
    if "developmentPlan" in stable_parent:
        plan = dict(stable_parent["developmentPlan"])
        child_plan = next((item for item in plan["childPlans"] if item["id"] == child_id), None)
        plan["sharedContracts"] = [item for item in plan["sharedContracts"] if child_id in item["consumerChildIds"]]
        plan.pop("childPlans", None)
        plan.pop("deliveryWaves", None)
        stable_parent["developmentPlan"] = plan
    value: dict[str, Any] = {"parent": stable_parent, "child": child}
    if child_plan:
        value["childDevelopmentPlan"] = child_plan
    return fingerprint(value)

def work_item_baseline_fingerprint(definition: dict[str, Any]) -> str:
    return fingerprint(definition)

def resolve_self_hosting_policy(*, project_name: str | None, explicit_dogfood: bool = False) -> dict[str, Any]:
    if project_name == "layered-delivery" and not explicit_dogfood:
        return {
            "route": "SELF_HOSTING_MAINTENANCE",
            "createsRuntimePackage": False,
            "reason": "HIERARCHICAL_GOVERNANCE_SELF_MAINTENANCE",
        }
    return {
        "route": "STANDARD_HIERARCHICAL_GOVERNANCE",
        "createsRuntimePackage": True,
        "reason": "EXPLICIT_DOGFOOD" if explicit_dogfood else "NOT_SELF_HOSTING",
    }
