from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg.mcp_tools import call_tool
from hdg.planning import freeze_hierarchy, prepare_delivery_revision
from hdg.repository import SchedulerRepository

from .test_scheduler_contracts import git_command
from .test_workspace_execution_strategy import (
    _confirm_existing_branch,
    _repository,
    _select,
    loop_node_id,
    reserve_loop,
)


def _claim_task(
    repository: Path,
    *,
    delivery_id: str,
    task_id: str,
) -> tuple[str, str]:
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
    operation_id = f"operation-{task_id}"
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
            "owner": f"receiver-{task_id}",
            "agent_id": "codex",
            "receiver_context_id": f"context-{task_id}",
            "operation_id": operation_id,
            "dispatch_mode": reservation["dispatchMode"],
            "dispatch_transport": reservation["dispatchTransport"],
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
    return node_id, operation_id


class SerialWorkspacePauseRecoveryTests(unittest.TestCase):
    def test_paused_without_changes_resumes_in_place_across_adapters(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-paused-retained-turn"
            node_id, operation_id = _claim_task(
                repository,
                delivery_id=delivery_id,
                task_id="t-paused-retained-turn",
            )

            paused = call_tool(
                "pause_loop",
                {
                    "root_id": delivery_id,
                    "node_id": node_id,
                    "operation_id": operation_id,
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(paused["status"], "PAUSED")
            self.assertEqual(paused["workspaceRelease"]["state"], "PENDING")
            self.assertEqual(
                paused["workspaceRelease"]["reason"],
                "HEAD_EQUALS_TURN_START_COMMIT",
            )

            resumed = call_tool(
                "resume_loop",
                {"root_id": delivery_id, "node_id": node_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="zcode",
            )

            self.assertEqual(resumed["status"], "READY")
            self.assertEqual(resumed["workspaceResume"]["state"], "RETAINED")
            self.assertEqual(resumed["workspaceTurn"]["state"], "ACQUIRED")
            self.assertEqual(
                resumed["workspaceTurn"]["ownerRootId"],
                delivery_id,
            )
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )
            event_types = [
                event["eventType"]
                for event in SchedulerRepository(str(repository)).events(
                    delivery_id
                )
            ]
            self.assertNotIn("WORKSPACE_TURN_RELEASED", event_types)
            self.assertNotIn("WORKSPACE_TURN_REQUEUED", event_types)
            self.assertNotIn("WORKSPACE_TURN_REACQUIRED", event_types)

    def test_paused_dirty_checkpoint_resumes_in_place(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-paused-dirty"
            node_id, operation_id = _claim_task(
                repository,
                delivery_id=delivery_id,
                task_id="t-paused-dirty",
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
                    "operation_id": operation_id,
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

            resumed = call_tool(
                "resume_loop",
                {"root_id": delivery_id, "node_id": node_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(resumed["status"], "READY")
            self.assertEqual(resumed["workspaceResume"]["state"], "RETAINED")
            resumed_node = next(
                node
                for node in SchedulerRepository(str(repository)).run(
                    delivery_id
                )["nodes"]
                if node["nodeId"] == node_id
            )
            self.assertEqual(resumed_node["status"], "READY")
            self.assertIsNone(
                SchedulerRepository(
                    str(repository)
                ).workspace_turn_release(delivery_id)
            )

    def test_cancelled_without_changes_releases_clean_turn(self) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-cancelled-unchanged"
            task_id = "t-cancelled-unchanged"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)

            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": delivery_id,
                    "cancelled_by": "human",
                    "reason": "Cancel before any business change.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertEqual(
                cancelled["workspaceRelease"]["state"],
                "RELEASED",
            )
            release = SchedulerRepository(
                str(repository)
            ).workspace_turn_release(delivery_id)
            self.assertIsNotNone(release)
            self.assertEqual(release["releaseReason"], "RUN_TERMINAL")
            self.assertEqual(len(release["projects"]), 1)
            self.assertTrue(release["projects"][0]["unchangedSinceTurnStart"])
            self.assertEqual(
                release["projects"][0]["businessChangedFiles"],
                [],
            )

    def test_cancelled_released_automatic_delivery_starts_next_revision(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            repository, _ = _repository(Path(temporary))
            delivery_id = "d-cancelled-next-revision"
            task_id = "t-cancelled-next-revision"
            branch_ref = f"feature/{delivery_id}"
            git_command(repository, "switch", "-c", branch_ref)
            confirmed = _confirm_existing_branch(
                repository,
                delivery_id,
                task_id,
                branch_ref,
            )
            _select(repository, confirmed)
            cancelled = call_tool(
                "cancel_graph_run",
                {
                    "root_id": delivery_id,
                    "cancelled_by": "human",
                    "reason": "Move the unfinished goal to Revision 2.",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(cancelled["workspaceRelease"]["state"], "RELEASED")
            status = call_tool(
                "workspace_status",
                {"root_id": delivery_id},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertTrue(status["canPrepareRevision"], status)
            self.assertEqual(
                status["nextAction"],
                "PREPARE_DELIVERY_REVISION",
            )

            route = call_tool(
                "route_entry_intent",
                {
                    "root_id": delivery_id,
                    "request_text": "继续这个交付",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertTrue(route["allowed"], route)
            self.assertEqual(route["intent"], "CONTINUE_DELIVERY")
            self.assertEqual(route["targetSkill"], "delivery-graph")
            revised = deepcopy(
                SchedulerRepository(str(repository)).hierarchy(delivery_id)[
                    "hierarchy"
                ]
            )
            revised["root"]["definition"]["summary"] = (
                "Continue the cancelled automatic Delivery in Revision 2."
            )
            candidate = prepare_delivery_revision(
                root=str(repository),
                root_id=delivery_id,
                expected_current_revision=1,
                hierarchy=revised,
                reason="Continue the same open Delivery after cancellation.",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="human",
                workspace_root=str(repository),
            )
            frozen = freeze_hierarchy(
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

            self.assertEqual(frozen["deliveryRevision"], 2)
            self.assertEqual(frozen["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
