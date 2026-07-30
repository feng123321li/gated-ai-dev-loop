from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from hdg.controller import (
    ControllerContext,
    LayeredDeliveryController,
)
from hdg.errors import GatedLoopError
from hdg.hierarchy_contract import hierarchy_contract
from hdg.host_policy import ProjectRootBinding
from hdg.jsonio import fingerprint
from hdg.mcp_adapter import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    LEGACY_PREFERRED_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSION,
    McpConnection,
    PROTOCOL_VERSION_META_KEY,
    SUPPORTED_PROTOCOL_VERSIONS,
    handle_message,
)
from hdg.mcp_tools import (
    call_tool,
    tool_definitions,
    validate_tool_arguments,
)
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import workspace_status
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import SchedulerRepository

from .test_loop_architecture import loop_descriptor, task_hierarchy
from .test_scheduler_runtime import at


def modern_meta(
    *,
    version: str = MODERN_PROTOCOL_VERSION,
    client_name: str = "test-modern-client",
    client_version: str = "1.0.0",
    **extra: object,
) -> dict[str, object]:
    return {
        PROTOCOL_VERSION_META_KEY: version,
        CLIENT_CAPABILITIES_META_KEY: {},
        CLIENT_INFO_META_KEY: {
            "name": client_name,
            "version": client_version,
        },
        **extra,
    }


def legacy_delivery_hierarchy_017() -> dict:
    tasks = [
        {
            "definition": {
                "schemaVersion": 3,
                "id": "t-api",
                "kind": "TASK",
                "parentId": "c-service",
                "title": "Run API task",
                "summary": "Run the API Task Loop.",
                "execution": {
                    "dependsOn": [],
                    "loop": loop_descriptor(),
                },
            },
            "children": [],
        }
    ]
    capability = {
        "definition": {
            "schemaVersion": 3,
            "id": "c-service",
            "kind": "CAPABILITY",
            "parentId": "d-service",
            "title": "Coordinate service capability",
            "summary": "Join service Task Loops.",
            "decomposition": {"dependsOn": []},
            "children": [
                {
                    "id": "t-api",
                    "kind": "TASK",
                    "title": "Run API task",
                }
            ],
        },
        "children": tasks,
    }
    return {
        "schemaVersion": 3,
        "skillHints": [],
        "reviewLoop": loop_descriptor(
            "root/independent-review-loop@1"
        ),
        "root": {
            "definition": {
                "schemaVersion": 3,
                "id": "d-service",
                "kind": "DELIVERY",
                "title": "Deliver service",
                "summary": "Coordinate the service delivery.",
                "decomposition": {},
                "children": [
                    {
                        "id": "c-service",
                        "kind": "CAPABILITY",
                        "title": "Coordinate service capability",
                    }
                ],
            },
            "children": [capability],
        },
    }


