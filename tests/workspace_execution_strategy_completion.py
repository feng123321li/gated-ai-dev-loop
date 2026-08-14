from __future__ import annotations

from .workspace_execution_strategy_support import (
    GatedLoopError,
    Path,
    SchedulerRepository,
    TemporaryDirectory,
    _complete_to_user_confirmation,
    _confirm_existing_branch,
    _confirm_new_branch,
    _is_waiting_for_workspace_turn,
    _repository,
    _resume,
    _select,
    call_tool,
    deepcopy,
    freeze_hierarchy,
    git_command,
    prepare_delivery_revision,
)


class WorkspaceExecutionStrategyTestsPart2:
    def test_revised_unaccepted_delivery_reenters_workspace_queue(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_id = "d-unaccepted-revision-first"
            first_task_id = "t-unaccepted-revision-first"
            first_branch = f"feature/{first_id}"
            second_id = "d-unaccepted-revision-second"
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
                "t-unaccepted-revision-second",
                second_branch,
            )
            _select(repository, first)
            _select(repository, second)
            implementation = repository / "unaccepted-revision-first.txt"
            implementation.write_text(
                "first revision implementation\n",
                encoding="utf-8",
            )
            _complete_to_user_confirmation(
                repository,
                delivery_id=first_id,
                task_id=first_task_id,
            )
            git_command(repository, "add", implementation.name)
            git_command(
                repository,
                "commit",
                "-m",
                "Commit first revision before confirmation",
            )
            branch_preparation = _resume(repository, second)
            self.assertEqual(
                branch_preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            scheduler = SchedulerRepository(str(repository))
            revised = deepcopy(scheduler.hierarchy(first_id)["hierarchy"])
            revised["root"]["definition"]["summary"] = (
                "Revise the unaccepted Delivery after user feedback."
            )
            prepared = prepare_delivery_revision(
                root=str(repository),
                root_id=first_id,
                expected_current_revision=1,
                hierarchy=revised,
                reason="The user requested changes before final acceptance.",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="human",
                workspace_root=str(repository),
            )

            queued = freeze_hierarchy(
                root=str(repository),
                root_id=first_id,
                expected_delivery_revision=2,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
                workspace_root=str(repository),
            )

            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(
                queued["workspaceTurn"]["state"],
                "WAITING_FOR_WORKSPACE_TURN",
            )
            self.assertEqual(queued["workspaceTurn"]["ownerRootId"], second_id)
            self.assertEqual(
                scheduler.serial_workspace_turn_state(first_id)["state"],
                "WAITING_FOR_WORKSPACE_TURN",
            )
            git_command(repository, "switch", "-c", second_branch, "main")
            self.assertEqual(_resume(repository, second)["status"], "ACTIVE")
            (repository / "unaccepted-revision-second.txt").write_text(
                "second Delivery checkpoint\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "unaccepted-revision-second.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Checkpoint second Delivery before cancellation",
            )
            call_tool(
                "cancel_graph_run",
                {
                    "root_id": second_id,
                    "cancelled_by": "human",
                    "reason": "Release the workspace for the revised Delivery.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            preparation = freeze_hierarchy(
                root=str(repository),
                root_id=first_id,
                expected_delivery_revision=2,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
                workspace_root=str(repository),
            )

            self.assertEqual(
                preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            self.assertEqual(
                preparation["workspacePreparation"][
                    "automaticHostPreparation"
                ]["state"],
                "READY",
            )
            git_command(repository, "switch", first_branch)
            resumed = freeze_hierarchy(
                root=str(repository),
                root_id=first_id,
                expected_delivery_revision=2,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
                workspace_root=str(repository),
            )
            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertEqual(resumed["deliveryRevision"], 2)

    def test_manual_delivery_confirmation_ready_releases_clean_turn(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_id = "d-manual-confirmation-first"
            first_task_id = "t-manual-confirmation-first"
            first_branch = f"feature/{first_id}"
            second_id = "d-after-manual-confirmation"
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
                "t-after-manual-confirmation",
                second_branch,
            )
            handoff = call_tool(
                "select_execution_mode",
                {
                    "root_id": first_id,
                    "selection": "MANUAL",
                    "expected_hierarchy_fingerprint": first[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": first[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            call_tool(
                "start_manual_handoff",
                {
                    "root_id": first_id,
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "started_by": "manual-developer",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(_select(repository, second)["status"], "QUEUED")
            implementation = repository / "manual-confirmation-first.txt"
            implementation.write_text(
                "manual implementation awaiting confirmation\n",
                encoding="utf-8",
            )
            frontier = _complete_to_user_confirmation(
                repository,
                delivery_id=first_id,
                task_id=first_task_id,
                manual_task=True,
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
                "Commit manual implementation before confirmation",
            )

            branch_preparation = _resume(repository, second)

            self.assertFalse(
                _is_waiting_for_workspace_turn(branch_preparation),
                branch_preparation,
            )
            self.assertEqual(
                branch_preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )

    def test_cancelled_clean_turn_ignores_stale_rebase_and_releases(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            first_id = "d-cancelled-stale-rebase"
            first_branch = f"feature/{first_id}"
            git_command(repository, "switch", "-c", first_branch)
            first = _confirm_existing_branch(
                repository,
                first_id,
                "t-cancelled-stale-rebase",
                first_branch,
            )
            self.assertEqual(_select(repository, first)["status"], "ACTIVE")
            (repository / "cancelled-work.txt").write_text(
                "committed before cancellation\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "cancelled-work.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Checkpoint work before cancellation",
            )
            git_command(repository, "switch", "main")
            (repository / "mainline-advance.txt").write_text(
                "advance integration target\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "mainline-advance.txt")
            git_command(repository, "commit", "-m", "Advance mainline")
            git_command(repository, "switch", first_branch)
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": first_id,
                    "cancelled_by": "human",
                    "reason": "The business goal was abandoned.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["status"], "CANCELLED")

            status = call_tool(
                "workspace_status",
                {"root_id": first_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(status["status"], "CANCELLED")
            self.assertEqual(status["workspaceTurn"]["state"], "RELEASED")
            self.assertNotIn("workspaceRebase", status)
            self.assertIsNotNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

    def test_deliveries_cannot_share_branch(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _base_commit = _repository(Path(temporary))
            branch_ref = "feature/d-shared"
            _confirm_new_branch(
                repository,
                "d-shared-first",
                "t-shared-first",
                branch_ref,
            )

            with self.assertRaises(GatedLoopError) as conflicting:
                _confirm_new_branch(
                    repository,
                    "d-shared-second",
                    "t-shared-second",
                    branch_ref,
                )

            self.assertEqual(
                conflicting.exception.code,
                "SCHEDULER_BASELINE_BRANCH_IN_USE",
            )
            self.assertEqual(
                conflicting.exception.details[
                    "conflictingDeliveries"
                ][0]["rootId"],
                "d-shared-first",
            )
            scheduler = SchedulerRepository(str(repository))
            for root_id in ("d-shared-first", "d-shared-second"):
                with self.assertRaises(GatedLoopError) as missing:
                    scheduler.run(root_id)
                self.assertEqual(
                    missing.exception.code,
                    "SCHEDULER_RUN_MISSING",
                )
