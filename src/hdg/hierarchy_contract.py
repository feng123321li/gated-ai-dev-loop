from __future__ import annotations

from typing import Any

from .constants import SCHEMA_VERSION
from .errors import fail
from .model_core import (
    ITEM_ID,
    SKILL_NAME,
    TRACE_ID,
    WORK_ITEM_CHANGE_SCENARIOS,
    WORK_ITEM_GATE_LEVELS,
    WORK_ITEM_INTERFACE_KINDS,
    WORK_ITEM_SKILL_STAGES,
)


HIERARCHY_CONTRACT_INPUT_MODES = (
    "COMPACT_TASK",
    "FULL_HIERARCHY",
)


def _text(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "description": description,
    }


def _string_array(
    description: str,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "array",
        "description": description,
        "items": _text("Nonempty text without placeholders."),
        "uniqueItems": True,
    }
    if not allow_empty:
        schema["minItems"] = 1
    return schema


def _object(
    properties: dict[str, dict[str, Any]],
    *,
    required: list[str] | tuple[str, ...] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }
    if description is not None:
        schema["description"] = description
    return schema


def _array(
    items: dict[str, Any],
    *,
    min_items: int | None = None,
    max_items: int | None = None,
    unique_items: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "array",
        "items": items,
    }
    if min_items is not None:
        schema["minItems"] = min_items
    if max_items is not None:
        schema["maxItems"] = max_items
    if unique_items:
        schema["uniqueItems"] = True
    return schema


def _identifier(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": ITEM_ID.pattern,
        "description": description,
    }


def _trace_identifier(prefix: str) -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": TRACE_ID.pattern,
        "description": f"Unique {prefix}-### trace identifier.",
    }


def _requirement_schema() -> dict[str, Any]:
    return _object({
        "id": _trace_identifier("R"),
        "text": _text("One independently observable requirement."),
    })


def _acceptance_schema() -> dict[str, Any]:
    return _object({
        "id": _trace_identifier("A"),
        "requirementIds": _array(
            _trace_identifier("R"),
            min_items=1,
            unique_items=True,
        ),
        "expectedResult": _text(
            "One independently observable acceptance result.",
        ),
    })


def _required_skill_schema() -> dict[str, Any]:
    return _object({
        "name": {
            "type": "string",
            "pattern": SKILL_NAME.pattern,
            "description": "Exact registered Skill catalog name.",
        },
        "stages": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(WORK_ITEM_SKILL_STAGES),
            },
            "minItems": 1,
            "uniqueItems": True,
        },
        "purpose": _text("Why this Skill is required."),
    })


def _test_commands_schema() -> dict[str, Any]:
    return _array(
        _array(
            _text("One argv token."),
            min_items=1,
        ),
        min_items=1,
    )


def _test_plan_schema() -> dict[str, Any]:
    return _array(
        _object({
            "acceptanceIds": _array(
                _trace_identifier("A"),
                min_items=1,
                unique_items=True,
            ),
            "approach": _text("How the linked acceptance is verified."),
            "commandIndexes": _array(
                {"type": "integer", "minimum": 0},
                min_items=1,
                unique_items=True,
            ),
        }),
        min_items=1,
    )


def _task_development_plan_schema() -> dict[str, Any]:
    required = [
        "purpose",
        "scenarios",
        "fileChanges",
        "interfaces",
        "logic",
        "dataAndTransactions",
        "compatibility",
        "testPlan",
        "reviewPoints",
    ]
    return _object(
        {
            "purpose": _text("Purpose of this executable Task plan."),
            "scenarios": _array(
                _object({
                    "kind": {
                        "type": "string",
                        "enum": list(WORK_ITEM_CHANGE_SCENARIOS),
                    },
                    "title": _text("Scenario title."),
                    "description": _text("Exact behavior change."),
                    "requirementIds": _array(
                        _trace_identifier("R"),
                        min_items=1,
                        unique_items=True,
                    ),
                }),
                min_items=1,
            ),
            "fileChanges": _array(
                _object({
                    "path": _text(
                        "Exact safe relative file path inside Task scope.",
                    ),
                    "action": {
                        "type": "string",
                        "enum": ["ADD", "MODIFY", "REMOVE"],
                    },
                    "purpose": _text("Why this exact file changes."),
                }),
            ),
            "generatedFileRoots": _array(
                _object({
                    "path": _text(
                        "Unique non-overlapping /** subtree for ADD-only files.",
                    ),
                    "purpose": _text("Why generated files are added."),
                }),
            ),
            "interfaces": _array(
                _object({
                    "name": _text("Interface name."),
                    "kind": {
                        "type": "string",
                        "enum": list(WORK_ITEM_INTERFACE_KINDS),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["ADD", "MODIFY", "REMOVE"],
                    },
                    "location": _text("Interface owner location."),
                    "currentContract": _text("Current observable contract."),
                    "targetContract": _text("Target observable contract."),
                    "requirementIds": _array(
                        _trace_identifier("R"),
                        min_items=1,
                        unique_items=True,
                    ),
                }),
            ),
            "logic": _string_array("Implementation logic."),
            "dataAndTransactions": _string_array(
                "Data and transaction behavior; may be empty.",
                allow_empty=True,
            ),
            "compatibility": _string_array(
                "Compatibility constraints.",
            ),
            "testPlan": _test_plan_schema(),
            "reviewPoints": _string_array("Human review points."),
        },
        required=required,
    )


