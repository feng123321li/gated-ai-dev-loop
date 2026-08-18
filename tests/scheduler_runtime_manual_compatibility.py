from __future__ import annotations

from .scheduler_runtime_support import (
    GRAPH_COMPILER_CONTRACT,
    Path,
    SchedulerRepository,
    at,
    call_tool,
    create_manual_handoff,
    delivery_task_hierarchy,
    fingerprint,
    json,
    preview_hierarchy,
    sqlite3,
    start_manual_handoff,
    workspace_status,
)


class SchedulerRuntimeTestsPart13:
    def test_unstarted_legacy_handoff_refreshes_runtime_before_manual_run(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-legacy-handoff-runtime",
            "t-legacy-handoff-runtime",
        )
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=preview[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        database = Path(
            self.root,
            ".layered-delivery",
            "scheduler.db",
        )
        connection = sqlite3.connect(database)
        try:
            row = connection.execute(
                "SELECT graph_json FROM hierarchies WHERE root_id = ?",
                (handoff["rootId"],),
            ).fetchone()
            legacy_graph = json.loads(row[0])
            legacy_graph["runtime"].pop("compilerContract")
            claim_policy = legacy_graph["runtime"]["claimPolicy"]
            claim_policy["leaseSeconds"] = 30 * 60
            claim_policy["heartbeatSeconds"] = 5 * 60
            claim_policy.pop("renewBeforeSeconds")
            claim_policy.pop("maxExpectedCommandSeconds")
            claim_policy.pop("longCommandLeaseBufferSeconds")
            legacy_graph_fingerprint = fingerprint(legacy_graph)
            encoded_graph = json.dumps(
                legacy_graph,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for table in ("hierarchies", "delivery_revisions"):
                connection.execute(
                    f"UPDATE {table} SET graph_json = ?, "
                    "graph_fingerprint = ? WHERE root_id = ?",
                    (
                        encoded_graph,
                        legacy_graph_fingerprint,
                        handoff["rootId"],
                    ),
                )
            connection.execute(
                "UPDATE delivery_revisions SET execution_mode = NULL "
                "WHERE root_id = ?",
                (handoff["rootId"],),
            )
            connection.execute(
                "DELETE FROM delivery_workspaces WHERE root_id = ?",
                (handoff["rootId"],),
            )
            connection.commit()
        finally:
            connection.close()

        status = workspace_status(
            root=self.root,
            root_id=handoff["rootId"],
        )
        self.assertEqual(status["status"], "HANDOFF_READY")
        self.assertEqual(
            status["graphCompatibility"]["state"],
            "REFRESH_ON_MANUAL_START",
        )
        history = call_tool(
            "delivery_revision_history",
            {"root_id": handoff["rootId"]},
            root=self.root,
            workspace_root=self.root,
        )
        self.assertEqual(
            history["revisions"][0]["graphFingerprint"],
            legacy_graph_fingerprint,
        )

        started = start_manual_handoff(
            root=self.root,
            root_id=handoff["rootId"],
            expected_hierarchy_fingerprint=handoff[
                "hierarchyFingerprint"
            ],
            expected_graph_fingerprint=legacy_graph_fingerprint,
            started_by="manual-upgrade-receiver",
            now=at(2),
        )

        self.assertEqual(started["executionMode"], "manual")
        self.assertTrue(started["graphRunCreated"])
        self.assertEqual(
            started["graphRuntimeRefresh"]["previousGraphFingerprint"],
            legacy_graph_fingerprint,
        )
        self.assertEqual(
            started["graphRuntimeRefresh"]["compilerContract"],
            GRAPH_COMPILER_CONTRACT,
        )
        stored = SchedulerRepository(self.root).hierarchy(handoff["rootId"])
        self.assertEqual(
            stored["graph"]["runtime"]["compilerContract"],
            GRAPH_COMPILER_CONTRACT,
        )
        self.assertNotEqual(
            stored["graphFingerprint"],
            legacy_graph_fingerprint,
        )
        handoff_content = Path(
            self.root,
            handoff["manualHandoff"]["path"],
        ).read_text(encoding="utf-8")
        self.assertIn(GRAPH_COMPILER_CONTRACT, handoff_content)
        self.assertIn(stored["graphFingerprint"], handoff_content)
