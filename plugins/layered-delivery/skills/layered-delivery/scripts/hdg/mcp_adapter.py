from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from . import __version__
from .errors import GatedLoopError
from .host_policy import (
    CODEX_SANDBOX_META_KEY,
    DEFAULT_HOST_POLICY,
    HostCompatibilityPolicy,
    ProjectRootBinding,
)
from .jsonio import redact
from .repository import SchedulerRepository
from .mcp_tools import (
    call_tool,
    tool_definitions,
    validate_tool_arguments,
)


MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PREFERRED_PROTOCOL_VERSION = "2025-11-25"
LEGACY_PROTOCOL_VERSIONS = (
    LEGACY_PREFERRED_PROTOCOL_VERSION,
)
SUPPORTED_PROTOCOL_VERSIONS = (
    MODERN_PROTOCOL_VERSION,
    *LEGACY_PROTOCOL_VERSIONS,
)

PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = (
    "io.modelcontextprotocol/clientCapabilities"
)
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"

DISCOVERY_TTL_MS = 60 * 60 * 1000
TOOLS_TTL_MS = 5 * 60 * 1000
CACHE_SCOPE = "private"

SERVER_INSTRUCTIONS = (
    "Use these tools as an outer Graph scheduler for isolated Deliveries. "
    "Each conversation workspace owns at most one active Delivery; linked "
    "Git worktrees share the primary checkout scheduler while retaining "
    "distinct Delivery workspace identities. A new user requirement "
    "defaults to a new Delivery; never infer Revision continuity merely "
    "because workspace_status returns an unfinished Delivery. Only explicit "
    "user intent to continue that delivery.id authorizes a TASK or Delivery "
    "Revision. When the user supplies an external work-item identifier, put "
    "it in delivery.requirementKey. One requirementKey maps to one stable "
    "delivery.id; common ticket references in the ID or title are also "
    "detected, and a different ID is rejected at preview and final write. "
    "An occupied workspace rejects another initial prepare before writing "
    "and returns host-owned worktreeSetup. Claude Code and Codex automatic "
    "Git Deliveries always use HOST_NATIVE_LINKED_WORKTREE. hostDispatch "
    "creates or reuses one stable Delivery worktree and starts a background "
    "Delivery coordinator while preserving the primary checkout. Claude "
    "does this inside the existing top-level session: it may briefly enter "
    "the host worktree to start the background coordinator, then returns to "
    "primary. It never asks the user to start another Claude session. Codex "
    "uses an environment=worktree project task. The main conversation is "
    "monitor-only and consumes progress from the shared control root. "
    "workspace_status reports "
    "worktreeProvenance with the actual host, topology, selectionSource, "
    "baseRef, baseCommit, baseHeadCommit, and integrationTarget regardless "
    "of which branch originally seeded the worktree. A feature branch name "
    "alone is not proof that the branch belongs to this Delivery. It is "
    "adoptable in a linked worktree "
    "only when no other worktree or Delivery uses it and its base "
    "relationship remains valid. A dirty candidate "
    "requires explicit user attribution of all current changes followed by "
    "an immediate workspace_status call with the exact returned "
    "confirmed_dirty_state_fingerprint; any changed fingerprint invalidates "
    "that confirmation. "
    "The Controller never performs Git writes; hierarchy preview and manual "
    "handoff themselves never create a worktree. workspace_status accepts "
    "an optional host-selected base_ref and otherwise discovers the current "
    "Git mainline from a valid origin/HEAD, then local main, then local "
    "master. It does not enumerate any other fallback branch names. A "
    "detached host-managed worktree returns CREATE_DELIVERY_FEATURE_BRANCH; "
    "after the host creates the local Delivery feature branch, call "
    "workspace_status again and use its suggested frozen gitBinding. One "
    "logical Delivery may "
    "freeze multiple local project workspaces; every writable Git project "
    "uses the same feature branch name while retaining its own immutable "
    "base commit. Exact project IDs require explicit authorization at "
    "freeze. All TASKs in that Delivery share those project branches; TASK "
    "agents never create, bind, or switch internal Git branches. "
    "Each TASK may stage and commit only its own changes on that Delivery "
    "branch when separately authorized; Git index and commit writes in the "
    "shared worktree must not overlap. "
    "Runtime calls verify the worktree, branch, and immutable fork commit "
    "without mutating Git. loop_context exposes runtime-verified paths in "
    "projectScopes and keeps the frozen preview paths separately as "
    "projectScopeAnchors. Receivers must use the verified paths as-is and "
    "must never create, switch, or check out Git branches. This prevents "
    "parallel Deliveries in one repository from moving each other's "
    "worktrees. Claude's fixed plugin project root is only the shared "
    "control root: a PreToolUse Hook injects one-time, tool-bound evidence "
    "for the actual host-observed cwd. Models must never supply or replay "
    "that internal evidence. "
    "Its decomposition is a recursive GROUP/TASK hierarchy: TASK is "
    "the execution leaf, while every GROUP joins and reviews its child "
    "subtree before succeeding. Start with workspace_status, compose the "
    "hierarchy, and call preview_hierarchy. It registers CHOICE_READY and "
    "generates scheduler.db, the root overview, baseline, progress, "
    "acceptance, revisions, and work-item projections before returning "
    "executionChoice. The controller is the sole owner of that interaction. "
    "Its presentationPolicy maps request_user_input for Codex and "
    "AskUserQuestion for Claude. The host must use the mapped native selector "
    "whenever that tool is callable in the current context, consuming "
    "executionChoice.options in order and preserving its IDs, labels, "
    "descriptions, default, and recommendation. Only when the mapped tool is "
    "not callable may the host display executionChoice.markdown exactly. It "
    "must not rewrite that fallback, must not ask the user to type an option, "
    "and must not add another choice. Direct text "
    "continues requirement discussion and a changed requirement must be "
    "previewed again. For a selected button call select_execution_mode once. "
    "AUTOMATIC records that human choice before workspace validation. Claude "
    "and Codex primary checkouts return host-owned stable linked-worktree "
    "background dispatch. The background Delivery coordinator calls "
    "workspace_status and resume_execution_mode without presenting the mode "
    "selector or asking for confirmation again; the main conversation only "
    "monitors and handles final user interaction. "
    "MANUAL creates the portable handoff and returns the exact receiverPrompt "
    "also embedded in that file. It registers HANDOFF_READY without binding "
    "a workspace or starting a Graph run. The receiving CLI must call "
    "start_manual_handoff in the actual development workspace before any "
    "code inspection or implementation; that starts the same frozen Graph "
    "in manual mode. Only TASK implementation Loops use MANUAL claims. TASK, "
    "GROUP, and Delivery Review Loops retain complete host-native automatic "
    "routing, isolated Review, findings closure, and final user confirmation. "
    "Changed "
    "HANDOFF_READY content keeps the same delivery.id and calls "
    "create_manual_handoff with the current revision, "
    "USER_EXPLICIT_SAME_DELIVERY, and a revision reason; it creates the next "
    "immutable manual revision in the same directory. While native "
    "child Agents run in the background, the main orchestrator polls "
    "graph_frontier after exactly the returned progress monitor interval; it "
    "must never use the 90-second first-heartbeat warning as a sleep or "
    "polling interval. A native child completion notification interrupts any "
    "wait and triggers graph_frontier immediately. Show "
    "progressMonitor.markdownTable in user commentary whenever progress or "
    "alerts change. Each claimed STANDARD Loop calls report_loop_progress at "
    "meaningful milestones including code inspection, root-cause "
    "confirmation, edit completion, test start and completion, rework, "
    "Review, and final verification, using concise summaries in the user's "
    "current language. A long-running test or build must use a non-blocking "
    "process or separate monitor so the receiver can heartbeat while it "
    "runs; report and heartbeat immediately before and after it. A host "
    "completion notification is not a heartbeat. LIGHT Loops report findings "
    "and final verification; a "
    "short problem-free LIGHT Loop may report only final verification. "
    "Heartbeat remains lease-only and raw graph events remain diagnostic-only. "
    "Each TASK or Review Loop owns its internal "
    "plan, tests, "
    "gates, rework, and Skill usage. A payload carries goals, explicit "
    "constraints, and known acceptance input rather than a complete "
    "implementation specification. The Loop derives and validates other "
    "in-scope necessary conditions from real code, contracts, and data flow. "
    "An actionable "
    "implementation, test, or Review finding stays inside the current Loop: "
    "adapt the internal plan, resolve it, and reevaluate before returning a "
    "terminal outcome. BLOCKED is only for a concrete condition that leaves "
    "no in-scope path with current authority; REPLAN_REQUIRED is only for a "
    "required change to frozen dependencies, resources, project scope, or "
    "topology. Before final user acceptance, such a change creates the next "
    "immutable revision under the same Delivery ID; the prior run becomes "
    "SUPERSEDED when the new revision is frozen. Shared "
    "skillHints are advisory "
    "runtime preferences: each Loop discovers its actual context and "
    "prioritizes only applicable hints; they are not assigned during "
    "requirement planning. Layered Delivery never recommends or selects a "
    "development model. Automatic execution inherits the current trusted "
    "host model. Manual handoff never selects a receiving Agent/model, never "
    "creates a receiving task, and never initializes a worktree. The "
    "receiver's host becomes known only after it reads the file and calls "
    "start_manual_handoff in the selected workspace. Its outer context only "
    "coordinates: each manual TASK runs in an independent receiving context "
    "that reports its receiver identity and claims with MANUAL provenance; every "
    "subsequent Review returns to automatic host-native planning. Automatic "
    "execution uses Plugin-owned fixed concurrency and quota pause/resume "
    "policies. Adapter trust comes from Plugin registration and model-"
    "external host attestation, never a user file or allowlist. Grok, "
    "DeepSeek, or another Agent integrates by adding one trusted outer "
    "Adapter lifecycle; its models and internal workers remain outside the "
    "Graph contract. For automatic "
    "execution, plan_dispatch_batch atomically reserves each selected Loop "
    "and its cross-Delivery host Agent slot, then returns concurrent outer-"
    "receiver assignments with CURRENT_HOST_INHERIT model policy. A second "
    "dispatcher sees "
    "WAIT_FOR_DISPATCH_RECEIVER and cannot reserve or launch the same Loop. "
    "It never analyzes Loop payloads, starts Agents, or claims Loops. The "
    "native host creates each outer receiver only after reservation; that "
    "receiver claims with its Adapter/Agent identity, host-attested receiving "
    "context ID, one-time "
    "receiver attestation, HOST_NATIVE transport, reservation ID, and the "
    "verified decision fingerprint. Only that outer receiver may heartbeat, "
    "report progress, or record the Loop result; internal workers have no "
    "Graph authority. When no Loop is claimed and prior Loop success has "
    "reached the next frontier, the same trusted Adapter may rotate the "
    "orchestrator root to a new host session for the next Review; active "
    "claims and live credentials still forbid takeover. The result may "
    "include display-only workerTelemetry by "
    "phase, with unknown Agent/model/effort values left unreported. Never "
    "spawn an autonomous CLI, subprocess, or companion script such as "
    "codex-companion to satisfy an assignment. Such a route stays unclaimed and is handed "
    "off manually. prepare_delivery_revision requires explicit same-"
    "Delivery or recorded replan continuity, only stages a candidate, "
    "and leaves the current run active until freeze. It "
    "does not require a generic host approval prompt; the user's automatic "
    "freeze or manual handoff-file choice is the single business "
    "confirmation for that revision. A model-external host adapter "
    "observing hard 429 quota "
    "exhaustion invokes the private capacity callback with the structured "
    "provider reset time, cancels "
    "recurring monitors, and keeps only one native wake after reset. The "
    "scheduler treats Loop payload and result "
    "as opaque and accepts only standard Loop outcomes. resourceClaims "
    "are exact cross-Delivery scheduling locks, not file scopes. Every TASK "
    "requirement starts frozen. An explicitly authorized, not-yet-started "
    "TASK may be unfrozen and refrozen with a revised title, summary, and "
    "opaque payload; topology, dependencies, and resource claims remain "
    "Delivery-frozen. Final completion still "
    "requires explicit user confirmation. External Git and publication "
    "actions remain outside this server."
)

