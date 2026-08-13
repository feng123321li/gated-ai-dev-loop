from __future__ import annotations

import unittest

from scripts.mcp_registration_probe import (
    build_registration_matrix,
    lifecycle_index,
    model_io_observation,
    strict_matrix_passes,
)


PREFIX = "mcp__plugin_delivery-graph_delivery-graph__"


def model_entry(
    tool_names: list[str] | None,
    *,
    session_id: str = "sess-main",
    role: str = "main",
    completed_at: str = "2026-08-13T08:00:00Z",
) -> dict[str, object]:
    body: dict[str, object] = {}
    if tool_names is not None:
        body["tools"] = [{"name": name} for name in tool_names]
    return {
        "type": "model_io",
        "sessionId": session_id,
        "turnId": "turn-1",
        "completedAt": completed_at,
        "model": {"role": role, "modelId": "test-model"},
        "request": {"body": body},
    }


class McpRegistrationProbeTests(unittest.TestCase):
    def test_complete_registration_is_reported_without_calling_tools(self) -> None:
        names = [f"{PREFIX}tool_{index}" for index in range(3)]
        observation = model_io_observation(
            model_entry(["shell", *names]),
            source="model-io.jsonl",
            host="zcode",
            tool_prefix=PREFIX,
            expected_count=3,
            workspace_index={"sess-main": "G:/workspace/alpha"},
        )

        self.assertEqual(observation["status"], "REGISTERED")
        self.assertEqual(observation["matchingToolCount"], 3)
        self.assertEqual(observation["workspace"], "G:/workspace/alpha")
        self.assertFalse(observation["mcpToolCallAttempted"])

    def test_missing_and_partial_registration_are_distinct(self) -> None:
        missing = model_io_observation(
            model_entry(["shell", "apply_patch"]),
            source="missing.jsonl",
            host="zcode",
            tool_prefix=PREFIX,
            expected_count=3,
            workspace_index={},
        )
        partial = model_io_observation(
            model_entry([f"{PREFIX}one"]),
            source="partial.jsonl",
            host="zcode",
            tool_prefix=PREFIX,
            expected_count=3,
            workspace_index={},
        )

        self.assertEqual(missing["status"], "PLUGIN_MCP_UNAVAILABLE")
        self.assertEqual(partial["status"], "PARTIAL_REGISTRATION")

    def test_equal_count_with_wrong_tool_name_is_partial(self) -> None:
        expected = [f"{PREFIX}one", f"{PREFIX}two"]
        observation = model_io_observation(
            model_entry([f"{PREFIX}one", f"{PREFIX}unexpected"]),
            source="wrong-name.jsonl",
            host="zcode",
            tool_prefix=PREFIX,
            expected_count=2,
            expected_names=expected,
            workspace_index={},
        )

        self.assertEqual(observation["status"], "PARTIAL_REGISTRATION")
        self.assertEqual(observation["missingToolNames"], [f"{PREFIX}two"])
        self.assertEqual(
            observation["unexpectedToolNames"],
            [f"{PREFIX}unexpected"],
        )

    def test_request_without_tool_surface_is_not_a_false_failure(self) -> None:
        observation = model_io_observation(
            model_entry(None, role="lite"),
            source="lite.jsonl",
            host="zcode",
            tool_prefix=PREFIX,
            expected_count=3,
            workspace_index={},
        )

        self.assertEqual(observation["status"], "NOT_OBSERVABLE")
        self.assertFalse(observation["toolSurfacePresent"])

    def test_lifecycle_events_correlate_session_and_workspace(self) -> None:
        entries = [
            {
                "event": "mcp.server.connect.started",
                "timestamp": "2026-08-13T08:00:00Z",
                "context": {
                    "sessionId": "sess-main",
                    "workspaceKey": "G:/workspace/alpha",
                    "mcpServerName": "plugin:delivery-graph:delivery-graph",
                },
            },
            {
                "event": "mcp.server.failed",
                "timestamp": "2026-08-13T08:00:01Z",
                "context": {
                    "sessionId": "sess-main",
                    "workspaceKey": "G:/workspace/alpha",
                    "mcpServerName": "plugin:delivery-graph:delivery-graph",
                    "exitCode": 1,
                    "stderr": "redacted example",
                },
            },
        ]

        indexed = lifecycle_index(
            entries,
            server_name="plugin:delivery-graph:delivery-graph",
        )

        self.assertEqual(indexed["workspaceBySession"]["sess-main"], "G:/workspace/alpha")
        self.assertEqual(indexed["eventsBySession"]["sess-main"][-1]["stage"], "FAILED")
        self.assertEqual(indexed["eventsBySession"]["sess-main"][-1]["exitCode"], 1)

    def test_matrix_keeps_agent_roles_separate_and_uses_latest_observation(self) -> None:
        entries = [
            model_entry(
                ["shell"],
                role="main",
                completed_at="2026-08-13T08:00:00Z",
            ),
            model_entry(
                [f"{PREFIX}one", f"{PREFIX}two"],
                role="main",
                completed_at="2026-08-13T08:01:00Z",
            ),
            model_entry(None, role="lite", completed_at="2026-08-13T08:02:00Z"),
        ]

        matrix = build_registration_matrix(
            entries,
            source="model-io.jsonl",
            host="zcode",
            tool_prefix=PREFIX,
            expected_count=2,
            lifecycle={
                "workspaceBySession": {"sess-main": "G:/workspace/alpha"},
                "eventsBySession": {},
            },
        )

        self.assertEqual(matrix["summary"]["registered"], 1)
        self.assertEqual(matrix["summary"]["notObservable"], 1)
        self.assertEqual(len(matrix["cases"]), 2)
        main = next(item for item in matrix["cases"] if item["agentRole"] == "main")
        self.assertEqual(main["status"], "REGISTERED")
        self.assertEqual(main["observedAt"], "2026-08-13T08:01:00Z")

    def test_strict_gate_requires_every_case_to_be_registered(self) -> None:
        healthy = {
            "summary": {
                "cases": 2,
                "registered": 2,
                "unavailable": 0,
                "partial": 0,
                "notObservable": 0,
            }
        }
        not_observable = {
            "summary": {
                "cases": 2,
                "registered": 1,
                "unavailable": 0,
                "partial": 0,
                "notObservable": 1,
            }
        }

        self.assertTrue(strict_matrix_passes(healthy))
        self.assertFalse(strict_matrix_passes(not_observable))


if __name__ == "__main__":
    unittest.main()
