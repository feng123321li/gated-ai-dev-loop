from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from hdg.errors import GatedLoopError
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository

from .test_loop_architecture import task_hierarchy


def _hierarchy(delivery_id: str) -> dict:
    hierarchy = task_hierarchy()
    hierarchy["delivery"]["id"] = delivery_id
    hierarchy["delivery"]["title"] = f"Deliver {delivery_id}"
    definition = hierarchy["root"]["definition"]
    definition["id"] = f"t-{delivery_id}"
    definition["title"] = f"Run {delivery_id}"
    return hierarchy


def _prepare(root: Path, workspace: Path, delivery_id: str) -> dict:
    return call_tool(
        "prepare_hierarchy",
        {"hierarchy": _hierarchy(delivery_id)},
        root=str(root),
        workspace_root=str(workspace),
    )


def _freeze(root: Path, prepared: dict) -> dict:
    return SchedulerRepository(str(root)).freeze(
        prepared["rootId"],
        expected_delivery_revision=prepared["deliveryRevision"],
        expected_hierarchy_fingerprint=prepared[
            "hierarchyFingerprint"
        ],
        authorized_project_ids=[],
        confirmed_by="test-user",
    )


def _cancel_and_release(
    root: Path,
    workspace: Path,
    prepared: dict,
) -> None:
    call_tool(
        "cancel_graph_run",
        {
            "root_id": prepared["rootId"],
            "cancelled_by": "test-user",
            "reason": "Release the serial workspace turn in the fixture.",
        },
        root=str(root),
        workspace_root=str(workspace),
    )
    SchedulerRepository(str(root)).release_serial_workspace_turn(
        prepared["rootId"],
        evidence={"reason": "TEST_EXPLICIT_RELEASE"},
    )


