from __future__ import annotations

import copy
import re
from typing import Any

from .constants import MAX_IDENTIFIER_LENGTH, MAX_MCP_EVENT_PAGE_SIZE
from .errors import GatedLoopError
from .evidence import safe_work_item_id
from .operations import OperationContext, execute_operation
from .payloads import (
    MAX_PAYLOAD_BYTES,
    MAX_PAYLOAD_CHUNK_BYTES,
    MAX_UPLOAD_ID_LENGTH,
    MAX_PAYLOAD_CHUNKS,
    PAYLOAD_TARGET_ARGUMENTS,
    resolve_payload_argument,
)


SAFE_IDENTIFIER_PATTERN = (
    rf"^[a-z0-9][a-z0-9._-]{{0,{MAX_IDENTIFIER_LENGTH - 1}}}$"
)
HOST_RUNTIME_PATTERN = r"^[a-z][a-z0-9._-]{0,63}$"
FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
SKILL_NAME_PATTERN = r"^[a-z0-9][a-z0-9._:-]{0,127}$"


def _string(
    description: str,
    *,
    pattern: str | None = None,
    enum: list[str] | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "string",
        "description": description,
    }
    if pattern is not None:
        schema["pattern"] = pattern
    if enum is not None:
        schema["enum"] = enum
    if min_length is not None:
        schema["minLength"] = min_length
    if max_length is not None:
        schema["maxLength"] = max_length
    return schema


def _object(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
    }


def _integer(
    description: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "integer",
        "description": description,
    }
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


ITEM_ID = _string(
    "Frozen work-item identifier in the bound project.",
    pattern=SAFE_IDENTIFIER_PATTERN,
    max_length=MAX_IDENTIFIER_LENGTH,
)
OWNER = _string(
    "Lowercase Agent or worker identifier.",
    pattern=SAFE_IDENTIFIER_PATTERN,
    max_length=MAX_IDENTIFIER_LENGTH,
)
OPERATION_ID = _string(
    "Unique operation identifier for the current graph run.",
    pattern=SAFE_IDENTIFIER_PATTERN,
    max_length=MAX_IDENTIFIER_LENGTH,
)
HIERARCHY_FINGERPRINT = _string(
    "SHA-256 fingerprint of the reviewed hierarchy.",
    pattern=FINGERPRINT_PATTERN,
)
BASELINE_FINGERPRINT = _string(
    "SHA-256 fingerprint of the frozen work-item baseline.",
    pattern=FINGERPRINT_PATTERN,
)
UPLOAD_ID = _string(
    "Unique lowercase staged-payload upload identifier.",
    pattern=SAFE_IDENTIFIER_PATTERN,
    min_length=1,
    max_length=MAX_UPLOAD_ID_LENGTH,
)
GENERATION_ID = _string(
    "Server-generated staged-payload generation returned by begin.",
    pattern=FINGERPRINT_PATTERN.replace("{64}", "{32}"),
    min_length=32,
    max_length=32,
)


def _payload_capable_object(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "payloadRef": {
                        "type": "object",
                        "properties": {
                            "uploadId": copy.deepcopy(UPLOAD_ID),
                            "generationId": copy.deepcopy(GENERATION_ID),
                            "sha256": _string(
                                "SHA-256 of the finalized payload.",
                                pattern=FINGERPRINT_PATTERN,
                                min_length=64,
                                max_length=64,
                            ),
                            "sizeBytes": _integer(
                                "Exact UTF-8 byte length of the payload.",
                                minimum=1,
                                maximum=MAX_PAYLOAD_BYTES,
                            ),
                        },
                        "required": [
                            "uploadId",
                            "generationId",
                            "sha256",
                            "sizeBytes",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": ["payloadRef"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "not": {"required": ["payloadRef"]},
            },
        ],
    }


EVIDENCE = _payload_capable_object(
    "Complete evidence artifact matching the current evidence contract, "
    "or an exact READY payloadRef bound to this tool."
)


def _input_schema(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


ERROR_OUTPUT = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "details": {"type": "object"},
    },
    "required": ["code", "message", "details"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "ok": {"const": True},
                "result": {},
            },
            "required": ["ok", "result"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "ok": {"const": False},
                "error": ERROR_OUTPUT,
            },
            "required": ["ok", "error"],
            "additionalProperties": False,
        },
    ],
}


