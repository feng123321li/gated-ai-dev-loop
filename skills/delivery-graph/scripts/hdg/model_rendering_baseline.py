from __future__ import annotations

from .model_rendering_common import (
    Any,
    GROUP_BASELINE_PROJECTION_TEMPLATE,
    INTERFACE_REQUEST_LOCATION_PREFIXES,
    KIND_TEXT,
    OVERVIEW_PROJECTION_TEMPLATE,
    PROJECTION_TEMPLATE_VERSION,
    TASK_BASELINE_PROJECTION_TEMPLATE,
    WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE,
    _markdown_text,
    _render_loop_baseline,
    _render_payload_markdown,
    _status_text,
    _table_row,
    _task_database_declarations,
    _task_interface_declarations,
    _utc_plus_8,
    _work_item_terminal_node_id,
    iter_hierarchy_nodes,
)


def _interface_scalar(
    value: object,
    *,
    fallback: str,
) -> str:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    return fallback

def _interface_call_identifier(
    protocol: str,
    snapshot: dict[str, Any] | None,
) -> str:
    if snapshot is None:
        return "不适用"
    if protocol == "HTTP":
        method = _interface_scalar(
            snapshot.get("method"),
            fallback="未声明方法",
        ).upper()
        path = _interface_scalar(
            snapshot.get("path"),
            fallback="未声明路径",
        )
        return f"{method} {path}"
    if protocol == "DUBBO":
        service = _interface_scalar(
            snapshot.get("service"),
            fallback="未声明服务",
        )
        method = _interface_scalar(
            snapshot.get("method"),
            fallback="未声明方法",
        )
        return f"{service}.{method}"
    return _interface_scalar(
        snapshot.get("identifier"),
        fallback="未声明",
    )

def _interface_required_text(value: object) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未声明"

