from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from hdg import mcp_server, mcp_tools, operations
from hdg.errors import GatedLoopError
from hdg.jsonio import canonical_json, strict_json_loads
from hdg.mcp_tools import call_tool, tool_definitions
from hdg.model import safe_id
from hdg.payloads import (
    MAX_UPLOAD_ID_LENGTH,
    abort_payload_upload,
    append_payload_chunk,
    begin_payload_upload,
    finalize_payload_upload,
    resolve_payload_argument,
)
from hdg.repository import GovernanceRepository


def _initialize_request(
    *,
    request_id: object = 1,
    client_name: str = "test-client",
    client_version: str = "1",
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": client_name,
                "version": client_version,
            },
        },
    }


def _initialized_notification() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }


def _tool_call(
    name: str,
    arguments: dict[str, object],
    *,
    request_id: int = 2,
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    }


class StrictJsonHardeningTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_keys_at_every_depth(self) -> None:
        for raw in (
            '{"a":1,"a":2}',
            '{"nested":{"a":1,"a":2}}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strict_json_loads(raw)

    def test_strict_json_rejects_lone_surrogates_and_overflowing_numbers(
        self,
    ) -> None:
        for raw in (
            r'{"key":"\ud800"}',
            r'{"\udfff":"value"}',
            '{"number":1e9999}',
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strict_json_loads(raw)

    def test_invalid_utf8_line_returns_parse_error_and_server_continues(
        self,
    ) -> None:
        initialize = json.dumps(_initialize_request()).encode("utf-8")
        stdout = io.StringIO()

        with tempfile.TemporaryDirectory() as root:
            mcp_server.serve(
                stdin=io.BytesIO(b"\xff\n" + initialize + b"\n"),
                stdout=stdout,
                root=root,
            )

        responses = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
        ]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 1)

    def test_escaped_surrogate_request_id_is_rejected_without_stopping_server(
        self,
    ) -> None:
        invalid = (
            r'{"jsonrpc":"2.0","id":"\ud800","method":"ping","params":{}}'
        )
        initialize = json.dumps(_initialize_request())
        stdout = io.StringIO()

        with tempfile.TemporaryDirectory() as root:
            mcp_server.serve(
                stdin=io.StringIO(invalid + "\n" + initialize + "\n"),
                stdout=stdout,
                root=root,
            )

        responses = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
        ]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 1)


