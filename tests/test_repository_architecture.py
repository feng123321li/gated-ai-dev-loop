from __future__ import annotations

import inspect
from pathlib import Path
import unittest

from hdg.repository import SchedulerRepository
from hdg.repository_dispatch import DeliveryDispatchStore
from hdg.repository_events import DeliveryEventStore
from hdg.repository_execution_setup import DeliveryExecutionSetupStore
from hdg.repository_hierarchies import DeliveryHierarchyStore
from hdg.repository_projections import DeliveryProjectionStore


class RepositoryArchitectureTests(unittest.TestCase):
    def test_p0_runtime_module_families_stay_below_1000_lines(self) -> None:
        source_root = Path(__file__).parents[1] / "src" / "hdg"
        module_patterns = (
            "graph_runtime*.py",
            "model_rendering*.py",
            "planning*.py",
            "repository_hierarch*.py",
        )

        paths = {
            path
            for pattern in module_patterns
            for path in source_root.glob(pattern)
        }
        self.assertTrue(paths)
        for path in sorted(paths):
            with self.subTest(path=path.name):
                self.assertLessEqual(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    1000,
                    f"{path.name} must be split by responsibility",
                )

    def test_test_modules_stay_below_1000_lines(self) -> None:
        tests_root = Path(__file__).parent
        paths = sorted(tests_root.rglob("*.py"))

        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.relative_to(tests_root).as_posix()):
                self.assertLessEqual(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    1000,
                    f"{path.name} must be split by test responsibility",
                )

    def test_dispatch_persistence_is_owned_by_a_dedicated_store(self) -> None:
        expected_methods = {
            "claimed_resource_reservations",
            "expire_dispatch_reservations",
            "active_dispatch_reservations",
            "open_host_capacity_breaker",
            "reserve_dispatch_assignments",
            "consume_dispatch_reservation",
        }

        self.assertTrue(
            expected_methods.issubset(DeliveryDispatchStore.__dict__)
        )
        self.assertTrue(
            expected_methods.issubset(SchedulerRepository.__dict__)
        )
        static_methods = {
            "expire_dispatch_reservations",
            "open_host_capacity_breaker",
        }
        for method_name in expected_methods:
            facade_source = inspect.getsource(
                SchedulerRepository.__dict__[method_name]
            )
            self.assertIn(
                (
                    "DeliveryDispatchStore"
                    if method_name in static_methods
                    else "_delivery_dispatch_store"
                ),
                facade_source,
            )
            self.assertEqual(
                str(
                    inspect.signature(
                        SchedulerRepository.__dict__[method_name]
                    )
                ),
                str(
                    inspect.signature(
                        DeliveryDispatchStore.__dict__[method_name]
                    )
                ),
            )

    def test_hook_identity_persistence_api_is_removed(self) -> None:
        receiver_methods = {
            "issue_receiver_attestation",
            "_assert_receiver_root",
            "_idle_frontier_allows_receiver_root_rotation",
            "_worker_lost_retry_allows_receiver_root_rotation",
            "issue_host_receiver_identity",
            "consume_receiver_attestation",
        }
        workspace_methods = {
            "issue_host_workspace_attestation",
            "validate_host_workspace_attestation",
            "consume_host_workspace_attestation",
        }

        for owner in (DeliveryDispatchStore, SchedulerRepository):
            with self.subTest(owner=owner.__name__):
                self.assertTrue(
                    receiver_methods.isdisjoint(owner.__dict__)
                )
        self.assertTrue(
            workspace_methods.isdisjoint(
                SchedulerRepository.__dict__
            )
        )


    def test_repository_facade_stays_below_1800_lines(self) -> None:
        repository_path = (
            Path(__file__).parents[1] / "src" / "hdg" / "repository.py"
        )

        self.assertLess(
            len(repository_path.read_text(encoding="utf-8").splitlines()),
            1800,
        )

    def test_projection_persistence_is_owned_by_a_dedicated_store(
        self,
    ) -> None:
        expected_methods = {
            "write_projections",
            "_write_workspace_overview",
            "write_workspace_overview",
            "_workspace_projection_sources",
        }

        self.assertTrue(
            expected_methods.issubset(DeliveryProjectionStore.__dict__)
        )
        for method_name in expected_methods:
            facade_method = SchedulerRepository.__dict__[method_name]
            store_method = DeliveryProjectionStore.__dict__[method_name]
            self.assertIn(
                "_delivery_projection_store",
                inspect.getsource(facade_method),
            )
            self.assertEqual(
                str(inspect.signature(facade_method)),
                str(inspect.signature(store_method)),
            )

    def test_execution_setup_is_owned_by_a_dedicated_store(self) -> None:
        expected_methods = {
            "git_branch_usage",
            "development_preference",
            "record_development_preference",
            "clear_development_preference",
            "record_choice_ready",
            "record_automatic_selection",
            "execution_selection",
        }

        self._assert_store_boundary(
            expected_methods,
            DeliveryExecutionSetupStore,
            "_delivery_execution_setup_store",
        )

    def test_hierarchy_lifecycle_is_owned_by_a_dedicated_store(self) -> None:
        expected_methods = {
            "record_manual_handoff",
            "prepare",
            "hierarchy",
            "_carriable_task_ids",
            "prepare_revision",
            "freeze",
            "freeze_manual_handoff",
            "_freeze",
            "_run_from_connection",
            "run",
            "revision_history",
            "task_requirement_states",
        }

        self._assert_store_boundary(
            expected_methods,
            DeliveryHierarchyStore,
            "_delivery_hierarchy_store",
            static_methods={
                "_carriable_task_ids",
                "task_requirement_states",
            },
        )

    def test_graph_events_are_owned_by_a_dedicated_store(self) -> None:
        expected_methods = {
            "latest_nodes",
            "_append_event",
            "append_event",
            "events",
            "refresh_ready",
        }

        self._assert_store_boundary(
            expected_methods,
            DeliveryEventStore,
            "_delivery_event_store",
            static_methods={"latest_nodes"},
        )

    def _assert_store_boundary(
        self,
        expected_methods: set[str],
        store: type,
        facade_factory: str,
        *,
        static_methods: set[str] | None = None,
    ) -> None:
        static_methods = static_methods or set()
        self.assertTrue(expected_methods.issubset(store.__dict__))
        self.assertTrue(
            expected_methods.issubset(SchedulerRepository.__dict__)
        )
        for method_name in expected_methods:
            facade_method = SchedulerRepository.__dict__[method_name]
            store_method = store.__dict__[method_name]
            self.assertIn(
                store.__name__
                if method_name in static_methods
                else facade_factory,
                inspect.getsource(facade_method),
            )
            self.assertEqual(
                str(inspect.signature(facade_method)),
                str(inspect.signature(store_method)),
            )


if __name__ == "__main__":
    unittest.main()
