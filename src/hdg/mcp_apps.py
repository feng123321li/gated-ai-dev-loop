from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import fail


DASHBOARD_RESOURCE_URI = "ui://delivery-graph/dashboard.html"
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
_ASSET = Path(__file__).with_name("assets") / "delivery-dashboard.html"

_RESOURCES = (
    {
        "uri": DASHBOARD_RESOURCE_URI,
        "name": "delivery-graph-dashboard",
        "title": "Delivery Graph 运行看板",
        "description": (
            "只读展示当前 Delivery、Graph 节点、活动 Loop、告警与 "
            "Revision 历史。"
        ),
        "mimeType": MCP_APP_MIME_TYPE,
    },
)


def resource_definitions() -> list[dict[str, Any]]:
    """Return detached MCP Resource descriptors."""

    return deepcopy(list(_RESOURCES))


def read_resource(uri: str) -> dict[str, Any]:
    """Read one bundled MCP App without granting filesystem access."""

    if uri != DASHBOARD_RESOURCE_URI:
        fail(
            "MCP_RESOURCE_NOT_FOUND",
            "Resource not found",
            uri=uri,
        )
    try:
        html = _ASSET.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        fail(
            "MCP_RESOURCE_UNAVAILABLE",
            "The Delivery Graph dashboard is unavailable",
        )
    return {
        "contents": [
            {
                "uri": DASHBOARD_RESOURCE_URI,
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
                        "当前 Delivery 的只读 Graph 运行看板。"
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
    "DASHBOARD_RESOURCE_URI",
    "MCP_APP_MIME_TYPE",
    "read_resource",
    "resource_definitions",
)
