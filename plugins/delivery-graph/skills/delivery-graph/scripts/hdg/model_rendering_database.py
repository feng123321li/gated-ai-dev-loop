from __future__ import annotations

from .model_rendering_acceptance import (
    _work_item_status_lines,
    render_delivery_acceptance,
    render_work_item_acceptance,
    render_work_item_progress,
)
from .model_rendering_common import (
    Any,
    DATABASE_CHANGES_PROJECTION_TEMPLATE,
    DATABASE_CHANGE_DETAIL_PROJECTION_TEMPLATE,
    INTERFACE_CHANGE_TYPE_TEXT,
    WORK_ITEM_DIRECTORY,
    _markdown_text,
    _table_row,
    _task_database_declarations,
    _task_interface_declarations,
    _utc_plus_8,
    iter_hierarchy_nodes,
    json,
    posixpath,
    work_item_projection_directories,
)
from .model_rendering_baseline import (
    render_group_baseline,
    render_scheduling_plan,
    render_work_item_baseline,
)
from .model_rendering_progress import render_delivery_progress
from .model_rendering_interfaces import (
    _interface_filename_slug,
    render_task_interface_documents,
)
from .model_rendering_state import (
    _projection_states,
    render_delivery_baseline,
)


def _database_identity(change: dict[str, Any]) -> str:
    return ".".join(
        str(change[key]).strip()
        for key in ("projectId", "database", "schema", "table")
        if key in change and str(change[key]).strip()
    )

def _database_document_filename(
    position: int,
    change: dict[str, Any],
) -> str:
    slug = _interface_filename_slug(_database_identity(change))[
        :64
    ].rstrip("-")
    return f"{position:03d}-{slug or 'table'}.md"

