from .mcp_apps_support import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    CODEX_SANDBOX_META_KEY,
    DASHBOARD_MIME_TYPE,
    DASHBOARD_RESOURCE_URI,
    EXISTING_TOOL_NAMES,
    GatedLoopError,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PROTOCOL_VERSION_META_KEY,
    Path,
    ProjectRootBinding,
    TemporaryDirectory,
    handle_message,
    io,
    json,
    modern_meta,
    patch,
    redirect_stderr,
    tool_definitions,
    unittest,
)
from .mcp_apps_case import McpAppsContractTestsSupport
from .mcp_apps_protocol import McpAppsContractTestsPart1
from .mcp_apps_projection import McpAppsContractTestsPart2


class McpAppsContractTests(
    McpAppsContractTestsSupport,
    McpAppsContractTestsPart1,
    McpAppsContractTestsPart2,
    unittest.TestCase,
):
    pass



if __name__ == "__main__":
    unittest.main()
