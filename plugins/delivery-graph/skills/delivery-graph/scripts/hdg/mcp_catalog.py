from __future__ import annotations

from copy import deepcopy
from typing import Final

from .mcp_tools import tool_definitions


ALL_TOOL_PROFILE: Final = "all"
PLANNING_TOOL_PROFILE: Final = "planning"
DISPATCH_TOOL_PROFILE: Final = "dispatch"
RECEIVER_TOOL_PROFILE: Final = "receiver"
TOOL_PROFILES: Final = (
    PLANNING_TOOL_PROFILE,
    DISPATCH_TOOL_PROFILE,
    RECEIVER_TOOL_PROFILE,
)

SKILL_TOOL_PROFILES: Final = {
    "delivery-graph": PLANNING_TOOL_PROFILE,
    "delivery-graph-dispatch": DISPATCH_TOOL_PROFILE,
    "delivery-graph-task": RECEIVER_TOOL_PROFILE,
    "delivery-graph-review": RECEIVER_TOOL_PROFILE,
}

SERVER_INSTRUCTIONS = (
    "Use these tools as an outer Graph scheduler for isolated Delivery "
    "control states. Each actual workspace may bind multiple Deliveries; "
    "callers retain rootId per conversation and pass root_id on every "
    "continuation. "
    "Rootless workspace_status returns DELIVERY_SELECTION_REQUIRED when more "
    "than one unfinished bound Delivery matches and never guesses an unbound "
    "CHOICE_READY or HANDOFF_READY draft. A new user requirement defaults to a new "
    "Delivery; never infer Revision continuity merely because workspace_status "
    "returns an unfinished Delivery. Only explicit user intent to continue "
    "that delivery.id authorizes a TASK or Delivery Revision. When the user "
    "supplies an external work-item identifier, put it in delivery.requirementKey. "
    "One requirementKey maps to one stable delivery.id; common ticket references "
    "in the ID or title are also detected, and a different ID is rejected at "
    "preview and final write. Multiple Deliveries may prepare and freeze against "
    "the same workspace key; Graph state remains rootId-scoped. Workspace execution "
    "is fixed to CURRENT_WORKSPACE_SERIAL for AUTOMATIC and manual runs: every "
    "Delivery has its own branch, but one physical checkout runs at most one "
    "Delivery turn at a time. A later-started or later-discovered Delivery "
    "with a recorded AUTOMATIC selection is marked QUEUED with an automatic "
    "continuation and waits until the prior Delivery is terminal or ready for "
    "final user confirmation, has a verifiable commit, a clean working "
    "tree and index, HEAD still matching its frozen binding, and no in-flight "
    "receiver or reservation. Releasing at the confirmation boundary does not "
    "complete the Delivery; final confirmation may be recorded later by root ID "
    "after another Delivery branch is checked out. "
    "Only then may the host prepare the next Delivery. The recorded AUTOMATIC "
    "choice authorizes it to fingerprint and stash pre-existing business changes "
    "while excluding .layered-delivery/**, create or switch to the frozen Delivery "
    "branch, and call resume_execution_mode with the retained root ID and "
    "fingerprints. The "
    "selection is already persisted and must not be retried. Resource conflicts, "
    "owner dirty state, unmerged changes, HEAD drift, or uncertain receiver "
    "release stop the switch. Never "
    "claim file isolation or run two Deliveries in one checkout. Existing "
    "linked checkouts are ordinary current workspaces; the scheduler and host do "
    "not automatically create, reserve, or launch a new worktree. The Controller "
    "never performs Git writes. workspace_status reports workspaceProvenance with "
    "the actual host, topology, selectionSource, baseRef, baseCommit, baseHeadCommit, "
    "and integrationTarget. A feature branch name alone is not proof that it belongs "
    "to this Delivery: no other Delivery or checkout may use it, and its base "
    "relationship must remain valid. Adopting the current dirty branch requires "
    "explicit user attribution with the exact working-tree fingerprint submitted "
    "as confirmed_dirty_state_fingerprint. Selecting another branch defers those "
    "pre-existing changes to automaticHostPreparation instead of attributing them "
    "to the new Delivery. workspace_status "
    "accepts an optional host-selected base_ref and otherwise discovers mainline "
    "from valid origin/HEAD, then local main, then local master. A clean current "
    "feature may offer NEW_FROM_CURRENT_BRANCH; it freezes a child branch at the "
    "parent HEAD and the host switches to it only after the same serial release "
    "checks. A logical Delivery may freeze multiple local project workspaces; every "
    "writable Git project uses the same feature branch name with its own immutable "
    "base commit, and every scope must satisfy commit, clean, HEAD, and receiver "
    "release checks together. Exact project IDs require explicit authorization at "
    "freeze. All TASKs in that Delivery share those project branches; TASK agents "
    "never create, bind, or switch internal Git branches. When separately authorized, "
    "each TASK may stage and commit only its own changes on the Delivery branch; Git "
    "index and commit writes remain serial. Runtime calls verify the "
    "workspace, branch, and immutable fork commit without mutating Git. loop_context "
    "exposes runtime-verified paths in projectScopes and frozen preview paths in "
    "projectScopeAnchors. Receivers use those paths as-is and never create, switch, "
    "or check out branches. Claude's fixed plugin project root is only the shared "
    "control root; trusted request workspace metadata identifies the actual "
    "development workspace and runtime verification rejects drift. "
    "Its decomposition is a recursive GROUP/TASK hierarchy: TASK is the "
    "execution leaf, every GROUP joins its child subtree, and only a GROUP "
    "with a concrete direct-child seam has a GROUP Review. Start with "
    "workspace_status, compose the "
    "hierarchy, and call preview_hierarchy. It registers CHOICE_READY and "
    "generates scheduler.db, the root overview, baseline, progress, "
    "acceptance, revisions, and work-item projections before returning the "
    "single pendingInteraction. It first has kind DEVELOPMENT_BASELINE when a "
    "Git Delivery lacks a frozen binding, including a dirty workspace; only a "
    "confirmed non-Git workspace skips that step. Git discovery failures are "
    "not converted into execution choices. After the baseline is confirmed, "
    "pendingInteraction has kind EXECUTION_MODE. The compatibility fields "
    "developmentBaseline and executionChoice alias that same object. The "
    "controller is the sole owner of the interaction. "
    "Its presentationPolicy maps request_user_input for Codex and "
    "AskUserQuestion for Claude. The host must use the mapped native selector "
    "whenever that tool is callable in the current context, consuming "
    "pendingInteraction.options in order and preserving its IDs, labels, "
    "descriptions, default, and recommendation. Only when the mapped tool is "
    "not callable may the host display pendingInteraction.markdown exactly. It "
    "must not rewrite that fallback, must not ask the user to type an option, "
    "and must not add another choice. Direct text "
    "continues requirement discussion and a changed requirement must be "
    "previewed again. A selected button supplies the only human confirmation. "
    "AUTOMATIC uses only CURRENT_WORKSPACE_SERIAL. A non-owner Delivery is marked "
    "QUEUED and its persisted automatic continuation waits until the previous "
    "one is terminal or ready for final user confirmation, has a verifiable "
    "business commit, a clean tree and index, unchanged frozen HEAD binding, "
    "and no receiver or reservation. select_execution_mode already persisted the choice "
    "and returns WAIT_FOR_AUTOMATIC_QUEUE_TURN. At the queue head, the host follows "
    "automaticHostPreparation to stash pre-existing business changes when needed, "
    "prepare the exact branch, and call resume_execution_mode with the "
    "retained root ID and fingerprints, without presenting the selector again. "
    "Any unmerged conflict, owner dirty state, HEAD drift, or uncertain release "
    "stops the "
    "switch. Do not create or launch a new worktree. The coordinator reserves "
    "each READY Loop and starts a distinct native receiver for its claim; "
    "Review Loops still use independent native receivers. The controlling "
    "conversation routes every continuation by rootId and never runs another "
    "Delivery concurrently in the same physical checkout. "
    "MANUAL creates the portable handoff and returns the exact receiverPrompt "
    "also embedded in that file. It registers HANDOFF_READY without binding "
    "a workspace or starting a Graph run. The receiving CLI must call "
    "start_manual_handoff in the actual development workspace before any "
    "code inspection or implementation. If its Git binding drifted, start "
    "returns a DEVELOPMENT_BASELINE pendingInteraction without creating a run "
    "or binding a workspace. Confirming a changed binding creates the next "
    "immutable manual revision; an unchanged binding restores the existing "
    "revision. The receiver retries with the returned fingerprints, then "
    "starts the Graph in manual mode. Only TASK implementation Loops use "
    "MANUAL claims. TASK Reviews, configured GROUP seam Reviews, and Delivery "
    "Acceptance/Readiness retain complete host-native automatic routing, "
    "isolated judgment, findings closure, and final user confirmation. "
    "Changed "
    "HANDOFF_READY content keeps the same delivery.id and calls "
    "create_manual_handoff with the current revision, "
    "USER_EXPLICIT_SAME_DELIVERY, and a revision reason; it creates the next "
    "immutable manual revision in the same directory. While native "
    "child Agents run in the background, consume every immediate action in "
    "the current frontier before waiting. Then use the host's native receiver "
    "wait until a completion or needs-attention event, or until the returned "
    "waitDirective deadline. A receiver event triggers graph_frontier once; a "
    "poll deadline triggers one read-only graph_status call; nextWakeAt or "
    "ADVANCE_REQUIRED triggers graph_frontier once. The host must never call "
    "graph_frontier or graph_status back-to-back, and the STANDARD-only "
    "90-second first-heartbeat warning is not a polling interval. Show "
    "progressMonitor.markdownTable in user commentary only when "
    "changeFingerprint changes or a new alert requires attention. Each "
    "claimed STANDARD Loop calls report_loop_progress at "
    "meaningful milestones including code inspection, root-cause "
    "confirmation, edit completion, test start and completion when tests are "
    "actually executed, rework, "
    "Review, and final verification, using concise summaries in the user's "
    "current language. A long-running test or build must use a non-blocking "
    "process or separate monitor so the receiver can heartbeat while it "
    "runs; report and heartbeat immediately before and after it. A host "
    "completion notification is not a heartbeat. A short problem-free LIGHT "
    "Loop may omit heartbeat and progress inside its initial lease, perform "
    "targeted verification, and submit its truthful final result directly. "
    "Heartbeat remains lease-only and raw graph events remain diagnostic-only. "
    "After plan_dispatch_batch creates reservations, consume all assignments "
    "and pass each non-empty assignment receiverPrompt verbatim into the new "
    "independent receiver. A user-explicit Skill hint uses exact host-native "
    "wording and should run at each applicable and available stage; most "
    "implementation Skills run in TASK. It may be skipped only when the stage "
    "is inapplicable or the host cannot provide it, and never gates Controller "
    "success. The host must obey its postActionWait: wait for a receiver event "
    "or the "
    "earliest "
    "reservation deadline, then call graph_frontier once. "
    "Each TASK or Review Loop owns its internal "
    "plan, tests, gates, rework, and Skill usage. A TASK runs the smallest "
    "affected verification scope that covers its change and reports auditable "
    "verificationEvidence. Review independence means independent judgment, "
    "not an automatic full-suite rerun. A Review reuses passing upstream "
    "evidence when its scope still covers the risk and the relevant workspace "
    "path snapshot is unchanged; unrelated workspace edits do not invalidate "
    "a bound scope. It then reruns only gaps, findings, and risky seams. "
    "TASK Review owns the frozen TASK acceptance, local behavior, public "
    "contract, and targeted regression. A configured GROUP Review owns only "
    "direct-child seams. Delivery Acceptance/Readiness owns top-level "
    "requirement coverage, system evidence, operational readiness, and global "
    "risk. Successful Reviews persist only their layer result, findings, and "
    "bounded evidence references; never copy upstreamLoopResults or lower-layer "
    "result bodies. Reuse exact state matches. A full "
    "rerun is reserved for an unbounded impact scope, critical cross-boundary "
    "risk without isolated checks, or an explicit frozen requirement. A "
    "relevant edit invalidates only the affected evidence and dependents. A "
    "the host planning layer supplies each Loop payload with requirement "
    "direction, explicit constraints, confirmed external contracts, and known "
    "acceptance outcomes rather than a complete implementation specification. "
    "The Graph structures work items into a DAG, preserves opaque inputs, "
    "controls dependencies/resources, routes execution, and aggregates global "
    "progress and results; it does not author business requirements. Planning "
    "only needs a sufficiently clear "
    "direction, not an exhaustive design. Ordinary file names, implementation "
    "classes, internal methods, code structure, and test organization remain "
    "Loop-owned unless the requirement or a confirmed external compatibility "
    "contract explicitly fixes an exact identifier. The reserved "
    "databaseChanges payload is "
    "the exception: table before/after structure and migration policy are "
    "fully designed and confirmed in the baseline before dispatch, and the "
    "TASK Loop only applies and verifies the frozen after snapshot. The Loop "
    "derives and validates other "
    "in-scope necessary conditions from real code, contracts, and data flow. "
    "An actionable "
    "implementation, test, or Review finding stays inside the current Loop: "
    "adapt the internal plan, resolve it, and reevaluate before returning a "
    "terminal outcome. BLOCKED is only for a concrete condition that leaves "
    "no in-scope path with current authority; REPLAN_REQUIRED is only for a "
    "required change to frozen dependencies, resources, project scope, "
    "topology, or a databaseChanges contract. Before final user acceptance, "
    "such a change creates the next "
    "immutable revision under the same Delivery ID; the prior run becomes "
    "SUPERSEDED when the new revision is frozen. Shared "
    "skillHints are advisory shared preferences. After initial scope inspection "
    "and before forming TASK boundaries or payload, the planning host should "
    "pre-trigger a hint natively when it is applicable and available, using it "
    "only to clarify direction, constraints, acceptance, boundaries, and risks. "
    "It must not promote Skill defaults or examples into frozen implementation "
    "facts. Hints remain unassigned to nodes and each Loop reevaluates them in "
    "its actual runtime context. Delivery Graph never recommends or selects a "
    "development model. Automatic execution inherits the current trusted "
    "host model. Manual handoff never selects a receiving Agent/model, never "
    "creates a receiving task, and never initializes a worktree. The "
    "receiver's host becomes known only after it reads the file and calls "
    "start_manual_handoff in the selected workspace. Its outer context only "
    "coordinates: each manual TASK runs in an independent receiving context "
    "that reports its receiver identity and claims with MANUAL provenance; every "
    "subsequent Review returns to automatic host-native planning. Automatic "
    "execution uses Plugin-owned receiver concurrency within the selected "
    "Delivery and quota pause/resume; CURRENT_WORKSPACE_SERIAL still prevents "
    "another Delivery turn in the same physical workspace. Adapter trust comes "
    "from Plugin registration and configured "
    "host startup, never a user file or allowlist. Grok, "
    "DeepSeek, or another Agent integrates by adding one trusted outer "
    "Adapter lifecycle; its models and internal workers remain outside the "
    "Graph contract. For automatic execution, plan_dispatch_batch "
    "atomically reserves every READY TASK or Review Loop with its host Agent "
    "slot, then returns "
    "outer-receiver assignments with CURRENT_HOST_INHERIT model policy. A second "
    "dispatcher sees "
    "WAIT_FOR_DISPATCH_RECEIVER and cannot reserve or launch the same Loop. "
    "It never analyzes Loop payloads, starts Agents, or claims Loops. The "
    "native host creates each outer receiver only after reservation; the "
    "caller submits the configured Adapter binding, caller-declared Agent "
    "and context IDs, HOST_NATIVE orchestration marker, exact reservation, "
    "operation ID, and verified decision fingerprint. These declarations "
    "are not cryptographic host-session proof; the live reservation, "
    "fingerprint, lease, and operation ID are the scheduler gates. The "
    "operation ID is required for progress, pause, and result writes. "
    "If native receiver startup fails twice, an unclaimed READY TASK may "
    "use handoff_ready_automatic_task only after its reservation expires, "
    "the Delivery workspace is clean, and the user confirms no code changes. "
    "That TASK accepts one MANUAL receiver while the Graph remains active "
    "and every Review remains automatic. The result may "
    "include display-only workerTelemetry by "
    "phase, with unknown Agent/model/effort values left unreported. Never "
    "spawn an autonomous CLI, subprocess, or companion script such as "
    "codex-companion to satisfy an assignment. Such a route stays unclaimed and is handed "
    "off manually. prepare_delivery_revision requires explicit same-"
    "Delivery or recorded replan continuity, only stages a candidate, "
    "and leaves the current run active until freeze. It "
    "does not require a generic host approval prompt; the user's automatic "
    "freeze or manual handoff-file choice is the single business "
    "confirmation for that revision. A host or Loop receiver observing "
    "quota exhaustion calls pause_loop with HOST or EXECUTOR scope and a "
    "structured provider reset time. The scheduler treats Loop payload and result "
    "as opaque and accepts only standard Loop outcomes. resourceClaims "
    "are exact cross-Delivery scheduling locks, not file scopes. Every TASK "
    "requirement starts frozen. An explicitly authorized, not-yet-started "
    "TASK may be unfrozen and refrozen with a revised title, summary, and "
    "opaque payload; topology, dependencies, and resource claims remain "
    "Delivery-frozen. record_loop_result captures the verified workspace "
    "snapshot, and the main control directory acceptance.md links its stable "
    "workspace-changes.patch for review; this evidence does not replace the "
    "verifiable commit, clean tree, or HEAD checks required before switching. "
    "Final completion still "
    "requires explicit user confirmation. A completed Delivery is archived "
    "only after another explicit user action; archive_delivery hides it from "
    "default workspace discovery while retaining its history and detail "
    "projections. External Git and publication "
    "actions remain outside this server."
)

