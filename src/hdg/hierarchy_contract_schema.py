from __future__ import annotations

from typing import Any

from .constants import (
    MAX_DATABASE_CHANGES_PER_TASK,
    MAX_DATABASE_COLUMNS_PER_TABLE,
    MAX_DATABASE_CONSTRAINTS_PER_TABLE,
    MAX_DATABASE_FOREIGN_KEYS_PER_TABLE,
    MAX_DATABASE_INDEXES_PER_TABLE,
    MAX_DATABASE_VERIFICATION_STEPS,
    MAX_IDENTIFIER_LENGTH,
    SCHEMA_VERSION,
)

from .errors import fail

from .interaction_contract import execution_choice_contract

def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }

def _identifier(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": (
            f"^[a-z0-9][a-z0-9._-]"
            f"{{0,{MAX_IDENTIFIER_LENGTH - 1}}}$"
        ),
        "description": description,
    }

def _text(description: str) -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "description": description,
    }

def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}

def _loop_schema() -> dict[str, Any]:
    return _object(
        {
            "ref": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9._:/@-]{0,191}$",
                "description": "Stable Loop implementation reference.",
            },
            "payload": {
                "type": "object",
                "description": (
                    "Opaque Loop-owned input prepared by the host planning "
                    "layer. Supply requirement direction, explicit constraints, "
                    "confirmed external contracts, and known acceptance "
                    "outcomes; ordinary files, implementation classes, internal "
                    "methods, code structure, tests, gates, and detailed "
                    "implementation plans remain Loop-owned. The Graph "
                    "structures work items into a hierarchy/DAG, preserves "
                    "their opaque input, controls dependencies and resources, "
                    "routes execution, and aggregates global progress and "
                    "results; it does not author or complete business "
                    "requirements. "
                    "An exact implementation identifier is frozen only when "
                    "the requirement explicitly mandates it or the user "
                    "confirms an external compatibility contract that fixes it. "
                    "The controller validates and projects the reserved "
                    "databaseChanges contract when it is declared."
                ),
                "properties": {
                    "databaseChanges": {
                        "type": "array",
                        "items": _ref("databaseChange"),
                        "minItems": 1,
                        "maxItems": MAX_DATABASE_CHANGES_PER_TASK,
                        "description": (
                            "Complete frozen table before/after designs, "
                            "migration plans, and matching resource locks."
                        ),
                    }
                },
                "additionalProperties": True,
            },
            "resourceClaims": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": "^[a-z0-9][a-z0-9._:/@-]{0,255}$",
                },
                "uniqueItems": True,
                "description": (
                    "Exact exclusive scheduler lock keys; not file scopes."
                ),
            },
        }
    )

def _skill_hint_schema() -> dict[str, Any]:
    return _object(
        {
            "name": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                "description": "Exact host Skill catalog name.",
            },
            "purpose": _text(
                "Why a later TASK or Review Loop should prefer this Skill."
            ),
        }
    )

def _skill_hints_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": _ref("skillHint"),
        "uniqueItems": True,
        "description": (
            "Root-shared advisory Skill preferences. Every TASK, configured "
            "GROUP seam Review, and Delivery Acceptance/Readiness Loop "
            "receives them at runtime and selects only applicable hints."
        ),
    }

def _git_binding_schema() -> dict[str, Any]:
    branch = {
        "type": "string",
        "minLength": 1,
        "maxLength": 240,
        "description": (
            "Local Git branch name without the refs/heads/ prefix."
        ),
    }
    return _object(
        {
            "branchRef": {
                **branch,
                "description": (
                    "Delivery feature branch checked out by its workspace."
                ),
            },
            "baseRef": {
                **branch,
                "description": (
                    "Branch from which the Delivery was created: normally "
                    "mainline, or an explicitly confirmed parent feature "
                    "for a stacked Delivery."
                ),
            },
            "baseCommit": {
                "type": "string",
                "pattern": "^(?:[0-9a-f]{40}|[0-9a-f]{64})$",
                "description": (
                    "Full immutable Git object ID of the Delivery fork base."
                ),
            },
            "integrationTarget": {
                **branch,
                "description": (
                    "Branch that receives the final Delivery integration; "
                    "it must equal baseRef."
                ),
            },
        }
    )