_USER_INTERACTION_TOOLS = frozenset(
    tool["name"]
    for tool in tool_definitions()
    if tool.get("_meta", {}).get("anthropic/requiresUserInteraction") is True
)

@dataclass
class McpConnection:
    """Transport connection plus legacy-only handshake state."""

    project_root: ProjectRootBinding
    host_policy: HostCompatibilityPolicy = DEFAULT_HOST_POLICY
    legacy_initialize_requested: bool = False
    legacy_initialized: bool = False
    legacy_client_info: dict[str, object] | None = None
    trusted_host_adapter: str | None = None


@dataclass(frozen=True)
class ModernRequestContext:
    protocol_version: str
    client_capabilities: Mapping[str, object]
    client_info: Mapping[str, object] | None
    meta: Mapping[str, object]


def _server_info() -> dict[str, str]:
    return {
        "name": "layered-delivery",
        "version": __version__,
    }


def _server_capabilities() -> dict[str, object]:
    return {
        "tools": {"listChanged": False},
        "experimental": {
            CODEX_SANDBOX_META_KEY: {},
        },
    }


def _result_meta() -> dict[str, object]:
    return {
        SERVER_INFO_META_KEY: _server_info(),
    }


def _rpc_error(
    request_id: object,
    code: int,
    message: str,
    *,
    data: object | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if data is not None:
        error["data"] = redact(data)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error,
    }


