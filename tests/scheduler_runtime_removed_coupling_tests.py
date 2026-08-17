from __future__ import annotations

from .scheduler_runtime_support import (
    GatedLoopError,
    Path,
    TemporaryDirectory,
    deepcopy,
    preview_hierarchy,
    sqlite3,
    task_hierarchy,
    tool_definitions,
    unittest,
    validate_hierarchy_definition,
    loop_execution_policy,
)


class RemovedCouplingTests(unittest.TestCase):
    def test_current_surface_omits_quota_and_model_reasoning_contracts(
        self,
    ) -> None:
        tools = {tool["name"]: tool for tool in tool_definitions()}
        self.assertNotIn("recommend_assurance_profile", tools)
        self.assertNotIn(
            "actual_model_id",
            tools["dispatch_loop"]["inputSchema"]["properties"],
        )
        self.assertEqual(
            set(tools["pause_loop"]["inputSchema"]["properties"]),
            {"root_id", "node_id", "operation_id"},
        )
        result_properties = tools["record_loop_result"]["inputSchema"][
            "properties"
        ]["outcome"]["properties"]["result"]["properties"]
        self.assertNotIn("workerTelemetry", result_properties)

        policy_text = repr(loop_execution_policy())
        for removed in (
            "providerRateLimit",
            "quota",
            "reasoningEffort",
            "modelPolicy",
        ):
            self.assertNotIn(removed, policy_text)

        with TemporaryDirectory() as root:
            preview_hierarchy(root=root, hierarchy=task_hierarchy())
            connection = sqlite3.connect(
                Path(root, ".layered-delivery", "scheduler.db")
            )
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                run_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(runs)")
                }
            finally:
                connection.close()

        self.assertNotIn("host_capacity_breakers", tables)
        self.assertTrue(
            {
                "host_capacity_key",
                "host_capacity_reset_at",
                "host_capacity_reported_at",
                "host_capacity_reason",
            }.isdisjoint(run_columns)
        )

    def test_current_schema_indexes_event_pages_by_run_and_event_id(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            preview_hierarchy(root=root, hierarchy=task_hierarchy())
            connection = sqlite3.connect(
                Path(root, ".layered-delivery", "scheduler.db")
            )
            try:
                index_columns = [
                    row[2]
                    for row in connection.execute(
                        "PRAGMA index_info(graph_events_by_run_event_id)"
                    )
                ]
            finally:
                connection.close()

        self.assertEqual(index_columns, ["run_id", "event_id"])

    def test_current_schema_omits_removed_model_routing_columns(self) -> None:
        with TemporaryDirectory() as root:
            preview_hierarchy(root=root, hierarchy=task_hierarchy())
            connection = sqlite3.connect(
                Path(root, ".layered-delivery", "scheduler.db")
            )
            try:
                reservation_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(dispatch_reservations)"
                    )
                }
                receiver_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(host_receiver_identities)"
                    )
                }
            finally:
                connection.close()

        self.assertTrue(
            {"model_id", "reasoning_class"}.isdisjoint(
                reservation_columns
            )
        )
        self.assertNotIn("model_id", receiver_columns)

    def test_old_scope_gate_skill_and_plan_fields_are_rejected(
        self,
    ) -> None:
        source = task_hierarchy()
        definition = source["root"]["definition"]
        for field, value in (
            ("scope", ["src/**"]),
            ("gateLevel", "FULL"),
            ("requiredSkills", []),
            ("developmentPlan", {}),
        ):
            candidate = deepcopy(source)
            candidate["root"]["definition"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(GatedLoopError):
                    validate_hierarchy_definition(candidate)

    def test_mcp_surface_contains_only_outer_scheduler_tools(
        self,
    ) -> None:
        tools = tool_definitions()
        names = {tool["name"] for tool in tools}
        self.assertIn("dispatch_loop", names)
        self.assertIn("record_loop_result", names)
        self.assertNotIn("dispatch_task", names)
        self.assertNotIn("gate_item", names)
        self.assertNotIn("record_skill_activation", names)
        self.assertNotIn("record_skill_conformance", names)
        self.assertNotIn("remediate_task", names)
        pause_tool = next(
            tool for tool in tools if tool["name"] == "pause_loop"
        )
        self.assertIn("live lease", pause_tool["description"])
        self.assertNotIn(
            "capacity handoff",
            pause_tool["description"],
        )
        context_tool = next(
            tool for tool in tools if tool["name"] == "loop_context"
        )
        self.assertIn(
            "expired-lease recovery",
            context_tool["description"],
        )
        self.assertIn(
            "completion policy",
            context_tool["description"],
        )
        result_tool = next(
            tool for tool in tools if tool["name"] == "record_loop_result"
        )
        self.assertIn(
            "correctable finding",
            result_tool["description"],
        )
        self.assertIn(
            "Required when outcome.status is BLOCKED",
            result_tool["inputSchema"]["properties"]["failure_class"][
                "description"
            ],
        )
        self.assertTrue(
            {
                "execute_sql",
                "query_sqlite",
                "write_projection",
                "refresh_projections",
            }.isdisjoint(names)
        )
        forbidden_arguments = {
            "sql",
            "query",
            "template",
            "filename",
            "content",
            "projection",
        }
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("sql", tool["name"].lower())
                self.assertNotIn(
                    "projection",
                    tool["name"].lower(),
                )
                self.assertTrue(
                    forbidden_arguments.isdisjoint(
                        tool["inputSchema"]["properties"]
                    )
                )