def _project_scope_schema() -> dict[str, Any]:
    return _object(
        {
            "id": _identifier(
                "Stable project ID within this Delivery."
            ),
            "workspaceRoot": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Absolute local repository anchor authorized for this "
                    "Delivery revision. Runtime loop_context resolves it "
                    "to this Delivery's verified workspace root."
                ),
            },
            "access": {
                "type": "string",
                "enum": ["READ_ONLY", "READ_WRITE"],
                "description": (
                    "Maximum scheduler-visible access for this project."
                ),
            },
            "gitBinding": _git_binding_schema(),
        },
        required=["id", "workspaceRoot", "access"],
    )

def _project_scopes_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": _project_scope_schema(),
        "minItems": 1,
        "description": (
            "Exact cross-project scope for this Delivery revision. Freeze "
            "requires explicit authorization of every listed project ID."
        ),
    }

def _nullable_text() -> dict[str, Any]:
    return {"type": ["string", "null"]}

def _database_column_schema() -> dict[str, Any]:
    return _object(
        {
            "name": _text("Exact column name."),
            "type": _text("Complete database-native column type."),
            "nullable": {"type": "boolean"},
            "default": {
                "type": ["string", "number", "integer", "boolean", "null"],
            },
            "comment": _nullable_text(),
            "autoIncrement": {"type": "boolean"},
            "generated": {"type": "boolean"},
        },
        required=["name", "type", "nullable", "default", "comment"],
    )

def _database_named_columns_schema() -> dict[str, Any]:
    return _object(
        {
            "name": _text("Exact constraint name."),
            "columns": {
                "type": "array",
                "items": _text("Exact column name."),
                "minItems": 1,
                "maxItems": MAX_DATABASE_COLUMNS_PER_TABLE,
                "uniqueItems": True,
            },
        }
    )

def _database_index_schema() -> dict[str, Any]:
    return _object(
        {
            "name": _text("Exact index name."),
            "columns": {
                "type": "array",
                "items": _text("Indexed column or expression."),
                "minItems": 1,
                "maxItems": MAX_DATABASE_COLUMNS_PER_TABLE,
                "uniqueItems": True,
            },
            "unique": {"type": "boolean"},
            "method": _text("Optional database index method."),
            "predicate": _text("Optional partial-index predicate."),
        },
        required=["name", "columns", "unique"],
    )

def _database_foreign_key_schema() -> dict[str, Any]:
    return _object(
        {
            "name": _text("Exact foreign-key name."),
            "columns": {
                "type": "array",
                "items": _text("Source column name."),
                "minItems": 1,
                "maxItems": MAX_DATABASE_COLUMNS_PER_TABLE,
                "uniqueItems": True,
            },
            "referencedTable": _text("Referenced table identity."),
            "referencedColumns": {
                "type": "array",
                "items": _text("Referenced column name."),
                "minItems": 1,
                "maxItems": MAX_DATABASE_COLUMNS_PER_TABLE,
                "uniqueItems": True,
            },
            "onDelete": _text("Explicit ON DELETE behavior or NOT_APPLICABLE."),
            "onUpdate": _text("Explicit ON UPDATE behavior or NOT_APPLICABLE."),
        }
    )

def _database_snapshot_schema() -> dict[str, Any]:
    return _object(
        {
            "comment": _nullable_text(),
            "columns": {
                "type": "array",
                "items": _ref("databaseColumn"),
                "minItems": 1,
                "maxItems": MAX_DATABASE_COLUMNS_PER_TABLE,
            },
            "primaryKey": {
                "oneOf": [_ref("databaseNamedColumns"), {"type": "null"}],
            },
            "uniqueConstraints": {
                "type": "array",
                "items": _ref("databaseNamedColumns"),
                "maxItems": MAX_DATABASE_CONSTRAINTS_PER_TABLE,
            },
            "indexes": {
                "type": "array",
                "items": _ref("databaseIndex"),
                "maxItems": MAX_DATABASE_INDEXES_PER_TABLE,
            },
            "foreignKeys": {
                "type": "array",
                "items": _ref("databaseForeignKey"),
                "maxItems": MAX_DATABASE_FOREIGN_KEYS_PER_TABLE,
            },
        }
    )

