from __future__ import annotations

from .scheduler_contracts_support import (
    GatedLoopError,
    TemporaryDirectory,
    bind_delivery_to_git,
    call_tool,
    database_hierarchy,
    hierarchy_contract,
    isolated_task_hierarchy,
    json,
    patch,
    task_hierarchy,
    unittest,
    validate_hierarchy_definition,
)


class HierarchyContractTests(unittest.TestCase):
    def test_planning_skill_pretrigger_preserves_loop_implementation_ownership(
        self,
    ) -> None:
        guidance = hierarchy_contract(root_kind="TASK")[
            "projectionGuidance"
        ]
        pretrigger = guidance["planningSkillPreTrigger"]
        boundary = guidance["planningContentRouting"]

        self.assertEqual(pretrigger["owner"], "HOST_PLANNING_LAYER")
        self.assertEqual(
            pretrigger["stage"],
            "AFTER_INITIAL_SCOPE_INSPECTION_BEFORE_TASK_BOUNDARIES_AND_PAYLOAD",
        )
        self.assertFalse(pretrigger["blocking"])
        self.assertEqual(
            pretrigger["planningDepth"],
            "DIRECTIONALLY_SUFFICIENT_NOT_EXHAUSTIVE",
        )
        self.assertEqual(
            pretrigger["explicitUserSkillUsage"],
            "ATTEMPT_AT_EACH_APPLICABLE_AND_AVAILABLE_STAGE",
        )
        self.assertEqual(
            pretrigger["defaultImplementationSkillStage"],
            "TASK_LOOP",
        )
        self.assertIn(
            "IMPLEMENTATION_CLASS_OR_TYPE_NAME",
            pretrigger["doNotPromoteSkillSuggestionsToFrozenFacts"],
        )
        self.assertFalse(pretrigger["controllerEnforcesInvocation"])
        self.assertTrue(pretrigger["runtimeReevaluationRequired"])
        self.assertEqual(
            boundary["planningCompleteness"],
            "CLEAR_DIRECTION_CONSTRAINTS_AND_ACCEPTANCE_NOT_EXHAUSTIVE_DESIGN",
        )
        self.assertIn(
            "IMPLEMENTATION_CLASS_AND_INTERNAL_METHOD_NAMES",
            boundary["loopOwnsAndExpands"],
        )
        self.assertEqual(
            boundary["exactImplementationIdentifierMayFreezeOnlyWhen"],
            "EXPLICITLY_STATED_BY_REQUIREMENT_OR_CONFIRMED_EXTERNAL_CONTRACT",
        )
        self.assertEqual(boundary["owner"], "HOST_PLANNING_LAYER")
        self.assertEqual(
            boundary["graphRole"],
            "STRUCTURE_WORK_ITEMS_MATERIALIZE_DAG_FINGERPRINT_CONTROL_DEPENDENCIES_AND_RESOURCES_DISPATCH_AGGREGATE_PROGRESS_RESULTS_AND_GLOBAL_STATE",
        )
        self.assertIn(
            "AUTHOR_OR_INVENT_BUSINESS_REQUIREMENTS",
            boundary["graphDoesNot"],
        )
        self.assertIn("FRONTIER_AND_GLOBAL_PROGRESS", boundary["aggregation"])
        self.assertEqual(
            boundary["nodePayloadRouting"],
            "DELIVER_EXACT_NODE_PAYLOAD_TO_CORRESPONDING_LOOP",
        )
        self.assertEqual(
            boundary["explicitSkillHintRouting"],
            "COPY_TO_ASSIGNMENT_MANUAL_ACTION_HANDOFF_AND_LOOP_CONTEXT",
        )
        self.assertTrue(boundary["skillDefaultsAndExamplesAreNotRequirementFacts"])
        self.assertFalse(boundary["controllerAnalyzesPlanningContent"])

    def test_task_split_preflight_is_blocking_and_planning_owned(self) -> None:
        preflight = hierarchy_contract(root_kind="GROUP")[
            "projectionGuidance"
        ]["taskSplitIntegrityPreflight"]

        self.assertEqual(preflight["owner"], "HOST_PLANNING_LAYER")
        self.assertEqual(
            preflight["stage"],
            "AFTER_CANDIDATE_HIERARCHY_BEFORE_PREVIEW_OR_REFREEZE",
        )
        self.assertTrue(preflight["blocking"])
        self.assertFalse(preflight["controllerAnalyzesLoopPayload"])
        self.assertEqual(
            preflight["levels"]["L1"]["mode"],
            "PLUGGABLE_TARGETED_CODE_ANALYSIS",
        )
        self.assertIn(
            "DELETE_SYMBOL",
            preflight["levels"]["L1"]["triggers"],
        )
        self.assertEqual(
            preflight["levels"]["L1"]["triggerSource"],
            "EXPLICIT_REQUIREMENT_OR_CONFIRMED_CURRENT_CODE_IMPACT_NOT_PLANNER_INVENTION",
        )
        self.assertFalse(
            preflight["levels"]["L1"]["fullBuildRequired"]
        )
        self.assertEqual(
            preflight["dispatchBoundary"],
            "COMPLETE_BEFORE_PLAN_DISPATCH_BATCH_RESERVATION",
        )

    def test_database_changes_are_frozen_before_execution(self) -> None:
        hierarchy = database_hierarchy()
        normalized = validate_hierarchy_definition(hierarchy)
        contract = hierarchy_contract(root_kind="TASK")
        guidance = contract["projectionGuidance"]["databaseChanges"]

        self.assertEqual(
            guidance["requiredBeforePreviewWhen"],
            "FEATURE_ADDS_MODIFIES_OR_DELETES_TABLE_SCHEMA",
        )
        self.assertEqual(
            guidance["executionRole"],
            "APPLY_FROZEN_DATABASE_CONTRACT_ONLY",
        )
        self.assertEqual(guidance["assuranceProfile"], "STANDARD")
        self.assertEqual(
            guidance["fieldProjection"]["documents"],
            {
                "index": "database-changes.md",
                "detailsDirectory": "database-changes/",
                "oneDocumentPerTable": True,
            },
        )
        self.assertEqual(
            normalized["root"]["definition"]["execution"]["loop"][
                "payload"
            ]["databaseChanges"][0]["table"],
            "orders",
        )

    def test_database_change_rejects_incomplete_or_unlocked_design(self) -> None:
        missing_before = database_hierarchy()
        missing_before["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["databaseChanges"][0]["before"] = None
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(missing_before)
        self.assertEqual(
            caught.exception.code,
            "DATABASE_CHANGE_CONTRACT_INVALID",
        )

        unlocked = database_hierarchy()
        unlocked["root"]["definition"]["execution"]["loop"][
            "resourceClaims"
        ] = []
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(unlocked)
        self.assertEqual(
            caught.exception.code,
            "DATABASE_CHANGE_CONTRACT_INVALID",
        )

    def test_database_change_rejects_light_assurance(self) -> None:
        hierarchy = database_hierarchy()
        hierarchy["delivery"]["assuranceProfile"] = "LIGHT"
        hierarchy["delivery"]["assuranceRationale"] = "局部字段变更。"
        hierarchy["delivery"]["reviewLoop"] = None
        hierarchy["root"]["reviewLoop"] = None
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(hierarchy)
        self.assertEqual(caught.exception.code, "DELIVERY_ASSURANCE_INVALID")
        self.assertIn("STANDARD", caught.exception.message)

    def test_requirement_key_is_stable_and_exposed_for_delivery_continuity(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"]["requirementKey"] = "mprotein-443"

        normalized = validate_hierarchy_definition(hierarchy)
        contract = hierarchy_contract(root_kind="TASK")

        self.assertEqual(
            normalized["delivery"]["requirementKey"],
            "MPROTEIN-443",
        )
        self.assertIn(
            "requirementKey",
            contract["inputSchema"]["properties"]["delivery"][
                "properties"
            ],
        )
        self.assertEqual(
            contract["projectionGuidance"]["deliveryContinuity"][
                "identity"
            ],
            "requirementKey -> delivery.id",
        )
        self.assertEqual(
            contract["projectionGuidance"]["deliveryContinuity"][
                "duplicatePolicy"
            ],
            "REJECT_DIFFERENT_DELIVERY_ID",
        )

    def test_execution_interaction_is_controller_owned_and_exact(self) -> None:
        interaction = hierarchy_contract(root_kind="TASK")[
            "projectionGuidance"
        ]["executionInteraction"]

        self.assertEqual(interaction["owner"], "CONTROLLER")
        self.assertEqual(
            interaction["artifactGate"],
            "CHOICE_READY_ARTIFACTS_READY",
        )
        self.assertEqual(interaction["hostMapping"], "MECHANICAL_NO_REWRITE")
        self.assertEqual(
            interaction["selectionTool"],
            "select_execution_mode",
        )
        self.assertEqual(
            interaction["automaticResumeTool"],
            "resume_execution_mode",
        )
        self.assertEqual(
            interaction["manualStartTool"],
            "start_manual_handoff",
        )
        self.assertEqual(
            interaction["manualExecutionBoundary"],
            "MANUAL_TASK_ONLY_REVIEWS_REMAIN_AUTOMATIC",
        )
        choice = interaction["executionChoice"]
        self.assertEqual(choice["defaultOptionId"], "AUTOMATIC")
        self.assertEqual(
            [option["id"] for option in choice["options"]],
            ["AUTOMATIC", "MANUAL"],
        )
        automatic = choice["options"][0]
        self.assertEqual(
            automatic["workspaceStrategy"],
            "CURRENT_WORKSPACE_SERIAL",
        )
        serialized_interaction = json.dumps(interaction).lower()
        self.assertNotIn("linked", serialized_interaction)
        self.assertNotIn("background", serialized_interaction)
        self.assertEqual(
            interaction["directTextAction"],
            "CONTINUE_REQUIREMENT_DISCUSSION",
        )

    def test_preview_selects_the_current_hosts_native_question_tool(
        self,
    ) -> None:
        for host_adapter, tool_name in (
            ("codex", "request_user_input"),
            ("claude-code", "AskUserQuestion"),
            ("zcode", "AskUserQuestion"),
        ):
            with self.subTest(host_adapter=host_adapter):
                with TemporaryDirectory() as root:
                    preview = call_tool(
                        "preview_hierarchy",
                        {
                            "hierarchy": isolated_task_hierarchy(
                                f"d-{host_adapter}",
                                f"t-{host_adapter}",
                            )
                        },
                        root=root,
                        trusted_host_adapter=host_adapter,
                    )

                    self.assertEqual(
                        preview["nextAction"],
                        "PRESENT_HOST_NATIVE_EXECUTION_CHOICE",
                    )
                    self.assertEqual(
                        preview["executionChoice"]["activeHostMapping"],
                        {
                            "hostAdapterId": host_adapter,
                            "tool": tool_name,
                            "requiredWhenCallable": True,
                        },
                    )

    def test_every_contract_example_is_valid(self) -> None:
        for root_kind in ("TASK", "GROUP"):
            with self.subTest(root_kind=root_kind):
                contract = hierarchy_contract(
                    root_kind=root_kind,
                )
                normalized = validate_hierarchy_definition(
                    contract["example"]
                )
                self.assertEqual(
                    normalized["root"]["definition"]["kind"],
                    root_kind,
                )

    def test_contract_places_detail_inside_opaque_loop_payload(
        self,
    ) -> None:
        contract = hierarchy_contract(root_kind="TASK")
        definition_properties = contract["inputSchema"]["$defs"][
            "taskRootDefinition"
        ]["properties"]

        self.assertEqual(
            set(definition_properties),
            {
                "schemaVersion",
                "id",
                "kind",
                "parentId",
                "title",
                "summary",
                "execution",
            },
        )
        self.assertEqual(
            definition_properties["execution"]["properties"]["loop"],
            {"$ref": "#/$defs/loop"},
        )
        payload = contract["inputSchema"]["$defs"]["loop"][
            "properties"
        ]["payload"]
        self.assertTrue(payload["additionalProperties"])
        self.assertEqual(
            set(contract["inputSchema"]["properties"]),
            {"delivery", "root"},
        )
        skill_hints = contract["inputSchema"]["$defs"][
            "taskRootNode"
        ]["properties"]["skillHints"]
        self.assertEqual(
            skill_hints["items"],
            {"$ref": "#/$defs/skillHint"},
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["skillHint"]["required"],
            ["name", "purpose"],
        )
        self.assertIn(
            "runtime",
            skill_hints["description"],
        )
        self.assertIn(
            "advisory",
            " ".join(contract["invariants"]).lower(),
        )
        group_children = contract["inputSchema"]["$defs"][
            "groupChildNode"
        ]["properties"]["children"]["items"]["oneOf"]
        self.assertEqual(
            {
                item["$ref"]
                for item in group_children
            },
            {
                "#/$defs/groupChildNode",
                "#/$defs/taskChildNode",
            },
        )
        delivery_properties = contract["inputSchema"]["properties"][
            "delivery"
        ]["properties"]
        self.assertEqual(
            delivery_properties["reviewLoop"]["oneOf"],
            [{"$ref": "#/$defs/loop"}, {"type": "null"}],
        )
        self.assertEqual(
            delivery_properties["assuranceProfile"]["enum"],
            ["LIGHT", "STANDARD"],
        )
        self.assertNotIn(
            "assuranceProfile",
            contract["inputSchema"]["properties"]["delivery"]["required"],
        )
        git_binding = contract["inputSchema"]["properties"]["delivery"][
            "properties"
        ]["gitBinding"]
        self.assertEqual(
            set(git_binding["properties"]),
            {
                "branchRef",
                "baseRef",
                "baseCommit",
                "integrationTarget",
            },
        )
        self.assertNotIn(
            "gitBinding",
            contract["inputSchema"]["properties"]["delivery"]["required"],
        )
        self.assertEqual(
            contract["projectionGuidance"]["gitBinding"][
                "defaultMainlinePreference"
            ],
            ["main", "master"],
        )
        self.assertEqual(
            contract["projectionGuidance"]["gitBinding"][
                "taskBranchPolicy"
            ],
            "SHARED_DELIVERY_FEATURE_BRANCH",
        )
        self.assertFalse(
            contract["projectionGuidance"]["gitBinding"][
                "taskBranchBindingsSupported"
            ],
        )
        self.assertEqual(
            contract["projectionGuidance"]["gitBinding"][
                "taskCommitPolicy"
            ],
            "TASK_SCOPED_COMMITS_ON_DELIVERY_BRANCH",
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["taskRootNode"]["properties"][
                "reviewLoop"
            ]["oneOf"],
            [{"$ref": "#/$defs/loop"}, {"type": "null"}],
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["groupRootNode"]["properties"][
                "reviewLoop"
            ],
            {
                "oneOf": [
                    {"$ref": "#/$defs/loop"},
                    {"type": "null"},
                ],
                "description": (
                    "Optional direct-child seam Review. Use null when the "
                    "GROUP is only a coordination or join boundary."
                ),
            },
        )
        acceptance_guidance = contract["projectionGuidance"][
            "acceptanceReports"
        ]
        self.assertEqual(
            acceptance_guidance["scope"],
            "CURRENT_LAYER",
        )
        self.assertEqual(
            acceptance_guidance["responsibilities"],
            {
                "controller": (
                    "GRAPH_GATING_RESULT_CONTRACT_VALIDATION_AND_PERSISTENCE"
                ),
                "reviewReceiver": "CURRENT_LAYER_TECHNICAL_ACCEPTANCE",
                "user": "FINAL_BUSINESS_CONFIRMATION",
            },
        )
        self.assertEqual(
            acceptance_guidance["groupReport"]["childReferences"],
            ["status", "summary", "acceptanceLink"],
        )
        self.assertEqual(
            acceptance_guidance["deliveryReport"]["rootReference"],
            ["status", "summary", "acceptanceLink"],
        )
        self.assertEqual(
            acceptance_guidance["nonDuplicatedFromLowerLayers"],
            [
                "payload",
                "resultBodies",
                "evidence",
                "reviewFindings",
                "workspaceChanges",
            ],
        )
        self.assertEqual(
            acceptance_guidance["workspaceChangeEvidence"],
            {
                "source": "CONTROLLER_CAPTURED_AT_RESULT",
                "scope": "VERIFIED_READ_WRITE_GIT_PROJECT_SCOPES",
                "comparison": (
                    "FROZEN_BASE_COMMIT_TO_CURRENT_WORKSPACE"
                ),
                "semantics": (
                    "WORKSPACE_SNAPSHOT_NOT_EXCLUSIVE_OWNERSHIP"
                ),
                "contentStorage": "METADATA_ONLY_NO_SOURCE_DIFF",
                "contentRead": (
                    "AUTHORIZED_WORKSPACE_OR_COMMIT_ON_DEMAND"
                ),
            },
        )
        interface_guidance = contract["projectionGuidance"]["interfaces"]
        self.assertEqual(
            interface_guidance["location"],
            "TASK definition.execution.loop.payload.interfaces",
        )
        self.assertEqual(
            interface_guidance["protocolExamples"],
            ["HTTP", "DUBBO", "GRPC", "GRAPHQL", "MESSAGE"],
        )
        self.assertEqual(
            interface_guidance["requiredFields"],
            [
                "protocol",
                "name",
                "summary",
                "changeType",
                "before",
                "after",
            ],
        )
        self.assertEqual(
            interface_guidance["changeTypes"],
            ["CREATE", "MODIFY", "DELETE"],
        )
        self.assertEqual(
            interface_guidance["snapshotRequiredFields"],
            ["request", "response"],
        )
        self.assertIn(
            "identifier",
            interface_guidance["genericSnapshotFields"],
        )
        self.assertIn(
            "method",
            interface_guidance["httpSnapshotFields"],
        )
        self.assertIn(
            "path",
            interface_guidance["httpSnapshotFields"],
        )
        self.assertIn(
            "service",
            interface_guidance["dubboSnapshotFields"],
        )
        self.assertIn(
            "method",
            interface_guidance["dubboSnapshotFields"],
        )
        self.assertIn(
            "signature",
            interface_guidance["dubboSnapshotFields"],
        )
        self.assertEqual(
            interface_guidance["supportedFieldShapes"],
            {
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
        )
        self.assertEqual(
            interface_guidance["fieldProjection"],
            {
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
                    "DUBBO": ["接口", "方法", "调用参数", "返回结果"],
                    "DEFAULT": ["入参", "出参"],
                },
                "responseEnvelopePolicy": "IGNORE",
                "deletedValueStyle": "MARKDOWN_STRIKETHROUGH",
                "singleSidedChangeStyle": "PRESENT_VALUE_ONLY",
                "transitionFormat": "BEFORE_TO_AFTER",
            },
        )
        self.assertIn(
            "explicit before and after snapshots",
            interface_guidance["description"],
        )
        self.assertIn(
            "has no interface projection or link",
            interface_guidance["description"],
        )
        self.assertIn(
            "source of truth",
            interface_guidance["description"],
        )
        self.assertIn("Torna", interface_guidance["description"])

    def test_git_binding_normalizes_and_rejects_invalid_contracts(
        self,
    ) -> None:
        source = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="feature/d-service",
            base_commit="a" * 40,
        )
        normalized = validate_hierarchy_definition(source)
        self.assertEqual(
            normalized["delivery"]["gitBinding"],
            {
                "branchRef": "feature/d-service",
                "baseRef": "main",
                "baseCommit": "a" * 40,
                "integrationTarget": "main",
            },
        )

        wrong_target = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="feature/d-service",
            base_commit="a" * 40,
        )
        wrong_target["delivery"]["gitBinding"][
            "integrationTarget"
        ] = "release"
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(wrong_target)
        self.assertEqual(
            caught.exception.code,
            "DELIVERY_GIT_BINDING_INVALID",
        )

        unsafe_branch = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="../feature",
            base_commit="a" * 40,
        )
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(unsafe_branch)

        full_ref = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="refs/heads/feature/d-service",
            base_commit="a" * 40,
        )
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(full_ref)

        mainline_delivery = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="master",
            base_commit="a" * 40,
        )
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(mainline_delivery)

    def test_posix_project_roots_remain_case_sensitive(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"]["projectScopes"] = [
            {
                "id": "upper",
                "workspaceRoot": "C:\\srv\\Repo",
                "access": "READ_ONLY",
            },
            {
                "id": "lower",
                "workspaceRoot": "C:\\srv\\repo",
                "access": "READ_ONLY",
            },
        ]
        with patch(
            "hdg.model_core.os.path.normcase",
            side_effect=lambda value: value,
        ):
            normalized = validate_hierarchy_definition(hierarchy)

        self.assertEqual(
            [scope["id"] for scope in normalized["delivery"]["projectScopes"]],
            ["lower", "upper"],
        )
