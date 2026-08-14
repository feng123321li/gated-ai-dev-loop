from __future__ import annotations

from .scheduler_contracts_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    bind_delivery_to_git,
    call_tool,
    deepcopy,
    git_command,
    git_delivery_checkout,
    isolated_task_hierarchy,
    loop_node_id,
    task_hierarchy,
    validate_tool_arguments,
)


class McpSurfaceTestsPart2:
    def test_repeated_automatic_choice_keeps_workspace_isolation(self) -> None:
        hierarchy = task_hierarchy()
        with TemporaryDirectory() as root:
            workspace_a = Path(root, "workspace-a")
            workspace_b = Path(root, "workspace-b")
            workspace_a.mkdir()
            workspace_b.mkdir()
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=root,
            )
            arguments = {
                "root_id": preview["rootId"],
                "selection": "AUTOMATIC",
                "expected_hierarchy_fingerprint": (
                    preview["hierarchyFingerprint"]
                ),
                "expected_graph_fingerprint": preview["graphFingerprint"],
                "authorized_project_ids": [],
                "confirmed_by": "human",
            }
            call_tool(
                "select_execution_mode",
                arguments,
                root=root,
                workspace_root=str(workspace_a),
            )

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "select_execution_mode",
                    arguments,
                    root=root,
                    workspace_root=str(workspace_b),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
        )

    def test_handoff_adapter_injects_strict_boolean_confirmation(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        with TemporaryDirectory() as root:
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=root,
            )
            handoff = call_tool(
                "create_manual_handoff",
                {
                    "hierarchy": hierarchy,
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

            control_root = Path(root, ".layered-delivery")
            self.assertTrue(Path(control_root, "scheduler.db").is_file())
            self.assertTrue(Path(control_root, "overview.md").is_file())
            generated_files = {
                path.relative_to(control_root).as_posix()
                for path in control_root.rglob("*")
                if path.is_file() and path.name != ".scheduler.lock"
            }
            expected_files = {
                "scheduler.db",
                "overview.md",
                Path(handoff["manualHandoff"]["path"])
                .relative_to(".layered-delivery")
                .as_posix(),
                "d-service/overview.md",
                "d-service/baseline.md",
                "d-service/progress.md",
                "d-service/acceptance.md",
                "d-service/revisions.md",
                "d-service/work-items/t-service/baseline.md",
                "d-service/work-items/t-service/progress.md",
                "d-service/work-items/t-service/acceptance.md",
            }
            self.assertEqual(generated_files, expected_files)
            self.assertTrue(handoff["controlStateCreated"])
            self.assertEqual(
                handoff["humanArtifacts"]["workspaceOverview"],
                ".layered-delivery/overview.md",
            )
            self.assertEqual(
                handoff["nextAction"],
                "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI",
            )
            status = call_tool(
                "workspace_status",
                {"root_id": handoff["rootId"]},
                root=root,
            )
            self.assertEqual(status["status"], "HANDOFF_READY")
            self.assertEqual(status["rootId"], handoff["rootId"])
            self.assertEqual(
                status["workspaceIsolation"]["mode"],
                "UNBOUND_MANUAL_HANDOFF",
            )
            started = call_tool(
                "start_manual_handoff",
                {
                    "root_id": handoff["rootId"],
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "started_by": "manual-orchestrator",
                },
                root=root,
                workspace_root=root,
            )
            frontier = call_tool(
                "graph_frontier",
                {"root_id": handoff["rootId"]},
                root=root,
                workspace_root=root,
            )
            self.assertEqual(started["executionMode"], "manual")
            self.assertTrue(started["graphRunCreated"])
            self.assertEqual(
                [action["action"] for action in frontier["actions"]],
                ["CLAIM_MANUAL_TASK"],
            )

        self.assertEqual(handoff["status"], "HANDOFF_READY")
        self.assertEqual(handoff["requirementSnapshotStatus"], "FROZEN")
        self.assertEqual(handoff["confirmedBy"], "human")

    def test_distinct_conversation_workspaces_run_distinct_deliveries(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            first_workspace = Path(root, "worktree-first")
            second_workspace = Path(root, "worktree-second")
            first_workspace.mkdir()
            second_workspace.mkdir()
            first = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-first",
                        "t-first",
                    )
                },
                root=root,
                workspace_root=str(first_workspace),
            )
            second = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-second",
                        "t-second",
                    )
                },
                root=root,
                workspace_root=str(second_workspace),
            )
            for prepared, workspace in (
                (first, first_workspace),
                (second, second_workspace),
            ):
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
                    workspace_root=str(workspace),
                )
                self.assertEqual(frozen["status"], "ACTIVE")
                status = call_tool(
                    "workspace_status",
                    {},
                    root=root,
                    workspace_root=str(workspace),
                )
                self.assertEqual(status["rootId"], prepared["rootId"])
                self.assertEqual(status["status"], "ACTIVE")
                selected = call_tool(
                    "workspace_status",
                    {"root_id": prepared["rootId"]},
                    root=root,
                    workspace_root=str(workspace),
                )
                self.assertEqual(selected["rootId"], prepared["rootId"])

            self.assertNotEqual(
                first["workspaceIsolation"]["workspaceKey"],
                second["workspaceIsolation"]["workspaceKey"],
            )
            self.assertEqual(
                SchedulerRepository(root).run("d-first")["status"],
                "ACTIVE",
            )
            self.assertEqual(
                SchedulerRepository(root).run("d-second")["status"],
                "ACTIVE",
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "graph_status",
                    {"root_id": "d-first"},
                    root=root,
                    workspace_root=str(second_workspace),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
            )

    def test_active_workspace_binds_multiple_deliveries_but_runs_one_turn(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            workspace = Path(root, "one-worktree")
            workspace.mkdir()
            first = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-first",
                        "t-first",
                    )
                },
                root=root,
                workspace_root=str(workspace),
            )
            call_tool(
                "freeze_hierarchy",
                {
                    "root_id": first["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": (
                        first["hierarchyFingerprint"]
                    ),
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=root,
                workspace_root=str(workspace),
            )
            second = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-second",
                        "t-second",
                    )
                },
                root=root,
                workspace_root=str(workspace),
            )
            selection_arguments = {
                "root_id": second["rootId"],
                "selection": "AUTOMATIC",
                "expected_hierarchy_fingerprint": (
                    second["hierarchyFingerprint"]
                ),
                "expected_graph_fingerprint": second["graphFingerprint"],
                "authorized_project_ids": [],
                "confirmed_by": "human",
            }
            selected = call_tool(
                "select_execution_mode",
                selection_arguments,
                root=root,
                workspace_root=str(workspace),
            )
            resumed = call_tool(
                "resume_execution_mode",
                {
                    "root_id": second["rootId"],
                    "expected_hierarchy_fingerprint": (
                        second["hierarchyFingerprint"]
                    ),
                    "expected_graph_fingerprint": (
                        second["graphFingerprint"]
                    ),
                },
                root=root,
                workspace_root=str(workspace),
            )
            for waiting in (selected, resumed):
                self.assertEqual(
                    waiting["status"],
                    "QUEUED",
                )
                self.assertEqual(
                    waiting["deliveryQueue"]["state"],
                    "QUEUED",
                )
                self.assertFalse(waiting["automaticDispatchRequested"])
                self.assertEqual(
                    waiting["workspaceTurn"]["ownerRootId"],
                    first["rootId"],
                )
            self.assertTrue(selected["selectionRecorded"])
            self.assertTrue(resumed["selectionAlreadyApplied"])
            with self.assertRaises(GatedLoopError) as missing_run:
                SchedulerRepository(root).run(second["rootId"])
            self.assertEqual(
                missing_run.exception.code,
                "SCHEDULER_RUN_MISSING",
            )
            status = call_tool(
                "workspace_status",
                {},
                root=root,
                workspace_root=str(workspace),
            )
            first_status = call_tool(
                "workspace_status",
                {"root_id": "d-first"},
                root=root,
                workspace_root=str(workspace),
            )
            second_status = call_tool(
                "workspace_status",
                {"root_id": "d-second"},
                root=root,
                workspace_root=str(workspace),
            )
            self.assertEqual(status["status"], "DELIVERY_SELECTION_REQUIRED")
            self.assertEqual(
                status["nextAction"],
                "CALL_WORKSPACE_STATUS_WITH_ROOT_ID_OR_PREVIEW_NEW_DELIVERY",
            )
            self.assertEqual(
                sorted(
                    item["rootId"]
                    for item in status["candidateDeliveries"]
                ),
                ["d-first", "d-second"],
            )
            self.assertTrue(status["canCreateDelivery"])
            self.assertEqual(first_status["status"], "ACTIVE")
            self.assertEqual(
                second_status["status"],
                "QUEUED",
            )
            self.assertEqual(
                second_status["deliveryQueue"]["state"],
                "QUEUED",
            )
            self.assertEqual(second_status["deliveryStatus"], "PREPARED")
            self.assertEqual(
                first_status["workspaceIsolation"]["mode"],
                "MULTI_DELIVERY_WORKSPACE",
            )
            self.assertEqual(
                first_status["workspaceIsolation"]["workspaceKey"],
                second_status["workspaceIsolation"]["workspaceKey"],
            )
            self.assertTrue(
                (Path(root) / ".layered-delivery" / "d-second").exists()
            )

    def test_detached_primary_checkout_requires_current_feature_branch(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, _worktree, base_commit, _branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(repository, "checkout", "--detach", base_commit)

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(discovered["status"], "ABSENT")
            self.assertEqual(
                discovered["workspacePreparation"]["state"],
                "FEATURE_BRANCH_REQUIRED",
            )
            self.assertEqual(
                discovered["workspacePreparation"]["nextAction"],
                "CREATE_DELIVERY_FEATURE_BRANCH",
            )
            self.assertEqual(
                discovered["workspaceProvenance"]["topology"],
                "PRIMARY_CHECKOUT",
            )
            self.assertNotIn("suggestedGitBinding", discovered)

    def test_claude_cli_automatic_choice_stays_in_current_workspace(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-claude-cli",
                )
            )
            git_command(repository, "switch", "main")
            mainline = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(mainline["status"], "ABSENT")
            self.assertEqual(
                mainline["workspacePreparation"]["state"],
                "FEATURE_BRANCH_REQUIRED",
            )
            self.assertEqual(
                mainline["workspacePreparation"]["nextAction"],
                "CREATE_DELIVERY_FEATURE_BRANCH",
            )
            self.assertEqual(
                mainline["workspaceProvenance"]["topology"],
                "PRIMARY_CHECKOUT",
            )

            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            hierarchy["delivery"]["id"] = "d-claude-cli"
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )
            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-claude-cli",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertEqual(
                selected["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            preparation = selected["workspacePreparation"]
            self.assertEqual(
                preparation["state"],
                "CURRENT_WORKSPACE_PREPARATION_REQUIRED",
            )
            self.assertEqual(
                preparation["strategy"], "CURRENT_WORKSPACE_SERIAL"
            )
            self.assertNotIn("hostDispatch", preparation)
            self.assertEqual(
                preparation["projectPreparations"][0]["workspaceRoot"],
                str(repository.resolve()),
            )
            self.assertEqual(
                preparation["projectPreparations"][0]["branchRef"],
                branch_ref,
            )
            scheduler = SchedulerRepository(str(repository))
            self.assertEqual(
                scheduler.execution_selection("d-claude-cli")["selection"],
                "AUTOMATIC",
            )
            with self.assertRaises(GatedLoopError) as missing_run:
                scheduler.run("d-claude-cli")
            self.assertEqual(
                missing_run.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

    def test_loop_mutations_require_explicit_operation_id(self) -> None:
        cases = {
            "heartbeat_loop": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
            },
            "report_loop_progress": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
                "phase": "STARTING",
                "summary_zh": "开始处理",
            },
            "pause_loop": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
            },
            "record_loop_result": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
                "outcome": {
                    "status": "SUCCEEDED",
                    "summary": "done",
                    "result": {},
                },
            },
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(GatedLoopError) as caught:
                    validate_tool_arguments(name, arguments)
                self.assertEqual(
                    caught.exception.code,
                    "MCP_TOOL_ARGUMENT_INVALID",
                )
                validated = validate_tool_arguments(
                    name,
                    {**arguments, "operation_id": "op-explicit"},
                )
                self.assertEqual(validated["operation_id"], "op-explicit")

    def test_codex_automatic_choice_prepares_current_project_workspaces(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-auto-transition",
                )
            )
            secondary_container = Path(root, "secondary")
            secondary_container.mkdir()
            (
                secondary_repository,
                secondary_worktree,
                secondary_base_commit,
                secondary_branch_ref,
            ) = git_delivery_checkout(
                str(secondary_container),
                delivery_id="d-auto-transition",
            )
            self.assertEqual(secondary_branch_ref, branch_ref)
            git_command(repository, "switch", "main")
            git_command(secondary_repository, "switch", "main")
            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            hierarchy["delivery"]["id"] = "d-auto-transition"
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "erp-protein",
                    "workspaceRoot": str(repository.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(
                        hierarchy["delivery"]["gitBinding"]
                    ),
                },
                {
                    "id": "erp-pm",
                    "workspaceRoot": str(secondary_repository.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": {
                        "branchRef": secondary_branch_ref,
                        "baseRef": "main",
                        "baseCommit": secondary_base_commit,
                        "integrationTarget": "main",
                    },
                },
            ]
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-auto-transition",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": ["erp-pm", "erp-protein"],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertEqual(selected["selection"], "AUTOMATIC")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertEqual(
                selected["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            self.assertEqual(
                selected["selectionContinuation"],
                {
                    "tool": "resume_execution_mode",
                    "confirmationRequired": False,
                    "selectionPreserved": True,
                },
            )
            preparation = selected["workspacePreparation"]
            self.assertEqual(
                preparation["state"],
                "CURRENT_WORKSPACE_PREPARATION_REQUIRED",
            )
            self.assertEqual(
                preparation["strategy"], "CURRENT_WORKSPACE_SERIAL"
            )
            self.assertNotIn("hostDispatch", preparation)
            project_preparations = {
                item["projectId"]: item
                for item in preparation["projectPreparations"]
            }
            self.assertEqual(
                set(project_preparations),
                {"erp-pm", "erp-protein"},
            )
            self.assertEqual(
                project_preparations["erp-protein"]["workspaceRoot"],
                str(repository.resolve()),
            )
            self.assertEqual(
                project_preparations["erp-pm"]["workspaceRoot"],
                str(secondary_repository.resolve()),
            )
            for item in project_preparations.values():
                self.assertEqual(
                    item["state"],
                    "CURRENT_WORKSPACE_BRANCH_REQUIRED",
                )
                self.assertEqual(
                    item["nextAction"],
                    "CREATE_OR_SWITCH_CURRENT_WORKSPACE_BRANCH",
                )
            scheduler = SchedulerRepository(str(repository))
            self.assertEqual(
                scheduler.execution_selection("d-auto-transition")[
                    "selection"
                ],
                "AUTOMATIC",
            )
            with self.assertRaises(GatedLoopError) as missing_run:
                scheduler.run("d-auto-transition")
            self.assertEqual(
                missing_run.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

    def test_codex_automatic_choice_requests_current_master_branch_setup(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-codex-master",
                    mainline="master",
                )
            )
            git_command(repository, "switch", "master")
            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
                base_ref="master",
            )
            hierarchy["delivery"]["id"] = "d-codex-master"
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-codex-master",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertEqual(
                selected["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertEqual(
                selected["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            preparation = selected["workspacePreparation"]
            self.assertNotIn("hostDispatch", preparation)
            project_preparation = preparation["projectPreparations"][0]
            self.assertEqual(
                project_preparation["workspaceRoot"],
                str(repository.resolve()),
            )
            self.assertEqual(project_preparation["branchRef"], branch_ref)
            self.assertEqual(
                project_preparation["gitBinding"]["baseRef"],
                "master",
            )
            self.assertEqual(
                project_preparation["gitBinding"]["integrationTarget"],
                "master",
            )
            self.assertEqual(
                git_command(repository, "branch", "--show-current"),
                "master",
            )

    def test_single_project_loop_context_synthesizes_verified_scope(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
            )
            call_tool(
                "freeze_hierarchy",
                {
                    "root_id": prepared["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": prepared[
                        "hierarchyFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(worktree),
            )

            context = call_tool(
                "loop_context",
                {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )

        self.assertEqual(context["projectScopeAnchors"], [])
        self.assertEqual(len(context["projectScopes"]), 1)
        scope = context["projectScopes"][0]
        self.assertEqual(scope["id"], "primary")
        self.assertEqual(scope["access"], "READ_WRITE")
        self.assertEqual(scope["scopeSource"], "DELIVERY_GIT_BINDING")
        self.assertEqual(scope["workspaceRoot"], str(worktree.resolve()))
        self.assertEqual(
            scope["gitBinding"],
            hierarchy["delivery"]["gitBinding"],
        )
        self.assertIn("gitWorkspace", scope)
        self.assertTrue(
            context["rules"][
                "projectScopeWorkspaceRootsAreRuntimeVerified"
            ]
        )