def _database_migration_schema() -> dict[str, Any]:
    return _object(
        {
            "forward": _text("Forward migration procedure or artifact."),
            "rollback": _text("Rollback procedure or explicit impossibility."),
            "backfill": _text("Historical-data backfill plan or NOT_APPLICABLE."),
            "compatibility": _text("Rollout compatibility and ordering."),
            "verification": {
                "type": "array",
                "items": _text("Concrete migration verification."),
                "minItems": 1,
                "maxItems": MAX_DATABASE_VERIFICATION_STEPS,
            },
        }
    )

def _database_change_schema() -> dict[str, Any]:
    base = _object(
        {
            "projectId": _text("Owning project ID when the Delivery spans projects."),
            "database": _text("Database or datasource identity."),
            "schema": _text("Database schema or namespace."),
            "table": _text("Exact table name."),
            "summary": _text("Business reason for this table change."),
            "changeType": {
                "type": "string",
                "enum": ["CREATE", "MODIFY", "DELETE"],
            },
            "before": {
                "oneOf": [_ref("databaseSnapshot"), {"type": "null"}],
            },
            "after": {
                "oneOf": [_ref("databaseSnapshot"), {"type": "null"}],
            },
            "migration": _ref("databaseMigration"),
            "resourceClaim": _text(
                "Exact lock key also present in the TASK Loop resourceClaims."
            ),
        },
        required=[
            "table",
            "summary",
            "changeType",
            "before",
            "after",
            "migration",
            "resourceClaim",
        ],
    )
    base["allOf"] = [
        {
            "if": {"properties": {"changeType": {"const": "CREATE"}}},
            "then": {
                "properties": {
                    "before": {"const": None},
                    "after": _ref("databaseSnapshot"),
                }
            },
        },
        {
            "if": {"properties": {"changeType": {"const": "MODIFY"}}},
            "then": {
                "properties": {
                    "before": _ref("databaseSnapshot"),
                    "after": _ref("databaseSnapshot"),
                }
            },
        },
        {
            "if": {"properties": {"changeType": {"const": "DELETE"}}},
            "then": {
                "properties": {
                    "before": _ref("databaseSnapshot"),
                    "after": {"const": None},
                }
            },
        },
    ]
    return base

def _depends_on_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": _identifier("Direct sibling GROUP or TASK dependency ID."),
        "uniqueItems": True,
        "description": (
            "Direct-sibling startup dependencies. A GROUP dependency gates "
            "the dependent subtree until the source GROUP review succeeds."
        ),
    }

def _child_summary_schema() -> dict[str, Any]:
    return _object(
        {
            "id": _identifier("Direct child ID."),
            "kind": {"type": "string", "enum": ["GROUP", "TASK"]},
            "title": _text("Direct child title."),
        }
    )

def _task_definition(*, root: bool) -> dict[str, Any]:
    return _object(
        {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "id": _identifier("TASK scheduler ID."),
            "kind": {"const": "TASK"},
            "parentId": (
                {"const": None}
                if root
                else _identifier("Parent GROUP ID.")
            ),
            "title": _text("TASK Loop title."),
            "summary": _text("Scheduler-facing TASK outcome summary."),
            "execution": _object(
                {
                    "dependsOn": _depends_on_schema(),
                    "loop": _ref("loop"),
                }
            ),
        }
    )

