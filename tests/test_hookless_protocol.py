from __future__ import annotations

from dataclasses import fields
from inspect import Parameter, signature
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hdg.graph_runtime as graph_runtime
from hdg.controller import ControllerContext
from hdg.host_policy import ProjectRootBinding
from hdg.mcp_adapter import (
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    McpConnection,
    handle_message,
)
from hdg.mcp_tools import call_tool, tool_definitions
from hdg.repository import SchedulerRepository
from hdg.storage_schema import SCHEDULER_STATE_CONTRACT


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "delivery-graph"


class HooklessProtocolTests(unittest.TestCase):
    def test_plugin_bundle_has_no_lifecycle_hooks(self) -> None:
        codex_manifest = json.loads(
            (PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        claude_manifest = json.loads(
            (PLUGIN / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertNotIn("hooks", codex_manifest)
        self.assertNotIn("hooks", claude_manifest)
        self.assertFalse((PLUGIN / "hooks").exists())

    def test_mcp_surface_has_no_hook_private_inputs(self) -> None:
        tools = {item["name"]: item for item in tool_definitions()}

        self.assertNotIn("claim_current_task", tools)
        for tool in tools.values():
            properties = tool["inputSchema"].get("properties", {})
            self.assertFalse(
                {name for name in properties if name.startswith("_host_")},
                tool["name"],
            )
        dispatch = tools["dispatch_loop"]["inputSchema"]
        self.assertNotIn("receiver_attestation_id", dispatch["properties"])
        self.assertIn("operation_id", dispatch["required"])
        self.assertIn("receiver_context_id", dispatch["required"])
        for name in (
            "heartbeat_loop",
            "report_loop_progress",
            "pause_loop",
            "record_loop_result",
        ):
            self.assertIn(
                "operation_id",
                tools[name]["inputSchema"]["required"],
            )

    def test_call_tool_rejects_removed_hook_context(self) -> None:
        parameters = signature(call_tool).parameters
        removed = {
            "host_hook_attested",
            "host_receiver_operation_attested",
            "host_session_attested",
            "host_session_context_id",
            "host_session_role",
        }
        self.assertTrue(removed.isdisjoint(parameters))
        self.assertNotIn(
            Parameter.VAR_KEYWORD,
            {parameter.kind for parameter in parameters.values()},
        )

    def test_mcp_adapter_forwards_only_bound_host_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(directory),
                trusted_host_adapter="codex",
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "codex",
                            "version": "test",
                        },
                    },
                },
                connection=connection,
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                connection=connection,
            )
            with patch(
                "hdg.mcp_adapter.call_tool",
                return_value={"status": "ABSENT"},
            ) as mocked_call:
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "workspace-status",
                        "method": "tools/call",
                        "params": {
                            "name": "workspace_status",
                            "arguments": {},
                        },
                    },
                    connection=connection,
                )

        self.assertTrue(response["result"]["structuredContent"]["ok"])
        mocked_call.assert_called_once()
        self.assertEqual(mocked_call.call_args.args, ("workspace_status", {}))
        self.assertEqual(
            set(mocked_call.call_args.kwargs),
            {
                "root",
                "workspace_root",
                "explicit_dogfood",
                "client_info",
                "trusted_host_adapter",
            },
        )
        self.assertEqual(
            mocked_call.call_args.kwargs["trusted_host_adapter"],
            "codex",
        )
        self.assertEqual(
            mocked_call.call_args.kwargs["root"],
            mocked_call.call_args.kwargs["workspace_root"],
        )

    def test_runtime_and_controller_expose_no_hook_attestation_api(self) -> None:
        removed_runtime_names = {
            "attest_loop_receiver",
            "authorize_claude_subagent_operation",
            "authorize_codex_subagent_operation",
            "authorize_host_session_operation",
            "claim_codex_subagent_receiver",
            "claim_current_task",
        }
        for name in removed_runtime_names:
            self.assertFalse(hasattr(graph_runtime, name), name)

        context_fields = {field.name for field in fields(ControllerContext)}
        self.assertFalse(
            {
                "host_hook_attested",
                "host_receiver_operation_attested",
                "host_session_attested",
                "host_session_context_id",
                "host_session_role",
            }
            & context_fields
        )

    def test_scheduler_schema_has_no_legacy_coordination_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SchedulerRepository(directory)
            with repository.transaction():
                pass
            with repository.read() as connection:
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                state_contract = connection.execute(
                    "SELECT value FROM scheduler_metadata WHERE key = ?",
                    ("state_contract",),
                ).fetchone()["value"]

        self.assertIn("dispatch_reservations", tables)
        self.assertEqual(
            SCHEDULER_STATE_CONTRACT,
            "schema-v3-graph-compiler-v1",
        )
        self.assertEqual(state_contract, SCHEDULER_STATE_CONTRACT)
        self.assertFalse(
            {
                "host_workspace_attestations",
                "receiver_attestations",
                "host_receiver_identities",
                "run_receiver_roots",
                "worktree_setup_reservations",
            }
            & tables
        )


if __name__ == "__main__":
    unittest.main()
