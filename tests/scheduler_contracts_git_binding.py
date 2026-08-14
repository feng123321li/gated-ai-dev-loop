from __future__ import annotations

from .scheduler_contracts_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    bind_delivery_to_git,
    call_tool,
    capture_verified_workspace_changes,
    deepcopy,
    freeze_hierarchy,
    git_command,
    git_delivery_checkout,
    isolated_task_hierarchy,
    patch,
    reserve_loop,
)


class McpSurfaceTestsPart3:
    def test_git_delivery_binding_is_frozen_and_checked_at_runtime(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            advanced_main = git_command(
                repository,
                "commit-tree",
                f"{base_commit}^{{tree}}",
                "-p",
                base_commit,
                "-m",
                "Advance main after feature fork",
            )
            git_command(
                repository,
                "update-ref",
                "refs/heads/main",
                advanced_main,
                base_commit,
            )
            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(discovered["status"], "ABSENT")
            self.assertEqual(
                discovered["gitWorkspace"]["branchRef"],
                branch_ref,
            )
            self.assertEqual(
                discovered["suggestedGitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                prepared["gitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            self.assertEqual(
                prepared["gitWorkspace"]["branchRef"],
                branch_ref,
            )
            self.assertEqual(
                prepared["gitWorkspace"]["headCommit"],
                base_commit,
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
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                frozen["gitBinding"]["branchRef"],
                branch_ref,
            )
            authoritative_run = SchedulerRepository(
                str(repository)
            ).run("d-git")
            self.assertEqual(
                authoritative_run["gitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            baseline = Path(
                repository,
                ".layered-delivery",
                "d-git",
                "baseline.md",
            ).read_text(encoding="utf-8")
            self.assertIn("## Git 分支绑定", baseline)
            self.assertIn(
                f"Delivery feature 分支：{branch_ref}",
                baseline,
            )
            self.assertIn(
                f"创建基线提交：{base_commit}",
                baseline,
            )
            self.assertIn("最终集成目标：main", baseline)
            self.assertIn(
                f"main@{base_commit} → {branch_ref} → main",
                baseline,
            )

            git_command(
                worktree,
                "switch",
                "-c",
                "feature/wrong-delivery",
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "graph_status",
                    {"root_id": "d-git"},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )
            git_command(worktree, "switch", branch_ref)
            resumed = call_tool(
                "graph_status",
                {"root_id": "d-git"},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertEqual(
                resumed["gitWorkspace"]["branchRef"],
                branch_ref,
            )

    def test_git_delivery_requires_binding_and_valid_common_base(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {
                        "hierarchy": isolated_task_hierarchy(
                            "d-git",
                            "t-git",
                        )
                    },
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BINDING_REQUIRED",
            )

            wrong_branch = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref="feature/another-delivery",
                base_commit=base_commit,
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": wrong_branch},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )

            missing_base = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit="f" * 40,
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": missing_base},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BASE_INVALID",
            )

            Path(repository, "MAINLINE.md").write_text(
                "This commit is not part of the feature branch.\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "MAINLINE.md")
            git_command(repository, "commit", "-m", "Advance mainline")
            non_ancestor = git_command(
                repository,
                "rev-parse",
                "HEAD",
            )
            wrong_base = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit=non_ancestor,
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": wrong_base},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BASE_INVALID",
            )

    def test_workspace_change_patch_combines_multiple_projects_and_empty_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root, delivery_id="d-multi-evidence")
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
                delivery_id="d-multi-evidence",
            )
            self.assertEqual(secondary_branch_ref, branch_ref)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy(
                    "d-multi-evidence",
                    "t-multi-evidence",
                ),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "project-empty",
                    "workspaceRoot": str(worktree.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(
                        hierarchy["delivery"]["gitBinding"]
                    ),
                },
                {
                    "id": "project-uncommitted",
                    "workspaceRoot": str(secondary_worktree.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": {
                        "branchRef": secondary_branch_ref,
                        "baseRef": "main",
                        "baseCommit": secondary_base_commit,
                        "integrationTarget": "main",
                    },
                },
            ]
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
            )
            freeze_hierarchy(
                root=str(repository),
                workspace_root=str(worktree),
                root_id="d-multi-evidence",
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                authorized_project_ids=[
                    "project-empty",
                    "project-uncommitted",
                ],
                confirmed=True,
                confirmed_by="human",
            )
            reservation = reserve_loop(
                root=str(repository),
                root_id="d-multi-evidence",
                node_id="loop:t-multi-evidence",
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": "d-multi-evidence",
                    "node_id": "loop:t-multi-evidence",
                    "owner": "agent-multi-evidence",
                    "agent_id": reservation["agentId"],
                    "dispatch_mode": reservation["dispatchMode"],
                    "dispatch_transport": reservation[
                        "dispatchTransport"
                    ],
                    "dispatch_reservation_id": reservation[
                        "dispatchReservationId"
                    ],
                    "dispatch_decision_fingerprint": reservation[
                        "dispatchDecisionFingerprint"
                    ],
                    "receiver_context_id": "context-multi-evidence",
                    "operation_id": "op-multi-evidence",
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            Path(
                secondary_worktree,
                "secondary-uncommitted.txt",
            ).write_text(
                "secondary uncommitted evidence\n",
                encoding="utf-8",
            )
            tested_snapshots = call_tool(
                "loop_context",
                {
                    "root_id": "d-multi-evidence",
                    "node_id": "loop:t-multi-evidence",
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )["currentWorkspaceSnapshots"]
            completed = call_tool(
                "record_loop_result",
                {
                    "root_id": "d-multi-evidence",
                    "node_id": "loop:t-multi-evidence",
                    "operation_id": "op-multi-evidence",
                    "outcome": {
                        "status": "SUCCEEDED",
                        "summary": "Captured all project scopes",
                        "result": {
                            "affectedScopes": [
                                {
                                    "scopeId": "secondary-change",
                                    "projectId": "project-uncommitted",
                                    "paths": ["secondary-uncommitted.txt"],
                                    "modules": [],
                                    "contracts": [],
                                    "dependencyBasis": (
                                        "Only the changed secondary file is in scope."
                                    ),
                                    "exclusions": [],
                                }
                            ],
                            "verificationEvidence": [
                                {
                                    "evidenceId": "secondary-file-check",
                                    "kind": "TEST",
                                    "check": "Secondary project targeted check",
                                    "command": "targeted secondary test",
                                    "scope": "secondary-uncommitted.txt",
                                    "scopeRefs": ["secondary-change"],
                                    "status": "PASSED",
                                    "tests": {
                                        "total": 1,
                                        "passed": 1,
                                        "failed": 0,
                                        "skipped": 0,
                                    },
                                    "completedAt": "2026-08-12T08:00:00Z",
                                    "testedWorkspaceSnapshots": (
                                        tested_snapshots
                                    ),
                                }
                            ],
                        },
                    },
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            snapshots = completed["outcome"]["result"][
                "workspaceChanges"
            ]
            evidence_binding = completed["outcome"]["result"][
                "evidenceWorkspaceSnapshots"
            ]
            self.assertEqual(
                [item["projectId"] for item in snapshots],
                ["project-empty", "project-uncommitted"],
            )
            self.assertEqual(snapshots[0]["changedFiles"], [])
            self.assertEqual(snapshots[0]["diff"], "")
            self.assertIn(
                "+secondary uncommitted evidence",
                snapshots[1]["diff"],
            )
            self.assertEqual(
                [item["bindingState"] for item in evidence_binding],
                ["BOUND", "BOUND"],
            )
            scope_binding = completed["outcome"]["result"][
                "evidenceScopeSnapshots"
            ][0]
            self.assertEqual(scope_binding["bindingState"], "BOUND")
            self.assertEqual(
                scope_binding["paths"],
                ["secondary-uncommitted.txt"],
            )
            Path(worktree, "unrelated-later-task.txt").write_text(
                "unrelated workspace change\n",
                encoding="utf-8",
            )
            unrelated_context = call_tool(
                "loop_context",
                {
                    "root_id": "d-multi-evidence",
                    "node_id": "review:task:t-multi-evidence",
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            evidence_index = unrelated_context["validationEvidenceIndex"]
            self.assertEqual(
                evidence_index["evidence"][0]["freshness"],
                "EXACT_MATCH",
            )
            self.assertEqual(
                evidence_index["evidence"][0]["evidenceRef"],
                {
                    "nodeId": "loop:t-multi-evidence",
                    "attempt": 1,
                    "evidenceId": "secondary-file-check",
                },
            )
            compact_upstream = unrelated_context["upstreamLoopResults"][0][
                "outcome"
            ]["result"]["workspaceChanges"]
            self.assertNotIn("diff", compact_upstream[1])
            self.assertTrue(
                compact_upstream[1]["diffOmittedFromLoopContext"]
            )
            Path(
                secondary_worktree,
                "secondary-uncommitted.txt",
            ).write_text(
                "secondary uncommitted evidence\nchanged after evidence\n",
                encoding="utf-8",
            )
            review_node_id = "review:task:t-multi-evidence"
            review_reservation = reserve_loop(
                root=str(repository),
                root_id="d-multi-evidence",
                node_id=review_node_id,
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": "d-multi-evidence",
                    "node_id": review_node_id,
                    "owner": "reviewer-multi-evidence",
                    "agent_id": review_reservation["agentId"],
                    "dispatch_mode": review_reservation["dispatchMode"],
                    "dispatch_transport": review_reservation[
                        "dispatchTransport"
                    ],
                    "dispatch_reservation_id": review_reservation[
                        "dispatchReservationId"
                    ],
                    "dispatch_decision_fingerprint": review_reservation[
                        "dispatchDecisionFingerprint"
                    ],
                    "receiver_context_id": "review-context-multi-evidence",
                    "operation_id": "review-op-multi-evidence",
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            review_result_arguments = {
                "root_id": "d-multi-evidence",
                "node_id": review_node_id,
                "operation_id": "review-op-multi-evidence",
                "outcome": {
                    "status": "SUCCEEDED",
                    "summary": "Independently reviewed targeted evidence",
                    "result": {
                        "reviewFindings": [],
                        "validationDecision": {
                            "decision": "REUSED",
                            "reusedEvidenceRefs": [
                                {
                                    "nodeId": "loop:t-multi-evidence",
                                    "attempt": 1,
                                    "evidenceId": "secondary-file-check",
                                }
                            ],
                            "executedEvidenceRefs": [],
                            "riskTriggers": [],
                            "rationale": "Relevant paths were unchanged.",
                        },
                        "taskAcceptance": {
                            "acceptanceChecks": [
                                {
                                    "acceptancePoint": (
                                        "The frozen TASK contract is met."
                                    ),
                                    "status": "SATISFIED",
                                    "evidenceRefs": [
                                        "secondary-file-check"
                                    ],
                                }
                            ],
                            "localBehavior": "VERIFIED",
                            "publicContract": "NOT_APPLICABLE",
                            "targetedRegression": "VERIFIED",
                            "decision": "ACCEPTED",
                            "rationale": (
                                "Only the TASK-owned boundary was reviewed."
                            ),
                        },
                    },
                },
            }
            with self.assertRaises(GatedLoopError) as stale_caught:
                call_tool(
                    "record_loop_result",
                    review_result_arguments,
                    root=str(repository),
                    workspace_root=str(worktree),
                    trusted_host_adapter="codex",
                )
            self.assertEqual(
                stale_caught.exception.code,
                "LOOP_EVIDENCE_STALE",
            )
            Path(
                secondary_worktree,
                "secondary-uncommitted.txt",
            ).write_text(
                "secondary uncommitted evidence\n",
                encoding="utf-8",
            )
            reviewed = call_tool(
                "record_loop_result",
                review_result_arguments,
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            self.assertEqual(reviewed["schedulerStatus"], "SUCCEEDED")
            task_directory = (
                repository
                / ".layered-delivery"
                / "d-multi-evidence"
                / "work-items"
                / "t-multi-evidence"
            )
            acceptance = Path(
                task_directory,
                "acceptance.md",
            ).read_text(encoding="utf-8")
            self.assertIn(
                "[打开工作区变更补丁](workspace-changes.patch)",
                acceptance,
            )
            workspace_patch = Path(
                task_directory,
                "workspace-changes.patch",
            ).read_text(encoding="utf-8")
            self.assertIn("# Project: project-empty", workspace_patch)
            self.assertIn(
                "# No displayable text diff in this snapshot.",
                workspace_patch,
            )
            self.assertIn(
                "# Project: project-uncommitted",
                workspace_patch,
            )
            self.assertIn(
                f"# Workspace: {secondary_worktree.resolve()}",
                workspace_patch,
            )
            self.assertIn(
                "+secondary uncommitted evidence",
                workspace_patch,
            )

    def test_workspace_change_capture_fails_if_working_tree_changes_during_snapshot(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root, delivery_id="d-evidence-race")
            )
            scope = {
                "id": "project-race",
                "workspaceRoot": str(worktree.resolve()),
                "access": "READ_WRITE",
                "gitBinding": {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            }
            with patch(
                "hdg.git_binding_changes._working_tree_state",
                side_effect=[
                    {"stateFingerprint": "before"},
                    {"stateFingerprint": "after"},
                ],
            ):
                with self.assertRaises(GatedLoopError) as caught:
                    capture_verified_workspace_changes([scope])
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_DIFF_CHANGED",
            )

    def test_git_binding_discovery_falls_back_to_master(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root, mainline="master")
            )
            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "master",
                    "baseCommit": base_commit,
                    "integrationTarget": "master",
                },
            )
            self.assertEqual(
                discovered["workspaceProvenance"]["selectionSource"],
                "LOCAL_MASTER_FALLBACK",
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit=base_commit,
                base_ref="master",
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                prepared["gitBinding"]["baseRef"],
                "master",
            )

    def test_git_binding_discovery_prefers_valid_origin_head(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(repository, "branch", "release", base_commit)
            git_command(
                repository,
                "update-ref",
                "refs/remotes/origin/release",
                base_commit,
            )
            git_command(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/release",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )

            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "release",
                    "baseCommit": base_commit,
                    "integrationTarget": "release",
                },
            )
            self.assertEqual(
                discovered["workspaceProvenance"]["selectionSource"],
                "ORIGIN_HEAD",
            )

    def test_git_binding_discovery_ignores_dangling_origin_head(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/missing",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )

            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["workspaceProvenance"]["selectionSource"],
                "LOCAL_MAIN_FALLBACK",
            )

    def test_git_binding_discovery_prefers_host_selected_base(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(repository, "branch", "release", base_commit)
            git_command(
                repository,
                "update-ref",
                "refs/remotes/origin/release",
                base_commit,
            )
            git_command(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/release",
            )

            discovered = call_tool(
                "workspace_status",
                {"base_ref": "main"},
                root=str(repository),
                workspace_root=str(worktree),
            )

            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["workspaceProvenance"]["selectionSource"],
                "HOST_SELECTED",
            )

    def test_host_selected_base_prefers_origin_tracking_ref(self) -> None:
        with TemporaryDirectory() as root:
            repository, _, base_commit, _ = git_delivery_checkout(root)
            git_command(repository, "switch", "main")
            git_command(repository, "branch", "release", base_commit)
            tree = git_command(repository, "rev-parse", "HEAD^{tree}")
            remote_commit = git_command(
                repository,
                "commit-tree",
                tree,
                "-p",
                base_commit,
                "-m",
                "Remote release head",
            )
            git_command(
                repository,
                "update-ref",
                "refs/remotes/origin/release",
                remote_commit,
            )
            git_command(repository, "switch", "release")

            discovered = call_tool(
                "workspace_status",
                {"base_ref": "release"},
                root=str(repository),
                workspace_root=str(repository),
            )

            self.assertEqual(
                discovered["workspacePreparation"]["baseCommit"],
                remote_commit,
            )