def _interface_field_rows(
    value: object,
    *,
    section: str,
) -> list[dict[str, str]]:
    """Normalize common interface field declarations for table rendering."""

    rows: list[dict[str, str]] = []
    used_paths: set[str] = set()

    def unique_path(path: str) -> str:
        candidate = path or "（整体）"
        if candidate not in used_paths:
            used_paths.add(candidate)
            return candidate
        index = 2
        while f"{candidate}（{index}）" in used_paths:
            index += 1
        resolved = f"{candidate}（{index}）"
        used_paths.add(resolved)
        return resolved

    def add_row(
        path: str,
        *,
        field_type: object = None,
        required: object = None,
        max_length: object = None,
        description: object = None,
        example: object = None,
    ) -> None:
        rows.append(
            {
                "path": unique_path(path),
                "type": _interface_scalar(
                    field_type,
                    fallback="未声明",
                ),
                "required": _interface_required_text(required),
                "maxLength": _interface_scalar(
                    max_length,
                    fallback="—",
                ),
                "description": _interface_scalar(
                    description,
                    fallback="未声明",
                ),
                "example": _interface_scalar(
                    example,
                    fallback="—",
                ),
            }
        )

    def joined_path(prefix: str, name: object) -> str:
        normalized = _interface_scalar(name, fallback="未命名字段")
        return f"{prefix}.{normalized}" if prefix else normalized

    def parse_named(
        name: object,
        specification: object,
        *,
        prefix: str,
        required_override: bool | None = None,
    ) -> None:
        path = joined_path(prefix, name)
        if not isinstance(specification, dict):
            add_row(
                path,
                field_type=specification,
                required=required_override,
            )
            return
        nested = specification.get(
            "properties",
            specification.get("fields"),
        )
        nested_required = specification.get("required")
        add_row(
            path,
            field_type=specification.get(
                "type",
                "object" if nested is not None else None,
            ),
            required=(
                nested_required
                if isinstance(nested_required, bool)
                else required_override
            ),
            max_length=specification.get(
                "maxLength",
                specification.get("max_length"),
            ),
            description=specification.get(
                "description",
                specification.get("summary"),
            ),
            example=specification.get(
                "example",
                specification.get("exampleValue"),
            ),
        )
        if nested is not None:
            required_names = (
                {
                    str(item)
                    for item in nested_required
                    if isinstance(item, str)
                }
                if isinstance(nested_required, list)
                else None
            )
            parse_collection(
                nested,
                prefix=path,
                required_names=required_names,
            )

    def parse_collection(
        collection: object,
        *,
        prefix: str,
        required_names: set[str] | None = None,
    ) -> None:
        has_required_declaration = required_names is not None
        required_names = required_names or set()
        if isinstance(collection, list):
            for index, item in enumerate(collection, start=1):
                if isinstance(item, dict):
                    name = item.get("name", f"第 {index} 项")
                    parse_named(
                        name,
                        item,
                        prefix=prefix,
                        required_override=(
                            str(name) in required_names
                            if has_required_declaration
                            else None
                        ),
                    )
                else:
                    parse_named(
                        item,
                        None,
                        prefix=prefix,
                        required_override=(
                            str(item) in required_names
                            if has_required_declaration
                            else None
                        ),
                    )
            return
        if isinstance(collection, dict):
            for name in sorted(collection):
                parse_named(
                    name,
                    collection[name],
                    prefix=prefix,
                    required_override=(
                        str(name) in required_names
                        if has_required_declaration
                        else None
                    ),
                )
            return
        add_row(
            prefix or "（整体）",
            description=collection,
        )

    def first_declared(
        specification: dict[str, Any],
        keys: tuple[str, ...],
    ) -> object:
        for key in keys:
            if key in specification:
                return specification[key]
        return None

    def parse_request_locations(specification: dict[str, Any]) -> bool:
        if not any(
            key in specification
            for key in INTERFACE_REQUEST_LOCATION_PREFIXES
        ):
            return False
        for key, prefix in INTERFACE_REQUEST_LOCATION_PREFIXES.items():
            if key not in specification:
                continue
            collection = specification[key]
            if collection is None or collection == [] or collection == {}:
                continue
            if key == "body" and isinstance(collection, dict):
                parse_named("body", collection, prefix="")
            else:
                parse_collection(collection, prefix=prefix)
        return True

    def normalize_response_aliases(
        specification: dict[str, Any],
    ) -> dict[str, Any]:
        uses_controller_alias = any(
            key in specification
            for key in (
                "controllerReturnType",
                "controllerReturnFields",
            )
        )
        if not uses_controller_alias:
            return specification
        normalized: dict[str, Any] = {}
        response_type = first_declared(
            specification,
            ("type", "controllerReturnType"),
        )
        response_fields = first_declared(
            specification,
            ("fields", "properties", "controllerReturnFields"),
        )
        response_description = first_declared(
            specification,
            ("description", "summary"),
        )
        if response_type is not None:
            normalized["type"] = response_type
        if response_fields is not None:
            normalized["fields"] = response_fields
        if response_description is not None:
            normalized["description"] = response_description
        return normalized

    if isinstance(value, list):
        parse_collection(value, prefix="")
    elif isinstance(value, dict):
        if section == "request" and parse_request_locations(value):
            return rows
        if section == "response":
            value = normalize_response_aliases(value)
        nested = value.get("properties", value.get("fields"))
        looks_like_schema = nested is not None or any(
            key in value
            for key in ("name", "type", "required", "description", "summary")
        )
        if value.get("name") is not None:
            parse_named(value["name"], value, prefix="")
        elif looks_like_schema:
            add_row(
                "（整体）",
                field_type=value.get(
                    "type",
                    "object" if nested is not None else None,
                ),
                description=value.get(
                    "description",
                    value.get("summary"),
                ),
            )
            if nested is not None:
                required_names = (
                    {
                        str(item)
                        for item in value.get("required", [])
                        if isinstance(item, str)
                    }
                    if isinstance(value.get("required"), list)
                    else None
                )
                parse_collection(
                    nested,
                    prefix="",
                    required_names=required_names,
                )
        else:
            parse_collection(value, prefix="")
    else:
        add_row("（整体）", description=value)
    return rows

def _interface_transition(
    before: dict[str, str] | None,
    after: dict[str, str] | None,
    field: str,
) -> str:
    if before is None:
        return after[field] if after is not None else "未声明"
    if after is None:
        return before[field]
    before_value = before[field]
    after_value = after[field]
    if before_value == after_value:
        return before_value
    return f"{before_value} → {after_value}"

