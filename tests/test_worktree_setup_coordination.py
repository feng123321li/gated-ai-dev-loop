from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import threading
import unittest

from hdg import planning
from hdg.errors import GatedLoopError
from hdg.git_binding import git_repository_identity
from hdg.mcp_tools import call_tool
from hdg.repository import SchedulerRepository
from hdg.workspace_identity import legacy_path_workspace_key

from .test_scheduler_contracts import (
    bind_delivery_to_git,
    isolated_task_hierarchy,
)


def _git(workspace: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _repository(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "--initial-branch=main")
    _git(path, "config", "user.name", "Worktree Setup Tests")
    _git(
        path,
        "config",
        "user.email",
        "worktree-setup-tests@example.invalid",
    )
    (path / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "Initial baseline")
    return _git(path, "rev-parse", "HEAD")


def _preview_and_select(
    repository: Path,
    hierarchy: dict,
    *,
    authorized_project_ids: list[str] | None = None,
) -> tuple[dict, dict]:
    preview = call_tool(
        "preview_hierarchy",
        {"hierarchy": hierarchy},
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )
    selected = call_tool(
        "select_execution_mode",
        {
            "root_id": hierarchy["delivery"]["id"],
            "selection": "AUTOMATIC",
            "expected_hierarchy_fingerprint": preview[
                "hierarchyFingerprint"
            ],
            "expected_graph_fingerprint": preview["graphFingerprint"],
            "authorized_project_ids": authorized_project_ids or [],
            "confirmed_by": "human",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )
    return preview, selected


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, *, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _preview_and_select_at(
    repository: Path,
    hierarchy: dict,
    clock: _Clock,
) -> tuple[dict, dict]:
    preview = planning.preview_hierarchy(
        root=str(repository),
        hierarchy=hierarchy,
        workspace_root=str(repository),
        host_adapter_id="codex",
        now=clock,
    )
    selected = planning.select_execution_mode(
        root=str(repository),
        root_id=hierarchy["delivery"]["id"],
        selection="AUTOMATIC",
        expected_hierarchy_fingerprint=preview["hierarchyFingerprint"],
        expected_graph_fingerprint=preview["graphFingerprint"],
        authorized_project_ids=[],
        confirmed_by="human",
        workspace_root=str(repository),
        host_adapter_id="codex",
        now=clock,
    )
    return preview, selected


class WorktreeSetupCoordinationTests(unittest.TestCase):
    def test_legacy_path_binding_remains_discoverable_in_place(self) -> None:
        with TemporaryDirectory() as root:
            workspace = Path(root, "workspace")
            workspace.mkdir()
            hierarchy = isolated_task_hierarchy(
                "d-legacy-workspace",
                "t-legacy-workspace",
            )
            prepared = planning.prepare_hierarchy(
                root=str(workspace),
                hierarchy=hierarchy,
                workspace_root=str(workspace),
            )
            planning.freeze_hierarchy(
                root=str(workspace),
                root_id=prepared["rootId"],
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            database = workspace / ".layered-delivery" / "scheduler.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE delivery_workspaces SET workspace_key = ? "
                    "WHERE root_id = ?",
                    (
                        legacy_path_workspace_key(workspace),
                        prepared["rootId"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            status = call_tool(
                "workspace_status",
                {"root_id": prepared["rootId"]},
                root=str(workspace),
                workspace_root=str(workspace),
            )

            self.assertEqual(status["status"], "ACTIVE")
            self.assertEqual(
                status["workspaceIsolation"]["identityVersion"],
                "PATH_V1",
            )

    def test_git_workspace_identity_survives_linked_worktree_move(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            _repository(repository)
            original = Path(root, "delivery-original")
            moved = Path(root, "delivery-moved")
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                "feature/stable-delivery",
                str(original),
                "main",
            )
            original_key = SchedulerRepository.workspace_key(original)

            _git(
                repository,
                "worktree",
                "move",
                str(original),
                str(moved),
            )

            self.assertEqual(
                SchedulerRepository.workspace_key(moved),
                original_key,
            )

    def test_git_workspace_identity_does_not_hash_repository_path(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            original = Path(root, "repository-original")
            moved = Path(root, "repository-moved")
            _repository(original)
            original_key = SchedulerRepository.workspace_key(original)

            shutil.move(str(original), str(moved))

            self.assertEqual(
                SchedulerRepository.workspace_key(moved),
                original_key,
            )

    def test_active_delivery_survives_worktree_move_after_v1_upgrade(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            original = Path(root, "delivery-original")
            moved = Path(root, "delivery-moved")
            branch_ref = "feature/d-stable-move"
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                branch_ref,
                str(original),
                "main",
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy(
                    "d-stable-move",
                    "t-stable-move",
                ),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            prepared = planning.prepare_hierarchy(
                root=str(repository),
                hierarchy=hierarchy,
                workspace_root=str(original),
            )
            planning.freeze_hierarchy(
                root=str(repository),
                root_id=prepared["rootId"],
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            database = repository / ".layered-delivery" / "scheduler.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE delivery_workspaces SET workspace_key = ? "
                    "WHERE root_id = ?",
                    (
                        legacy_path_workspace_key(original),
                        prepared["rootId"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            upgraded = call_tool(
                "workspace_status",
                {"root_id": prepared["rootId"]},
                root=str(repository),
                workspace_root=str(original),
                trusted_host_adapter="codex",
            )

            _git(
                repository,
                "worktree",
                "move",
                str(original),
                str(moved),
            )
            resumed = call_tool(
                "workspace_status",
                {"root_id": prepared["rootId"]},
                root=str(repository),
                workspace_root=str(moved),
                trusted_host_adapter="codex",
            )

            self.assertEqual(
                upgraded["workspaceIsolation"]["identityVersion"],
                "GIT_BRANCH_V1",
            )
            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertEqual(
                resumed["workspaceIsolation"]["workspaceKey"],
                upgraded["workspaceIsolation"]["workspaceKey"],
            )

    def test_git_workspace_identity_keeps_branches_isolated(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            _repository(repository)
            first = Path(root, "delivery-first")
            second = Path(root, "delivery-second")
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                "feature/first-delivery",
                str(first),
                "main",
            )
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                "feature/second-delivery",
                str(second),
                "main",
            )

            self.assertNotEqual(
                SchedulerRepository.workspace_key(first),
                SchedulerRepository.workspace_key(second),
            )

    def test_worktree_progress_is_reported_in_workspace_monitor(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-progress", "t-progress"),
                branch_ref="feature/d-progress",
                base_commit=base_commit,
            )
            _, selected = _preview_and_select(repository, hierarchy)
            dispatch = selected["worktreeSetup"]["hostDispatch"]

            self.assertEqual(dispatch["setupAttempt"], 1)
            self.assertEqual(
                dispatch["progressReporting"]["tool"],
                "report_worktree_setup",
            )
            self.assertEqual(
                dispatch["progressReporting"]["heartbeatIntervalSeconds"],
                30,
            )

            reported = call_tool(
                "report_worktree_setup",
                {
                    "root_id": "d-progress",
                    "project_id": "d-progress",
                    "reservation_id": dispatch["reservationId"],
                    "expected_attempt": 1,
                    "event": "PROGRESS",
                    "phase": "CREATING_WORKTREE",
                    "summary_zh": "正在创建独立 worktree",
                    "progress_percent": 40,
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            self.assertEqual(reported["setupProgress"]["health"], "ACTIVE")

            status = call_tool(
                "workspace_status",
                {"root_id": "d-progress"},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            monitor = status["worktreeSetup"]["progressMonitor"]
            self.assertEqual(monitor["recommendedPollSeconds"], 10)
            self.assertEqual(len(monitor["rows"]), 1)
            row = monitor["rows"][0]
            self.assertEqual(row["projectId"], "d-progress")
            self.assertEqual(row["attempt"], 1)
            self.assertEqual(row["phase"], "CREATING_WORKTREE")
            self.assertEqual(row["summaryZh"], "正在创建独立 worktree")
            self.assertEqual(row["progressPercent"], 40)
            self.assertEqual(row["health"], "ACTIVE")

    def test_expired_setup_blocks_until_one_atomic_reconciliation_retry(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-expired", "t-expired"),
                branch_ref="feature/d-expired",
                base_commit=base_commit,
            )
            clock = _Clock()
            _, selected = _preview_and_select_at(
                repository,
                hierarchy,
                clock,
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]
            clock.advance(seconds=121)

            expired = planning.workspace_status(
                root=str(repository),
                root_id="d-expired",
                workspace_root=str(repository),
                host_adapter_id="codex",
                now=clock,
            )
            setup = expired["worktreeSetup"]
            self.assertEqual(setup["state"], "WORKTREE_SETUP_LEASE_EXPIRED")
            self.assertEqual(
                setup["nextAction"],
                "RECONCILE_EXPIRED_WORKTREE_SETUP",
            )
            self.assertEqual(setup["hostDispatch"]["launchPolicy"], "BLOCKED")
            self.assertEqual(
                setup["progressMonitor"]["alerts"][0]["code"],
                "WORKTREE_SETUP_LEASE_EXPIRED",
            )

            def retry(index: int) -> dict | GatedLoopError:
                try:
                    return planning.report_worktree_setup(
                        root=str(repository),
                        root_id="d-expired",
                        project_id="d-expired",
                        reservation_id=dispatch["reservationId"],
                        expected_attempt=1,
                        event="RETRY_CONFIRMED",
                        retry_request_id=f"retry-expired-{index}",
                        phase="RECONCILING",
                        summary_zh="已确认旧创建进程停止且残留路径安全",
                        confirmed_previous_attempt_stopped=True,
                        confirmed_partial_state_reconciled=True,
                        workspace_root=str(repository),
                        host_adapter_id="codex",
                        now=clock,
                    )
                except GatedLoopError as error:
                    return error

            with ThreadPoolExecutor(max_workers=2) as executor:
                retries = list(executor.map(retry, range(2)))

            successful = [item for item in retries if isinstance(item, dict)]
            rejected = [
                item for item in retries if isinstance(item, GatedLoopError)
            ]
            self.assertEqual(len(successful), 1)
            self.assertEqual(len(rejected), 1)
            self.assertEqual(
                rejected[0].code,
                "SCHEDULER_WORKTREE_SETUP_ATTEMPT_STALE",
            )
            retry_setup = successful[0]["worktreeSetup"]
            self.assertEqual(
                retry_setup["hostDispatch"]["launchPolicy"],
                "IMMEDIATE",
            )
            self.assertEqual(retry_setup["hostDispatch"]["setupAttempt"], 2)
            self.assertTrue(successful[0]["retryDispatchGranted"])
            replayed = planning.report_worktree_setup(
                root=str(repository),
                root_id="d-expired",
                project_id="d-expired",
                reservation_id=dispatch["reservationId"],
                expected_attempt=1,
                event="RETRY_CONFIRMED",
                phase="RECONCILING",
                summary_zh="重放未知响应的同一核对请求",
                retry_request_id=successful[0]["retryRequestId"],
                confirmed_previous_attempt_stopped=True,
                confirmed_partial_state_reconciled=True,
                workspace_root=str(repository),
                host_adapter_id="codex",
                now=clock,
            )
            self.assertTrue(replayed["retryRequestReplayed"])
            self.assertEqual(replayed["setupAttempt"], 2)
            self.assertEqual(
                replayed["worktreeSetup"]["hostDispatch"]["launchPolicy"],
                "IMMEDIATE",
            )

    def test_progress_heartbeat_renews_setup_lease(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-renew", "t-renew"),
                branch_ref="feature/d-renew",
                base_commit=base_commit,
            )
            clock = _Clock()
            _, selected = _preview_and_select_at(
                repository,
                hierarchy,
                clock,
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]
            clock.advance(seconds=100)
            planning.report_worktree_setup(
                root=str(repository),
                root_id="d-renew",
                project_id="d-renew",
                reservation_id=dispatch["reservationId"],
                expected_attempt=1,
                event="PROGRESS",
                phase="CREATING_WORKTREE",
                summary_zh="创建仍在进行",
                progress_percent=60,
                workspace_root=str(repository),
                host_adapter_id="codex",
                now=clock,
            )
            clock.advance(seconds=100)

            status = planning.workspace_status(
                root=str(repository),
                root_id="d-renew",
                workspace_root=str(repository),
                host_adapter_id="codex",
                now=clock,
            )

            setup = status["worktreeSetup"]
            self.assertEqual(setup["state"], "DEDICATED_WORKTREE_REQUIRED")
            self.assertEqual(setup["setupProgress"]["health"], "ACTIVE")
            self.assertEqual(
                setup["setupProgress"]["summaryZh"],
                "创建仍在进行",
            )
            self.assertEqual(setup["progressMonitor"]["alerts"], [])

    def test_late_progress_is_rejected_and_expiry_remains_visible(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-late", "t-late"),
                branch_ref="feature/d-late",
                base_commit=base_commit,
            )
            clock = _Clock()
            _, selected = _preview_and_select_at(
                repository,
                hierarchy,
                clock,
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]
            clock.advance(seconds=121)

            with self.assertRaises(GatedLoopError) as caught:
                planning.report_worktree_setup(
                    root=str(repository),
                    root_id="d-late",
                    project_id="d-late",
                    reservation_id=dispatch["reservationId"],
                    expected_attempt=1,
                    event="PROGRESS",
                    phase="CREATING_WORKTREE",
                    summary_zh="迟到的旧宿主心跳",
                    progress_percent=80,
                    workspace_root=str(repository),
                    host_adapter_id="codex",
                    now=clock,
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_WORKTREE_SETUP_NOT_ACTIVE",
            )

            status = planning.workspace_status(
                root=str(repository),
                root_id="d-late",
                workspace_root=str(repository),
                host_adapter_id="codex",
                now=clock,
            )
            self.assertEqual(
                status["worktreeSetup"]["state"],
                "WORKTREE_SETUP_LEASE_EXPIRED",
            )

    def test_failed_setup_requires_explicit_reconciliation_before_retry(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-failed", "t-failed"),
                branch_ref="feature/d-failed",
                base_commit=base_commit,
            )
            clock = _Clock()
            _, selected = _preview_and_select_at(
                repository,
                hierarchy,
                clock,
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]

            failed = planning.report_worktree_setup(
                root=str(repository),
                root_id="d-failed",
                project_id="d-failed",
                reservation_id=dispatch["reservationId"],
                expected_attempt=1,
                event="FAILED",
                phase="FAILED",
                summary_zh="宿主创建失败并留下待检查目录",
                failure_code="HOST_WORKTREE_CREATE_FAILED",
                workspace_root=str(repository),
                host_adapter_id="codex",
                now=clock,
            )
            self.assertEqual(
                failed["worktreeSetup"]["state"],
                "WORKTREE_SETUP_FAILED",
            )
            self.assertEqual(
                failed["worktreeSetup"]["hostDispatch"]["launchPolicy"],
                "BLOCKED",
            )

            conflicting = bind_delivery_to_git(
                isolated_task_hierarchy(
                    "d-failed-conflict",
                    "t-failed-conflict",
                ),
                branch_ref="feature/d-failed",
                base_commit=base_commit,
            )
            with self.assertRaises(GatedLoopError) as reservation_conflict:
                _preview_and_select_at(repository, conflicting, clock)
            self.assertEqual(
                reservation_conflict.exception.code,
                "SCHEDULER_WORKTREE_BRANCH_RESERVED",
            )

            with self.assertRaises(GatedLoopError) as caught:
                planning.report_worktree_setup(
                    root=str(repository),
                    root_id="d-failed",
                    project_id="d-failed",
                    reservation_id=dispatch["reservationId"],
                    expected_attempt=1,
                    event="RETRY_CONFIRMED",
                    retry_request_id="retry-failed-1",
                    phase="RECONCILING",
                    summary_zh="只确认旧进程停止",
                    confirmed_previous_attempt_stopped=True,
                    confirmed_partial_state_reconciled=False,
                    workspace_root=str(repository),
                    host_adapter_id="codex",
                    now=clock,
                )

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_WORKTREE_SETUP_RECONCILIATION_REQUIRED",
            )

    def test_concurrent_repeat_selection_issues_one_creation_only(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-concurrent", "t-concurrent"),
                branch_ref="feature/d-concurrent",
                base_commit=base_commit,
            )
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            barrier = threading.Barrier(2)

            def select() -> dict:
                barrier.wait()
                return call_tool(
                    "select_execution_mode",
                    {
                        "root_id": "d-concurrent",
                        "selection": "AUTOMATIC",
                        "expected_hierarchy_fingerprint": preview[
                            "hierarchyFingerprint"
                        ],
                        "expected_graph_fingerprint": preview[
                            "graphFingerprint"
                        ],
                        "authorized_project_ids": [],
                        "confirmed_by": "human",
                    },
                    root=str(repository),
                    workspace_root=str(repository),
                    trusted_host_adapter="codex",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: select(), range(2)))

            dispatches = [
                result["worktreeSetup"]["hostDispatch"]
                for result in results
            ]
            self.assertEqual(
                sum(
                    dispatch["launchPolicy"] == "IMMEDIATE"
                    for dispatch in dispatches
                ),
                1,
            )
            self.assertEqual(
                sum(
                    dispatch["launchPolicy"] == "DO_NOT_REISSUE"
                    for dispatch in dispatches
                ),
                1,
            )
            self.assertEqual(
                sum(result["selectionRecorded"] for result in results),
                1,
            )

    def test_branch_usage_is_scoped_to_the_git_repository(self) -> None:
        with TemporaryDirectory() as root:
            primary = Path(root, "primary")
            secondary = Path(root, "secondary")
            primary_base = _repository(primary)
            secondary_base = _repository(secondary)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-scoped", "t-scoped"),
                branch_ref="feature/primary-only",
                base_commit=primary_base,
            )
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "primary",
                    "workspaceRoot": str(primary),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(
                        hierarchy["delivery"]["gitBinding"]
                    ),
                },
                {
                    "id": "secondary",
                    "workspaceRoot": str(secondary),
                    "access": "READ_ONLY",
                    "gitBinding": {
                        "branchRef": "feature/shared-name",
                        "baseRef": "main",
                        "baseCommit": secondary_base,
                        "integrationTarget": "main",
                    },
                },
            ]
            call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(primary),
                workspace_root=str(primary),
                trusted_host_adapter="codex",
            )
            repository = SchedulerRepository(str(primary))

            self.assertEqual(
                repository.git_branch_usage(
                    "feature/shared-name",
                    repository_key=git_repository_identity(str(primary)),
                ),
                [],
            )
            secondary_usage = repository.git_branch_usage(
                "feature/shared-name",
                repository_key=git_repository_identity(str(secondary)),
            )
            self.assertEqual(
                [item["rootId"] for item in secondary_usage],
                ["d-scoped"],
            )

    def test_repeated_selection_does_not_reissue_same_worktree_creation(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-repeat", "t-repeat"),
                branch_ref="feature/d-repeat",
                base_commit=base_commit,
            )
            preview, first = _preview_and_select(repository, hierarchy)

            second = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-repeat",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            first_dispatch = first["worktreeSetup"]["hostDispatch"]
            second_dispatch = second["worktreeSetup"]["hostDispatch"]
            self.assertEqual(
                first_dispatch["idempotencyKey"],
                second_dispatch["idempotencyKey"],
            )
            self.assertEqual(
                first_dispatch["gitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            self.assertEqual(
                first_dispatch["branchRef"],
                "feature/d-repeat",
            )
            self.assertFalse(first_dispatch["dispatchAlreadyIssued"])
            self.assertTrue(second_dispatch["dispatchAlreadyIssued"])
            self.assertEqual(
                second_dispatch["launchPolicy"],
                "DO_NOT_REISSUE",
            )
            self.assertEqual(
                second["nextAction"],
                "WAIT_FOR_EXISTING_WORKTREE_SETUP",
            )
            self.assertTrue(first["selectionRecorded"])
            self.assertFalse(second["selectionRecorded"])
            self.assertTrue(second["selectionAlreadyApplied"])

    def test_same_repository_branch_is_reserved_across_deliveries(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            first = bind_delivery_to_git(
                isolated_task_hierarchy("d-first", "t-first"),
                branch_ref="feature/shared",
                base_commit=base_commit,
            )
            second = bind_delivery_to_git(
                isolated_task_hierarchy("d-second", "t-second"),
                branch_ref="feature/shared",
                base_commit=base_commit,
            )
            _preview_and_select(repository, first)

            with self.assertRaises(GatedLoopError) as caught:
                _preview_and_select(repository, second)

            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_WORKTREE_BRANCH_RESERVED",
            )
            self.assertEqual(
                caught.exception.details["conflictingRootId"],
                "d-first",
            )

    def test_host_generated_branch_returns_frozen_branch_recovery_setup(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-host", "t-host"),
                branch_ref="feature/expected",
                base_commit=base_commit,
            )
            _preview_and_select(repository, hierarchy)
            host_worktree = Path(root, "host-worktree")
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                "feature/host-generated",
                str(host_worktree),
                "main",
            )

            status = call_tool(
                "workspace_status",
                {"root_id": "d-host"},
                root=str(repository),
                workspace_root=str(host_worktree),
                trusted_host_adapter="codex",
            )

            setup = status["worktreeSetup"]
            self.assertEqual(
                setup["state"],
                "FROZEN_DELIVERY_BRANCH_REQUIRED",
            )
            self.assertEqual(
                setup["nextAction"],
                "CHECKOUT_FROZEN_DELIVERY_BRANCH",
            )
            self.assertEqual(setup["branchRef"], "feature/expected")
            self.assertEqual(
                setup["actualBranchRef"],
                "feature/host-generated",
            )
            self.assertEqual(
                setup["gitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )

    def test_wrong_host_branch_with_changes_blocks_branch_checkout(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            base_commit = _repository(repository)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-dirty-host", "t-dirty-host"),
                branch_ref="feature/expected",
                base_commit=base_commit,
            )
            _preview_and_select(repository, hierarchy)
            host_worktree = Path(root, "host-worktree")
            _git(
                repository,
                "worktree",
                "add",
                "-b",
                "feature/host-generated",
                str(host_worktree),
                "main",
            )
            (host_worktree / "uncommitted.txt").write_text(
                "do not lose this\n",
                encoding="utf-8",
            )

            status = call_tool(
                "workspace_status",
                {"root_id": "d-dirty-host"},
                root=str(repository),
                workspace_root=str(host_worktree),
                trusted_host_adapter="codex",
            )

            setup = status["worktreeSetup"]
            self.assertEqual(
                setup["state"],
                "FROZEN_DELIVERY_BRANCH_DIRTY",
            )
            self.assertEqual(
                setup["nextAction"],
                "REVIEW_CHANGES_BEFORE_FROZEN_BRANCH_CHECKOUT",
            )
            self.assertFalse(setup["workingTree"]["clean"])

    def test_multi_project_setup_prepares_every_write_scope_before_resume(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            primary = Path(root, "primary")
            secondary = Path(root, "secondary")
            primary_base = _repository(primary)
            secondary_base = _repository(secondary)
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-multi", "t-multi"),
                branch_ref="feature/d-multi",
                base_commit=primary_base,
            )
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "primary",
                    "workspaceRoot": str(primary),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(
                        hierarchy["delivery"]["gitBinding"]
                    ),
                },
                {
                    "id": "secondary",
                    "workspaceRoot": str(secondary),
                    "access": "READ_WRITE",
                    "gitBinding": {
                        "branchRef": "feature/d-multi",
                        "baseRef": "main",
                        "baseCommit": secondary_base,
                        "integrationTarget": "main",
                    },
                },
            ]
            preview, selected = _preview_and_select(
                primary,
                hierarchy,
                authorized_project_ids=["primary", "secondary"],
            )

            initial_setup = selected["worktreeSetup"]
            self.assertEqual(
                initial_setup["state"],
                "PROJECT_WORKTREES_REQUIRED",
            )
            self.assertEqual(
                initial_setup["pendingProjectIds"],
                ["primary", "secondary"],
            )
            self.assertEqual(
                {
                    item["projectId"]
                    for item in initial_setup["projectWorktreeSetups"]
                },
                {"primary", "secondary"},
            )
            self.assertEqual(
                initial_setup["progressControlRoot"],
                str(primary.resolve()),
            )

            primary_worktree = Path(root, "primary-worktree")
            _git(
                primary,
                "worktree",
                "add",
                "-b",
                "feature/d-multi",
                str(primary_worktree),
                "main",
            )
            waiting = call_tool(
                "workspace_status",
                {"root_id": "d-multi"},
                root=str(primary),
                workspace_root=str(primary_worktree),
                trusted_host_adapter="codex",
            )
            self.assertEqual(
                waiting["worktreeSetup"]["pendingProjectIds"],
                ["secondary"],
            )

            secondary_worktree = Path(root, "secondary-worktree")
            _git(
                secondary,
                "worktree",
                "add",
                "-b",
                "feature/d-multi",
                str(secondary_worktree),
                "main",
            )
            resumed = call_tool(
                "resume_execution_mode",
                {
                    "root_id": "d-multi",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                },
                root=str(primary),
                workspace_root=str(primary_worktree),
                trusted_host_adapter="codex",
            )

            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertTrue(resumed["automaticDispatchRequested"])
            self.assertEqual(
                {
                    item["id"]: item["workspaceRoot"]
                    for item in resumed["verifiedProjectScopes"]
                },
                {
                    "primary": str(primary_worktree.resolve()),
                    "secondary": str(secondary_worktree.resolve()),
                },
            )


if __name__ == "__main__":
    unittest.main()