def _coordination_development_plan_schema() -> dict[str, Any]:
    return _object({
        "purpose": _text("Purpose of this coordination plan."),
        "childPlans": _array(
            _object({
                "id": _identifier("Exact direct-child ID."),
                "purpose": _text("Child responsibility."),
                "deliverables": _string_array("Child deliverables."),
                "requirementIds": _array(
                    _trace_identifier("R"),
                    min_items=1,
                    unique_items=True,
                ),
                "acceptanceIds": _array(
                    _trace_identifier("A"),
                    min_items=1,
                    unique_items=True,
                ),
                "dependsOn": _array(
                    _identifier("Sibling child dependency ID."),
                    unique_items=True,
                ),
            }),
            min_items=1,
        ),
        "sharedContracts": _array(
            _object({
                "name": _text("Shared contract name."),
                "kind": {
                    "type": "string",
                    "enum": list(WORK_ITEM_INTERFACE_KINDS),
                },
                "description": _text("Shared contract behavior."),
                "providerChildIds": _array(
                    _identifier("Provider child ID."),
                    min_items=1,
                    unique_items=True,
                ),
                "consumerChildIds": _array(
                    _identifier("Consumer child ID."),
                    min_items=1,
                    unique_items=True,
                ),
                "requirementIds": _array(
                    _trace_identifier("R"),
                    min_items=1,
                    unique_items=True,
                ),
            }),
        ),
        "integrationFlow": _string_array("Child integration flow."),
        "deliveryWaves": _array(
            _object({
                "order": {"type": "integer", "minimum": 1},
                "name": _text("Delivery wave name."),
                "childIds": _array(
                    _identifier("Child ID in this wave."),
                    min_items=1,
                    unique_items=True,
                ),
                "exitCriteria": _text("Observable wave exit criteria."),
            }),
            min_items=1,
        ),
        "testPlan": _test_plan_schema(),
        "reviewPoints": _string_array("Human review points."),
    })


def _child_record_schema(kind: str) -> dict[str, Any]:
    return _object({
        "id": _identifier("Exact direct-child ID."),
        "kind": {"const": kind},
        "title": _text("Child title."),
        "requirementIds": _array(
            _trace_identifier("R"),
            min_items=1,
            unique_items=True,
        ),
        "acceptanceIds": _array(
            _trace_identifier("A"),
            min_items=1,
            unique_items=True,
        ),
    })


def _common_definition_properties(
    kind: str,
) -> dict[str, dict[str, Any]]:
    gate_levels = (
        list(WORK_ITEM_GATE_LEVELS)
        if kind == "TASK"
        else ["FULL"]
    )
    return {
        "schemaVersion": {"const": SCHEMA_VERSION},
        "id": _identifier("Unique safe work-item ID."),
        "kind": {"const": kind},
        "gateLevel": {"type": "string", "enum": gate_levels},
        "title": _text("Reviewable work-item title."),
        "goal": _text("One observable work-item goal."),
        "scope": _array(
            _text("Safe relative path or terminal /** subtree."),
            min_items=1,
            unique_items=True,
        ),
        "nonGoals": _string_array("Explicitly excluded outcomes."),
        "requirements": _array(
            _requirement_schema(),
            min_items=1,
        ),
        "acceptance": _array(
            _acceptance_schema(),
            min_items=1,
        ),
        "testCommands": _test_commands_schema(),
        "requiredSkills": _array(
            _required_skill_schema(),
        ),
        "risks": _string_array("Known delivery risks."),
        "decisions": _string_array("Frozen planning decisions."),
    }


