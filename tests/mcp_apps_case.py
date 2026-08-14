from __future__ import annotations

from .mcp_apps_support import (
    CODEX_SANDBOX_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    McpConnection,
    Path,
    ProjectRootBinding,
    handle_message,
    modern_meta,
)


class McpAppsContractTestsSupport:
    def _connection(self, root: str) -> McpConnection:
        return McpConnection(
            project_root=ProjectRootBinding.from_startup(root)
        )

    def _initialize_legacy(self, root: str) -> McpConnection:
        connection = self._connection(root)
        initialized = handle_message(
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "legacy-mcp-apps-contract-test",
                        "version": "1.0.0",
                    },
                },
            },
            connection=connection,
        )
        self.assertEqual(
            initialized["result"]["protocolVersion"],
            LEGACY_PREFERRED_PROTOCOL_VERSION,
        )
        notification = handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            connection=connection,
        )
        self.assertIsNone(notification)
        return connection

    def _initialize_meta_bound_codex(self) -> McpConnection:
        connection = McpConnection(
            project_root=ProjectRootBinding.from_startup(
                None,
                from_sandbox_meta=True,
            ),
            trusted_host_adapter="codex",
        )
        initialized = handle_message(
            {
                "jsonrpc": "2.0",
                "id": "initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "codex-mcp-apps-contract-test",
                        "version": "1.0.0",
                    },
                },
            },
            connection=connection,
        )
        self.assertEqual(
            initialized["result"]["protocolVersion"],
            LEGACY_PREFERRED_PROTOCOL_VERSION,
        )
        self.assertIsNone(handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            connection=connection,
        ))
        return connection

    @staticmethod
    def _sandbox_meta(root: str) -> dict[str, object]:
        return {
            CODEX_SANDBOX_META_KEY: {
                "sandboxCwd": Path(root).resolve().as_uri(),
            }
        }

    def _modern_request(
        self,
        connection: McpConnection,
        method: str,
        params: dict[str, object] | None = None,
        *,
        request_id: str = "modern-request",
    ) -> dict[str, object]:
        request_params = dict(params or {})
        request_params["_meta"] = modern_meta()
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": request_params,
            },
            connection=connection,
        )
        self.assertIsInstance(response, dict)
        assert isinstance(response, dict)
        return response

    def _legacy_request(
        self,
        connection: McpConnection,
        method: str,
        params: dict[str, object] | None = None,
        *,
        request_id: str = "legacy-request",
    ) -> dict[str, object]:
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params or {}),
            },
            connection=connection,
        )
        self.assertIsInstance(response, dict)
        assert isinstance(response, dict)
        return response
