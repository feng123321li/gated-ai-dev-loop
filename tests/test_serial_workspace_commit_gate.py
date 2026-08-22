from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import git_command
from .test_workspace_execution_strategy import (
    _complete_to_user_confirmation,
    _confirm_existing_branch,
    _confirm_new_branch,
    _is_waiting_for_workspace_commit,
    _repository,
    _resume,
    _select,
    loop_node_id,
    reserve_loop,
)
from .scheduler_runtime_support import disjoint_parallel_hierarchy


def _terminal_first_turn(
    repository: Path,
    case: str,
    *,
    preexisting_commit: bool = False,
) -> tuple[dict, str, str]:
    first_id = f"d-{case}-first"
    second_id = f"d-{case}-second"
    first_branch = f"feature/{first_id}"
    second_branch = f"feature/{second_id}"
    git_command(repository, "switch", "-c", first_branch)
    if preexisting_commit:
        (repository / "before-turn.txt").write_text(
            "committed before the workspace turn\n",
            encoding="utf-8",
        )
        git_command(repository, "add", "before-turn.txt")
        git_command(repository, "commit", "-m", "Commit before turn start")
    first = _confirm_existing_branch(
        repository,
        first_id,
        f"t-{case}-first",
        first_branch,
    )
    second = _confirm_new_branch(
        repository,
        second_id,
        f"t-{case}-second",
        second_branch,
    )
    active = _select(repository, first)
    if active["status"] != "ACTIVE":
        raise AssertionError(active)
    turn_start = SchedulerRepository(
        str(repository)
    ).workspace_turn_start(first_id)
    start_commit = turn_start["projects"][0]["turnStartCommit"]
    cancelled = call_tool(
        "cancel_graph_run",
        {
            "root_id": first_id,
            "cancelled_by": "human",
            "reason": "Exercise the serial commit release gate.",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )
    if cancelled["status"] != "CANCELLED":
        raise AssertionError(cancelled)
    return second, first_id, start_commit


def _barrier_reason(result: dict) -> str:
    return result["workspaceTurn"]["projectBarriers"][0]["reason"]


class SerialWorkspaceCommitGateTests(unittest.TestCase):
    def test_cancelled_live_receiver_lease_exposes_quiescence_pending(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-cancelled-live-receiver"
            task_id = "t-cancelled-live-receiver"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)
            (repository / "cancelled-checkpoint.txt").write_text(
                "committed cancellation checkpoint\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "cancelled-checkpoint.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Commit before cancellation",
            )
            node_id = loop_node_id(task_id)
            reservation = reserve_loop(
                root=str(repository),
                root_id=delivery_id,
                node_id=node_id,
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": delivery_id,
                    "node_id": node_id,
                    "owner": "receiver-cancelled-live",
                    "agent_id": "codex",
                    "receiver_context_id": "context-cancelled-live",
                    "operation_id": "operation-cancelled-live",
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
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": delivery_id,
                    "cancelled_by": "human",
                    "reason": "Cancel while the receiver lease is live.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertEqual(
                cancelled["workspaceRelease"]["state"],
                "PENDING",
            )
            self.assertEqual(
                cancelled["workspaceRelease"]["reason"],
                "CANCELLED_RECEIVER_LEASE_ACTIVE",
            )
            self.assertEqual(
                cancelled["workspaceRelease"]["gateState"],
                "WAITING_FOR_WORKSPACE_QUIESCENCE",
            )
            self.assertEqual(
                cancelled["nextAction"],
                "QUIESCE_RECEIVERS_AND_RECHECK_RELEASE",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

    def test_paused_with_another_live_receiver_does_not_release(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-paused-live-receiver"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            hierarchy = disjoint_parallel_hierarchy()
            hierarchy["delivery"]["id"] = delivery_id
            hierarchy["delivery"]["title"] = "Pause with live receiver"
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": delivery_id,
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
            _select(repository, confirmed)
            reservations = {}
            for task_id in ("t-api", "t-core"):
                node_id = loop_node_id(task_id)
                reservation = reserve_loop(
                    root=str(repository),
                    root_id=delivery_id,
                    node_id=node_id,
                )
                reservations[task_id] = reservation
                call_tool(
                    "dispatch_loop",
                    {
                        "root_id": delivery_id,
                        "node_id": node_id,
                        "owner": f"receiver-{task_id}",
                        "agent_id": "codex",
                        "receiver_context_id": f"context-{task_id}",
                        "operation_id": f"operation-{task_id}",
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
                    },
                    root=str(repository),
                    workspace_root=str(repository),
                    trusted_host_adapter="codex",
                )

            paused = call_tool(
                "pause_loop",
                {
                    "root_id": delivery_id,
                    "node_id": loop_node_id("t-api"),
                    "operation_id": "operation-t-api",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(paused["status"], "PAUSED")
            self.assertEqual(paused["workspaceRelease"]["state"], "PENDING")
            self.assertEqual(
                paused["workspaceRelease"]["reason"],
                "RUN_NOT_AT_RELEASE_BOUNDARY",
            )
            self.assertEqual(
                SchedulerRepository(str(repository)).run(delivery_id)[
                    "status"
                ],
                "ACTIVE",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

    def test_completed_operation_persists_release_before_branch_switch(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-completed-handshake"
            task_id = "t-completed-handshake"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)
            (repository / "completed-result.txt").write_text(
                "completed business result\n",
                encoding="utf-8",
            )
            _complete_to_user_confirmation(
                repository,
                delivery_id=delivery_id,
                task_id=task_id,
            )
            git_command(repository, "add", "completed-result.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Commit completed Delivery result",
            )

            completed = call_tool(
                "record_user_confirmation",
                {
                    "root_id": delivery_id,
                    "confirmed": True,
                    "confirmed_by": "human",
                    "summary": "Accepted on the frozen Delivery branch.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(
                completed["workspaceRelease"]["state"],
                "RELEASED",
            )
            self.assertEqual(
                completed["workspaceTurn"]["state"],
                "RELEASED",
            )
            self.assertEqual(
                completed["nextAction"],
                "PREPARE_REVISION_OR_CLOSE_DELIVERY",
            )
            self.assertEqual(
                completed["workspaceNextAction"],
                "WORKSPACE_RELEASED_BRANCH_SWITCH_ALLOWED",
            )
            self.assertIsNotNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

    def test_completed_dirty_operation_exposes_release_pending(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-completed-dirty-handshake"
            task_id = "t-completed-dirty-handshake"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)
            _complete_to_user_confirmation(
                repository,
                delivery_id=delivery_id,
                task_id=task_id,
            )
            (repository / "uncommitted-result.txt").write_text(
                "not committed yet\n",
                encoding="utf-8",
            )

            completed = call_tool(
                "record_user_confirmation",
                {
                    "root_id": delivery_id,
                    "confirmed": True,
                    "confirmed_by": "human",
                    "summary": "Accepted, but Git is not released yet.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(
                completed["workspaceRelease"]["state"],
                "PENDING",
            )
            self.assertEqual(
                completed["workspaceRelease"]["reason"],
                "UNCOMMITTED_CHANGES",
            )
            self.assertEqual(
                completed["nextAction"],
                "PREPARE_REVISION_OR_CLOSE_DELIVERY",
            )
            self.assertEqual(
                completed["workspaceNextAction"],
                "COMMIT_CLEAN_FROZEN_WORKSPACE_AND_RECHECK_RELEASE",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

    def test_completed_after_branch_switch_stays_drifted_not_released(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-completed-drifted-handshake"
            task_id = "t-completed-drifted-handshake"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)
            (repository / "committed-before-drift.txt").write_text(
                "committed on the frozen branch\n",
                encoding="utf-8",
            )
            _complete_to_user_confirmation(
                repository,
                delivery_id=delivery_id,
                task_id=task_id,
            )
            git_command(repository, "add", "committed-before-drift.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Commit before unsafe branch switch",
            )
            git_command(repository, "switch", "main")

            completed = call_tool(
                "record_user_confirmation",
                {
                    "root_id": delivery_id,
                    "confirmed": True,
                    "confirmed_by": "human",
                    "summary": "Accepted after the host switched too early.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(completed["status"], "COMPLETED")
            self.assertEqual(
                completed["workspaceRelease"]["state"],
                "PENDING",
            )
            self.assertEqual(
                completed["workspaceRelease"]["reason"],
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )
            self.assertEqual(
                completed["workspaceTurn"]["projectBarriers"][0]["state"],
                "WORKSPACE_DRIFTED",
            )
            self.assertEqual(
                completed["nextAction"],
                "PREPARE_REVISION_OR_CLOSE_DELIVERY",
            )
            self.assertEqual(
                completed["workspaceNextAction"],
                "RESTORE_FROZEN_WORKSPACE_AND_RECHECK_RELEASE",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

    def test_paused_clean_checkpoint_releases_and_resume_requeues(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            first_id = "d-paused-release-first"
            first_task_id = "t-paused-release-first"
            first_branch = f"feature/{first_id}"
            second_id = "d-paused-release-second"
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
                "t-paused-release-second",
                second_branch,
            )
            _select(repository, first)
            _select(repository, second)
            (repository / "paused-checkpoint.txt").write_text(
                "safe paused checkpoint\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "paused-checkpoint.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Commit paused Delivery checkpoint",
            )
            node_id = loop_node_id(first_task_id)
            reservation = reserve_loop(
                root=str(repository),
                root_id=first_id,
                node_id=node_id,
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": first_id,
                    "node_id": node_id,
                    "owner": "receiver-paused-first",
                    "agent_id": "codex",
                    "receiver_context_id": "context-paused-first",
                    "operation_id": "operation-paused-first",
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
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            paused = call_tool(
                "pause_loop",
                {
                    "root_id": first_id,
                    "node_id": node_id,
                    "operation_id": "operation-paused-first",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(paused["status"], "PAUSED")
            self.assertEqual(paused["workspaceRelease"]["state"], "RELEASED")
            self.assertEqual(
                paused["workspaceRelease"]["releaseReason"],
                "RUN_PAUSED_SAFE_CHECKPOINT",
            )
            requeued = call_tool(
                "resume_loop",
                {"root_id": first_id, "node_id": node_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(requeued["status"], "QUEUED")
            self.assertEqual(
                requeued["workspaceTurn"]["state"],
                "WAITING_FOR_WORKSPACE_TURN",
            )
            self.assertEqual(
                requeued["workspaceTurn"]["ownerRootId"],
                second_id,
            )
            self.assertEqual(
                requeued["nextAction"],
                "WAIT_FOR_PAUSED_LOOP_WORKSPACE_TURN",
            )
            paused_node = next(
                node
                for node in SchedulerRepository(str(repository)).run(
                    first_id
                )["nodes"]
                if node["nodeId"] == node_id
            )
            self.assertEqual(paused_node["status"], "PAUSED")
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

            preparation = _resume(repository, second)
            self.assertEqual(
                preparation["nextAction"],
                "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            )
            git_command(repository, "switch", "-c", second_branch, "main")
            self.assertEqual(_resume(repository, second)["status"], "ACTIVE")
            (repository / "second-checkpoint.txt").write_text(
                "second Delivery checkpoint\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "second-checkpoint.txt")
            git_command(repository, "commit", "-m", "Commit second checkpoint")
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": second_id,
                    "cancelled_by": "human",
                    "reason": "Return the turn to the paused Delivery.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(
                cancelled["workspaceRelease"]["state"],
                "RELEASED",
            )
            git_command(repository, "switch", first_branch)

            resumed = call_tool(
                "resume_loop",
                {"root_id": first_id, "node_id": node_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(resumed["status"], "READY")
            self.assertEqual(
                resumed["workspaceTurn"]["state"],
                "ACQUIRED",
            )
            self.assertEqual(
                resumed["nextAction"],
                "READ_GRAPH_FRONTIER_AND_REDISPATCH_IN_INDEPENDENT_CONTEXT",
            )

    def test_paused_dirty_checkpoint_keeps_release_pending(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-paused-dirty"
            task_id = "t-paused-dirty"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)
            node_id = loop_node_id(task_id)
            reservation = reserve_loop(
                root=str(repository),
                root_id=delivery_id,
                node_id=node_id,
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": delivery_id,
                    "node_id": node_id,
                    "owner": "receiver-paused-dirty",
                    "agent_id": "codex",
                    "receiver_context_id": "context-paused-dirty",
                    "operation_id": "operation-paused-dirty",
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
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            (repository / "paused-dirty.txt").write_text(
                "uncommitted pause state\n",
                encoding="utf-8",
            )

            paused = call_tool(
                "pause_loop",
                {
                    "root_id": delivery_id,
                    "node_id": node_id,
                    "operation_id": "operation-paused-dirty",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(paused["status"], "PAUSED")
            self.assertEqual(paused["workspaceRelease"]["state"], "PENDING")
            self.assertEqual(
                paused["workspaceRelease"]["reason"],
                "UNCOMMITTED_CHANGES",
            )
            self.assertEqual(
                paused["nextAction"],
                "COMMIT_CLEAN_FROZEN_WORKSPACE_AND_RECHECK_RELEASE",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

            blocked_resume = call_tool(
                "resume_loop",
                {"root_id": delivery_id, "node_id": node_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(blocked_resume["status"], "PAUSED")
            self.assertEqual(
                blocked_resume["workspaceRelease"]["state"],
                "PENDING",
            )
            self.assertEqual(
                blocked_resume["workspaceRelease"]["reason"],
                "UNCOMMITTED_CHANGES",
            )
            paused_node = next(
                node
                for node in SchedulerRepository(str(repository)).run(
                    delivery_id
                )["nodes"]
                if node["nodeId"] == node_id
            )
            self.assertEqual(paused_node["status"], "PAUSED")

            git_command(repository, "add", "paused-dirty.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Commit paused checkpoint before resume",
            )
            resumed = call_tool(
                "resume_loop",
                {"root_id": delivery_id, "node_id": node_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(resumed["status"], "READY")
            self.assertEqual(
                resumed["workspaceResume"]["state"],
                "REACQUIRED",
            )
            event_types = [
                event["eventType"]
                for event in SchedulerRepository(str(repository)).events(
                    delivery_id
                )
            ]
            self.assertIn("WORKSPACE_TURN_RELEASED", event_types)
            self.assertIn("WORKSPACE_TURN_REQUEUED", event_types)
            self.assertIn("WORKSPACE_TURN_REACQUIRED", event_types)
            rebuilt = call_tool(
                "rebuild_graph_run",
                {"root_id": delivery_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            rebuilt_node = next(
                node
                for node in rebuilt["nodes"]
                if node["nodeId"] == node_id
            )
            self.assertEqual(rebuilt["status"], "ACTIVE")
            self.assertEqual(rebuilt_node["status"], "READY")

    def test_allow_empty_commit_does_not_release_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            second, first_id, _ = _terminal_first_turn(
                repository,
                "allow-empty",
            )
            git_command(
                repository,
                "commit",
                "--allow-empty",
                "-m",
                "Empty bookkeeping commit",
            )

            waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertEqual(
                _barrier_reason(waiting),
                "NO_BUSINESS_CHANGES_SINCE_TURN_START",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

    def test_control_directory_only_commit_does_not_release_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            second, first_id, _ = _terminal_first_turn(
                repository,
                "control-only",
            )
            control_file = (
                repository / ".layered-delivery" / "control-only.txt"
            )
            control_file.parent.mkdir(exist_ok=True)
            control_file.write_text("control plane only\n", encoding="utf-8")
            git_command(
                repository,
                "add",
                "-f",
                ".layered-delivery/control-only.txt",
            )
            git_command(
                repository,
                "commit",
                "-m",
                "Commit only controller state",
            )

            waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertEqual(
                _barrier_reason(waiting),
                "NO_BUSINESS_CHANGES_SINCE_TURN_START",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

    def test_rewritten_history_does_not_release_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, base_commit = _repository(Path(temporary))
            second, first_id, start_commit = _terminal_first_turn(
                repository,
                "rewritten-history",
                preexisting_commit=True,
            )
            self.assertNotEqual(start_commit, base_commit)
            git_command(repository, "reset", "--hard", base_commit)
            (repository / "after-rewrite.txt").write_text(
                "business change on rewritten history\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "after-rewrite.txt")
            git_command(
                repository,
                "commit",
                "-m",
                "Rewrite turn history with a business change",
            )

            waiting = _select(repository, second)

            self.assertTrue(
                _is_waiting_for_workspace_commit(waiting),
                waiting,
            )
            self.assertEqual(
                _barrier_reason(waiting),
                "TURN_START_COMMIT_NOT_ANCESTOR_OF_HEAD",
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(first_id)
            )

if __name__ == "__main__":
    unittest.main()