def _definition_schema(
    kind: str,
    *,
    root: bool,
) -> dict[str, Any]:
    properties = _common_definition_properties(kind)
    if kind == "TASK":
        properties.update({
            "parentId": (
                {"const": None}
                if root
                else _identifier("Parent Capability ID.")
            ),
            "execution": _object({
                "dependsOn": _array(
                    _identifier("Sibling Task dependency ID."),
                    unique_items=True,
                ),
                "inputs": _string_array(
                    "Task inputs; may be empty.",
                    allow_empty=True,
                ),
                "outputs": _string_array("Task outputs."),
            }),
            "developmentPlan": _task_development_plan_schema(),
        })
    elif kind == "CAPABILITY":
        properties.update({
            "parentId": (
                {"const": None}
                if root
                else _identifier("Parent Delivery ID.")
            ),
            "decomposition": _object({
                "status": {
                    "type": "string",
                    "enum": ["OPEN", "SEALED"],
                },
                "dependsOn": _array(
                    _identifier("Sibling Capability dependency ID."),
                    unique_items=True,
                ),
            }),
            "children": _array(
                _child_record_schema("TASK"),
                min_items=1,
            ),
            "developmentPlan": (
                _coordination_development_plan_schema()
            ),
        })
    else:
        properties.update({
            "decomposition": _object({
                "status": {
                    "type": "string",
                    "enum": ["OPEN", "SEALED"],
                },
            }),
            "children": _array(
                _child_record_schema("CAPABILITY"),
                min_items=1,
            ),
            "developmentPlan": (
                _coordination_development_plan_schema()
            ),
        })
    required = [
        key for key in properties
        if key != "requiredSkills"
    ]
    return _object(
        properties,
        required=required,
        description=f"Complete schema-v3 {kind} definition.",
    )


def _node_schema(kind: str, *, root: bool) -> dict[str, Any]:
    if kind == "TASK":
        children = _array({}, max_items=0)
    elif kind == "CAPABILITY":
        children = _array(
            _node_schema("TASK", root=False),
            min_items=1,
        )
    else:
        children = _array(
            _node_schema("CAPABILITY", root=False),
            min_items=1,
        )
    return _object({
        "definition": _definition_schema(kind, root=root),
        "children": children,
    })


def _full_hierarchy_schema(root_kind: str) -> dict[str, Any]:
    return _object({
        "schemaVersion": {"const": SCHEMA_VERSION},
        "root": _node_schema(root_kind, root=True),
    })


def _compact_task_schema() -> dict[str, Any]:
    required = [
        "id",
        "gateLevel",
        "title",
        "goal",
        "scope",
        "requirements",
        "acceptance",
        "testCommands",
        "fileChanges",
        "logic",
    ]
    properties = {
        "id": _identifier("Unique root Task ID."),
        "gateLevel": {
            "type": "string",
            "enum": list(WORK_ITEM_GATE_LEVELS),
        },
        "title": _text("Reviewable Task title."),
        "goal": _text("One observable Task goal."),
        "scope": _array(
            _text("Safe relative path or terminal /** subtree."),
            min_items=1,
            unique_items=True,
        ),
        "requirements": _array(_requirement_schema(), min_items=1),
        "acceptance": _array(_acceptance_schema(), min_items=1),
        "testCommands": _test_commands_schema(),
        "fileChanges": (
            _task_development_plan_schema()["properties"]["fileChanges"]
        ),
        "logic": _string_array("Implementation logic."),
        "nonGoals": _string_array("Explicitly excluded outcomes."),
        "requiredSkills": _array(_required_skill_schema()),
        "risks": _string_array("Known delivery risks."),
        "decisions": _string_array("Frozen planning decisions."),
        "scenarios": (
            _task_development_plan_schema()["properties"]["scenarios"]
        ),
        "interfaces": (
            _task_development_plan_schema()["properties"]["interfaces"]
        ),
        "dataAndTransactions": _string_array(
            "Data and transaction behavior; may be empty.",
            allow_empty=True,
        ),
        "compatibility": _string_array("Compatibility constraints."),
        "reviewPoints": _string_array("Human review points."),
        "inputs": _string_array("Task inputs; may be empty.", allow_empty=True),
        "outputs": _string_array("Task outputs."),
        "generatedFileRoots": (
            _task_development_plan_schema()["properties"][
                "generatedFileRoots"
            ]
        ),
    }
    return _object({
        "schemaVersion": {"const": SCHEMA_VERSION},
        "compactTask": _object(
            properties,
            required=required,
        ),
    })


