from __future__ import annotations

from .repository_hierarchy_common import HierarchyStoreBase
from .repository_hierarchy_freeze import HierarchyFreezeMixin
from .repository_hierarchy_preparation import HierarchyPreparationMixin
from .repository_hierarchy_queries import HierarchyQueryMixin
from .repository_hierarchy_revisions import HierarchyRevisionMixin


class DeliveryHierarchyStore(
    HierarchyPreparationMixin,
    HierarchyRevisionMixin,
    HierarchyFreezeMixin,
    HierarchyQueryMixin,
    HierarchyStoreBase,
):
    """Own Delivery hierarchy revisions, freezing, and run history."""

    record_manual_handoff = HierarchyPreparationMixin.record_manual_handoff
    prepare = HierarchyPreparationMixin.prepare
    hierarchy = HierarchyRevisionMixin.hierarchy
    revision_hierarchy = HierarchyRevisionMixin.revision_hierarchy
    _carriable_task_ids = HierarchyRevisionMixin.__dict__["_carriable_task_ids"]
    _task_requirement_material = HierarchyRevisionMixin.__dict__["_task_requirement_material"]
    _next_task_requirement_revisions = HierarchyRevisionMixin.__dict__["_next_task_requirement_revisions"]
    prepare_revision = HierarchyRevisionMixin.prepare_revision
    freeze = HierarchyFreezeMixin.freeze
    freeze_manual_handoff = HierarchyFreezeMixin.freeze_manual_handoff
    _project_workspace_keys = HierarchyFreezeMixin.__dict__["_project_workspace_keys"]
    _assert_project_workspace_turn_owned = HierarchyFreezeMixin._assert_project_workspace_turn_owned
    _freeze = HierarchyFreezeMixin._freeze
    _run_from_connection = HierarchyQueryMixin._run_from_connection
    run = HierarchyQueryMixin.run
    revision_history = HierarchyQueryMixin.revision_history
    task_requirement_states = HierarchyQueryMixin.__dict__["task_requirement_states"]
