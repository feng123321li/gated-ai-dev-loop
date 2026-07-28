from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from typing import Any

from .errors import GatedLoopError, fail
from .evidence import safe_work_item_id
from .jsonio import (
    json_structure_within_limits,
    sha256_bytes,
    strict_json_loads,
)
from .repository import GovernanceRepository, timestamp, timestamp_after


MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_CHUNK_BYTES = 1024 * 1024
MAX_PAYLOAD_CHUNKS = 1024
MAX_UPLOAD_ID_LENGTH = 128
MAX_ACTIVE_PAYLOAD_UPLOADS = 16
MAX_PROJECT_PAYLOAD_BYTES = 256 * 1024 * 1024
PAYLOAD_UPLOAD_TTL_SECONDS = 60 * 60
PAYLOAD_TARGET_ARGUMENTS = {
    "prepare_hierarchy": "hierarchy",
    "task_result": "evidence",
    "remediate_task": "evidence",
    "gate_item": "evidence",
    "accept_item": "evidence",
    "record_independent_review_pass": "evidence",
    "record_independent_review_blocked": "evidence",
    "record_human_review_acceptance": "evidence",
    "record_user_confirmation": "evidence",
}
PAYLOAD_REFERENCE_FIELDS = {
    "uploadId",
    "generationId",
    "sha256",
    "sizeBytes",
}
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_GENERATION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_UPLOAD_STATUSES = {
    "UPLOADING",
    "FINALIZING",
    "READY",
    "INVALID",
}
_INVALIDATING_ERRORS = {
    "MCP_PAYLOAD_HASH_MISMATCH",
    "MCP_PAYLOAD_CORRUPT",
    "MCP_PAYLOAD_STRUCTURE_LIMIT",
    "MCP_PAYLOAD_JSON_INVALID",
}


def _plain_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _valid_generation_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and _GENERATION_ID_RE.fullmatch(value) is not None
    )


def _valid_upload_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_UPLOAD_ID_LENGTH
        and safe_work_item_id(value)
    )


