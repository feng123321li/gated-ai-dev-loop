from __future__ import annotations

from copy import deepcopy
from typing import Any


def task_definition(**overrides: Any) -> dict[str, Any]:
    definition = {
        "schemaVersion": 3,
        "id": "t-python-controller",
        "kind": "TASK",
        "gateLevel": "LIGHT",
        "parentId": None,
        "title": "Python controller",
        "goal": "Run the governance controller with Python standard-library modules.",
        "scope": ["src/controller.py", "tests/test_controller.py"],
        "nonGoals": ["Do not deploy the controller."],
        "requirements": [
            {"id": "R-001", "text": "The controller must run without Node or third-party Python packages."},
        ],
        "acceptance": [
            {
                "id": "A-001",
                "requirementIds": ["R-001"],
                "expectedResult": "The frozen Python command completes successfully.",
            },
        ],
        "execution": {"dependsOn": [], "inputs": [], "outputs": ["Verified Python controller"]},
        "testCommands": [["python", "-m", "unittest", "tests.test_controller"]],
        "risks": ["Fingerprint behavior must remain deterministic."],
        "decisions": ["Use only the Python standard library at runtime."],
        "developmentPlan": {
            "purpose": "Replace the runtime controller with maintainable Python modules.",
            "scenarios": [
                {
                    "kind": "REFACTOR",
                    "title": "Python runtime",
                    "description": "Replace the controller runtime without changing the frozen behavior.",
                    "requirementIds": ["R-001"],
                },
            ],
            "fileChanges": [
                {"path": "src/controller.py", "action": "ADD", "purpose": "Provide the Python controller."},
                {"path": "tests/test_controller.py", "action": "ADD", "purpose": "Verify the Python controller."},
            ],
            "interfaces": [
                {
                    "name": "hdg command line",
                    "kind": "CLI",
                    "action": "MODIFY",
                    "location": "src/controller.py",
                    "currentContract": "The controller requires Node.",
                    "targetContract": "The controller runs with Python and preserves command semantics.",
                    "requirementIds": ["R-001"],
                },
            ],
            "logic": ["Parse stable CLI options.", "Apply deterministic state transitions."],
            "dataAndTransactions": ["Write registry and package files atomically."],
            "compatibility": ["Keep the complete schema v3 disk contract."],
            "testPlan": [
                {
                    "acceptanceIds": ["A-001"],
                    "approach": "Run the frozen Python unittest command.",
                    "commandIndexes": [0],
                },
            ],
            "reviewPoints": ["Confirm the exact files and CLI contract before freezing."],
        },
    }
    definition.update(overrides)
    return deepcopy(definition)


def coordination_plan(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "purpose": f"Coordinate {definition['title']}.",
        "childPlans": [
            {
                "id": child["id"],
                "purpose": f"Deliver {child['title']}.",
                "deliverables": [f"Verified result for {child['title']}."],
                "requirementIds": child["requirementIds"],
                "acceptanceIds": child["acceptanceIds"],
                "dependsOn": [],
            }
            for child in definition["children"]
        ],
        "sharedContracts": [],
        "integrationFlow": ["Verify every child before the aggregate gate."],
        "deliveryWaves": [
            {
                "order": index + 1,
                "name": f"Wave {index + 1}",
                "childIds": [child["id"]],
                "exitCriteria": f"{child['id']} is verified.",
            }
            for index, child in enumerate(definition["children"])
        ],
        "testPlan": [
            {
                "acceptanceIds": [item["id"] for item in definition["acceptance"]],
                "approach": "Run the frozen aggregate command.",
                "commandIndexes": [0],
            },
        ],
        "reviewPoints": ["Confirm child responsibilities and aggregate acceptance."],
    }


