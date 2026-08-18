from __future__ import annotations

from .mcp_apps_support import (
    CODEX_SANDBOX_META_KEY,
    GatedLoopError,
    McpConnection,
    ProjectRootBinding,
    TemporaryDirectory,
    handle_message,
    modern_meta,
    patch,
)


DASHBOARD_GRANT_TTL_SECONDS = 5 * 60
DASHBOARD_GRANT_LIMIT = 8


class McpAppsDashboardGrantTests:
    def _modern_meta_bound_codex(self) -> McpConnection:
        return McpConnection(
            project_root=ProjectRootBinding.from_startup(
                None,
                from_sandbox_meta=True,
            ),
            trusted_host_adapter="codex",
        )

    def _grant_modern_dashboard(
        self,
        connection: McpConnection,
        *,
        root: str,
        root_id: str = "d-dashboard",
        request_id: str = "initial-dashboard",
    ) -> dict[str, object]:
        return self._modern_request(
            connection,
            "tools/call",
            {
                "name": "open_delivery_dashboard",
                "arguments": {"root_id": root_id},
            },
            request_id=request_id,
            sandbox_root=root,
        )

    def test_modern_codex_apps_refresh_reuses_exact_session_grant(
        self,
    ) -> None:
        dashboard = {"rootId": "d-dashboard", "readOnly": True}
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value=dashboard,
        ) as call:
            connection = self._modern_meta_bound_codex()
            initial = self._grant_modern_dashboard(
                connection,
                root=root,
            )
            refreshed = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="apps-standard-refresh",
            )

        self.assertTrue(initial["result"]["structuredContent"]["ok"])
        self.assertTrue(refreshed["result"]["structuredContent"]["ok"])
        self.assertEqual(call.call_count, 2)
        first = call.call_args_list[0].kwargs
        second = call.call_args_list[1].kwargs
        self.assertEqual(second["root"], first["root"])
        self.assertEqual(
            second["workspace_root"],
            first["workspace_root"],
        )

    def test_modern_dashboard_grant_is_exact_to_root_id(self) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            response = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-other"},
                },
                request_id="ungranted-root-refresh",
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_modern_dashboard_grant_does_not_relax_other_tools(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            response = self._modern_request(
                connection,
                "tools/call",
                {"name": "workspace_status", "arguments": {}},
                request_id="metadata-free-other-tool",
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_modern_dashboard_grant_is_connection_local(self) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            authorized = self._modern_meta_bound_codex()
            other_connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(authorized, root=root)
            response = self._modern_request(
                other_connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="other-connection-refresh",
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_modern_dashboard_grant_is_bound_to_codex_host(self) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            connection.trusted_host_adapter = "claude-code"
            response = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="other-host-refresh",
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_modern_dashboard_grant_tracks_latest_authorized_workspace(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as first_root,
            TemporaryDirectory() as second_root,
            patch(
                "hdg.mcp_adapter.call_tool",
                return_value={"rootId": "d-dashboard", "readOnly": True},
            ) as call,
        ):
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(
                connection,
                root=first_root,
                request_id="first-workspace",
            )
            self._grant_modern_dashboard(
                connection,
                root=second_root,
                request_id="second-workspace",
            )
            refreshed = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="latest-workspace-refresh",
            )

        self.assertTrue(refreshed["result"]["structuredContent"]["ok"])
        first = call.call_args_list[0].kwargs
        second = call.call_args_list[1].kwargs
        third = call.call_args_list[2].kwargs
        self.assertNotEqual(
            first["workspace_root"],
            second["workspace_root"],
        )
        self.assertEqual(
            third["workspace_root"],
            second["workspace_root"],
        )

    def test_dashboard_grant_expires_and_is_removed(self) -> None:
        clock = [1000.0]
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter_common.time.monotonic",
            side_effect=lambda: clock[0],
        ), patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            clock[0] += DASHBOARD_GRANT_TTL_SECONDS + 1
            response = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="expired-refresh",
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(connection._dashboard_read_grants), 0)

    def test_successful_dashboard_refresh_slides_grant_expiry(self) -> None:
        clock = [1000.0]
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter_common.time.monotonic",
            side_effect=lambda: clock[0],
        ), patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            clock[0] += DASHBOARD_GRANT_TTL_SECONDS - 10
            renewed = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="renew-grant",
            )
            clock[0] += 20
            after_original_expiry = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="after-original-expiry",
            )

        self.assertTrue(renewed["result"]["structuredContent"]["ok"])
        self.assertTrue(
            after_original_expiry["result"]["structuredContent"]["ok"]
        )
        self.assertEqual(call.call_count, 3)

    def test_modern_malformed_sandbox_metadata_cannot_reuse_grant(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            request_meta = modern_meta()
            request_meta[CODEX_SANDBOX_META_KEY] = {}
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "malformed-sandbox-meta",
                    "method": "tools/call",
                    "params": {
                        "name": "open_delivery_dashboard",
                        "arguments": {"root_id": "d-dashboard"},
                        "_meta": request_meta,
                    },
                },
                connection=connection,
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "SANDBOX_METADATA_INVALID",
        )
        self.assertEqual(call.call_count, 1)

    def test_failed_modern_dashboard_read_does_not_create_grant(
        self,
    ) -> None:
        failure = GatedLoopError(
            "SCHEDULER_DELIVERY_NOT_FOUND",
            "Delivery is not available",
        )
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            side_effect=failure,
        ) as call:
            connection = self._modern_meta_bound_codex()
            failed = self._grant_modern_dashboard(connection, root=root)
            retry = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="ungranted-modern-refresh",
            )

        self.assertEqual(
            failed["result"]["structuredContent"]["error"]["code"],
            "SCHEDULER_DELIVERY_NOT_FOUND",
        )
        self.assertEqual(
            retry["result"]["structuredContent"]["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_dashboard_grants_are_bounded_and_evict_oldest(self) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            for index in range(DASHBOARD_GRANT_LIMIT + 1):
                self._grant_modern_dashboard(
                    connection,
                    root=root,
                    root_id=f"d-{index}",
                    request_id=f"grant-{index}",
                )
            evicted = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-0"},
                },
                request_id="evicted-refresh",
            )
            retained = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {
                        "root_id": f"d-{DASHBOARD_GRANT_LIMIT}"
                    },
                },
                request_id="retained-refresh",
            )

        self.assertFalse(evicted["result"]["structuredContent"]["ok"])
        self.assertTrue(retained["result"]["structuredContent"]["ok"])
        self.assertEqual(
            len(connection._dashboard_read_grants),
            DASHBOARD_GRANT_LIMIT,
        )
        self.assertEqual(call.call_count, DASHBOARD_GRANT_LIMIT + 2)

    def test_dashboard_grants_are_revoked_when_connection_closes(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._modern_meta_bound_codex()
            self._grant_modern_dashboard(connection, root=root)
            connection.close()
            response = self._modern_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="closed-session-refresh",
            )

        payload = response["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)