def _interface_change_table(
    before: object,
    after: object,
    *,
    section: str,
    include_required: bool = True,
    include_max_length: bool = False,
    include_example: bool = False,
    path_group: str | None = None,
    strip_path_group: bool = False,
    omit_container_rows: bool = False,
    render_empty: bool = True,
) -> list[str]:
    before_rows = (
        _interface_field_rows(before, section=section)
        if before is not None
        else []
    )
    after_rows = (
        _interface_field_rows(after, section=section)
        if after is not None
        else []
    )
    location_prefixes = tuple(
        prefix
        for prefix in INTERFACE_REQUEST_LOCATION_PREFIXES.values()
        if prefix
    )

    def in_path_group(path: str) -> bool:
        if path_group is None:
            return True
        if path_group == "":
            return not any(
                path == prefix or path.startswith(f"{prefix}.")
                for prefix in location_prefixes
            )
        return path == path_group or path.startswith(f"{path_group}.")

    before_rows = [
        row for row in before_rows if in_path_group(row["path"])
    ]
    after_rows = [
        row for row in after_rows if in_path_group(row["path"])
    ]
    if omit_container_rows:
        all_paths = {
            row["path"] for row in [*before_rows, *after_rows]
        }

        def is_container(path: str) -> bool:
            return len(all_paths) > 1 and (
                path == "（整体）"
                or (
                    path_group not in (None, "")
                    and path == path_group
                )
            )

        before_rows = [
            row for row in before_rows if not is_container(row["path"])
        ]
        after_rows = [
            row for row in after_rows if not is_container(row["path"])
        ]
    before_by_path = {row["path"]: row for row in before_rows}
    after_by_path = {row["path"]: row for row in after_rows}
    paths = [
        *(row["path"] for row in before_rows),
        *(
            row["path"]
            for row in after_rows
            if row["path"] not in before_by_path
        ),
    ]
    rendered_rows: list[str] = []
    for path in paths:
        before_row = before_by_path.get(path)
        after_row = after_by_path.get(path)
        comparison_fields = ["type"]
        if include_required:
            comparison_fields.append("required")
        if include_max_length:
            comparison_fields.append("maxLength")
        comparison_fields.append("description")
        if include_example:
            comparison_fields.append("example")
        if before_row is None:
            change = "新增"
        elif after_row is None:
            change = "删除"
        elif any(
            before_row[field] != after_row[field]
            for field in comparison_fields
        ):
            change = "修改"
        else:
            change = "未变"
        rendered_path = path
        if strip_path_group and path_group:
            rendered_path = (
                "（整体）"
                if path == path_group
                else path.removeprefix(f"{path_group}.")
            )
        values = [
            rendered_path,
            change,
            _interface_transition(before_row, after_row, "type"),
        ]
        if include_required:
            values.append(
                _interface_transition(
                    before_row,
                    after_row,
                    "required",
                )
            )
        if include_max_length:
            values.append(
                _interface_transition(
                    before_row,
                    after_row,
                    "maxLength",
                )
            )
        values.append(
            _interface_transition(
                before_row,
                after_row,
                "description",
            )
        )
        if include_example:
            values.append(
                _interface_transition(
                    before_row,
                    after_row,
                    "example",
                )
            )
        if before_row is not None and after_row is None:
            values = [
                value if index == 1 else f"~~{value}~~"
                for index, value in enumerate(values)
            ]
        rendered_rows.append(
            _table_row(values)
        )
    if not rendered_rows and not render_empty:
        return []
    columns = [
        "字段路径",
        "变更",
        "类型（修改前 → 修改后）",
    ]
    if include_required:
        columns.append("必填（修改前 → 修改后）")
    if include_max_length:
        columns.append("最大长度（修改前 → 修改后）")
    columns.append("说明（修改前 → 修改后）")
    if include_example:
        columns.append("示例值（修改前 → 修改后）")
    header = _table_row(columns)
    separator = f"|{'---|' * len(columns)}"
    empty_values = [
        "（无）",
        "无入参" if section == "request" else "无出参",
        *(["—"] * (len(columns) - 2)),
    ]
    return [
        header,
        separator,
        *(rendered_rows or [_table_row(empty_values)]),
    ]

def render_workspace_overview(
    deliveries: list[dict[str, Any]],
) -> str:
    """Render the workspace-wide Delivery summary from SQLite-loaded state."""

    ordered = sorted(
        deliveries,
        key=lambda item: (
            (
                item["run"]["updatedAt"]
                if item.get("run") is not None
                else item["updatedAt"]
            ),
            item["rootId"],
        ),
        reverse=True,
    )
    rows: list[str] = []
    for item in ordered:
        state_error = item.get("stateError")
        hierarchy = item.get("hierarchy")
        run = item.get("run")
        status = (
            run["status"]
            if run is not None
            else item.get("queueState", item["status"])
        )
        updated_at = (
            run["updatedAt"]
            if run is not None
            else item["updatedAt"]
        )
        cells = [
            item["rootId"],
            (
                "调度状态不可读取"
                if state_error is not None
                else hierarchy["delivery"]["title"]
            ),
            _status_text(status),
            (
                item.get("deliveryClosure", {}).get("label", "—")
                if state_error is None
                else "—"
            ),
            _utc_plus_8(updated_at),
        ]
        detail_link = (
            _markdown_text(
                f"需修复 SQLite 状态（{state_error['code']}）"
            )
            if state_error is not None
            else f"[查看交付详情]({item['rootId']}/overview.md)"
        )
        rows.append(
            "| "
            + " | ".join(
                [
                    *(_markdown_text(cell) for cell in cells),
                    detail_link,
                ]
            )
            + " |"
        )
    latest_update = (
        max(
            (
                (
                    item["run"]["updatedAt"]
                    if item.get("run") is not None
                    else item["updatedAt"]
                )
                for item in deliveries
            ),
            default=None,
        )
    )
    return WORKSPACE_OVERVIEW_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_count=str(len(deliveries)),
        updated_at=_utc_plus_8(latest_update),
        delivery_rows=(
            "\n".join(rows)
            or "| 无 | 无 | 无 | 无 | 无 |"
        ),
    )