def capability_definition(**overrides: Any) -> dict[str, Any]:
    definition = {
        "schemaVersion": 3,
        "id": "c-python-runtime",
        "kind": "CAPABILITY",
        "gateLevel": "FULL",
        "parentId": None,
        "title": "Python runtime capability",
        "goal": "Coordinate Python runtime implementation and verification.",
        "scope": ["src/**", "tests/**"],
        "nonGoals": ["Do not deploy."],
        "requirements": [{"id": "R-001", "text": "The capability must provide a verified Python runtime."}],
        "acceptance": [{"id": "A-001", "requirementIds": ["R-001"], "expectedResult": "Every Task passes."}],
        "decomposition": {"status": "SEALED", "dependsOn": []},
        "children": [
            {
                "id": "t-python-controller",
                "kind": "TASK",
                "title": "Python controller",
                "requirementIds": ["R-001"],
                "acceptanceIds": ["A-001"],
            },
        ],
        "testCommands": [["python", "-m", "unittest", "discover"]],
        "risks": ["Child contracts must remain stable."],
        "decisions": ["Use a Capability because the work is aggregated."],
    }
    definition.update(overrides)
    definition["developmentPlan"] = overrides.get("developmentPlan", coordination_plan(definition))
    return deepcopy(definition)


def delivery_definition(**overrides: Any) -> dict[str, Any]:
    definition = {
        "schemaVersion": 3,
        "id": "d-python-governance",
        "kind": "DELIVERY",
        "gateLevel": "FULL",
        "title": "Python governance delivery",
        "goal": "Deliver Python-driven governance capabilities.",
        "scope": ["src/**", "tests/**", "skills/**"],
        "nonGoals": ["Do not deploy."],
        "requirements": [{"id": "R-001", "text": "The delivery must provide Python-driven governance."}],
        "acceptance": [{"id": "A-001", "requirementIds": ["R-001"], "expectedResult": "Every Capability passes."}],
        "decomposition": {"status": "SEALED"},
        "children": [
            {
                "id": "c-python-runtime",
                "kind": "CAPABILITY",
                "title": "Python runtime capability",
                "requirementIds": ["R-001"],
                "acceptanceIds": ["A-001"],
            },
        ],
        "testCommands": [["python", "-m", "unittest", "discover"]],
        "risks": ["Cross-capability contracts must remain stable."],
        "decisions": ["Use a Delivery because multiple capabilities are coordinated."],
    }
    definition.update(overrides)
    definition["developmentPlan"] = overrides.get("developmentPlan", coordination_plan(definition))
    return deepcopy(definition)


def hierarchy_node(definition: dict[str, Any], children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "definition": deepcopy(definition),
        "children": deepcopy(children or []),
    }


def hierarchy_definition(
    definition: dict[str, Any],
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 3,
        "root": hierarchy_node(definition, children),
    }


def task_hierarchy(**overrides: Any) -> dict[str, Any]:
    return hierarchy_definition(task_definition(**overrides))


def capability_hierarchy() -> dict[str, Any]:
    capability = capability_definition()
    task = task_definition(parentId=capability["id"], gateLevel="FULL")
    return hierarchy_definition(capability, [hierarchy_node(task)])


def two_task_capability_hierarchy() -> dict[str, Any]:
    children = [
        {
            "id": "t-python-controller",
            "kind": "TASK",
            "title": "Python controller",
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
        },
        {
            "id": "t-python-worker",
            "kind": "TASK",
            "title": "Python worker",
            "requirementIds": ["R-001"],
            "acceptanceIds": ["A-001"],
        },
    ]
    capability = capability_definition(children=children)
    controller = task_definition(parentId=capability["id"], gateLevel="FULL")
    worker = task_definition(
        id="t-python-worker",
        parentId=capability["id"],
        gateLevel="FULL",
        title="Python worker",
        scope=["src/worker.py", "tests/test_worker.py"],
        testCommands=[["python", "-m", "unittest", "tests.test_worker"]],
    )
    worker["developmentPlan"]["fileChanges"] = [
        {"path": "src/worker.py", "action": "ADD", "purpose": "Provide the Python worker."},
        {"path": "tests/test_worker.py", "action": "ADD", "purpose": "Verify the Python worker."},
    ]
    worker["developmentPlan"]["interfaces"][0]["location"] = "src/worker.py"
    return hierarchy_definition(
        capability,
        [hierarchy_node(controller), hierarchy_node(worker)],
    )


def delivery_hierarchy() -> dict[str, Any]:
    delivery = delivery_definition()
    capability = capability_definition(parentId=delivery["id"])
    task = task_definition(parentId=capability["id"], gateLevel="FULL")
    return hierarchy_definition(
        delivery,
        [hierarchy_node(capability, [hierarchy_node(task)])],
    )
