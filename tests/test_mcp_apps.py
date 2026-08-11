from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg.errors import GatedLoopError
from hdg.host_policy import CODEX_SANDBOX_META_KEY, ProjectRootBinding
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PROTOCOL_VERSION_META_KEY,
    handle_message,
)
from hdg.mcp_tools import tool_definitions


DASHBOARD_RESOURCE_URI = "ui://delivery-graph/dashboard-v2.html"
DASHBOARD_MIME_TYPE = "text/html;profile=mcp-app"

EXISTING_TOOL_NAMES = (
    "workspace_status",
    "hierarchy_contract",
    "preview_hierarchy",
    "confirm_development_baseline",
    "select_execution_mode",
    "resume_execution_mode",
    "create_manual_handoff",
    "start_manual_handoff",
    "prepare_hierarchy",
    "prepare_delivery_revision",
    "delivery_revision_history",
    "plan_dispatch_batch",
    "freeze_hierarchy",
    "graph_frontier",
    "unfreeze_task_requirement",
    "refreeze_task_requirement",
    "graph_status",
    "graph_events",
    "advance_graph",
    "rebuild_graph_run",
    "loop_context",
    "dispatch_loop",
    "handoff_ready_automatic_task",
    "heartbeat_loop",
    "report_loop_progress",
    "pause_loop",
    "resume_loop",
    "record_loop_result",
    "record_user_confirmation",
    "cancel_graph_run",
    "archive_delivery",
)


def modern_meta() -> dict[str, object]:
    return {
        PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {
            "name": "mcp-apps-contract-test",
            "version": "1.0.0",
        },
    }