class PayloadHardeningTests(unittest.TestCase):
    def _stage(
        self,
        root: str,
        *,
        upload_id: str,
        payload_text: str,
    ) -> dict[str, object]:
        begun = begin_payload_upload(
            root=root,
            upload_id=upload_id,
            target_tool="prepare_hierarchy",
            total_chunks=1,
        )
        generation_id = str(begun["generationId"])
        append_payload_chunk(
            root=root,
            upload_id=upload_id,
            generation_id=generation_id,
            chunk_index=0,
            data=payload_text,
        )
        return finalize_payload_upload(
            root=root,
            upload_id=upload_id,
            generation_id=generation_id,
        )

    def test_upload_id_is_bounded_in_schema_and_runtime(self) -> None:
        tools = {
            tool["name"]: tool
            for tool in tool_definitions()
        }
        upload_schema = tools["begin_payload_upload"]["inputSchema"][
            "properties"
        ]["upload_id"]
        self.assertLessEqual(upload_schema["maxLength"], 128)

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(GatedLoopError) as raised:
                begin_payload_upload(
                    root=root,
                    upload_id="a" * 129,
                    target_tool="prepare_hierarchy",
                    total_chunks=1,
                )
        self.assertEqual(
            raised.exception.code,
            "MCP_PAYLOAD_UPLOAD_ID_INVALID",
        )

    def test_schema_length_limit_runs_before_identifier_regex(self) -> None:
        with patch.object(
            mcp_tools.re,
            "fullmatch",
            side_effect=AssertionError("regex must not inspect oversized IDs"),
        ):
            with self.assertRaises(GatedLoopError) as raised:
                mcp_tools.validate_tool_arguments(
                    "begin_payload_upload",
                    {
                        "upload_id": "a" * 1024,
                        "target_tool": "prepare_hierarchy",
                        "total_chunks": 1,
                    },
                )
        self.assertEqual(raised.exception.code, "MCP_ARGUMENT_INVALID")
        self.assertEqual(raised.exception.details["field"], "upload_id")
        self.assertEqual(
            raised.exception.details["maxLength"],
            MAX_UPLOAD_ID_LENGTH,
        )

    def test_all_transport_identifiers_and_chunks_are_schema_bounded(
        self,
    ) -> None:
        tools = {
            tool["name"]: tool
            for tool in tool_definitions()
        }
        for tool_name, field in (
            ("graph_status", "item_id"),
            ("dispatch_task", "owner"),
            ("dispatch_task", "operation_id"),
        ):
            with self.subTest(tool=tool_name, field=field):
                schema = tools[tool_name]["inputSchema"]["properties"][field]
                self.assertLessEqual(schema["maxLength"], 128)
        chunk_schema = tools["append_payload_chunk"]["inputSchema"][
            "properties"
        ]["data"]
        self.assertEqual(chunk_schema["minLength"], 1)
        self.assertEqual(chunk_schema["maxLength"], 1024 * 1024)

        with self.assertRaises(GatedLoopError) as raised:
            safe_id("a" * 129)
        self.assertEqual(raised.exception.code, "WORK_ITEM_ID_INVALID")
        self.assertNotIn("value", raised.exception.details)
        self.assertEqual(raised.exception.details["maxLength"], 128)

    def test_payload_reference_has_an_exact_nested_schema(self) -> None:
        tools = {
            tool["name"]: tool
            for tool in tool_definitions()
        }
        for tool_name, field in (
            ("prepare_hierarchy", "hierarchy"),
            ("task_result", "evidence"),
        ):
            with self.subTest(tool=tool_name):
                schema = tools[tool_name]["inputSchema"]["properties"][field]
                reference_branch = schema["oneOf"][0]
                payload_ref = reference_branch["properties"]["payloadRef"]
                self.assertEqual(
                    set(payload_ref["required"]),
                    {"uploadId", "generationId", "sha256", "sizeBytes"},
                )
                self.assertIs(payload_ref["additionalProperties"], False)
                self.assertEqual(
                    payload_ref["properties"]["generationId"]["maxLength"],
                    32,
                )

    def test_finalize_returns_no_payload_keys_or_payload_content(self) -> None:
        marker = "CANARY_PAYLOAD_KEY_MUST_NOT_RETURN"
        with tempfile.TemporaryDirectory() as root:
            finalized = self._stage(
                root,
                upload_id="no-key-leak",
                payload_text=canonical_json({marker: "value"}),
            )

        rendered = json.dumps(finalized, ensure_ascii=False)
        self.assertNotIn("topLevelKeys", finalized)
        self.assertNotIn(marker, rendered)
        self.assertEqual(finalized["topLevelMemberCount"], 1)
        rendered_result = mcp_server._tool_result(
            {"ok": True, "result": finalized},
            is_error=False,
        )
        self.assertEqual(
            rendered_result["structuredContent"]["result"][
                "topLevelMemberCount"
            ],
            1,
        )

    def test_expired_upload_recreation_does_not_resurrect_old_reference(
        self,
    ) -> None:
        payload_text = canonical_json({"value": "same"})
        with tempfile.TemporaryDirectory() as root:
            first = self._stage(
                root,
                upload_id="generation-bound",
                payload_text=payload_text,
            )
            database = Path(
                root,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "UPDATE payload_uploads "
                    "SET expires_at = '2000-01-01T00:00:00.000Z' "
                    "WHERE upload_id = ?",
                    ("generation-bound",),
                )
                connection.commit()

            second = self._stage(
                root,
                upload_id="generation-bound",
                payload_text=payload_text,
            )

            self.assertNotEqual(
                first["payloadRef"]["generationId"],
                second["payloadRef"]["generationId"],
            )
            with self.assertRaises(GatedLoopError) as stale:
                resolve_payload_argument(
                    root=root,
                    target_tool="prepare_hierarchy",
                    target_argument="hierarchy",
                    value={"payloadRef": first["payloadRef"]},
                )
            self.assertIn(
                stale.exception.code,
                {"MCP_PAYLOAD_NOT_FOUND", "MCP_PAYLOAD_STALE"},
            )

    def test_project_payload_byte_quota_is_enforced(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root,
            patch("hdg.payloads.MAX_PROJECT_PAYLOAD_BYTES", 8),
        ):
            first = begin_payload_upload(
                root=root,
                upload_id="quota-one",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            append_payload_chunk(
                root=root,
                upload_id="quota-one",
                generation_id=first["generationId"],
                chunk_index=0,
                data="12345678",
            )
            second = begin_payload_upload(
                root=root,
                upload_id="quota-two",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            with self.assertRaises(GatedLoopError) as raised:
                append_payload_chunk(
                    root=root,
                    upload_id="quota-two",
                    generation_id=second["generationId"],
                    chunk_index=0,
                    data="x",
                )
        self.assertEqual(
            raised.exception.code,
            "MCP_PAYLOAD_PROJECT_QUOTA_EXCEEDED",
        )

    def test_abort_of_only_upload_leaves_classified_staging_only_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            begun = begin_payload_upload(
                root=root,
                upload_id="cleanup-empty",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            aborted = abort_payload_upload(
                root=root,
                upload_id="cleanup-empty",
                generation_id=begun["generationId"],
            )
            status = call_tool("workspace_status", {}, root=root)

            self.assertIs(aborted["aborted"], True)
            self.assertEqual(status["state"], "STAGING_ONLY")
            self.assertEqual(status["activePayloadUploads"], 0)


class PermissionAndStorageHardeningTests(unittest.TestCase):
    def test_inline_comment_and_descendant_root_cannot_bypass_dogfood(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as source_root:
            root = Path(source_root)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "layered-delivery" # source project\n',
                encoding="utf-8",
            )
            child = root / "child"
            child.mkdir()

            for candidate in (root, child):
                with self.subTest(candidate=candidate):
                    with self.assertRaises(GatedLoopError) as raised:
                        begin_payload_upload(
                            root=str(candidate),
                            upload_id="dogfood-required",
                            target_tool="prepare_hierarchy",
                            total_chunks=1,
                        )
                    self.assertEqual(
                        raised.exception.code,
                        "SELF_HOSTING_DOGFOOD_REQUIRED",
                    )

    def test_governance_database_hardlink_is_rejected(self) -> None:
        with (
            tempfile.TemporaryDirectory() as protected_root,
            tempfile.TemporaryDirectory() as linked_root,
        ):
            protected = Path(protected_root)
            protected.joinpath("pyproject.toml").write_text(
                '[project]\nname = "layered-delivery"\n',
                encoding="utf-8",
            )
            begin_payload_upload(
                root=protected_root,
                upload_id="protected",
                target_tool="prepare_hierarchy",
                total_chunks=1,
                explicit_dogfood=True,
            )
            protected_database = protected / (
                ".layered-delivery/governance.sqlite3"
            )
            linked_runtime = Path(linked_root, ".layered-delivery")
            linked_runtime.mkdir()
            linked_database = linked_runtime / "governance.sqlite3"
            os.link(protected_database, linked_database)

            with self.assertRaises(GatedLoopError) as raised:
                begin_payload_upload(
                    root=linked_root,
                    upload_id="must-not-cross-root",
                    target_tool="prepare_hierarchy",
                    total_chunks=1,
                )
            self.assertIn(
                raised.exception.code,
                {"PATH_HARDLINK", "WORK_ITEM_DATABASE_PATH_INVALID"},
            )

    def test_payload_schema_without_foreign_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            begin_payload_upload(
                root=root,
                upload_id="schema-check",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            database = Path(
                root,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DROP TABLE payload_chunks")
                connection.execute(
                    "CREATE TABLE payload_chunks ("
                    "upload_id TEXT NOT NULL,"
                    "chunk_index INTEGER NOT NULL,"
                    "chunk_sha256 TEXT NOT NULL,"
                    "byte_size INTEGER NOT NULL,"
                    "chunk_text TEXT NOT NULL,"
                    "PRIMARY KEY (upload_id, chunk_index)"
                    ")"
                )
                connection.commit()

            with self.assertRaises(GatedLoopError) as raised:
                begin_payload_upload(
                    root=root,
                    upload_id="schema-check-two",
                    target_tool="prepare_hierarchy",
                    total_chunks=1,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            )

    def test_payload_schema_without_check_constraints_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            begin_payload_upload(
                root=root,
                upload_id="schema-checks",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            database = Path(
                root,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE payload_chunks")
                connection.execute(
                    "ALTER TABLE payload_uploads RENAME TO old_payload_uploads"
                )
                connection.execute(
                    "CREATE TABLE payload_uploads ("
                    "upload_id TEXT PRIMARY KEY,"
                    "generation_id TEXT NOT NULL,"
                    "target_tool TEXT NOT NULL,"
                    "target_argument TEXT NOT NULL,"
                    "total_chunks INTEGER NOT NULL,"
                    "status TEXT NOT NULL,"
                    "received_bytes INTEGER NOT NULL,"
                    "received_chunks INTEGER NOT NULL,"
                    "content_sha256 TEXT,"
                    "created_at TEXT NOT NULL,"
                    "expires_at TEXT NOT NULL,"
                    "finalized_at TEXT,"
                    "UNIQUE (upload_id, generation_id)"
                    ")"
                )
                connection.execute(
                    "INSERT INTO payload_uploads SELECT * "
                    "FROM old_payload_uploads"
                )
                connection.execute("DROP TABLE old_payload_uploads")
                connection.execute(
                    "CREATE TABLE payload_chunks ("
                    "upload_id TEXT NOT NULL,"
                    "generation_id TEXT NOT NULL,"
                    "chunk_index INTEGER NOT NULL,"
                    "chunk_sha256 TEXT NOT NULL,"
                    "byte_size INTEGER NOT NULL,"
                    "chunk_text TEXT NOT NULL,"
                    "PRIMARY KEY (upload_id, generation_id, chunk_index),"
                    "FOREIGN KEY (upload_id, generation_id) "
                    "REFERENCES payload_uploads(upload_id, generation_id) "
                    "ON DELETE CASCADE"
                    ")"
                )
                connection.execute(
                    "CREATE INDEX payload_uploads_expiry "
                    "ON payload_uploads(expires_at)"
                )
                connection.commit()

            with self.assertRaises(GatedLoopError) as raised:
                begin_payload_upload(
                    root=root,
                    upload_id="schema-checks-two",
                    target_tool="prepare_hierarchy",
                    total_chunks=1,
                )
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            )

    def test_non_payload_table_column_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            begin_payload_upload(
                root=root,
                upload_id="schema-all-tables",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            database = Path(
                root,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("DROP TABLE reports")
                connection.execute(
                    "CREATE TABLE reports ("
                    "work_item_id TEXT NOT NULL,"
                    "report_kind TEXT NOT NULL,"
                    "report_json TEXT NOT NULL,"
                    "PRIMARY KEY (work_item_id, report_kind)"
                    ")"
                )
                connection.commit()

            with self.assertRaises(GatedLoopError) as raised:
                call_tool("workspace_status", {}, root=root)
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_DATABASE_SCHEMA_UNSUPPORTED",
            )

    def test_interaction_log_hash_chain_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            begin_payload_upload(
                root=root,
                upload_id="interaction-chain",
                target_tool="prepare_hierarchy",
                total_chunks=1,
            )
            database = Path(
                root,
                ".layered-delivery",
                "governance.sqlite3",
            )
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "INSERT INTO interaction_events("
                    "event_uuid, work_item_id, session_id, actor, event_type, "
                    "summary, operation_id, host_runtime, payload_json, "
                    "registry_revision, recorded_at, previous_hash, event_hash"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "event-one",
                        "task-one",
                        "session-one",
                        "AGENT",
                        "AGENT_UPDATE",
                        "tampered",
                        None,
                        "codex",
                        "{}",
                        1,
                        "2026-01-01T00:00:00.000Z",
                        None,
                        "0" * 64,
                    ),
                )
                connection.commit()

            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(root).read_interaction_events()
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_INTERACTION_INVALID",
            )

    def test_old_claude_client_cannot_call_always_prompt_tools(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            session = mcp_server.ServerSession(
                project_root=mcp_server.ProjectRootBinding.from_startup(root),
            )
            initialized = mcp_server.handle_message(
                _initialize_request(
                    client_name="claude-code",
                    client_version="2.1.198",
                ),
                session=session,
            )
            self.assertIn("result", initialized)
            mcp_server.handle_message(
                _initialized_notification(),
                session=session,
            )
            response = mcp_server.handle_message(
                _tool_call(
                    "freeze_hierarchy",
                    {
                        "item_id": "t-example",
                        "expected_hierarchy_fingerprint": "0" * 64,
                        "development_mode": "active",
                    },
                ),
                session=session,
            )

        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "MCP_CLIENT_UPGRADE_REQUIRED")

    def test_mutating_tool_annotations_do_not_claim_replacements_are_additive(
        self,
    ) -> None:
        tools = {
            tool["name"]: tool
            for tool in tool_definitions()
        }
        for name in (
            "begin_payload_upload",
            "prepare_hierarchy",
            "advance_graph",
            "refresh_projections",
        ):
            with self.subTest(tool=name):
                self.assertIs(
                    tools[name]["annotations"]["destructiveHint"],
                    True,
                )

    def test_tool_output_schema_requires_success_or_error_payload(self) -> None:
        schemas = {
            json.dumps(tool["outputSchema"], sort_keys=True)
            for tool in tool_definitions()
        }
        self.assertEqual(len(schemas), 1)
        schema = tool_definitions()[0]["outputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(len(schema["oneOf"]), 2)
        success, failure = schema["oneOf"]
        self.assertEqual(success["properties"]["ok"]["const"], True)
        self.assertEqual(success["required"], ["ok", "result"])
        self.assertEqual(failure["properties"]["ok"]["const"], False)
        self.assertEqual(failure["required"], ["ok", "error"])


class BoundedQueryTests(unittest.TestCase):
    def test_graph_events_mcp_operation_returns_a_bounded_page(self) -> None:
        events = [
            {"eventId": 1, "eventType": "ONE"},
            {"eventId": 2, "eventType": "TWO"},
            {"eventId": 3, "eventType": "THREE"},
        ]
        with patch.object(
            operations,
            "list_graph_events",
            return_value=events,
        ) as mocked_list:
            result = operations.execute_operation(
                "graph_events",
                {
                    "item_id": "t-example",
                    "after_event_id": 0,
                    "limit": 2,
                },
                context=operations.OperationContext(root="C:/project"),
            )

        self.assertEqual(result["items"], events[:2])
        self.assertIs(result["hasMore"], True)
        self.assertEqual(result["nextCursor"], 2)
        mocked_list.assert_called_once_with(
            root="C:/project",
            work_item_id="t-example",
            after_event_id=0,
            limit=3,
        )

    def test_interaction_log_mcp_operation_returns_a_bounded_page(self) -> None:
        events = [
            {"eventId": 10, "eventType": "ONE"},
            {"eventId": 11, "eventType": "TWO"},
        ]
        with patch.object(
            operations,
            "list_interactions",
            return_value=events,
        ) as mocked_list:
            result = operations.execute_operation(
                "interaction_log",
                {
                    "item_id": "t-example",
                    "after_event_id": 10,
                    "limit": 1,
                },
                context=operations.OperationContext(root="C:/project"),
            )

        self.assertEqual(result["items"], [events[1]])
        self.assertIs(result["hasMore"], False)
        self.assertIsNone(result["nextCursor"])
        mocked_list.assert_called_once_with(
            root="C:/project",
            item_id="t-example",
            after_event_id=10,
            limit=2,
        )


if __name__ == "__main__":
    unittest.main()
