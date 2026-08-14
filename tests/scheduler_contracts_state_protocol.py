from __future__ import annotations

from .scheduler_contracts_support import (
    GatedLoopError,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    Mock,
    Path,
    ProjectRootBinding,
    SCHEDULER_STATE_CONTRACT,
    SUPPORTED_PROTOCOL_VERSIONS,
    SchedulerRepository,
    SimpleNamespace,
    TemporaryDirectory,
    at,
    call_tool,
    fingerprint,
    freeze_hierarchy,
    git_command,
    git_delivery_checkout,
    handle_message,
    io,
    json,
    legacy_delivery_hierarchy_017,
    mcp_server,
    modern_meta,
    patch,
    prepare_hierarchy,
    redirect_stderr,
    sqlite3,
    task_hierarchy,
    workspace_status,
)


class McpSurfaceTestsPart4:
    def test_remote_default_does_not_treat_main_as_feature_branch(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, _, base_commit, _ = git_delivery_checkout(root)
            git_command(repository, "switch", "main")
            git_command(
                repository,
                "update-ref",
                "refs/remotes/origin/release",
                base_commit,
            )
            git_command(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/release",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(repository),
            )

            self.assertEqual(
                discovered["gitWorkspace"]["role"],
                "MAINLINE",
            )
            self.assertEqual(
                discovered["workspacePreparation"]["baseRef"],
                "release",
            )

    def test_workspace_status_tool_starts_absent(self) -> None:
        with TemporaryDirectory() as root:
            result = call_tool(
                "workspace_status",
                {},
                root=root,
            )
        self.assertEqual(result["status"], "ABSENT")

    def test_self_hosting_requires_explicit_dogfood(self) -> None:
        with TemporaryDirectory() as root:
            Path(root, "pyproject.toml").write_text(
                '[project]\nname = "delivery-graph"\n',
                encoding="utf-8",
            )
            with self.assertRaises(GatedLoopError) as caught:
                workspace_status(root=root)
            self.assertEqual(
                caught.exception.code,
                "SELF_HOSTING_DOGFOOD_REQUIRED",
            )
            self.assertEqual(
                workspace_status(
                    root=root,
                    explicit_dogfood=True,
                )["status"],
                "ABSENT",
            )

    def test_legacy_database_is_rejected_without_migration(self) -> None:
        with TemporaryDirectory() as root:
            control = Path(root, ".layered-delivery")
            control.mkdir()
            Path(control, "governance.sqlite3").write_bytes(b"legacy")
            with self.assertRaises(GatedLoopError) as caught:
                workspace_status(root=root)
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_LEGACY_STATE_UNSUPPORTED",
        )

    def test_schema_v3_delivery_capability_state_is_incompatible(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
                now=at(0),
            )
            legacy = legacy_delivery_hierarchy_017()
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE hierarchies "
                    "SET hierarchy_json = ?, hierarchy_fingerprint = ? "
                    "WHERE root_id = ?",
                    (
                        json.dumps(legacy, separators=(",", ":")),
                        fingerprint(legacy),
                        prepared["rootId"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            operations = (
                ("workspace_status", lambda: workspace_status(root=root)),
                (
                    "hierarchy_load",
                    lambda: SchedulerRepository(root).hierarchy(
                        prepared["rootId"]
                    ),
                ),
            )
            for name, operation in operations:
                with self.subTest(operation=name):
                    with self.assertRaises(GatedLoopError) as caught:
                        operation()
                    self.assertEqual(
                        caught.exception.code,
                        "SCHEDULER_STATE_INCOMPATIBLE",
                    )

    def test_tampered_frozen_graph_is_rejected_by_runtime(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
                now=at(0),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )
            database = Path(root, ".layered-delivery", "scheduler.db")

            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT graph_json FROM hierarchies WHERE root_id = ?",
                    (prepared["rootId"],),
                ).fetchone()
                graph = json.loads(row[0])
                graph["runtime"]["retryPolicy"]["maxAttempts"] = 99
                connection.execute(
                    "UPDATE hierarchies SET graph_json = ? "
                    "WHERE root_id = ?",
                    (
                        json.dumps(graph, separators=(",", ":")),
                        prepared["rootId"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "advance_graph",
                    {"root_id": prepared["rootId"]},
                    root=root,
                )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_STATE_INVALID",
        )
        self.assertEqual(
            caught.exception.details["rootId"],
            prepared["rootId"],
        )

    def test_state_contract_mismatch_is_rejected_before_state_access(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            control = Path(root, ".layered-delivery")
            control.mkdir()
            database = control / "scheduler.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE scheduler_metadata ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO scheduler_metadata(key, value) "
                    "VALUES ('state_contract', ?)",
                    ("schema-v3-incompatible-generator",),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(GatedLoopError) as caught:
                SchedulerRepository(root).hierarchy("d-incompatible")

            inspection = sqlite3.connect(database)
            try:
                tables = {
                    row[0]
                    for row in inspection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                journal_mode = inspection.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
            finally:
                inspection.close()

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_STATE_CONTRACT_MISMATCH",
        )
        self.assertEqual(
            caught.exception.details["actualStateContract"],
            "schema-v3-incompatible-generator",
        )
        self.assertEqual(tables, {"scheduler_metadata"})
        self.assertEqual(journal_mode, "delete")

    def test_new_scheduler_records_the_current_state_contract(self) -> None:
        with TemporaryDirectory() as root:
            prepare_hierarchy(root=root, hierarchy=task_hierarchy())
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT value FROM scheduler_metadata "
                    "WHERE key = 'state_contract'"
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual(row[0], SCHEDULER_STATE_CONTRACT)

    def test_existing_scheduler_recreates_compatible_indexes(self) -> None:
        compatible_indexes = {
            "node_runs_by_run_status",
            "node_runs_by_lease_expires",
            "graph_events_by_run_type_event_id",
            "active_dispatch_reservations_by_expiry",
        }
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
                now=at(0),
            )
            repository = SchedulerRepository(root)
            stored_before_upgrade = repository.hierarchy(
                prepared["rootId"]
            )
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                for index_name in compatible_indexes:
                    connection.execute(
                        f'DROP INDEX "{index_name}"'
                    )
                connection.commit()
            finally:
                connection.close()

            stored_after_upgrade = SchedulerRepository(root).hierarchy(
                prepared["rootId"]
            )
            stored_after_second_connect = SchedulerRepository(
                root
            ).hierarchy(prepared["rootId"])

            inspection = sqlite3.connect(database)
            try:
                recreated_indexes = {
                    row[0]
                    for row in inspection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'index'"
                    )
                    if row[0] in compatible_indexes
                }
            finally:
                inspection.close()

        self.assertEqual(stored_after_upgrade, stored_before_upgrade)
        self.assertEqual(
            stored_after_second_connect,
            stored_before_upgrade,
        )
        self.assertEqual(recreated_indexes, compatible_indexes)

    def test_non_text_state_contract_is_rejected_as_unknown(self) -> None:
        with TemporaryDirectory() as root:
            control = Path(root, ".layered-delivery")
            control.mkdir()
            database = control / "scheduler.db"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "CREATE TABLE scheduler_metadata ("
                    "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO scheduler_metadata(key, value) "
                    "VALUES ('state_contract', ?)",
                    (sqlite3.Binary(b"untrusted-contract"),),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(GatedLoopError) as caught:
                SchedulerRepository(root).workspace_status()

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_STATE_CONTRACT_MISMATCH",
        )
        self.assertIsNone(
            caught.exception.details["actualStateContract"],
        )

    def test_schema_valid_graph_tamper_is_rejected_before_mutation(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
                now=at(0),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                row = connection.execute(
                    "SELECT graph_json FROM hierarchies WHERE root_id = ?",
                    (prepared["rootId"],),
                ).fetchone()
                graph = json.loads(row[0])
                task_loop = next(
                    node
                    for node in graph["nodes"]
                    if node["kind"] == "TASK_LOOP"
                )
                task_loop["loop"]["payload"]["tampered"] = True
                connection.execute(
                    "UPDATE hierarchies SET graph_json = ?, "
                    "graph_fingerprint = ? WHERE root_id = ?",
                    (
                        json.dumps(graph, separators=(",", ":")),
                        fingerprint(graph),
                        prepared["rootId"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "dispatch_loop",
                    {
                        "root_id": prepared["rootId"],
                        "node_id": "loop:t-service",
                        "owner": "agent-integrity",
                        "agent_id": "codex",
                        "dispatch_mode": "AUTO",
                        "dispatch_transport": "HOST_NATIVE",
                        "dispatch_reservation_id": "reservation-integrity",
                        "dispatch_decision_fingerprint": "0" * 64,
                        "receiver_context_id": "context-integrity",
                        "operation_id": "op-integrity",
                    },
                    root=root,
                    trusted_host_adapter="codex",
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_STATE_INVALID",
            )

            connection = sqlite3.connect(database)
            try:
                status = connection.execute(
                    "SELECT status FROM node_runs "
                    "WHERE node_id = 'loop:t-service'"
                ).fetchone()[0]
                claimed_events = connection.execute(
                    "SELECT COUNT(*) FROM graph_events "
                    "WHERE event_type = 'LOOP_CLAIMED'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(status, "READY")
            self.assertEqual(claimed_events, 0)

    def test_delivery_namespace_must_match_stored_root_id(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
                now=at(0),
            )
            database = Path(root, ".layered-delivery", "scheduler.db")
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE hierarchies SET root_id = 'd-alias' "
                    "WHERE root_id = ?",
                    (prepared["rootId"],),
                )
                connection.commit()
            finally:
                connection.close()

            operations = (
                lambda: workspace_status(root=root, root_id="d-alias"),
                lambda: SchedulerRepository(root).hierarchy("d-alias"),
            )
            for operation in operations:
                with self.assertRaises(GatedLoopError) as caught:
                    operation()
                self.assertEqual(
                    caught.exception.code,
                    "SCHEDULER_STATE_INVALID",
                )

    def test_mcp_initialize_and_tool_call(self) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            initialized = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "test-client",
                            "version": "1.0.0",
                        },
                    },
                },
                connection=connection,
            )
            self.assertIn(
                "outer Graph scheduler",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "planning host should pre-trigger a hint natively",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "actionable implementation, test, or Review finding stays "
                "inside the current Loop",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "Delivery Graph never recommends or selects a "
                "development model",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "plan_dispatch_batch atomically reserves every READY TASK "
                "or Review Loop",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "operation ID is required for progress, pause, and result "
                "writes",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "A new user requirement defaults to a new Delivery",
                initialized["result"]["instructions"],
            )
            instructions = initialized["result"]["instructions"]
            self.assertIn(
                "Workspace execution is fixed to CURRENT_WORKSPACE_SERIAL",
                instructions,
            )
            self.assertIn(
                "callers retain rootId per conversation and pass root_id on "
                "every continuation",
                instructions,
            )
            self.assertIn(
                "verifiable commit, a clean working tree and index, HEAD "
                "still matching its frozen binding, and no in-flight "
                "receiver",
                instructions,
            )
            self.assertIn(
                "do not automatically create, reserve, or launch a new "
                "worktree",
                instructions,
            )
            self.assertIn(
                "Do not create or launch a new worktree",
                instructions,
            )
            self.assertIn(
                "request_user_input for Codex and AskUserQuestion for "
                "Claude",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "must use the mapped native selector whenever that tool is "
                "callable in the current context",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "must not ask the user to type an option",
                initialized["result"]["instructions"],
            )
            for removed_instruction in (
                "AUTOMATIC_PARALLEL",
                "LINKED_WORKTREE_PARALLEL",
                "HOST_NATIVE_LINKED_WORKTREE",
                "environment=worktree",
                "hostDispatch",
                "preserving the primary checkout",
            ):
                self.assertNotIn(removed_instruction, instructions)
            self.assertNotIn(
                "EXCLUSIVE_PRIMARY_CHECKOUT",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "workspaceProvenance",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "confirmed_dirty_state_fingerprint",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "A feature branch name alone is not proof",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "never infer Revision continuity",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "All TASKs in that Delivery share those project branches",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "each TASK may stage and commit only its own changes on the "
                "Delivery branch",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "must never call graph_frontier or graph_status back-to-back",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "consume every immediate action in the current frontier before "
                "waiting",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "Review independence means independent judgment, not an "
                "automatic full-suite rerun",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "test start and completion when tests are actually executed",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "obey its postActionWait",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "short problem-free LIGHT Loop may omit heartbeat and progress",
                initialized["result"]["instructions"],
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                connection=connection,
            )
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {},
                    },
                },
                connection=connection,
            )
            structured = response["result"]["structuredContent"]
            self.assertTrue(structured["ok"])
            self.assertEqual(
                structured["result"]["status"],
                "ABSENT",
            )
            rendered = response["result"]["content"][0]["text"]
            self.assertEqual(json.loads(rendered), structured)
            self.assertNotIn("resultType", response["result"])

    def test_mcp_internal_error_is_correlated_without_leaking_details(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            stderr = io.StringIO()
            failure = RuntimeError(
                "token=raw-secret-value; 路径=G:\\Private Folder\\state.json"
            )
            leaky_frame = SimpleNamespace(
                filename=r"G:\Private Folder\state.py",
                name="raise_private_error",
                lineno=42,
            )
            with (
                patch(
                    "hdg.mcp_adapter.call_tool",
                    side_effect=failure,
                ),
                patch(
                    "hdg.mcp_adapter.traceback.extract_tb",
                    return_value=[leaky_frame],
                ),
                redirect_stderr(stderr),
            ):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "private-request-id",
                        "method": "tools/call",
                        "params": {
                            "name": "workspace_status",
                            "arguments": {},
                            "_meta": modern_meta(),
                        },
                    },
                    connection=connection,
                )

        structured = response["result"]["structuredContent"]
        error = structured["error"]
        self.assertEqual(error["code"], "INTERNAL_ERROR")
        self.assertEqual(error["message"], "Unexpected error")
        self.assertEqual(set(error["details"]), {"diagnosticId"})
        diagnostic_id = error["details"]["diagnosticId"]
        self.assertRegex(diagnostic_id, r"^[0-9a-f]{32}$")

        log_text = stderr.getvalue()
        log_text.encode("ascii")
        log_lines = log_text.splitlines()
        self.assertEqual(len(log_lines), 1)
        diagnostic = json.loads(log_lines[0])
        self.assertEqual(
            diagnostic,
            {
                "diagnosticId": diagnostic_id,
                "event": "delivery_graph_internal_error",
                "exceptionType": "RuntimeError",
                "operation": "tool:workspace_status",
                "stack": diagnostic["stack"],
            },
        )
        self.assertEqual(
            diagnostic["stack"],
            [
                {
                    "file": "state.py",
                    "function": "raise_private_error",
                    "line": 42,
                }
            ],
        )
        for frame in diagnostic["stack"]:
            self.assertEqual(set(frame), {"file", "function", "line"})
            self.assertNotIn("/", frame["file"])
            self.assertNotIn("\\", frame["file"])
        for secret in (
            "raw-secret-value",
            "Private Folder",
            "private-request-id",
            str(Path(root).resolve()),
        ):
            self.assertNotIn(secret, log_text)

    def test_mcp_internal_error_response_survives_diagnostic_log_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            failing_stderr = Mock()
            failing_stderr.write.side_effect = OSError("stderr unavailable")
            with (
                patch(
                    "hdg.mcp_adapter.call_tool",
                    side_effect=RuntimeError("private failure"),
                ),
                patch("hdg.mcp_adapter.sys.stderr", failing_stderr),
            ):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": "call",
                        "method": "tools/call",
                        "params": {
                            "name": "workspace_status",
                            "arguments": {},
                            "_meta": modern_meta(),
                        },
                    },
                    connection=connection,
                )

        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["code"], "INTERNAL_ERROR")
        self.assertRegex(
            error["details"]["diagnosticId"],
            r"^[0-9a-f]{32}$",
        )

    def test_mcp_server_top_level_error_emits_correlated_diagnostic(
        self,
    ) -> None:
        stderr = io.StringIO()
        with (
            patch("hdg.mcp_server._configure_utf8_stdio"),
            patch(
                "hdg.mcp_server.serve",
                side_effect=RuntimeError("token=top-level-secret"),
            ),
            redirect_stderr(stderr),
        ):
            returncode = mcp_server.main([])

        self.assertEqual(returncode, 1)
        lines = stderr.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        diagnostic = json.loads(lines[0])
        self.assertRegex(diagnostic["diagnosticId"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            diagnostic["operation"],
            "mcp_server",
        )
        self.assertNotIn("top-level-secret", stderr.getvalue())

    def test_mcp_supports_exactly_modern_and_claude_legacy_versions(
        self,
    ) -> None:
        self.assertEqual(
            LEGACY_PROTOCOL_VERSIONS,
            (LEGACY_PREFERRED_PROTOCOL_VERSION,),
        )
        self.assertEqual(
            SUPPORTED_PROTOCOL_VERSIONS,
            (
                MODERN_PROTOCOL_VERSION,
                LEGACY_PREFERRED_PROTOCOL_VERSION,
            ),
        )

    def test_mcp_modern_discovery_list_and_tool_call(self) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            discovery = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "discover",
                    "method": "server/discover",
                    "params": {"_meta": modern_meta()},
                },
                connection=connection,
            )
            discovered = discovery["result"]
            self.assertEqual(discovered["resultType"], "complete")
            self.assertEqual(
                discovered["supportedVersions"],
                [
                    MODERN_PROTOCOL_VERSION,
                    LEGACY_PREFERRED_PROTOCOL_VERSION,
                ],
            )
            self.assertEqual(discovered["cacheScope"], "private")
            self.assertGreater(discovered["ttlMs"], 0)
            self.assertEqual(
                discovered["_meta"][
                    "io.modelcontextprotocol/serverInfo"
                ]["name"],
                "delivery-graph",
            )
            self.assertFalse(connection.legacy_initialize_requested)

            listed = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "list",
                    "method": "tools/list",
                    "params": {"_meta": modern_meta()},
                },
                connection=connection,
            )
            self.assertEqual(
                listed["result"]["resultType"],
                "complete",
            )
            self.assertEqual(len(listed["result"]["tools"]), 33)
            self.assertEqual(listed["result"]["cacheScope"], "private")

            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "call",
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {},
                        "_meta": modern_meta(
                            client_version="1.0.1",
                        ),
                    },
                },
                connection=connection,
            )
            self.assertEqual(
                response["result"]["resultType"],
                "complete",
            )
            self.assertEqual(
                response["result"]["structuredContent"]["result"][
                    "status"
                ],
                "ABSENT",
            )
            self.assertFalse(connection.legacy_initialized)
