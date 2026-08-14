from __future__ import annotations

from .scheduler_contracts_support import (
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    Path,
    ProjectRootBinding,
    TemporaryDirectory,
    handle_message,
    modern_meta,
    tool_definitions,
    validate_tool_arguments,
)


class McpSurfaceTestsPart5:
    def test_mcp_modern_rejects_missing_or_unsupported_metadata(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            missing = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "server/discover",
                    "params": {},
                },
                connection=connection,
            )
            self.assertEqual(missing["error"]["code"], -32602)

            unsupported = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "server/discover",
                    "params": {
                        "_meta": modern_meta(
                            version="2099-01-01",
                        )
                    },
                },
                connection=connection,
            )
            self.assertEqual(
                unsupported["error"]["code"],
                -32022,
            )
            self.assertEqual(
                unsupported["error"]["data"]["requested"],
                "2099-01-01",
            )
            self.assertEqual(
                unsupported["error"]["data"]["supported"][0],
                MODERN_PROTOCOL_VERSION,
            )

    def test_legacy_initialize_never_negotiates_modern_semantics(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MODERN_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "legacy-client",
                            "version": "1.0.0",
                        },
                    },
                },
                connection=connection,
            )
            self.assertEqual(
                response["result"]["protocolVersion"],
                LEGACY_PREFERRED_PROTOCOL_VERSION,
            )

    def test_modern_codex_project_root_is_per_request(self) -> None:
        with (
            TemporaryDirectory() as first,
            TemporaryDirectory() as second,
        ):
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(
                    None,
                    from_sandbox_meta=True,
                )
            )
            for request_id, root in enumerate((first, second), start=1):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "workspace_status",
                            "arguments": {},
                            "_meta": modern_meta(
                                **{
                                    "codex/sandbox-state-meta": {
                                        "sandboxCwd": Path(root).as_uri(),
                                    }
                                }
                            ),
                        },
                    },
                    connection=connection,
                )
                self.assertEqual(
                    response["result"]["structuredContent"]["result"][
                        "status"
                    ],
                    "ABSENT",
                )
            self.assertIsNone(connection.project_root.bound_root)

    def test_worker_telemetry_is_display_only_phase_reporting(self) -> None:
        tools = {tool["name"]: tool for tool in tool_definitions()}
        telemetry = tools["record_loop_result"]["inputSchema"][
            "properties"
        ]["outcome"]["properties"]["result"]["properties"][
            "workerTelemetry"
        ]["items"]
        self.assertEqual(
            telemetry["required"],
            ["phase", "agent", "model", "reasoningEffort"],
        )
        self.assertEqual(
            validate_tool_arguments(
                "record_loop_result",
                {
                    "root_id": "d-service",
                    "node_id": "loop:t-service",
                    "operation_id": "op-telemetry",
                    "outcome": {
                        "status": "SUCCEEDED",
                        "summary": "Loop completed.",
                        "result": {
                            "workerTelemetry": [
                                {
                                    "phase": "review",
                                    "agent": "unreported",
                                    "model": "unreported",
                                    "reasoningEffort": "unreported",
                                    "displayOnly": True,
                                    "nonAuthoritative": True,
                                }
                            ]
                        },
                    },
                },
            )["outcome"]["result"]["workerTelemetry"][0]["model"],
            "unreported",
        )
