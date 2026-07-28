from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hdg import mcp_server, mcp_tools, operations
from hdg.errors import GatedLoopError
from hdg.mcp_tools import tool_definitions


EXPECTED_TOOL_PROPERTIES = {
    "workspace_status": set(),
    "begin_payload_upload": {
        "upload_id",
        "target_tool",
        "total_chunks",
    },
    "append_payload_chunk": {
        "upload_id",
        "generation_id",
        "chunk_index",
        "data",
    },
    "finalize_payload_upload": {"upload_id", "generation_id"},
    "payload_upload_status": {"upload_id", "generation_id"},
    "abort_payload_upload": {"upload_id", "generation_id"},
    "prepare_hierarchy": {"hierarchy", "host_runtime"},
    "freeze_hierarchy": {
        "item_id",
        "expected_hierarchy_fingerprint",
        "development_mode",
    },
    "ready_tasks": {"item_id"},
    "graph_status": {"item_id"},
    "graph_frontier": {"item_id"},
    "graph_events": {"item_id", "after_event_id", "limit"},
    "graph_replay": {"item_id"},
    "rebuild_graph_run": {"item_id"},
    "advance_graph": {"item_id"},
    "cancel_graph_run": {"item_id"},
    "task_context": {"item_id"},
    "evidence_contract": {"item_id", "contract_kind"},
    "record_skill_activation": {
        "item_id",
        "stage",
        "skill_name",
        "activation",
    },
    "record_skill_conformance": {
        "item_id",
        "activation_receipt_id",
        "conformance",
    },
    "dispatch_task": {"item_id", "owner", "operation_id"},
    "heartbeat_task": {"item_id", "operation_id"},
    "pause_task": {"item_id", "operation_id"},
    "resume_task": {"item_id"},
    "claim_task": {"item_id", "owner", "operation_id"},
    "task_result": {"item_id", "operation_id", "status", "evidence"},
    "remediate_task": {
        "item_id",
        "expected_baseline_fingerprint",
        "evidence",
    },
    "retry_item": {"item_id", "expected_baseline_fingerprint"},
    "gate_item": {"item_id", "status", "evidence"},
    "accept_item": {"item_id", "evidence"},
    "record_independent_review_pass": {"item_id", "evidence"},
    "record_independent_review_blocked": {"item_id", "evidence"},
    "record_human_review_acceptance": {"item_id", "evidence"},
    "record_user_confirmation": {"item_id", "evidence"},
    "refresh_projections": set(),
    "record_interaction": {"item_id", "interaction"},
    "interaction_log": {"item_id", "after_event_id", "limit"},
}

READ_ONLY_TOOLS = {
    "workspace_status",
    "payload_upload_status",
    "ready_tasks",
    "graph_status",
    "graph_frontier",
    "graph_events",
    "graph_replay",
    "task_context",
    "evidence_contract",
    "interaction_log",
}

CONFIRMATION_TOOLS = {
    "freeze_hierarchy",
    "rebuild_graph_run",
    "cancel_graph_run",
    "record_user_confirmation",
}

REQUIRES_USER_INTERACTION = {
    "rebuild_graph_run",
    "cancel_graph_run",
    "record_human_review_acceptance",
    "record_user_confirmation",
}


