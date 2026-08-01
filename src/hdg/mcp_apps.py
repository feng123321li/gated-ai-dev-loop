from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import fail


ORCHESTRATOR_SETTINGS_RESOURCE_URI = (
    "ui://layered-delivery/orchestrator-settings.html"
)
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
_ASSET = Path(__file__).with_name("assets") / "orchestrator-settings.html"

_RESOURCES = (
    {
        "uri": ORCHESTRATOR_SETTINGS_RESOURCE_URI,
        "name": "orchestrator-settings",
        "title": "中央编排器设置",
        "description": (
            "配置自动编排、模型选择、Adapter、并发、额度恢复和审查策略。"
        ),
        "mimeType": MCP_APP_MIME_TYPE,
    },
)


def resource_definitions() -> list[dict[str, Any]]:
    return deepcopy(list(_RESOURCES))


def read_resource(uri: str) -> dict[str, Any]:
    if uri != ORCHESTRATOR_SETTINGS_RESOURCE_URI:
        fail("MCP_RESOURCE_NOT_FOUND", f"Unknown MCP resource: {uri}")
    try:
        html = _ASSET.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        fail(
            "MCP_RESOURCE_UNAVAILABLE",
            "The orchestrator settings panel asset is unavailable",
        )
    return {
        "contents": [
            {
                "uri": ORCHESTRATOR_SETTINGS_RESOURCE_URI,
                "mimeType": MCP_APP_MIME_TYPE,
                "text": html,
                "_meta": {
                    "ui": {
                        "prefersBorder": True,
                        "csp": {
                            "connectDomains": [],
                            "resourceDomains": [],
                        },
                    },
                    "openai/widgetDescription": (
                        "Layered Delivery 中央编排器的本机用户级设置面板。"
                    ),
                    "openai/widgetPrefersBorder": True,
                    "openai/widgetCSP": {
                        "connect_domains": [],
                        "resource_domains": [],
                    },
                },
            }
        ]
    }


__all__ = (
    "MCP_APP_MIME_TYPE",
    "ORCHESTRATOR_SETTINGS_RESOURCE_URI",
    "read_resource",
    "resource_definitions",
)