CODEX_SERVER_INSTRUCTIONS = (
    "Use the installed delivery-graph Skill as the workflow contract. Start "
    "with workspace_status, retain rootId, and call hierarchy_contract before "
    "schema-v3 planning. Planning supplies directional per-Loop input; Graph "
    "structures, remembers, summarizes, and schedules without authoring "
    "requirements. Pre-trigger a requested Skill only if planning-relevant. "
    "Scheduler state is MCP-owned; never access scheduler.db directly. The "
    "primary plans, routes, and monitors only. AUTOMATIC uses "
    "CURRENT_WORKSPACE_SERIAL; Controller never writes Git. Reserve READY Loops "
    "with plan_dispatch_batch and let distinct native receivers dispatch_loop. "
    "Pass receiverPrompt verbatim; a requested Skill should run when applicable "
    "and available, usually in TASK. Never claim inline or expose operation IDs. "
    "Follow pendingInteraction and wait directives exactly. Git, publication, "
    "migrations, permissions, final confirmation, and archive retain explicit "
    "authority."
)

_PROFILE_TOOL_NAMES: Final = {
    PLANNING_TOOL_PROFILE: frozenset(
        {
            "workspace_status",
            "recommend_assurance_profile",
            "hierarchy_contract",
            "preview_hierarchy",
            "confirm_development_baseline",
            "select_execution_mode",
            "resume_execution_mode",
            "create_manual_handoff",
            "prepare_hierarchy",
            "prepare_delivery_revision",
            "delivery_revision_history",
            "freeze_hierarchy",
            "unfreeze_task_requirement",
            "refreeze_task_requirement",
            "record_user_confirmation",
            "archive_delivery",
        }
    ),
    DISPATCH_TOOL_PROFILE: frozenset(
        {
            "workspace_status",
            "resume_execution_mode",
            "start_manual_handoff",
            "plan_dispatch_batch",
            "graph_frontier",
            "graph_status",
            "open_delivery_dashboard",
            "graph_events",
            "advance_graph",
            "rebuild_graph_run",
            "handoff_ready_automatic_task",
            "cancel_graph_run",
        }
    ),
    RECEIVER_TOOL_PROFILE: frozenset(
        {
            "loop_context",
            "dispatch_loop",
            "heartbeat_loop",
            "report_loop_progress",
            "pause_loop",
            "resume_loop",
            "record_loop_result",
        }
    ),
}

