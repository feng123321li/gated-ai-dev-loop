from __future__ import annotations

from typing import Any

from .payloads import (
    abort_payload_upload,
    append_payload_chunk,
    begin_payload_upload,
    finalize_payload_upload,
    get_payload_upload_status,
)
from .operation_support import (
    NOT_HANDLED,
    OperationContext,
)


def execute_payload_operation(
    name: str,
    arguments: dict[str, Any],
    *,
    context: OperationContext,
) -> Any:
    root = context.root
    dogfood = context.explicit_dogfood

    if name == "begin_payload_upload":
        return begin_payload_upload(
            root=root,
            upload_id=arguments["upload_id"],
            target_tool=arguments["target_tool"],
            total_chunks=arguments["total_chunks"],
            explicit_dogfood=dogfood,
        )
    if name == "append_payload_chunk":
        return append_payload_chunk(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
            chunk_index=arguments["chunk_index"],
            data=arguments["data"],
            explicit_dogfood=dogfood,
        )
    if name == "finalize_payload_upload":
        return finalize_payload_upload(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
            explicit_dogfood=dogfood,
        )
    if name == "payload_upload_status":
        return get_payload_upload_status(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
        )
    if name == "abort_payload_upload":
        return abort_payload_upload(
            root=root,
            upload_id=arguments["upload_id"],
            generation_id=arguments["generation_id"],
            explicit_dogfood=dogfood,
        )
    return NOT_HANDLED
