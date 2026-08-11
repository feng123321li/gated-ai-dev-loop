from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Callable

from .errors import GatedLoopError
from .fs_safe import (
    atomic_replace_directory,
    atomic_write,
    exclusive_file_lock,
    read_regular_file,
    safe_path,
)
from .model_rendering import (
    WORK_ITEM_DIRECTORY,
    render_projection_documents,
    render_work_item_projection_documents,
    render_workspace_overview,
)
from .progress_reporting import attach_progress_monitor


MANUAL_WRITABLE_PROJECTIONS = frozenset(
    {"progress.md", "acceptance.md"}
)


def _projection_tree_matches(
    directory: Path,
    documents: dict[str, str],
) -> bool:
    try:
        entries = list(directory.rglob("*"))
    except (FileNotFoundError, NotADirectoryError):
        return False
    if any(entry.is_symlink() for entry in entries):
        return False
    if any(
        not entry.is_dir() and not entry.is_file()
        for entry in entries
    ):
        return False
    relative_files = {
        entry.relative_to(directory).as_posix()
        for entry in entries
        if entry.is_file()
    }
    if relative_files != set(documents):
        return False
    expected_directories = {
        Path(filename).parent.as_posix()
        for filename in documents
        if Path(filename).parent != Path(".")
    }
    actual_directories = {
        entry.relative_to(directory).as_posix()
        for entry in entries
        if entry.is_dir()
    }
    if actual_directories != expected_directories:
        return False
    try:
        return all(
            (directory / filename).read_bytes()
            == content.encode("utf-8")
            for filename, content in documents.items()
        )
    except (FileNotFoundError, OSError):
        return False


class DeliveryProjectionStore:
    """Own human-readable Delivery and workspace projections."""

    def __init__(
        self,
        repository: Any,
        *,
        validate_stored_definition: Callable[..., Any],
        timestamp_fn: Callable[[object], str],
    ) -> None:
        self.repository = repository
        self.validate_stored_definition = validate_stored_definition
        self.timestamp_fn = timestamp_fn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)

    def write_projections(
        self,
        root_id: str,
        *,
        preserve_manual_updates: bool = True,
        refresh_workspace_overview: bool = True,
    ) -> None:
        """Regenerate controller-owned projections from SQLite state."""

        with exclusive_file_lock(self.lock_path):
            definition = self.hierarchy(root_id)
            run = None
            try:
                run = self.run(root_id)
            except Exception as error:
                if (
                    getattr(error, "code", None)
                    != "SCHEDULER_RUN_MISSING"
                ):
                    raise
            if run is not None:
                run = attach_progress_monitor(
                    run,
                    definition["graph"],
                    observed_at=self.timestamp_fn(self.now),
                )
            projection_root = safe_path(self.control_root, root_id)
            revision_history = self.revision_history(root_id)
            documents = render_projection_documents(
                definition,
                run,
                revision_history,
            )
            manual_snapshot = (
                definition["status"] == "HANDOFF_READY" and run is None
                and preserve_manual_updates
            )
            for legacy_filename in (
                "hierarchy.json",
                "graph.json",
                "state.json",
                "interfaces.md",
            ):
                legacy_projection = safe_path(
                    projection_root,
                    legacy_filename,
                )
                if (
                    legacy_projection.is_file()
                    or legacy_projection.is_symlink()
                ):
                    legacy_projection.unlink()
            for filename, content in documents.items():
                target = projection_root / filename
                preserve_manual_update = (
                    manual_snapshot
                    and filename in MANUAL_WRITABLE_PROJECTIONS
                    and target.is_file()
                    and not target.is_symlink()
                )
                if not preserve_manual_update:
                    atomic_write(target, content)
            work_item_root = safe_path(
                projection_root,
                WORK_ITEM_DIRECTORY,
            )
            work_item_documents = render_work_item_projection_documents(
                definition,
                run,
            )
            preserved_work_item_documents: dict[str, bytes] = {}
            if manual_snapshot:
                for filename in work_item_documents:
                    if (
                        Path(filename).name
                        not in MANUAL_WRITABLE_PROJECTIONS
                    ):
                        continue
                    target = work_item_root / filename
                    if target.is_file() and not target.is_symlink():
                        preserved_work_item_documents[filename] = (
                            read_regular_file(work_item_root, filename)
                        )

            def populate_work_items(staging: Path) -> None:
                for filename, content in work_item_documents.items():
                    atomic_write(
                        staging / filename,
                        preserved_work_item_documents.get(
                            filename,
                            content,
                        ),
                    )

            if not _projection_tree_matches(
                work_item_root,
                work_item_documents,
            ):
                atomic_replace_directory(
                    work_item_root,
                    populate_work_items,
                )
            legacy_task_baselines = safe_path(
                projection_root,
                "task-baselines",
            )
            if legacy_task_baselines.is_dir():
                shutil.rmtree(legacy_task_baselines)
            elif legacy_task_baselines.exists():
                legacy_task_baselines.unlink()
            if refresh_workspace_overview:
                self._write_workspace_overview()

    def _write_workspace_overview(self) -> None:
        atomic_write(
            safe_path(self.control_root, "overview.md"),
            render_workspace_overview(
                self._workspace_projection_sources()
            ),
        )

    def write_workspace_overview(self) -> None:
        """Refresh the cross-Delivery overview without coupling projections."""

        with exclusive_file_lock(self.lock_path):
            self._write_workspace_overview()

    def _workspace_projection_sources(self) -> list[dict[str, Any]]:
        """Load unarchived Delivery summaries for the root overview."""

        with self.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hierarchies WHERE status != 'ARCHIVED' "
                "ORDER BY updated_at DESC, root_id"
            ).fetchall()
            sources: list[dict[str, Any]] = []
            for row in rows:
                try:
                    hierarchy, graph = self.validate_stored_definition(row)
                except GatedLoopError as error:
                    sources.append(
                        {
                            "rootId": row["root_id"],
                            "status": "STATE_INVALID",
                            "createdAt": row["created_at"],
                            "updatedAt": row["updated_at"],
                            "stateError": {
                                "code": error.code,
                                "message": error.message,
                            },
                        }
                    )
                    continue
                run_row = connection.execute(
                    "SELECT * FROM runs "
                    "WHERE root_id = ? AND revision = ?",
                    (row["root_id"], row["revision"]),
                ).fetchone()
                run = None
                if run_row is not None:
                    run = {
                        "runId": run_row["run_id"],
                        "rootId": run_row["root_id"],
                        "deliveryRevision": run_row["revision"],
                        "status": run_row["status"],
                        "startedAt": run_row["started_at"],
                        "updatedAt": run_row["updated_at"],
                        "completedAt": run_row["completed_at"],
                        "cancelledAt": run_row["cancelled_at"],
                        "nodes": self.latest_nodes(
                            connection,
                            run_row["run_id"],
                        ),
                    }
                sources.append(
                    {
                        "rootId": row["root_id"],
                        "status": row["status"],
                        "hierarchyFingerprint": row[
                            "hierarchy_fingerprint"
                        ],
                        "graphFingerprint": row[
                            "graph_fingerprint"
                        ],
                        "hierarchy": hierarchy,
                        "graph": graph,
                        "createdAt": row["created_at"],
                        "updatedAt": row["updated_at"],
                        "run": run,
                    }
                )
        return sources

__all__ = (
    "MANUAL_WRITABLE_PROJECTIONS",
    "DeliveryProjectionStore",
)