def _complete_result(result: Mapping[str, object]) -> dict[str, object]:
    completed = dict(result)
    completed["resultType"] = "complete"
    response_meta = completed.get("_meta")
    if isinstance(response_meta, dict):
        merged_meta = dict(response_meta)
        merged_meta.update(_result_meta())
    else:
        merged_meta = _result_meta()
    completed["_meta"] = merged_meta
    return completed


def _rpc_result(
    request_id: object,
    result: Mapping[str, object],
    *,
    modern: bool,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": (
            _complete_result(result)
            if modern
            else dict(result)
        ),
    }


def _tool_result(
    payload: dict[str, Any],
    *,
    is_error: bool,
    modern: bool,
) -> dict[str, Any]:
    safe_payload = redact(payload)
    business_result = safe_payload.get("result")
    progress_monitor = (
        business_result.get("progressMonitor")
        if isinstance(business_result, dict)
        else None
    )
    markdown_table = (
        progress_monitor.get("markdownTable")
        if isinstance(progress_monitor, dict)
        else None
    )
    if not is_error and isinstance(markdown_table, str) and markdown_table:
        alerts = progress_monitor.get("alerts", [])
        alert_lines = [
            f"- ⚠️ {item['messageZh']}"
            for item in alerts
            if isinstance(item, dict)
            and isinstance(item.get("messageZh"), str)
        ]
        text = "\n".join(
            [
                "## 后台执行进度",
                "",
                *alert_lines,
                *([""] if alert_lines else []),
                markdown_table,
                "",
                (
                    "主 Agent 应按建议间隔继续刷新；原始事件仅用于"
                    "展开诊断。"
                ),
            ]
        )
    else:
        text = json.dumps(
            safe_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_payload,
        "isError": is_error,
    }
    return _complete_result(result) if modern else result


