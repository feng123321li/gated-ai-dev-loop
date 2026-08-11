from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.graph_model import compile_delivery_graph, graph_fingerprint
from hdg.graph_runtime import cancel_graph_run
from hdg.jsonio import canonical_json
from hdg.model_core import (
    hierarchy_fingerprint,
    validate_hierarchy_definition,
)
from hdg.repository import SchedulerRepository

from .test_loop_architecture import task_hierarchy


def _hierarchy(
    delivery_id: str,
    coordinator: Path,
    secondary: Path,
) -> dict:
    hierarchy = task_hierarchy()
    hierarchy["delivery"]["id"] = delivery_id
    hierarchy["delivery"]["title"] = f"Deliver {delivery_id}"
    hierarchy["delivery"]["projectScopes"] = [
        {
            "id": "coordinator",
            "workspaceRoot": str(coordinator),
            "access": "READ_WRITE",
        },
        {
            "id": "secondary",
            "workspaceRoot": str(secondary),
            "access": "READ_WRITE",
        },
    ]
    definition = hierarchy["root"]["definition"]
    definition["id"] = f"t-{delivery_id}"
    definition["title"] = f"Implement {delivery_id}"
    return hierarchy


def _prepare(
    repository: SchedulerRepository,
    coordinator: Path,
    secondary: Path,
    delivery_id: str,
) -> dict:
    hierarchy = validate_hierarchy_definition(
        _hierarchy(delivery_id, coordinator, secondary)
    )
    hierarchy_value = hierarchy_fingerprint(hierarchy)
    graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_value,
    )
    return repository.prepare(
        hierarchy,
        graph,
        hierarchy_fingerprint=hierarchy_value,
        graph_fingerprint=graph_fingerprint(graph),
        workspace_root=coordinator,
    )


def _turn_start(
    coordinator: Path,
    secondary: Path,
) -> dict:
    return {
        "schemaVersion": 1,
        "strategy": "CURRENT_WORKSPACE_SERIAL",
        "projects": [
            {
                "projectId": "coordinator",
                "workspaceKey": SchedulerRepository.workspace_key(
                    coordinator
                ),
            },
            {
                "projectId": "secondary",
                "workspaceKey": SchedulerRepository.workspace_key(secondary),
            },
        ],
    }


def _freeze(
    repository: SchedulerRepository,
    prepared: dict,
    turn_start: dict,
) -> dict:
    return repository.freeze(
        prepared["rootId"],
        expected_delivery_revision=prepared["deliveryRevision"],
        expected_hierarchy_fingerprint=prepared[
            "hierarchyFingerprint"
        ],
        authorized_project_ids=["coordinator", "secondary"],
        confirmed_by="test-user",
        workspace_turn_start=turn_start,
    )