def _tool(
    name: str,
    title: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    *,
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool = False,
    requires_user_interaction: bool = False,
) -> dict[str, Any]:
    definition = {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": _input_schema(properties),
        "outputSchema": OUTPUT_SCHEMA,
        "annotations": {
            "title": title,
            "readOnlyHint": read_only,
            "destructiveHint": (
                False
                if read_only
                else True
                if destructive is None
                else destructive
            ),
            "idempotentHint": idempotent,
            "openWorldHint": False,
        },
    }
    if requires_user_interaction:
        definition["_meta"] = {
            "anthropic/requiresUserInteraction": True,
        }
    return definition


_TOOLS = (
    _tool(
        "workspace_status",
        "Read workspace governance status",
        (
            "Classify the bound project as ABSENT, STAGING_ONLY, or ACTIVE "
            "without requiring a work-item or upload identifier."
        ),
        {},
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "begin_payload_upload",
        "Begin staged payload upload",
        (
            "Create an expiring, target-bound manifest for a lossless JSON "
            "payload that is too large for one direct MCP call."
        ),
        {
            "upload_id": UPLOAD_ID,
            "target_tool": _string(
                "Original domain tool that will consume the finalized payload.",
                enum=sorted(PAYLOAD_TARGET_ARGUMENTS),
            ),
            "total_chunks": _integer(
                "Exact number of chunks that will be uploaded.",
                minimum=1,
                maximum=MAX_PAYLOAD_CHUNKS,
            ),
        },
        idempotent=True,
    ),
    _tool(
        "append_payload_chunk",
        "Append staged payload chunk",
        (
            "Store one UTF-8 text chunk with its exact index and SHA-256 "
            "digest; identical retries are idempotent."
        ),
        {
            "upload_id": UPLOAD_ID,
            "generation_id": GENERATION_ID,
            "chunk_index": _integer(
                "Zero-based position in the declared payload manifest.",
                minimum=0,
                maximum=MAX_PAYLOAD_CHUNKS - 1,
            ),
            "data": _string(
                "One non-empty slice of the exact serialized JSON text; "
                "the Server computes its UTF-8 byte length and SHA-256.",
                min_length=1,
                max_length=MAX_PAYLOAD_CHUNK_BYTES,
            ),
        },
        destructive=False,
        idempotent=True,
    ),
    _tool(
        "finalize_payload_upload",
        "Finalize staged payload upload",
        (
            "Verify every chunk, total byte length, full SHA-256 digest, "
            "strict JSON syntax, and structure limits, then return a compact "
            "payloadRef without returning the payload."
        ),
        {
            "upload_id": UPLOAD_ID,
            "generation_id": GENERATION_ID,
        },
        idempotent=True,
    ),
    _tool(
        "payload_upload_status",
        "Read staged payload status",
        (
            "Read compact manifest progress and the READY payloadRef without "
            "returning staged content."
        ),
        {
            "upload_id": UPLOAD_ID,
            "generation_id": GENERATION_ID,
        },
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "abort_payload_upload",
        "Abort staged payload upload",
        "Delete one staged upload and all of its chunks; repeated aborts are safe.",
        {
            "upload_id": UPLOAD_ID,
            "generation_id": GENERATION_ID,
        },
        destructive=True,
        idempotent=True,
    ),
    _tool(
        "prepare_hierarchy",
        "Prepare delivery hierarchy",
        "Validate and persist one complete reviewable requirement hierarchy before it is frozen.",
        {
            "hierarchy": _payload_capable_object(
                "Complete schema-v3 Task, Capability, or Delivery hierarchy, "
                "where each definition may omit requiredSkills or use an "
                "empty array when no Skill gate is required; "
                "for a genuinely oversized hierarchy, pass an exact READY "
                "payloadRef bound to this tool."
            ),
            "host_runtime": _string(
                "Lowercase host Agent runtime identifier.",
                pattern=HOST_RUNTIME_PATTERN,
            ),
        },
    ),
    _tool(
        "freeze_hierarchy",
        "Freeze reviewed hierarchy",
        "Irreversibly freeze the reviewed hierarchy and selected active or manual development mode.",
        {
            "item_id": ITEM_ID,
            "expected_hierarchy_fingerprint": HIERARCHY_FINGERPRINT,
            "development_mode": _string(
                (
                    "Development mode explicitly chosen by the user; this "
                    "choice is also the one-time authorization to freeze the "
                    "reviewed hierarchy, so do not ask for another approval."
                ),
                enum=["active", "manual"],
            ),
        },
        destructive=True,
    ),
    _tool(
        "ready_tasks",
        "List ready tasks",
        "List executable Task identifiers in the requested root or subtree.",
        {"item_id": ITEM_ID},
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "graph_status",
        "Read graph status",
        "Read the current execution and governance graph status for a root or subtree.",
        {"item_id": ITEM_ID},
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "graph_frontier",
        "Read graph frontier",
        "Read the authoritative next actions, dispatch plan, wake time, and blockers.",
        {"item_id": ITEM_ID},
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "graph_events",
        "List graph events",
        "Read one bounded page of append-only graph events for a root or subtree.",
        {
            "item_id": ITEM_ID,
            "after_event_id": _integer(
                "Return events whose eventId is greater than this cursor.",
                minimum=0,
            ),
            "limit": _integer(
                "Maximum events in this response page.",
                minimum=1,
                maximum=MAX_MCP_EVENT_PAGE_SIZE,
            ),
        },
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "graph_replay",
        "Replay graph state",
        "Reconstruct and verify graph state from persisted events without mutating it.",
        {"item_id": ITEM_ID},
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "rebuild_graph_run",
        "Rebuild graph run",
        "Rebuild a damaged graph run from its frozen hierarchy after explicit recovery authorization.",
        {"item_id": ITEM_ID},
        destructive=True,
        requires_user_interaction=True,
    ),
    _tool(
        "advance_graph",
        "Advance graph",
        "Apply the graph runtime's current deterministic transition and recovery decisions.",
        {"item_id": ITEM_ID},
    ),
    _tool(
        "cancel_graph_run",
        "Cancel graph run",
        "Cancel the active graph run for the requested root or subtree after explicit authorization.",
        {"item_id": ITEM_ID},
        destructive=True,
        requires_user_interaction=True,
    ),
    _tool(
        "task_context",
        "Read task context",
        "Read a non-claiming diagnostic Task context; use dispatch_task to start work.",
        {"item_id": ITEM_ID},
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "evidence_contract",
        "Read evidence contract",
        "Read the exact current evidence template and constraints for one work item.",
        {
            "item_id": ITEM_ID,
            "contract_kind": _string(
                "Evidence contract category.",
                enum=[
                    "result",
                    "gate",
                    "remediation",
                    "review",
                    "confirmation",
                ],
            ),
        },
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "record_skill_activation",
        "Record native Skill activation",
        (
            "The frozen requiredSkills contract already authorizes execution. "
            "The execution adapter automatically invokes each entry through "
            "the current Agent's native Skill mechanism, then binds "
            "its actual execution host and native invocation identity to the "
            "current graph node attempt as HOST_NATIVE_SKILL without another "
            "user prompt. The planning host is audit-only. Reading or loading "
            "SKILL.md without "
            "the automatic invocation is not a valid activation."
        ),
        {
            "item_id": ITEM_ID,
            "stage": _string(
                "Frozen lifecycle stage for this invocation.",
                enum=["DEVELOPMENT", "GATE", "FINAL_REVIEW"],
            ),
            "skill_name": _string(
                "Exact arbitrary Skill catalog name frozen in requiredSkills.",
                pattern=SKILL_NAME_PATTERN,
                max_length=128,
            ),
            "activation": _object(
                "Strict host-native activation credential with sessionId, "
                "executorId, executionId, nativeInvocationId, mechanism, "
                "status, and concrete summary."
            ),
        },
    ),
    _tool(
        "record_skill_conformance",
        "Record Skill conformance",
        (
            "Bind structured completion checks to one native Skill "
            "activation receipt from the same execution host. A successful "
            "stage requires PASS for every frozen required Skill."
        ),
        {
            "item_id": ITEM_ID,
            "activation_receipt_id": _string(
                "Graph event hash returned by record_skill_activation.",
                pattern=FINGERPRINT_PATTERN,
                min_length=64,
                max_length=64,
            ),
            "conformance": _object(
                "Strict conformance result containing status, concrete "
                "summary, and nonempty named checks with evidence."
            ),
        },
    ),
    _tool(
        "dispatch_task",
        "Dispatch task",
        "Claim a ready Task for a worker and persist its isolated execution context and handoff.",
        {
            "item_id": ITEM_ID,
            "owner": OWNER,
            "operation_id": OPERATION_ID,
        },
    ),
    _tool(
        "heartbeat_task",
        "Heartbeat task",
        "Renew the active Task claim before its lease expires.",
        {
            "item_id": ITEM_ID,
            "operation_id": OPERATION_ID,
        },
    ),
    _tool(
        "pause_task",
        "Pause task",
        "Pause an actively claimed Task while preserving its fenced operation.",
        {
            "item_id": ITEM_ID,
            "operation_id": OPERATION_ID,
        },
    ),
    _tool(
        "resume_task",
        "Resume task",
        "Resume a paused Task so the graph can schedule it again.",
        {"item_id": ITEM_ID},
    ),
    _tool(
        "claim_task",
        "Recover task claim",
        "Recovery-only claim operation that does not build a new development handoff.",
        {
            "item_id": ITEM_ID,
            "owner": OWNER,
            "operation_id": OPERATION_ID,
        },
    ),
    _tool(
        "task_result",
        "Record task result",
        "Record a complete IMPLEMENTED or BLOCKED result artifact for the active operation.",
        {
            "item_id": ITEM_ID,
            "operation_id": OPERATION_ID,
            "status": _string(
                "Result status.",
                enum=["IMPLEMENTED", "BLOCKED"],
            ),
            "evidence": EVIDENCE,
        },
    ),
    _tool(
        "remediate_task",
        "Authorize validation remediation",
        "Record same-requirement remediation evidence for previously unauthorized validation changes.",
        {
            "item_id": ITEM_ID,
            "expected_baseline_fingerprint": BASELINE_FINGERPRINT,
            "evidence": EVIDENCE,
        },
    ),
    _tool(
        "retry_item",
        "Retry work item",
        "Invalidate the failed attempt and create the next budgeted attempt for the same frozen baseline.",
        {
            "item_id": ITEM_ID,
            "expected_baseline_fingerprint": BASELINE_FINGERPRINT,
        },
        destructive=True,
    ),
    _tool(
        "gate_item",
        "Record work-item gate",
        "Record PASS or FAIL gate evidence for a Task or aggregate work item.",
        {
            "item_id": ITEM_ID,
            "status": _string(
                "Gate verdict.",
                enum=["PASS", "FAIL"],
            ),
            "evidence": EVIDENCE,
        },
    ),
    _tool(
        "accept_item",
        "Accept work item",
        "Validate and record the work-item acceptance artifact after its implementation gate.",
        {
            "item_id": ITEM_ID,
            "evidence": EVIDENCE,
        },
    ),
    _tool(
        "record_independent_review_pass",
        "Record independent review pass",
        "Record a fresh read-only independent review PASS using its complete review artifact.",
        {
            "item_id": ITEM_ID,
            "evidence": EVIDENCE,
        },
    ),
    _tool(
        "record_independent_review_blocked",
        "Record blocked independent review",
        (
            "Persist that a frozen FINAL_REVIEW Skill is unavailable, "
            "including exact BLOCKED skillUsage and a concrete reason."
        ),
        {
            "item_id": ITEM_ID,
            "evidence": EVIDENCE,
        },
    ),
    _tool(
        "record_human_review_acceptance",
        "Record human review acceptance",
        "Record that a human explicitly accepted the review, with a complete human-review artifact.",
        {
            "item_id": ITEM_ID,
            "evidence": EVIDENCE,
        },
        requires_user_interaction=True,
    ),
    _tool(
        "record_user_confirmation",
        "Record final user confirmation",
        "Complete the delivery only after the user explicitly confirms the root acceptance report.",
        {
            "item_id": ITEM_ID,
            "evidence": EVIDENCE,
        },
        destructive=True,
        requires_user_interaction=True,
    ),
    _tool(
        "refresh_projections",
        "Refresh projections",
        "Rebuild all human-readable projections from authoritative SQLite state.",
        {},
        idempotent=True,
    ),
    _tool(
        "record_interaction",
        "Record interaction",
        "Append a structured work-item interaction to the governance audit log.",
        {
            "item_id": ITEM_ID,
            "interaction": _object(
                "Complete structured interaction record."
            ),
        },
    ),
    _tool(
        "interaction_log",
        "Read interaction log",
        "Read one bounded page of persisted interaction records.",
        {
            "item_id": ITEM_ID,
            "after_event_id": _integer(
                "Return interactions whose eventId is greater than this cursor.",
                minimum=0,
            ),
            "limit": _integer(
                "Maximum interactions in this response page.",
                minimum=1,
                maximum=MAX_MCP_EVENT_PAGE_SIZE,
            ),
        },
        read_only=True,
        idempotent=True,
    ),
)