def _gated_error_tool_result(
    error: GatedLoopError,
    *,
    modern: bool,
) -> dict[str, Any]:
    return _tool_result(
        {
            "ok": False,
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            },
        },
        is_error=True,
        modern=modern,
    )


def _invalid_params(
    request_id: object,
    error: GatedLoopError | None = None,
) -> dict[str, Any]:
    data = None
    if error is not None:
        data = {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        }
    return _rpc_error(
        request_id,
        -32602,
        "Invalid params",
        data=data,
    )


def _unsupported_protocol_version(
    request_id: object,
    requested: str,
) -> dict[str, Any]:
    return _rpc_error(
        request_id,
        -32022,
        "Unsupported protocol version",
        data={
            "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
            "requested": requested,
        },
    )


def _valid_client_info(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("name"), str):
        return False
    if not isinstance(value.get("version"), str):
        return False
    for optional_string in (
        "title",
        "description",
        "websiteUrl",
    ):
        if (
            optional_string in value
            and not isinstance(value[optional_string], str)
        ):
            return False
    return True


def _has_modern_metadata(params: object) -> bool:
    if not isinstance(params, dict):
        return False
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return False
    return (
        PROTOCOL_VERSION_META_KEY in meta
        or CLIENT_CAPABILITIES_META_KEY in meta
    )


def _is_modern_request(
    method: str,
    params: object,
    connection: McpConnection,
) -> bool:
    if method == "server/discover" or _has_modern_metadata(params):
        return True
    if method in {"initialize", "ping"}:
        return False
    return not connection.legacy_initialized


def _modern_request_context(
    params: Mapping[str, object],
    request_id: object,
) -> tuple[ModernRequestContext | None, dict[str, Any] | None]:
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None, _invalid_params(request_id)
    protocol_version = meta.get(PROTOCOL_VERSION_META_KEY)
    client_capabilities = meta.get(CLIENT_CAPABILITIES_META_KEY)
    client_info = meta.get(CLIENT_INFO_META_KEY)
    if (
        not isinstance(protocol_version, str)
        or not isinstance(client_capabilities, dict)
        or (
            client_info is not None
            and not _valid_client_info(client_info)
        )
    ):
        return None, _invalid_params(request_id)
    if protocol_version != MODERN_PROTOCOL_VERSION:
        return (
            None,
            _unsupported_protocol_version(
                request_id,
                protocol_version,
            ),
        )
    return (
        ModernRequestContext(
            protocol_version=protocol_version,
            client_capabilities=client_capabilities,
            client_info=client_info,
            meta=meta,
        ),
        None,
    )