class SerialWorkspaceOwnerGateTests(unittest.TestCase):
    def test_freeze_requires_queue_owner_until_explicit_release(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            owner = _prepare(root, workspace, "d-a-owner")
            waiter = _prepare(root, workspace, "d-b-waiter")

            with self.assertRaises(GatedLoopError) as raised:
                _freeze(root, waiter)
            self.assertEqual(
                raised.exception.code,
                "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
            )
            self.assertEqual(
                raised.exception.details["ownerRootId"],
                owner["rootId"],
            )
            self.assertEqual(
                raised.exception.details["ownerStatus"],
                "PREPARED",
            )
            with self.assertRaises(GatedLoopError) as missing:
                SchedulerRepository(str(root)).run(waiter["rootId"])
            self.assertEqual(missing.exception.code, "SCHEDULER_RUN_MISSING")

            owner_run = _freeze(root, owner)
            self.assertEqual(owner_run["status"], "ACTIVE")
            _cancel_and_release(root, workspace, owner)

            waiter_run = _freeze(root, waiter)
            self.assertEqual(waiter_run["status"], "ACTIVE")

    def test_concurrent_freeze_only_starts_the_queue_owner(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            owner = _prepare(root, workspace, "d-a-concurrent-owner")
            waiter = _prepare(root, workspace, "d-b-concurrent-waiter")
            barrier = threading.Barrier(2)

            def attempt(prepared: dict) -> tuple[str, str]:
                barrier.wait(timeout=10)
                try:
                    result = _freeze(root, prepared)
                except GatedLoopError as error:
                    return prepared["rootId"], error.code
                return prepared["rootId"], result["status"]

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = dict(executor.map(attempt, (owner, waiter)))

            self.assertEqual(outcomes[owner["rootId"]], "ACTIVE")
            self.assertEqual(
                outcomes[waiter["rootId"]],
                "SCHEDULER_WORKSPACE_TURN_NOT_OWNED",
            )
            self.assertEqual(
                SchedulerRepository(str(root)).run(owner["rootId"])[
                    "status"
                ],
                "ACTIVE",
            )
            with self.assertRaises(GatedLoopError) as missing:
                SchedulerRepository(str(root)).run(waiter["rootId"])
            self.assertEqual(missing.exception.code, "SCHEDULER_RUN_MISSING")

    def test_automatic_resume_returns_waiting_after_atomic_turn_loss(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            owner = _prepare(root, workspace, "d-auto-owner")
            _freeze(root, owner)
            waiter = _prepare(root, workspace, "d-auto-waiter")
            repository = SchedulerRepository(str(root))
            stored = repository.hierarchy(waiter["rootId"])
            repository.record_automatic_selection(
                waiter["rootId"],
                expected_hierarchy_fingerprint=stored[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=stored["graphFingerprint"],
                authorized_project_ids=[],
                confirmed_by="test-user",
            )

            result = call_tool(
                "resume_execution_mode",
                {
                    "root_id": waiter["rootId"],
                    "expected_hierarchy_fingerprint": stored[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": stored[
                        "graphFingerprint"
                    ],
                },
                root=str(root),
                workspace_root=str(workspace),
            )

            self.assertEqual(result["status"], "QUEUED")
            self.assertEqual(result["deliveryQueue"]["state"], "QUEUED")
            self.assertEqual(result["deliveryStatus"], "PREPARED")
            self.assertFalse(result["automaticDispatchRequested"])
            self.assertEqual(
                result["workspaceTurn"]["ownerRootId"],
                owner["rootId"],
            )
            with self.assertRaises(GatedLoopError) as missing:
                repository.run(waiter["rootId"])
            self.assertEqual(missing.exception.code, "SCHEDULER_RUN_MISSING")

    def test_manual_start_returns_waiting_after_atomic_turn_loss(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            owner = _prepare(root, workspace, "d-manual-owner")
            _freeze(root, owner)
            hierarchy = _hierarchy("d-manual-waiter")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(root),
                workspace_root=str(workspace),
            )
            handoff = call_tool(
                "create_manual_handoff",
                {
                    "hierarchy": hierarchy,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "test-user",
                },
                root=str(root),
                workspace_root=str(workspace),
            )

            result = call_tool(
                "start_manual_handoff",
                {
                    "root_id": handoff["rootId"],
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "started_by": "manual-test-agent",
                },
                root=str(root),
                workspace_root=str(workspace),
            )

            self.assertEqual(result["status"], "WAITING_FOR_WORKSPACE_TURN")
            self.assertEqual(result["manualStartState"], result["status"])
            self.assertFalse(result["graphRunCreated"])
            self.assertEqual(
                result["workspaceTurn"]["ownerRootId"],
                owner["rootId"],
            )
            with self.assertRaises(GatedLoopError) as missing:
                SchedulerRepository(str(root)).run(handoff["rootId"])
            self.assertEqual(missing.exception.code, "SCHEDULER_RUN_MISSING")

    def test_recorded_unbound_choice_recovers_into_serial_queue(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            hierarchy = _hierarchy("d-recorded-unbound")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(root),
                workspace_root=str(workspace),
            )
            repository = SchedulerRepository(str(root))
            repository.record_automatic_selection(
                preview["rootId"],
                expected_hierarchy_fingerprint=preview[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=preview["graphFingerprint"],
                authorized_project_ids=[],
                confirmed_by="test-user",
            )
            with self.assertRaises(GatedLoopError) as missing:
                repository.workspace_binding(preview["rootId"])
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
            )

            status = call_tool(
                "workspace_status",
                {"root_id": preview["rootId"]},
                root=str(root),
                workspace_root=str(workspace),
            )

            self.assertEqual(status["status"], "CHOICE_READY")
            self.assertEqual(
                status["workspaceTurn"]["state"],
                "ACQUIRED",
            )
            self.assertEqual(
                repository.workspace_binding(preview["rootId"])["workspaceKey"],
                SchedulerRepository.workspace_key(workspace),
            )


    def test_changed_choice_releases_unstarted_queue_binding(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            hierarchy = _hierarchy("d-changed-choice")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(root),
                workspace_root=str(workspace),
            )
            repository = SchedulerRepository(str(root))
            repository.record_automatic_selection(
                preview["rootId"],
                expected_hierarchy_fingerprint=preview[
                    "hierarchyFingerprint"
                ],
                expected_graph_fingerprint=preview["graphFingerprint"],
                authorized_project_ids=[],
                confirmed_by="test-user",
                workspace_key=SchedulerRepository.workspace_key(workspace),
            )
            self.assertEqual(
                repository.workspace_binding(preview["rootId"])[
                    "workspaceKey"
                ],
                SchedulerRepository.workspace_key(workspace),
            )

            hierarchy["delivery"]["title"] = "Changed requirement"
            changed = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(root),
                workspace_root=str(workspace),
            )

            self.assertEqual(changed["status"], "CHOICE_READY")
            self.assertIsNone(
                repository.execution_selection(preview["rootId"])
            )
            with self.assertRaises(GatedLoopError) as missing:
                repository.workspace_binding(preview["rootId"])
            self.assertEqual(
                missing.exception.code,
                "SCHEDULER_DELIVERY_WORKSPACE_MISSING",
            )

if __name__ == "__main__":
    unittest.main()
