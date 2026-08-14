from __future__ import annotations

from .model_rendering_common import (
    Any,
    INTERFACES_PROJECTION_TEMPLATE,
    INTERFACE_CHANGE_TYPE_TEXT,
    INTERFACE_DETAIL_PROJECTION_TEMPLATE,
    PROJECTION_TEMPLATE_VERSION,
    _markdown_text,
    _table_row,
    _task_interface_declarations,
)
from .model_rendering_baseline import (
    _interface_call_identifier,
    _interface_change_table,
    _interface_scalar,
)
from .model_rendering_acceptance import _work_item_status_lines


def _interface_projection_values(
    interface: dict[str, Any],
) -> dict[str, Any]:
    protocol = _interface_scalar(
        interface.get("protocol"),
        fallback="未声明",
    ).upper()
    name = _interface_scalar(
        interface.get("name"),
        fallback="未命名接口",
    )
    summary = _interface_scalar(
        interface.get("summary"),
        fallback="未提供简介",
    )
    change_type = _interface_scalar(
        interface.get("changeType"),
        fallback="UNSPECIFIED",
    ).upper()
    before_value = interface.get("before")
    after_value = interface.get("after")
    before = before_value if isinstance(before_value, dict) else None
    after = after_value if isinstance(after_value, dict) else None
    return {
        "protocol": protocol,
        "name": name,
        "summary": summary,
        "changeType": change_type,
        "changeText": INTERFACE_CHANGE_TYPE_TEXT.get(
            change_type,
            "未声明",
        ),
        "before": before,
        "after": after,
        "beforeIdentifier": _interface_call_identifier(protocol, before),
        "afterIdentifier": _interface_call_identifier(protocol, after),
    }

def _interface_filename_slug(value: str) -> str:
    characters: list[str] = []
    pending_separator = False
    for character in value.casefold():
        if character.isascii() and character.isalnum():
            if pending_separator and characters:
                characters.append("-")
            characters.append(character)
            pending_separator = False
        else:
            pending_separator = True
    return "".join(characters).strip("-")

def _interface_document_filename(
    position: int,
    values: dict[str, Any],
) -> str:
    name_slug = _interface_filename_slug(values["name"])
    identity = values["name"] if name_slug else values["afterIdentifier"]
    if identity == "不适用":
        identity = values["beforeIdentifier"]
    slug = _interface_filename_slug(
        f"{values['protocol']}-{identity}"
    )[:64].rstrip("-")
    return f"{position:03d}-{slug or 'interface'}.md"

def _interface_schema_type(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("type", "controllerReturnType"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float)) and str(candidate).strip():
            return str(candidate).strip()
    return None

def _dubbo_method_signature(snapshot: dict[str, Any]) -> str:
    explicit = snapshot.get("signature")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    method = _interface_scalar(
        snapshot.get("method"),
        fallback="未声明方法",
    )
    response_type = _interface_schema_type(snapshot.get("response"))
    request = snapshot.get("request")
    parameters: list[str] = []
    if isinstance(request, dict):
        request_type = _interface_schema_type(request)
        request_name = request.get("name")
        if request_type is not None:
            parameters.append(
                " ".join(
                    part
                    for part in (
                        request_type,
                        (
                            str(request_name).strip()
                            if isinstance(request_name, str)
                            and request_name.strip()
                            else ""
                        ),
                    )
                    if part
                )
            )
    elif isinstance(request, list):
        for parameter in request:
            if not isinstance(parameter, dict):
                continue
            parameter_type = _interface_scalar(
                parameter.get("type"),
                fallback="Object",
            )
            parameter_name = _interface_scalar(
                parameter.get("name"),
                fallback="arg",
            )
            parameters.append(f"{parameter_type} {parameter_name}")
    return (
        f"{response_type or '未声明返回类型'} {method}"
        f"({', '.join(parameters)})"
    )

def _interface_protocol_metadata(
    values: dict[str, Any],
) -> list[str]:
    snapshot = values["after"] or values["before"]
    if snapshot is None:
        return []
    protocol = values["protocol"]
    if protocol == "HTTP":
        lines = [
            (
                "- 方法："
                f"{_markdown_text(_interface_scalar(snapshot.get('method'), fallback='未声明'))}"
            ),
            (
                "- 路径："
                f"{_markdown_text(_interface_scalar(snapshot.get('path'), fallback='未声明'))}"
            ),
        ]
        content_type = snapshot.get("contentType")
        if isinstance(content_type, str) and content_type.strip():
            lines.append(f"- Content-Type：{_markdown_text(content_type)}")
        response_type = _interface_schema_type(snapshot.get("response"))
        if response_type is not None:
            lines.append(f"- 返回类型：{_markdown_text(response_type)}")
        return lines
    if protocol == "DUBBO":
        return [
            (
                "- 接口："
                f"{_markdown_text(_interface_scalar(snapshot.get('service'), fallback='未声明'))}"
            ),
            f"- 方法：{_markdown_text(_dubbo_method_signature(snapshot))}",
        ]
    return []

def _interface_table_section(
    heading: str,
    table: list[str],
) -> str:
    return "\n".join([f"### {heading}", "", *table])

def _http_request_contract(
    before: object,
    after: object,
) -> str:
    sections: list[str] = []
    for path_group, heading in (
        ("path", "Path 参数"),
        ("query", "Query 参数"),
        ("header", "请求头"),
        ("body", "请求体"),
        ("business", "业务参数"),
        ("context", "上下文参数"),
        ("", "请求参数"),
    ):
        table = _interface_change_table(
            before,
            after,
            section="request",
            include_example=True,
            path_group=path_group,
            strip_path_group=bool(path_group),
            omit_container_rows=True,
            render_empty=False,
        )
        if table:
            sections.append(_interface_table_section(heading, table))
    return "\n\n".join(sections) if sections else "无"