def _validate_list_params(
    params: Mapping[str, object],
) -> bool:
    allowed = {"cursor", "_meta"}
    if set(params) - allowed:
        return False
    cursor = params.get("cursor")
    request_meta = params.get("_meta")
    return (
        (cursor is None or isinstance(cursor, str))
        and (
            request_meta is None
            or isinstance(request_meta, dict)
        )
    )


def _validate_call_params(
    params: Mapping[str, object],
    *,
    modern: bool,
) -> tuple[str, dict[str, Any]] | None:
    allowed = {"name", "arguments", "_meta"}
    if modern:
        allowed.update({"inputResponses", "requestState"})
    if set(params) - allowed:
        return None
    name = params.get("name")
    arguments = params.get("arguments", {})
    request_meta = params.get("_meta")
    input_responses = params.get("inputResponses")
    request_state = params.get("requestState")
    if (
        not isinstance(name, str)
        or not isinstance(arguments, dict)
        or (
            request_meta is not None
            and not isinstance(request_meta, dict)
        )
        or (
            input_responses is not None
            and not isinstance(input_responses, dict)
        )
        or (
            request_state is not None
            and not isinstance(request_state, str)
        )
    ):
        return None
    return name, arguments


def _call_scheduler_tool(
    *,
    request_id: object,
    params: Mapping[str, object],
    connection: McpConnection,
    client_info: Mapping[str, object] | None,
    modern: bool,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    validated_call = _validate_call_params(
        params,
        modern=modern,
    )
    if validated_call is None:
        return _invalid_params(request_id)
    name, arguments = validated_call
    try:
        validate_tool_arguments(name, arguments)
    except GatedLoopError as error:
        if error.code == "MCP_TOOL_UNKNOWN":
            return _invalid_params(request_id, error)
        return _rpc_result(
            request_id,
            _gated_error_tool_result(error, modern=modern),
            modern=False,
        )
    try:
        if not modern:
            connection.host_policy.ensure_user_interaction_tool_supported(
                requires_user_interaction=name in _USER_INTERACTION_TOOLS,
                client_info=client_info,
            )
        root_resolution = connection.project_root.resolve_request(
            params.get("_meta"),
            stateless=modern,
            require_sandbox_metadata=True,
        )
        tool_arguments = dict(arguments)
        workspace_attestation = tool_arguments.pop(
            "_host_workspace_attestation",
            None,
        )
        workspace_root = root_resolution.workspace_root
        if workspace_attestation is not None:
            if (
                connection.trusted_host_adapter != "claude-code"
                or not isinstance(workspace_attestation, str)
            ):
                raise GatedLoopError(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_UNTRUSTED",
                    "Only the Claude Code host Hook may attest a workspace",
                )
            workspace_root = SchedulerRepository(
                root_resolution.project_root
            ).consume_host_workspace_attestation(
                workspace_attestation,
                host_adapter_id="claude-code",
                tool_name=name,
            )
        business_result = call_tool(
            name,
            tool_arguments,
            root=root_resolution.project_root,
            workspace_root=workspace_root,
            explicit_dogfood=explicit_dogfood,
            client_info=(dict(client_info) if client_info else None),
            trusted_host_adapter=connection.trusted_host_adapter,
        )
        payload = {
            "ok": True,
            "result": business_result,
        }
        return _rpc_result(
            request_id,
            _tool_result(
                payload,
                is_error=False,
                modern=modern,
            ),
            modern=False,
        )
    except GatedLoopError as error:
        return _rpc_result(
            request_id,
            _gated_error_tool_result(error, modern=modern),
            modern=False,
        )
    except Exception:
        return _rpc_result(
            request_id,
            _tool_result(
                {
                    "ok": False,
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Unexpected error",
                        "details": {},
                    },
                },
                is_error=True,
                modern=modern,
            ),
            modern=False,
        )