def _example_task(
    *,
    item_id: str,
    parent_id: str | None,
    gate_level: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": item_id,
        "kind": "TASK",
        "gateLevel": gate_level,
        "parentId": parent_id,
        "title": "Implement verified example behavior",
        "goal": "Deliver one observable example result.",
        "scope": ["src/example.py", "tests/test_example.py"],
        "nonGoals": ["Do not modify unrelated modules."],
        "requirements": [{
            "id": "R-001",
            "text": "The example behavior must be implemented.",
        }],
        "acceptance": [{
            "id": "A-001",
            "requirementIds": ["R-001"],
            "expectedResult": "The focused example test passes.",
        }],
        "execution": {
            "dependsOn": [],
            "inputs": [],
            "outputs": ["Verified example behavior"],
        },
        "testCommands": [[
            "python",
            "-m",
            "unittest",
            "tests.test_example",
        ]],
        "requiredSkills": [],
        "risks": ["The example contract must remain deterministic."],
        "decisions": ["Use the smallest independently verifiable change."],
        "developmentPlan": {
            "purpose": "Implement and verify the example behavior.",
            "scenarios": [{
                "kind": "OTHER",
                "title": "Implement example behavior",
                "description": "Add the scoped behavior and focused test.",
                "requirementIds": ["R-001"],
            }],
            "fileChanges": [
                {
                    "path": "src/example.py",
                    "action": "ADD",
                    "purpose": "Implement the example behavior.",
                },
                {
                    "path": "tests/test_example.py",
                    "action": "ADD",
                    "purpose": "Verify the example behavior.",
                },
            ],
            "interfaces": [],
            "logic": ["Implement the frozen example behavior."],
            "dataAndTransactions": [],
            "compatibility": ["Preserve behavior outside the frozen scope."],
            "testPlan": [{
                "acceptanceIds": ["A-001"],
                "approach": "Run the focused unittest command.",
                "commandIndexes": [0],
            }],
            "reviewPoints": ["Confirm scope and acceptance evidence."],
        },
    }


def _coordination_definition(
    *,
    kind: str,
    item_id: str,
    parent_id: str | None,
    child_id: str,
    child_kind: str,
) -> dict[str, Any]:
    definition = {
        "schemaVersion": SCHEMA_VERSION,
        "id": item_id,
        "kind": kind,
        "gateLevel": "FULL",
        "title": f"Coordinate example {kind.lower()}",
        "goal": f"Coordinate one verified {child_kind.lower()}.",
        "scope": ["src/**", "tests/**"],
        "nonGoals": ["Do not modify unrelated modules."],
        "requirements": [{
            "id": "R-001",
            "text": "The coordinated example must be verified.",
        }],
        "acceptance": [{
            "id": "A-001",
            "requirementIds": ["R-001"],
            "expectedResult": "The child and aggregate tests pass.",
        }],
        "decomposition": (
            {"status": "SEALED"}
            if kind == "DELIVERY"
            else {"status": "SEALED", "dependsOn": []}
        ),
        "children": [{
            "id": child_id,
            "kind": child_kind,
            "title": f"Example {child_kind.lower()}",
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
        }],
        "testCommands": [[
            "python",
            "-m",
            "unittest",
            "discover",
        ]],
        "requiredSkills": [],
        "risks": ["Child and aggregate contracts must remain aligned."],
        "decisions": [f"Use a {kind} to coordinate the child contract."],
        "developmentPlan": {
            "purpose": f"Coordinate the example {child_kind.lower()}.",
            "childPlans": [{
                "id": child_id,
                "purpose": f"Deliver the example {child_kind.lower()}.",
                "deliverables": ["Verified example result."],
                "requirementIds": ["R-001"],
                "acceptanceIds": ["A-001"],
                "dependsOn": [],
            }],
            "sharedContracts": [],
            "integrationFlow": ["Verify the child before the aggregate gate."],
            "deliveryWaves": [{
                "order": 1,
                "name": "Example delivery",
                "childIds": [child_id],
                "exitCriteria": "The child is verified.",
            }],
            "testPlan": [{
                "acceptanceIds": ["A-001"],
                "approach": "Run the aggregate unittest command.",
                "commandIndexes": [0],
            }],
            "reviewPoints": ["Confirm child responsibility and traceability."],
        },
    }
    if kind == "CAPABILITY":
        definition["parentId"] = parent_id
    return definition


