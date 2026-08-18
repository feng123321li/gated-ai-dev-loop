from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    at,
    call_tool,
    delivery_task_hierarchy,
    freeze_hierarchy,
    get_graph_frontier,
    graph_runtime,
    graph_status,
    heartbeat_loop,
    loop_node_id,
    patch,
    plan_dispatch_batch,
    prepare_hierarchy,
    preview_hierarchy,
    rebuild_graph_run,
    record_loop_result,
    review_success,
    review_node_id,
    runtime_dispatch_loop,
    select_execution_mode,
    skill_hint,
    sqlite3,
    subprocess,
    success,
    task_hierarchy,
    task_review_node_id,
)


class SchedulerRuntimeTestsPart1:
    def test_planned_task_and_delivery_review_dispatch_without_hooks(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        task_node_id = loop_node_id("t-service")
        task_plan = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(2),
        )
        task_assignment = task_plan["assignments"][0]

        task_claim = runtime_dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            owner="codex-task-child",
            operation_id="op-hookless-task",
            agent_id="codex",
            receiver_context_id="codex-task-child",
            dispatch_mode="AUTO",
            dispatch_transport=task_assignment["dispatchTransport"],
            dispatch_reservation_id=task_assignment[
                "dispatchReservationId"
            ],
            dispatch_decision_fingerprint=task_assignment[
                "decisionFingerprint"
            ],
            host_native_agent_ids=("codex",),
            host_adapter_id="codex",
            verified_project_scopes=[],
            now=at(3),
        )
        heartbeat = heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            operation_id="op-hookless-task",
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            operation_id="op-hookless-task",
            outcome=success("TASK completed."),
            now=at(4),
        )

        task_review_id = task_review_node_id("t-service")
        review_plan = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(5),
        )
        review_assignment = review_plan["assignments"][0]
        review_claim = runtime_dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
            owner="codex-review-child",
            operation_id="op-hookless-review",
            agent_id="codex",
            receiver_context_id="codex-review-child",
            dispatch_mode="AUTO",
            dispatch_transport=review_assignment["dispatchTransport"],
            dispatch_reservation_id=review_assignment[
                "dispatchReservationId"
            ],
            dispatch_decision_fingerprint=review_assignment[
                "decisionFingerprint"
            ],
            host_native_agent_ids=("codex",),
            host_adapter_id="codex",
            verified_project_scopes=[],
            now=at(6),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
            operation_id="op-hookless-review",
            outcome=review_success("TASK_REVIEW_LOOP"),
            now=at(7),
        )

        delivery_review_node_id = review_node_id(root_id)
        delivery_review_plan = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(8),
        )
        delivery_review_assignment = delivery_review_plan["assignments"][0]

        self.assertEqual(
            task_plan["nextAction"],
            "CREATE_INDEPENDENT_RECEIVERS",
        )
        self.assertEqual(task_assignment["nodeId"], task_node_id)
        self.assertEqual(task_claim["operationId"], "op-hookless-task")
        self.assertEqual(heartbeat["status"], "CLAIMED")
        self.assertNotIn("receiverAttested", task_claim)
        self.assertEqual(review_assignment["nodeId"], task_review_id)
        self.assertTrue(review_assignment["independence"]["required"])
        self.assertEqual(
            review_claim["receiverContextId"],
            "codex-review-child",
        )
        self.assertNotIn("receiverAttested", review_claim)
        self.assertEqual(
            delivery_review_assignment["nodeId"],
            delivery_review_node_id,
        )
        self.assertIn(
            "$delivery-graph-review",
            delivery_review_assignment["receiverPrompt"],
        )

    def test_ready_automatic_task_can_be_explicitly_handed_to_manual_receiver(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        task_node_id = loop_node_id("t-service")
        assignment = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(2),
        )["assignments"][0]

        with self.assertRaises(GatedLoopError) as live_reservation:
            graph_runtime.handoff_ready_automatic_task(
                root=self.root,
                root_id=root_id,
                node_id=task_node_id,
                expected_graph_fingerprint=prepared["graphFingerprint"],
                handoff_request_id="handoff-live-reservation",
                confirmed_no_code_changes=True,
                confirmed_by="human",
                reason="Native receiver startup failed.",
                workspace_root=self.root,
                now=at(3),
            )
        self.assertEqual(
            live_reservation.exception.code,
            "SCHEDULER_MANUAL_HANDOFF_RESERVATION_ACTIVE",
        )

        handed_off = call_tool(
            "handoff_ready_automatic_task",
            {
                "root_id": root_id,
                "node_id": task_node_id,
                "expected_graph_fingerprint": prepared[
                    "graphFingerprint"
                ],
                "handoff_request_id": (
                    "handoff-after-receiver-startup-failure"
                ),
                "confirmed_no_code_changes": True,
                "confirmed_by": "human",
                "reason": (
                    "Native receiver startup failed twice."
                ),
            },
            root=self.root,
            workspace_root=self.root,
            trusted_host_adapter="codex",
        )
        replayed = graph_runtime.handoff_ready_automatic_task(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            handoff_request_id="handoff-after-receiver-startup-failure",
            confirmed_no_code_changes=True,
            confirmed_by="human",
            reason="Native receiver startup failed twice.",
            workspace_root=self.root,
            now=at(9),
        )
        rebuilt = rebuild_graph_run(root=self.root, root_id=root_id)
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(9),
        )
        dispatch = plan_dispatch_batch(
            root=self.root,
            root_id=root_id,
            expected_graph_fingerprint=prepared["graphFingerprint"],
            host_adapter_id="codex",
            host_native_agent_ids=("codex",),
            now=at(9),
        )
        claimed = runtime_dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            owner="manual-cli",
            operation_id="op-manual-recovery",
            agent_id="codex",
            receiver_context_id="manual-cli",
            dispatch_mode="MANUAL",
            host_adapter_id="codex",
            now=at(10),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_node_id,
            operation_id="op-manual-recovery",
            outcome=success("Recovered TASK completed."),
            now=at(11),
        )
        review_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(12),
        )

        self.assertEqual(assignment["nodeId"], task_node_id)
        self.assertEqual(handed_off["manualTaskHandoff"]["state"], "READY")
        self.assertFalse(handed_off["handoffRequestReplayed"])
        self.assertTrue(replayed["handoffRequestReplayed"])
        rebuilt_task = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == task_node_id
        )
        self.assertTrue(rebuilt_task["manualHandoffEnabled"])
        self.assertIn(
            "CLAIM_MANUAL_TASK",
            {action["action"] for action in frontier["actions"]},
        )
        self.assertEqual(dispatch["assignments"], [])
        self.assertEqual(claimed["dispatchMode"], "MANUAL")
        self.assertIn(
            task_review_node_id("t-service"),
            {
                action["nodeId"]
                for action in review_frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            },
        )
        self.assertEqual(graph_status(root=self.root, root_id=root_id)[
            "executionMode"
        ], "active")

    def test_ready_automatic_task_handoff_fails_closed(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        task_node_id = loop_node_id("t-service")

        with self.assertRaises(GatedLoopError) as confirmation:
            graph_runtime.handoff_ready_automatic_task(
                root=self.root,
                root_id=root_id,
                node_id=task_node_id,
                expected_graph_fingerprint=prepared["graphFingerprint"],
                handoff_request_id="handoff-without-confirmation",
                confirmed_no_code_changes=False,
                confirmed_by="human",
                reason="Native receiver startup failed.",
                workspace_root=self.root,
                now=at(2),
            )
        self.assertEqual(
            confirmation.exception.code,
            "SCHEDULER_MANUAL_HANDOFF_CONFIRMATION_REQUIRED",
        )

        with self.assertRaises(GatedLoopError) as review:
            graph_runtime.handoff_ready_automatic_task(
                root=self.root,
                root_id=root_id,
                node_id=task_review_node_id("t-service"),
                expected_graph_fingerprint=prepared["graphFingerprint"],
                handoff_request_id="handoff-review",
                confirmed_no_code_changes=True,
                confirmed_by="human",
                reason="Native receiver startup failed.",
                workspace_root=self.root,
                now=at(2),
            )
        self.assertEqual(
            review.exception.code,
            "SCHEDULER_MANUAL_HANDOFF_TASK_ONLY",
        )

    def test_ready_automatic_task_handoff_rejects_dirty_git_working_tree(
        self,
    ) -> None:
        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ["git", "-C", self.root, *arguments],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return completed.stdout.strip()

        git("init", "--initial-branch=main")
        git("config", "user.name", "Scheduler Tests")
        git("config", "user.email", "scheduler@example.invalid")
        tracked = Path(self.root, "tracked.txt")
        tracked.write_text("baseline\n", encoding="utf-8")
        git("add", "tracked.txt")
        git("commit", "-m", "baseline")
        base_commit = git("rev-parse", "HEAD")
        git("switch", "-c", "feature/manual-recovery")
        hierarchy = task_hierarchy()
        hierarchy["delivery"]["gitBinding"] = {
            "branchRef": "feature/manual-recovery",
            "baseRef": "main",
            "baseCommit": base_commit,
            "integrationTarget": "main",
        }
        prepared = self.prepare_and_freeze(hierarchy)
        tracked.write_text("dirty\n", encoding="utf-8")

        with self.assertRaises(GatedLoopError) as dirty:
            graph_runtime.handoff_ready_automatic_task(
                root=self.root,
                root_id=prepared["rootId"],
                node_id=loop_node_id("t-service"),
                expected_graph_fingerprint=prepared[
                    "graphFingerprint"
                ],
                handoff_request_id="handoff-dirty-worktree",
                confirmed_no_code_changes=True,
                confirmed_by="human",
                reason="Codex receiver startup failed.",
                workspace_root=self.root,
                now=at(2),
            )

        self.assertEqual(
            dirty.exception.code,
            "SCHEDULER_MANUAL_HANDOFF_WORKSPACE_DIRTY",
        )

    def test_preview_materializes_artifacts_before_controller_owned_choice(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-choice", "t-choice")

        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )

        self.assertEqual(preview["status"], "CHOICE_READY")
        self.assertTrue(preview["controlStateCreated"])
        self.assertTrue(preview["artifactsReady"])
        self.assertEqual(
            preview["nextAction"],
            "PRESENT_HOST_NATIVE_EXECUTION_CHOICE",
        )
        choice = preview["executionChoice"]
        self.assertEqual(choice["schemaVersion"], 2)
        self.assertEqual(choice["owner"], "CONTROLLER")
        self.assertEqual(choice["kind"], "EXECUTION_MODE")
        self.assertTrue(choice["selectionRequired"])
        self.assertEqual(choice["defaultOptionId"], "AUTOMATIC")
        self.assertEqual(choice["recommendedOptionId"], "AUTOMATIC")
        self.assertEqual(
            choice["presentationPolicy"],
            {
                "preferredMode": "HOST_NATIVE_SELECTOR",
                "nativeSelectorRequiredWhenAvailable": True,
                "availabilityRule": (
                    "MAPPED_TOOL_CALLABLE_IN_CURRENT_CONTEXT"
                ),
                "optionSourceField": "options",
                "selectionValueField": "id",
                "preserveOptionOrder": True,
                "preserveOptionCopy": True,
                "hostMappings": {
                    "codex": {"tool": "request_user_input"},
                    "claude-code": {"tool": "AskUserQuestion"},
                    "zcode": {"tool": "AskUserQuestion"},
                },
                "fallback": {
                    "allowedOnlyWhen": (
                        "MAPPED_NATIVE_SELECTOR_UNAVAILABLE"
                    ),
                    "mode": "EXACT_CONTROLLER_MARKDOWN",
                    "contentField": "markdown",
                    "agentRewriteAllowed": False,
                    "typedOptionPromptAllowed": False,
                },
            },
        )
        self.assertIsNone(choice["activeHostMapping"])
        self.assertEqual(
            choice["freeformInput"],
            {
                "allowed": True,
                "nextAction": "CONTINUE_REQUIREMENT_DISCUSSION",
            },
        )
        self.assertEqual(
            [item["id"] for item in choice["options"]],
            ["AUTOMATIC", "MANUAL"],
        )
        self.assertEqual(
            [item["label"] for item in choice["options"]],
            ["自动执行（当前 workspace 串行）", "手动开发"],
        )
        self.assertEqual(
            [item["description"] for item in choice["options"]],
            [
                "复用当前 workspace 串行执行；选择后若已有调度运行，"
                "本 Delivery 标记排队。轮到队首后由宿主自动 stash 既有"
                "业务改动、创建或切换独立 Delivery 分支并继续调度。"
                "前一 Delivery 仍须先满足可验证提交、clean、HEAD 与"
                "receiver 释放边界。",
                "生成 handoff；接收 CLI 启动同一 Graph，手动完成 TASK，"
                "后续审查与自动执行一致。",
            ],
        )
        self.assertTrue(choice["options"][0]["recommended"])
        self.assertFalse(choice["options"][1]["recommended"])
        self.assertEqual(
            [item["nextAction"] for item in choice["options"]],
            [
                "RECORD_SELECTION_THEN_WAIT_OR_PREPARE_CURRENT_WORKSPACE",
                "CREATE_HANDOFF_THEN_START_GOVERNED_MANUAL_GRAPH",
            ],
        )
        self.assertFalse(
            choice["options"][0]["requiresAdditionalConfirmation"]
        )
        self.assertEqual(
            choice["options"][0]["workspaceContinuation"],
            "RESUME_EXECUTION_MODE_WITHOUT_CONFIRMATION",
        )
        self.assertEqual(
            choice["options"][0]["workspaceStrategy"],
            "CURRENT_WORKSPACE_SERIAL",
        )
        self.assertFalse(
            choice["options"][1]["requiresAdditionalConfirmation"]
        )
        self.assertIn("默认：自动执行", choice["markdown"])
        self.assertLess(
            choice["markdown"].index("1. 自动执行"),
            choice["markdown"].index("2. 手动开发"),
        )
        self.assertIn("直接输入修改意见", choice["markdown"])
        self.assertNotIn("Type something", choice["markdown"])

        for artifact_name in (
            "workspaceOverview",
            "overview",
            "baseline",
            "progress",
            "acceptance",
            "revisions",
        ):
            with self.subTest(artifact=artifact_name):
                self.assertTrue(
                    Path(
                        self.root,
                        preview["humanArtifacts"][artifact_name],
                    ).is_file()
                )
        task_artifacts = preview["humanArtifacts"]["workItems"][
            "t-choice"
        ]
        for artifact_name in ("baseline", "progress", "acceptance"):
            with self.subTest(task_artifact=artifact_name):
                self.assertTrue(
                    Path(
                        self.root,
                        task_artifacts[artifact_name],
                    ).is_file()
                )
        self.assertTrue(
            Path(
                self.root,
                ".layered-delivery",
                "scheduler.db",
            ).is_file()
        )
        self.assertFalse(Path(self.root, "worktrees").exists())
        stored = SchedulerRepository(self.root).hierarchy("d-choice")
        self.assertEqual(stored["status"], "CHOICE_READY")

    def test_choice_ready_snapshot_can_enter_automatic_execution(self) -> None:
        hierarchy = delivery_task_hierarchy("d-choice-auto", "t-choice")
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )

        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(1),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(2),
        )

        self.assertEqual(preview["status"], "CHOICE_READY")
        self.assertEqual(prepared["status"], "PREPARED")
        self.assertEqual(frozen["status"], "ACTIVE")
        self.assertEqual(frozen["deliveryRevision"], 1)

    def test_repeated_freeze_returns_the_existing_run_without_self_locking(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-freeze-replay", "t-freeze")
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        first = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        sqlite_connect = sqlite3.connect

        def connect_without_lock_wait(
            *args: object,
            **kwargs: object,
        ) -> sqlite3.Connection:
            kwargs["timeout"] = 0.01
            return sqlite_connect(*args, **kwargs)

        with patch(
            "hdg.repository.sqlite3.connect",
            side_effect=connect_without_lock_wait,
        ):
            repeated = freeze_hierarchy(
                root=self.root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
                now=at(2),
            )

        self.assertEqual(repeated["status"], "ACTIVE")
        self.assertEqual(repeated["runId"], first["runId"])

    def test_changed_freeform_requirement_regenerates_choice_ready_artifacts(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-discussion", "t-discussion")
        first = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        baseline_path = Path(
            self.root,
            first["humanArtifacts"]["baseline"],
        )
        first_content = baseline_path.read_text(encoding="utf-8")

        hierarchy["delivery"]["summary"] = "需求沟通后的新范围"
        second = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(1),
        )
        second_content = baseline_path.read_text(encoding="utf-8")

        self.assertEqual(second["status"], "CHOICE_READY")
        self.assertTrue(second["artifactsReady"])
        self.assertNotEqual(
            second["hierarchyFingerprint"],
            first["hierarchyFingerprint"],
        )
        self.assertNotEqual(second_content, first_content)
        self.assertIn("需求沟通后的新范围", second_content)
        self.assertEqual(
            second["executionChoice"],
            first["executionChoice"],
        )

    def test_changed_requirement_clears_recorded_automatic_selection(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-selection-invalidated",
            "t-selection-invalidated",
        )
        first = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        SchedulerRepository(self.root, now=at(1)).record_automatic_selection(
            "d-selection-invalidated",
            expected_hierarchy_fingerprint=first[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=first["graphFingerprint"],
            authorized_project_ids=[],
            confirmed_by="human",
        )
        same = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )
        self.assertIn("executionSelection", same)
        self.assertNotIn("executionChoice", same)

        hierarchy["delivery"]["summary"] = "Changed delivery scope."
        changed = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(3),
        )

        self.assertNotIn("executionSelection", changed)
        self.assertIn("executionChoice", changed)
        self.assertEqual(
            changed["nextAction"],
            "PRESENT_HOST_NATIVE_EXECUTION_CHOICE",
        )

    def test_controller_selection_starts_automatic_graph_without_reconfirm(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-select-auto", "t-choice")
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )

        selected = select_execution_mode(
            root=self.root,
            root_id="d-select-auto",
            selection="AUTOMATIC",
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed_by="human",
            now=at(1),
        )
        repeated = select_execution_mode(
            root=self.root,
            root_id="d-select-auto",
            selection="AUTOMATIC",
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed_by="human",
            now=at(2),
        )

        self.assertEqual(selected["selection"], "AUTOMATIC")
        self.assertEqual(selected["status"], "ACTIVE")
        self.assertTrue(selected["automaticDispatchRequested"])
        self.assertEqual(
            selected["nextAction"],
            "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id="d-select-auto",
            now=at(2),
        )
        self.assertIn(
            "DISPATCH_LOOP",
            {action["action"] for action in frontier["actions"]},
        )
        self.assertEqual(repeated["runId"], selected["runId"])
        self.assertTrue(repeated["selectionAlreadyApplied"])

    def test_controller_manual_selection_returns_embedded_receiver_prompt(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-select-manual", "t-choice")
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the receiving Loop is Spring Boot work.",
            )
        ]
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )

        selected = select_execution_mode(
            root=self.root,
            root_id="d-select-manual",
            selection="MANUAL",
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed_by="human",
            now=at(1),
        )

        self.assertEqual(selected["selection"], "MANUAL")
        self.assertEqual(selected["status"], "HANDOFF_READY")
        self.assertFalse(selected["graphRunCreated"])
        prompt = selected["manualHandoff"]["receiverPrompt"]
        handoff_path = Path(
            self.root,
            selected["manualHandoff"]["path"],
        )
        self.assertTrue(handoff_path.is_file())
        self.assertIn(
            prompt,
            handoff_path.read_text(encoding="utf-8"),
        )
        self.assertIn("`$springboot-tdd`", prompt)
        self.assertIn("应在当前相应阶段优先原生触发", prompt)
        self.assertIn("才跳过", prompt)
        repeated_selection = select_execution_mode(
            root=self.root,
            root_id="d-select-manual",
            selection="MANUAL",
            expected_hierarchy_fingerprint=preview[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed_by="human",
            now=at(2),
        )
        self.assertTrue(repeated_selection["selectionAlreadyApplied"])
        self.assertEqual(repeated_selection["status"], "HANDOFF_READY")
        repeated_preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(3),
        )
        self.assertEqual(repeated_preview["status"], "HANDOFF_READY")
        self.assertNotIn("executionChoice", repeated_preview)
        self.assertEqual(
            repeated_preview["nextAction"],
            "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI",
        )