class HierarchyContractTests(unittest.TestCase):
    def test_every_contract_example_is_valid(self) -> None:
        for root_kind in ("TASK", "GROUP"):
            with self.subTest(root_kind=root_kind):
                contract = hierarchy_contract(
                    root_kind=root_kind,
                )
                normalized = validate_hierarchy_definition(
                    contract["example"]
                )
                self.assertEqual(
                    normalized["root"]["definition"]["kind"],
                    root_kind,
                )

    def test_contract_places_detail_inside_opaque_loop_payload(
        self,
    ) -> None:
        contract = hierarchy_contract(root_kind="TASK")
        definition_properties = contract["inputSchema"]["$defs"][
            "taskRootDefinition"
        ]["properties"]

        self.assertEqual(
            set(definition_properties),
            {
                "schemaVersion",
                "id",
                "kind",
                "parentId",
                "title",
                "summary",
                "execution",
            },
        )
        self.assertEqual(
            definition_properties["execution"]["properties"]["loop"],
            {"$ref": "#/$defs/loop"},
        )
        payload = contract["inputSchema"]["$defs"]["loop"][
            "properties"
        ]["payload"]
        self.assertTrue(payload["additionalProperties"])
        self.assertEqual(
            set(contract["inputSchema"]["properties"]),
            {"delivery", "root"},
        )
        skill_hints = contract["inputSchema"]["$defs"][
            "taskRootNode"
        ]["properties"]["skillHints"]
        self.assertEqual(
            skill_hints["items"],
            {"$ref": "#/$defs/skillHint"},
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["skillHint"]["required"],
            ["name", "purpose"],
        )
        self.assertIn(
            "runtime",
            skill_hints["description"],
        )
        self.assertIn(
            "advisory",
            " ".join(contract["invariants"]).lower(),
        )
        group_children = contract["inputSchema"]["$defs"][
            "groupChildNode"
        ]["properties"]["children"]["items"]["oneOf"]
        self.assertEqual(
            {
                item["$ref"]
                for item in group_children
            },
            {
                "#/$defs/groupChildNode",
                "#/$defs/taskChildNode",
            },
        )
        self.assertEqual(
            contract["inputSchema"]["properties"]["delivery"]["properties"][
                "reviewLoop"
            ],
            {"$ref": "#/$defs/loop"},
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["taskRootNode"]["properties"][
                "reviewLoop"
            ],
            {"$ref": "#/$defs/loop"},
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["groupRootNode"]["properties"][
                "reviewLoop"
            ],
            {"$ref": "#/$defs/loop"},
        )
        acceptance_guidance = contract["projectionGuidance"][
            "acceptanceReports"
        ]
        self.assertEqual(
            acceptance_guidance["scope"],
            "CURRENT_LAYER",
        )
        self.assertEqual(
            acceptance_guidance["groupReport"]["childReferences"],
            ["status", "summary", "acceptanceLink"],
        )
        self.assertEqual(
            acceptance_guidance["deliveryReport"]["rootReference"],
            ["status", "summary", "acceptanceLink"],
        )
        self.assertEqual(
            acceptance_guidance["nonDuplicatedFromLowerLayers"],
            ["payload", "evidence", "reviewFindings"],
        )
        interface_guidance = contract["projectionGuidance"]["interfaces"]
        self.assertEqual(
            interface_guidance["location"],
            "TASK definition.execution.loop.payload.interfaces",
        )
        self.assertEqual(
            interface_guidance["protocolExamples"],
            ["HTTP", "DUBBO", "GRPC", "GRAPHQL", "MESSAGE"],
        )
        self.assertEqual(
            interface_guidance["requiredFields"],
            [
                "protocol",
                "name",
                "summary",
                "changeType",
                "before",
                "after",
            ],
        )
        self.assertEqual(
            interface_guidance["changeTypes"],
            ["CREATE", "MODIFY", "DELETE"],
        )
        self.assertEqual(
            interface_guidance["snapshotRequiredFields"],
            ["request", "response"],
        )
        self.assertIn(
            "identifier",
            interface_guidance["genericSnapshotFields"],
        )
        self.assertIn(
            "method",
            interface_guidance["httpSnapshotFields"],
        )
        self.assertIn(
            "path",
            interface_guidance["httpSnapshotFields"],
        )
        self.assertIn(
            "service",
            interface_guidance["dubboSnapshotFields"],
        )
        self.assertIn(
            "method",
            interface_guidance["dubboSnapshotFields"],
        )
        self.assertIn(
            "explicit before and after snapshots",
            interface_guidance["description"],
        )
        self.assertIn(
            "has no interface projection or link",
            interface_guidance["description"],
        )


