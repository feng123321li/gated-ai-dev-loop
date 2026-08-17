from __future__ import annotations

from .mcp_tool_schemas import (
    FINGERPRINT,
    NODE_ID,
    OPERATION_ID,
    OUTCOME,
    ROOT_ID,
    SCHEDULER_IDENTITY,
    _object,
    _string,
    _tool,
)


RUNTIME_TOOLS = (
    _tool(
        "dispatch_loop",
        (
            "Claim one ready TASK, TASK Review, configured GROUP seam Review, "
            "or Delivery Acceptance/Readiness Loop for its orchestrated outer "
            "receiver. The claim binds "
            "the configured trusted Adapter and request workspace to a "
            "caller-declared receiving context; it does not authenticate a "
            "real host session. The caller must guard "
            "the returned Loop operation ID as a bearer capability."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "owner": SCHEDULER_IDENTITY,
                "agent_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                    "description": (
                        "Caller-declared receiving Agent ID, such as codex or "
                        "claude-code. Used for execution attribution, not "
                        "authenticated identity or executor recommendation."
                    ),
                },
                "dispatch_mode": {
                    "type": "string",
                    "enum": ["AUTO", "MANUAL"],
                    "description": (
                        "AUTO is required for every automatically routed Loop. "
                        "MANUAL is allowed for TASK implementation Loops in "
                        "a Graph started by start_manual_handoff, or for one "
                        "READY automatic TASK explicitly recovered through "
                        "handoff_ready_automatic_task. Review Loops remain "
                        "AUTO and independent."
                    ),
                },
                "receiver_context_id": _string(
                    "Caller-declared host-native receiving Agent context ID. "
                    "Orchestration must provide the actual native context; "
                    "Review Loops must differ from every upstream receiving "
                    "context."
                ),
                "dispatch_transport": {
                    "type": "string",
                    "enum": ["HOST_NATIVE"],
                    "description": (
                        "Required with dispatch_mode=AUTO. It expresses the "
                        "orchestration requirement to use the current host's "
                        "native Agent API, never a CLI, subprocess, or "
                        "companion script; it is not process/session proof."
                    ),
                },
                "dispatch_reservation_id": _string(
                    "Required with dispatch_mode=AUTO. Use the exact "
                    "dispatchReservationId returned for this assignment."
                ),
                "dispatch_decision_fingerprint": {
                    **FINGERPRINT,
                    "description": (
                        "Exact automatic dispatch decision fingerprint. "
                        "Only valid with dispatch_mode=AUTO."
                    ),
                },
                "operation_id": OPERATION_ID,
            },
            required=[
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "dispatch_mode",
                "receiver_context_id",
                "operation_id",
            ],
        ),
    ),
    _tool(
        "handoff_ready_automatic_task",
        (
            "Recover one active AUTOMATIC TASK after native receiver startup "
            "has failed. This explicit mutation is allowed only while the "
            "TASK Loop is READY, its current attempt has never been claimed, "
            "the Delivery workspace and index are clean, no automatic "
            "reservation is "
            "live, and the user confirms no code changes were made. It "
            "switches only that TASK to MANUAL receipt without changing the "
            "Graph execution mode, baseline, fingerprints, or Revision. "
            "Automatic dispatch remains disabled for that TASK; every Review "
            "Loop stays host-native AUTOMATIC. Reuse the same "
            "handoff_request_id only to recover an unknown response."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "expected_graph_fingerprint": FINGERPRINT,
                "handoff_request_id": _string(
                    "Unique idempotency key for this explicit recovery."
                ),
                "confirmed_no_code_changes": {
                    "type": "boolean",
                    "const": True,
                    "description": (
                        "Explicit confirmation that no implementation change "
                        "was made for the unclaimed TASK attempt."
                    ),
                },
                "confirmed_by": SCHEDULER_IDENTITY,
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1024,
                    "description": (
                        "Why native automatic receiver startup cannot safely "
                        "continue, such as repeated startup failure."
                    ),
                },
            },
            required=[
                "root_id",
                "node_id",
                "expected_graph_fingerprint",
                "handoff_request_id",
                "confirmed_no_code_changes",
                "confirmed_by",
                "reason",
            ],
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        human=True,
    ),
    _tool(
        "heartbeat_loop",
        (
            "Record a live heartbeat for one claimed Loop, refresh the "
            "agent progress monitor, and renew its short lease only after "
            "the renewal threshold is reached. Heartbeats do not rewrite "
            "human projection files."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "expected_command_seconds": {
                    "type": "integer",
                    "minimum": 61,
                    "maximum": 1800,
                    "description": (
                        "Optional bounded runtime estimate for a command "
                        "that has already been selected, such as a first "
                        "Maven dependency warmup. The Controller adds a "
                        "short completion buffer; this is not an unlimited "
                        "lease."
                    ),
                },
            },
            required=["root_id", "node_id", "operation_id"],
        ),
    ),
    _tool(
        "report_loop_progress",
        (
            "Report bounded, user-visible business progress for one claimed "
            "Loop without renewing its lease. Human-facing text follows the "
            "user's current language; raw terminal logs and hidden reasoning "
            "are not accepted."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "phase": {
                    "type": "string",
                    "enum": [
                        "STARTING",
                        "INSPECTING",
                        "TESTING",
                        "INVESTIGATING",
                        "FIXING",
                        "REVIEWING",
                        "VERIFYING",
                        "WAITING",
                    ],
                    "description": (
                        "Current user-visible Loop phase; the controller "
                        "renders it as a Chinese label."
                    ),
                },
                "summary_zh": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": (
                        "Concise current progress written for the main Agent "
                        "window in the user's current language."
                    ),
                },
                "completed_zh": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                    },
                    "maxItems": 8,
                    "description": (
                        "Completed milestones in the user's current language."
                    ),
                },
                "next_step_zh": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "description": (
                        "Next action in the user's current language."
                    ),
                },
                "progress_percent": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
                "tests": _object(
                    {
                        "passed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                        "failed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                        "skipped": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                        "total": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1_000_000,
                        },
                    },
                    required=["passed", "failed", "skipped", "total"],
                ),
            },
            required=["root_id", "node_id", "operation_id", "phase", "summary_zh"],
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    ),
    _tool(
        "pause_loop",
        (
            "Pause one claimed Loop with a live lease while preserving its "
            "current attempt and frozen Graph. Resume it explicitly in an "
            "independent receiving context when work can continue."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
            },
            required=["root_id", "node_id", "operation_id"],
        ),
    ),
    _tool(
        "resume_loop",
        (
            "Resume one paused Loop in a receiving independent context and "
            "return it to Graph readiness."
        ),
        _object(
            {"root_id": ROOT_ID, "node_id": NODE_ID},
            required=["root_id", "node_id"],
        ),
    ),
    _tool(
        "record_loop_result",
        (
            "Record a genuine terminal outcome returned by a claimed Loop. "
            "Do not call for a correctable finding or internal Gate failure; "
            "adapt the Loop plan, resolve it, and reevaluate first."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "node_id": NODE_ID,
                "operation_id": OPERATION_ID,
                "outcome": OUTCOME,
                "failure_class": {
                    "type": "string",
                    "enum": [
                        "RETRYABLE_INFRA",
                        "WORKER_LOST",
                        "LOOP_BLOCKED",
                        "REPLAN_REQUIRED",
                        "EXTERNAL_AUTHORITY",
                        "NON_RETRYABLE",
                    ],
                    "description": (
                        "Required when outcome.status is BLOCKED. Select it "
                        "only after the current Loop has no in-scope path to "
                        "progress with its existing authority."
                    ),
                },
            },
            required=[
                "root_id",
                "node_id",
                "operation_id",
                "outcome",
            ],
        ),
    ),
    _tool(
        "record_user_confirmation",
        (
            "Complete the graph after its Review Loop succeeds and the user "
            "accepts. If the clean committed workspace turn was already "
            "released at the confirmation boundary, record this control-plane "
            "decision by root ID without requiring the old Delivery branch to "
            "be checked out."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "confirmed": {
                    "type": "boolean",
                    "const": True,
                    "description": (
                        "JSON Boolean true after explicit user acceptance."
                    ),
                },
                "confirmed_by": _string("Human confirmer identity."),
                "summary": _string("Human completion summary."),
            },
            required=[
                "root_id",
                "confirmed",
                "confirmed_by",
                "summary",
            ],
        ),
    ),
    _tool(
        "cancel_graph_run",
        "Cancel all unfinished nodes in a non-terminal scheduler run.",
        _object(
            {
                "root_id": ROOT_ID,
                "cancelled_by": _string("Human canceller identity."),
                "reason": _string("Cancellation reason."),
            },
            required=["root_id", "cancelled_by", "reason"],
        ),
        human=True,
    ),
    _tool(
        "archive_delivery",
        (
            "Archive a completed Delivery from default workspace discovery "
            "while retaining its SQLite history and detail projections."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
        human=True,
        annotations={"idempotentHint": True},
    ),
)
