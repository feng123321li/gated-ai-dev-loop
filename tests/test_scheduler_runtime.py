from .scheduler_runtime_support import (
    at,
    database_hierarchy,
    review_success,
    success,
    success_for_node,
    unittest,
)
from .scheduler_runtime_case import SchedulerRuntimeTestsSupport
from .scheduler_runtime_dispatch_selection import SchedulerRuntimeTestsPart1
from .scheduler_runtime_manual_handoff import SchedulerRuntimeTestsPart2
from .scheduler_runtime_archival import SchedulerRuntimeTestsPart3
from .scheduler_runtime_graph_reviews import SchedulerRuntimeTestsPart4
from .scheduler_runtime_capacity import SchedulerRuntimeTestsPart5
from .scheduler_runtime_resources import SchedulerRuntimeTestsPart6
from .scheduler_runtime_requirement_revisions import SchedulerRuntimeTestsPart7
from .scheduler_runtime_projection_baseline import SchedulerRuntimeTestsPart8
from .scheduler_runtime_projection_contracts import SchedulerRuntimeTestsPart9
from .scheduler_runtime_progress_recovery import SchedulerRuntimeTestsPart10
from .scheduler_runtime_rebuild_cancellation import SchedulerRuntimeTestsPart11
from .scheduler_runtime_command_workers import SchedulerRuntimeTestsPart12
from .scheduler_runtime_manual_compatibility import SchedulerRuntimeTestsPart13
from .scheduler_runtime_heartbeat_continuity import SchedulerRuntimeTestsPart14
from .scheduler_runtime_removed_coupling_tests import RemovedCouplingTests


class SchedulerRuntimeTests(
    SchedulerRuntimeTestsSupport,
    SchedulerRuntimeTestsPart1,
    SchedulerRuntimeTestsPart2,
    SchedulerRuntimeTestsPart3,
    SchedulerRuntimeTestsPart4,
    SchedulerRuntimeTestsPart5,
    SchedulerRuntimeTestsPart6,
    SchedulerRuntimeTestsPart7,
    SchedulerRuntimeTestsPart8,
    SchedulerRuntimeTestsPart9,
    SchedulerRuntimeTestsPart10,
    SchedulerRuntimeTestsPart11,
    SchedulerRuntimeTestsPart12,
    SchedulerRuntimeTestsPart13,
    SchedulerRuntimeTestsPart14,
    unittest.TestCase,
):
    pass



if __name__ == "__main__":
    unittest.main()