class McpSurfaceTests(unittest.TestCase):
    def test_shared_controller_executes_without_mcp_context(self) -> None:
        with TemporaryDirectory() as root:
            controller = LayeredDeliveryController()
            result = controller.execute(
                "workspace_status",
                {},
                context=ControllerContext(project_root=root),
            )
            self.assertEqual(result["status"], "ABSENT")
            with self.assertRaises(GatedLoopError) as caught:
                controller.execute(
                    "missing",
                    {},
                    context=ControllerContext(project_root=root),
                )
            self.assertEqual(
                caught.exception.code,
                "CONTROLLER_OPERATION_UNKNOWN",
            )

    def test_tool_schemas_are_closed_and_only_destructive_calls_prompt(
        self,
    ) -> None:
        tools = tool_definitions()
        self.assertTrue(tools)
        self.assertTrue(
            all(
                tool["inputSchema"]["additionalProperties"] is False
                for tool in tools
            )
        )
        human = {
            tool["name"]
            for tool in tools
            if tool.get("_meta", {}).get(
                "anthropic/requiresUserInteraction"
            )
        }
        self.assertEqual(
            human,
            {
                "cancel_graph_run",
            },
        )
        by_name = {tool["name"]: tool for tool in tools}
        self.assertEqual(
            by_name["available_agents"]["inputSchema"]["required"],
            [],
        )
        self.assertEqual(
            by_name["recommend_executors"]["inputSchema"]["required"],
            ["root_id"],
        )
        self.assertEqual(
            set(
                by_name["recommend_executors"]["inputSchema"][
                    "properties"
                ]
            ),
            {"root_id"},
        )
        self.assertNotIn("_meta", by_name["available_agents"])
        self.assertNotIn("_meta", by_name["recommend_executors"])
        pause_schema = by_name["pause_loop"]["inputSchema"]
        self.assertNotIn("resume_at", pause_schema["required"])
        self.assertEqual(
            pause_schema["properties"]["resume_at"],
            {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Optional known provider quota reset time as an ISO "
                    "8601 timestamp. Before it, the same Agent waits for a "
                    "host-native scheduled prompt or manual resume. The "
                    "first frontier call at or after it makes the same Loop "
                    "attempt ready for redispatch."
                ),
            },
        )
        self.assertEqual(
            pause_schema["properties"]["capacity_scope"],
            {
                "type": "string",
                "enum": ["EXECUTOR", "HOST"],
                "description": (
                    "Required with resume_at. EXECUTOR waits for the "
                    "same Loop Agent; HOST means the native "
                    "orchestrator itself is quota-limited. Both wait for a "
                    "host-native scheduled prompt or manual Agent resume."
                ),
            },
        )
        freeze = by_name["freeze_hierarchy"]
        self.assertNotIn("_meta", freeze)
        freeze_schema = freeze["inputSchema"]
        self.assertNotIn("confirmed", freeze_schema["properties"])
        self.assertEqual(
            freeze_schema["properties"]["execution_mode"],
            {
                "type": "string",
                "enum": ["active", "manual"],
                "description": (
                    "User-selected host execution mode. active continues "
                    "the Graph in this session; manual freezes and emits "
                    "a handoff for another session."
                ),
            },
        )
        self.assertIn("execution_mode", freeze_schema["required"])
        final_confirmation = by_name["record_user_confirmation"][
            "inputSchema"
        ]["properties"]["confirmed"]
        self.assertNotIn(
            "_meta",
            by_name["record_user_confirmation"],
        )
        self.assertEqual(final_confirmation["type"], "boolean")
        self.assertIs(final_confirmation["const"], True)

    def test_argument_validation_rejects_unknown_fields(self) -> None:
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "workspace_status",
                {"legacyScope": ["src/**"]},
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments("missing_tool", {})
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "recommend_executors",
                {
                    "root_id": "d-service",
                    "temporarily_unavailable_agent_ids": ["codex"],
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "record_user_confirmation",
                {
                    "root_id": "d-service",
                    "confirmed": "true",
                    "confirmed_by": "human",
                    "summary": "accepted",
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "freeze_hierarchy",
                {
                    "root_id": "d-service",
                    "expected_hierarchy_fingerprint": "fingerprint",
                    "execution_mode": "adjust",
                    "confirmed_by": "human",
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "freeze_hierarchy",
                {
                    "root_id": "d-service",
                    "expected_hierarchy_fingerprint": "fingerprint",
                    "execution_mode": "active",
                    "confirmed": True,
                    "confirmed_by": "human",
                },
            )
        with self.assertRaises(GatedLoopError):
            validate_tool_arguments(
                "record_user_confirmation",
                {
                    "root_id": "d-service",
                    "confirmed": 1,
                    "confirmed_by": "human",
                    "summary": "accepted",
                },
            )

    def test_freeze_adapter_injects_strict_boolean_confirmation(
        self,
    ) -> None:
        for execution_mode in ("active", "manual"):
            with self.subTest(execution_mode=execution_mode):
                with TemporaryDirectory() as root:
                    prepared = call_tool(
                        "prepare_hierarchy",
                        {"hierarchy": task_hierarchy()},
                        root=root,
                    )
                    frozen = call_tool(
                        "freeze_hierarchy",
                        {
                            "root_id": prepared["rootId"],
                            "expected_hierarchy_fingerprint": (
                                prepared["hierarchyFingerprint"]
                            ),
                            "execution_mode": execution_mode,
                            "confirmed_by": "human",
                        },
                        root=root,
                    )
                    status = call_tool(
                        "workspace_status",
                        {},
                        root=root,
                    )

                self.assertEqual(frozen["status"], "ACTIVE")
                self.assertEqual(
                    frozen["executionMode"],
                    execution_mode,
                )
                self.assertEqual(frozen["confirmedBy"], "human")
                self.assertEqual(status["status"], "ACTIVE")

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
                '[project]\nname = "layered-delivery"\n',
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
                        "operation_id": "op-integrity",
                    },
                    root=root,
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
                lambda: workspace_status(root=root),
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
                "skillHints are advisory runtime preferences",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "actionable implementation, test, or Review finding stays "
                "inside the current Loop",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "available_agents and recommend_executors expose live, "
                "non-binding",
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
                "layered-delivery",
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
            self.assertEqual(len(listed["result"]["tools"]), 19)
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

    def test_mcp_modern_rejects_missing_or_unsupported_metadata(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            missing = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "server/discover",
                    "params": {},
                },
                connection=connection,
            )
            self.assertEqual(missing["error"]["code"], -32602)

            unsupported = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "server/discover",
                    "params": {
                        "_meta": modern_meta(
                            version="2099-01-01",
                        )
                    },
                },
                connection=connection,
            )
            self.assertEqual(
                unsupported["error"]["code"],
                -32022,
            )
            self.assertEqual(
                unsupported["error"]["data"]["requested"],
                "2099-01-01",
            )
            self.assertEqual(
                unsupported["error"]["data"]["supported"][0],
                MODERN_PROTOCOL_VERSION,
            )

    def test_legacy_initialize_never_negotiates_modern_semantics(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root)
            )
            response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": MODERN_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "legacy-client",
                            "version": "1.0.0",
                        },
                    },
                },
                connection=connection,
            )
            self.assertEqual(
                response["result"]["protocolVersion"],
                LEGACY_PREFERRED_PROTOCOL_VERSION,
            )

    def test_modern_codex_project_root_is_per_request(self) -> None:
        with (
            TemporaryDirectory() as first,
            TemporaryDirectory() as second,
        ):
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(
                    None,
                    from_sandbox_meta=True,
                )
            )
            for request_id, root in enumerate((first, second), start=1):
                response = handle_message(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {
                            "name": "workspace_status",
                            "arguments": {},
                            "_meta": modern_meta(
                                **{
                                    "codex/sandbox-state-meta": {
                                        "sandboxCwd": Path(root).as_uri(),
                                    }
                                }
                            ),
                        },
                    },
                    connection=connection,
                )
                self.assertEqual(
                    response["result"]["structuredContent"]["result"][
                        "status"
                    ],
                    "ABSENT",
                )
            self.assertIsNone(connection.project_root.bound_root)


if __name__ == "__main__":
    unittest.main()