def _encoded(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        fail(
            "MCP_PAYLOAD_ENCODING_INVALID",
            "Payload chunks must contain valid UTF-8 text",
        )


def _validate_upload_id(upload_id: object) -> str:
    if not _valid_upload_id(upload_id):
        fail(
            "MCP_PAYLOAD_UPLOAD_ID_INVALID",
            (
                "upload_id must be a safe lowercase identifier no longer "
                f"than {MAX_UPLOAD_ID_LENGTH} characters"
            ),
            maxLength=MAX_UPLOAD_ID_LENGTH,
        )
    return str(upload_id)


def _validate_generation_id(generation_id: object) -> str:
    if not _valid_generation_id(generation_id):
        fail(
            "MCP_PAYLOAD_GENERATION_INVALID",
            "generation_id must be the 32-character token returned by begin",
        )
    return str(generation_id)


def _validate_target_tool(target_tool: object) -> tuple[str, str]:
    if (
        not isinstance(target_tool, str)
        or target_tool not in PAYLOAD_TARGET_ARGUMENTS
    ):
        fail(
            "MCP_PAYLOAD_TARGET_INVALID",
            "target_tool does not accept staged payload references",
            allowedTargets=sorted(PAYLOAD_TARGET_ARGUMENTS),
        )
    return target_tool, PAYLOAD_TARGET_ARGUMENTS[target_tool]


def _row(
    connection: sqlite3.Connection,
    upload_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT upload_id, generation_id, target_tool, target_argument, "
        "total_chunks, "
        "status, received_bytes, received_chunks, content_sha256, "
        "created_at, expires_at, finalized_at "
        "FROM payload_uploads WHERE upload_id = ?",
        (upload_id,),
    ).fetchone()


def _assert_row(row: sqlite3.Row) -> None:
    target_tool = row["target_tool"]
    if (
        not _valid_upload_id(row["upload_id"])
        or not _valid_generation_id(row["generation_id"])
        or target_tool not in PAYLOAD_TARGET_ARGUMENTS
        or row["target_argument"] != PAYLOAD_TARGET_ARGUMENTS[target_tool]
        or not _plain_int(
            row["total_chunks"],
            minimum=1,
            maximum=MAX_PAYLOAD_CHUNKS,
        )
        or row["status"] not in _UPLOAD_STATUSES
        or not _plain_int(row["received_bytes"])
        or not _plain_int(row["received_chunks"])
        or row["received_bytes"] > MAX_PAYLOAD_BYTES
        or row["received_chunks"] > row["total_chunks"]
        or (
            row["content_sha256"] is not None
            and not _valid_sha256(row["content_sha256"])
        )
        or (
            row["status"] == "READY"
            and not _valid_sha256(row["content_sha256"])
        )
        or not isinstance(row["created_at"], str)
        or not isinstance(row["expires_at"], str)
        or (
            row["finalized_at"] is not None
            and not isinstance(row["finalized_at"], str)
        )
    ):
        fail(
            "MCP_PAYLOAD_CORRUPT",
            "Stored staged-payload metadata is invalid",
            uploadId=row["upload_id"],
        )


def _assert_not_expired(row: sqlite3.Row, now_at: str) -> None:
    if row["expires_at"] <= now_at:
        fail(
            "MCP_PAYLOAD_EXPIRED",
            "Staged payload has expired; begin a new upload",
            uploadId=row["upload_id"],
            expiresAt=row["expires_at"],
        )


def _assert_generation(row: sqlite3.Row, generation_id: str) -> None:
    if row["generation_id"] != generation_id:
        fail(
            "MCP_PAYLOAD_STALE",
            "Staged payload generation no longer matches this request",
            uploadId=row["upload_id"],
        )


def _missing_chunk_indexes(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> list[int]:
    stored = {
        candidate["chunk_index"]
        for candidate in connection.execute(
            "SELECT chunk_index FROM payload_chunks "
            "WHERE upload_id = ? AND generation_id = ? "
            "ORDER BY chunk_index",
            (row["upload_id"], row["generation_id"]),
        )
    }
    return [
        index
        for index in range(row["total_chunks"])
        if index not in stored
    ]


def _payload_ref(row: sqlite3.Row) -> dict[str, object]:
    return {
        "uploadId": row["upload_id"],
        "generationId": row["generation_id"],
        "sha256": row["content_sha256"],
        "sizeBytes": row["received_bytes"],
    }


def _status(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, object]:
    _assert_row(row)
    missing = _missing_chunk_indexes(connection, row)
    result: dict[str, object] = {
        "uploadId": row["upload_id"],
        "generationId": row["generation_id"],
        "status": row["status"],
        "targetTool": row["target_tool"],
        "targetArgument": row["target_argument"],
        "totalChunks": row["total_chunks"],
        "receivedBytes": row["received_bytes"],
        "receivedChunks": row["received_chunks"],
        "missingChunkCount": len(missing),
        "nextMissingChunkIndex": missing[0] if missing else None,
        "createdAt": row["created_at"],
        "expiresAt": row["expires_at"],
        "finalizedAt": row["finalized_at"],
    }
    if row["status"] == "READY":
        result["payloadRef"] = _payload_ref(row)
    return result


def _cleanup_expired(
    connection: sqlite3.Connection,
    now_at: str,
) -> None:
    connection.execute(
        "DELETE FROM payload_uploads WHERE expires_at <= ?",
        (now_at,),
    )


def begin_payload_upload(
    *,
    root: str,
    upload_id: str,
    target_tool: str,
    total_chunks: int,
    explicit_dogfood: bool = False,
) -> dict[str, object]:
    upload_id = _validate_upload_id(upload_id)
    target_tool, target_argument = _validate_target_tool(target_tool)
    if not _plain_int(
        total_chunks,
        minimum=1,
        maximum=MAX_PAYLOAD_CHUNKS,
    ):
        fail(
            "MCP_PAYLOAD_CHUNK_COUNT_INVALID",
            "total_chunks is outside the allowed range",
            maxChunks=MAX_PAYLOAD_CHUNKS,
        )

    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    now_at = timestamp(repository.now)
    expires_at = timestamp_after(now_at, PAYLOAD_UPLOAD_TTL_SECONDS)
    with repository.staging_transaction() as connection:
        existing = _row(connection, upload_id)
        if existing is not None and existing["expires_at"] <= now_at:
            connection.execute(
                "DELETE FROM payload_uploads WHERE upload_id = ?",
                (upload_id,),
            )
            existing = None
        _cleanup_expired(connection, now_at)
        if existing is not None:
            _assert_row(existing)
            expected = (
                target_tool,
                target_argument,
                total_chunks,
            )
            actual = (
                existing["target_tool"],
                existing["target_argument"],
                existing["total_chunks"],
            )
            if actual != expected:
                fail(
                    "MCP_PAYLOAD_UPLOAD_CONFLICT",
                    "upload_id is already bound to a different payload",
                    uploadId=upload_id,
                )
            return _status(connection, existing)
        active_uploads = connection.execute(
            "SELECT COUNT(*) FROM payload_uploads WHERE expires_at > ?",
            (now_at,),
        ).fetchone()[0]
        if active_uploads >= MAX_ACTIVE_PAYLOAD_UPLOADS:
            fail(
                "MCP_PAYLOAD_PROJECT_QUOTA_EXCEEDED",
                "Project has too many active staged payload uploads",
                maxActiveUploads=MAX_ACTIVE_PAYLOAD_UPLOADS,
            )
        generation_id = secrets.token_hex(16)
        connection.execute(
            "INSERT INTO payload_uploads("
            "upload_id, generation_id, target_tool, target_argument, "
            "total_chunks, "
            "status, received_bytes, received_chunks, content_sha256, "
            "created_at, expires_at, finalized_at"
            ") VALUES (?, ?, ?, ?, ?, 'UPLOADING', 0, 0, NULL, ?, ?, NULL)",
            (
                upload_id,
                generation_id,
                target_tool,
                target_argument,
                total_chunks,
                now_at,
                expires_at,
            ),
        )
        created = _row(connection, upload_id)
        assert created is not None
        result = _status(connection, created)
        result["limits"] = {
            "maxPayloadBytes": MAX_PAYLOAD_BYTES,
            "maxChunkBytes": MAX_PAYLOAD_CHUNK_BYTES,
            "maxChunks": MAX_PAYLOAD_CHUNKS,
            "maxActiveUploads": MAX_ACTIVE_PAYLOAD_UPLOADS,
            "maxProjectPayloadBytes": MAX_PROJECT_PAYLOAD_BYTES,
        }
        return result


def append_payload_chunk(
    *,
    root: str,
    upload_id: str,
    generation_id: str,
    chunk_index: int,
    data: str,
    explicit_dogfood: bool = False,
) -> dict[str, object]:
    upload_id = _validate_upload_id(upload_id)
    generation_id = _validate_generation_id(generation_id)
    if not _plain_int(chunk_index):
        fail(
            "MCP_PAYLOAD_CHUNK_INDEX_INVALID",
            "chunk_index must be a non-negative integer",
        )
    if not isinstance(data, str) or not data:
        fail(
            "MCP_PAYLOAD_CHUNK_INVALID",
            "data must be a non-empty text chunk",
        )
    data_bytes = _encoded(data)
    byte_size = len(data_bytes)
    if byte_size > MAX_PAYLOAD_CHUNK_BYTES:
        fail(
            "MCP_PAYLOAD_CHUNK_TOO_LARGE",
            "Payload chunk exceeds the configured size limit",
            maxChunkBytes=MAX_PAYLOAD_CHUNK_BYTES,
            chunkBytes=byte_size,
        )
    actual_sha256 = sha256_bytes(data_bytes)

    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    now_at = timestamp(repository.now)
    with repository.staging_transaction() as connection:
        row = _row(connection, upload_id)
        if row is None:
            fail(
                "MCP_PAYLOAD_NOT_FOUND",
                "Staged payload upload does not exist",
                uploadId=upload_id,
            )
        _assert_row(row)
        _assert_generation(row, generation_id)
        _assert_not_expired(row, now_at)
        if row["status"] != "UPLOADING":
            fail(
                "MCP_PAYLOAD_NOT_APPENDABLE",
                "Staged payload no longer accepts chunks",
                uploadId=upload_id,
                status=row["status"],
            )
        if chunk_index >= row["total_chunks"]:
            fail(
                "MCP_PAYLOAD_CHUNK_INDEX_INVALID",
                "chunk_index is outside the declared chunk range",
                totalChunks=row["total_chunks"],
            )
        existing = connection.execute(
            "SELECT chunk_sha256, byte_size, chunk_text "
            "FROM payload_chunks WHERE upload_id = ? AND generation_id = ? "
            "AND chunk_index = ?",
            (upload_id, generation_id, chunk_index),
        ).fetchone()
        already_stored = existing is not None
        if existing is not None:
            if (
                existing["chunk_sha256"] != actual_sha256
                or existing["byte_size"] != byte_size
                or existing["chunk_text"] != data
            ):
                fail(
                    "MCP_PAYLOAD_CHUNK_CONFLICT",
                    "Chunk index is already bound to different content",
                    uploadId=upload_id,
                    chunkIndex=chunk_index,
                )
        else:
            if row["received_bytes"] + byte_size > MAX_PAYLOAD_BYTES:
                fail(
                    "MCP_PAYLOAD_TOO_LARGE",
                    "Staged payload exceeds the configured total size limit",
                    maxBytes=MAX_PAYLOAD_BYTES,
                    receivedBytes=row["received_bytes"],
                    chunkBytes=byte_size,
                )
            project_bytes = connection.execute(
                "SELECT COALESCE(SUM(received_bytes), 0) "
                "FROM payload_uploads WHERE expires_at > ?",
                (now_at,),
            ).fetchone()[0]
            if project_bytes + byte_size > MAX_PROJECT_PAYLOAD_BYTES:
                fail(
                    "MCP_PAYLOAD_PROJECT_QUOTA_EXCEEDED",
                    "Project staged-payload byte quota would be exceeded",
                    maxProjectPayloadBytes=MAX_PROJECT_PAYLOAD_BYTES,
                    stagedBytes=project_bytes,
                    chunkBytes=byte_size,
                )
            connection.execute(
                "INSERT INTO payload_chunks("
                "upload_id, generation_id, chunk_index, chunk_sha256, "
                "byte_size, chunk_text"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    upload_id,
                    generation_id,
                    chunk_index,
                    actual_sha256,
                    byte_size,
                    data,
                ),
            )
            connection.execute(
                "UPDATE payload_uploads SET "
                "received_bytes = received_bytes + ?, "
                "received_chunks = received_chunks + 1 "
                "WHERE upload_id = ? AND generation_id = ?",
                (byte_size, upload_id, generation_id),
            )
        current = _row(connection, upload_id)
        assert current is not None
        _assert_generation(current, generation_id)
        result = _status(connection, current)
        result["alreadyStored"] = already_stored
        result["chunkSha256"] = actual_sha256
        result["chunkBytes"] = byte_size
        return result


def _read_payload(
    repository: GovernanceRepository,
    upload_id: str,
    generation_id: str,
    *,
    expected_statuses: set[str],
) -> tuple[sqlite3.Row, dict[str, Any], str]:
    now_at = timestamp(repository.now)
    try:
        with repository._read_connection() as connection:
            row = _row(connection, upload_id)
            if row is None:
                fail(
                    "MCP_PAYLOAD_NOT_FOUND",
                    "Staged payload upload does not exist",
                    uploadId=upload_id,
                )
            _assert_row(row)
            _assert_generation(row, generation_id)
            _assert_not_expired(row, now_at)
            if row["status"] not in expected_statuses:
                fail(
                    "MCP_PAYLOAD_NOT_READY",
                    "Staged payload is not ready for this operation",
                    uploadId=upload_id,
                    status=row["status"],
                )
            texts: list[str] = []
            total_bytes = 0
            expected_index = 0
            content_hash = hashlib.sha256()
            for chunk in connection.execute(
                "SELECT chunk_index, chunk_sha256, byte_size, chunk_text "
                "FROM payload_chunks WHERE upload_id = ? "
                "AND generation_id = ? ORDER BY chunk_index",
                (upload_id, generation_id),
            ):
                if chunk["chunk_index"] != expected_index:
                    fail(
                        "MCP_PAYLOAD_CORRUPT",
                        "Stored staged-payload chunks are incomplete",
                        uploadId=upload_id,
                    )
                text = chunk["chunk_text"]
                if not isinstance(text, str):
                    fail(
                        "MCP_PAYLOAD_CORRUPT",
                        "Stored staged-payload chunk is invalid",
                        uploadId=upload_id,
                    )
                encoded = _encoded(text)
                if (
                    len(encoded) != chunk["byte_size"]
                    or len(encoded) > MAX_PAYLOAD_CHUNK_BYTES
                    or sha256_bytes(encoded) != chunk["chunk_sha256"]
                ):
                    fail(
                        "MCP_PAYLOAD_CORRUPT",
                        "Stored staged-payload chunk failed integrity checks",
                        uploadId=upload_id,
                        chunkIndex=chunk["chunk_index"],
                    )
                texts.append(text)
                total_bytes += len(encoded)
                content_hash.update(encoded)
                expected_index += 1
            if expected_index != row["total_chunks"]:
                fail(
                    "MCP_PAYLOAD_CORRUPT",
                    "Stored staged-payload chunks are incomplete",
                    uploadId=upload_id,
                )
            if (
                total_bytes != row["received_bytes"]
                or total_bytes > MAX_PAYLOAD_BYTES
            ):
                fail(
                    "MCP_PAYLOAD_CORRUPT",
                    "Stored staged-payload size does not match its manifest",
                    uploadId=upload_id,
                )
            actual_sha256 = content_hash.hexdigest()
            if (
                row["content_sha256"] is not None
                and actual_sha256 != row["content_sha256"]
            ):
                raise GatedLoopError(
                    "MCP_PAYLOAD_HASH_MISMATCH",
                    (
                        "Staged payload SHA-256 does not match its finalized "
                        "manifest"
                    ),
                    details={
                        "uploadId": upload_id,
                        "expectedSha256": row["content_sha256"],
                        "actualSha256": actual_sha256,
                    },
                )
            payload_text = "".join(texts)
            if not json_structure_within_limits(payload_text):
                raise GatedLoopError(
                    "MCP_PAYLOAD_STRUCTURE_LIMIT",
                    "Staged JSON exceeds the server structure limit",
                    details={"uploadId": upload_id},
                )
            try:
                payload = strict_json_loads(payload_text)
            except (
                ValueError,
                UnicodeError,
                RecursionError,
            ) as error:
                raise GatedLoopError(
                    "MCP_PAYLOAD_JSON_INVALID",
                    "Staged payload must contain strict JSON",
                    details={"uploadId": upload_id},
                ) from error
            if not isinstance(payload, dict):
                fail(
                    "MCP_PAYLOAD_JSON_INVALID",
                    "Staged payload must contain a top-level JSON object",
                    uploadId=upload_id,
                )
            return row, payload, actual_sha256
    except MemoryError as error:
        raise GatedLoopError(
            "MCP_PAYLOAD_RESOURCE_LIMIT",
            "Server memory was insufficient to reconstruct the staged payload",
            details={"uploadId": upload_id},
        ) from error


def _mark_invalid(
    repository: GovernanceRepository,
    upload_id: str,
    generation_id: str,
    *,
    explicit_dogfood: bool,
) -> None:
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    with repository.staging_transaction() as connection:
        connection.execute(
            "UPDATE payload_uploads "
            "SET status = 'INVALID', content_sha256 = NULL "
            "WHERE upload_id = ? AND generation_id = ? "
            "AND status IN ('FINALIZING', 'READY')",
            (upload_id, generation_id),
        )


def finalize_payload_upload(
    *,
    root: str,
    upload_id: str,
    generation_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, object]:
    upload_id = _validate_upload_id(upload_id)
    generation_id = _validate_generation_id(generation_id)
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    now_at = timestamp(repository.now)
    with repository.staging_transaction() as connection:
        row = _row(connection, upload_id)
        if row is None:
            fail(
                "MCP_PAYLOAD_NOT_FOUND",
                "Staged payload upload does not exist",
                uploadId=upload_id,
            )
        _assert_row(row)
        _assert_generation(row, generation_id)
        _assert_not_expired(row, now_at)
        if row["status"] == "INVALID":
            fail(
                "MCP_PAYLOAD_INVALID",
                "Staged payload is invalid; abort it and begin again",
                uploadId=upload_id,
            )
        if row["status"] == "UPLOADING":
            missing = _missing_chunk_indexes(connection, row)
            if missing:
                fail(
                    "MCP_PAYLOAD_INCOMPLETE",
                    "Staged payload is missing declared chunks",
                    uploadId=upload_id,
                    missingChunkCount=len(missing),
                    missingChunkIndexes=missing[:32],
                )
            connection.execute(
                "UPDATE payload_uploads SET status = 'FINALIZING' "
                "WHERE upload_id = ? AND generation_id = ?",
                (upload_id, generation_id),
            )

    try:
        row, payload, content_sha256 = _read_payload(
            repository,
            upload_id,
            generation_id,
            expected_statuses={"FINALIZING", "READY"},
        )
    except GatedLoopError as error:
        if error.code in _INVALIDATING_ERRORS:
            _mark_invalid(
                repository,
                upload_id,
                generation_id,
                explicit_dogfood=explicit_dogfood,
            )
        raise

    finalized_at = timestamp(repository.now)
    with repository.staging_transaction() as connection:
        current = _row(connection, upload_id)
        if current is None:
            fail(
                "MCP_PAYLOAD_NOT_FOUND",
                "Staged payload upload does not exist",
                uploadId=upload_id,
            )
        _assert_row(current)
        _assert_generation(current, generation_id)
        _assert_not_expired(current, finalized_at)
        if current["status"] == "FINALIZING":
            connection.execute(
                "UPDATE payload_uploads "
                "SET status = 'READY', content_sha256 = ?, finalized_at = ? "
                "WHERE upload_id = ? AND generation_id = ?",
                (
                    content_sha256,
                    finalized_at,
                    upload_id,
                    generation_id,
                ),
            )
            current = _row(connection, upload_id)
            assert current is not None
        elif current["status"] != "READY":
            fail(
                "MCP_PAYLOAD_NOT_READY",
                "Staged payload could not be finalized",
                uploadId=upload_id,
                status=current["status"],
            )
        result = _status(connection, current)
        result["topLevelMemberCount"] = len(payload)
        return result


def get_payload_upload_status(
    *,
    root: str,
    upload_id: str,
    generation_id: str,
) -> dict[str, object]:
    upload_id = _validate_upload_id(upload_id)
    generation_id = _validate_generation_id(generation_id)
    repository = GovernanceRepository(root)
    if not repository.database_path.is_file():
        fail(
            "MCP_PAYLOAD_NOT_FOUND",
            "Staged payload upload does not exist",
            uploadId=upload_id,
        )
    now_at = timestamp(repository.now)
    with repository._read_connection() as connection:
        row = _row(connection, upload_id)
        if row is None:
            fail(
                "MCP_PAYLOAD_NOT_FOUND",
                "Staged payload upload does not exist",
                uploadId=upload_id,
            )
        _assert_row(row)
        _assert_generation(row, generation_id)
        _assert_not_expired(row, now_at)
        return _status(connection, row)


def abort_payload_upload(
    *,
    root: str,
    upload_id: str,
    generation_id: str,
    explicit_dogfood: bool = False,
) -> dict[str, object]:
    upload_id = _validate_upload_id(upload_id)
    generation_id = _validate_generation_id(generation_id)
    repository = GovernanceRepository(root)
    repository.assert_self_hosting_dogfood(explicit_dogfood)
    if not repository.database_path.is_file():
        return {
            "uploadId": upload_id,
            "generationId": generation_id,
            "aborted": False,
        }
    with repository.staging_transaction() as connection:
        row = _row(connection, upload_id)
        if row is None:
            return {
                "uploadId": upload_id,
                "generationId": generation_id,
                "aborted": False,
            }
        _assert_row(row)
        _assert_generation(row, generation_id)
        deleted = connection.execute(
            "DELETE FROM payload_uploads "
            "WHERE upload_id = ? AND generation_id = ?",
            (upload_id, generation_id),
        ).rowcount
        return {
            "uploadId": upload_id,
            "generationId": generation_id,
            "aborted": deleted == 1,
        }


def _validated_reference(
    value: object,
) -> dict[str, object] | None:
    if not isinstance(value, dict) or set(value) != {"payloadRef"}:
        return None
    reference = value["payloadRef"]
    if (
        not isinstance(reference, dict)
        or set(reference) != PAYLOAD_REFERENCE_FIELDS
        or not _valid_upload_id(reference.get("uploadId"))
        or not _valid_generation_id(reference.get("generationId"))
        or not _valid_sha256(reference.get("sha256"))
        or not _plain_int(
            reference.get("sizeBytes"),
            minimum=1,
            maximum=MAX_PAYLOAD_BYTES,
        )
    ):
        fail(
            "MCP_PAYLOAD_REFERENCE_INVALID",
            "payloadRef does not match the staged-payload reference contract",
        )
    return reference


def resolve_payload_argument(
    *,
    root: str,
    target_tool: str,
    target_argument: str,
    value: object,
    explicit_dogfood: bool = False,
) -> object:
    reference = _validated_reference(value)
    if reference is None:
        return value
    expected_argument = PAYLOAD_TARGET_ARGUMENTS.get(target_tool)
    if expected_argument != target_argument:
        fail(
            "MCP_PAYLOAD_TARGET_MISMATCH",
            "The target tool argument does not accept this payload reference",
            targetTool=target_tool,
            targetArgument=target_argument,
        )
    repository = GovernanceRepository(root)
    upload_id = str(reference["uploadId"])
    generation_id = str(reference["generationId"])
    try:
        row, payload, content_sha256 = _read_payload(
            repository,
            upload_id,
            generation_id,
            expected_statuses={"READY"},
        )
    except GatedLoopError as error:
        if error.code in _INVALIDATING_ERRORS:
            _mark_invalid(
                repository,
                upload_id,
                generation_id,
                explicit_dogfood=explicit_dogfood,
            )
        raise
    if (
        row["target_tool"] != target_tool
        or row["generation_id"] != generation_id
        or row["target_argument"] != target_argument
        or content_sha256 != reference["sha256"]
        or row["received_bytes"] != reference["sizeBytes"]
    ):
        fail(
            "MCP_PAYLOAD_TARGET_MISMATCH",
            "payloadRef is bound to a different tool, argument, or content",
            uploadId=reference["uploadId"],
            expectedTool=row["target_tool"],
            expectedArgument=row["target_argument"],
        )
    return payload