_ALL_TOOL_DEFINITIONS: Final = tuple(tool_definitions())
_ALL_TOOL_NAMES: Final = frozenset(
    str(tool["name"]) for tool in _ALL_TOOL_DEFINITIONS
)
_USER_INTERACTION_TOOL_NAMES: Final = frozenset(
    str(tool["name"])
    for tool in _ALL_TOOL_DEFINITIONS
    if tool.get("_meta", {}).get("anthropic/requiresUserInteraction") is True
)

_PROFILE_INSTRUCTIONS: Final = {
    PLANNING_TOOL_PROFILE: (
        "Use $delivery-graph for workspace discovery, requirement modeling, "
        "baseline confirmation, immutable revision preparation, execution-mode "
        "selection, freeze, and final user confirmation. Retain rootId. Do not "
        "dispatch, claim, execute, or review Loops from this profile."
    ),
    DISPATCH_TOOL_PROFILE: (
        "Use $delivery-graph-dispatch as the primary coordinator. Route by rootId, "
        "consume the full frontier, reserve with plan_dispatch_batch, create one "
        "independent receiver per assignment, pass receiverPrompt verbatim, and "
        "obey postActionWait. Never claim or execute a Loop inline."
    ),
    RECEIVER_TOOL_PROFILE: (
        "This is the isolated Loop receiver catalog. Follow receiverPrompt: use "
        "$delivery-graph-task for TASK_LOOP or $delivery-graph-review for any "
        "Review Loop. Claim only the assigned node, keep its lease/progress, and "
        "submit one truthful standard result. Never plan or dispatch other Loops."
    ),
}