def _group_definition(*, root: bool) -> dict[str, Any]:
    return _object(
        {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "id": _identifier("GROUP scheduler ID."),
            "kind": {"const": "GROUP"},
            "parentId": (
                {"const": None}
                if root
                else _identifier("Parent GROUP ID.")
            ),
            "title": _text("GROUP title."),
            "summary": _text("Scheduler-facing GROUP join summary."),
            "decomposition": _object(
                {"dependsOn": _depends_on_schema()}
            ),
            "children": {
                "type": "array",
                "items": _ref("childSummary"),
                "minItems": 1,
            },
        }
    )

def _task_node(*, root: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "definition": _ref(
            "taskRootDefinition" if root else "taskChildDefinition"
        ),
        "reviewLoop": {
            "oneOf": [_ref("loop"), {"type": "null"}],
            "description": (
                "Independent TASK Review Loop. It may be null only for a "
                "root TASK in an explicitly classified LIGHT Delivery."
            ),
        },
        "children": {"type": "array", "maxItems": 0},
    }
    if root:
        properties = {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "skillHints": _skill_hints_schema(),
            **properties,
        }
    return _object(
        properties
    )

def _group_node(*, root: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "definition": _ref(
            "groupRootDefinition" if root else "groupChildDefinition"
        ),
        "reviewLoop": {
            "oneOf": [_ref("loop"), {"type": "null"}],
            "description": (
                "Optional direct-child seam Review. Use null when the "
                "GROUP is only a coordination or join boundary."
            ),
        },
        "children": {
            "type": "array",
            "items": {
                "oneOf": [
                    _ref("groupChildNode"),
                    _ref("taskChildNode"),
                ]
            },
            "minItems": 1,
        },
    }
    if root:
        properties = {
            "schemaVersion": {"const": SCHEMA_VERSION},
            "skillHints": _skill_hints_schema(),
            **properties,
        }
    return _object(
        properties
    )

def _definitions() -> dict[str, Any]:
    return {
        "loop": _loop_schema(),
        "databaseColumn": _database_column_schema(),
        "databaseNamedColumns": _database_named_columns_schema(),
        "databaseIndex": _database_index_schema(),
        "databaseForeignKey": _database_foreign_key_schema(),
        "databaseSnapshot": _database_snapshot_schema(),
        "databaseMigration": _database_migration_schema(),
        "databaseChange": _database_change_schema(),
        "skillHint": _skill_hint_schema(),
        "childSummary": _child_summary_schema(),
        "taskRootDefinition": _task_definition(root=True),
        "taskChildDefinition": _task_definition(root=False),
        "groupRootDefinition": _group_definition(root=True),
        "groupChildDefinition": _group_definition(root=False),
        "taskRootNode": _task_node(root=True),
        "taskChildNode": _task_node(root=False),
        "groupRootNode": _group_node(root=True),
        "groupChildNode": _group_node(root=False),
    }

def _loop(
    reference: str,
    goal: str,
    claims: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ref": reference,
        "payload": {
            "goal": goal,
            "acceptance": ["Return one standard Loop outcome."],
        },
        "resourceClaims": claims or [],
    }

def _task(
    item_id: str,
    parent_id: str | None,
    *,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "definition": {
            "schemaVersion": SCHEMA_VERSION,
            "id": item_id,
            "kind": "TASK",
            "parentId": parent_id,
            "title": f"Run {item_id}",
            "summary": "Produce one independently schedulable result.",
            "execution": {
                "dependsOn": depends_on or [],
                "loop": _loop(
                    "project/example-task-loop@1",
                    f"Implement and verify {item_id} internally.",
                    [f"project:example/module:{item_id}"],
                ),
            },
        },
        "reviewLoop": _loop(
            "task/independent-review-loop@1",
            f"Independently review the completed {item_id} TASK result.",
        ),
        "children": [],
    }