def render_work_item_baseline(
    node: dict[str, Any],
    *,
    delivery: dict[str, Any] | None = None,
    delivery_baseline: str = "../../baseline.md",
    skill_hints: list[dict[str, str]] | None = None,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    task_requirement: dict[str, Any] | None = None,
) -> str:
    """Render one TASK baseline from scheduler-visible metadata.

    Loop payloads remain semantically opaque and are structurally projected
    through a fixed human-readable Markdown renderer.
    """

    definition = node["definition"]
    if definition["kind"] != "TASK":
        raise ValueError("TASK baseline requires a TASK definition")
    loop = definition["execution"]["loop"]
    review_loop = node["reviewLoop"]
    hints = skill_hints or []
    rendered_hints = (
        "\n".join(
            f"- {_markdown_text(hint['name'])}："
            f"{_markdown_text(hint['purpose'])}"
            for hint in hints
        )
        if hints
        else "- 无"
    )
    delivery = delivery or {}
    interface_declarations = _task_interface_declarations(definition)
    database_declarations = _task_database_declarations(definition)
    parent_baseline = (
        "- [上级节点需求基线](../../baseline.md)"
        if definition["parentId"]
        else "- 上级节点需求基线：无（Delivery 根工作项）"
    )
    return TASK_BASELINE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_id=_markdown_text(delivery.get("id", "不可用")),
        delivery_title=_markdown_text(
            delivery.get("title", "不可用")
        ),
        task_id=_markdown_text(definition["id"]),
        requirement_revision=(
            str(task_requirement["revision"])
            if task_requirement is not None
            else "未冻结"
        ),
        requirement_status=(
            {
                "FROZEN": "已冻结",
                "UNFROZEN": "已解冻，禁止派遣",
            }.get(task_requirement["status"], "未知")
            if task_requirement is not None
            else "待 Delivery 冻结"
        ),
        hierarchy_status=_status_text(
            hierarchy_status or "UNKNOWN"
        ),
        hierarchy_fingerprint=(
            hierarchy_fingerprint or "不可用"
        ),
        graph_fingerprint=graph_fingerprint or "不可用",
        updated_at=_utc_plus_8(updated_at),
        delivery_baseline=delivery_baseline,
        parent_baseline=parent_baseline,
        task_title=_markdown_text(definition["title"]),
        task_summary=_markdown_text(definition["summary"]),
        parent_id=_markdown_text(definition["parentId"] or "无"),
        dependencies=(
            "、".join(
                _markdown_text(item)
                for item in definition["execution"]["dependsOn"]
            )
            or "无"
        ),
        loop_ref=_markdown_text(loop["ref"]),
        resource_claims=(
            "、".join(
                _markdown_text(item)
                for item in loop["resourceClaims"]
            )
            or "无"
        ),
        payload=_render_payload_markdown(
            {
                key: value
                for key, value in loop["payload"].items()
                if not (
                    (key == "interfaces" and interface_declarations)
                    or (
                        key == "databaseChanges"
                        and database_declarations
                    )
                )
            },
            heading_level=3,
        ),
        interface_section=(
            "## 关联接口契约\n\n"
            "[查看本 TASK 的接口契约](interfaces.md)"
            if interface_declarations
            else ""
        ),
        database_section=(
            "## 关联数据库变更契约\n\n"
            "[查看本 TASK 的数据库变更契约](database-changes.md)"
            if database_declarations
            else ""
        ),
        review_section=_render_loop_baseline(
            "TASK Review Loop",
            review_loop,
            heading_level=2,
        ),
        skill_hints=rendered_hints,
    )

