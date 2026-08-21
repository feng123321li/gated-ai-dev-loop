from __future__ import annotations

from .scheduler_contracts_support import (
    ControllerContext,
    GatedLoopError,
    LayeredDeliveryController,
    Mock,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    _tool_result,
    call_tool,
    freeze_hierarchy,
    group_hierarchy,
    hierarchy_contract,
    inspect,
    re,
    task_hierarchy,
    tool_definitions,
    validate_tool_arguments,
)


class McpSurfaceTestsPart1:
    def test_progress_tool_result_defaults_to_a_chinese_table(self) -> None:
        result = _tool_result(
            {
                "ok": True,
                "result": {
                    "progressMonitor": {
                        "alerts": [
                            {
                                "code": "SUSPECT_NOT_STARTED",
                                "messageZh": "领取后仍没有首次独立心跳。",
                            }
                        ],
                        "markdownTable": (
                            "| 节点 | 当前阶段 |\n"
                            "|---|---|\n"
                            "| provider review | 运行测试 |"
                        ),
                        "changeFingerprint": "progress-fingerprint",
                        "waitDirective": {
                            "mode": "HOST_NATIVE_EVENT_OR_DEADLINE",
                            "pollNotBefore": "2026-08-12T08:00:10Z",
                            "interruptOn": [
                                "NATIVE_RECEIVER_COMPLETED",
                                "NATIVE_RECEIVER_NEEDS_ATTENTION",
                            ],
                            "onTimeout": "CALL_GRAPH_FRONTIER_ONCE",
                            "suppressUnchangedCommentary": True,
                        },
                    }
                },
            },
            is_error=False,
            modern=True,
        )

        rendered = result["content"][0]["text"]
        self.assertIn("## 后台执行进度", rendered)
        self.assertIn("领取后仍没有首次独立心跳", rendered)
        self.assertIn("| provider review | 运行测试 |", rendered)
        self.assertNotIn("SUSPECT_NOT_STARTED", rendered)
        self.assertIn("不要立即再次调用 `graph_frontier`", rendered)
        self.assertIn("2026-08-12T08:00:10Z", rendered)
        self.assertIn("原生 receiver 完成或需要关注", rendered)
        self.assertIn("progressMonitor", result["structuredContent"]["result"])

    def test_shared_controller_executes_without_mcp_context(self) -> None:
        with TemporaryDirectory() as root:
            controller = LayeredDeliveryController()
            result = controller.execute(
                "workspace_status",
                {},
                context=ControllerContext(project_root=root),
            )
            self.assertEqual(result["status"], "ABSENT")
            with self.assertRaises(GatedLoopError) as caught:
                controller.execute(
                    "missing",
                    {},
                    context=ControllerContext(project_root=root),
                )
            self.assertEqual(
                caught.exception.code,
                "CONTROLLER_OPERATION_UNKNOWN",
            )

    def test_tool_schemas_are_closed_and_only_destructive_calls_prompt(
        self,
    ) -> None:
        tools = tool_definitions()
        self.assertTrue(tools)
        self.assertEqual(len(tools), 33)
        self.assertNotIn("claim_current_task", {tool["name"] for tool in tools})
        descriptions = {tool["name"]: tool["description"] for tool in tools}
        self.assertIn(
            "Never call it back-to-back",
            descriptions["graph_frontier"],
        )
        self.assertIn(
            "read-only periodic observation",
            descriptions["graph_status"],
        )
        self.assertIn(
            "obey postActionWait",
            descriptions["plan_dispatch_batch"],
        )
        record_schema = next(
            tool["inputSchema"]
            for tool in tools
            if tool["name"] == "record_loop_result"
        )
        result_properties = record_schema["properties"]["outcome"][
            "properties"
        ]["result"]["properties"]
        self.assertIn("affectedScopes", result_properties)
        self.assertIn("verificationEvidence", result_properties)
        self.assertIn("evidenceWorkspaceSnapshots", result_properties)
        self.assertIn("evidenceScopeSnapshots", result_properties)
        self.assertIn("validationDecision", result_properties)
        self.assertIn("reviewFindings", result_properties)
        self.assertIn("taskAcceptance", result_properties)
        self.assertIn("groupIntegration", result_properties)
        self.assertIn("deliveryReadiness", result_properties)
        reused_ref = result_properties["validationDecision"]["properties"][
            "reusedEvidenceRefs"
        ]["items"]
        self.assertEqual(
            set(reused_ref["required"]),
            {"nodeId", "attempt", "evidenceId"},
        )
        self.assertTrue(
            all(
                tool["inputSchema"]["additionalProperties"] is False
                for tool in tools
            )
        )
        human = {
            tool["name"]
            for tool in tools
            if tool.get("_meta", {}).get(
                "anthropic/requiresUserInteraction"
            )
        }
        self.assertEqual(
            human,
            {
                "archive_delivery",
                "close_delivery",
                "cancel_graph_run",
                "refreeze_task_requirement",
                "unfreeze_task_requirement",
                "handoff_ready_automatic_task",
            },
        )
        by_name = {tool["name"]: tool for tool in tools}
        result_tool = by_name["record_loop_result"]
        result_description = result_tool["inputSchema"]["properties"][
            "outcome"
        ]["properties"]["result"]["description"]
        self.assertIn(
            "receiver owns the technical acceptance judgment",
            result_description,
        )
        self.assertIn(
            "Controller validates only structure and declared terminal consistency",
            result_description,
        )
        archive_tool = by_name["archive_delivery"]
        self.assertEqual(
            archive_tool["inputSchema"]["required"],
            ["root_id"],
        )
        self.assertTrue(archive_tool["annotations"]["destructiveHint"])
        self.assertTrue(archive_tool["annotations"]["idempotentHint"])
        close_tool = by_name["close_delivery"]
        self.assertEqual(
            close_tool["inputSchema"]["required"],
            ["root_id", "confirmed", "closed_by", "summary"],
        )
        self.assertTrue(close_tool["annotations"]["destructiveHint"])
        self.assertTrue(close_tool["annotations"]["idempotentHint"])
        progress_tool = by_name["report_loop_progress"]
        self.assertFalse(progress_tool["annotations"]["readOnlyHint"])
        self.assertEqual(
            progress_tool["inputSchema"]["properties"]["phase"]["enum"],
            [
                "STARTING",
                "INSPECTING",
                "TESTING",
                "INVESTIGATING",
                "FIXING",
                "REVIEWING",
                "VERIFYING",
                "WAITING",
            ],
        )
        self.assertIn(
            "user's current language",
            progress_tool["inputSchema"]["properties"]["summary_zh"][
                "description"
            ],
        )
        self.assertNotIn(
            "Simplified Chinese",
            progress_tool["inputSchema"]["properties"]["summary_zh"][
                "description"
            ],
        )
        self.assertEqual(
            set(
                by_name["workspace_status"]["inputSchema"][
                    "properties"
                ]
            ),
            {
                "root_id",
                "base_ref",
                "confirmed_dirty_state_fingerprint",
            },
        )
        self.assertEqual(
            by_name["workspace_status"]["inputSchema"]["required"],
            [],
        )
        self.assertNotIn("available_agents", by_name)
        preview_schema = by_name["preview_hierarchy"]["inputSchema"]
        self.assertEqual(preview_schema["required"], [])
        self.assertIn("hierarchy", preview_schema["properties"])
        self.assertIn("hierarchy_file", preview_schema["properties"])
        self.assertEqual(
            by_name["select_execution_mode"]["inputSchema"]["required"],
            [
                "root_id",
                "selection",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        )
        selection_properties = by_name["select_execution_mode"][
            "inputSchema"
        ]["properties"]
        self.assertEqual(
            selection_properties["selection"]["enum"],
            ["AUTOMATIC", "MANUAL"],
        )
        self.assertNotIn("confirmed", selection_properties)
        self.assertIn(
            "call resume_execution_mode for AUTOMATIC or "
            "start_manual_handoff for MANUAL",
            by_name["select_execution_mode"]["description"],
        )
        self.assertEqual(
            by_name["resume_execution_mode"]["inputSchema"]["required"],
            [
                "root_id",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
            ],
        )
        self.assertNotIn(
            "confirmed_by",
            by_name["resume_execution_mode"]["inputSchema"]["properties"],
        )
        manual_handoff_schema = by_name["create_manual_handoff"][
            "inputSchema"
        ]
        self.assertEqual(
            manual_handoff_schema["required"],
            [
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        )
        self.assertIn("hierarchy", manual_handoff_schema["properties"])
        self.assertIn(
            "hierarchy_file", manual_handoff_schema["properties"]
        )
        self.assertEqual(
            by_name["start_manual_handoff"]["inputSchema"]["required"],
            [
                "root_id",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "started_by",
            ],
        )
        self.assertIn(
            "never weakens or skips configured STANDARD Review nodes",
            by_name["start_manual_handoff"]["description"],
        )
        self.assertNotIn(
            "confirmed",
            by_name["create_manual_handoff"]["inputSchema"]["properties"],
        )
        manual_handoff_properties = by_name["create_manual_handoff"][
            "inputSchema"
        ]["properties"]
        self.assertIn("expected_current_revision", manual_handoff_properties)
        self.assertIn("continuity_basis", manual_handoff_properties)
        self.assertIn("revision_reason", manual_handoff_properties)
        self.assertEqual(
            manual_handoff_properties["continuity_basis"]["enum"],
            ["USER_EXPLICIT_SAME_DELIVERY"],
        )
        self.assertIn(
            ".layered-delivery/<delivery-id>/handoff-<fingerprint>.md",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertIn(
            "same overview, baseline, progress, acceptance, revisions, "
            "and work-items projections",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertIn(
            "shared scheduler.db",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertIn(
            "root overview.md",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertNotIn("_meta", by_name["create_manual_handoff"])
        self.assertNotIn("recommend_executors", by_name)
        dispatch_plan_schema = by_name[
            "plan_dispatch_batch"
        ]["inputSchema"]
        self.assertEqual(
            dispatch_plan_schema["required"],
            [
                "root_id",
                "expected_graph_fingerprint",
            ],
        )
        self.assertEqual(
            set(dispatch_plan_schema["properties"]),
            {"root_id", "expected_graph_fingerprint"},
        )
        self.assertNotIn("_meta", by_name["plan_dispatch_batch"])
        dispatch_schema = by_name["dispatch_loop"]["inputSchema"]
        self.assertEqual(
            dispatch_schema["required"],
            [
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "dispatch_mode",
                "receiver_context_id",
                "operation_id",
            ],
        )
        self.assertIn(
            "Caller-declared host-native receiving Agent context ID",
            dispatch_schema["properties"]["receiver_context_id"][
                "description"
            ],
        )
        self.assertEqual(
            set(dispatch_schema["properties"]),
            {
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "dispatch_mode",
                "dispatch_transport",
                "dispatch_reservation_id",
                "dispatch_decision_fingerprint",
                "receiver_context_id",
                "operation_id",
            },
        )
        for mutation in (
            "heartbeat_loop",
            "report_loop_progress",
            "pause_loop",
            "record_loop_result",
        ):
            self.assertIn(
                "operation_id",
                by_name[mutation]["inputSchema"]["required"],
            )
        heartbeat_schema = by_name["heartbeat_loop"]["inputSchema"]
        expected_command = heartbeat_schema["properties"][
            "expected_command_seconds"
        ]
        self.assertEqual(expected_command["minimum"], 61)
        self.assertEqual(expected_command["maximum"], 1800)
        recovery = by_name["handoff_ready_automatic_task"]
        self.assertEqual(
            recovery["inputSchema"]["required"],
            [
                "root_id",
                "node_id",
                "expected_graph_fingerprint",
                "handoff_request_id",
                "confirmed_no_code_changes",
                "confirmed_by",
                "reason",
            ],
        )
        self.assertTrue(
            recovery["inputSchema"]["properties"][
                "confirmed_no_code_changes"
            ]["const"]
        )
        self.assertTrue(recovery["annotations"]["idempotentHint"])
        self.assertIn("Review", recovery["description"])
        pause_schema = by_name["pause_loop"]["inputSchema"]
        self.assertEqual(
            set(pause_schema["properties"]),
            {"root_id", "node_id", "operation_id"},
        )
        freeze = by_name["freeze_hierarchy"]
        self.assertNotIn("_meta", freeze)
        freeze_schema = freeze["inputSchema"]
        self.assertNotIn("confirmed", freeze_schema["properties"])
        self.assertNotIn("execution_mode", freeze_schema["properties"])
        self.assertNotIn(
            "execution_mode",
            inspect.signature(freeze_hierarchy).parameters,
        )
        self.assertNotIn(
            "execution_mode",
            inspect.signature(SchedulerRepository.freeze).parameters,
        )
        self.assertIn(
            "expected_delivery_revision",
            freeze_schema["required"],
        )
        self.assertIn(
            "authorized_project_ids",
            freeze_schema["required"],
        )
        self.assertTrue(
            freeze_schema["properties"]["authorized_project_ids"][
                "uniqueItems"
            ]
        )
        revision_prepare = by_name["prepare_delivery_revision"]
        self.assertNotIn("_meta", revision_prepare)
        self.assertNotIn(
            "hierarchy",
            revision_prepare["inputSchema"]["required"],
        )
        self.assertIn(
            "hierarchy_file",
            revision_prepare["inputSchema"]["properties"],
        )
        self.assertEqual(
            by_name["delivery_revision_history"]["inputSchema"][
                "required"
            ],
            ["root_id"],
        )
        final_confirmation = by_name["record_user_confirmation"][
            "inputSchema"
        ]["properties"]["confirmed"]
        self.assertNotIn(
            "_meta",
            by_name["record_user_confirmation"],
        )
        self.assertEqual(final_confirmation["type"], "boolean")
        self.assertIs(final_confirmation["const"], True)

        unfreeze = by_name["unfreeze_task_requirement"]["inputSchema"]
        self.assertEqual(
            unfreeze["required"],
            [
                "root_id",
                "task_id",
                "expected_revision",
                "authorized_by",
                "reason",
            ],
        )
        refreeze = by_name["refreeze_task_requirement"]["inputSchema"]
        self.assertEqual(
            refreeze["required"],
            [
                "root_id",
                "task_id",
                "expected_revision",
                "requirement",
                "confirmed_by",
            ],
        )
        self.assertEqual(
            set(refreeze["properties"]["requirement"]["properties"]),
            {"title", "summary", "payload"},
        )

    def test_prepare_hierarchy_exposes_the_complete_v3_input_schema(
        self,
    ) -> None:
        by_name = {
            tool["name"]: tool
            for tool in tool_definitions()
        }
        prepare_schema = by_name["prepare_hierarchy"]["inputSchema"]
        hierarchy_schema = prepare_schema["properties"]["hierarchy"]

        self.assertIs(hierarchy_schema["additionalProperties"], False)
        self.assertEqual(
            set(hierarchy_schema["properties"]),
            {"delivery", "root"},
        )
        self.assertEqual(
            hierarchy_schema["properties"]["root"],
            {
                "oneOf": [
                    {"$ref": "#/$defs/groupRootNode"},
                    {"$ref": "#/$defs/taskRootNode"},
                ]
            },
        )
        self.assertEqual(
            prepare_schema["$defs"],
            hierarchy_contract(root_kind="TASK")["inputSchema"]["$defs"],
        )
        self.assertTrue(
            prepare_schema["$defs"]["loop"]["properties"]["payload"][
                "additionalProperties"
            ]
        )
        project_scopes = hierarchy_schema["properties"]["delivery"][
            "properties"
        ]["projectScopes"]
        self.assertEqual(project_scopes["minItems"], 1)
        self.assertEqual(
            project_scopes["items"]["properties"]["access"]["enum"],
            ["READ_ONLY", "READ_WRITE"],
        )

    def test_prepare_hierarchy_rejects_invalid_schema_before_controller(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["schemaVersion"] = 3
        controller = Mock(spec=LayeredDeliveryController)

        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root="unused",
                controller=controller,
            )

        self.assertEqual(
            caught.exception.code,
            "MCP_TOOL_ARGUMENT_INVALID",
        )
        self.assertEqual(
            caught.exception.details["unknownFields"],
            ["schemaVersion"],
        )
        controller.execute.assert_not_called()

    def test_prepare_hierarchy_preflights_semantic_contract_before_controller(
        self,
    ) -> None:
        hierarchy = group_hierarchy()
        hierarchy["root"]["children"][0]["definition"]["parentId"] = (
            "g-other"
        )
        controller = Mock(spec=LayeredDeliveryController)

        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root="unused",
                controller=controller,
            )

        self.assertEqual(
            caught.exception.code,
            "MCP_TOOL_ARGUMENT_INVALID",
        )
        self.assertEqual(
            caught.exception.details["schemaError"]["code"],
            "WORK_ITEM_PARENT_INVALID",
        )
        controller.execute.assert_not_called()

    def test_prepare_hierarchy_preflight_accepts_both_root_kinds(
        self,
    ) -> None:
        task = task_hierarchy()
        task["root"]["definition"]["execution"]["loop"]["payload"][
            "custom"
        ] = {"nested": ["opaque", 1, True]}

        self.assertEqual(
            validate_tool_arguments(
                "prepare_hierarchy",
                {"hierarchy": task},
            ),
            {"hierarchy": task},
        )
        self.assertEqual(
            validate_tool_arguments(
                "prepare_hierarchy",
                {"hierarchy": group_hierarchy()},
            ),
            {"hierarchy": group_hierarchy()},
        )

    def test_argument_validation_rejects_unknown_fields(self) -> None:
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "workspace_status",
                {"legacyScope": ["src/**"]},
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments("missing_tool", {})
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "recommend_executors",
                {
                    "root_id": "d-service",
                    "temporarily_unavailable_agent_ids": ["codex"],
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "record_user_confirmation",
                {
                    "root_id": "d-service",
                    "confirmed": "true",
                    "confirmed_by": "human",
                    "summary": "accepted",
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "freeze_hierarchy",
                {
                    "root_id": "d-service",
                    "expected_hierarchy_fingerprint": "fingerprint",
                    "execution_mode": "adjust",
                    "confirmed_by": "human",
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "freeze_hierarchy",
                {
                    "root_id": "d-service",
                    "expected_hierarchy_fingerprint": "fingerprint",
                    "execution_mode": "active",
                    "confirmed": True,
                    "confirmed_by": "human",
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "record_user_confirmation",
                {
                    "root_id": "d-service",
                    "confirmed": 1,
                    "confirmed_by": "human",
                    "summary": "accepted",
                },
            )

    def test_dispatch_requires_bounded_receiver_metadata(self) -> None:
        dispatch_schema = {
            tool["name"]: tool
            for tool in tool_definitions()
        }["dispatch_loop"]["inputSchema"]
        self.assertEqual(
            dispatch_schema["properties"]["dispatch_mode"]["enum"],
            ["AUTO", "MANUAL"],
        )
        base = {
            "root_id": "d-service",
            "node_id": "loop:t-service",
            "owner": "agent-1",
            "agent_id": "codex",
            "dispatch_mode": "AUTO",
            "receiver_context_id": "context-1",
            "operation_id": "op-1",
        }
        self.assertEqual(
            validate_tool_arguments("dispatch_loop", base),
            base,
        )
        for invalid in (
            {key: value for key, value in base.items() if key != "agent_id"},
            {
                key: value
                for key, value in base.items()
                if key != "receiver_context_id"
            },
            {
                key: value
                for key, value in base.items()
                if key != "operation_id"
            },
            {**base, "agent_id": "x" * 257},
            {**base, "actual_model_id": "host-observed-model"},
            {**base, "model_id": "gpt-5.6-sol"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(GatedLoopError) as caught:
                    validate_tool_arguments("dispatch_loop", invalid)
                self.assertEqual(
                    caught.exception.code,
                    "MCP_TOOL_ARGUMENT_INVALID",
                )

    def test_dispatch_owner_exposes_portable_identity_contract(self) -> None:
        dispatch = {
            tool["name"]: tool["inputSchema"]
            for tool in tool_definitions()
        }["dispatch_loop"]
        owner = dispatch["properties"]["owner"]
        self.assertEqual(owner["maxLength"], 192)
        self.assertIn("native agent_id", owner["description"])
        self.assertIsNotNone(re.fullmatch(owner["pattern"], "claude-code"))
        self.assertIsNone(
            re.fullmatch(owner["pattern"], "claude-code#loop:task")
        )

    def test_freeze_adapter_injects_strict_boolean_confirmation(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": task_hierarchy()},
                root=root,
            )
            frozen = call_tool(
                "freeze_hierarchy",
                {
                    "root_id": prepared["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": (
                        prepared["hierarchyFingerprint"]
                    ),
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=root,
            )
            status = call_tool(
                "workspace_status",
                {},
                root=root,
            )

        self.assertEqual(frozen["status"], "ACTIVE")
        self.assertEqual(frozen["executionMode"], "active")
        self.assertEqual(frozen["confirmedBy"], "human")
        self.assertEqual(status["status"], "ACTIVE")
        self.assertEqual(
            frozen["executionMode"],
            status["executionMode"],
        )

    def test_execution_choice_adapter_applies_both_modes_without_reconfirm(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        for selection, expected_status in (
            ("AUTOMATIC", "ACTIVE"),
            ("MANUAL", "HANDOFF_READY"),
        ):
            with (
                self.subTest(selection=selection),
                TemporaryDirectory() as root,
            ):
                preview = call_tool(
                    "preview_hierarchy",
                    {"hierarchy": hierarchy},
                    root=root,
                )
                selected = call_tool(
                    "select_execution_mode",
                    {
                        "root_id": preview["rootId"],
                        "selection": selection,
                        "expected_hierarchy_fingerprint": (
                            preview["hierarchyFingerprint"]
                        ),
                        "expected_graph_fingerprint": (
                            preview["graphFingerprint"]
                        ),
                        "authorized_project_ids": [],
                        "confirmed_by": "human",
                    },
                    root=root,
                )

                self.assertEqual(selected["status"], expected_status)
                self.assertEqual(selected["selection"], selection)
                if selection == "AUTOMATIC":
                    self.assertTrue(selected["automaticDispatchRequested"])
                else:
                    self.assertIn(
                        selected["manualHandoff"]["receiverPrompt"],
                        Path(
                            root,
                            selected["manualHandoff"]["path"],
                        ).read_text(encoding="utf-8"),
                    )