def _database_scalar(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

def _database_column_table(change: dict[str, Any]) -> str:
    before = change.get("before")
    after = change.get("after")
    before_columns = (
        before.get("columns", []) if isinstance(before, dict) else []
    )
    after_columns = (
        after.get("columns", []) if isinstance(after, dict) else []
    )
    before_by_name = {
        item["name"]: item
        for item in before_columns
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    after_by_name = {
        item["name"]: item
        for item in after_columns
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    names = [
        *before_by_name,
        *(name for name in after_by_name if name not in before_by_name),
    ]
    attributes = ("type", "nullable", "default", "comment")
    rows: list[str] = []
    for name in names:
        old = before_by_name.get(name)
        new = after_by_name.get(name)
        if old is None:
            state = "新增"
        elif new is None:
            state = "删除"
        elif any(old.get(key) != new.get(key) for key in attributes):
            state = "修改"
        else:
            state = "未变"

        def transition(key: str) -> str:
            if old is None:
                return _database_scalar(new.get(key))
            if new is None:
                return _database_scalar(old.get(key))
            old_value = _database_scalar(old.get(key))
            new_value = _database_scalar(new.get(key))
            return (
                old_value
                if old_value == new_value
                else f"{old_value} → {new_value}"
            )

        values = [name, state, *(transition(key) for key in attributes)]
        if new is None:
            values = [
                value if index == 1 else f"~~{value}~~"
                for index, value in enumerate(values)
            ]
        rows.append(_table_row(values))
    return "\n".join(
        [
            (
                "| 字段 | 变更 | 类型（修改前 → 修改后） | "
                "可空（修改前 → 修改后） | 默认值（修改前 → 修改后） | "
                "注释（修改前 → 修改后） |"
            ),
            "|---|---|---|---|---|---|",
            *rows,
        ]
    )

def _database_snapshot(value: object) -> str:
    if value is None:
        return "- 不适用"
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return "\n".join(f"    {line}" for line in serialized.splitlines())

def _render_database_change_detail(
    change: dict[str, Any],
) -> str:
    migration = change["migration"]
    metadata = "\n".join(
        [
            f"- 表标识：{_markdown_text(_database_identity(change))}",
            (
                "- 变更类型："
                + _markdown_text(
                    INTERFACE_CHANGE_TYPE_TEXT.get(
                        change["changeType"],
                        change["changeType"],
                    )
                )
            ),
            f"- 简介：{_markdown_text(change['summary'])}",
            f"- 排他资源锁：{_markdown_text(change['resourceClaim'])}",
        ]
    )
    migration_lines = [
        f"- 正向迁移：{_markdown_text(migration['forward'])}",
        f"- 回滚方案：{_markdown_text(migration['rollback'])}",
        f"- 数据回填：{_markdown_text(migration['backfill'])}",
        f"- 发布兼容：{_markdown_text(migration['compatibility'])}",
        "- 验证要求：",
        *(
            f"  - {_markdown_text(item)}"
            for item in migration["verification"]
        ),
    ]
    return DATABASE_CHANGE_DETAIL_PROJECTION_TEMPLATE.substitute(
        table=_markdown_text(_database_identity(change)),
        metadata=metadata,
        column_table=_database_column_table(change),
        before_snapshot=_database_snapshot(change.get("before")),
        after_snapshot=_database_snapshot(change.get("after")),
        migration="\n".join(migration_lines),
    )

def render_task_database_changes(
    definition: dict[str, Any],
    *,
    delivery_baseline: str = "../../baseline.md",
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    if definition["kind"] != "TASK":
        raise ValueError("Database projection requires a TASK definition")
    rows: list[str] = []
    for position, change in enumerate(
        _task_database_declarations(definition),
        start=1,
    ):
        filename = _database_document_filename(position, change)
        name = (
            f"[{_markdown_text(_database_identity(change))}]"
            f"(database-changes/{filename})"
        )
        if change["changeType"] == "DELETE":
            name = f"~~{name}~~"
        before = change.get("before")
        after = change.get("after")
        rows.append(
            _table_row(
                [
                    definition["id"],
                    name,
                    INTERFACE_CHANGE_TYPE_TEXT[change["changeType"]],
                    str(len(before["columns"])) if before else "不适用",
                    str(len(after["columns"])) if after else "不适用",
                    change["resourceClaim"],
                    change["summary"],
                ],
                raw_indices={1},
            )
        )
    database_rows = "\n".join(
        [
            (
                "| 来源 TASK | 表标识 | 变更类型 | 修改前字段数 | "
                "修改后字段数 | 排他资源锁 | 简介 |"
            ),
            "|---|---|---|---|---|---|---|",
            *rows,
        ]
    )
    return DATABASE_CHANGES_PROJECTION_TEMPLATE.substitute(
        database_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        delivery_baseline=delivery_baseline,
        database_rows=database_rows,
    )

def render_task_database_documents(
    definition: dict[str, Any],
    **index_arguments: Any,
) -> dict[str, str]:
    documents = {
        "database-changes.md": render_task_database_changes(
            definition,
            **index_arguments,
        )
    }
    for position, change in enumerate(
        _task_database_declarations(definition),
        start=1,
    ):
        filename = _database_document_filename(position, change)
        documents[f"database-changes/{filename}"] = (
            _render_database_change_detail(change)
        )
    return documents

def render_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
    revision_history: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Render the complete Delivery human projection set.

    The definition is either controller-owned SQLite state or a validated,
    portable manual-handoff snapshot. Callers cannot supply a template or
    filename.
    """

    hierarchy = stored_definition["hierarchy"]
    updated_at = (
        stored_definition["updatedAt"]
        if stored_definition["status"] == "ARCHIVED" or run is None
        else run["updatedAt"]
    )
    human_projection_arguments = {
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": updated_at,
    }
    documents = {
        "overview.md": render_scheduling_plan(
            hierarchy,
            **human_projection_arguments,
            run=run,
            delivery_closure=stored_definition.get("deliveryClosure"),
        ),
        "baseline.md": render_delivery_baseline(
            hierarchy,
            **human_projection_arguments,
        ),
        "progress.md": render_delivery_progress(
            hierarchy,
            **human_projection_arguments,
            run=run,
        ),
        "acceptance.md": render_delivery_acceptance(
            hierarchy,
            **human_projection_arguments,
            run=run,
        ),
    }
    history = revision_history or {
        "currentRevision": stored_definition.get(
            "deliveryRevision",
            1,
        ),
        "revisions": [],
    }
    rows = []
    for item in history["revisions"]:
        rows.append(
            "| "
            + " | ".join(
                [
                    str(item["revision"]),
                    _markdown_text(item["status"]),
                    _markdown_text(item["runStatus"] or "NOT_STARTED"),
                    _markdown_text(item["reason"] or "初始范围"),
                    _markdown_text(
                        "、".join(item["authorizedProjectIds"]) or "无"
                    ),
                    _utc_plus_8(item["updatedAt"]),
                ]
            )
            + " |"
        )
    documents["revisions.md"] = "\n".join(
        [
            "# Delivery 修订历史",
            "",
            f"- 交付标识：{_markdown_text(stored_definition['rootId'])}",
            f"- 当前修订：{history['currentRevision']}",
            "",
            "每个修订的 Graph 与需求指纹均保存在 SQLite；旧修订只读保留。",
            "",
            "| 修订 | 范围状态 | 运行状态 | 变更原因 | 已授权项目 | 最近更新（UTC+8） |",
            "|---|---|---|---|---|---|",
            *(
                rows
                or [
                    "| 1 | PREPARED | NOT_STARTED | 初始范围 | 无 | "
                    + _utc_plus_8(stored_definition["updatedAt"])
                    + " |"
                ]
            ),
            "",
        ]
    )
    return documents

def render_work_item_projection_documents(
    stored_definition: dict[str, Any],
    run: dict[str, Any] | None,
) -> dict[str, str]:
    """Render the exact GROUP/TASK tree from validated Delivery state."""

    hierarchy = stored_definition["hierarchy"]
    projection_directories = work_item_projection_directories(hierarchy)
    baseline_arguments = {
        "delivery": hierarchy["delivery"],
        "skill_hints": hierarchy["root"]["skillHints"],
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": stored_definition["updatedAt"],
    }
    task_requirements = {
        item["taskId"]: item
        for item in (
            run.get("taskRequirements", [])
            if run is not None
            else []
        )
    }
    dynamic_arguments = {
        "hierarchy_fingerprint": stored_definition[
            "hierarchyFingerprint"
        ],
        "graph_fingerprint": stored_definition["graphFingerprint"],
        "hierarchy_status": stored_definition["status"],
        "updated_at": (
            stored_definition["updatedAt"]
            if stored_definition["status"] == "ARCHIVED" or run is None
            else run["updatedAt"]
        ),
        "run": run,
    }
    documents: dict[str, str] = {}
    for node in iter_hierarchy_nodes(hierarchy):
        definition = node["definition"]
        item_id = definition["id"]
        projection_directory = projection_directories[item_id]
        tree_directory = projection_directory.removeprefix(
            f"{WORK_ITEM_DIRECTORY}/"
        )
        delivery_baseline = posixpath.relpath(
            "baseline.md",
            start=projection_directory,
        )
        documents[f"{tree_directory}/baseline.md"] = (
            render_work_item_baseline(
                node,
                delivery_baseline=delivery_baseline,
                task_requirement=task_requirements.get(item_id),
                **baseline_arguments,
            )
            if definition["kind"] == "TASK"
            else render_group_baseline(
                node,
                delivery_baseline=delivery_baseline,
                **baseline_arguments,
            )
        )
        documents[f"{tree_directory}/progress.md"] = (
            render_work_item_progress(
                node,
                **dynamic_arguments,
            )
        )
        documents[f"{tree_directory}/acceptance.md"] = (
            render_work_item_acceptance(
                node,
                **dynamic_arguments,
            )
        )
        if _task_interface_declarations(definition):
            interface_documents = render_task_interface_documents(
                definition,
                delivery_baseline=delivery_baseline,
                hierarchy_fingerprint=stored_definition[
                    "hierarchyFingerprint"
                ],
                graph_fingerprint=stored_definition[
                    "graphFingerprint"
                ],
                hierarchy_status=stored_definition["status"],
                updated_at=stored_definition["updatedAt"],
            )
            for filename, content in interface_documents.items():
                documents[f"{tree_directory}/{filename}"] = content
        if _task_database_declarations(definition):
            database_documents = render_task_database_documents(
                definition,
                delivery_baseline=delivery_baseline,
                hierarchy_fingerprint=stored_definition[
                    "hierarchyFingerprint"
                ],
                graph_fingerprint=stored_definition[
                    "graphFingerprint"
                ],
                hierarchy_status=stored_definition["status"],
                updated_at=stored_definition["updatedAt"],
            )
            for filename, content in database_documents.items():
                documents[f"{tree_directory}/{filename}"] = content
    return documents