class SecondaryWorkspaceOwnerGateTests(unittest.TestCase):
    def test_distinct_coordinators_cannot_share_unreleased_secondary(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator_one = root / "coordinator-one"
            coordinator_two = root / "coordinator-two"
            shared_secondary = root / "shared-secondary"
            for workspace in (
                coordinator_one,
                coordinator_two,
                shared_secondary,
            ):
                workspace.mkdir()
            repository = SchedulerRepository(str(root))
            owner = _prepare(
                repository,
                coordinator_one,
                shared_secondary,
                "d-secondary-owner",
            )
            waiter = _prepare(
                repository,
                coordinator_two,
                shared_secondary,
                "d-secondary-waiter",
            )
            owner_turn_start = _turn_start(
                coordinator_one,
                shared_secondary,
            )
            waiter_turn_start = _turn_start(
                coordinator_two,
                shared_secondary,
            )
            _freeze(repository, owner, owner_turn_start)

            with self.assertRaises(GatedLoopError) as caught:
                _freeze(repository, waiter, waiter_turn_start)

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
            )
            self.assertEqual(
                caught.exception.details["ownerRootId"],
                owner["rootId"],
            )
            self.assertEqual(
                caught.exception.details["conflictingWorkspaceKeys"],
                [
                    SchedulerRepository.workspace_key(
                        shared_secondary
                    )
                ],
            )
            self.assertEqual(
                caught.exception.details["workspaceScope"],
                "READ_WRITE_PROJECT_CHECKOUTS",
            )
            with self.assertRaises(GatedLoopError) as missing:
                repository.run(waiter["rootId"])
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_RUN_MISSING",
            )

            cancel_graph_run(
                root=str(root),
                root_id=owner["rootId"],
                cancelled_by="test-user",
                reason="Release the owner for the next serial turn.",
            )
            repository.release_serial_workspace_turn(
                owner["rootId"],
                evidence={"projects": []},
            )

            frozen = _freeze(repository, waiter, waiter_turn_start)
            self.assertEqual(frozen["status"], "ACTIVE")

    def test_disjoint_secondary_checkouts_can_freeze_independently(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator_one = root / "coordinator-one"
            coordinator_two = root / "coordinator-two"
            secondary_one = root / "secondary-one"
            secondary_two = root / "secondary-two"
            for workspace in (
                coordinator_one,
                coordinator_two,
                secondary_one,
                secondary_two,
            ):
                workspace.mkdir()
            repository = SchedulerRepository(str(root))
            first = _prepare(
                repository,
                coordinator_one,
                secondary_one,
                "d-disjoint-first",
            )
            second = _prepare(
                repository,
                coordinator_two,
                secondary_two,
                "d-disjoint-second",
            )

            first_run = _freeze(
                repository,
                first,
                _turn_start(coordinator_one, secondary_one),
            )
            second_run = _freeze(
                repository,
                second,
                _turn_start(coordinator_two, secondary_two),
            )

            self.assertEqual(first_run["status"], "ACTIVE")
            self.assertEqual(second_run["status"], "ACTIVE")

    def test_superseded_unreleased_revision_does_not_retain_secondary(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator_one = root / "coordinator-one"
            coordinator_two = root / "coordinator-two"
            shared_secondary = root / "shared-secondary"
            for workspace in (
                coordinator_one,
                coordinator_two,
                shared_secondary,
            ):
                workspace.mkdir()
            repository = SchedulerRepository(str(root))
            owner = _prepare(
                repository,
                coordinator_one,
                shared_secondary,
                "d-revised-owner",
            )
            _freeze(
                repository,
                owner,
                _turn_start(coordinator_one, shared_secondary),
            )

            revised_hierarchy = validate_hierarchy_definition(
                _hierarchy(
                    "d-revised-owner",
                    coordinator_one,
                    shared_secondary,
                )
            )
            revised_hierarchy["root"]["definition"]["title"] = (
                "Implement revised d-revised-owner"
            )
            revised_hierarchy = validate_hierarchy_definition(
                revised_hierarchy
            )
            revised_hierarchy_value = hierarchy_fingerprint(
                revised_hierarchy
            )
            revised_graph = compile_delivery_graph(
                revised_hierarchy,
                hierarchy_fingerprint=revised_hierarchy_value,
            )
            revision = repository.prepare_revision(
                revised_hierarchy,
                revised_graph,
                root_id=owner["rootId"],
                expected_current_revision=1,
                hierarchy_fingerprint=revised_hierarchy_value,
                graph_fingerprint=graph_fingerprint(revised_graph),
                reason="Revise the active Delivery.",
                continuity_basis="USER_EXPLICIT_SAME_DELIVERY",
                requested_by="test-user",
                workspace_root=coordinator_one,
            )
            _freeze(
                repository,
                revision,
                _turn_start(coordinator_one, shared_secondary),
            )
            cancel_graph_run(
                root=str(root),
                root_id=owner["rootId"],
                cancelled_by="test-user",
                reason="Release the current revision.",
            )
            repository.release_serial_workspace_turn(
                owner["rootId"],
                evidence={"projects": []},
            )

            connection = sqlite3.connect(repository.database_path)
            try:
                event_counts = connection.execute(
                    "SELECT r.revision, "
                    "SUM(CASE WHEN e.event_type = 'GRAPH_RUN_STARTED' "
                    "THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN e.event_type = "
                    "'WORKSPACE_TURN_RELEASED' THEN 1 ELSE 0 END) "
                    "FROM runs r "
                    "JOIN graph_events e ON e.run_id = r.run_id "
                    "WHERE r.root_id = ? "
                    "GROUP BY r.revision ORDER BY r.revision",
                    (owner["rootId"],),
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(event_counts, [(1, 1, 0), (2, 1, 1)])

            waiter = _prepare(
                repository,
                coordinator_two,
                shared_secondary,
                "d-after-revision",
            )
            frozen = _freeze(
                repository,
                waiter,
                _turn_start(coordinator_two, shared_secondary),
            )
            self.assertEqual(frozen["status"], "ACTIVE")

    def test_frozen_idempotency_preserves_first_started_owner(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator_one = root / "coordinator-one"
            coordinator_two = root / "coordinator-two"
            secondary_one = root / "secondary-one"
            secondary_two = root / "secondary-two"
            for workspace in (
                coordinator_one,
                coordinator_two,
                secondary_one,
                secondary_two,
            ):
                workspace.mkdir()
            repository = SchedulerRepository(str(root))
            first = _prepare(
                repository,
                coordinator_one,
                secondary_one,
                "d-idempotent-first",
            )
            second = _prepare(
                repository,
                coordinator_two,
                secondary_two,
                "d-idempotent-second",
            )
            first_start = _turn_start(coordinator_one, secondary_one)
            second_start = _turn_start(coordinator_two, secondary_two)
            _freeze(repository, first, first_start)
            _freeze(repository, second, second_start)

            connection = sqlite3.connect(repository.database_path)
            try:
                row = connection.execute(
                    "SELECT e.event_id, e.payload_json "
                    "FROM graph_events e "
                    "JOIN runs r ON r.run_id = e.run_id "
                    "WHERE r.root_id = ? "
                    "AND e.event_type = 'GRAPH_RUN_STARTED'",
                    (second["rootId"],),
                ).fetchone()
                self.assertIsNotNone(row)
                payload = json.loads(row[1])
                payload["workspaceTurnStart"] = _turn_start(
                    coordinator_two,
                    secondary_one,
                )
                connection.execute(
                    "UPDATE graph_events SET payload_json = ? "
                    "WHERE event_id = ?",
                    (canonical_json(payload), row[0]),
                )
                connection.commit()
            finally:
                connection.close()

            owner_result = _freeze(repository, first, first_start)
            self.assertEqual(owner_result["rootId"], first["rootId"])
            with self.assertRaises(GatedLoopError) as caught:
                _freeze(repository, second, second_start)
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
            )
            self.assertEqual(
                caught.exception.details["ownerRootId"],
                first["rootId"],
            )
            self.assertEqual(
                caught.exception.details["conflictingWorkspaceKeys"],
                [SchedulerRepository.workspace_key(secondary_one)],
            )


if __name__ == "__main__":
    unittest.main()