def render_group_baseline(
    node: dict[str, Any],
    *,
    delivery: dict[str, Any] | None = None,
    delivery_baseline: str = "../../baseline.md",
    skill_hints: list[dict[str, str]] | None = None,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    definition = node["definition"]
    if definition["kind"] != "GROUP":
        raise ValueError("GROUP baseline requires a GROUP hierarchy node")
    loop = node["reviewLoop"]
    hints = skill_hints or []
    rendered_hints = (
        "\n".join(
            f"- {_markdown_text(hint['name'])}："
            f"{_markdown_text(hint['purpose'])}"
            for hint in hints
        )
        if hints
        else "- 无"
    )
    parent_baseline = (
        "- [上级节点需求基线](../../baseline.md)"
        if definition["parentId"]
        else "- 上级节点需求基线：无（Delivery 根工作项）"
    )
    child_rows = []
    for child in node["children"]:
        child_definition = child["definition"]
        child_id = child_definition["id"]
        child_rows.append(
            "| "
            + " | ".join(
                [
                    KIND_TEXT[child_definition["kind"]],
                    _markdown_text(child_id),
                    _markdown_text(child_definition["title"]),
                    f"[查看](children/{child_id}/baseline.md)",
                    f"[查看](children/{child_id}/progress.md)",
                    f"[查看](children/{child_id}/acceptance.md)",
                ]
            )
            + " |"
        )
    delivery = delivery or {}
    return GROUP_BASELINE_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_id=_markdown_text(delivery.get("id", "不可用")),
        delivery_title=_markdown_text(
            delivery.get("title", "不可用")
        ),
        group_id=_markdown_text(definition["id"]),
        hierarchy_status=_status_text(
            hierarchy_status or "UNKNOWN"
        ),
        hierarchy_fingerprint=hierarchy_fingerprint or "不可用",
        graph_fingerprint=graph_fingerprint or "不可用",
        updated_at=_utc_plus_8(updated_at),
        delivery_baseline=delivery_baseline,
        parent_baseline=parent_baseline,
        group_title=_markdown_text(definition["title"]),
        group_summary=_markdown_text(definition["summary"]),
        parent_id=_markdown_text(definition["parentId"] or "无"),
        dependencies=(
            "、".join(
                _markdown_text(item)
                for item in definition["decomposition"]["dependsOn"]
            )
            or "无"
        ),
        child_rows="\n".join(child_rows),
        review_section=_render_loop_baseline(
            "GROUP seam Review Loop",
            loop,
            heading_level=2,
            absent_message=(
                "未配置独立 GROUP seam Review；GROUP 完成点是本节点终态。"
            ),
        ),
        skill_hints=rendered_hints,
    )

def render_scheduling_plan(
    hierarchy: dict[str, Any],
    *,
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
    run: dict[str, Any] | None = None,
    delivery_closure: dict[str, Any] | None = None,
) -> str:
    """Render the concise Delivery overview and projection navigation."""

    hierarchy_nodes = iter_hierarchy_nodes(hierarchy)
    definitions = [node["definition"] for node in hierarchy_nodes]
    tasks = [
        definition
        for definition in definitions
        if definition["kind"] == "TASK"
    ]
    groups = [
        definition
        for definition in definitions
        if definition["kind"] == "GROUP"
    ]
    states = {
        node["nodeId"]: node["status"]
        for node in (run or {}).get("nodes", [])
    }
    completed_tasks = sum(
        states.get(_work_item_terminal_node_id(node))
        in {"SUCCEEDED", "COMPLETED"}
        for node in hierarchy_nodes
        if node["definition"]["kind"] == "TASK"
    )
    archived = hierarchy_status == "ARCHIVED"
    status = (
        "ARCHIVED"
        if archived
        else (
            run["status"]
            if run is not None
            else hierarchy_status or "UNKNOWN"
        )
    )
    latest_update = (
        updated_at
        if archived or run is None
        else run["updatedAt"]
    )
    delivery = hierarchy["delivery"]
    status_row = "| " + " | ".join(
        _markdown_text(value)
        for value in (
            delivery["id"],
            delivery["title"],
            _status_text(status),
            (delivery_closure or {}).get("label", "未上线"),
            f"已完成 {completed_tasks}/{len(tasks)}",
            str(len(groups)),
            _utc_plus_8(latest_update),
        )
    ) + " |"
    return OVERVIEW_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        delivery_status="\n".join(
            [
                (
                    "| 交付标识 | 标题 | 当前阶段 | 上线状态 | TASK 进度 | "
                    "GROUP 数量 | 最近更新（UTC+8） |"
                ),
                "|---|---|---|---|---|---:|---|",
                status_row,
            ]
        ),
    )