_TOOLS_BY_NAME = {tool["name"]: tool for tool in _TOOLS}
_CONFIRMED_OPERATIONS = {
    "freeze_hierarchy",
    "rebuild_graph_run",
    "cancel_graph_run",
}


def tool_definitions() -> list[dict[str, Any]]:
    """Return an isolated MCP tool catalog suitable for tools/list."""

    return copy.deepcopy(list(_TOOLS))


def validate_tool_arguments(name: str, arguments: object) -> dict[str, Any]:
    """Validate the strict top-level schema used by one MCP tool."""

    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise GatedLoopError(
            "MCP_TOOL_UNKNOWN",
            f"Unknown layered-delivery MCP tool: {name}",
        )
    if not isinstance(arguments, dict):
        raise GatedLoopError(
            "MCP_ARGUMENTS_INVALID",
            "Tool arguments must be a JSON object",
        )

    schema = tool["inputSchema"]
    properties = schema["properties"]
    missing = sorted(set(schema["required"]) - set(arguments))
    unexpected = sorted(set(arguments) - set(properties))
    if missing or unexpected:
        raise GatedLoopError(
            "MCP_ARGUMENTS_INVALID",
            "Tool arguments do not match the declared schema",
            details={
                "missing": missing,
                "unexpected": unexpected,
            },
        )

    for key, property_schema in properties.items():
        value = arguments[key]
        expected_type = property_schema["type"]
        if expected_type == "string" and not isinstance(value, str):
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} must be a string",
                details={"field": key},
            )
        if expected_type == "object" and not isinstance(value, dict):
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} must be a JSON object",
                details={"field": key},
            )
        if expected_type == "integer" and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} must be an integer",
                details={"field": key},
            )
        if "enum" in property_schema and value not in property_schema["enum"]:
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} is not one of the allowed values",
                details={
                    "field": key,
                    "allowed": list(property_schema["enum"]),
                },
            )
        min_length = property_schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} is shorter than the allowed minimum",
                details={"field": key, "minLength": min_length},
            )
        max_length = property_schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} exceeds the allowed string length",
                details={"field": key, "maxLength": max_length},
            )
        pattern = property_schema.get("pattern")
        if pattern is not None and (
            not isinstance(value, str) or re.fullmatch(pattern, value) is None
        ):
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} has an invalid identifier format",
                details={"field": key},
            )
        minimum = property_schema.get("minimum")
        if minimum is not None and value < minimum:
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} is below the allowed minimum",
                details={"field": key, "minimum": minimum},
            )
        maximum = property_schema.get("maximum")
        if maximum is not None and value > maximum:
            raise GatedLoopError(
                "MCP_ARGUMENT_INVALID",
                f"{key} exceeds the allowed maximum",
                details={"field": key, "maximum": maximum},
            )

    item_id = arguments.get("item_id")
    if item_id is not None and not safe_work_item_id(item_id):
        raise GatedLoopError(
            "MCP_ARGUMENT_INVALID",
            "item_id must be a safe work-item identifier",
            details={"field": "item_id"},
        )
    return dict(arguments)


def call_tool(
    name: str,
    arguments: object,
    *,
    root: str,
    execution_host_runtime: str | None = None,
    explicit_dogfood: bool = False,
) -> Any:
    """Invoke one validated tool against the server's fixed project root."""

    validated = validate_tool_arguments(name, arguments)
    internal_arguments = dict(validated)
    payload_argument = PAYLOAD_TARGET_ARGUMENTS.get(name)
    if payload_argument is not None:
        internal_arguments[payload_argument] = resolve_payload_argument(
            root=root,
            target_tool=name,
            target_argument=payload_argument,
            value=internal_arguments[payload_argument],
            explicit_dogfood=explicit_dogfood,
        )
    if name in _CONFIRMED_OPERATIONS:
        internal_arguments["confirmed"] = True
    return execute_operation(
        name,
        internal_arguments,
        context=OperationContext(
            root=root,
            explicit_dogfood=explicit_dogfood,
            execution_host_runtime=execution_host_runtime,
        ),
    )
