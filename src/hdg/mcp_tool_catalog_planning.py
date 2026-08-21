from __future__ import annotations

from .mcp_tool_schemas import (
    BASE_REF,
    DIRTY_STATE_FINGERPRINT,
    ROOT_ID,
    _development_baseline_tool_schema,
    _execution_choice_tool_schema,
    _execution_resume_tool_schema,
    _manual_handoff_tool_schema,
    _manual_start_tool_schema,
    _object,
    _prepare_hierarchy_tool_schema,
    _prepare_revision_tool_schema,
    _string,
    _tool,
)


PLANNING_TOOLS = (
    _tool(
        "workspace_status",
        (
            "Inspect Deliveries bound to this actual workspace, or select "
            "one by root ID. More than one unfinished bound Delivery returns "
            "DELIVERY_SELECTION_REQUIRED and candidateDeliveries; callers "
            "must retry with their retained root ID. Unbound CHOICE_READY or "
            "legacy HANDOFF_READY drafts are discoverable only by explicit root ID. "
            "Every existing checkout is treated as the current workspace; "
            "an existing linked checkout receives no special scheduling "
            "behavior. CURRENT_WORKSPACE_SERIAL permits one Delivery turn at "
            "a time. A later Delivery is returned as QUEUED after either its "
            "AUTOMATIC or MANUAL selection is recorded, and carries the matching "
            "resume or manual-start continuation until the previous run is "
            "paused, terminal, or ready for current Revision confirmation; every "
            "frozen writable branch must have a verifiable business commit, "
            "a clean work tree and index, matching HEAD binding, and quiesced "
            "receivers/reservations. Status alone is not release: the Controller "
            "must persist WORKSPACE_TURN_RELEASED before branch preparation. "
            "A cancelled Delivery releases independently of archive, and "
            "terminal status suppresses stale workspace-rebase advice. "
            "Resource conflicts, dirty state, HEAD drift, or "
            "uncertain release return a stop or wait state instead of "
            "switching. With explicit root ID, CHOICE_READY restores "
            "pendingInteraction before frozen-binding runtime verification."
        ),
        _object(
            {
                "root_id": ROOT_ID,
                "base_ref": BASE_REF,
                "confirmed_dirty_state_fingerprint": (
                    DIRTY_STATE_FINGERPRINT
                ),
            }
        ),
    ),
    _tool(
        "hierarchy_contract",
        (
            "Return the exact schema-v3 outer Graph contract, one example, "
            "advisory planning-stage Skill pre-trigger guidance, and the "
            "boundary between frozen requirements and Loop-owned implementation."
        ),
        _object(
            {
                "root_kind": {
                    "type": "string",
                    "enum": ["GROUP", "TASK"],
                }
            },
            required=["root_kind"],
        ),
    ),
    _tool(
        "preview_hierarchy",
        (
            "Validate and fingerprint a proposed hierarchy, register its "
            "CHOICE_READY snapshot, and generate scheduler.db, root overview, "
            "baseline, progress, acceptance, revisions, and work-item "
            "artifacts before returning one controller-owned "
            "pendingInteraction with a host-native-selector-first policy. A "
            "Git Delivery without a frozen binding first returns "
            "DEVELOPMENT_BASELINE, including for a dirty workspace; a confirmed "
            "non-Git workspace proceeds to EXECUTION_MODE. Git discovery "
            "errors fail closed. "
            "The mapped native question tool is mandatory whenever callable; "
            "exact Markdown is only a capability fallback. It does not bind "
            "a workspace, freeze a Graph, create a run, or change the current "
            "checkout."
        ),
        _prepare_hierarchy_tool_schema(),
    ),
    _tool(
        "confirm_development_baseline",
        (
            "Apply one DEVELOPMENT_BASELINE pendingInteraction option before "
            "the execution-mode choice, or after a manual-start Git drift "
            "block: record the per-Delivery preference, "
            "compute the Git binding read-only, re-stage the hierarchy with "
            "the binding frozen in, and return the updated "
            "hierarchyFingerprint plus the next pendingInteraction. During "
            "manual reconfirmation, a changed binding creates the next "
            "immutable manual revision; an unchanged binding restores the "
            "existing revision. The Controller computes the binding read-only "
            "and never changes the checkout; NEW_FROM_MAINLINE pins baseCommit "
            "to the mainline HEAD, while NEW_FROM_CURRENT_BRANCH pins a "
            "stacked child to the clean current feature HEAD and makes that "
            "parent feature the integration target. The host may create or "
            "switch to the required branch only after the Controller persists "
            "the clean CURRENT_WORKSPACE_SERIAL release. The choice is remembered and "
            "not re-asked on subsequent revisions."
        ),
        _development_baseline_tool_schema(),
    ),
    _tool(
        "select_execution_mode",
        (
            "Apply one exact option returned by an EXECUTION_MODE "
            "pendingInteraction for the retained root ID. AUTOMATIC records "
            "the human choice immediately and fixes execution to "
            "CURRENT_WORKSPACE_SERIAL: the actual workspace runs one Delivery "
            "turn at a time. A later AUTOMATIC or MANUAL Delivery waits until "
            "the previous run is paused, terminal, or ready for current Revision "
            "confirmation, then requires a verifiable business commit and clean "
            "matching binding on every frozen writable branch, quiesced receivers "
            "and reservations, and persisted WORKSPACE_TURN_RELEASED before any "
            "branch transition. The frozen MANUAL snapshot "
            "remains internally HANDOFF_READY while its external workspace status is "
            "QUEUED. At the queue head, the recorded choice authorizes the "
            "host to verify the exact dirty fingerprint, stash business "
            "changes while excluding .layered-delivery/**, create or switch "
            "the frozen Delivery branch, and resume without reconfirmation. "
            "Unmerged changes, resource conflicts, HEAD drift, or uncertain "
            "release stop the transition. Existing linked checkouts are "
            "ordinary current workspaces. If branch preparation is required, "
            "the persisted selection returns mode-specific host preparation; after "
            "performing its stash/create-or-switch actions, call resume_execution_mode "
            "for AUTOMATIC or start_manual_handoff for MANUAL and never retry the "
            "selection. No additional checkout or separate workspace task is "
            "scheduled. MANUAL creates the handoff "
            "and returns the exact "
            "receiver prompt embedded in that file. Direct dialog text is "
            "not a tool option and continues requirement discussion."
        ),
        _execution_choice_tool_schema(),
    ),
    _tool(
        "resume_execution_mode",
        (
            "Continue a previously recorded AUTOMATIC selection after the "
            "queued Delivery becomes the workspace turn owner and the trusted "
            "host completes automaticHostPreparation, reaching the required "
            "feature branch after the prior owner's clean release was persisted. "
            "Revalidate "
            "the exact fingerprints and Git/project bindings, then prepare, "
            "freeze, and dispatch only that Delivery without asking the user "
            "to select or confirm again. It never creates another checkout "
            "and never changes MANUAL into AUTOMATIC."
        ),
        _execution_resume_tool_schema(),
    ),
    _tool(
        "create_manual_handoff",
        (
            "Create a later explicit manual revision, or serve the "
            "controller-owned selection operation internally. For the "
            "initial execution choice, hosts must call "
            "select_execution_mode(MANUAL), not this low-level tool. Freeze "
            "the confirmed requirement snapshot as a portable bundle under "
            ".layered-delivery/<delivery-id>/. The bundle contains one "
            "self-contained .layered-delivery/<delivery-id>/"
            "handoff-<fingerprint>.md plus the same overview, baseline, "
            "progress, acceptance, revisions, and work-items projections "
            "used by automatic development. It also registers the frozen "
            "HANDOFF_READY snapshot, MANUAL queue selection, and workspace binding "
            "atomically in the shared scheduler.db, then refreshes the root overview.md. "
            "Never create a shared handoffs directory. "
            "If the user explicitly changes an existing HANDOFF_READY "
            "requirement, retain its delivery.id and provide the current "
            "revision, USER_EXPLICIT_SAME_DELIVERY continuity, and a reason; "
            "the controller creates the next immutable manual revision in "
            "the same directory. A requirementKey already mapped to another "
            "delivery.id is rejected. "
            "This does not prepare, freeze, or start a Graph run; do not "
            "choose an Agent, create a receiving task, or initialize another checkout. "
            "It records the current workspace as the serial queue binding. "
            "The user may open the bundle in "
            "any "
            "CLI, but that receiver must call start_manual_handoff before "
            "code work and then complete the full governed Graph."
        ),
        _manual_handoff_tool_schema(),
    ),
    _tool(
        "start_manual_handoff",
        (
            "Start the exact HANDOFF_READY snapshot in the bound receiving workspace "
            "before any implementation work. If another Delivery owns the serial "
            "turn, return QUEUED with manualHostPreparation and no run. "
            "If the frozen Git binding drifted, return a DEVELOPMENT_BASELINE "
            "pendingInteraction without creating a run; the receiver confirms it "
            "and retries with the returned fingerprints. For an unstarted legacy "
            "handoff whose hierarchy is unchanged, refresh only the versioned Graph "
            "runtime policy and fingerprint before execution. Otherwise this creates one "
            "governed manual Graph run. TASK implementation Loops must be "
            "claimed with MANUAL provenance; TASK Reviews, configured GROUP "
            "seam Reviews, and Delivery Acceptance/Readiness remain independent "
            "host-native automatic Loops, followed by current Revision completion "
            "confirmation. "
            "It never weakens or skips configured STANDARD Review nodes."
        ),
        _manual_start_tool_schema(),
    ),
    _tool(
        "prepare_hierarchy",
        (
            "Validate and prepare an outer scheduling graph for an explicit "
            "revision or controller-owned selection. For the initial "
            "execution choice, hosts call select_execution_mode(AUTOMATIC) "
            "instead of this low-level tool. Shared Skill "
            "hints remain advisory, Loop payloads stay opaque to scheduling; "
            "the reserved databaseChanges contract is validated and projected "
            "before dispatch. A Git Delivery feature-branch binding is "
            "verified read-only. One physical workspace may bind multiple "
            "Delivery control states, routed by root ID, while actual "
            "execution remains CURRENT_WORKSPACE_SERIAL."
        ),
        _prepare_hierarchy_tool_schema(),
    ),
    _tool(
        "prepare_delivery_revision",
        (
            "Prepare the next immutable revision of the same OPEN/未上线 "
            "Delivery after its frozen scope changes, including after the "
            "previous Revision is COMPLETED. The Delivery ID stays "
            "stable, completed unchanged TASKs are candidates for "
            "carry-forward, and every project scope is reauthorized at "
            "freeze. CLOSED/已上线交付 rejects new Revisions."
        ),
        _prepare_revision_tool_schema(),
    ),
    _tool(
        "delivery_revision_history",
        (
            "Read every immutable revision and run status for one logical "
            "Delivery."
        ),
        _object(
            {"root_id": ROOT_ID},
            required=["root_id"],
        ),
    ),
)
