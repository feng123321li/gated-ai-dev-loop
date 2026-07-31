from __future__ import annotations

from typing import Any

from .constants import MAX_IDENTIFIER_LENGTH, SCHEMA_VERSION
from .errors import fail


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
                    "Opaque Loop-owned input. It may contain implementation "
                    "plans, acceptance rules, tests, gates, and Skills."
                ),
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
            "Root-shared advisory Skill preferences. Every TASK, GROUP "
            "Review, and Delivery Review Loop receives them at runtime and "
            "selects only applicable hints."
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
                    "Delivery feature branch checked out by its worktree."
                ),
            },
            "baseRef": {
                **branch,
                "description": (
                    "Mainline branch from which the Delivery was created."
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
                    "Mainline branch that receives the final Delivery PR."
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
                    "Absolute local workspace root authorized for this "
                    "Delivery revision."
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
        "reviewLoop": _ref("loop"),
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
        "reviewLoop": _ref("loop"),
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
        "reviewLoop": _loop(
            "group/independent-review-loop@1",
            f"Review the completed {item_id} GROUP boundary.",
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
        root = _group("g-root", None, [service, docs])
    return {
        "delivery": {
            "id": "d-example",
            "title": "Example delivery",
            "summary": "Complete the recursive GROUP/TASK Graph.",
            "reviewLoop": _loop(
                "delivery/independent-review-loop@1",
                "Independently review the complete Delivery.",
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
                    "gitBinding": _git_binding_schema(),
                    "projectScopes": _project_scopes_schema(),
                    "reviewLoop": _ref("loop"),
                },
                required=["id", "title", "summary", "reviewLoop"],
            ),
            "root": root_schema,
        }
    )
    input_schema["$defs"] = _definitions()
    return input_schema


def hierarchy_contract(
    *,
    root_kind: str,
    **_: Any,
) -> dict[str, Any]:
    input_schema = hierarchy_input_schema(root_kind=root_kind)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "rootKind": root_kind,
        "inputSchema": input_schema,
        "example": _example(root_kind),
        "projectionGuidance": {
            "gitBinding": {
                "requiredForGitWorkspace": True,
                "branchRole": "DELIVERY_FEATURE",
                "defaultMainlinePreference": ["main", "master"],
                "baseAndIntegrationTargetMustMatch": True,
                "baseCommitRole": "IMMUTABLE_FORK_POINT",
                "taskBranchPolicy": "SHARED_DELIVERY_FEATURE_BRANCH",
                "taskBranchBindingsSupported": False,
                "taskCommitPolicy": (
                    "TASK_SCOPED_COMMITS_ON_DELIVERY_BRANCH"
                ),
                "taskCommitConstraints": [
                    "EXPLICIT_TASK_SCOPE",
                    "SERIALIZED_GIT_INDEX_WRITE",
                    "SEPARATE_GIT_WRITE_AUTHORIZATION",
                ],
                "runtimeValidation": [
                    "WORKTREE_ROOT",
                    "BOUND_BRANCH",
                    "HEAD_INHERITS_BASE_COMMIT",
                    "MAINLINE_CONTAINS_BASE_COMMIT",
                ],
                "description": (
                    "For a Git workspace, copy workspace_status."
                    "suggestedGitBinding into delivery.gitBinding. Each "
                    "writable repository in one Delivery uses the same "
                    "feature branch name, created from that repository's "
                    "mainline (main, falling back to master). All TASKs "
                    "share those Delivery branches; TASK "
                    "agents do not create, bind, or switch internal branches. "
                    "When separately authorized, each TASK may stage and "
                    "commit only its own changes on the Delivery branch; "
                    "shared Git index writes must be serialized. "
                    "The scheduler verifies but never creates, switches, "
                    "commits, merges, or pushes Git branches."
                ),
            },
            "projectScopes": {
                "freezeAuthorization": "EXACT_PROJECT_ID_SET",
                "writableGitBranchPolicy": (
                    "SAME_BRANCH_REF_ACROSS_PROJECTS"
                ),
                "description": (
                    "One logical Delivery may authorize multiple local "
                    "repositories. Every writable Git project uses the same "
                    "feature branch name, while each repository freezes its "
                    "own base commit and integration target. Adding a "
                    "project requires a new Delivery revision and a new "
                    "exact project authorization at freeze."
                ),
            },
            "acceptanceReports": {
                "scope": "CURRENT_LAYER",
                "taskReport": {
                    "fullDetails": [
                        "TASK_LOOP",
                        "TASK_REVIEW_LOOP",
                    ],
                },
                "groupReport": {
                    "fullDetails": [
                        "GROUP_JOIN",
                        "GROUP_REVIEW_LOOP",
                    ],
                    "childReferences": [
                        "status",
                        "summary",
                        "acceptanceLink",
                    ],
                },
                "deliveryReport": {
                    "fullDetails": [
                        "DELIVERY_REVIEW_LOOP",
                        "USER_CONFIRMATION",
                    ],
                    "rootReference": [
                        "status",
                        "summary",
                        "acceptanceLink",
                    ],
                },
                "nonDuplicatedFromLowerLayers": [
                    "payload",
                    "evidence",
                    "reviewFindings",
                ],
                "description": (
                    "Each acceptance report fully renders only its current "
                    "layer. GROUP reports summarize and link direct child "
                    "reports; the Delivery report summarizes and links the "
                    "root work-item report. Lower-layer payloads, evidence, "
                    "and review findings are not copied upward."
                ),
            },
            "interfaces": {
                "location": (
                    "TASK definition.execution.loop.payload.interfaces"
                ),
                "protocolExamples": [
                    "HTTP",
                    "DUBBO",
                    "GRPC",
                    "GRAPHQL",
                    "MESSAGE",
                ],
                "requiredFields": [
                    "protocol",
                    "name",
                    "summary",
                    "changeType",
                    "before",
                    "after",
                ],
                "changeTypes": ["CREATE", "MODIFY", "DELETE"],
                "snapshotRequiredFields": ["request", "response"],
                "genericSnapshotFields": [
                    "identifier",
                    "request",
                    "response",
                ],
                "httpSnapshotFields": ["method", "path"],
                "dubboSnapshotFields": ["service", "method"],
                "fieldProjection": {
                    "layout": "REQUEST_RESPONSE_TABLES",
                    "documents": {
                        "index": "interfaces.md",
                        "detailsDirectory": "interfaces/",
                        "oneDocumentPerInterface": True,
                    },
                    "changeStates": [
                        "CREATE",
                        "MODIFY",
                        "DELETE",
                        "UNCHANGED",
                    ],
                    "requestComparisonColumns": [
                        "type",
                        "required",
                        "description",
                    ],
                    "responseComparisonColumns": [
                        "type",
                        "description",
                    ],
                    "deletedValueStyle": "MARKDOWN_STRIKETHROUGH",
                    "singleSidedChangeStyle": "PRESENT_VALUE_ONLY",
                    "transitionFormat": "BEFORE_TO_AFTER",
                },
                "description": (
                    "When a TASK adds, changes, or deletes an interface, "
                    "declare each concrete interface here with explicit "
                    "before and after snapshots. protocol is an open string; "
                    "protocolExamples are illustrative, not exhaustive. "
                    "Each applicable snapshot contains the complete request "
                    "and response contract plus a generic identifier or "
                    "protocol-specific call fields. The controller writes "
                    "an index to that TASK's interfaces.md and one field-level "
                    "document per interface under interfaces/. Request tables "
                    "compare type, required, and description; response tables "
                    "omit required. Deleted values use Markdown strikethrough, "
                    "and added or deleted fields show only the present side. "
                    "When a TASK declares no interfaces, its directory has no "
                    "interface projection or link. Code inspection may help "
                    "prepare or verify the declaration, but the controller "
                    "does not infer it dynamically. The payload remains "
                    "opaque to Graph scheduling."
                ),
            }
        },
        "invariants": [
            "Delivery is the frozen Graph and final acceptance boundary.",
            (
                "Each writable Git project freezes the Delivery's shared "
                "feature branch name, its own immutable mainline fork "
                "commit, and the same per-project base/integration target."
            ),
            (
                "One Delivery may span multiple local repositories; every "
                "writable Git project uses the same feature branch name."
            ),
            "GROUP may recursively contain GROUP or TASK children.",
            "TASK is the only execution leaf and cannot contain children.",
            (
                "Every TASK compiles to TASK_LOOP followed by its required "
                "independent TASK_REVIEW_LOOP."
            ),
            (
                "Every GROUP compiles to GROUP_JOIN followed by its required "
                "independent GROUP_REVIEW_LOOP."
            ),
            (
                "Sibling dependsOn references direct GROUP/TASK siblings; a "
                "GROUP dependency gates the dependent subtree entries."
            ),
            (
                "The root terminal flows through DELIVERY_REVIEW_LOOP and "
                "one final USER_CONFIRMATION."
            ),
            "Loop payloads own implementation, tests, gates, and Skills.",
            (
                "skillHints are advisory, shared, and late-bound; they are "
                "never assigned during requirement planning."
            ),
            "resourceClaims are exact scheduler locks, not file scopes.",
            "Only standard Loop outcomes cross a Loop boundary.",
        ],
    }


__all__ = ("hierarchy_contract", "hierarchy_input_schema")