def _http_response_contract(
    before: object,
    after: object,
) -> str:
    table = _interface_change_table(
        before,
        after,
        section="response",
        include_required=False,
        include_example=True,
        omit_container_rows=True,
        render_empty=False,
    )
    return _interface_table_section("响应参数", table) if table else "无"

def _dubbo_contract_table(
    before: object,
    after: object,
    *,
    section: str,
    heading: str,
) -> str:
    table = _interface_change_table(
        before,
        after,
        section=section,
        include_required=True,
        include_max_length=True,
        include_example=True,
        omit_container_rows=True,
        render_empty=False,
    )
    return _interface_table_section(heading, table) if table else "无"

def _render_task_interface_detail(
    definition: dict[str, Any],
    values: dict[str, Any],
) -> str:
    before = values["before"]
    after = values["after"]
    metadata = "\n".join(
        [
            f"- 来源 TASK：{_markdown_text(definition['id'])}",
            f"- 协议：{_markdown_text(values['protocol'])}",
            f"- 接口名称：{_markdown_text(values['name'])}",
            f"- 变更类型：{_markdown_text(values['changeText'])}",
            f"- 简介：{_markdown_text(values['summary'])}",
            (
                "- 调用标识（修改前 → 修改后）："
                f"{_markdown_text(values['beforeIdentifier'])} → "
                f"{_markdown_text(values['afterIdentifier'])}"
            ),
            *_interface_protocol_metadata(values),
        ]
    )
    before_request = (
        before.get("request", "未声明") if before is not None else None
    )
    after_request = (
        after.get("request", "未声明") if after is not None else None
    )
    before_response = (
        before.get("response", "未声明") if before is not None else None
    )
    after_response = (
        after.get("response", "未声明") if after is not None else None
    )
    if values["protocol"] == "HTTP":
        request_table = _http_request_contract(
            before_request,
            after_request,
        )
        response_table = _http_response_contract(
            before_response,
            after_response,
        )
    elif values["protocol"] == "DUBBO":
        request_table = _dubbo_contract_table(
            before_request,
            after_request,
            section="request",
            heading="调用参数",
        )
        response_table = _dubbo_contract_table(
            before_response,
            after_response,
            section="response",
            heading="返回结果",
        )
    else:
        request_table = "\n".join(
            _interface_change_table(
                before_request,
                after_request,
                section="request",
            )
        )
        response_table = "\n".join(
            _interface_change_table(
                before_response,
                after_response,
                section="response",
                include_required=False,
            )
        )
    return INTERFACE_DETAIL_PROJECTION_TEMPLATE.substitute(
        protocol=_markdown_text(values["protocol"]),
        name=_markdown_text(values["name"]),
        interface_metadata=metadata,
        request_table=request_table,
        response_table=response_table,
    )

def render_task_interfaces(
    definition: dict[str, Any],
    *,
    delivery_baseline: str = "../../baseline.md",
    hierarchy_fingerprint: str | None = None,
    graph_fingerprint: str | None = None,
    hierarchy_status: str | None = None,
    updated_at: str | None = None,
) -> str:
    if definition["kind"] != "TASK":
        raise ValueError("Interface projection requires a TASK definition")
    declarations = _task_interface_declarations(definition)
    rows: list[str] = []
    for position, interface in enumerate(declarations, start=1):
        values = _interface_projection_values(interface)
        filename = _interface_document_filename(position, values)
        name_link = (
            f"[{_markdown_text(values['name'])}]"
            f"(interfaces/{filename})"
        )
        if values["changeType"] == "DELETE":
            name_link = f"~~{name_link}~~"
        rows.append(
            _table_row(
                [
                    definition["id"],
                    values["protocol"],
                    name_link,
                    values["changeText"],
                    values["beforeIdentifier"],
                    values["afterIdentifier"],
                    values["summary"],
                ],
                raw_indices={2},
            )
        )
    interface_rows = (
        "\n".join(
            [
                (
                    "| 来源 TASK | 协议 | 接口名称 | 变更类型 | "
                    "修改前调用标识 | 修改后调用标识 | 简介 |"
                ),
                "|---|---|---|---|---|---|---|",
                *rows,
            ]
        )
        if rows
        else "- 当前需求未显式声明接口契约。"
    )
    return INTERFACES_PROJECTION_TEMPLATE.substitute(
        template_version=str(PROJECTION_TEMPLATE_VERSION),
        interface_status="\n".join(
            _work_item_status_lines(
                definition,
                hierarchy_fingerprint=hierarchy_fingerprint,
                graph_fingerprint=graph_fingerprint,
                hierarchy_status=hierarchy_status,
                updated_at=updated_at,
            )
        ),
        interface_rows=interface_rows,
        delivery_baseline=delivery_baseline,
    )

def render_task_interface_documents(
    definition: dict[str, Any],
    **index_arguments: Any,
) -> dict[str, str]:
    if definition["kind"] != "TASK":
        raise ValueError("Interface projection requires a TASK definition")
    documents = {
        "interfaces.md": render_task_interfaces(
            definition,
            **index_arguments,
        )
    }
    for position, interface in enumerate(
        _task_interface_declarations(definition),
        start=1,
    ):
        values = _interface_projection_values(interface)
        filename = _interface_document_filename(position, values)
        documents[f"interfaces/{filename}"] = (
            _render_task_interface_detail(definition, values)
        )
    return documents
