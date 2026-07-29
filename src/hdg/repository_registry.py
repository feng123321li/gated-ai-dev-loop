from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .errors import GatedLoopError, fail
from .fs_safe import exclusive_file_lock, safe_path
from .jsonio import canonical_json
from .repository_contracts import GOVERNANCE_DIRECTORY, PROJECTION_LOCK_FILE
from .timing import timed_stage, timing_increment, timing_metric


def read_registry(
    self,
    *,
    allow_missing: bool = False,
    isolate_historical_evidence: bool = False,
) -> dict[str, Any]:
    if not isolate_historical_evidence:
        self._isolated_entry_ids = set()
    if self._connection is None and not self.database_path.is_file():
        if allow_missing:
            self._isolated_entry_ids = set()
            return self.empty_registry()
        fail("WORK_ITEM_DATABASE_MISSING", "Governance database does not exist")
    with self._read_connection() as connection:
        row = connection.execute(
            "SELECT schema_version, coordination_root, revision, current_focus_json, updated_at "
            "FROM workspace WHERE singleton = 1"
        ).fetchone()
        if row is None:
            if allow_missing:
                self._isolated_entry_ids = set()
                return self.empty_registry()
            fail("WORK_ITEM_REGISTRY_MISSING", "Governance registry does not exist")
        try:
            focus = json.loads(row["current_focus_json"])
            entries = [
                json.loads(item["entry_json"])
                for item in connection.execute("SELECT entry_json FROM work_items ORDER BY id")
            ]
        except (TypeError, json.JSONDecodeError):
            fail("WORK_ITEM_REGISTRY_INVALID", "Governance registry records are invalid")
        registry = {
            "schemaVersion": row["schema_version"],
            "coordinationRoot": row["coordination_root"],
            "revision": row["revision"],
            "currentFocus": focus,
            "workItems": entries,
            "updatedAt": row["updated_at"],
        }
        return self.validate_registry(
            registry,
            isolate_historical_evidence=isolate_historical_evidence,
        )


def read_operational_registry(self, *, allow_missing: bool = False) -> dict[str, Any]:
    registry = self.read_registry(
        allow_missing=allow_missing,
        isolate_historical_evidence=True,
    )
    self.validate_stored_evidence(registry)
    return registry


def validate_operational_registry(self, registry: dict[str, Any]) -> dict[str, Any]:
    self.validate_registry(registry, isolate_historical_evidence=True)
    if self._isolated_entry_ids != self._transaction_isolated_entry_ids:
        fail(
            "WORK_ITEM_ISOLATION_CHANGED",
            "A governance write cannot create, repair, or expand isolated entries implicitly",
        )
    by_id = {entry["id"]: entry for entry in registry["workItems"]}
    for item_id, snapshot in self._transaction_isolated_snapshots.items():
        if item_id not in by_id or canonical_json(by_id[item_id]) != snapshot:
            fail(
                "WORK_ITEM_ISOLATED_ENTRY_CHANGED",
                f"A read-only isolated entry cannot be changed: {item_id}",
                id=item_id,
            )
    return registry