def _group(
    item_id: str,
    parent_id: str | None,
    children: list[dict[str, Any]],
    *,
    depends_on: list[str] | None = None,
    review_boundary: bool = True,
) -> dict[str, Any]:
    return {
        "definition": {
            "schemaVersion": SCHEMA_VERSION,
            "id": item_id,
            "kind": "GROUP",
            "parentId": parent_id,
            "title": f"Coordinate {item_id}",
            "summary": "Join and review direct child results.",
            "decomposition": {"dependsOn": depends_on or []},
            "children": [
                {
                    "id": child["definition"]["id"],
                    "kind": child["definition"]["kind"],
                    "title": child["definition"]["title"],
                }
                for child in children
            ],
        },
        "reviewLoop": (
            _loop(
                "group/direct-child-seam-review-loop@1",
                f"Verify only the direct-child seams of {item_id}.",
            )
            if review_boundary
            else None
        ),
        "children": children,
    }

def _example(root_kind: str) -> dict[str, Any]:
    if root_kind == "TASK":
        root = _task("t-example", None)
    else:
        service = _group(
            "g-service",
            "g-root",
            [
                _task("t-api", "g-service"),
                _task(
                    "t-core",
                    "g-service",
                    depends_on=["t-api"],
                ),
            ],
        )
        docs = _task(
            "t-docs",
            "g-root",
            depends_on=["g-service"],
        )
        root = _group(
            "g-root",
            None,
            [service, docs],
            review_boundary=False,
        )
    return {
        "delivery": {
            "id": "d-example",
            "title": "Example delivery",
            "summary": "Complete the recursive GROUP/TASK Graph.",
            "reviewLoop": _loop(
                "delivery/acceptance-readiness-loop@1",
                "Verify complete Delivery acceptance and readiness.",
            ),
        },
        "root": {
            "schemaVersion": SCHEMA_VERSION,
            "skillHints": [
                {
                    "name": "springboot-tdd",
                    "purpose": (
                        "Prefer this Skill in a later TASK or Review Loop "
                        "when its actual work is Spring Boot development."
                    ),
                }
            ],
            **root,
        },
    }

def hierarchy_input_schema(
    *,
    root_kind: str | None = None,
) -> dict[str, Any]:
    """Return the shared schema-v3 hierarchy input contract."""

    if root_kind not in {None, "GROUP", "TASK"}:
        fail(
            "WORK_ITEM_HIERARCHY_CONTRACT_INVALID",
            "root_kind must be GROUP or TASK",
        )
    if root_kind is None:
        root_schema: dict[str, Any] = {
            "oneOf": [
                _ref("groupRootNode"),
                _ref("taskRootNode"),
            ]
        }
    else:
        root_schema = _ref(
            "groupRootNode"
            if root_kind == "GROUP"
            else "taskRootNode"
        )
    input_schema = _object(
        {
            "delivery": _object(
                {
                    "id": _identifier("Delivery and Graph run ID."),
                    "title": _text("Delivery title."),
                    "summary": _text("Delivery outcome summary."),
                    "requirementKey": {
                        "type": "string",
                        "pattern": (
                            "^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,127}$"
                        ),
                        "description": (
                            "Stable external requirement key such as "
                            "MPROTEIN-443. The same key must keep one "
                            "delivery.id and use Delivery Revisions."
                        ),
                    },
                    "assuranceProfile": {
                        "type": "string",
                        "enum": ["LIGHT", "STANDARD"],
                        "description": (
                            "Assurance inferred from actual change content "
                            "and impact. Omit for the safe STANDARD default."
                        ),
                    },
                    "assuranceRationale": _text(
                        "Required for LIGHT: concise evidence from the actual "
                        "change surface and impact assessment."
                    ),
                    "gitBinding": _git_binding_schema(),
                    "projectScopes": _project_scopes_schema(),
                    "reviewLoop": {
                        "oneOf": [_ref("loop"), {"type": "null"}],
                        "description": (
                            "Final technical Acceptance/Readiness Loop for "
                            "top-level requirement coverage, system evidence, "
                            "operational readiness, and global risk. It may be "
                            "null only for a LIGHT Delivery."
                        ),
                    },
                },
                required=["id", "title", "summary", "reviewLoop"],
            ),
            "root": root_schema,
        }
    )
    input_schema["$defs"] = _definitions()
    return input_schema