def _full_example(root_kind: str) -> dict[str, Any]:
    task = _example_task(
        item_id="t-example",
        parent_id=None if root_kind == "TASK" else "c-example",
        gate_level="FULL",
    )
    task_node = {"definition": task, "children": []}
    if root_kind == "TASK":
        root = task_node
    else:
        capability = _coordination_definition(
            kind="CAPABILITY",
            item_id="c-example",
            parent_id=None if root_kind == "CAPABILITY" else "d-example",
            child_id="t-example",
            child_kind="TASK",
        )
        capability_node = {
            "definition": capability,
            "children": [task_node],
        }
        if root_kind == "CAPABILITY":
            root = capability_node
        else:
            delivery = _coordination_definition(
                kind="DELIVERY",
                item_id="d-example",
                parent_id=None,
                child_id="c-example",
                child_kind="CAPABILITY",
            )
            root = {
                "definition": delivery,
                "children": [capability_node],
            }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "root": root,
    }


def _compact_example() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "compactTask": {
            "id": "t-example",
            "gateLevel": "FULL",
            "title": "Implement verified example behavior",
            "goal": "Deliver one observable example result.",
            "scope": ["src/example.py", "tests/test_example.py"],
            "requirements": [{
                "id": "R-001",
                "text": "The example behavior must be implemented.",
            }],
            "acceptance": [{
                "id": "A-001",
                "requirementIds": ["R-001"],
                "expectedResult": "The focused example test passes.",
            }],
            "testCommands": [[
                "python",
                "-m",
                "unittest",
                "tests.test_example",
            ]],
            "fileChanges": [
                {
                    "path": "src/example.py",
                    "action": "ADD",
                    "purpose": "Implement the example behavior.",
                },
                {
                    "path": "tests/test_example.py",
                    "action": "ADD",
                    "purpose": "Verify the example behavior.",
                },
            ],
            "logic": ["Implement the frozen example behavior."],
            "requiredSkills": [],
        },
    }


def hierarchy_contract(
    *,
    root_kind: str,
    input_mode: str,
) -> dict[str, Any]:
    """Return the exact on-demand v3 planning contract and valid example."""

    if root_kind not in {"TASK", "CAPABILITY", "DELIVERY"}:
        fail(
            "WORK_ITEM_HIERARCHY_CONTRACT_INVALID",
            "root_kind must be TASK, CAPABILITY, or DELIVERY",
            field="root_kind",
            allowed=["TASK", "CAPABILITY", "DELIVERY"],
        )
    allowed_modes = (
        list(HIERARCHY_CONTRACT_INPUT_MODES)
        if root_kind == "TASK"
        else ["FULL_HIERARCHY"]
    )
    if input_mode not in allowed_modes:
        fail(
            "WORK_ITEM_HIERARCHY_CONTRACT_INVALID",
            f"{root_kind} does not support input mode {input_mode}",
            field="input_mode",
            rootKind=root_kind,
            allowedInputModes=allowed_modes,
        )
    compact = input_mode == "COMPACT_TASK"
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootKind": root_kind,
        "inputMode": input_mode,
        "inputSchema": (
            _compact_task_schema()
            if compact
            else _full_hierarchy_schema(root_kind)
        ),
        "example": (
            _compact_example()
            if compact
            else _full_example(root_kind)
        ),
        "invariants": [
            "Task is the only executable leaf.",
            "Every requirement has an independent acceptance criterion.",
            "requiredSkills may be omitted or supplied as an exact array.",
            (
                "Known changes use exact fileChanges; generatedFileRoots "
                "authorize ADD-only files."
            ),
            "Test commands are argv arrays.",
        ],
    }
