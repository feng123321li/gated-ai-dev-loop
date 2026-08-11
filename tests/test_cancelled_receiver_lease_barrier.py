from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hdg import planning
from hdg.repository import SchedulerRepository, timestamp


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


def _seed_terminal_run(
    repository: SchedulerRepository,
    *,
    root_id: str,
    run_status: str,
    lease_expires_at: datetime,
    node_status: str = "CANCELLED",
    ending_event: str | None = None,
) -> str:
    run_id = f"run-{root_id}"
    node_id = f"loop-{root_id}"
    operation_id = f"operation-{root_id}"
    claimed_at = timestamp(NOW - timedelta(minutes=2))
    terminal_at = timestamp(NOW - timedelta(minutes=1))
    terminal_event = {
        "CANCELLED": "GRAPH_RUN_CANCELLED",
        "SUPERSEDED": "GRAPH_RUN_SUPERSEDED",
        "COMPLETED": "GRAPH_RUN_COMPLETED",
    }[run_status]
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO hierarchies("
            "root_id, revision, hierarchy_fingerprint, graph_fingerprint, "
            "hierarchy_json, graph_json, status, created_at, updated_at"
            ") VALUES (?, 1, ?, ?, ?, ?, 'FROZEN', ?, ?)",
            (
                root_id,
                f"hierarchy-{root_id}",
                f"graph-{root_id}",
                json.dumps({"delivery": {"id": root_id}}),
                json.dumps({"rootId": root_id}),
                claimed_at,
                terminal_at,
            ),
        )
        connection.execute(
            "INSERT INTO runs("
            "run_id, root_id, revision, execution_mode, status, "
            "started_at, updated_at, completed_at, cancelled_at, "
            "superseded_at"
            ") VALUES (?, ?, 1, 'active', ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                root_id,
                run_status,
                claimed_at,
                terminal_at,
                terminal_at if run_status == "COMPLETED" else None,
                terminal_at if run_status == "CANCELLED" else None,
                terminal_at if run_status == "SUPERSEDED" else None,
            ),
        )
        connection.execute(
            "INSERT INTO node_runs("
            "run_id, node_id, attempt, status, owner, operation_id, "
            "claimed_at, last_heartbeat_at, lease_expires_at, "
            "finished_at, outcome_json"
            ") VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                node_id,
                node_status,
                f"owner-{root_id}",
                operation_id,
                claimed_at,
                claimed_at,
                timestamp(lease_expires_at),
                (
                    terminal_at
                    if node_status in {"CANCELLED", "SUCCEEDED"}
                    else None
                ),
                (
                    json.dumps(
                        {
                            "status": "SUCCEEDED",
                            "summary": "Already completed.",
                            "result": {},
                        }
                    )
                    if node_status == "SUCCEEDED"
                    else None
                ),
            ),
        )
        repository.append_event(
            connection,
            run_id=run_id,
            node_id=node_id,
            attempt=1,
            event_type="LOOP_CLAIMED",
            actor=f"owner-{root_id}",
            operation_id=operation_id,
            payload={
                "receiverContextId": f"receiver-{root_id}",
                "leaseExpiresAt": timestamp(lease_expires_at),
            },
            at=claimed_at,
        )
        if ending_event is not None:
            repository.append_event(
                connection,
                run_id=run_id,
                node_id=node_id,
                attempt=1,
                event_type=ending_event,
                actor=f"owner-{root_id}",
                operation_id=operation_id,
                payload={
                    "outcome": {
                        "status": "SUCCEEDED",
                        "summary": "Already completed.",
                        "result": {},
                    }
                },
                at=timestamp(NOW - timedelta(seconds=90)),
            )
        repository.append_event(
            connection,
            run_id=run_id,
            node_id=None,
            attempt=None,
            event_type=terminal_event,
            actor="CONTROLLER",
            operation_id=None,
            payload={"reason": "test terminal event"},
            at=terminal_at,
        )
    return run_id


