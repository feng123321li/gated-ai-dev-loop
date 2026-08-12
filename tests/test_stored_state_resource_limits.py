from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hdg.errors import GatedLoopError
from hdg.graph_model import compile_delivery_graph
from hdg.jsonio import canonical_json, fingerprint
from hdg.model_core import validate_hierarchy_definition
from hdg.repository import SchedulerRepository
from hdg.storage_schema import initialize_scheduler_storage

from .test_loop_architecture import (
    delivery,
    group_definition,
    node,
    task_definition,
)
from .test_scheduler_runtime import database_hierarchy


def _store_preexisting_hierarchy(root: str, hierarchy: dict) -> None:
    hierarchy_value = fingerprint(hierarchy)
    graph = compile_delivery_graph(
        hierarchy,
        hierarchy_fingerprint=hierarchy_value,
    )
    control_root = Path(root, ".layered-delivery")
    control_root.mkdir()
    connection = sqlite3.connect(control_root / "scheduler.db")
    try:
        initialize_scheduler_storage(connection)
        connection.execute(
            "INSERT INTO hierarchies("
            "root_id, revision, hierarchy_fingerprint, graph_fingerprint, "
            "hierarchy_json, graph_json, status, created_at, updated_at"
            ") VALUES (?, 1, ?, ?, ?, ?, 'PREPARED', ?, ?)",
            (
                hierarchy["delivery"]["id"],
                hierarchy_value,
                fingerprint(graph),
                canonical_json(hierarchy),
                canonical_json(graph),
                "2026-08-12T00:00:00Z",
                "2026-08-12T00:00:00Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _oversized_database_hierarchy() -> dict:
    hierarchy = validate_hierarchy_definition(database_hierarchy())
    loop = hierarchy["root"]["definition"]["execution"]["loop"]
    template = loop["payload"]["databaseChanges"][0]
    changes = []
    claims = []
    for index in range(257):
        change = deepcopy(template)
        change["table"] = f"orders_{index:03d}"
        change["resourceClaim"] = f"db-schema-orders-{index:03d}"
        changes.append(change)
        claims.append(change["resourceClaim"])
    loop["payload"]["databaseChanges"] = changes
    loop["resourceClaims"] = claims
    return hierarchy


def _deep_preexisting_hierarchy() -> dict:
    current = node(
        task_definition(
            item_id="t-deep",
            parent_id="g-064",
        )
    )
    for depth in reversed(range(65)):
        group_id = f"g-{depth:03d}"
        parent_id = None if depth == 0 else f"g-{depth - 1:03d}"
        current = node(
            group_definition(
                item_id=group_id,
                parent_id=parent_id,
                children=[current],
            ),
            [current],
        )
    return delivery(current, delivery_id="d-deep-preexisting")


class StoredStateResourceLimitTests(unittest.TestCase):
    def test_preexisting_database_contract_above_new_limit_remains_readable(
        self,
    ) -> None:
        hierarchy = _oversized_database_hierarchy()
        with self.assertRaises(GatedLoopError) as rejected:
            validate_hierarchy_definition(hierarchy)
        self.assertEqual(
            rejected.exception.code,
            "DATABASE_CHANGE_CONTRACT_INVALID",
        )

        with TemporaryDirectory() as root:
            _store_preexisting_hierarchy(root, hierarchy)

            stored = SchedulerRepository(root).hierarchy("d-service")

        self.assertEqual(stored["hierarchy"], hierarchy)

    def test_preexisting_deep_hierarchy_remains_readable(self) -> None:
        hierarchy = _deep_preexisting_hierarchy()
        with self.assertRaises(GatedLoopError) as rejected:
            validate_hierarchy_definition(hierarchy)
        self.assertEqual(
            rejected.exception.code,
            "WORK_ITEM_HIERARCHY_TOO_DEEP",
        )

        with TemporaryDirectory() as root:
            _store_preexisting_hierarchy(root, hierarchy)

            stored = SchedulerRepository(root).hierarchy(
                "d-deep-preexisting"
            )

        self.assertEqual(stored["hierarchy"], hierarchy)


if __name__ == "__main__":
    unittest.main()
