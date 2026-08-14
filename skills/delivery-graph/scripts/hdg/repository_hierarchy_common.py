from __future__ import annotations

import json

import os

import sqlite3

from typing import Any, Callable

import uuid

from .errors import fail

from .graph_model import loop_node_id, task_review_node_id

from .jsonio import canonical_json

from .model_core import (
    iter_hierarchy_nodes,
    validate_hierarchy_definition,
)

class HierarchyStoreBase:
    def __init__(
        self,
        repository: Any,
        *,
        validate_stored_definition: Callable[..., Any],
        commit_timestamp_fn: Callable[..., str],
        timestamp_fn: Callable[[object], str],
    ) -> None:
        self.repository = repository
        self.validate_stored_definition = validate_stored_definition
        self.commit_timestamp_fn = commit_timestamp_fn
        self.timestamp_fn = timestamp_fn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.repository, name)