def _handle_modern_request(
    *,
    request_id: object,
    method: str,
    params: Mapping[str, object],
    context: ModernRequestContext,
    connection: McpConnection,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    if method == "server/discover":
        if set(params) - {"_meta"}:
            return _invalid_params(request_id)
        return _rpc_result(
            request_id,
            {
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": _server_capabilities(),
                "instructions": SERVER_INSTRUCTIONS,
                "ttlMs": DISCOVERY_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            },
            modern=True,
        )

    if method == "tools/list":
        if not _validate_list_params(params):
            return _invalid_params(request_id)
        return _rpc_result(
            request_id,
            {
                "tools": tool_definitions(),
                "ttlMs": TOOLS_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            },
            modern=True,
        )

    if method == "tools/call":
        return _call_scheduler_tool(
            request_id=request_id,
            params=params,
            connection=connection,
            client_info=context.client_info,
            modern=True,
            explicit_dogfood=explicit_dogfood,
        )

    return _rpc_error(request_id, -32601, "Method not found")


def _handle_legacy_request(
    *,
    request_id: object,
    method: str,
    params: Mapping[str, object],
    connection: McpConnection,
    explicit_dogfood: bool,
) -> dict[str, Any]:
    if method == "initialize":
        if connection.legacy_initialize_requested:
            return _rpc_error(
                request_id,
                -32600,
                "Already initialized",
            )
        protocol_version = params.get("protocolVersion")
        capabilities = params.get("capabilities")
        client_info = params.get("clientInfo")
        request_meta = params.get("_meta")
        if (
            not isinstance(protocol_version, str)
            or not isinstance(capabilities, dict)
            or not _valid_client_info(client_info)
            or (
                request_meta is not None
                and not isinstance(request_meta, dict)
            )
        ):
            return _invalid_params(request_id)
        negotiated = (
            protocol_version
            if protocol_version in LEGACY_PROTOCOL_VERSIONS
            else LEGACY_PREFERRED_PROTOCOL_VERSION
        )
        connection.legacy_initialize_requested = True
        connection.legacy_client_info = dict(client_info)
        return _rpc_result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": _server_capabilities(),
                "serverInfo": _server_info(),
                "instructions": SERVER_INSTRUCTIONS,
            },
            modern=False,
        )

    if method == "ping":
        return _rpc_result(request_id, {}, modern=False)

    if not connection.legacy_initialized:
        return _rpc_error(
            request_id,
            -32002,
            "Server not initialized",
        )

    if method == "tools/list":
        if not _validate_list_params(params):
            return _invalid_params(request_id)
        return _rpc_result(
            request_id,
            {"tools": tool_definitions()},
            modern=False,
        )

    if method == "tools/call":
        return _call_scheduler_tool(
            request_id=request_id,
            params=params,
            connection=connection,
            client_info=connection.legacy_client_info,
            modern=False,
            explicit_dogfood=explicit_dogfood,
        )

    return _rpc_error(request_id, -32601, "Method not found")


def handle_message(
    message: object,
    *,
    connection: McpConnection,
    explicit_dogfood: bool = False,
) -> dict[str, Any] | None:
    """Adapt one decoded MCP JSON-RPC message to the shared controller."""

    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid Request")
    request_id = message.get("id")
    if (
        message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
        or (
            "id" in message
            and (
                request_id is None
                or isinstance(request_id, bool)
                or not isinstance(request_id, (str, int))
            )
        )
    ):
        return _rpc_error(
            request_id if isinstance(request_id, (str, int)) else None,
            -32600,
            "Invalid Request",
        )

    method = message["method"]
    is_notification = "id" not in message
    params = message.get("params", {})
    if is_notification:
        if (
            method == "notifications/initialized"
            and connection.legacy_initialize_requested
            and not _has_modern_metadata(params)
            and (params is None or isinstance(params, dict))
        ):
            connection.legacy_initialized = True
        return None

    if not isinstance(params, dict):
        return _invalid_params(request_id)

    modern = _is_modern_request(method, params, connection)
    if modern:
        context, error = _modern_request_context(
            params,
            request_id,
        )
        if error is not None:
            return error
        assert context is not None
        return _handle_modern_request(
            request_id=request_id,
            method=method,
            params=params,
            context=context,
            connection=connection,
            explicit_dogfood=explicit_dogfood,
        )

    return _handle_legacy_request(
        request_id=request_id,
        method=method,
        params=params,
        connection=connection,
        explicit_dogfood=explicit_dogfood,
    )


__all__ = (
    "CLIENT_CAPABILITIES_META_KEY",
    "CLIENT_INFO_META_KEY",
    "LEGACY_PREFERRED_PROTOCOL_VERSION",
    "LEGACY_PROTOCOL_VERSIONS",
    "MODERN_PROTOCOL_VERSION",
    "McpConnection",
    "PROTOCOL_VERSION_META_KEY",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "handle_message",
)
