from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import GatedLoopError, fail
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

ITEM_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
TRACE_ID = re.compile(r"^(?:R|A)-(?:00[1-9]|0[1-9]\d|[1-9]\d{2})$")
PLACEHOLDER = re.compile(r"\b(?:TBD|TODO|FIXME|PLACEHOLDER)\b|<[^>\n]+>|\{\{[^}\n]+\}\}|\?\?\?", re.I)
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
WILDCARD = re.compile(r"[?*{}\[\]]")


def _exact_keys(value: object, expected: list[str] | tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(expected)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or PLACEHOLDER.search(value) or CONTROL.search(value):
        fail("WORK_ITEM_VALUE_INVALID", f"{field} must be nonempty text without placeholders", field=field)
    return value.strip()


def safe_id(value: object, field: str = "id") -> str:
    reserved = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)")
    if not isinstance(value, str) or not ITEM_ID.fullmatch(value) or value.endswith(".") or reserved.match(value):
        fail("WORK_ITEM_ID_INVALID", f"{field} must be a safe lowercase identifier", field=field, value=value)
    return value


def _gate_level(value: object, kind: str) -> str:
    if value not in WORK_ITEM_GATE_LEVELS or (kind != "TASK" and value != "FULL"):
        fail(
            "WORK_ITEM_GATE_LEVEL_INVALID",
            "gateLevel must be LIGHT or FULL, and coordination work items must be FULL",
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
        or normalized == ".hierarchical-delivery-governance"
        or normalized.startswith(".hierarchical-delivery-governance/")
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
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        if (
            not _exact_keys(entry, expected)
            or not isinstance(entry_id, str)
            or not TRACE_ID.fullmatch(entry_id)
            or not entry_id.startswith(prefix + "-")
            or entry_id in seen
        ):
            fail("WORK_ITEM_TRACE_INVALID", f"{field}[{index}] has an invalid or duplicate ID", field=field, index=index)
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
    accepted: set[str] = set()
    for entry in acceptance:
        for requirement_id in entry["requirementIds"]:
            if requirement_id not in requirement_ids:
                fail("WORK_ITEM_TRACE_INVALID", f"{entry['id']} references unknown requirement {requirement_id}")
            accepted.add(requirement_id)
    if any(item["id"] not in accepted for item in requirements):
        fail("WORK_ITEM_TRACE_INVALID", "Every requirement must be covered by acceptance")


def _child_records(
    values: object,
    kind: str,
    requirements: list[dict[str, Any]],
    acceptance: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        fail("WORK_ITEM_CHILDREN_INVALID", f"{kind} must declare at least one child work item")
    expected_kind = "CAPABILITY" if kind == "DELIVERY" else "TASK"
    requirement_ids = {item["id"] for item in requirements}
    acceptance_ids = {item["id"] for item in acceptance}
    seen: set[str] = set()
    result = []
    for index, entry in enumerate(values):
        if not _exact_keys(entry, ["id", "kind", "title", "requirementIds", "acceptanceIds"]) or entry["kind"] != expected_kind:
            fail("WORK_ITEM_CHILDREN_INVALID", f"{kind} children must be {expected_kind} records", index=index)
        entry_id = safe_id(entry["id"], f"children[{index}].id")
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


def _execution_record(value: object, item_id: str) -> dict[str, Any]:
    if not _exact_keys(value, ["dependsOn", "inputs", "outputs"]):
        fail("WORK_ITEM_EXECUTION_INVALID", "Task execution must contain dependsOn, inputs, and outputs")
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


def _decomposition_record(value: object, kind: str, item_id: str, parent: dict[str, Any] | None) -> dict[str, Any]:
    expected = ["status", "dependsOn"] if kind == "CAPABILITY" else ["status"]
    if not _exact_keys(value, expected) or value["status"] not in {"OPEN", "SEALED"}:
        fail("WORK_ITEM_DECOMPOSITION_INVALID", "Coordination work items require decomposition status OPEN or SEALED")
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


def _linked_trace_ids(values: object, allowed: set[str], field: str, *, allow_empty: bool = False) -> list[str]:
    linked = sorted(_strings(values, field, allow_empty=allow_empty))
    if any(item not in allowed for item in linked):
        fail("WORK_ITEM_TRACE_INVALID", f"{field} references an unknown trace ID", field=field)
    return linked


def _development_test_plan(
    values: object,
    acceptance: list[dict[str, Any]],
    test_command_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.testPlan must be a nonempty array")
    acceptance_ids = {item["id"] for item in acceptance}
    covered: set[str] = set()
    result = []
    for index, entry in enumerate(values):
        field = f"developmentPlan.testPlan[{index}]"
        if not _exact_keys(entry, ["acceptanceIds", "approach", "commandIndexes"]):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} has missing or unknown fields", field=field)
        linked = _linked_trace_ids(entry["acceptanceIds"], acceptance_ids, f"{field}.acceptanceIds")
        covered.update(linked)
        indexes = entry["commandIndexes"]
        if (
            not isinstance(indexes, list)
            or not indexes
            or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 or item >= test_command_count for item in indexes)
            or len(set(indexes)) != len(indexes)
        ):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field}.commandIndexes must reference frozen test commands", field=field)
        result.append({
            "acceptanceIds": linked,
            "approach": _text(entry["approach"], f"{field}.approach"),
            "commandIndexes": sorted(indexes),
        })
    if any(item["id"] not in covered for item in acceptance):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Every acceptance criterion must be covered by developmentPlan.testPlan")
    return result