class CancelledReceiverLeaseBarrierTests(unittest.TestCase):
    def test_cancelled_claim_blocks_until_repository_clock_expires_lease(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            active_repository = SchedulerRepository(
                str(root),
                now=lambda: NOW,
            )
            run_id = _seed_terminal_run(
                active_repository,
                root_id="d-cancelled-active",
                run_status="CANCELLED",
                lease_expires_at=NOW + timedelta(minutes=5),
            )

            leases = (
                active_repository.unexpired_cancelled_receiver_leases(
                    "d-cancelled-active"
                )
            )

            self.assertEqual(
                leases,
                [
                    {
                        "rootId": "d-cancelled-active",
                        "runId": run_id,
                        "revision": 1,
                        "runStatus": "CANCELLED",
                        "nodeId": "loop-d-cancelled-active",
                        "attempt": 1,
                        "owner": "owner-d-cancelled-active",
                        "receiverContextId": (
                            "receiver-d-cancelled-active"
                        ),
                        "operationId": (
                            "operation-d-cancelled-active"
                        ),
                        "claimedAt": timestamp(
                            NOW - timedelta(minutes=2)
                        ),
                        "lastHeartbeatAt": timestamp(
                            NOW - timedelta(minutes=2)
                        ),
                        "leaseExpiresAt": timestamp(
                            NOW + timedelta(minutes=5)
                        ),
                    }
                ],
            )

            expired_repository = SchedulerRepository(
                str(root),
                now=lambda: NOW + timedelta(minutes=6),
            )
            self.assertEqual(
                expired_repository.unexpired_cancelled_receiver_leases(
                    "d-cancelled-active"
                ),
                [],
            )

    def test_superseded_active_claim_is_also_reported(self) -> None:
        with TemporaryDirectory() as temporary:
            repository = SchedulerRepository(
                temporary,
                now=lambda: NOW,
            )
            _seed_terminal_run(
                repository,
                root_id="d-superseded-active",
                run_status="SUPERSEDED",
                lease_expires_at=NOW + timedelta(minutes=5),
            )

            leases = repository.unexpired_cancelled_receiver_leases(
                "d-superseded-active"
            )

            self.assertEqual(len(leases), 1)
            self.assertEqual(leases[0]["runStatus"], "SUPERSEDED")

    def test_completed_success_with_future_lease_does_not_block(self) -> None:
        with TemporaryDirectory() as temporary:
            repository = SchedulerRepository(
                temporary,
                now=lambda: NOW,
            )
            _seed_terminal_run(
                repository,
                root_id="d-completed-success",
                run_status="COMPLETED",
                lease_expires_at=NOW + timedelta(minutes=5),
                node_status="SUCCEEDED",
                ending_event="LOOP_SUCCEEDED",
            )

            self.assertEqual(
                repository.unexpired_cancelled_receiver_leases(
                    "d-completed-success"
                ),
                [],
            )

    def test_serial_release_gate_short_circuits_on_receiver_lease(
        self,
    ) -> None:
        lease = {
            "rootId": "d-cancelled-active",
            "receiverContextId": "receiver-d-cancelled-active",
            "leaseExpiresAt": timestamp(NOW + timedelta(minutes=5)),
        }

        class RepositoryDouble:
            @staticmethod
            def workspace_turn_release(_root_id: str) -> None:
                return None

            @staticmethod
            def unexpired_cancelled_receiver_leases(
                _root_id: str,
            ) -> list[dict]:
                return [lease]

            @staticmethod
            def hierarchy(_root_id: str) -> dict:
                raise AssertionError(
                    "Git commit inspection must wait for receiver release"
                )

        result = planning._serial_commit_barrier(
            RepositoryDouble(),
            "unused-workspace",
            {"rootId": "d-cancelled-active", "status": "CANCELLED"},
        )

        self.assertEqual(result["reason"], "CANCELLED_RECEIVER_LEASE_ACTIVE")
        self.assertEqual(result["receiverLeases"], [lease])


if __name__ == "__main__":
    unittest.main()
