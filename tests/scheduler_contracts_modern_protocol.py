from __future__ import annotations

from .scheduler_contracts_support import (
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    ProjectRootBinding,
    TemporaryDirectory,
    handle_message,
    modern_meta,
)


class McpModernProtocolTests:
    def test_mcp_modern_discovery_list_and_tool_call(self) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            discovery = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "discover",
                    "method": "server/discover",
                    "params": {"_meta": modern_meta()},
                },
                connection=connection,
            )
            discovered = discovery["result"]
            self.assertEqual(discovered["resultType"], "complete")
            self.assertEqual(
                discovered["supportedVersions"],
                [MODERN_PROTOCOL_VERSION, LEGACY_PREFERRED_PROTOCOL_VERSION],
            )
            self.assertEqual(discovered["cacheScope"], "private")
            self.assertGreater(discovered["ttlMs"], 0)
            self.assertEqual(
                discovered["_meta"]["io.modelcontextprotocol/serverInfo"][
                    "name"
                ],
                "delivery-graph",
            )
            self.assertFalse(connection.legacy_initialize_requested)

            listed = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "list",
                    "method": "tools/list",
                    "params": {"_meta": modern_meta()},
                },
                connection=connection,
            )
            self.assertEqual(listed["result"]["resultType"], "complete")
            self.assertEqual(len(listed["result"]["tools"]), 35)
            self.assertEqual(listed["result"]["cacheScope"], "private")

            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "call",
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {},
                        "_meta": modern_meta(client_version="1.0.1"),
                    },
                },
                connection=connection,
            )
            self.assertEqual(response["result"]["resultType"], "complete")
            self.assertEqual(
                response["result"]["structuredContent"]["result"]["status"],
                "ABSENT",
            )
            self.assertFalse(connection.legacy_initialized)
