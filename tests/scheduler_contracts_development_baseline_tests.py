from __future__ import annotations

from .scheduler_contracts_support import (
    Path,
    TemporaryDirectory,
    call_tool,
    git_command,
    git_delivery_checkout,
    isolated_task_hierarchy,
    unittest,
)


class DevelopmentBaselineTests(unittest.TestCase):
    def test_clean_primary_feature_recommends_stacked_child_branch(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            repository.mkdir()
            git_command(repository, "init", "--initial-branch=main")
            git_command(repository, "config", "user.name", "Scheduler Tests")
            git_command(
                repository,
                "config",
                "user.email",
                "scheduler-tests@example.invalid",
            )
            Path(repository, "README.md").write_text(
                "# stacked delivery fixture\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Initial main baseline")
            parent_branch = "feature/m_lf_protein"
            child_branch = "feature/m_lf_mprotein_409"
            git_command(repository, "switch", "-c", parent_branch)
            Path(repository, "parent.txt").write_text(
                "parent feature content\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "parent.txt")
            git_command(repository, "commit", "-m", "Parent feature baseline")
            parent_head = git_command(repository, "rev-parse", "HEAD")

            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-stacked-child",
                        "t-stacked-child",
                    )
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            interaction = preview["pendingInteraction"]
            stacked = next(
                option
                for option in interaction["options"]
                if option["id"] == "NEW_FROM_CURRENT_BRANCH"
            )
            self.assertEqual(
                interaction["defaultOptionId"],
                "NEW_FROM_CURRENT_BRANCH",
            )
            self.assertEqual(
                interaction["recommendedOptionId"],
                "NEW_FROM_CURRENT_BRANCH",
            )
            self.assertTrue(stacked["stackedDelivery"])
            self.assertEqual(stacked["baseRef"], parent_branch)
            self.assertEqual(stacked["baseCommit"], parent_head)
            self.assertEqual(stacked["integrationTarget"], parent_branch)
            self.assertIn(
                "创建子分支（默认、推荐）",
                interaction["markdown"],
            )

            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-stacked-child",
                    "selection": "NEW_FROM_CURRENT_BRANCH",
                    "branch_name": child_branch,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "expected_delivery_revision": 1,
                    "baseline_context_fingerprint": interaction[
                        "baselineContextFingerprint"
                    ],
                    "confirmed_by": "李锋",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            preference = confirmed["developmentBaselineConfirmed"]
            self.assertEqual(preference["branchRef"], child_branch)
            self.assertEqual(preference["baseRef"], parent_branch)
            self.assertEqual(preference["baseCommit"], parent_head)
            self.assertEqual(preference["integrationTarget"], parent_branch)
            self.assertEqual(preference["source"], "NEW_FROM_CURRENT_BRANCH")
            self.assertEqual(
                confirmed["executionChoice"]["baseRef"],
                parent_branch,
            )
            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-stacked-child",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": confirmed[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": confirmed[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "李锋",
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
            self.assertEqual(
                preparation["strategy"], "CURRENT_WORKSPACE_SERIAL"
            )
            self.assertNotIn("hostDispatch", preparation)
            project_preparation = preparation["projectPreparations"][0]
            self.assertEqual(project_preparation["branchRef"], child_branch)
            self.assertEqual(
                project_preparation["gitBinding"]["baseRef"],
                parent_branch,
            )
            self.assertEqual(
                project_preparation["gitBinding"]["baseCommit"],
                parent_head,
            )
            self.assertEqual(
                project_preparation["gitBinding"]["integrationTarget"],
                parent_branch,
            )
            self.assertEqual(
                git_command(repository, "branch", "--list", child_branch),
                "",
            )

    def test_dirty_primary_feature_does_not_offer_stacked_child(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            repository.mkdir()
            git_command(repository, "init", "--initial-branch=main")
            git_command(repository, "config", "user.name", "Scheduler Tests")
            git_command(
                repository,
                "config",
                "user.email",
                "scheduler-tests@example.invalid",
            )
            Path(repository, "README.md").write_text("base\n", encoding="utf-8")
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Initial baseline")
            git_command(repository, "switch", "-c", "feature/parent")
            Path(repository, "dirty.txt").write_text("dirty\n", encoding="utf-8")

            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-dirty-stacked",
                        "t-dirty-stacked",
                    )
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )
            self.assertNotIn(
                "NEW_FROM_CURRENT_BRANCH",
                {
                    option["id"]
                    for option in preview["pendingInteraction"]["options"]
                },
            )

    def test_clean_working_tree_without_binding_prompts_then_confirms_and_remembers(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, branch_ref = (
                git_delivery_checkout(root, delivery_id="d-baseline")
            )
            hierarchy = isolated_task_hierarchy("d-baseline", "t-baseline")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertEqual(
                preview["nextAction"],
                "PRESENT_HOST_NATIVE_BASELINE_CHOICE",
            )
            self.assertIn("developmentBaseline", preview)
            self.assertNotIn("executionChoice", preview)
            option_ids = [
                option["id"]
                for option in preview["developmentBaseline"]["options"]
            ]
            self.assertIn(branch_ref, option_ids)
            self.assertIn("NEW_FROM_MAINLINE", option_ids)
            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-baseline",
                    "selection": branch_ref,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_by": "李锋",
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertIn("executionChoice", confirmed)
            self.assertEqual(
                confirmed["developmentBaselineConfirmed"]["branchRef"],
                branch_ref,
            )
            # Regression for the master-default bug: executionChoice now carries
            # the confirmed mainline base instead of None.
            self.assertEqual(
                confirmed["executionChoice"]["baseRef"], "main"
            )
            self.assertNotEqual(
                confirmed["hierarchyFingerprint"],
                preview["hierarchyFingerprint"],
            )
            remembered = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-baseline", "t-baseline"
                    )
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertIn("executionChoice", remembered)
            self.assertNotIn("developmentBaseline", remembered)

    def test_dirty_working_tree_requires_attributed_baseline(self) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root, delivery_id="d-dirty")
            )
            Path(worktree, "uncommitted.txt").write_text(
                "pending change\n", encoding="utf-8"
            )
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": isolated_task_hierarchy("d-dirty", "t-dirty")},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertEqual(
                preview["pendingInteraction"]["kind"],
                "DEVELOPMENT_BASELINE",
            )
            self.assertTrue(
                preview["developmentBaseline"][
                    "dirtyStateConfirmationRequired"
                ]
            )
            self.assertNotIn("executionChoice", preview)

    def test_new_from_mainline_pins_mainline_head(self) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root, delivery_id="d-new")
            )
            mainline_head = git_command(repository, "rev-parse", "main")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": isolated_task_hierarchy("d-new", "t-new")},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertIn("developmentBaseline", preview)
            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-new",
                    "selection": "NEW_FROM_MAINLINE",
                    "branch_name": "feature/new-baseline",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_by": "李锋",
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            preference = confirmed["developmentBaselineConfirmed"]
            self.assertEqual(preference["branchRef"], "feature/new-baseline")
            self.assertEqual(preference["baseRef"], "main")
            self.assertEqual(preference["baseCommit"], mainline_head)
            self.assertEqual(preference["source"], "NEW_FROM_MAINLINE")