def _task_development_plan(value: object, normalized: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "purpose", "scenarios", "fileChanges", "interfaces", "logic", "dataAndTransactions",
        "compatibility", "testPlan", "reviewPoints",
    ]
    if not _exact_keys(value, keys):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan contains missing or unknown fields")
    requirement_ids = {item["id"] for item in normalized["requirements"]}
    covered: set[str] = set()
    if not isinstance(value["scenarios"], list) or not value["scenarios"]:
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.scenarios must be nonempty")
    scenarios = []
    for index, entry in enumerate(value["scenarios"]):
        field = f"developmentPlan.scenarios[{index}]"
        if not _exact_keys(entry, ["kind", "title", "description", "requirementIds"]) or entry["kind"] not in WORK_ITEM_CHANGE_SCENARIOS:
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} is invalid", field=field)
        linked = _linked_trace_ids(entry["requirementIds"], requirement_ids, f"{field}.requirementIds")
        covered.update(linked)
        scenarios.append({
            "kind": entry["kind"],
            "title": _text(entry["title"], f"{field}.title"),
            "description": _text(entry["description"], f"{field}.description"),
            "requirementIds": linked,
        })
    if any(item["id"] not in covered for item in normalized["requirements"]):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Every requirement must be covered by a development scenario")

    if not isinstance(value["fileChanges"], list) or not value["fileChanges"]:
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.fileChanges must be nonempty")
    seen_paths: set[str] = set()
    file_changes = []
    for index, entry in enumerate(value["fileChanges"]):
        field = f"developmentPlan.fileChanges[{index}]"
        if not _exact_keys(entry, ["path", "action", "purpose"]) or entry["action"] not in {"ADD", "MODIFY", "REMOVE"}:
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} is invalid", field=field)
        planned_path = normalize_scope_pattern(entry["path"])
        if WILDCARD.search(planned_path) or planned_path in seen_paths or not scope_contains(normalized["scope"], [planned_path]):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field}.path must be a unique exact path inside Task scope", field=field)
        seen_paths.add(planned_path)
        file_changes.append({
            "path": planned_path,
            "action": entry["action"],
            "purpose": _text(entry["purpose"], f"{field}.purpose"),
        })
    file_changes.sort(key=lambda item: item["path"])

    if not isinstance(value["interfaces"], list):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Task developmentPlan.interfaces must be an array")
    interfaces = []
    interface_keys = ["name", "kind", "action", "location", "currentContract", "targetContract", "requirementIds"]
    for index, entry in enumerate(value["interfaces"]):
        field = f"developmentPlan.interfaces[{index}]"
        if (
            not _exact_keys(entry, interface_keys)
            or entry["kind"] not in WORK_ITEM_INTERFACE_KINDS
            or entry["action"] not in {"ADD", "MODIFY", "REMOVE"}
        ):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} is invalid", field=field)
        interfaces.append({
            "name": _text(entry["name"], f"{field}.name"),
            "kind": entry["kind"],
            "action": entry["action"],
            "location": _text(entry["location"], f"{field}.location"),
            "currentContract": _text(entry["currentContract"], f"{field}.currentContract"),
            "targetContract": _text(entry["targetContract"], f"{field}.targetContract"),
            "requirementIds": _linked_trace_ids(entry["requirementIds"], requirement_ids, f"{field}.requirementIds"),
        })
    return {
        "purpose": _text(value["purpose"], "developmentPlan.purpose"),
        "scenarios": scenarios,
        "fileChanges": file_changes,
        "interfaces": interfaces,
        "logic": _strings(value["logic"], "developmentPlan.logic"),
        "dataAndTransactions": _strings(value["dataAndTransactions"], "developmentPlan.dataAndTransactions", allow_empty=True),
        "compatibility": _strings(value["compatibility"], "developmentPlan.compatibility"),
        "testPlan": _development_test_plan(value["testPlan"], normalized["acceptance"], len(normalized["testCommands"])),
        "reviewPoints": _strings(value["reviewPoints"], "developmentPlan.reviewPoints"),
    }