@contextmanager
def transaction(self) -> Iterator[dict[str, Any]]:
    self.ensure_runtime_root()
    with timed_stage("sqlite.connect"):
        connection = self._connect(create=True)
    committed = False
    projection_request: dict[str, Any] | None = None
    try:
        with timed_stage("sqlite.lockWait"):
            connection.execute("BEGIN IMMEDIATE")
        self._initialize_database(connection, create=True)
        self._connection = connection
        self._pending_projection = None
        with timed_stage("sqlite.read"):
            registry = self.read_operational_registry(allow_missing=True)
        self._transaction_isolated_entry_ids = set(self._isolated_entry_ids)
        self._transaction_isolated_snapshots = {
            entry["id"]: canonical_json(entry)
            for entry in registry["workItems"]
            if entry["id"] in self._transaction_isolated_entry_ids
        }
        yield registry
        with timed_stage("sqlite.commit"):
            connection.commit()
        committed = True
        projection_request = self._pending_projection
    except Exception:
        if not committed:
            connection.rollback()
        raise
    finally:
        self._transaction_isolated_entry_ids = set()
        self._transaction_isolated_snapshots = {}
        self._pending_projection = None
        self._connection = None
        connection.close()
    if projection_request is not None:
        mode = projection_request["mode"]
        timing_metric("projectionMode", mode)
        try:
            with timed_stage(f"projection.{mode}"):
                projection_lock = safe_path(
                    self.root,
                    (
                        f"{GOVERNANCE_DIRECTORY}/"
                        f"{PROJECTION_LOCK_FILE}"
                    ),
                )
                with exclusive_file_lock(projection_lock):
                    projection_registry = projection_request["registry"]
                    if (
                        self.current_registry_revision()
                        != projection_registry["revision"]
                    ):
                        timing_increment("projectionRefreshRetries")
                        projection_registry = (
                            self.read_operational_registry()
                        )
                        mode = "full"
                    for attempt in range(3):
                        if mode == "heartbeat":
                            self.refresh_heartbeat_projections(
                                projection_registry,
                                projection_request["rootId"],
                            )
                        elif mode == "interaction":
                            self.refresh_interaction_projection(
                                projection_registry,
                                projection_request["rootId"],
                            )
                        elif mode == "incremental":
                            self.refresh_incremental_projections(
                                projection_registry,
                                projection_request["rootIds"],
                                projection_request["changedItemIds"],
                            )
                        else:
                            self.refresh_registry_projections(
                                projection_registry,
                            )
                        if (
                            self.current_registry_revision()
                            == projection_registry["revision"]
                        ):
                            break
                        timing_increment("projectionRefreshRetries")
                        projection_registry = (
                            self.read_operational_registry()
                        )
                        mode = "full"
                    else:
                        fail(
                            "WORK_ITEM_PROJECTION_BUSY",
                            (
                                "Projection could not catch up with "
                                "concurrent state changes"
                            ),
                        )
        except Exception as error:
            raise GatedLoopError(
                "WORK_ITEM_PROJECTION_REFRESH_REQUIRED",
                "Machine state committed, but derived projections require refresh",
                details={
                    "registryRevision": projection_request["registry"]["revision"],
                    "projectionMode": mode,
                    "cause": type(error).__name__,
                },
            ) from error


def current_registry_revision(self) -> int:
    with self._read_connection() as connection:
        row = connection.execute(
            "SELECT revision FROM workspace WHERE singleton = 1"
        ).fetchone()
    if row is None:
        fail("WORK_ITEM_REGISTRY_MISSING", "Governance registry does not exist")
    return row["revision"]


def schedule_projection(
    self,
    registry: dict[str, Any],
    *,
    mode: str,
    root_id: str | None = None,
    root_ids: set[str] | None = None,
    changed_item_ids: set[str] | None = None,
) -> None:
    if mode not in {"full", "incremental", "heartbeat", "interaction"}:
        fail("WORK_ITEM_PROJECTION_MODE_INVALID", "Projection mode is invalid")
    if self._connection is None:
        fail("WORK_ITEM_TRANSACTION_REQUIRED", "Projection scheduling requires an active transaction")
    if mode == "incremental" and (not root_ids or not changed_item_ids):
        fail(
            "WORK_ITEM_PROJECTION_MODE_INVALID",
            "Incremental projection requires affected roots and items",
        )
    request = {
        "mode": mode,
        "registry": registry,
        "rootId": root_id,
        "rootIds": set(root_ids or ()),
        "changedItemIds": set(changed_item_ids or ()),
    }
    current = self._pending_projection
    if current is None or mode == "full":
        self._pending_projection = request
    elif current["mode"] == "full":
        return
    elif current["mode"] == mode == "incremental":
        current["registry"] = registry
        current["rootIds"].update(request["rootIds"])
        current["changedItemIds"].update(request["changedItemIds"])
    elif (
        current["mode"] != mode
        or current["rootId"] != root_id
    ):
        self._pending_projection = {
            "mode": "full",
            "registry": registry,
            "rootId": None,
            "rootIds": set(),
            "changedItemIds": set(),
        }