def rpc_request(
    method: str,
    *,
    request_id: int = 1,
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    request: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        request["params"] = params
    return request


def tool_call(
    name: str,
    arguments: dict[str, object],
    *,
    request_id: int = 1,
    meta: dict[str, object] | None = None,
) -> dict[str, object]:
    params: dict[str, object] = {
        "name": name,
        "arguments": arguments,
    }
    if meta is not None:
        params["_meta"] = meta
    return rpc_request(
        "tools/call",
        request_id=request_id,
        params=params,
    )


def ready_session(
    root: str | os.PathLike[str] = ".",
) -> mcp_server.ServerSession:
    return mcp_server.ServerSession(
        project_root=mcp_server.ProjectRootBinding.from_startup(root),
        initialize_requested=True,
        initialized=True,
    )


class CwdChangingInput:
    """Switch cwd immediately before yielding the second JSON-RPC line."""

    def __init__(self, lines: list[str], switch_to: str) -> None:
        self._lines = iter(lines)
        self._switch_to = switch_to
        self._count = 0

    def __iter__(self) -> CwdChangingInput:
        return self

    def __next__(self) -> str:
        line = self.readline()
        if line == "":
            raise StopIteration
        return line

    def readline(self, size: int = -1) -> str:
        try:
            if self._count == 1:
                os.chdir(self._switch_to)
            line = next(self._lines)
        except StopIteration:
            return ""
        self._count += 1
        return line


class BoundedReadInput:
    """Reject iterator-based reads and record every readline bound."""

    def __init__(self) -> None:
        self.read_sizes: list[int] = []

    def __iter__(self) -> BoundedReadInput:
        raise AssertionError("serve must not iterate over unbounded input lines")

    def readline(self, size: int = -1) -> str:
        self.read_sizes.append(size)
        return ""


class McpServerProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_supported_and_unknown_versions(self) -> None:
        supported = tuple(mcp_server.SUPPORTED_PROTOCOL_VERSIONS)
        self.assertTrue(supported)
        self.assertIn(mcp_server.LATEST_PROTOCOL_VERSION, supported)

        for protocol_version in (*supported, "1900-01-01"):
            with self.subTest(protocol_version=protocol_version):
                response = mcp_server.handle_message(
                    rpc_request(
                        "initialize",
                        params={
                            "protocolVersion": protocol_version,
                            "capabilities": {},
                            "clientInfo": {"name": "test-client", "version": "1"},
                        },
                    ),
                    session=mcp_server.ServerSession(
                        project_root=(
                            mcp_server.ProjectRootBinding.from_startup(".")
                        ),
                    ),
                )
                self.assertIsNotNone(response)
                assert response is not None
                self.assertEqual(response["jsonrpc"], "2.0")
                self.assertEqual(response["id"], 1)
                negotiated = (
                    protocol_version
                    if protocol_version in supported
                    else mcp_server.LATEST_PROTOCOL_VERSION
                )
                self.assertEqual(
                    response["result"]["protocolVersion"],
                    negotiated,
                )
                self.assertEqual(
                    response["result"]["capabilities"],
                    {
                        "tools": {"listChanged": False},
                        "experimental": {
                            "codex/sandbox-state-meta": {},
                        },
                    },
                )
                self.assertEqual(
                    response["result"]["serverInfo"]["name"],
                    "layered-delivery",
                )
                self.assertTrue(response["result"]["serverInfo"]["version"])

    def test_initialized_notification_has_no_response(self) -> None:
        session = mcp_server.ServerSession(
            project_root=mcp_server.ProjectRootBinding.from_startup("."),
            initialize_requested=True,
        )
        response = mcp_server.handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            session=session,
        )
        self.assertIsNone(response)
        self.assertIs(session.initialized, True)

    def test_tools_list_exposes_37_strict_snake_case_tools(self) -> None:
        response = mcp_server.handle_message(
            rpc_request("tools/list"),
            session=ready_session(),
        )
        self.assertIsNotNone(response)
        assert response is not None
        tools = response["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}

        self.assertEqual(len(tools), 37)
        self.assertIn("record_skill_activation", by_name)
        self.assertIn("record_skill_conformance", by_name)
        hierarchy_description = by_name["prepare_hierarchy"][
            "inputSchema"
        ]["properties"]["hierarchy"]["description"]
        self.assertIn("may omit requiredSkills", hierarchy_description)
        self.assertIn("empty array", hierarchy_description)
        self.assertEqual(set(by_name), set(EXPECTED_TOOL_PROPERTIES))
        self.assertEqual(tools, tool_definitions())

        for name, expected_properties in EXPECTED_TOOL_PROPERTIES.items():
            with self.subTest(tool=name):
                self.assertRegex(name, r"^[a-z][a-z0-9_]*$")
                tool = by_name[name]
                self.assertTrue(tool["description"].strip())

                schema = tool["inputSchema"]
                self.assertEqual(
                    set(schema),
                    {"type", "properties", "required", "additionalProperties"},
                )
                self.assertEqual(schema["type"], "object")
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(
                    set(schema["properties"]),
                    expected_properties,
                )
                self.assertEqual(set(schema["required"]), expected_properties)
                self.assertTrue(
                    all(
                        isinstance(property_schema, dict)
                        and isinstance(property_schema.get("type"), str)
                        for property_schema in schema["properties"].values()
                    )
                )
                self.assertTrue(
                    {"root", "project_root", "dogfood", "confirmed"}.isdisjoint(
                        schema["properties"]
                    )
                )

                annotations = tool["annotations"]
                self.assertEqual(
                    set(annotations),
                    {
                        "title",
                        "readOnlyHint",
                        "destructiveHint",
                        "idempotentHint",
                        "openWorldHint",
                    },
                )
                self.assertTrue(annotations["title"].strip())
                self.assertTrue(
                    all(
                        isinstance(annotations[key], bool)
                        for key in (
                            "readOnlyHint",
                            "destructiveHint",
                            "idempotentHint",
                            "openWorldHint",
                        )
                    )
                )
                self.assertIs(annotations["openWorldHint"], False)
                self.assertEqual(
                    annotations["readOnlyHint"],
                    name in READ_ONLY_TOOLS,
                )
                if name in READ_ONLY_TOOLS:
                    self.assertIs(annotations["destructiveHint"], False)
                    self.assertIs(annotations["idempotentHint"], True)

        for name in CONFIRMATION_TOOLS:
            with self.subTest(confirmation_tool=name):
                self.assertIs(
                    by_name[name]["annotations"]["destructiveHint"],
                    True,
                )
                self.assertIs(
                    by_name[name]["annotations"]["readOnlyHint"],
                    False,
                )

        self.assertIs(
            by_name["record_user_confirmation"]["annotations"]["idempotentHint"],
            False,
        )
        self.assertIs(
            by_name["record_independent_review_pass"]["annotations"][
                "destructiveHint"
            ],
            True,
        )
        self.assertIs(
            by_name["record_human_review_acceptance"]["annotations"][
                "destructiveHint"
            ],
            True,
        )

        self.assertEqual(
            by_name["freeze_hierarchy"]["inputSchema"]["properties"][
                "development_mode"
            ]["enum"],
            ["active", "manual"],
        )
        self.assertEqual(
            by_name["evidence_contract"]["inputSchema"]["properties"][
                "contract_kind"
            ]["enum"],
            ["result", "gate", "remediation", "review", "confirmation"],
        )
        self.assertEqual(
            by_name["task_result"]["inputSchema"]["properties"]["status"]["enum"],
            ["IMPLEMENTED", "BLOCKED"],
        )
        self.assertEqual(
            by_name["gate_item"]["inputSchema"]["properties"]["status"]["enum"],
            ["PASS", "FAIL"],
        )
        for name, tool in by_name.items():
            with self.subTest(user_interaction_tool=name):
                if name in REQUIRES_USER_INTERACTION:
                    self.assertEqual(
                        tool.get("_meta"),
                        {"anthropic/requiresUserInteraction": True},
                    )
                else:
                    self.assertNotIn("_meta", tool)

    def test_tools_call_returns_text_and_structured_success_result(self) -> None:
        business_result = {"id": "t-one", "status": "READY"}
        with patch.object(
            mcp_server,
            "call_tool",
            return_value=business_result,
        ) as mocked_call:
            response = mcp_server.handle_message(
                tool_call("graph_status", {"item_id": "t-one"}),
                session=ready_session(),
            )

        self.assertIsNotNone(response)
        assert response is not None
        result = response["result"]
        expected = {"ok": True, "result": business_result}
        self.assertIs(result["isError"], False)
        self.assertEqual(result["structuredContent"], expected)
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"]), expected)
        mocked_call.assert_called_once()
        self.assertEqual(mocked_call.call_args.args[:2], ("graph_status", {"item_id": "t-one"}))

    def test_gated_loop_error_is_a_structured_error_tool_result(self) -> None:
        error = GatedLoopError(
            "WORK_ITEM_NOT_READY",
            "Task is not ready",
            details={"itemId": "t-one"},
        )
        with patch.object(mcp_server, "call_tool", side_effect=error):
            response = mcp_server.handle_message(
                tool_call("graph_status", {"item_id": "t-one"}),
                session=ready_session(),
            )

        self.assertIsNotNone(response)
        assert response is not None
        result = response["result"]
        expected = {
            "ok": False,
            "error": {
                "code": "WORK_ITEM_NOT_READY",
                "message": "Task is not ready",
                "details": {"itemId": "t-one"},
            },
        }
        self.assertIs(result["isError"], True)
        self.assertEqual(result["structuredContent"], expected)
        self.assertEqual(json.loads(result["content"][0]["text"]), expected)

    def test_unknown_tool_is_protocol_error_but_known_argument_errors_are_tool_results(
        self,
    ) -> None:
        session = ready_session()
        with patch.object(mcp_server, "call_tool") as mocked_call:
            unknown = mcp_server.handle_message(
                tool_call("does_not_exist", {}),
                session=session,
            )
            unexpected_property = mcp_server.handle_message(
                tool_call(
                    "graph_status",
                    {"item_id": "t-one", "project_root": "C:/escape"},
                    request_id=2,
                ),
                session=session,
            )
            missing_required = mcp_server.handle_message(
                tool_call("graph_status", {}, request_id=3),
                session=session,
            )

        self.assertIsNotNone(unknown)
        assert unknown is not None
        self.assertEqual(unknown["id"], 1)
        self.assertEqual(unknown["error"]["code"], -32602)

        for response, request_id in (
            (unexpected_property, 2),
            (missing_required, 3),
        ):
            with self.subTest(request_id=request_id):
                self.assertIsNotNone(response)
                assert response is not None
                self.assertEqual(response["id"], request_id)
                self.assertIs(response["result"]["isError"], True)
                self.assertEqual(
                    response["result"]["structuredContent"]["error"]["code"],
                    "MCP_ARGUMENTS_INVALID",
                )
        mocked_call.assert_not_called()

    def test_lifecycle_and_initialize_contract_are_enforced(self) -> None:
        session = mcp_server.ServerSession(
            project_root=mcp_server.ProjectRootBinding.from_startup("."),
        )
        before_initialize = mcp_server.handle_message(
            rpc_request("tools/list"),
            session=session,
        )
        self.assertEqual(before_initialize["error"]["code"], -32002)

        invalid_initialize = mcp_server.handle_message(
            rpc_request(
                "initialize",
                request_id=2,
                params={
                    "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                    "clientInfo": {"name": "test-client", "version": "1"},
                },
            ),
            session=session,
        )
        self.assertEqual(invalid_initialize["error"]["code"], -32602)
        self.assertIs(session.initialize_requested, False)

        valid_initialize = mcp_server.handle_message(
            rpc_request(
                "initialize",
                request_id=3,
                params={
                    "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1"},
                },
            ),
            session=session,
        )
        self.assertIn("result", valid_initialize)
        self.assertIs(session.initialize_requested, True)
        self.assertIs(session.initialized, False)

        before_initialized_notification = mcp_server.handle_message(
            rpc_request("tools/list", request_id=4),
            session=session,
        )
        self.assertEqual(
            before_initialized_notification["error"]["code"],
            -32002,
        )

        self.assertIsNone(
            mcp_server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                session=session,
            )
        )
        self.assertIs(session.initialized, True)

        repeated_initialize = mcp_server.handle_message(
            rpc_request(
                "initialize",
                request_id=5,
                params={
                    "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1"},
                },
            ),
            session=session,
        )
        self.assertEqual(repeated_initialize["error"]["code"], -32600)
        after_initialized = mcp_server.handle_message(
            rpc_request("tools/list", request_id=6),
            session=session,
        )
        self.assertIn("result", after_initialized)

    def test_null_request_id_and_structurally_invalid_tool_call_are_rejected(
        self,
    ) -> None:
        null_id = mcp_server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": None,
                "method": "ping",
            },
            session=ready_session(),
        )
        self.assertEqual(null_id["error"]["code"], -32600)
        invalid_arguments = mcp_server.handle_message(
            rpc_request(
                "tools/call",
                request_id=2,
                params={
                    "name": "graph_status",
                    "arguments": [],
                },
            ),
            session=ready_session(),
        )
        self.assertEqual(invalid_arguments["error"]["code"], -32602)

    def test_malformed_json_returns_parse_error_and_server_continues(self) -> None:
        initialize = rpc_request(
            "initialize",
            request_id=7,
            params={
                "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        )
        stdin = io.StringIO("{not-json}\n" + json.dumps(initialize) + "\n")
        stdout = io.StringIO()

        mcp_server.serve(stdin=stdin, stdout=stdout, root=".")

        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertIsNone(responses[0]["id"])
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 7)
        self.assertIn("result", responses[1])

    def test_non_finite_json_numbers_return_parse_error_and_server_continues(
        self,
    ) -> None:
        initialize = rpc_request(
            "initialize",
            request_id=8,
            params={
                "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        )
        invalid_messages = [
            (
                '{"jsonrpc":"2.0","id":1,"method":"ping",'
                f'"params":{{"value":{constant}}}}}'
            )
            for constant in ("NaN", "Infinity", "-Infinity")
        ]
        stdin = io.StringIO(
            "\n".join([*invalid_messages, json.dumps(initialize)]) + "\n"
        )
        stdout = io.StringIO()

        mcp_server.serve(stdin=stdin, stdout=stdout, root=".")

        responses = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
        ]
        self.assertEqual(len(responses), 4)
        for response in responses[:3]:
            self.assertIsNone(response["id"])
            self.assertEqual(response["error"]["code"], -32700)
        self.assertEqual(responses[3]["id"], 8)
        self.assertIn("result", responses[3])

    def test_stdio_emits_exactly_one_json_object_per_response_line(self) -> None:
        initialize = rpc_request(
            "initialize",
            request_id=1,
            params={
                "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        )
        initialized = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        messages = (
            "{broken}\n"
            + json.dumps(initialize)
            + "\n"
            + json.dumps(initialized)
            + "\n"
            + json.dumps(rpc_request("tools/list", request_id=2))
            + "\n"
        )
        stdout = io.StringIO()

        mcp_server.serve(
            stdin=io.StringIO(messages),
            stdout=stdout,
            root=".",
        )

        rendered = stdout.getvalue()
        self.assertTrue(rendered.endswith("\n"))
        lines = rendered.splitlines()
        self.assertEqual(len(lines), 3)
        responses = [json.loads(line) for line in lines]
        self.assertEqual(
            [response.get("id") for response in responses],
            [None, 1, 2],
        )

    def test_stdio_reads_each_message_with_an_explicit_size_bound(self) -> None:
        stdin = BoundedReadInput()

        mcp_server.serve(
            stdin=stdin,
            stdout=io.StringIO(),
            root=".",
        )

        self.assertEqual(
            stdin.read_sizes,
            [mcp_server.MAX_MESSAGE_BYTES + 1],
        )

    def test_oversized_stdio_line_reports_once_and_server_continues(
        self,
    ) -> None:
        initialize = rpc_request(
            "initialize",
            request_id=10,
            params={
                "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        )
        stdout = io.StringIO()
        with patch.object(mcp_server, "MAX_MESSAGE_BYTES", 512):
            mcp_server.serve(
                stdin=io.StringIO(
                    "x" * 700
                    + "\n"
                    + json.dumps(initialize)
                    + "\n"
                ),
                stdout=stdout,
                root=".",
            )

        responses = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
        ]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(
            responses[0]["error"]["data"],
            {
                "maxBytes": 512,
                "messageDiscarded": True,
                "recoveryTools": [
                    "begin_payload_upload",
                    "append_payload_chunk",
                    "finalize_payload_upload",
                ],
            },
        )
        self.assertEqual(responses[1]["id"], 10)
        self.assertIn("result", responses[1])

    def test_oversized_unterminated_stdio_line_reports_once_then_exits(
        self,
    ) -> None:
        stdout = io.StringIO()
        with patch.object(mcp_server, "MAX_MESSAGE_BYTES", 512):
            mcp_server.serve(
                stdin=io.StringIO("x" * 700),
                stdout=stdout,
                root=".",
            )

        responses = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
        ]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], None)
        self.assertEqual(responses[0]["error"]["code"], -32600)

    def test_excessive_json_nesting_is_rejected_without_stopping_server(
        self,
    ) -> None:
        too_deep = "[" * (mcp_server.MAX_JSON_DEPTH + 1)
        too_deep += "0"
        too_deep += "]" * (mcp_server.MAX_JSON_DEPTH + 1)
        initialize = rpc_request(
            "initialize",
            request_id=9,
            params={
                "protocolVersion": mcp_server.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        )
        stdout = io.StringIO()
        mcp_server.serve(
            stdin=io.StringIO(too_deep + "\n" + json.dumps(initialize) + "\n"),
            stdout=stdout,
            root=".",
        )

        responses = [
            json.loads(line)
            for line in stdout.getvalue().splitlines()
        ]
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(responses[1]["id"], 9)
        self.assertIn("result", responses[1])

    def test_server_resolves_startup_cwd_once_for_every_request(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            messages = [
                json.dumps(rpc_request("ping", request_id=1)) + "\n",
                json.dumps(rpc_request("ping", request_id=2)) + "\n",
            ]
            stdin = CwdChangingInput(messages, second)
            stdout = io.StringIO()
            observed_roots: list[Path] = []

            def fake_handle(
                message: dict[str, object],
                *,
                session: mcp_server.ServerSession,
                **_: object,
            ) -> dict[str, object]:
                observed_roots.append(
                    Path(session.project_root.bound_root).resolve()
                )
                return {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {},
                }

            original_cwd = os.getcwd()
            try:
                os.chdir(first)
                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(mcp_server, "handle_message", side_effect=fake_handle),
                ):
                    mcp_server.serve(stdin=stdin, stdout=stdout)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(
                observed_roots,
                [Path(first).resolve(), Path(first).resolve()],
            )

    def test_server_project_root_environment_overrides_startup_cwd(self) -> None:
        with (
            tempfile.TemporaryDirectory() as startup_cwd,
            tempfile.TemporaryDirectory() as configured_root,
        ):
            observed_roots: list[Path] = []

            def fake_handle(
                message: dict[str, object],
                *,
                session: mcp_server.ServerSession,
                **_: object,
            ) -> dict[str, object]:
                observed_roots.append(
                    Path(session.project_root.bound_root).resolve()
                )
                return {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {},
                }

            original_cwd = os.getcwd()
            try:
                os.chdir(startup_cwd)
                with (
                    patch.dict(
                        os.environ,
                        {"HDG_PROJECT_ROOT": configured_root},
                        clear=True,
                    ),
                    patch.object(mcp_server, "handle_message", side_effect=fake_handle),
                ):
                    mcp_server.serve(
                        stdin=io.StringIO(
                            json.dumps(rpc_request("ping", request_id=1)) + "\n"
                        ),
                        stdout=io.StringIO(),
                    )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(observed_roots, [Path(configured_root).resolve()])

    def test_codex_sandbox_metadata_binds_one_project_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first,
            tempfile.TemporaryDirectory() as second,
        ):
            session = mcp_server.ServerSession(
                project_root=mcp_server.ProjectRootBinding.from_startup(
                    None,
                    from_sandbox_meta=True,
                ),
                initialize_requested=True,
                initialized=True,
            )
            first_meta = {
                "codex/sandbox-state-meta": {
                    "sandboxCwd": Path(first).resolve().as_uri(),
                },
            }
            second_meta = {
                "codex/sandbox-state-meta": {
                    "sandboxCwd": Path(second).resolve().as_uri(),
                },
            }
            with patch.object(
                mcp_server,
                "call_tool",
                return_value={"status": "READY"},
            ) as mocked_call:
                first_response = mcp_server.handle_message(
                    tool_call(
                        "graph_status",
                        {"item_id": "t-one"},
                        meta=first_meta,
                    ),
                    session=session,
                )
                second_response = mcp_server.handle_message(
                    tool_call(
                        "graph_status",
                        {"item_id": "t-one"},
                        request_id=2,
                        meta=second_meta,
                    ),
                    session=session,
                )

            self.assertIs(first_response["result"]["isError"], False)
            self.assertEqual(
                Path(mocked_call.call_args.kwargs["root"]),
                Path(first).resolve(),
            )
            self.assertIs(second_response["result"]["isError"], True)
            self.assertEqual(
                second_response["result"]["structuredContent"]["error"]["code"],
                "PROJECT_ROOT_MISMATCH",
            )
            mocked_call.assert_called_once()

    def test_tool_calls_propagate_the_current_execution_host(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            codex_meta = {
                "codex/sandbox-state-meta": {
                    "sandboxCwd": Path(root).resolve().as_uri(),
                },
            }
            sessions = (
                (
                    mcp_server.ServerSession(
                        project_root=(
                            mcp_server.ProjectRootBinding.from_startup(
                                None,
                                from_sandbox_meta=True,
                            )
                        ),
                        initialize_requested=True,
                        initialized=True,
                        client_name="test-client",
                    ),
                    codex_meta,
                    "codex",
                ),
                (
                    mcp_server.ServerSession(
                        project_root=(
                            mcp_server.ProjectRootBinding.from_startup(root)
                        ),
                        initialize_requested=True,
                        initialized=True,
                        client_name="Claude Code",
                    ),
                    None,
                    "claude-code",
                ),
                (
                    mcp_server.ServerSession(
                        project_root=(
                            mcp_server.ProjectRootBinding.from_startup(root)
                        ),
                        initialize_requested=True,
                        initialized=True,
                        client_name="Cursor",
                    ),
                    None,
                    "cursor",
                ),
            )
            for session, meta, expected_runtime in sessions:
                with self.subTest(
                    expected_runtime=expected_runtime,
                ), patch.object(
                    mcp_server,
                    "call_tool",
                    return_value={"status": "READY"},
                ) as mocked_call:
                    response = mcp_server.handle_message(
                        tool_call(
                            "graph_status",
                            {"item_id": "t-one"},
                            meta=meta,
                        ),
                        session=session,
                    )

                    self.assertIs(response["result"]["isError"], False)
                    self.assertEqual(
                        mocked_call.call_args.kwargs[
                            "execution_host_runtime"
                        ],
                        expected_runtime,
                    )

    def test_skill_lifecycle_accepts_any_named_mcp_agent(self) -> None:
        session = ready_session()
        session.client_name = "Generic MCP Client"
        with patch.object(
            mcp_server,
            "call_tool",
            return_value={"status": "RECORDED"},
        ) as mocked_call:
            response = mcp_server.handle_message(
                tool_call(
                    "record_skill_activation",
                    {
                        "item_id": "t-one",
                        "stage": "DEVELOPMENT",
                        "skill_name": "tdd-workflow",
                        "activation": {},
                    },
                ),
                session=session,
            )

        self.assertIs(response["result"]["isError"], False)
        self.assertEqual(
            mocked_call.call_args.kwargs["execution_host_runtime"],
            "generic-mcp-client",
        )

    def test_skill_lifecycle_requires_a_session_client_identity(self) -> None:
        with patch.object(mcp_server, "call_tool") as mocked_call:
            response = mcp_server.handle_message(
                tool_call(
                    "record_skill_activation",
                    {
                        "item_id": "t-one",
                        "stage": "DEVELOPMENT",
                        "skill_name": "tdd-workflow",
                        "activation": {},
                    },
                ),
                session=ready_session(),
            )

        self.assertIs(response["result"]["isError"], True)
        self.assertEqual(
            response["result"]["structuredContent"]["error"]["code"],
            "MCP_SKILL_EXECUTION_HOST_REQUIRED",
        )
        mocked_call.assert_not_called()

    def test_deferred_project_root_requires_codex_sandbox_metadata(self) -> None:
        session = mcp_server.ServerSession(
            project_root=mcp_server.ProjectRootBinding.from_startup(
                None,
                from_sandbox_meta=True,
            ),
            initialize_requested=True,
            initialized=True,
        )
        response = mcp_server.handle_message(
            tool_call("graph_status", {"item_id": "t-one"}),
            session=session,
        )
        self.assertIs(response["result"]["isError"], True)
        self.assertEqual(
            response["result"]["structuredContent"]["error"]["code"],
            "PROJECT_ROOT_UNAVAILABLE",
        )

    def test_main_reports_an_explicit_transport_disconnect(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(mcp_server, "_configure_utf8_stdio"),
            patch.object(
                mcp_server,
                "serve",
                side_effect=BrokenPipeError("client closed"),
            ),
            patch("sys.stderr", stderr),
        ):
            self.assertEqual(mcp_server.main([]), 1)

        self.assertIn("ERROR PLUGIN_MCP_DISCONNECTED:", stderr.getvalue())
        self.assertNotIn("INTERNAL_ERROR", stderr.getvalue())

    def test_tool_results_preserve_sensitive_field_redaction(self) -> None:
        raw_result = {
            "safe": "visible",
            "apiToken": "secret-token",
            "stdout": "raw process output",
            "nested": {
                "password": "secret-password",
                "ordinary": "kept",
            },
        }
        with patch.object(mcp_server, "call_tool", return_value=raw_result):
            response = mcp_server.handle_message(
                tool_call("graph_status", {"item_id": "t-one"}),
                session=ready_session(),
            )

        self.assertIsNotNone(response)
        assert response is not None
        result = response["result"]
        structured = result["structuredContent"]
        self.assertEqual(structured["result"]["safe"], "visible")
        self.assertEqual(structured["result"]["apiToken"], "[REDACTED]")
        self.assertEqual(structured["result"]["stdout"], "[REDACTED]")
        self.assertEqual(
            structured["result"]["nested"]["password"],
            "[REDACTED]",
        )
        self.assertEqual(structured["result"]["nested"]["ordinary"], "kept")
        self.assertEqual(
            json.loads(result["content"][0]["text"]),
            structured,
        )

    def test_call_tool_injects_confirmation_and_fixed_server_context(self) -> None:
        arguments = {
            "item_id": "root-one",
            "expected_hierarchy_fingerprint": "a" * 64,
            "development_mode": "active",
        }
        with patch.object(
            mcp_tools,
            "execute_operation",
            return_value={"status": "FROZEN"},
        ) as mocked_execute:
            result = mcp_tools.call_tool(
                "freeze_hierarchy",
                arguments,
                root="C:/fixed-project",
                explicit_dogfood=True,
            )

        self.assertEqual(result, {"status": "FROZEN"})
        name, internal_arguments = mocked_execute.call_args.args
        context = mocked_execute.call_args.kwargs["context"]
        self.assertEqual(name, "freeze_hierarchy")
        self.assertEqual(
            internal_arguments,
            {**arguments, "confirmed": True},
        )
        self.assertEqual(context.root, "C:/fixed-project")
        self.assertIs(context.explicit_dogfood, True)
        self.assertNotIn("confirmed", arguments)
        freeze_definition = next(
            tool
            for tool in tool_definitions()
            if tool["name"] == "freeze_hierarchy"
        )
        self.assertNotIn("_meta", freeze_definition)
        self.assertIn(
            "one-time authorization to freeze",
            freeze_definition["inputSchema"]["properties"][
                "development_mode"
            ]["description"],
        )

    def test_split_acceptance_operations_fix_their_domain_actions(self) -> None:
        cases = {
            "record_independent_review_pass": "INDEPENDENT_REVIEW_PASS",
            "record_independent_review_blocked": "REVIEW_BLOCKED",
            "record_human_review_acceptance": "HUMAN_REVIEW_ACCEPTED",
            "record_user_confirmation": "USER_CONFIRMED",
        }
        for operation_name, expected_action in cases.items():
            with self.subTest(operation=operation_name):
                with patch.object(
                    operations,
                    "record_acceptance",
                    return_value={"action": expected_action},
                ) as mocked_record:
                    result = operations.execute_operation(
                        operation_name,
                        {
                            "item_id": "root-one",
                            "evidence": {"kind": "test"},
                        },
                        context=operations.OperationContext(
                            root="C:/fixed-project",
                        ),
                    )

                self.assertEqual(result, {"action": expected_action})
                self.assertEqual(
                    mocked_record.call_args.kwargs["action"],
                    expected_action,
                )

    def test_reserved_windows_item_identifier_is_rejected_before_execution(self) -> None:
        with patch.object(mcp_tools, "execute_operation") as mocked_execute:
            with self.assertRaises(GatedLoopError) as raised:
                mcp_tools.call_tool(
                    "graph_status",
                    {"item_id": "con"},
                    root=".",
                )

        self.assertEqual(raised.exception.code, "MCP_ARGUMENT_INVALID")
        mocked_execute.assert_not_called()

    def test_staging_integer_schema_rejects_boolean_and_out_of_range_values(
        self,
    ) -> None:
        cases = (
            (
                "begin_payload_upload",
                {
                    "upload_id": "upload-one",
                    "target_tool": "prepare_hierarchy",
                    "total_chunks": True,
                },
            ),
            (
                "begin_payload_upload",
                {
                    "upload_id": "upload-one",
                    "target_tool": "prepare_hierarchy",
                    "total_chunks": 0,
                },
            ),
            (
                "append_payload_chunk",
                {
                    "upload_id": "upload-one",
                    "generation_id": "0" * 32,
                    "chunk_index": -1,
                    "data": "{}",
                },
            ),
        )
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(GatedLoopError) as raised:
                    mcp_tools.validate_tool_arguments(name, arguments)
                self.assertEqual(
                    raised.exception.code,
                    "MCP_ARGUMENT_INVALID",
                )

    def test_unexpected_tool_exception_is_sanitized(self) -> None:
        with patch.object(
            mcp_server,
            "call_tool",
            side_effect=RuntimeError("C:/private/secret.txt"),
        ):
            response = mcp_server.handle_message(
                tool_call("graph_status", {"item_id": "t-one"}),
                session=ready_session(),
            )

        self.assertIsNotNone(response)
        assert response is not None
        result = response["result"]
        self.assertIs(result["isError"], True)
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("private", rendered)
        self.assertNotIn("secret.txt", rendered)
        self.assertEqual(
            result["structuredContent"]["error"]["code"],
            "INTERNAL_ERROR",
        )


if __name__ == "__main__":
    unittest.main()