def _validated_profile(profile: str) -> str:
    if profile == ALL_TOOL_PROFILE or profile in TOOL_PROFILES:
        return profile
    raise ValueError(f"Unknown MCP tool profile: {profile}")


def tool_names_for_profile(profile: str) -> frozenset[str]:
    """Return the exact callable names exposed by one MCP server profile."""

    selected = _validated_profile(profile)
    if selected == ALL_TOOL_PROFILE:
        return _ALL_TOOL_NAMES
    return _PROFILE_TOOL_NAMES[selected]


def tool_definitions_for_profile(profile: str) -> list[dict[str, object]]:
    """Return a defensive copy of one profile's MCP tool catalog."""

    names = tool_names_for_profile(profile)
    return deepcopy(
        [
            tool
            for tool in _ALL_TOOL_DEFINITIONS
            if tool["name"] in names
        ]
    )


def user_interaction_tool_names_for_profile(
    profile: str,
) -> frozenset[str]:
    """Return interaction-gated tools visible in one profile."""

    return _USER_INTERACTION_TOOL_NAMES & tool_names_for_profile(profile)


def server_instructions_for_profile(
    profile: str,
    *,
    host_adapter: str | None = None,
) -> str:
    """Return bounded workflow instructions matched to the active catalog."""

    selected = _validated_profile(profile)
    if selected != ALL_TOOL_PROFILE:
        return _PROFILE_INSTRUCTIONS[selected]
    if host_adapter == "codex":
        return CODEX_SERVER_INSTRUCTIONS
    return SERVER_INSTRUCTIONS


__all__ = (
    "ALL_TOOL_PROFILE",
    "DISPATCH_TOOL_PROFILE",
    "PLANNING_TOOL_PROFILE",
    "RECEIVER_TOOL_PROFILE",
    "SKILL_TOOL_PROFILES",
    "TOOL_PROFILES",
    "server_instructions_for_profile",
    "tool_definitions_for_profile",
    "tool_names_for_profile",
    "user_interaction_tool_names_for_profile",
)