class McpAppsContractTests(unittest.TestCase):
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

    def test_modern_and_legacy_advertise_static_resources(self) -> None:
        with TemporaryDirectory() as root:
            modern_connection = self._connection(root)
            discovered = self._modern_request(
                modern_connection,
                "server/discover",
            )
            modern_capabilities = discovered["result"]["capabilities"]
            self.assertIn("resources", modern_capabilities)
            self.assertEqual(
                modern_capabilities["resources"],
                {"subscribe": False, "listChanged": False},
            )

            legacy_connection = self._connection(root)
            initialized = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": (
                            LEGACY_PREFERRED_PROTOCOL_VERSION
                        ),
                        "capabilities": {},
                        "clientInfo": {
                            "name": "legacy-mcp-apps-contract-test",
                            "version": "1.0.0",
                        },
                    },
                },
                connection=legacy_connection,
            )
            self.assertEqual(
                initialized["result"]["capabilities"]["resources"],
                {"subscribe": False, "listChanged": False},
            )

    def test_resources_list_is_shared_by_modern_and_legacy(self) -> None:
        with TemporaryDirectory() as root:
            modern = self._modern_request(
                self._connection(root),
                "resources/list",
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "resources/list",
            )

        self.assertIn("result", modern)
        self.assertIn("result", legacy)
        modern_result = modern["result"]
        legacy_result = legacy["result"]
        self.assertEqual(modern_result["resultType"], "complete")
        self.assertEqual(
            set(modern_result),
            {"resources", "ttlMs", "cacheScope", "resultType", "_meta"},
        )
        self.assertEqual(set(legacy_result), {"resources"})
        self.assertGreater(modern_result["ttlMs"], 0)
        self.assertEqual(modern_result["cacheScope"], "private")
        self.assertEqual(
            modern_result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "delivery-graph",
        )
        self.assertEqual(
            modern_result["resources"],
            legacy_result["resources"],
        )
        self.assertEqual(len(modern_result["resources"]), 1)
        resource = modern_result["resources"][0]
        self.assertEqual(resource["uri"], DASHBOARD_RESOURCE_URI)
        self.assertEqual(resource["mimeType"], DASHBOARD_MIME_TYPE)
        self.assertIsInstance(resource.get("name"), str)
        self.assertTrue(resource["name"].strip())

    def test_shared_dispatcher_preserves_tools_list_envelopes(self) -> None:
        with TemporaryDirectory() as root:
            modern = self._modern_request(
                self._connection(root),
                "tools/list",
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "tools/list",
            )

        self.assertEqual(
            modern["result"]["tools"],
            legacy["result"]["tools"],
        )
        self.assertEqual(
            set(modern["result"]),
            {"tools", "ttlMs", "cacheScope", "resultType", "_meta"},
        )
        self.assertEqual(set(legacy["result"]), {"tools"})

    def test_modern_only_call_fields_do_not_leak_into_legacy(self) -> None:
        call_params = {
            "name": "workspace_status",
            "arguments": {},
            "inputResponses": {},
            "requestState": "dashboard-refresh",
        }
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"status": "ABSENT"},
        ) as call:
            modern = self._modern_request(
                self._connection(root),
                "tools/call",
                call_params,
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "tools/call",
                call_params,
            )

        self.assertIn("result", modern)
        self.assertEqual(legacy["error"]["code"], -32602)
        call.assert_called_once()

    def test_resources_read_returns_one_shared_self_contained_view(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            modern = self._modern_request(
                self._connection(root),
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )

        self.assertIn("result", modern)
        self.assertIn("result", legacy)
        modern_result = modern["result"]
        legacy_result = legacy["result"]
        self.assertEqual(modern_result["resultType"], "complete")
        self.assertEqual(
            set(modern_result),
            {"contents", "ttlMs", "cacheScope", "resultType", "_meta"},
        )
        self.assertEqual(set(legacy_result), {"contents"})
        self.assertGreater(modern_result["ttlMs"], 0)
        self.assertEqual(modern_result["cacheScope"], "private")
        self.assertEqual(
            modern_result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "delivery-graph",
        )
        self.assertEqual(
            modern_result["contents"],
            legacy_result["contents"],
        )
        self.assertEqual(len(modern_result["contents"]), 1)
        content = modern_result["contents"][0]
        self.assertEqual(content["uri"], DASHBOARD_RESOURCE_URI)
        self.assertEqual(content["mimeType"], DASHBOARD_MIME_TYPE)
        self.assertIsInstance(content.get("text"), str)
        self.assertIn("<html", content["text"].lower())

    def test_dashboard_view_is_self_contained_and_read_only(self) -> None:
        with TemporaryDirectory() as root:
            response = self._modern_request(
                self._connection(root),
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )

        content = response["result"]["contents"][0]
        html = content["text"]
        metadata = content["_meta"]
        self.assertIn('method === "ui/notifications/tool-result"', html)
        self.assertIn('message.method === "ui/resource-teardown"', html)
        self.assertIn('message.method === "ui/notifications/tool-cancelled"', html)
        self.assertIn("callResult.isError !== true", html)
        self.assertIn("structured?.ok !== false", html)
        self.assertIn("RPC_TIMEOUT_MS", html)
        self.assertIn("AUTO_REFRESH_MS = 15000", html)
        self.assertIn("const scheduleAutoRefresh =", html)
        self.assertIn("refreshInFlight", html)
        self.assertIn("document.addEventListener(", html)
        self.assertIn('"visibilitychange"', html)
        self.assertNotIn("setInterval(", html)
        self.assertIn('request("ui/initialize"', html)
        self.assertIn('version: "1.1.0"', html)
        self.assertIn('name: "open_delivery_dashboard"', html)
        self.assertIn("window.openai?.callTool", html)
        self.assertIn("const callStandardDashboard = async", html)
        self.assertIn("const callCompatibilityDashboard = async", html)
        call_dashboard = html.split(
            "const callDashboard = async",
            1,
        )[1].split(
            "refreshButton.addEventListener",
            1,
        )[0]
        self.assertIn(
            "const response = await callStandardDashboard(rootId)",
            call_dashboard,
        )
        self.assertIn("catch (error)", call_dashboard)
        self.assertIn(
            'dashboardErrorCode(response) !== "PROJECT_ROOT_UNAVAILABLE"',
            call_dashboard,
        )
        self.assertIn(
            "preferCompatibilityCalls = true",
            call_dashboard,
        )
        self.assertIn(
            "return callCompatibilityDashboard(rootId)",
            call_dashboard,
        )
        fallback_catch = call_dashboard.split(
            "catch (error)",
            1,
        )[1]
        self.assertIn(
            "return callCompatibilityDashboard(rootId)",
            fallback_catch,
        )
        self.assertIn("--on-accent", html)
        self.assertIn(
            '["SUCCEEDED", "COMPLETED"].includes(node.status)',
            html,
        )
        self.assertIn("只读", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("fetch(", html)
        graph_viewport = html.split(
            ".graph-viewport {",
            1,
        )[1].split(
            ".edge-layer",
            1,
        )[0]
        self.assertNotIn("overflow: auto", graph_viewport)
        self.assertIn("overflow-x: clip", graph_viewport)
        self.assertNotIn("min-width: max-content", html)
        self.assertNotIn(
            "repeat(${maxRank + 1}, 190px)",
            html,
        )
        self.assertNotIn(".edge-layer { display: none; }", html)
        self.assertNotIn(
            'window.matchMedia("(max-width: 719px)").matches',
            html,
        )
        self.assertIn('className = "graph-rank"', html)
        self.assertIn('nodeLayer.dataset.layout = "vertical"', html)
        self.assertIn('nodeLayer.dataset.layout = "horizontal"', html)
        self.assertIn(
            "minmax(min(100%, 180px), 1fr)",
            html,
        )
        self.assertIn('dependency.className = "node-dependencies"', html)
        self.assertIn(
            'edgeLayer.hidden = nodeLayer.dataset.layout === "vertical"',
            html,
        )
        self.assertIn('"前置："', html)
        for write_tool in (
            "advance_graph",
            "archive_delivery",
            "cancel_graph_run",
            "dispatch_loop",
            "record_loop_result",
        ):
            self.assertNotIn(write_tool, html)
        self.assertEqual(
            metadata["ui"]["csp"],
            {"connectDomains": [], "resourceDomains": []},
        )

    def test_resources_reject_invalid_params_in_both_protocols(self) -> None:
        invalid_cases = (
            ("resources/list", {"unexpected": True}),
            ("resources/read", {}),
            ("resources/read", {"uri": None}),
            (
                "resources/read",
                {
                    "uri": DASHBOARD_RESOURCE_URI,
                    "unexpected": True,
                },
            ),
        )
        with TemporaryDirectory() as root:
            modern_connection = self._connection(root)
            legacy_connection = self._initialize_legacy(root)
            for method, params in invalid_cases:
                with self.subTest(
                    protocol="modern",
                    method=method,
                    params=params,
                ):
                    response = self._modern_request(
                        modern_connection,
                        method,
                        params,
                    )
                    self.assertEqual(response["error"]["code"], -32602)
                with self.subTest(
                    protocol="legacy",
                    method=method,
                    params=params,
                ):
                    response = self._legacy_request(
                        legacy_connection,
                        method,
                        params,
                    )
                    self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_resource_uses_versioned_not_found_error(self) -> None:
        unknown_uri = "ui://delivery-graph/unknown.html"
        with TemporaryDirectory() as root:
            modern = self._modern_request(
                self._connection(root),
                "resources/read",
                {"uri": unknown_uri},
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "resources/read",
                {"uri": unknown_uri},
            )

        self.assertEqual(modern["error"]["code"], -32602)
        self.assertEqual(legacy["error"]["code"], -32002)
        for response in (modern, legacy):
            self.assertNotIn("result", response)
            self.assertEqual(
                response["error"]["message"],
                "Resource not found",
            )
            self.assertEqual(
                response["error"]["data"]["uri"],
                unknown_uri,
            )

    def test_unavailable_resource_is_a_generic_internal_error(self) -> None:
        unavailable = GatedLoopError(
            "MCP_RESOURCE_UNAVAILABLE",
            "Private asset path G:/private/dashboard.html",
            details={"path": "G:/private/dashboard.html"},
        )
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.read_resource",
            side_effect=unavailable,
        ):
            modern = self._modern_request(
                self._connection(root),
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )

        for response in (modern, legacy):
            self.assertEqual(response["error"], {
                "code": -32603,
                "message": "Internal error",
            })
            self.assertNotIn("private", json.dumps(response).lower())

    def test_unexpected_resource_error_is_isolated_and_correlated(self) -> None:
        stderr = io.StringIO()
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.read_resource",
            side_effect=[
                RuntimeError("token=private-resource-secret"),
                {"contents": []},
            ],
        ), redirect_stderr(stderr):
            connection = self._connection(root)
            failed = self._modern_request(
                connection,
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )
            recovered = self._modern_request(
                connection,
                "resources/read",
                {"uri": DASHBOARD_RESOURCE_URI},
            )

        self.assertEqual(failed["error"]["code"], -32603)
        self.assertEqual(failed["error"]["message"], "Internal error")
        self.assertEqual(set(failed["error"]["data"]), {"diagnosticId"})
        self.assertEqual(recovered["result"]["contents"], [])
        diagnostic = json.loads(stderr.getvalue())
        self.assertEqual(diagnostic["operation"], "resources/read")
        self.assertEqual(
            diagnostic["diagnosticId"],
            failed["error"]["data"]["diagnosticId"],
        )
        self.assertNotIn("private-resource-secret", stderr.getvalue())

    def test_stdio_connection_rejects_cross_era_requests(self) -> None:
        with TemporaryDirectory() as root:
            modern_connection = self._connection(root)
            discovered = self._modern_request(
                modern_connection,
                "server/discover",
            )
            self.assertIn("result", discovered)

            legacy_ping = self._legacy_request(
                modern_connection,
                "ping",
            )
            self.assertEqual(legacy_ping["error"]["code"], -32022)
            initialized_notification = handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                connection=modern_connection,
            )
            self.assertIsNone(initialized_notification)
            self.assertFalse(modern_connection.legacy_initialized)
            self.assertIn(
                "result",
                self._modern_request(modern_connection, "tools/list"),
            )

            legacy_connection = self._initialize_legacy(root)
            modern_list = self._modern_request(
                legacy_connection,
                "tools/list",
            )
            self.assertEqual(modern_list["error"]["code"], -32022)
            self.assertIn(
                "result",
                self._legacy_request(legacy_connection, "tools/list"),
            )

    def test_legacy_ping_pins_the_stdio_connection_era(self) -> None:
        with TemporaryDirectory() as root:
            connection = self._connection(root)
            ping = self._legacy_request(connection, "ping")
            self.assertEqual(ping["result"], {})

            modern = self._modern_request(connection, "server/discover")
            self.assertEqual(modern["error"]["code"], -32022)

            initialized = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "initialize-after-ping",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "legacy-after-ping",
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

    def test_legacy_initialize_gate_cannot_be_bypassed_by_modern_meta(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
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
                            "name": "legacy-gate-test",
                            "version": "1.0.0",
                        },
                    },
                },
                connection=connection,
            )
            self.assertIn("result", initialized)

            before_notification = self._legacy_request(
                connection,
                "tools/list",
            )
            self.assertEqual(before_notification["error"]["code"], -32002)
            cross_era = self._modern_request(connection, "tools/list")
            self.assertEqual(cross_era["error"]["code"], -32022)

            self.assertIsNone(handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {"_meta": modern_meta()},
                },
                connection=connection,
            ))
            still_gated = self._legacy_request(connection, "tools/list")
            self.assertEqual(still_gated["error"]["code"], -32002)

            self.assertIsNone(handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                connection=connection,
            ))
            self.assertIn(
                "result",
                self._legacy_request(connection, "tools/list"),
            )

    def test_dashboard_tool_is_additive_and_read_only(self) -> None:
        tools = tool_definitions()
        tools_by_name = {tool["name"]: tool for tool in tools}
        names_without_dashboard = tuple(
            tool["name"]
            for tool in tools
            if tool["name"] != "open_delivery_dashboard"
        )
        self.assertEqual(names_without_dashboard, EXISTING_TOOL_NAMES)
        self.assertIn(
            "open_delivery_dashboard",
            tuple(tools_by_name),
        )

        dashboard = tools_by_name["open_delivery_dashboard"]
        self.assertEqual(
            dashboard["_meta"]["ui"]["resourceUri"],
            DASHBOARD_RESOURCE_URI,
        )
        self.assertEqual(
            dashboard["annotations"],
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        )
        schema = dashboard["inputSchema"]
        self.assertEqual(schema["required"], ["root_id"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["root_id"]["type"],
            "string",
        )

    def test_dashboard_tool_keeps_text_and_structured_fallbacks(self) -> None:
        dashboard = {
            "rootId": "d-dashboard",
            "readOnly": True,
            "status": "RUNNING",
        }
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value=dashboard,
        ) as call:
            modern = self._modern_request(
                self._connection(root),
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
            )
            legacy = self._legacy_request(
                self._initialize_legacy(root),
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
            )

        self.assertEqual(call.call_count, 2)
        for response, modern_result in (
            (modern, True),
            (legacy, False),
        ):
            result = response["result"]
            self.assertEqual(
                result["structuredContent"],
                {"ok": True, "result": dashboard},
            )
            self.assertEqual(
                json.loads(result["content"][0]["text"]),
                result["structuredContent"],
            )
            self.assertFalse(result["isError"])
            self.assertEqual(
                "resultType" in result,
                modern_result,
            )

    def test_legacy_codex_dashboard_refresh_reuses_exact_bound_workspace(
        self,
    ) -> None:
        dashboard = {"rootId": "d-dashboard", "readOnly": True}
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value=dashboard,
        ) as call:
            connection = self._initialize_meta_bound_codex()
            initial = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                    "_meta": self._sandbox_meta(root),
                },
                request_id="initial-dashboard",
            )
            refreshed = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="embedded-refresh",
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

    def test_legacy_codex_dashboard_refresh_requires_same_root_grant(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._initialize_meta_bound_codex()
            unbound = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="unbound-dashboard",
            )
            self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                    "_meta": self._sandbox_meta(root),
                },
                request_id="grant-dashboard",
            )
            different_root = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-other"},
                },
                request_id="different-dashboard",
            )

        for response in (unbound, different_root):
            payload = response["result"]["structuredContent"]
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"]["code"],
                "PROJECT_ROOT_UNAVAILABLE",
            )
        self.assertEqual(call.call_count, 1)

    def test_legacy_codex_dashboard_grant_does_not_relax_other_tools(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._initialize_meta_bound_codex()
            self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                    "_meta": self._sandbox_meta(root),
                },
                request_id="grant-dashboard",
            )
            workspace_status = self._legacy_request(
                connection,
                "tools/call",
                {"name": "workspace_status", "arguments": {}},
                request_id="metadata-free-other-tool",
            )

        payload = workspace_status["result"]["structuredContent"]
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_dashboard_refresh_does_not_treat_empty_meta_as_bridge_omission(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._initialize_meta_bound_codex()
            self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                    "_meta": self._sandbox_meta(root),
                },
                request_id="grant-dashboard",
            )
            responses = [
                self._legacy_request(
                    connection,
                    "tools/call",
                    {
                        "name": "open_delivery_dashboard",
                        "arguments": {"root_id": "d-dashboard"},
                        "_meta": explicit_meta,
                    },
                    request_id=f"explicit-meta-{index}",
                )
                for index, explicit_meta in enumerate((None, {}))
            ]

        for response in responses:
            payload = response["result"]["structuredContent"]
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"]["code"],
                "PROJECT_ROOT_UNAVAILABLE",
            )
        self.assertEqual(call.call_count, 1)

    def test_failed_dashboard_read_does_not_create_refresh_grant(
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
            connection = self._initialize_meta_bound_codex()
            failed = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                    "_meta": self._sandbox_meta(root),
                },
                request_id="failed-dashboard",
            )
            retry = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="ungranted-refresh",
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

    def test_non_codex_connection_cannot_reuse_dashboard_grant(
        self,
    ) -> None:
        with TemporaryDirectory() as root, patch(
            "hdg.mcp_adapter.call_tool",
            return_value={"rootId": "d-dashboard", "readOnly": True},
        ) as call:
            connection = self._initialize_meta_bound_codex()
            self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                    "_meta": self._sandbox_meta(root),
                },
                request_id="claude-dashboard",
            )
            connection.trusted_host_adapter = "claude-code"
            refreshed = self._legacy_request(
                connection,
                "tools/call",
                {
                    "name": "open_delivery_dashboard",
                    "arguments": {"root_id": "d-dashboard"},
                },
                request_id="claude-refresh",
            )

        self.assertEqual(
            refreshed["result"]["structuredContent"]["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )
        self.assertEqual(call.call_count, 1)

    def test_dashboard_projection_is_read_only_and_data_minimized(
        self,
    ) -> None:
        from hdg.dashboard import open_delivery_dashboard

        definition = {
            "rootId": "d-dashboard",
            "deliveryRevision": 2,
            "status": "FROZEN",
            "updatedAt": "2026-08-08T08:00:00Z",
            "hierarchy": {
                "delivery": {
                    "id": "d-dashboard",
                    "title": "Dashboard delivery",
                    "summary": "Read-only dashboard projection.",
                }
            },
            "graph": {
                "nodes": [
                    {
                        "id": "loop:t-api",
                        "kind": "TASK_LOOP",
                        "workItemId": "t-api",
                        "loop": {"secretInput": "must-not-leak"},
                    },
                    {
                        "id": "confirm:d-dashboard",
                        "kind": "USER_CONFIRMATION",
                        "workItemId": "d-dashboard",
                    },
                ],
                "edges": [
                    {
                        "source": "loop:t-api",
                        "target": "confirm:d-dashboard",
                    }
                ],
            },
        }
        status = {
            "rootId": "d-dashboard",
            "runId": "d-dashboard-r2",
            "status": "RUNNING",
            "executionMode": "automatic",
            "startedAt": "2026-08-08T07:00:00Z",
            "updatedAt": "2026-08-08T08:00:00Z",
            "completedAt": None,
            "nodes": [
                {
                    "nodeId": "loop:t-api",
                    "kind": "TASK_LOOP",
                    "workItemId": "t-api",
                    "status": "CLAIMED",
                    "attempt": 1,
                    "operationId": "must-not-leak",
                    "monitor": {"health": "HEALTHY"},
                },
                {
                    "nodeId": "confirm:d-dashboard",
                    "kind": "USER_CONFIRMATION",
                    "workItemId": "d-dashboard",
                    "status": "PENDING",
                    "attempt": 0,
                },
            ],
            "progressMonitor": {
                "observedAt": "2026-08-08T08:00:00Z",
                "recommendedPollSeconds": 10,
                "alerts": [],
                "rows": [],
                "markdownTable": "| node | status |",
            },
        }
        history = {
            "rootId": "d-dashboard",
            "currentRevision": 2,
            "revisions": [
                {
                    "revision": 1,
                    "status": "SUPERSEDED",
                    "runId": "d-dashboard-r1",
                    "runStatus": "SUPERSEDED",
                    "reason": "private reason",
                    "requestedBy": "private actor",
                    "createdAt": "2026-08-07T08:00:00Z",
                    "updatedAt": "2026-08-07T09:00:00Z",
                    "frozenAt": "2026-08-07T08:10:00Z",
                    "completedAt": None,
                    "cancelledAt": None,
                    "supersededAt": "2026-08-07T09:00:00Z",
                }
            ],
        }

        with (
            patch(
                "hdg.dashboard.SchedulerRepository",
                autospec=True,
            ) as repository_class,
            patch("hdg.dashboard.graph_status", return_value=status),
        ):
            repository = repository_class.return_value
            repository.hierarchy.return_value = definition
            repository.revision_history.return_value = history
            result = open_delivery_dashboard(
                root="C:/control",
                root_id="d-dashboard",
            )

        self.assertTrue(result["readOnly"])
        self.assertEqual(result["delivery"]["revision"], 2)
        self.assertEqual(result["run"]["status"], "RUNNING")
        self.assertEqual(result["summary"]["activeLoops"], 1)
        self.assertEqual(result["graph"]["edges"], definition["graph"]["edges"])
        self.assertNotIn("loop", result["graph"]["nodes"][0])
        self.assertNotIn("operationId", result["graph"]["nodes"][0])
        self.assertNotIn("reason", result["revisions"][0])
        self.assertNotIn("requestedBy", result["revisions"][0])
        repository.assert_self_hosting_dogfood.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
