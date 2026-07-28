from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from queue import Empty
from unittest.mock import patch

from hdg.cli import run_cli
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task
from hdg.fs_safe import atomic_write, exclusive_file_lock
from hdg.interactions import record_interaction
from hdg.planning import (
    freeze_hierarchy,
    prepare_hierarchy,
    refresh_work_item_projections,
)
from hdg.repository import GovernanceRepository, timestamp

from .fixtures import hierarchy_definition, task_definition, task_hierarchy


def _acquire_projection_lock_in_child(
    lock_path: str,
    events: multiprocessing.Queue,
) -> None:
    events.put("waiting")
    with exclusive_file_lock(lock_path, timeout_seconds=5.0):
        events.put("acquired")


class RuntimePerformanceTests(unittest.TestCase):
    @staticmethod
    def _prepare_claimed(root: str) -> dict[str, object]:
        prepared = prepare_hierarchy(
            root=root,
            hierarchy=task_hierarchy(),
            host_runtime="codex",
        )
        freeze_hierarchy(
            root=root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
            development_mode="active",
            confirmed=True,
        )
        dispatch_task(
            root=root,
            item_id=prepared["rootId"],
            owner="developer",
            operation_id="op-performance",
        )
        return prepared

    def test_projection_refresh_runs_after_the_sqlite_transaction_is_closed(self) -> None:
        observed_connection_states: list[bool] = []
        original = GovernanceRepository.refresh_markdown_projections

        def observe_refresh(
            repository: GovernanceRepository,
            registry: dict[str, object],
        ) -> None:
            observed_connection_states.append(repository._connection is None)
            original(repository, registry)

        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(
                GovernanceRepository,
                "refresh_markdown_projections",
                new=observe_refresh,
            ):
                prepare_hierarchy(
                    root=temporary,
                    hierarchy=task_hierarchy(),
                    host_runtime="codex",
                )

        self.assertTrue(observed_connection_states)
        self.assertTrue(all(observed_connection_states))

    def test_identical_atomic_write_skips_a_second_fsync_and_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary, "projection.md")
            with patch("hdg.fs_safe.os.fsync", wraps=__import__("os").fsync) as fsync:
                atomic_write(target, "stable projection\n")
                first_identity = target.stat().st_ino, target.stat().st_mtime_ns
                atomic_write(target, "stable projection\n")
                second_identity = target.stat().st_ino, target.stat().st_mtime_ns

        self.assertEqual(fsync.call_count, 1)
        self.assertEqual(second_identity, first_identity)

    def test_heartbeat_uses_incremental_projection_and_reports_timing_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = self._prepare_claimed(temporary)
            stdout = StringIO()
            stderr = StringIO()

            exit_code = run_cli(
                [
                    "heartbeat-task",
                    "--item",
                    str(prepared["rootId"]),
                    "--operation",
                    "op-performance",
                    "--timing",
                ],
                cwd=temporary,
                stdout=stdout,
                stderr=stderr,
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["id"], prepared["rootId"])
            timing_lines = [
                line.removeprefix("HDG_TIMING ")
                for line in stderr.getvalue().splitlines()
                if line.startswith("HDG_TIMING ")
            ]
            self.assertEqual(len(timing_lines), 1)
            timing = json.loads(timing_lines[0])
            self.assertEqual(timing["command"], "heartbeat-task")
            self.assertTrue(timing["ok"])
            self.assertEqual(timing["metrics"]["projectionMode"], "heartbeat")
            self.assertEqual(timing["metrics"]["registryRowsUpdated"], 1)
            stage_names = {stage["name"] for stage in timing["stages"]}
            self.assertIn("sqlite.lockWait", stage_names)
            self.assertIn("sqlite.commit", stage_names)
            self.assertIn("projection.heartbeat", stage_names)
            self.assertIn("command.execute", stage_names)
            self.assertGreaterEqual(timing["totalMs"], 0)

    def test_projection_retries_with_the_latest_revision_after_a_concurrent_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            original = GovernanceRepository.refresh_markdown_projections
            observed_revisions: list[int] = []
            injected = False

            def inject_newer_revision(
                repository: GovernanceRepository,
                registry: dict[str, object],
            ) -> None:
                nonlocal injected
                observed_revisions.append(int(registry["revision"]))
                if not injected:
                    injected = True
                    concurrent = GovernanceRepository(temporary)
                    with concurrent.transaction() as newer_registry:
                        newer_registry["revision"] += 1
                        newer_registry["updatedAt"] = timestamp(None)
                        concurrent.write_registry(newer_registry)
                original(repository, registry)

            with patch.object(
                GovernanceRepository,
                "refresh_markdown_projections",
                new=inject_newer_revision,
            ):
                refresh_work_item_projections(root=temporary)

            current_revision = GovernanceRepository(
                temporary
            ).read_operational_registry()["revision"]
            self.assertEqual(observed_revisions[-1], current_revision)
            self.assertGreaterEqual(observed_revisions.count(current_revision), 2)

    def test_concurrent_projection_refreshes_are_serialized(self) -> None:
        active_refreshes = 0
        maximum_active_refreshes = 0
        counter_lock = threading.Lock()

        def observe_refresh(
            repository: GovernanceRepository,
            registry: dict[str, object],
        ) -> None:
            nonlocal active_refreshes, maximum_active_refreshes
            with counter_lock:
                active_refreshes += 1
                maximum_active_refreshes = max(
                    maximum_active_refreshes,
                    active_refreshes,
                )
            try:
                time.sleep(0.1)
            finally:
                with counter_lock:
                    active_refreshes -= 1

        with tempfile.TemporaryDirectory() as temporary:
            definitions = []
            for index in range(2):
                definition = task_definition(
                    id=f"t-projection-lock-{index}",
                    title=f"Projection lock {index}",
                    scope=[
                        f"src/projection-lock-{index}.py",
                        f"tests/test_projection_lock_{index}.py",
                    ],
                    testCommands=[[
                        "python",
                        "-m",
                        "unittest",
                        f"tests.test_projection_lock_{index}",
                    ]],
                )
                definition["developmentPlan"]["fileChanges"] = [
                    {
                        "path": f"src/projection-lock-{index}.py",
                        "action": "ADD",
                        "purpose": f"Projection lock source {index}.",
                    },
                    {
                        "path": f"tests/test_projection_lock_{index}.py",
                        "action": "ADD",
                        "purpose": f"Projection lock test {index}.",
                    },
                ]
                definition["developmentPlan"]["interfaces"][0][
                    "location"
                ] = f"src/projection-lock-{index}.py"
                definitions.append(hierarchy_definition(definition))

            with (
                patch.object(
                    GovernanceRepository,
                    "refresh_registry_projections",
                    new=observe_refresh,
                ),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                results = list(executor.map(
                    lambda hierarchy: prepare_hierarchy(
                        root=temporary,
                        hierarchy=hierarchy,
                        host_runtime="codex",
                    ),
                    definitions,
                ))

        self.assertEqual(len(results), 2)
        self.assertEqual(maximum_active_refreshes, 1)

    def test_projection_lock_serializes_separate_processes(self) -> None:
        context = multiprocessing.get_context("spawn")
        events = context.Queue()
        process: multiprocessing.Process | None = None
        try:
            with tempfile.TemporaryDirectory() as temporary:
                lock_path = str(Path(temporary, "projection.lock"))
                with exclusive_file_lock(lock_path):
                    process = context.Process(
                        target=_acquire_projection_lock_in_child,
                        args=(lock_path, events),
                    )
                    process.start()
                    self.assertEqual(events.get(timeout=5), "waiting")
                    with self.assertRaises(Empty):
                        events.get(timeout=0.2)
                self.assertEqual(events.get(timeout=5), "acquired")
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
        finally:
            if process is not None and process.is_alive():
                process.terminate()
                process.join(timeout=5)
            events.close()
            events.join_thread()

    def test_projection_lock_timeout_includes_local_thread_wait(
        self,
    ) -> None:
        entered = threading.Event()
        release = threading.Event()

        def hold_lock(lock_path: Path) -> None:
            with exclusive_file_lock(lock_path):
                entered.set()
                release.wait(timeout=1.0)

        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary, "projection.lock")
            holder = threading.Thread(target=hold_lock, args=(lock_path,))
            holder.start()
            self.assertTrue(entered.wait(timeout=1.0))
            try:
                with self.assertRaises(GatedLoopError) as raised:
                    with exclusive_file_lock(
                        lock_path,
                        timeout_seconds=0.05,
                    ):
                        self.fail("contended lock should not be acquired")
                self.assertEqual(
                    raised.exception.code,
                    "FILESYSTEM_LOCK_TIMEOUT",
                )
            finally:
                release.set()
                holder.join(timeout=1.0)
            self.assertFalse(holder.is_alive())

    def test_interaction_projection_gets_a_unique_registry_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            repository = GovernanceRepository(temporary)
            before = repository.read_operational_registry()["revision"]

            event = record_interaction(
                root=temporary,
                item_id=str(prepared["rootId"]),
                interaction={
                    "schemaVersion": 3,
                    "sessionId": "session-performance",
                    "actor": "AGENT",
                    "eventType": "STATUS",
                    "summary": "Record an ordered projection event.",
                    "operationId": None,
                    "hostRuntime": "codex",
                },
            )

            after = repository.read_operational_registry()["revision"]
            self.assertEqual(after, before + 1)
            self.assertEqual(event["registryRevision"], after)


if __name__ == "__main__":
    unittest.main()
