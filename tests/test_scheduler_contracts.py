from .scheduler_contracts_support import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    ControllerContext,
    GatedLoopError,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    LayeredDeliveryController,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    Mock,
    PROTOCOL_VERSION_META_KEY,
    Path,
    ProjectRootBinding,
    SCHEDULER_STATE_CONTRACT,
    SUPPORTED_PROTOCOL_VERSIONS,
    SchedulerRepository,
    SimpleNamespace,
    TemporaryDirectory,
    _tool_result,
    at,
    bind_delivery_to_git,
    call_tool,
    capture_verified_workspace_changes,
    database_hierarchy,
    deepcopy,
    fingerprint,
    freeze_hierarchy,
    git_command,
    git_delivery_checkout,
    group_hierarchy,
    handle_message,
    hierarchy_contract,
    inspect,
    inspect_frozen_git_workspace_provenance,
    io,
    isolated_task_hierarchy,
    json,
    legacy_delivery_hierarchy_017,
    loop_descriptor,
    loop_node_id,
    mcp_server,
    modern_meta,
    patch,
    prepare_hierarchy,
    re,
    redirect_stderr,
    reserve_loop,
    sqlite3,
    subprocess,
    task_hierarchy,
    tool_definitions,
    unittest,
    validate_hierarchy_definition,
    validate_tool_arguments,
    workspace_status,
)
from .scheduler_contracts_development_baseline_tests import DevelopmentBaselineTests
from .scheduler_contracts_hierarchy_contract_tests import HierarchyContractTests
from .scheduler_contracts_tool_schemas import McpSurfaceTestsPart1
from .scheduler_contracts_workspace_isolation import McpSurfaceTestsPart2
from .scheduler_contracts_git_binding import McpSurfaceTestsPart3
from .scheduler_contracts_state_protocol import McpSurfaceTestsPart4
from .scheduler_contracts_modern_protocol import McpModernProtocolTests
from .scheduler_contracts_protocol_metadata import McpSurfaceTestsPart5
from .scheduler_contracts_hierarchy_file_tests import HierarchyFileTests
from .scheduler_contracts_draft_cleanup_tests import DraftCleanupTests
from .scheduler_contracts_stale_base_rebase_advisory_tests import StaleBaseRebaseAdvisoryTests


class McpSurfaceTests(
    McpSurfaceTestsPart1,
    McpSurfaceTestsPart2,
    McpSurfaceTestsPart3,
    McpSurfaceTestsPart4,
    McpModernProtocolTests,
    McpSurfaceTestsPart5,
    unittest.TestCase,
):
    pass



if __name__ == "__main__":
    unittest.main()
