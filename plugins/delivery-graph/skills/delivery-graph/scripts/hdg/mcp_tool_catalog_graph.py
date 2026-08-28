from __future__ import annotations

from .mcp_tool_schemas import (
    DASHBOARD_RESOURCE_URI,
    FINGERPRINT,
    NODE_ID,
    ROOT_ID,
    _object,
    _string,
    _tool,
)


GRAPH_TOOLS = (
    _tool(
        "route_entry_intent",
        (
            "Return a deterministic entry route for a user request by "
            "combining explicit intent rules with authoritative Delivery "
            "state. Ambiguous or state-conflicting requests fail closed in "
            "the decision and require model classification or user "
            "confirmation. A CANCELLED Run remains terminal; explicit "
            "continuation or replanning routes to the next immutable "
            "Revision only when the Delivery is OPEN and its workspace "
            "release is persisted. The response also contains an optional, "
            "decision-only multi-Supervisor plan; Supervisors have no tools, "
            "do not query business data, execute routes, or generate the "
            "final response. This tool never mutates Graph state."
        ),
        _object(
            {
                "request_text": _string(
                    "The current user request to classify."
                ),
                "root_id": ROOT_ID,
            },
            required=["request_text"],
        ),
    ),
    _tool(
        "plan_dispatch_batch",
        (
            "Plan one concurrent batch for the current DISPATCH_LOOP "
            "frontier when an independent receiver is required, including "
            "every Review Loop. It reserves each assignment before the "
            "trusted current host creates an independent outer receiver. "
            "Returns receiver identities, a versioned specialist Agent "
            "Profile Catalog binding, an owner/helper teamPlan, and decision "
            "fingerprints. Only the owner receives control-plane credentials; "
            "helpers are optional Loop-internal roles. When shared Skill hints exist, each "
            "assignment also carries their exact advisory receiverPrompt. A "
            "user-explicit Skill should be invoked host-natively at each "
            "applicable and available stage, usually TASK for implementation "
            "Skills; it may be skipped only when the stage is inapplicable or "
            "the host cannot provide it, and never gates Controller success. "
            "The Controller never "
            "starts Agents or claims Loops. "
            "After consuming every assignment, obey postActionWait: wait for "
            "a receiver event or the earliest reservation deadline, then call "
            "graph_frontier once; never busy-poll."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_graph_fingerprint": FINGERPRINT,
            },
            required=[
                "root_id",
                "expected_graph_fingerprint",
            ],
        ),
    ),
    _tool(
        "freeze_hierarchy",
        (
            "Freeze an explicitly prepared later revision. The initial "
            "automatic button calls select_execution_mode(AUTOMATIC), not "
            "this low-level tool. Every same-Delivery N-to-N+1 freeze keeps "
            "the original clean workspace turn when its exact project "
            "checkouts and Git binding are unchanged, so unfinished business "
            "changes need not be stashed, deleted, or checkpoint-committed. "
            "Unmerged conflicts, rewritten turn history, and changed scope "
            "or binding still fail closed. Freezing never authorizes a Git "
            "commit. Manual revisions use "
            "create_manual_handoff; their receiving CLI later creates the "
            "governed manual run through start_manual_handoff."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "expected_delivery_revision": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Exact Delivery revision returned by prepare."
                    ),
                },
                "expected_hierarchy_fingerprint": _string(
                    "Fingerprint returned by prepare_hierarchy."
                ),
                "authorized_project_ids": {
                    "type": "array",
                    "items": ROOT_ID,
                    "uniqueItems": True,
                    "description": (
                        "Exact project IDs explicitly authorized by the "
                        "user for this revision; use an empty array when "
                        "projectScopes is absent."
                    ),
                },
                "confirmed_by": _string("Human confirmer identity."),
            },
            required=[
                "root_id",
                "expected_delivery_revision",
                "expected_hierarchy_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        ),
    ),
    _tool(
        "graph_frontier",
        (
            "Advance scheduler bookkeeping and return the next Graph actions. "
            "Consume every returned immediate action, then follow "
            "progressMonitor.waitDirective. Never call it back-to-back; use "
            "graph_status for any permitted periodic observation."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "unfreeze_task_requirement",
        (
            "Unfreeze one not-yet-started TASK requirement so it can be "
            "revised without changing Delivery topology, dependencies, or "
            "resource locks. The entire current Graph Run must have no "
            "pending dispatch reservation because every assignment is bound "
            "to the current Graph fingerprint."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "task_id": _string("Exact TASK work-item ID."),
                "expected_revision": {
                    "type": "integer",
                    "minimum": 1,
                },
                "authorized_by": _string("Human authorizer identity."),
                "reason": _string("Reason for revising the TASK requirement."),
            },
            required=[
                "root_id",
                "task_id",
                "expected_revision",
                "authorized_by",
                "reason",
            ],
        ),
        human=True,
    ),
    _tool(
        "refreeze_task_requirement",
        (
            "Replace and refreeze one previously unfrozen, unstarted TASK "
            "requirement. The replacement may change only title, summary, "
            "and opaque Loop payload. The Controller anchors the confirmed "
            "replacement as the next immutable Delivery revision, preserves "
            "the prior revision fingerprints, and starts the replacement "
            "Run in the existing execution mode. The entire current Graph "
            "Run must have no pending dispatch reservation."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "task_id": _string("Exact TASK work-item ID."),
                "expected_revision": {
                    "type": "integer",
                    "minimum": 1,
                },
                "requirement": _object(
                    {
                        "title": _string("Revised TASK title."),
                        "summary": _string("Revised TASK summary."),
                        "payload": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    required=["title", "summary", "payload"],
                ),
                "confirmed_by": _string("Human confirmer identity."),
            },
            required=[
                "root_id",
                "task_id",
                "expected_revision",
                "requirement",
                "confirmed_by",
            ],
        ),
        human=True,
    ),
    _tool(
        "graph_status",
        (
            "Read the current materialized Graph and Loop states. Use it only "
            "for read-only periodic observation at or after "
            "progressMonitor.waitDirective.pollNotBefore, never back-to-back. "
            "Call graph_frontier only for returned actions, receiver events, "
            "nextWakeAt, or ADVANCE_REQUIRED."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "delivery_result",
        (
            "Read the deterministic Delivery result assembled from every "
            "persisted Loop outcome, acceptance record, verification "
            "evidence item, and Review finding. It reports completeness "
            "issues explicitly and never relies on conversation memory."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "open_delivery_dashboard",
        (
            "Open a read-only MCP Apps dashboard for the current Delivery. "
            "It returns a data-minimized snapshot of Graph nodes, active "
            "Loops, alerts, and Revision history without advancing the "
            "scheduler or changing control-plane state."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
        title="Open Delivery Dashboard",
        meta={
            "ui": {
                "resourceUri": DASHBOARD_RESOURCE_URI,
                "visibility": ["model", "app"],
            },
            "openai/outputTemplate": DASHBOARD_RESOURCE_URI,
            "openai/widgetAccessible": True,
            "openai/toolInvocation/invoking": "正在读取 Delivery 运行状态…",
            "openai/toolInvocation/invoked": "Delivery 运行看板已更新",
        },
    ),
    _tool(
        "graph_events",
        "Read the tamper-evident scheduler event stream.",
        _object(
            {
                "root_id": ROOT_ID,
                "after_event_id": {
                    "type": "integer",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            required=["root_id"],
        ),
    ),
    _tool(
        "advance_graph",
        "Advance lease expiry, infrastructure retry, joins, and readiness.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "rebuild_graph_run",
        "Rebuild mutable node projections from the verified event stream.",
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
    _tool(
        "loop_context",
        (
            "Read one opaque Loop descriptor, shared advisory Skill hints for "
            "runtime reevaluation, "
            "direct predecessors, transitive upstream results, TASK baseline "
            "path, runtime-verified project workspace roots, frozen project "
            "scope anchors, completion policy for internal adaptation and "
            "rework, and the execution policy for live-lease handoff and "
            "expired-lease recovery. Loops use the "
            "verified roots as-is and never create, switch, or check out Git "
            "branches."
        ),
        _object(
            {"root_id": ROOT_ID, "node_id": NODE_ID},
            required=["root_id", "node_id"],
        ),
    ),
)
