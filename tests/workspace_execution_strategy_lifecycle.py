from __future__ import annotations

from .workspace_execution_strategy_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    _complete_to_user_confirmation,
    _confirm_existing_branch,
    _confirm_new_branch,
    _is_waiting_for_workspace_commit,
    _is_waiting_for_workspace_turn,
    _preview,
    _repository,
    _resume,
    _select,
    call_tool,
    deepcopy,
    freeze_hierarchy,
    git_command,
    isolated_task_hierarchy,
    prepare_delivery_revision,
    subprocess,
)


class WorkspaceExecutionStrategyTestsPart1:
    def test_revision_continues_dirty_delivery_workspace_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            delivery_id = "d-dirty-revision-continuation"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                "t-dirty-revision-continuation",
                branch_ref,
            )
            active = _select(repository, confirmed)
            self.assertEqual(active["status"], "ACTIVE")

            scheduler = SchedulerRepository(str(repository))
            original_turn_start = scheduler.workspace_turn_start(
                delivery_id
            )
            (repository / "README.md").write_text(
                "# unfinished Revision 1 implementation\n",
                encoding="utf-8",
            )
            (repository / "staged-result.py").write_text(
                "RESULT = 'unfinished'\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "staged-result.py")
            generated = repository / "__pycache__"
            generated.mkdir()
            (generated / "generated.pyc").write_bytes(b"generated")
            for revision in range(2, 6):
                revised = deepcopy(
                    scheduler.hierarchy(delivery_id)["hierarchy"]
                )
                revised["root"]["definition"]["summary"] = (
                    "Continue the same dirty Delivery as Revision "
                    f"{revision}."
                )
                candidate = prepare_delivery_revision(
                    root=str(repository),
                    root_id=delivery_id,
                    expected_current_revision=revision - 1,
                    hierarchy=revised,
                    reason=(
                        "Revise the same Delivery before final acceptance."
                    ),
                    continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                    requested_by="human",
                    workspace_root=str(repository),
                )

                frozen = freeze_hierarchy(
                    root=str(repository),
                    root_id=delivery_id,
                    expected_delivery_revision=revision,
                    expected_hierarchy_fingerprint=candidate[
                        "hierarchyFingerprint"
                    ],
                    authorized_project_ids=[],
                    confirmed=True,
                    confirmed_by="human",
                    workspace_root=str(repository),
                )

                self.assertEqual(frozen["deliveryRevision"], revision)
                self.assertEqual(frozen["status"], "ACTIVE")
                self.assertEqual(
                    scheduler.workspace_turn_start(delivery_id),
                    original_turn_start,
                )
            self.assertEqual(
                git_command(repository, "status", "--short", "README.md"),
                "M README.md",
            )
            self.assertEqual(
                git_command(
                    repository,
                    "status",
                    "--short",
                    "staged-result.py",
                ),
                "A  staged-result.py",
            )
            self.assertEqual(
                git_command(
                    repository,
                    "status",
                    "--short",
                    "__pycache__",
                ),
                "?? __pycache__/",
            )

    def test_revision_rejects_rewritten_workspace_turn_history(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, base_commit = _repository(Path(temporary))
            delivery_id = "d-rewritten-revision-turn"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            (repository / "before-turn.txt").write_text(
                "committed before the Delivery turn\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "before-turn.txt")
            git_command(repository, "commit", "-m", "Prepare feature")
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                "t-rewritten-revision-turn",
                branch_ref,
            )
            active = _select(repository, confirmed)
            self.assertEqual(active["status"], "ACTIVE")
            git_command(repository, "reset", "--hard", base_commit)

            scheduler = SchedulerRepository(str(repository))
            revised = deepcopy(
                scheduler.hierarchy(delivery_id)["hierarchy"]
            )
            revised["root"]["definition"]["summary"] = (
                "Attempt to continue after rewriting the turn history."
            )
            candidate = prepare_delivery_revision(
                root=str(repository),
                root_id=delivery_id,
                expected_current_revision=1,
                hierarchy=revised,
                reason="Exercise rewritten turn protection.",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="human",
                workspace_root=str(repository),
            )

            with self.assertRaises(GatedLoopError) as rejected:
                freeze_hierarchy(
                    root=str(repository),
                    root_id=delivery_id,
                    expected_delivery_revision=2,
                    expected_hierarchy_fingerprint=candidate[
                        "hierarchyFingerprint"
                    ],
                    authorized_project_ids=[],
                    confirmed=True,
                    confirmed_by="human",
                    workspace_root=str(repository),
                )

            self.assertEqual(
                rejected.exception.code,
                "SCHEDULER_GIT_TURN_START_INVALID",
            )

    def test_revision_binding_change_requires_a_clean_boundary(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            delivery_id = "d-changed-revision-binding"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "branch", "release")
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                "t-changed-revision-binding",
                branch_ref,
            )
            active = _select(repository, confirmed)
            self.assertEqual(active["status"], "ACTIVE")
            (repository / "README.md").write_text(
                "# unfinished work under the original binding\n",
                encoding="utf-8",
            )

            scheduler = SchedulerRepository(str(repository))
            revised = deepcopy(
                scheduler.hierarchy(delivery_id)["hierarchy"]
            )
            revised["delivery"]["gitBinding"]["baseRef"] = "release"
            revised["delivery"]["gitBinding"][
                "integrationTarget"
            ] = "release"
            candidate = prepare_delivery_revision(
                root=str(repository),
                root_id=delivery_id,
                expected_current_revision=1,
                hierarchy=revised,
                reason="Change the frozen integration target.",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="human",
                workspace_root=str(repository),
            )

            with self.assertRaises(GatedLoopError) as rejected:
                freeze_hierarchy(
                    root=str(repository),
                    root_id=delivery_id,
                    expected_delivery_revision=2,
                    expected_hierarchy_fingerprint=candidate[
                        "hierarchyFingerprint"
                    ],
                    authorized_project_ids=[],
                    confirmed=True,
                    confirmed_by="human",
                    workspace_root=str(repository),
                )

            self.assertEqual(
                rejected.exception.code,
                "SCHEDULER_WORKSPACE_TURN_DIRTY",
            )

    def test_revision_rejects_unresolved_git_conflicts(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            git_command(repository, "switch", "-c", "conflict-side")
            (repository / "README.md").write_text(
                "conflicting side change\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Create side change")
            git_command(repository, "switch", "main")
            delivery_id = "d-conflicted-revision-turn"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                "t-conflicted-revision-turn",
                branch_ref,
            )
            active = _select(repository, confirmed)
            self.assertEqual(active["status"], "ACTIVE")
            (repository / "README.md").write_text(
                "conflicting Delivery change\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(
                repository,
                "commit",
                "-m",
                "Create Delivery-side change",
            )
            with self.assertRaises(subprocess.CalledProcessError):
                git_command(repository, "merge", "conflict-side")

            scheduler = SchedulerRepository(str(repository))
            revised = deepcopy(
                scheduler.hierarchy(delivery_id)["hierarchy"]
            )
            revised["root"]["definition"]["summary"] = (
                "Attempt to continue with unresolved conflicts."
            )
            candidate = prepare_delivery_revision(
                root=str(repository),
                root_id=delivery_id,
                expected_current_revision=1,
                hierarchy=revised,
                reason="Exercise unresolved conflict protection.",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="human",
                workspace_root=str(repository),
            )

            with self.assertRaises(GatedLoopError) as rejected:
                freeze_hierarchy(
                    root=str(repository),
                    root_id=delivery_id,
                    expected_delivery_revision=2,
                    expected_hierarchy_fingerprint=candidate[
                        "hierarchyFingerprint"
                    ],
                    authorized_project_ids=[],
                    confirmed=True,
                    confirmed_by="human",
                    workspace_root=str(repository),
                )

            self.assertEqual(
                rejected.exception.code,
                "SCHEDULER_WORKSPACE_TURN_DIRTY",
            )
            self.assertEqual(
                rejected.exception.details["nextAction"],
                "RESOLVE_CONFLICTS_BEFORE_FREEZING_REVISION",
            )

    def test_automatic_defaults_to_current_workspace_serial(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            confirmed = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-serial-ready",
                        "t-serial-ready",
                    )
                },
                root=str(workspace),
                workspace_root=str(workspace),
                trusted_host_adapter="codex",
            )

            selected = _select(workspace, confirmed)

            self.assertEqual(selected["status"], "ACTIVE")
            self.assertEqual(
                selected["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )

    def test_serial_choice_waits_for_current_delivery_branch(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            confirmed = _confirm_new_branch(
                repository,
                "d-serial-waiting",
                "t-serial-waiting",
                "feature/d-serial-waiting",
            )

            selected = _select(repository, confirmed)

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertEqual(
                selected["workspacePreparation"]["strategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertNotIn("worktreeSetup", selected)
            self.assertNotIn("projectWorktreeSetup", selected)
            self.assertNotIn(
                "controllerCreatesWorktree",
                selected["workspacePreparation"],
            )
            self.assertEqual(
                selected["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            host_preparation = selected["workspacePreparation"][
                "automaticHostPreparation"
            ]
            self.assertEqual(host_preparation["state"], "READY")
            self.assertFalse(host_preparation["confirmationRequired"])
            self.assertEqual(
                [item["action"] for item in host_preparation["actions"]],
                [
                    "CREATE_OR_SWITCH_DELIVERY_BRANCH",
                    "RESUME_EXECUTION_MODE",
                ],
            )
            scheduler = SchedulerRepository(str(repository))
            self.assertEqual(
                scheduler.execution_selection("d-serial-waiting")[
                    "selection"
                ],
                "AUTOMATIC",
            )

    def test_dirty_unrelated_changes_offer_stash_or_wait_before_branch_switch(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            (repository / "README.md").write_text(
                "# staged unrelated change\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            (repository / "untracked-note.txt").write_text(
                "untracked unrelated change\n",
                encoding="utf-8",
            )
            branch_ref = "feature/d-stash-before-run"

            confirmed = _confirm_new_branch(
                repository,
                "d-stash-before-run",
                "t-stash-before-run",
                branch_ref,
            )
            selected = _select(repository, confirmed)

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["nextAction"],
                "HOST_STASH_PREPARE_BRANCH_THEN_RESUME_EXECUTION",
            )
            self.assertEqual(
                git_command(repository, "branch", "--show-current"),
                "main",
            )
            self.assertEqual(
                len(
                    git_command(
                        repository,
                        "status",
                        "--porcelain",
                        "--",
                        ".",
                        ":(exclude).layered-delivery",
                        ":(exclude).layered-delivery/**",
                    ).splitlines()
                ),
                2,
            )

            preparation = selected["workspacePreparation"]
            project = preparation["projectPreparations"][0]
            working_tree = project["workingTree"]
            self.assertTrue(working_tree["hasStagedChanges"])
            self.assertTrue(working_tree["hasUntrackedChanges"])
            self.assertFalse(working_tree["hasUnmergedChanges"])
            handling = preparation["workspaceChangeHandling"]
            self.assertEqual(
                handling["kind"],
                "AUTOMATIC_DIRTY_WORKSPACE_PREPARATION",
            )
            self.assertEqual(handling["action"], "STASH_AND_RUN")
            self.assertFalse(handling["confirmationRequired"])
            self.assertEqual(
                handling["authorizationSource"],
                "AUTOMATIC_EXECUTION_SELECTION",
            )
            self.assertEqual(
                handling["fallbackAction"],
                "KEEP_CHANGES_AND_WAIT",
            )
            stash = handling["hostAction"]
            self.assertEqual(stash["owner"], "HOST")
            self.assertFalse(stash["controllerExecutesGit"])
            self.assertEqual(
                stash["expectedProjects"],
                [
                    {
                        "projectId": "d-stash-before-run",
                        "workspaceRoot": str(repository.resolve()),
                        "workingTreeStateFingerprint": working_tree[
                            "stateFingerprint"
                        ],
                    }
                ],
            )
            self.assertTrue(stash["stashPolicy"]["includeUntracked"])
            self.assertEqual(
                stash["stashPolicy"]["pathspec"],
                [
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                ],
            )
            self.assertTrue(stash["restorePolicy"]["restoreIndex"])
            self.assertFalse(
                handling["preservedUnrelatedChanges"]["supported"]
            )
            self.assertEqual(
                handling["preservedUnrelatedChanges"]["reason"],
                "DELIVERY_TURN_MUST_START_CLEAN",
            )
            self.assertEqual(
                [
                    item["action"]
                    for item in preparation["automaticHostPreparation"][
                        "actions"
                    ]
                ],
                [
                    "STASH_BUSINESS_CHANGES",
                    "CREATE_OR_SWITCH_DELIVERY_BRANCH",
                    "RESUME_EXECUTION_MODE",
                ],
            )

            git_command(
                repository,
                "stash",
                "push",
                "--include-untracked",
                "--message",
                "delivery-graph:d-stash-before-run",
                "--",
                ".",
                ":(exclude).layered-delivery",
                ":(exclude).layered-delivery/**",
            )
            self.assertEqual(
                git_command(
                    repository,
                    "status",
                    "--porcelain",
                    "--",
                    ".",
                    ":(exclude).layered-delivery",
                    ":(exclude).layered-delivery/**",
                ),
                "",
            )
            git_command(repository, "switch", "-c", branch_ref, "main")

            resumed = _resume(repository, confirmed)

            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertTrue(resumed["automaticDispatchRequested"])
            self.assertIn(
                "delivery-graph:d-stash-before-run",
                git_command(repository, "stash", "list"),
            )

    def test_dirty_current_branch_adoption_still_requires_attribution(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            branch_ref = "feature/d-owned-dirty"
            git_command(repository, "switch", "-c", branch_ref)
            (repository / "owned.txt").write_text(
                "delivery-owned change\n",
                encoding="utf-8",
            )
            preview = _preview(
                repository,
                "d-owned-dirty",
                "t-owned-dirty",
            )

            with self.assertRaises(GatedLoopError) as missing:
                call_tool(
                    "confirm_development_baseline",
                    {
                        "root_id": "d-owned-dirty",
                        "selection": branch_ref,
                        "expected_hierarchy_fingerprint": preview[
                            "hierarchyFingerprint"
                        ],
                        "confirmed_by": "human",
                    },
                    root=str(repository),
                    workspace_root=str(repository),
                    trusted_host_adapter="codex",
                )
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_GIT_DIRTY_CONFIRMATION_REQUIRED",
            )

            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-owned-dirty",
                    "selection": branch_ref,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_dirty_state_fingerprint": preview[
                        "developmentBaseline"
                    ][
                        "workingTree"
                    ]["stateFingerprint"],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(confirmed["rootId"], "d-owned-dirty")

    def test_unmerged_workspace_does_not_offer_executable_stash(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            git_command(repository, "switch", "-c", "conflict-side")
            (repository / "README.md").write_text(
                "side branch\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Side change")
            git_command(repository, "switch", "main")
            (repository / "README.md").write_text(
                "main branch\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Main change")
            with self.assertRaises(subprocess.CalledProcessError):
                git_command(repository, "merge", "conflict-side")

            confirmed = _confirm_new_branch(
                repository,
                "d-conflicted-before-run",
                "t-conflicted-before-run",
                "feature/d-conflicted-before-run",
            )
            selected = _select(repository, confirmed)

            preparation = selected["workspacePreparation"]
            self.assertEqual(
                selected["nextAction"],
                "RESOLVE_CONFLICTS_OR_KEEP_CHANGES_AND_WAIT",
            )
            self.assertTrue(
                preparation["projectPreparations"][0]["workingTree"][
                    "hasUnmergedChanges"
                ]
            )
            handling = preparation["workspaceChangeHandling"]
            self.assertEqual(handling["action"], "KEEP_CHANGES_AND_WAIT")
            self.assertEqual(
                handling["blockedAutomaticAction"],
                "STASH_AND_RUN",
            )
            self.assertEqual(
                handling["blockedReason"],
                "UNMERGED_CHANGES",
            )

    def test_serial_delivery_waits_for_turn_before_switch_and_activation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_branch = "feature/d-serial-first"
            second_branch = "feature/d-serial-second"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                "d-serial-first",
                "t-serial-first",
                first_branch,
            )
            second = _confirm_new_branch(
                repository,
                "d-serial-second",
                "t-serial-second",
                second_branch,
            )
            first_active = _select(repository, first)
            self.assertEqual(first_active["status"], "ACTIVE")

            second_waiting = _select(repository, second)

            scheduler = SchedulerRepository(str(repository))

            self.assertEqual(
                second_waiting["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertEqual(second_waiting["status"], "QUEUED")
            self.assertFalse(
                second_waiting["automaticDispatchRequested"]
            )
            self.assertTrue(second_waiting["selectionRecorded"])
            self.assertTrue(
                _is_waiting_for_workspace_turn(second_waiting),
                second_waiting,
            )
            self.assertIn(
                "排队中（等待工作区串行调度）",
                (
                    repository
                    / ".layered-delivery"
                    / "d-serial-second"
                    / "overview.md"
                ).read_text(encoding="utf-8"),
            )
            implementation = repository / "serial-first.txt"
            implementation.write_text(
                "committed implementation\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "serial-first.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Complete first serial workspace turn",
            )
            committed_but_active = _resume(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_turn(committed_but_active),
                committed_but_active,
            )
            self.assertFalse(
                committed_but_active["automaticDispatchRequested"]
            )
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": "d-serial-first",
                    "cancelled_by": "human",
                    "reason": "Release the serial workspace turn.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["status"], "CANCELLED")

            branch_preparation = _resume(repository, second)

            self.assertFalse(
                _is_waiting_for_workspace_turn(branch_preparation),
                branch_preparation,
            )
            self.assertFalse(
                branch_preparation["automaticDispatchRequested"]
            )
            self.assertEqual(
                branch_preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            git_command(
                repository,
                "switch",
                "-c",
                second_branch,
                "main",
            )
            second_active = _resume(repository, second)

            self.assertEqual(second_active["status"], "ACTIVE")
            self.assertEqual(
                second_active["workspaceStrategy"],
                "CURRENT_WORKSPACE_SERIAL",
            )
            self.assertEqual(
                scheduler.run("d-serial-first")["status"],
                "CANCELLED",
            )
            self.assertEqual(
                scheduler.run("d-serial-second")["status"],
                "ACTIVE",
            )

    def test_user_confirmation_ready_releases_committed_clean_turn(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_id = "d-confirmation-ready-first"
            first_task_id = "t-confirmation-ready-first"
            first_branch = f"feature/{first_id}"
            second_id = "d-confirmation-ready-second"
            second_branch = f"feature/{second_id}"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                first_id,
                first_task_id,
                first_branch,
            )
            second = _confirm_new_branch(
                repository,
                second_id,
                "t-confirmation-ready-second",
                second_branch,
            )
            self.assertEqual(_select(repository, first)["status"], "ACTIVE")
            self.assertEqual(_select(repository, second)["status"], "QUEUED")

            implementation = repository / "confirmation-ready-first.txt"
            implementation.write_text(
                "implementation awaiting user confirmation\n",
                encoding="utf-8",
            )
            frontier = _complete_to_user_confirmation(
                repository,
                delivery_id=first_id,
                task_id=first_task_id,
            )
            self.assertIn(
                "RECORD_USER_CONFIRMATION",
                [action["action"] for action in frontier["actions"]],
            )
            git_command(repository, "add", implementation.name)
            git_command(
                repository,
                "commit",
                "-m",
                "Complete implementation before user confirmation",
            )

            branch_preparation = _resume(repository, second)

            scheduler = SchedulerRepository(str(repository))
            self.assertEqual(scheduler.run(first_id)["status"], "ACTIVE")
            self.assertIsNotNone(scheduler.workspace_turn_release(first_id))
            self.assertFalse(
                _is_waiting_for_workspace_turn(branch_preparation),
                branch_preparation,
            )
            self.assertEqual(
                branch_preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            git_command(repository, "switch", "-c", second_branch, "main")
            self.assertEqual(_resume(repository, second)["status"], "ACTIVE")
            completed = call_tool(
                "record_user_confirmation",
                {
                    "root_id": first_id,
                    "confirmed": True,
                    "confirmed_by": "human",
                    "summary": "Accepted after the workspace turn moved on.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(
                scheduler.serial_workspace_turn_state(second_id)["state"],
                "ACQUIRED",
            )

    def test_user_confirmation_ready_keeps_dirty_turn_blocked(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_id = "d-confirmation-dirty-first"
            first_task_id = "t-confirmation-dirty-first"
            first_branch = f"feature/{first_id}"
            second_id = "d-confirmation-dirty-second"
            second_branch = f"feature/{second_id}"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                first_id,
                first_task_id,
                first_branch,
            )
            second = _confirm_new_branch(
                repository,
                second_id,
                "t-confirmation-dirty-second",
                second_branch,
            )
            _select(repository, first)
            _select(repository, second)
            (repository / "confirmation-dirty-first.txt").write_text(
                "uncommitted implementation\n",
                encoding="utf-8",
            )
            _complete_to_user_confirmation(
                repository,
                delivery_id=first_id,
                task_id=first_task_id,
            )

            waiting = _resume(repository, second)

            scheduler = SchedulerRepository(str(repository))
            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertIsNone(scheduler.workspace_turn_release(first_id))
