from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .repository_contracts import (
    GOVERNANCE_DIRECTORY,
    WORK_ITEM_DATABASE_FILE,
    timestamp,
)

__all__ = (
    "GovernanceRepository",
    "timestamp",
)


class GovernanceRepository:
    """Own safe persistence while delegating each storage responsibility."""

    from .repository_evidence_store import (
        _automatic_event_summary,
        _stored_evidence_error,
        _validated_stored_artifact,
        actual_development_skill_usage,
        append_interaction_event,
        effective_required_skills,
        effective_task_file_changes,
        read_interaction_events,
        read_validation_remediations,
        validate_stored_evidence,
    )
    from .repository_graph_store import (
        append_graph_event,
        begin_graph_attempts,
        freeze_graph_definition,
        read_graph_definition,
        read_graph_events,
        read_graph_evidence,
        read_graph_run,
        rebuild_graph_run_from_events,
        start_graph_run,
        store_graph_definition,
        sync_graph_runs,
    )
    from .repository_hierarchy import (
        assert_subtree_operational,
        empty_registry,
        is_item_isolated,
        item_by_id,
        lineage_item_ids,
    )
    from .repository_packages import (
        _progress_counts,
        assert_current_lineage,
        package_files,
        read_hierarchy_state,
        read_package,
        recompute_progress,
        replace_package,
        store_hierarchy,
        write_hierarchy_package,
        write_new_package,
    )
    from .repository_projections import (
        _graph_projection_snapshot,
        _write_interaction_logs,
        refresh_heartbeat_projections,
        refresh_incremental_projections,
        refresh_interaction_logs,
        refresh_interaction_projection,
        refresh_markdown_projections,
        refresh_registry_projections,
        write_acceptance_report,
        write_development_review,
        write_registry,
        write_task_context,
    )
    from .repository_registry import (
        current_registry_revision,
        read_operational_registry,
        read_registry,
        schedule_projection,
        transaction,
        validate_operational_registry,
    )
    from .repository_registry_validation import (
        _is_read_only_evidence_entry,
        _validate_registry_entry,
        validate_registry,
    )
    from .repository_sqlite import (
        _active_connection,
        _assert_database_schema,
        _connect,
        _initialize_database,
        _read_connection,
        staging_transaction,
    )
    from .repository_workspace import (
        assert_self_hosting_dogfood,
        ensure_runtime_root,
        inspect_workspace_state,
    )

    def __init__(self, root: str | os.PathLike[str], *, now: object = None) -> None:
        self.root = Path(root).absolute()
        self.now = now
        self._connection: sqlite3.Connection | None = None
        self._isolated_entry_ids: set[str] = set()
        self._transaction_isolated_entry_ids: set[str] = set()
        self._transaction_isolated_snapshots: dict[str, str] = {}
        self._pending_projection: dict[str, Any] | None = None

    @property
    def governance_root(self) -> Path:
        return self.root / GOVERNANCE_DIRECTORY

    @property
    def database_path(self) -> Path:
        return self.governance_root / WORK_ITEM_DATABASE_FILE

    def item_path(self, entry: dict[str, Any]) -> Path:
        return self.governance_root / entry["packagePath"]
