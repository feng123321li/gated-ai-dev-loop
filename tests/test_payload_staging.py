from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hdg.errors import GatedLoopError
from hdg.jsonio import canonical_json
from hdg.mcp_tools import call_tool
from hdg.payloads import (
    MAX_PAYLOAD_BYTES,
    abort_payload_upload,
    append_payload_chunk,
    begin_payload_upload,
    finalize_payload_upload,
    get_payload_upload_status,
)
from hdg.repository import GovernanceRepository

from .fixtures import task_hierarchy


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_text(value: str, count: int = 3) -> list[str]:
    width = max(1, (len(value) + count - 1) // count)
    return [
        value[index:index + width]
        for index in range(0, len(value), width)
    ]


class PayloadStagingTests(unittest.TestCase):
    def _stage(
        self,
        root: str,
        *,
        upload_id: str,
        target_tool: str,
        payload: object,
        chunk_count: int = 3,
    ) -> dict[str, object]:
        payload_text = canonical_json(payload)
        chunks = _split_text(payload_text, chunk_count)
        begun = begin_payload_upload(
            root=root,
            upload_id=upload_id,
            target_tool=target_tool,
            total_chunks=len(chunks),
        )
        for index, chunk in enumerate(chunks):
            append_payload_chunk(
                root=root,
                upload_id=upload_id,
                generation_id=begun["generationId"],
                chunk_index=index,
                data=chunk,
            )
        return finalize_payload_upload(
            root=root,
            upload_id=upload_id,
            generation_id=begun["generationId"],
        )

    def test_finalized_reference_prepares_hierarchy_without_returning_content(
        self,
    ) -> None:
        marker = "staged-payload-content-must-not-return"
        hierarchy = task_hierarchy(title=marker)
        with tempfile.TemporaryDirectory() as temporary:
            finalized = self._stage(
                temporary,
                upload_id="upload-hierarchy",
                target_tool="prepare_hierarchy",
                payload=hierarchy,
            )

            self.assertEqual(finalized["status"], "READY")
            self.assertNotIn(
                marker,
                json.dumps(finalized, ensure_ascii=False),
            )
            reference = {"payloadRef": finalized["payloadRef"]}
            prepared = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": reference,
                    "host_runtime": "codex",
                },
                root=temporary,
            )

            self.assertEqual(prepared["rootId"], "t-python-controller")
            repository = GovernanceRepository(temporary)
            registry = repository.read_registry()
            entry = repository.item_by_id(
                registry,
                "t-python-controller",
            )
            definition = repository.read_package(
                registry,
                entry,
            )[0]
            self.assertEqual(definition["title"], marker)

    def test_mcp_staging_tools_compute_hashes_without_client_hash_arguments(
        self,
    ) -> None:
        payload_text = canonical_json({"value": "服务端哈希🙂"})
        with tempfile.TemporaryDirectory() as temporary:
            begun = call_tool(
                "begin_payload_upload",
                {
                    "upload_id": "upload-through-mcp",
                    "target_tool": "prepare_hierarchy",
                    "total_chunks": 1,
                },
                root=temporary,
            )
            appended = call_tool(
                "append_payload_chunk",
                {
                    "upload_id": "upload-through-mcp",
                    "generation_id": begun["generationId"],
                    "chunk_index": 0,
                    "data": payload_text,
                },
                root=temporary,
            )
            finalized = call_tool(
                "finalize_payload_upload",
                {
                    "upload_id": "upload-through-mcp",
                    "generation_id": begun["generationId"],
                },
                root=temporary,
            )

            self.assertEqual(begun["status"], "UPLOADING")
            self.assertEqual(appended["chunkSha256"], _sha256(payload_text))
            self.assertEqual(
                finalized["payloadRef"]["sha256"],
                _sha256(payload_text),
            )
            self.assertEqual(
                finalized["payloadRef"]["sizeBytes"],
                len(payload_text.encode("utf-8")),
            )

    def test_upload_and_chunk_retries_are_idempotent_but_conflicts_fail(
        self,
    ) -> None:
        payload_text = canonical_json({"value": "same"})
        begin_arguments = {
            "upload_id": "upload-idempotent",
            "target_tool": "prepare_hierarchy",
            "total_chunks": 1,
        }
        with tempfile.TemporaryDirectory() as temporary:
            first_begin = begin_payload_upload(
                root=temporary,
                **begin_arguments,
            )
            second_begin = begin_payload_upload(
                root=temporary,
                **begin_arguments,
            )
            chunk_arguments = {
                "upload_id": "upload-idempotent",
                "generation_id": first_begin["generationId"],
                "chunk_index": 0,
                "data": payload_text,
            }
            first_chunk = append_payload_chunk(
                root=temporary,
                **chunk_arguments,
            )
            second_chunk = append_payload_chunk(
                root=temporary,
                **chunk_arguments,
            )

            self.assertEqual(first_begin["uploadId"], second_begin["uploadId"])
            self.assertIs(first_chunk["alreadyStored"], False)
            self.assertIs(second_chunk["alreadyStored"], True)
            with self.assertRaises(GatedLoopError) as raised:
                append_payload_chunk(
                    root=temporary,
                    upload_id="upload-idempotent",
                    generation_id=first_begin["generationId"],
                    chunk_index=0,
                    data='{"value":"different"}',
                )
            self.assertEqual(
                raised.exception.code,
                "MCP_PAYLOAD_CHUNK_CONFLICT",
            )

    def test_finalize_rejects_missing_chunks_and_computes_full_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload_text = canonical_json({"value": "complete"})
            chunks = _split_text(payload_text, 2)
            begun = begin_payload_upload(
                root=temporary,
                upload_id="upload-incomplete",
                target_tool="prepare_hierarchy",
                total_chunks=len(chunks),
            )
            append_payload_chunk(
                root=temporary,
                upload_id="upload-incomplete",
                generation_id=begun["generationId"],
                chunk_index=0,
                data=chunks[0],
            )

            with self.assertRaises(GatedLoopError) as incomplete:
                finalize_payload_upload(
                    root=temporary,
                    upload_id="upload-incomplete",
                    generation_id=begun["generationId"],
                )
            self.assertEqual(
                incomplete.exception.code,
                "MCP_PAYLOAD_INCOMPLETE",
            )
            self.assertEqual(
                incomplete.exception.details["missingChunkIndexes"],
                [1],
            )

            append_payload_chunk(
                root=temporary,
                upload_id="upload-incomplete",
                generation_id=begun["generationId"],
                chunk_index=1,
                data=chunks[1],
            )
            finalized = finalize_payload_upload(
                root=temporary,
                upload_id="upload-incomplete",
                generation_id=begun["generationId"],
            )
            self.assertEqual(
                finalized["payloadRef"],
                {
                    "uploadId": "upload-incomplete",
                    "generationId": begun["generationId"],
                    "sha256": _sha256(payload_text),
                    "sizeBytes": len(payload_text.encode("utf-8")),
                },
            )
            status = get_payload_upload_status(
                root=temporary,
                upload_id="upload-incomplete",
                generation_id=begun["generationId"],
            )
            self.assertEqual(status["status"], "READY")

    def test_payload_reference_is_bound_to_its_original_tool_and_argument(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        with tempfile.TemporaryDirectory() as temporary:
            finalized = self._stage(
                temporary,
                upload_id="upload-bound-result",
                target_tool="task_result",
                payload=hierarchy,
            )

            with self.assertRaises(GatedLoopError) as raised:
                call_tool(
                    "prepare_hierarchy",
                    {
                        "hierarchy": {
                            "payloadRef": finalized["payloadRef"],
                        },
                        "host_runtime": "codex",
                    },
                    root=temporary,
                )
            self.assertEqual(
                raised.exception.code,
                "MCP_PAYLOAD_TARGET_MISMATCH",
            )
            registry = GovernanceRepository(
                temporary
            ).read_registry(allow_missing=True)
            self.assertEqual(registry["workItems"], [])

    def test_ready_payload_detects_sqlite_chunk_tampering_before_domain_write(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        with tempfile.TemporaryDirectory() as temporary:
            finalized = self._stage(
                temporary,
                upload_id="upload-tampered",
                target_tool="prepare_hierarchy",
                payload=hierarchy,
            )
            database = Path(
                temporary,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE payload_chunks "
                    "SET chunk_text = chunk_text || ' ' "
                    "WHERE upload_id = ? AND chunk_index = 0",
                    ("upload-tampered",),
                )
                connection.commit()

            with self.assertRaises(GatedLoopError) as raised:
                call_tool(
                    "prepare_hierarchy",
                    {
                        "hierarchy": {
                            "payloadRef": finalized["payloadRef"],
                        },
                        "host_runtime": "codex",
                    },
                    root=temporary,
                )
            self.assertEqual(
                raised.exception.code,
                "MCP_PAYLOAD_CORRUPT",
            )
            status = get_payload_upload_status(
                root=temporary,
                upload_id="upload-tampered",
                generation_id=finalized["payloadRef"]["generationId"],
            )
            self.assertEqual(status["status"], "INVALID")
            registry = GovernanceRepository(
                temporary
            ).read_registry(allow_missing=True)
            self.assertEqual(registry["workItems"], [])

    def test_status_is_compact_and_expired_upload_ids_can_be_recreated(
        self,
    ) -> None:
        payload_text = canonical_json({"large": "x" * 1000})
        with tempfile.TemporaryDirectory() as temporary:
            begun = begin_payload_upload(
                root=temporary,
                upload_id="upload-expiring",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            status = get_payload_upload_status(
                root=temporary,
                upload_id="upload-expiring",
                generation_id=begun["generationId"],
            )
            self.assertNotIn("x" * 100, json.dumps(status))

            database = Path(
                temporary,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE payload_uploads "
                    "SET expires_at = '2000-01-01T00:00:00.000Z' "
                    "WHERE upload_id = ?",
                    ("upload-expiring",),
                )
                connection.commit()
            with self.assertRaises(GatedLoopError) as expired:
                get_payload_upload_status(
                    root=temporary,
                    upload_id="upload-expiring",
                    generation_id=begun["generationId"],
                )
            self.assertEqual(
                expired.exception.code,
                "MCP_PAYLOAD_EXPIRED",
            )

            recreated = begin_payload_upload(
                root=temporary,
                upload_id="upload-expiring",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            self.assertEqual(recreated["status"], "UPLOADING")

    def test_payload_limits_and_deep_json_are_rejected_without_domain_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("hdg.payloads.MAX_PAYLOAD_BYTES", 4):
                begun = begin_payload_upload(
                    root=temporary,
                    upload_id="upload-too-large",
                    target_tool="prepare_hierarchy",
                    total_chunks=1,
                )
                with self.assertRaises(GatedLoopError) as oversized:
                    append_payload_chunk(
                        root=temporary,
                        upload_id="upload-too-large",
                        generation_id=begun["generationId"],
                        chunk_index=0,
                        data="12345",
                    )
            self.assertEqual(
                oversized.exception.code,
                "MCP_PAYLOAD_TOO_LARGE",
            )

            deep_payload = '{"value":' + "[" * 130 + "0" + "]" * 130 + "}"
            begun = begin_payload_upload(
                root=temporary,
                upload_id="upload-too-deep",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            append_payload_chunk(
                root=temporary,
                upload_id="upload-too-deep",
                generation_id=begun["generationId"],
                chunk_index=0,
                data=deep_payload,
            )
            with self.assertRaises(GatedLoopError) as too_deep:
                finalize_payload_upload(
                    root=temporary,
                    upload_id="upload-too-deep",
                    generation_id=begun["generationId"],
                )
            self.assertEqual(
                too_deep.exception.code,
                "MCP_PAYLOAD_STRUCTURE_LIMIT",
            )

    def test_abort_is_idempotent_and_removes_staged_chunks(self) -> None:
        payload_text = canonical_json({"value": "discard"})
        with tempfile.TemporaryDirectory() as temporary:
            begun = begin_payload_upload(
                root=temporary,
                upload_id="upload-abort",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            append_payload_chunk(
                root=temporary,
                upload_id="upload-abort",
                generation_id=begun["generationId"],
                chunk_index=0,
                data=payload_text,
            )

            first = abort_payload_upload(
                root=temporary,
                upload_id="upload-abort",
                generation_id=begun["generationId"],
            )
            second = abort_payload_upload(
                root=temporary,
                upload_id="upload-abort",
                generation_id=begun["generationId"],
            )
            self.assertIs(first["aborted"], True)
            self.assertIs(second["aborted"], False)
            database = Path(
                temporary,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM payload_chunks "
                    "WHERE upload_id = ?",
                    ("upload-abort",),
                ).fetchone()[0]
            self.assertEqual(count, 0)

    def test_declared_default_payload_limit_is_larger_than_direct_message_limit(
        self,
    ) -> None:
        self.assertGreater(MAX_PAYLOAD_BYTES, 8 * 1024 * 1024)

    def test_invalid_unicode_chunk_is_rejected_before_sqlite_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            begun = begin_payload_upload(
                root=temporary,
                upload_id="upload-invalid-unicode",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )

            with self.assertRaises(GatedLoopError) as raised:
                append_payload_chunk(
                    root=temporary,
                    upload_id="upload-invalid-unicode",
                    generation_id=begun["generationId"],
                    chunk_index=0,
                    data="\ud800",
                )

            self.assertEqual(
                raised.exception.code,
                "MCP_PAYLOAD_ENCODING_INVALID",
            )
            status = get_payload_upload_status(
                root=temporary,
                upload_id="upload-invalid-unicode",
                generation_id=begun["generationId"],
            )
            self.assertEqual(status["receivedChunks"], 0)
            self.assertEqual(status["receivedBytes"], 0)


if __name__ == "__main__":
    unittest.main()
