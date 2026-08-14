from __future__ import annotations

from copy import deepcopy

from pathlib import Path

from typing import Any

from .errors import GatedLoopError, fail

from .fs_safe import atomic_write, safe_path

from .git_binding import (
    enumerate_local_feature_branches,
    git_repository_identity,
    inspect_business_commit_range,
    inspect_delivery_git_workspace,
    inspect_frozen_git_workspace_provenance,
    resolve_branch_binding,
    verify_delivery_git_binding,
    verify_delivery_project_scopes,
)

from .graph_model import (
    compile_delivery_graph,
    graph_fingerprint,
    graph_summary,
)

from .interaction_contract import (
    development_baseline_contract,
    execution_choice_contract,
    manual_receiver_prompt,
)

from .jsonio import fingerprint

from .model_core import (
    hierarchy_fingerprint,
    iter_hierarchy_nodes,
    validate_git_binding,
    validate_hierarchy_definition,
)

from .model_rendering import (
    render_manual_handoff,
    task_baseline_relative_path,
    task_has_database_projection,
    task_has_interface_projection,
    work_item_projection_relative_path,
)

from .repository import (
    GOVERNANCE_DIRECTORY,
    SchedulerRepository,
)
