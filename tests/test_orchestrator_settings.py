from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg.controller import ControllerContext, LayeredDeliveryController
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    MODERN_PROTOCOL_VERSION,
    PROTOCOL_VERSION_META_KEY,
    McpConnection,
    handle_message,
)
from hdg.mcp_apps import (
    MCP_APP_MIME_TYPE,
    ORCHESTRATOR_SETTINGS_RESOURCE_URI,
)
from hdg.host_policy import ProjectRootBinding
from hdg.orchestrator_config import (
    ORCHESTRATOR_CONFIG_ENV,
    OrchestratorConfig,
)
from hdg.orchestrator_settings import open_orchestrator_settings


def _modern_meta() -> dict[str, object]:
    return {
        PROTOCOL_VERSION_META_KEY: MODERN_PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {
            "name": "orchestrator-panel-test",
            "version": "1.0.0",
        },
    }


def _policy(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schemaVersion": 2,
        "maxConcurrentExecutors": 4,
        "quotaExhaustionPolicy": "PAUSE_AND_RESUME",
    }
    value.update(overrides)
    return value


class OrchestratorSettingsTests(unittest.TestCase):
    def test_panel_returns_only_policy_and_read_only_host_diagnostic(self) -> None:
        result = open_orchestrator_settings(
            root="unused",
            orchestrator_config=OrchestratorConfig(),
            host_adapter_id="codex",
        )

        self.assertEqual(set(result), {"config", "currentHostAdapter"})
        self.assertEqual(result["currentHostAdapter"], "codex")
        self.assertEqual(result["config"]["schemaVersion"], 2)
        self.assertEqual(result["config"]["maxConcurrentExecutors"], 4)
        self.assertEqual(
            result["config"]["quotaExhaustionPolicy"],
            "PAUSE_AND_RESUME",
        )

    def test_resources_expose_self_contained_mcp_app(self) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            listed = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "resources",
                    "method": "resources/list",
                    "params": {"_meta": _modern_meta()},
                },
                connection=connection,
            )
            read = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "resource",
                    "method": "resources/read",
                    "params": {
                        "uri": ORCHESTRATOR_SETTINGS_RESOURCE_URI,
                        "_meta": _modern_meta(),
                    },
                },
                connection=connection,
            )

        resource = listed["result"]["resources"][0]
        self.assertEqual(resource["uri"], ORCHESTRATOR_SETTINGS_RESOURCE_URI)
        content = read["result"]["contents"][0]
        self.assertEqual(content["mimeType"], MCP_APP_MIME_TYPE)
        self.assertIn('request("ui/initialize"', content["text"])
        self.assertIn('name: "update_orchestrator_settings"', content["text"])
        self.assertNotIn('id="automatic"', content["text"])
        self.assertNotIn('id="model"', content["text"])
        self.assertNotIn('id="cross"', content["text"])
        self.assertNotIn('id="review"', content["text"])
        self.assertNotIn('id="adapter-list"', content["text"])
        self.assertNotIn('id="cross-adapter-notice"', content["text"])
        self.assertNotIn("crossCapability.mutable", content["text"])
        self.assertNotIn("automaticOrchestration", content["text"])
        self.assertNotIn("allowedAdapters", content["text"])
        self.assertNotIn("autoSelectModel", content["text"])
        self.assertNotIn("allowCrossAdapterDispatch", content["text"])
        self.assertNotIn("preferDifferentAdapterForReview", content["text"])
        self.assertEqual(content["_meta"]["ui"]["csp"]["connectDomains"], [])

    def test_approved_update_persists_and_refreshes_current_connection(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "user", "orchestrator.json")
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                trusted_host_adapter="codex",
                orchestrator_config=OrchestratorConfig(config_path=str(path)),
            )
            updated = _policy(
                maxConcurrentExecutors=8,
            )
            with patch.dict(
                os.environ,
                {ORCHESTRATOR_CONFIG_ENV: str(path)},
            ):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "update",
                        "method": "tools/call",
                        "params": {
                            "name": "update_orchestrator_settings",
                            "arguments": {"config": updated},
                            "_meta": _modern_meta(),
                        },
                    },
                    connection=connection,
                )

            payload = response["result"]["structuredContent"]
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["result"]["saved"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), updated)
            self.assertEqual(connection.orchestrator_config.max_concurrent_executors, 8)

    def test_project_independent_update_does_not_require_codex_sandbox_metadata(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "user", "orchestrator.json")
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(
                    None,
                    from_sandbox_meta=True,
                ),
                trusted_host_adapter="codex",
                orchestrator_config=OrchestratorConfig(
                    config_path=str(path)
                ),
            )
            updated = _policy(maxConcurrentExecutors=6)
            with patch.dict(
                os.environ,
                {ORCHESTRATOR_CONFIG_ENV: str(path)},
            ):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "update-without-sandbox",
                        "method": "tools/call",
                        "params": {
                            "name": "update_orchestrator_settings",
                            "arguments": {"config": updated},
                            "_meta": _modern_meta(),
                        },
                    },
                    connection=connection,
                )

            payload = response["result"]["structuredContent"]
            self.assertTrue(payload["ok"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), updated)
            self.assertIsNone(connection.project_root.bound_root)

            graph_response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "graph-without-sandbox",
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {},
                        "_meta": _modern_meta(),
                    },
                },
                connection=connection,
            )
            graph_payload = graph_response["result"]["structuredContent"]
            self.assertFalse(graph_payload["ok"])
            self.assertEqual(
                graph_payload["error"]["code"],
                "PROJECT_ROOT_UNAVAILABLE",
            )

    def test_update_rejects_legacy_cross_adapter_field(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "user", "orchestrator.json")
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                trusted_host_adapter="codex",
                orchestrator_config=OrchestratorConfig(
                    config_path=str(path)
                ),
            )
            updated = _policy(allowCrossAdapterDispatch=True)
            with patch.dict(
                os.environ,
                {ORCHESTRATOR_CONFIG_ENV: str(path)},
            ):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "unsupported-cross-adapter",
                        "method": "tools/call",
                        "params": {
                            "name": "update_orchestrator_settings",
                            "arguments": {"config": updated},
                            "_meta": _modern_meta(),
                        },
                    },
                    connection=connection,
                )

            payload = response["result"]["structuredContent"]
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"]["code"],
                "MCP_TOOL_ARGUMENT_INVALID",
            )
            self.assertFalse(path.exists())

    def test_update_rejects_adapter_switch_quota_policy(self) -> None:
        with TemporaryDirectory() as root:
            path = Path(root, "user", "orchestrator.json")
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                trusted_host_adapter="codex",
                orchestrator_config=OrchestratorConfig(
                    config_path=str(path)
                ),
            )
            updated = _policy(quotaExhaustionPolicy="SWITCH_ADAPTER")
            with patch.dict(
                os.environ,
                {ORCHESTRATOR_CONFIG_ENV: str(path)},
            ):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "unsupported-adapter-switch",
                        "method": "tools/call",
                        "params": {
                            "name": "update_orchestrator_settings",
                            "arguments": {"config": updated},
                            "_meta": _modern_meta(),
                        },
                    },
                    connection=connection,
                )

            payload = response["result"]["structuredContent"]
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"]["code"],
                "MCP_TOOL_ARGUMENT_INVALID",
            )
            self.assertFalse(path.exists())

    def test_controller_config_read_does_not_create_scheduler_runtime(self) -> None:
        with TemporaryDirectory() as root:
            result = LayeredDeliveryController().execute(
                "open_orchestrator_settings",
                {},
                context=ControllerContext(
                    project_root=root,
                    host_adapter_id="codex",
                    orchestrator_config=OrchestratorConfig(),
                ),
            )
            self.assertEqual(result["currentHostAdapter"], "codex")
            self.assertFalse(Path(root, ".layered-delivery").exists())


if __name__ == "__main__":
    unittest.main()