def _coordination_development_plan(value: object, normalized: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "purpose", "childPlans", "sharedContracts", "integrationFlow", "deliveryWaves",
        "testPlan", "reviewPoints",
    ]
    if not _exact_keys(value, keys):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Coordination developmentPlan contains missing or unknown fields")
    requirements = {item["id"] for item in normalized["requirements"]}
    acceptance = {item["id"] for item in normalized["acceptance"]}
    child_by_id = {item["id"]: item for item in normalized["children"]}
    if not isinstance(value["childPlans"], list) or len(value["childPlans"]) != len(normalized["children"]):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.childPlans must cover every direct child exactly once")
    seen: set[str] = set()
    child_plans = []
    for index, entry in enumerate(value["childPlans"]):
        field = f"developmentPlan.childPlans[{index}]"
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        child = child_by_id.get(entry_id)
        if (
            not _exact_keys(entry, ["id", "purpose", "deliverables", "requirementIds", "acceptanceIds", "dependsOn"])
            or child is None
            or entry_id in seen
        ):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} does not match a unique planned child", field=field)
        seen.add(entry_id)
        linked_requirements = _linked_trace_ids(entry["requirementIds"], requirements, f"{field}.requirementIds")
        linked_acceptance = _linked_trace_ids(entry["acceptanceIds"], acceptance, f"{field}.acceptanceIds")
        if linked_requirements != child["requirementIds"] or linked_acceptance != child["acceptanceIds"]:
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} trace mapping must match the child contract", field=field)
        if not isinstance(entry["dependsOn"], list):
            fail("WORK_ITEM_DEPENDENCY_INVALID", f"{field}.dependsOn must reference unique sibling children", field=field)
        depends_on = [safe_id(item, f"{field}.dependsOn[{dependency_index}]") for dependency_index, item in enumerate(entry["dependsOn"])]
        if entry_id in depends_on or len(set(depends_on)) != len(depends_on) or any(item not in child_by_id for item in depends_on):
            fail("WORK_ITEM_DEPENDENCY_INVALID", f"{field}.dependsOn must reference unique sibling children", field=field)
        child_plans.append({
            "id": entry_id,
            "purpose": _text(entry["purpose"], f"{field}.purpose"),
            "deliverables": _strings(entry["deliverables"], f"{field}.deliverables"),
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
        field = f"developmentPlan.sharedContracts[{index}]"
        if not _exact_keys(entry, contract_keys) or entry["kind"] not in WORK_ITEM_INTERFACE_KINDS:
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} is invalid", field=field)
        providers = sorted(_strings(entry["providerChildIds"], f"{field}.providerChildIds"))
        consumers = sorted(_strings(entry["consumerChildIds"], f"{field}.consumerChildIds"))
        if any(item not in child_by_id for item in providers + consumers):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} references an unknown child", field=field)
        shared_contracts.append({
            "name": _text(entry["name"], f"{field}.name"),
            "kind": entry["kind"],
            "description": _text(entry["description"], f"{field}.description"),
            "providerChildIds": providers,
            "consumerChildIds": consumers,
            "requirementIds": _linked_trace_ids(entry["requirementIds"], requirements, f"{field}.requirementIds"),
        })

    if not isinstance(value["deliveryWaves"], list) or not value["deliveryWaves"]:
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "developmentPlan.deliveryWaves must be nonempty")
    wave_by_child: dict[str, int] = {}
    wave_orders: set[int] = set()
    delivery_waves = []
    for index, entry in enumerate(value["deliveryWaves"]):
        field = f"developmentPlan.deliveryWaves[{index}]"
        order = entry.get("order") if isinstance(entry, dict) else None
        if (
            not _exact_keys(entry, ["order", "name", "childIds", "exitCriteria"])
            or not isinstance(order, int)
            or isinstance(order, bool)
            or order < 1
            or order in wave_orders
        ):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} is invalid", field=field)
        wave_orders.add(order)
        child_ids = sorted(safe_id(item, f"{field}.childIds") for item in _strings(entry["childIds"], f"{field}.childIds"))
        if any(item not in child_by_id or item in wave_by_child for item in child_ids):
            fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", f"{field} must contain unique planned children", field=field)
        for child_id in child_ids:
            wave_by_child[child_id] = order
        delivery_waves.append({
            "order": order,
            "name": _text(entry["name"], f"{field}.name"),
            "childIds": child_ids,
            "exitCriteria": _text(entry["exitCriteria"], f"{field}.exitCriteria"),
        })
    delivery_waves.sort(key=lambda item: item["order"])
    if len(wave_by_child) != len(child_by_id) or any(
        wave_by_child[dependency] >= wave_by_child[item["id"]]
        for item in child_plans
        for dependency in item["dependsOn"]
    ):
        fail("WORK_ITEM_DEVELOPMENT_PLAN_INVALID", "Delivery waves must cover every child and order dependencies before consumers")
    return {
        "purpose": _text(value["purpose"], "developmentPlan.purpose"),
        "childPlans": child_plans,
        "sharedContracts": shared_contracts,
        "integrationFlow": _strings(value["integrationFlow"], "developmentPlan.integrationFlow"),
        "deliveryWaves": delivery_waves,
        "testPlan": _development_test_plan(value["testPlan"], normalized["acceptance"], len(normalized["testCommands"])),
        "reviewPoints": _strings(value["reviewPoints"], "developmentPlan.reviewPoints"),
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
) -> dict[str, Any]:
    if not isinstance(definition, dict):
        fail("WORK_ITEM_DEFINITION_INVALID", "Work item definition must be an object")
    kind = definition.get("kind")
    if kind not in WORK_ITEM_KINDS:
        fail("WORK_ITEM_KIND_INVALID", "Work item kind must be DELIVERY, CAPABILITY, or TASK")
    if definition.get("schemaVersion") != WORK_ITEM_SCHEMA_VERSION:
        fail("WORK_ITEM_SCHEMA_INVALID", f"Work item schemaVersion must be {WORK_ITEM_SCHEMA_VERSION}")
    if kind == "TASK" and "children" in definition:
        fail("WORK_ITEM_TASK_NOT_LEAF", "Task is an executable leaf and cannot contain children")
    if kind != "TASK" and "execution" in definition:
        fail("WORK_ITEM_EXECUTION_INVALID", "Only Task work items can contain execution metadata")
    common = [
        "schemaVersion", "id", "kind", "gateLevel", "title", "goal", "scope", "nonGoals",
        "requirements", "acceptance", "testCommands", "risks", "decisions",
    ]
    plan_keys = ["developmentPlan"]
    expected = (
        common + plan_keys + ["decomposition", "children"]
        if kind == "DELIVERY"
        else common + plan_keys + ["parentId"] + (["execution"] if kind == "TASK" else ["decomposition", "children"])
    )
    if not _exact_keys(definition, expected):
        fail(
            "WORK_ITEM_DEFINITION_INVALID",
            "Work item definition contains missing or unknown fields",
            expectedKeys=sorted(expected),
            actualKeys=sorted(definition),
        )
    normalized: dict[str, Any] = {
        "schemaVersion": WORK_ITEM_SCHEMA_VERSION,
        "id": safe_id(definition["id"]),
        "kind": kind,
        "gateLevel": _gate_level(definition["gateLevel"], kind),
        "authorityKind": WORK_ITEM_AUTHORITIES[kind],
        "title": _text(definition["title"], "title"),
        "goal": _text(definition["goal"], "goal"),
        "scope": _normalize_scope(definition["scope"]),
        "nonGoals": _strings(definition["nonGoals"], "nonGoals"),
        "requirements": _trace_records(definition["requirements"], "R", "requirements"),
        "acceptance": _trace_records(definition["acceptance"], "A", "acceptance"),
        "testCommands": _test_commands(definition["testCommands"]),
        "risks": _strings(definition["risks"], "risks"),
        "decisions": _strings(definition["decisions"], "decisions"),
    }
    _validate_trace(normalized["requirements"], normalized["acceptance"])
    if kind == "TASK":
        normalized["execution"] = _execution_record(definition["execution"], normalized["id"])
    else:
        normalized["decomposition"] = _decomposition_record(definition["decomposition"], kind, normalized["id"], parent)
        normalized["children"] = _child_records(definition["children"], kind, normalized["requirements"], normalized["acceptance"])
    if "developmentPlan" in definition:
        normalized["developmentPlan"] = (
            _task_development_plan(definition["developmentPlan"], normalized)
            if kind == "TASK"
            else _coordination_development_plan(definition["developmentPlan"], normalized)
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
        fail(
            "WORK_ITEM_HIERARCHY_INVALID",
            "Hierarchy definition must contain only schemaVersion and root",
        )
    if hierarchy["schemaVersion"] != WORK_ITEM_SCHEMA_VERSION:
        fail(
            "WORK_ITEM_SCHEMA_INVALID",
            f"Hierarchy schemaVersion must be {WORK_ITEM_SCHEMA_VERSION}",
        )

    seen: set[str] = set()

    def normalize_node(value: object, parent: dict[str, Any] | None) -> dict[str, Any]:
        if not _exact_keys(value, ["definition", "children"]):
            fail(
                "WORK_ITEM_HIERARCHY_INVALID",
                "Every hierarchy node must contain only definition and children",
            )
        if not isinstance(value["children"], list):
            fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy node children must be an array")
        definition = validate_work_item_definition(value["definition"], parent=parent)
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
        for child in value["children"]:
            if not isinstance(child, dict) or not isinstance(child.get("definition"), dict):
                fail("WORK_ITEM_HIERARCHY_INVALID", "Hierarchy child definition is invalid")
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
        children = [normalize_node(child, definition) for child in value["children"]]
        children.sort(key=lambda item: item["definition"]["id"])
        return {"definition": definition, "children": children}

    root = normalize_node(hierarchy["root"], None)
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


def raw_definition(definition: dict[str, Any]) -> dict[str, Any]:
    omitted = {"authorityKind", "parentContractFingerprint"}
    if definition.get("kind") == "DELIVERY":
        omitted.add("parentId")
    return {key: value for key, value in definition.items() if key not in omitted}


def _list(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def render_work_item_baseline(definition: dict[str, Any]) -> str:
    lines = [
        "# Work Item Baseline",
        "",
        f"Work Item: {definition['id']}",
        f"Kind: {definition['kind']}",
        f"Gate Level: {definition['gateLevel']}",
        f"Authority: {definition['authorityKind']}",
        f"Parent: {definition['parentId'] or 'none'}",
        f"Parent Contract: {definition['parentContractFingerprint'] or 'none'}",
        "",
        "## Goal",
        definition["goal"],
        "",
        "## Scope",
        _list(definition["scope"]),
        "",
        "## Non-Goals",
        _list(definition["nonGoals"]),
        "",
        "## Requirements",
    ]
    for requirement in definition["requirements"]:
        lines.extend([f"### {requirement['id']}", requirement["text"], ""])
    lines.append("## Acceptance")
    for acceptance in definition["acceptance"]:
        lines.extend([
            f"### {acceptance['id']} [{','.join(acceptance['requirementIds'])}]",
            acceptance["expectedResult"],
            "",
        ])
    if "children" in definition:
        lines.extend([
            "## Decomposition",
            f"- Status: {definition['decomposition']['status']}",
        ])
        if definition["kind"] == "CAPABILITY":
            lines.append(f"- Capability dependencies: {', '.join(definition['decomposition']['dependsOn']) or 'none'}")
        lines.extend(["", "## Children"])
        for child in definition["children"]:
            lines.append(
                f"- {child['id']} [{child['kind']}] [{','.join(child['requirementIds'])}] "
                f"[{','.join(child['acceptanceIds'])}] {child['title']}"
            )
    else:
        lines.extend([
            "## Execution",
            f"- Depends on: {', '.join(definition['execution']['dependsOn']) or 'none'}",
            f"- Inputs: {'; '.join(definition['execution']['inputs']) or 'none'}",
            f"- Outputs: {'; '.join(definition['execution']['outputs'])}",
        ])
    import json

    lines.extend(["", "## Test Commands"])
    lines.extend(f"- {json.dumps(argv, ensure_ascii=False, separators=(',', ':'))}" for argv in definition["testCommands"])
    lines.extend([
        "",
        "## Development Plan Contract",
        definition["developmentPlan"]["purpose"],
        "",
        "- Full human-readable plan: [development-plan.md](development-plan.md)",
        "- Structured plan: [development-plan.json](development-plan.json)",
        "",
        "## Risks",
        _list(definition["risks"]),
        "",
        "## Decisions",
        _list(definition["decisions"]),
        "",
    ])
    return "\n".join(lines)


def _review_status_text(state: dict[str, Any]) -> str:
    review = state.get("review", {})
    if review.get("status") == "APPROVED":
        return f"已由人工确认（{review['reviewedBy']}，{review['reviewedAt']}）"
    return "等待人工评审；尚未冻结，禁止开始开发"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_development_plan(definition: dict[str, Any], state: dict[str, Any]) -> str:
    plan = definition["developmentPlan"]
    lines = [
        f"# 开发方案：{definition['title']}",
        "",
        f"- 工作项：{definition['id']}",
        f"- 层级：{definition['kind']}",
        f"- 门禁等级：{definition['gateLevel']}",
        f"- Baseline 指纹：{state['baselineFingerprint']}",
        f"- 评审状态：{_review_status_text(state)}",
        f"- 开发目的：{plan['purpose']}",
        "",
        "## 需求与验收边界",
        "",
        "| 需求 | 内容 |",
        "| --- | --- |",
    ]
    lines.extend(f"| {item['id']} | {_markdown_cell(item['text'])} |" for item in definition["requirements"])
    lines.extend([
        "",
        "| 验收 | 覆盖需求 | 预期结果 |",
        "| --- | --- | --- |",
    ])
    lines.extend(
        f"| {item['id']} | {', '.join(item['requirementIds'])} | {_markdown_cell(item['expectedResult'])} |"
        for item in definition["acceptance"]
    )
    lines.append("")

    if definition["kind"] == "TASK":
        lines.extend([
            "## 变更场景",
            "",
            "| 场景 | 标题 | 开发内容 | 覆盖需求 |",
            "| --- | --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['kind']} | {_markdown_cell(item['title'])} | {_markdown_cell(item['description'])} | {', '.join(item['requirementIds'])} |"
            for item in plan["scenarios"]
        )
        lines.extend([
            "",
            "## 文件改动",
            "",
            "| 动作 | 文件 | 目的 |",
            "| --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['action']} | `{item['path']}` | {_markdown_cell(item['purpose'])} |"
            for item in plan["fileChanges"]
        )
        lines.extend(["", "## 接口与功能契约", ""])
        if not plan["interfaces"]:
            lines.append("- 本 Task 不新增、修改或删除外部/内部接口。")
        else:
            lines.extend([
                "| 动作 | 类型 | 名称与位置 | 当前契约 | 目标契约 | 覆盖需求 |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            lines.extend(
                f"| {item['action']} | {item['kind']} | {_markdown_cell(item['name'])}<br>"
                f"{_markdown_cell(item['location'])} | {_markdown_cell(item['currentContract'])} | "
                f"{_markdown_cell(item['targetContract'])} | {', '.join(item['requirementIds'])} |"
                for item in plan["interfaces"]
            )
        lines.extend(["", "## 实现逻辑", ""])
        lines.extend(f"- {item}" for item in plan["logic"])
        lines.extend(["", "## 数据与事务", ""])
        lines.extend(
            [f"- {item}" for item in plan["dataAndTransactions"]]
            or ["- 不涉及数据模型、持久化或事务边界变更。"]
        )
        lines.extend(["", "## 兼容性", ""])
        lines.extend(f"- {item}" for item in plan["compatibility"])
    else:
        child_label = "Capability" if definition["kind"] == "DELIVERY" else "Task"
        lines.extend([
            f"## {child_label} 开发内容",
            "",
            f"| {child_label} | 开发目的 | 交付内容 | 依赖 | R/A |",
            "| --- | --- | --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['id']} | {_markdown_cell(item['purpose'])} | "
            f"{_markdown_cell('；'.join(item['deliverables']))} | {', '.join(item['dependsOn']) or '无'} | "
            f"{', '.join(item['requirementIds'])} / {', '.join(item['acceptanceIds'])} |"
            for item in plan["childPlans"]
        )
        lines.extend(["", f"## 跨 {child_label} 接口与共享契约", ""])
        if not plan["sharedContracts"]:
            lines.append(f"- 无跨 {child_label} 共享接口；子级仅通过冻结输出和聚合门禁组合。")
        else:
            lines.extend([
                "| 类型 | 契约 | 提供方 | 消费方 | 说明 | 覆盖需求 |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            lines.extend(
                f"| {item['kind']} | {_markdown_cell(item['name'])} | {', '.join(item['providerChildIds'])} | "
                f"{', '.join(item['consumerChildIds'])} | {_markdown_cell(item['description'])} | "
                f"{', '.join(item['requirementIds'])} |"
                for item in plan["sharedContracts"]
            )
        lines.extend(["", "## 集成流程", ""])
        lines.extend(f"- {item}" for item in plan["integrationFlow"])
        lines.extend([
            "",
            "## 开发与集成波次",
            "",
            "| 波次 | 名称 | 子级 | 退出条件 |",
            "| --- | --- | --- | --- |",
        ])
        lines.extend(
            f"| {item['order']} | {_markdown_cell(item['name'])} | {', '.join(item['childIds'])} | "
            f"{_markdown_cell(item['exitCriteria'])} |"
            for item in plan["deliveryWaves"]
        )
    lines.extend([
        "",
        "## 测试与验收映射",
        "",
        "| 验收项 | 验证方法 | 冻结命令序号 |",
        "| --- | --- | --- |",
    ])
    lines.extend(
        f"| {', '.join(item['acceptanceIds'])} | {_markdown_cell(item['approach'])} | "
        f"{', '.join(str(index) for index in item['commandIndexes'])} |"
        for item in plan["testPlan"]
    )
    lines.extend(["", "## 人工评审重点", ""])
    lines.extend(f"- {item}" for item in plan["reviewPoints"])
    lines.extend([
        "",
        "## 冻结说明",
        "",
        "- 请先评审本文件中的开发目的、内容、文件、接口/共享契约、依赖波次和测试映射。",
        "- 如需修改，先修改 definition 并重新 prepare；不要冻结错误版本。",
        "- 人工评审当前开发方案并选择 active/manual 后一次确认，无需复制或复述指纹。",
        "- Agent 必须使用展示本方案时保存的当前指纹调用冻结；方案已变化时控制器会拒绝旧确认。",
        "",
    ])
    return "\n".join(lines)


def render_hierarchy_plan(
    hierarchy: dict[str, Any],
    states: dict[str, dict[str, Any]],
    hierarchy_state: dict[str, Any],
) -> str:
    """Render the single human plan for one complete requirement tree."""
    kind_text = {"DELIVERY": "交付", "CAPABILITY": "能力", "TASK": "任务"}
    review = hierarchy_state["review"]
    review_text = (
        f"已由人工确认（{review['reviewedBy']}，{review['reviewedAt']}）"
        if review["status"] == "APPROVED"
        else "等待人工评审；尚未冻结，禁止开始开发"
    )
    lines = [
        "# 需求层级开发方案",
        "",
        f"- 根工作项：{hierarchy_state['rootId']}",
        f"- 层级指纹：{hierarchy_state['hierarchyFingerprint']}",
        f"- 方案状态：{review_text}",
        "- 确认方式：人工评审本文件、选择 active/manual 后一次确认，无需复制或复述指纹。",
        "",
        "## 层级结构",
        "",
    ]

    def append_tree(node: dict[str, Any], prefix: str, connector: str) -> None:
        definition = node["definition"]
        lines.append(
            f"{prefix}{connector}{kind_text[definition['kind']]} `{definition['id']}`：{definition['title']}"
        )
        children = node["children"]
        for index, child in enumerate(children):
            last = index == len(children) - 1
            child_prefix = prefix + ("   " if connector in {"", "└─ "} else "│  ")
            append_tree(child, child_prefix, "└─ " if last else "├─ ")

    append_tree(hierarchy["root"], "", "")

    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        item_plan = render_development_plan(definition, states[definition["id"]]).splitlines()
        lines.extend([
            "",
            f"## {kind_text[definition['kind']]}：{definition['id']} — {definition['title']}",
            "",
        ])
        for line in item_plan[1:]:
            lines.append("#" + line if line.startswith("## ") else line)

    lines.extend([
        "",
        "## 统一冻结说明",
        "",
        "- 本文件一次展示并绑定整棵当前需求树的所有 baseline、接口、文件、依赖波次和测试映射。",
        "- 需要修改时重新准备整棵树；旧层级指纹会自动失效。",
        "- 人工选择根级开发方式并确认本文件后，Agent 使用已保存的层级指纹一次记录方式并冻结全部节点。",
        "- 冻结后不得静默新增或修改节点；需求边界变化时停止执行并重新规划完整需求树。",
        "",
    ])
    return "\n".join(lines)


def resolve_self_hosting_policy(*, project_name: str | None, explicit_dogfood: bool = False) -> dict[str, Any]:
    if project_name == "hierarchical-delivery-governance" and not explicit_dogfood:
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
