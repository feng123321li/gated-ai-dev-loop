from __future__ import annotations

from .hierarchy_contract_schema import (
    Any,
    SCHEMA_VERSION,
    _example,
    execution_choice_contract,
    hierarchy_input_schema,
)


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
            "executionInteraction": {
                "owner": "CONTROLLER",
                "artifactGate": "CHOICE_READY_ARTIFACTS_READY",
                "hostMapping": "MECHANICAL_NO_REWRITE",
                "selectionTool": "select_execution_mode",
                "automaticResumeTool": "resume_execution_mode",
                "manualStartTool": "start_manual_handoff",
                "manualExecutionBoundary": (
                    "MANUAL_TASK_ONLY_REVIEWS_REMAIN_AUTOMATIC"
                ),
                "directTextAction": "CONTINUE_REQUIREMENT_DISCUSSION",
                "executionChoice": execution_choice_contract(),
                "description": (
                    "Generate baseline and associated projections before "
                    "showing this controller-owned choice. Hosts preserve "
                    "the option order, default, labels, descriptions, and "
                    "freeform behavior exactly. They must use the mapped "
                    "native selector whenever callable and may show exact "
                    "Markdown only when it is unavailable; skills do not "
                    "reconstruct, rewrite, or add options. Selecting "
                    "AUTOMATIC is recorded once and fixes execution to "
                    "CURRENT_WORKSPACE_SERIAL. One actual workspace runs one "
                    "Delivery turn at a time. A later Delivery with a "
                    "recorded AUTOMATIC selection is marked QUEUED with an "
                    "automatic continuation and waits until the prior run is "
                    "terminal or ready for final user confirmation, has a "
                    "verifiable business commit, a clean work tree and index, "
                    "unchanged frozen HEAD binding, and safe release of every "
                    "receiver and reservation. Releasing at the confirmation "
                    "boundary does not complete that Delivery. "
                    "Only then does the host mechanically stash pre-existing "
                    "business changes when needed, create or switch the exact "
                    "Delivery branch, and call "
                    "resume_execution_mode for the retained root ID, without "
                    "another user confirmation. No additional checkout or "
                    "workspace task is created."
                ),
            },
            "deliveryContinuity": {
                "identity": "requirementKey -> delivery.id",
                "fallbackDetection": "ID_OR_TITLE_TICKET_REFERENCE",
                "duplicatePolicy": "REJECT_DIFFERENT_DELIVERY_ID",
                "revisionPolicy": (
                    "REUSE_DELIVERY_ID_AND_CREATE_IMMUTABLE_REVISION"
                ),
                "description": (
                    "When the user supplies a stable external work-item or "
                    "requirement identifier, declare it as requirementKey. "
                    "One requirement key maps to one immutable delivery.id; "
                    "changed content creates the next revision in the same "
                    "projection directory. The controller also detects "
                    "ticket-like keys in the Delivery ID or title so a "
                    "renamed ID cannot silently create duplicate files."
                ),
            },
            "assuranceProfiles": {
                "default": "STANDARD",
                "classificationOwner": "PLANNING_AGENT",
                "classificationBasis": (
                    "ACTUAL_CHANGE_CONTENT_AND_IMPACT_SCOPE"
                ),
                "light": {
                    "shape": "SINGLE_ROOT_TASK",
                    "reviewLoops": "OMITTED",
                    "verification": "TARGETED_FOR_DECLARED_CHANGE",
                    "requiresRationale": True,
                    "expandedImpact": "REPLAN_REQUIRED_TO_STANDARD",
                },
                "standard": {
                    "shape": "RECURSIVE_GROUP_TASK",
                    "reviewLoops": (
                        "TASK_OPTIONAL_GROUP_SEAM_AND_DELIVERY_ACCEPTANCE"
                    ),
                    "verification": (
                        "AFFECTED_SCOPE_WITH_EVIDENCE_FIRST_REVIEWS"
                    ),
                },
                "standardWhenAny": [
                    "MULTI_TASK_OR_MULTI_PROJECT",
                    "PUBLIC_OR_CROSS_MODULE_INTERFACE_CHANGE",
                    "DATA_SCHEMA_OR_MIGRATION",
                    "AUTHORIZATION_SECURITY_OR_PRIVACY",
                    "MONEY_BILLING_OR_IRREVERSIBLE_SIDE_EFFECT",
                    "CONCURRENCY_OR_PRODUCTION_DEPLOYMENT",
                    "UNKNOWN_OR_EXPANDING_IMPACT",
                ],
            },
            "planningSkillPreTrigger": {
                "owner": "HOST_PLANNING_LAYER",
                "stage": (
                    "AFTER_INITIAL_SCOPE_INSPECTION_BEFORE_TASK_BOUNDARIES_AND_PAYLOAD"
                ),
                "mode": "ADVISORY_HOST_NATIVE_SKILL_PRETRIGGER",
                "blocking": False,
                "applicability": (
                    "LIKELY_TO_CLARIFY_REQUIREMENT_DIRECTION_CONSTRAINTS_"
                    "ACCEPTANCE_OR_TASK_BOUNDARIES"
                ),
                "nativeInvocation": {
                    "codex": "$skill-name",
                    "claudeCode": "Skill tool with catalog name",
                    "other": "host-native Skill entry with catalog name",
                },
                "whenApplicableAndAvailable": (
                    "TRIGGER_BEFORE_FINALIZING_CANDIDATE_HIERARCHY"
                ),
                "whenNotApplicableOrUnavailable": (
                    "CONTINUE_WITHOUT_BLOCKING_OR_USER_RECONFIRMATION"
                ),
                "explicitUserSkillUsage": (
                    "ATTEMPT_AT_EACH_APPLICABLE_AND_AVAILABLE_STAGE"
                ),
                "defaultImplementationSkillStage": "TASK_LOOP",
                "planningDepth": "DIRECTIONALLY_SUFFICIENT_NOT_EXHAUSTIVE",
                "useOutputFor": [
                    "REQUIREMENT_DIRECTION",
                    "EXPLICIT_BUSINESS_AND_EXTERNAL_CONSTRAINTS",
                    "KNOWN_ACCEPTANCE_OUTCOMES",
                    "TASK_BOUNDARIES_AND_DEPENDENCIES",
                    "MATERIAL_RISKS_AND_UNKNOWNS",
                ],
                "doNotPromoteSkillSuggestionsToFrozenFacts": [
                    "FILE_PATH_OR_FILE_NAME",
                    "IMPLEMENTATION_CLASS_OR_TYPE_NAME",
                    "INTERNAL_METHOD_NAME",
                    "CODE_STRUCTURE_OR_DETAILED_IMPLEMENTATION_PLAN",
                    "TEST_FILE_OR_TEST_ORGANIZATION",
                ],
                "exactIdentifierException": (
                    "EXPLICIT_REQUIREMENT_OR_CONFIRMED_EXTERNAL_COMPATIBILITY_CONTRACT"
                ),
                "controllerEnforcesInvocation": False,
                "runtimeReevaluationRequired": True,
            },
            "planningContentRouting": {
                "owner": "HOST_PLANNING_LAYER",
                "planningCompleteness": (
                    "CLEAR_DIRECTION_CONSTRAINTS_AND_ACCEPTANCE_NOT_EXHAUSTIVE_DESIGN"
                ),
                "planningLayerSupplies": [
                    "REQUIREMENT_DIRECTION_AND_TARGET_OUTCOMES",
                    "EXPLICIT_USER_CONSTRAINTS",
                    "CONFIRMED_PUBLIC_OR_EXTERNAL_CONTRACTS",
                    "KNOWN_ACCEPTANCE_CRITERIA",
                    "TASK_BOUNDARIES_DEPENDENCIES_AND_RESOURCE_LOCKS",
                    "RESERVED_DATABASE_CHANGE_CONTRACTS",
                ],
                "loopOwnsAndExpands": [
                    "FILE_LAYOUT_AND_FILE_NAMES",
                    "IMPLEMENTATION_CLASS_AND_INTERNAL_METHOD_NAMES",
                    "CODE_STRUCTURE_AND_ALGORITHMS",
                    "DETAILED_IMPLEMENTATION_AND_TEST_PLAN",
                    "IN_SCOPE_NECESSARY_CONDITIONS_DISCOVERED_FROM_REAL_CODE",
                ],
                "graphRole": (
                    "STRUCTURE_WORK_ITEMS_MATERIALIZE_DAG_FINGERPRINT_CONTROL_"
                    "DEPENDENCIES_AND_RESOURCES_DISPATCH_AGGREGATE_PROGRESS_"
                    "RESULTS_AND_GLOBAL_STATE"
                ),
                "graphDoesNot": [
                    "AUTHOR_OR_INVENT_BUSINESS_REQUIREMENTS",
                    "COMPLETE_DETAILED_IMPLEMENTATION_PLANNING",
                    "SELECT_IMPLEMENTATION",
                    "ENFORCE_SKILL_INVOCATION",
                ],
                "aggregation": [
                    "HIERARCHY_AND_DAG_SUMMARY",
                    "FRONTIER_AND_GLOBAL_PROGRESS",
                    "LOOP_RESULTS_AND_REVIEW_STATUS",
                    "DELIVERY_ACCEPTANCE_AND_READINESS_ROUTE",
                ],
                "nodePayloadRouting": (
                    "DELIVER_EXACT_NODE_PAYLOAD_TO_CORRESPONDING_LOOP"
                ),
                "explicitSkillHintRouting": (
                    "COPY_TO_ASSIGNMENT_MANUAL_ACTION_HANDOFF_AND_LOOP_CONTEXT"
                ),
                "progression": (
                    "ADVANCE_FROM_DEPENDENCIES_RESERVATIONS_CLAIMS_LEASES_"
                    "PROGRESS_AND_TERMINAL_OUTCOMES"
                ),
                "exactImplementationIdentifierMayFreezeOnlyWhen": (
                    "EXPLICITLY_STATED_BY_REQUIREMENT_OR_CONFIRMED_EXTERNAL_CONTRACT"
                ),
                "skillDefaultsAndExamplesAreNotRequirementFacts": True,
                "controllerAnalyzesPlanningContent": False,
            },
            "taskSplitIntegrityPreflight": {
                "owner": "HOST_PLANNING_LAYER",
                "stage": (
                    "AFTER_CANDIDATE_HIERARCHY_BEFORE_PREVIEW_OR_REFREEZE"
                ),
                "blocking": True,
                "controllerAnalyzesLoopPayload": False,
                "levels": {
                    "L0": {
                        "mode": "DETERMINISTIC_CONTRACT_CHECK",
                        "checks": [
                            "TASK_END_STATE_USES_ONLY_BASELINE_AND_PREDECESSORS",
                            "TASK_REVIEW_IS_RUNNABLE_BEFORE_ANY_SUCCESSOR",
                            "NO_ACCEPTANCE_OR_BUILD_DEFERRED_TO_SUCCESSOR",
                        ],
                    },
                    "L1": {
                        "mode": "PLUGGABLE_TARGETED_CODE_ANALYSIS",
                        "triggerSource": (
                            "EXPLICIT_REQUIREMENT_OR_CONFIRMED_CURRENT_CODE_IMPACT_"
                            "NOT_PLANNER_INVENTION"
                        ),
                        "triggers": [
                            "DELETE_SYMBOL",
                            "RENAME_OR_MOVE_SYMBOL",
                            "CHANGE_PUBLIC_FIELD_METHOD_OR_SIGNATURE",
                        ],
                        "scope": "AUTHORIZED_PROJECT_SCOPES",
                        "minimumChecks": [
                            "LOCATE_CURRENT_DECLARATIONS",
                            "LOCATE_REMAINING_MAIN_AND_TEST_REFERENCES",
                            "MAP_REFERENCES_TO_OWNING_TASK",
                            "PLACE_DESTRUCTIVE_CHANGE_WITH_LAST_REFERENCE_UPDATE",
                        ],
                        "fullBuildRequired": False,
                        "languageAnalyzers": {
                            "selection": "BY_DETECTED_PROJECT_LANGUAGE",
                            "java": (
                                "SYMBOL_REFERENCE_SCAN_WITH_TEXT_SEARCH_FALLBACK"
                            ),
                        },
                    },
                },
                "passCriteria": [
                    "EVERY_TASK_HAS_AN_INDEPENDENTLY_VERIFIABLE_END_STATE",
                    "NO_TASK_REQUIRES_A_SUCCESSOR_TO_RESTORE_BUILDABILITY",
                    "TRIGGERED_DESTRUCTIVE_CHANGES_HAVE_NO_UNOWNED_REFERENCES",
                ],
                "failureAction": "REVISE_CANDIDATE_TASK_BOUNDARIES",
                "dispatchBoundary": (
                    "COMPLETE_BEFORE_PLAN_DISPATCH_BATCH_RESERVATION"
                ),
            },
            "gitBinding": {
                "requiredForGitWorkspace": True,
                "branchRole": "DELIVERY_FEATURE",
                "defaultMainlinePreference": ["main", "master"],
                "baseAndIntegrationTargetMustMatch": True,
                "baseCommitRole": "IMMUTABLE_FORK_POINT",
                "stackedDelivery": {
                    "selection": "NEW_FROM_CURRENT_BRANCH",
                    "eligibility": "CLEAN_CURRENT_FEATURE_WORKSPACE",
                    "branchRef": "NEW_CHILD_BRANCH",
                    "baseRef": "CURRENT_PARENT_FEATURE_BRANCH",
                    "baseCommit": "CURRENT_PARENT_FEATURE_HEAD",
                    "integrationTarget": "CURRENT_PARENT_FEATURE_BRANCH",
                    "defaultWhenEligible": True,
                    "controllerGitWrites": False,
                },
                "taskBranchPolicy": "SHARED_DELIVERY_FEATURE_BRANCH",
                "taskBranchBindingsSupported": False,
                "workspacePreparationAuthorization": (
                    "AUTOMATIC_MODE_STASH_CREATE_OR_SWITCH_WITHOUT_"
                    "RECONFIRMATION"
                ),
                "taskCommitPolicy": (
                    "TASK_SCOPED_COMMITS_ON_DELIVERY_BRANCH"
                ),
                "taskCommitConstraints": [
                    "EXPLICIT_TASK_SCOPE",
                    "SERIALIZED_GIT_INDEX_WRITE",
                    "SEPARATE_GIT_WRITE_AUTHORIZATION",
                ],
                "runtimeValidation": [
                    "WORKSPACE_ROOT",
                    "BOUND_BRANCH",
                    "HEAD_INHERITS_BASE_COMMIT",
                    "MAINLINE_CONTAINS_BASE_COMMIT",
                ],
                "description": (
                    "For a Git workspace, copy workspace_status."
                    "suggestedGitBinding into delivery.gitBinding. Each "
                    "writable repository in one Delivery uses the same "
                    "feature branch name, created from that repository's "
                    "mainline (main, falling back to master), unless the user "
                    "explicitly selects a stacked child from the clean current "
                    "feature branch. In that case the parent feature is both "
                    "baseRef and integrationTarget. The parent Delivery must "
                    "reach the same clean, safely released serial boundary "
                    "before the host creates or switches to the child. All TASKs "
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
                "responsibilities": {
                    "controller": (
                        "GRAPH_GATING_RESULT_CONTRACT_VALIDATION_AND_PERSISTENCE"
                    ),
                    "reviewReceiver": "CURRENT_LAYER_TECHNICAL_ACCEPTANCE",
                    "user": "FINAL_BUSINESS_CONFIRMATION",
                },
                "taskReport": {
                    "fullDetails": [
                        "TASK_LOOP",
                        "TASK_REVIEW_LOOP",
                    ],
                },
                "groupReport": {
                    "fullDetails": [
                        "GROUP_JOIN",
                    ],
                    "optionalFullDetails": ["GROUP_REVIEW_LOOP"],
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
                    "resultBodies",
                    "evidence",
                    "reviewFindings",
                    "workspaceChanges",
                ],
                "reviewBoundaries": {
                    "task": (
                        "FROZEN_TASK_ACCEPTANCE_LOCAL_BEHAVIOR_"
                        "PUBLIC_CONTRACT_TARGETED_REGRESSION"
                    ),
                    "group": "OPTIONAL_DIRECT_CHILD_SEAMS_ONLY",
                    "delivery": (
                        "TOP_LEVEL_REQUIREMENT_COVERAGE_SYSTEM_EVIDENCE_"
                        "OPERATIONAL_READINESS_GLOBAL_RISK"
                    ),
                },
                "persistence": {
                    "absentGroupReview": (
                        "NO_GRAPH_NODE_RUN_EVENT_OR_OUTCOME"
                    ),
                    "reviewOutcome": (
                        "LAYER_OWNED_CONCLUSION_FINDINGS_AND_EVIDENCE_REFS"
                    ),
                    "upstreamResults": "CONTEXT_ONLY_NEVER_COPIED",
                },
                "workspaceChangeEvidence": {
                    "source": "CONTROLLER_CAPTURED_AT_RESULT",
                    "scope": "VERIFIED_READ_WRITE_GIT_PROJECT_SCOPES",
                    "comparison": (
                        "FROZEN_BASE_COMMIT_TO_CURRENT_WORKSPACE"
                    ),
                    "semantics": (
                        "WORKSPACE_SNAPSHOT_NOT_EXCLUSIVE_OWNERSHIP"
                    ),
                },
                "description": (
                    "Each acceptance report fully renders only its current "
                    "layer. GROUP reports summarize and link direct child "
                    "reports; the Delivery report summarizes and links the "
                    "root work-item report. Lower-layer payloads, result "
                    "bodies, evidence, review findings, and workspace snapshots "
                    "are not copied upward. A GROUP without a concrete "
                    "direct-child seam has no Review node, run state, outcome, "
                    "or projection section."
                ),
            },
            "databaseChanges": {
                "location": (
                    "TASK definition.execution.loop.payload.databaseChanges"
                ),
                "requiredBeforePreviewWhen": (
                    "FEATURE_ADDS_MODIFIES_OR_DELETES_TABLE_SCHEMA"
                ),
                "designOwner": "PLANNING_CONTEXT_BEFORE_BASELINE_CONFIRMATION",
                "executionRole": "APPLY_FROZEN_DATABASE_CONTRACT_ONLY",
                "assuranceProfile": "STANDARD",
                "changeTypes": ["CREATE", "MODIFY", "DELETE"],
                "requiredFields": [
                    "table",
                    "summary",
                    "changeType",
                    "before",
                    "after",
                    "migration",
                    "resourceClaim",
                ],
                "snapshotRequiredFields": [
                    "comment",
                    "columns",
                    "primaryKey",
                    "uniqueConstraints",
                    "indexes",
                    "foreignKeys",
                ],
                "columnRequiredFields": [
                    "name",
                    "type",
                    "nullable",
                    "default",
                    "comment",
                ],
                "migrationRequiredFields": [
                    "forward",
                    "rollback",
                    "backfill",
                    "compatibility",
                    "verification",
                ],
                "snapshotPolicy": {
                    "CREATE": {"before": "NULL", "after": "COMPLETE"},
                    "MODIFY": {"before": "COMPLETE", "after": "COMPLETE"},
                    "DELETE": {"before": "COMPLETE", "after": "NULL"},
                },
                "resourcePolicy": (
                    "EACH_CHANGE_RESOURCE_CLAIM_MUST_EXIST_IN_LOOP_CLAIMS"
                ),
                "fieldProjection": {
                    "documents": {
                        "index": "database-changes.md",
                        "detailsDirectory": "database-changes/",
                        "oneDocumentPerTable": True,
                    },
                    "sourceOfTruth": "FROZEN_AFTER_SNAPSHOT",
                },
                "changePolicy": (
                    "IMPLEMENTATION_DEVIATION_REQUIRES_REPLAN_REQUIRED_AND_NEW_REVISION"
                ),
                "description": (
                    "When a feature adds, changes, or deletes table schema, "
                    "the planning context must inspect the real current "
                    "schema and declare every affected table before calling "
                    "preview_hierarchy. Each declaration contains complete "
                    "before/after snapshots, forward and rollback migration, "
                    "backfill, rollout compatibility, verification, and an "
                    "exact scheduler resource claim. The controller rejects "
                    "incomplete declarations and LIGHT assurance, then writes "
                    "a TASK database index plus one field-level document per "
                    "table. The frozen after snapshot is the only schema "
                    "source of truth for the TASK Loop; the Loop applies and "
                    "verifies it rather than designing a different schema. "
                    "Any required deviation returns REPLAN_REQUIRED and is "
                    "confirmed as a new immutable Delivery revision."
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
                "httpSnapshotFields": ["method", "path", "contentType"],
                "dubboSnapshotFields": [
                    "service",
                    "method",
                    "signature",
                ],
                "supportedFieldShapes": {
                    "fieldList": (
                        "[{name,type,required?,maxLength?,"
                        "description?,example?}]"
                    ),
                    "typedObject": (
                        "{type,description?,fields|properties:[...]}"
                    ),
                    "fieldAttributes": [
                        "name",
                        "type",
                        "required",
                        "maxLength",
                        "description",
                        "example",
                    ],
                    "emptyContract": "[]",
                    "requestLocationContainers": {
                        "headers": "header",
                        "pathParameters": "path",
                        "queryParameters": "query",
                        "body": "body",
                        "businessParameters": "business",
                        "contextDependencies": "context",
                        "contextDerived": "context",
                        "contextualInputs": "context",
                        "parameters": "",
                    },
                    "responseAliases": {
                        "type": ["type", "controllerReturnType"],
                        "fields": [
                            "fields",
                            "properties",
                            "controllerReturnFields",
                        ],
                        "description": [
                            "description",
                            "summary",
                        ],
                        "ignoredEnvelopeMetadata": [
                            "wireType",
                            "frameworkEnvelope",
                            "wrapping",
                        ],
                    },
                    "emptyRequestText": "无入参",
                    "emptyResponseText": "无出参",
                    "metadataPolicy": "CONTAINERS_ARE_NOT_FIELDS",
                },
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
                        "example",
                    ],
                    "responseComparisonColumns": [
                        "type",
                        "description",
                        "example",
                    ],
                    "dubboComparisonColumns": [
                        "type",
                        "required",
                        "maxLength",
                        "description",
                        "example",
                    ],
                    "protocolLayouts": {
                        "HTTP": [
                            "Path 参数",
                            "Query 参数",
                            "请求头",
                            "请求体",
                            "响应参数",
                        ],
                        "DUBBO": [
                            "接口",
                            "方法",
                            "调用参数",
                            "返回结果",
                        ],
                        "DEFAULT": ["入参", "出参"],
                    },
                    "responseEnvelopePolicy": "IGNORE",
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
                    "compare type, required, description, and example; response "
                    "tables omit required, while Dubbo also exposes maximum "
                    "length. Deleted values use Markdown strikethrough, "
                    "and added or deleted fields show only the present side. "
                    "Field lists and typed objects render their actual fields. "
                    "Known request location containers and Controller response "
                    "aliases are normalized but never rendered as business "
                    "fields; empty lists explicitly render as no input or no "
                    "output. "
                    "This frozen projection is the interface source of truth "
                    "for implementation and later Torna publication; method, "
                    "path, signature, field hierarchy, type, requiredness, "
                    "maximum length, description, and example must stay "
                    "identical. Framework response envelopes are ignored. "
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
                "feature branch name, its own immutable base fork commit, "
                "and the same per-project base/integration target."
            ),
            (
                "One Delivery may span multiple local repositories; every "
                "writable Git project uses the same feature branch name."
            ),
            (
                "Table schema and migration changes are fully designed before "
                "preview, use STANDARD assurance, and execute only the frozen "
                "database before/after contract."
            ),
            "GROUP may recursively contain GROUP or TASK children.",
            "TASK is the only execution leaf and cannot contain children.",
            (
                "Under STANDARD assurance every TASK compiles to TASK_LOOP "
                "followed by its independent TASK_REVIEW_LOOP; an eligible "
                "LIGHT Delivery contains one root TASK_LOOP and omits it."
            ),
            (
                "Every GROUP compiles to GROUP_JOIN; it compiles an independent "
                "GROUP_REVIEW_LOOP only when a concrete direct-child seam "
                "requires verification."
            ),
            (
                "Sibling dependsOn references direct GROUP/TASK siblings; a "
                "GROUP dependency gates the dependent subtree entries."
            ),
            (
                "Under STANDARD assurance the root terminal flows through the "
                "Delivery Acceptance/Readiness boundary represented by "
                "DELIVERY_REVIEW_LOOP and final USER_CONFIRMATION; under LIGHT "
                "it flows directly from the root TASK_LOOP to final "
                "USER_CONFIRMATION."
            ),
            (
                "LIGHT is inferred from actual change content and impact, "
                "requires an audit rationale, and must replan to STANDARD "
                "when the observed impact expands or remains uncertain."
            ),
            (
                "The host planning layer prepares direction, constraints, "
                "confirmed contracts, and acceptance as opaque per-Loop input. "
                "The Graph structures those work items, controls and summarizes "
                "global delivery state, and schedules them without authoring "
                "business requirements; Loops own ordinary implementation."
            ),
            (
                "skillHints may be pre-triggered by the planning host to "
                "clarify direction, remain advisory and unassigned to nodes, "
                "and are reevaluated by each receiving Loop at runtime."
            ),
            "resourceClaims are exact scheduler locks, not file scopes.",
            "Only standard Loop outcomes cross a Loop boundary.",
        ],
    }
