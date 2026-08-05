from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

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
    _tool_result,
    handle_message,
)
from hdg.mcp_tools import (
    call_tool,
    tool_definitions,
    validate_tool_arguments,
)
from hdg.graph_runtime import attest_loop_receiver
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import workspace_status
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    group_hierarchy,
    loop_descriptor,
    task_hierarchy,
)
from .test_scheduler_runtime import at
from .automatic_dispatch import reserve_loop


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


def isolated_task_hierarchy(
    delivery_id: str,
    task_id: str,
    *,
    claims: list[str] | None = None,
) -> dict:
    hierarchy = task_hierarchy()
    hierarchy["delivery"]["id"] = delivery_id
    hierarchy["delivery"]["title"] = f"Deliver {delivery_id}"
    definition = hierarchy["root"]["definition"]
    definition["id"] = task_id
    definition["title"] = f"Run {task_id}"
    definition["execution"]["loop"]["resourceClaims"] = claims or []
    return hierarchy
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


def git_command(worktree: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(worktree), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def git_delivery_checkout(
    root: str,
    *,
    delivery_id: str = "d-git",
    mainline: str = "main",
) -> tuple[Path, Path, str, str]:
    repository = Path(root, "repository")
    worktree = Path(root, "worktrees", delivery_id)
    repository.mkdir()
    git_command(
        repository,
        "init",
        f"--initial-branch={mainline}",
    )
    git_command(repository, "config", "user.name", "Scheduler Tests")
    git_command(
        repository,
        "config",
        "user.email",
        "scheduler-tests@example.invalid",
    )
    Path(repository, "README.md").write_text(
        "# Git delivery fixture\n",
        encoding="utf-8",
    )
    git_command(repository, "add", "README.md")
    git_command(repository, "commit", "-m", "Initial main baseline")
    base_commit = git_command(repository, "rev-parse", "HEAD")
    branch_ref = f"feature/{delivery_id}"
    git_command(
        repository,
        "worktree",
        "add",
        "-b",
        branch_ref,
        str(worktree),
        mainline,
    )
    return repository, worktree, base_commit, branch_ref


def bind_delivery_to_git(
    hierarchy: dict,
    *,
    branch_ref: str,
    base_commit: str,
    base_ref: str = "main",
) -> dict:
    hierarchy["delivery"]["gitBinding"] = {
        "branchRef": branch_ref,
        "baseRef": base_ref,
        "baseCommit": base_commit,
        "integrationTarget": base_ref,
    }
    return hierarchy


class HierarchyContractTests(unittest.TestCase):
    def test_requirement_key_is_stable_and_exposed_for_delivery_continuity(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"]["requirementKey"] = "mprotein-443"

        normalized = validate_hierarchy_definition(hierarchy)
        contract = hierarchy_contract(root_kind="TASK")

        self.assertEqual(
            normalized["delivery"]["requirementKey"],
            "MPROTEIN-443",
        )
        self.assertIn(
            "requirementKey",
            contract["inputSchema"]["properties"]["delivery"][
                "properties"
            ],
        )
        self.assertEqual(
            contract["projectionGuidance"]["deliveryContinuity"][
                "identity"
            ],
            "requirementKey -> delivery.id",
        )
        self.assertEqual(
            contract["projectionGuidance"]["deliveryContinuity"][
                "duplicatePolicy"
            ],
            "REJECT_DIFFERENT_DELIVERY_ID",
        )

    def test_execution_interaction_is_controller_owned_and_exact(self) -> None:
        interaction = hierarchy_contract(root_kind="TASK")[
            "projectionGuidance"
        ]["executionInteraction"]

        self.assertEqual(interaction["owner"], "CONTROLLER")
        self.assertEqual(
            interaction["artifactGate"],
            "CHOICE_READY_ARTIFACTS_READY",
        )
        self.assertEqual(interaction["hostMapping"], "MECHANICAL_NO_REWRITE")
        self.assertEqual(
            interaction["selectionTool"],
            "select_execution_mode",
        )
        self.assertEqual(
            interaction["automaticResumeTool"],
            "resume_execution_mode",
        )
        self.assertEqual(
            interaction["manualStartTool"],
            "start_manual_handoff",
        )
        self.assertEqual(
            interaction["manualExecutionBoundary"],
            "MANUAL_TASK_ONLY_REVIEWS_REMAIN_AUTOMATIC",
        )
        choice = interaction["executionChoice"]
        self.assertEqual(choice["defaultOptionId"], "AUTOMATIC")
        self.assertEqual(
            [option["id"] for option in choice["options"]],
            ["AUTOMATIC", "MANUAL"],
        )
        self.assertEqual(
            interaction["directTextAction"],
            "CONTINUE_REQUIREMENT_DISCUSSION",
        )

    def test_preview_selects_the_current_hosts_native_question_tool(
        self,
    ) -> None:
        for host_adapter, tool_name in (
            ("codex", "request_user_input"),
            ("claude-code", "AskUserQuestion"),
        ):
            with self.subTest(host_adapter=host_adapter):
                with TemporaryDirectory() as root:
                    preview = call_tool(
                        "preview_hierarchy",
                        {
                            "hierarchy": isolated_task_hierarchy(
                                f"d-{host_adapter}",
                                f"t-{host_adapter}",
                            )
                        },
                        root=root,
                        trusted_host_adapter=host_adapter,
                    )

                    self.assertEqual(
                        preview["nextAction"],
                        "PRESENT_HOST_NATIVE_EXECUTION_CHOICE",
                    )
                    self.assertEqual(
                        preview["executionChoice"]["activeHostMapping"],
                        {
                            "hostAdapterId": host_adapter,
                            "tool": tool_name,
                            "requiredWhenCallable": True,
                        },
                    )

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
        delivery_properties = contract["inputSchema"]["properties"][
            "delivery"
        ]["properties"]
        self.assertEqual(
            delivery_properties["reviewLoop"]["oneOf"],
            [{"$ref": "#/$defs/loop"}, {"type": "null"}],
        )
        self.assertEqual(
            delivery_properties["assuranceProfile"]["enum"],
            ["LIGHT", "STANDARD"],
        )
        self.assertNotIn(
            "assuranceProfile",
            contract["inputSchema"]["properties"]["delivery"]["required"],
        )
        git_binding = contract["inputSchema"]["properties"]["delivery"][
            "properties"
        ]["gitBinding"]
        self.assertEqual(
            set(git_binding["properties"]),
            {
                "branchRef",
                "baseRef",
                "baseCommit",
                "integrationTarget",
            },
        )
        self.assertNotIn(
            "gitBinding",
            contract["inputSchema"]["properties"]["delivery"]["required"],
        )
        self.assertEqual(
            contract["projectionGuidance"]["gitBinding"][
                "defaultMainlinePreference"
            ],
            ["main", "master"],
        )
        self.assertEqual(
            contract["projectionGuidance"]["gitBinding"][
                "taskBranchPolicy"
            ],
            "SHARED_DELIVERY_FEATURE_BRANCH",
        )
        self.assertFalse(
            contract["projectionGuidance"]["gitBinding"][
                "taskBranchBindingsSupported"
            ],
        )
        self.assertEqual(
            contract["projectionGuidance"]["gitBinding"][
                "taskCommitPolicy"
            ],
            "TASK_SCOPED_COMMITS_ON_DELIVERY_BRANCH",
        )
        self.assertEqual(
            contract["inputSchema"]["$defs"]["taskRootNode"]["properties"][
                "reviewLoop"
            ]["oneOf"],
            [{"$ref": "#/$defs/loop"}, {"type": "null"}],
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
            "signature",
            interface_guidance["dubboSnapshotFields"],
        )
        self.assertEqual(
            interface_guidance["supportedFieldShapes"],
            {
                "fieldList": (
                    "[{name,type,required?,maxLength?,"
                    "description?,example?}]"
                ),
                "typedObject": (
                    "{type,description?,fields|properties:[...]}"
                ),
                "fieldAttributes": [
                    "name",
                    "type",
                    "required",
                    "maxLength",
                    "description",
                    "example",
                ],
                "emptyContract": "[]",
                "requestLocationContainers": {
                    "headers": "header",
                    "pathParameters": "path",
                    "queryParameters": "query",
                    "body": "body",
                    "businessParameters": "business",
                    "contextDependencies": "context",
                    "contextDerived": "context",
                    "contextualInputs": "context",
                    "parameters": "",
                },
                "responseAliases": {
                    "type": ["type", "controllerReturnType"],
                    "fields": [
                        "fields",
                        "properties",
                        "controllerReturnFields",
                    ],
                    "description": [
                        "description",
                        "summary",
                    ],
                    "ignoredEnvelopeMetadata": [
                        "wireType",
                        "frameworkEnvelope",
                        "wrapping",
                    ],
                },
                "emptyRequestText": "无入参",
                "emptyResponseText": "无出参",
                "metadataPolicy": "CONTAINERS_ARE_NOT_FIELDS",
            },
        )
        self.assertEqual(
            interface_guidance["fieldProjection"],
            {
                "layout": "REQUEST_RESPONSE_TABLES",
                "documents": {
                    "index": "interfaces.md",
                    "detailsDirectory": "interfaces/",
                    "oneDocumentPerInterface": True,
                },
                "changeStates": [
                    "CREATE",
                    "MODIFY",
                    "DELETE",
                    "UNCHANGED",
                ],
                "requestComparisonColumns": [
                    "type",
                    "required",
                    "description",
                    "example",
                ],
                "responseComparisonColumns": [
                    "type",
                    "description",
                    "example",
                ],
                "dubboComparisonColumns": [
                    "type",
                    "required",
                    "maxLength",
                    "description",
                    "example",
                ],
                "protocolLayouts": {
                    "HTTP": [
                        "Path 参数",
                        "Query 参数",
                        "请求头",
                        "请求体",
                        "响应参数",
                    ],
                    "DUBBO": ["接口", "方法", "调用参数", "返回结果"],
                    "DEFAULT": ["入参", "出参"],
                },
                "responseEnvelopePolicy": "IGNORE",
                "deletedValueStyle": "MARKDOWN_STRIKETHROUGH",
                "singleSidedChangeStyle": "PRESENT_VALUE_ONLY",
                "transitionFormat": "BEFORE_TO_AFTER",
            },
        )
        self.assertIn(
            "explicit before and after snapshots",
            interface_guidance["description"],
        )
        self.assertIn(
            "has no interface projection or link",
            interface_guidance["description"],
        )
        self.assertIn(
            "source of truth",
            interface_guidance["description"],
        )
        self.assertIn("Torna", interface_guidance["description"])

    def test_git_binding_normalizes_and_rejects_invalid_contracts(
        self,
    ) -> None:
        source = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="feature/d-service",
            base_commit="a" * 40,
        )
        normalized = validate_hierarchy_definition(source)
        self.assertEqual(
            normalized["delivery"]["gitBinding"],
            {
                "branchRef": "feature/d-service",
                "baseRef": "main",
                "baseCommit": "a" * 40,
                "integrationTarget": "main",
            },
        )

        wrong_target = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="feature/d-service",
            base_commit="a" * 40,
        )
        wrong_target["delivery"]["gitBinding"][
            "integrationTarget"
        ] = "release"
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(wrong_target)
        self.assertEqual(
            caught.exception.code,
            "DELIVERY_GIT_BINDING_INVALID",
        )

        unsafe_branch = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="../feature",
            base_commit="a" * 40,
        )
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(unsafe_branch)

        full_ref = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="refs/heads/feature/d-service",
            base_commit="a" * 40,
        )
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(full_ref)

        mainline_delivery = bind_delivery_to_git(
            task_hierarchy(),
            branch_ref="master",
            base_commit="a" * 40,
        )
        with self.assertRaises(GatedLoopError):
            validate_hierarchy_definition(mainline_delivery)

    def test_posix_project_roots_remain_case_sensitive(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"]["projectScopes"] = [
            {
                "id": "upper",
                "workspaceRoot": "C:\\srv\\Repo",
                "access": "READ_ONLY",
            },
            {
                "id": "lower",
                "workspaceRoot": "C:\\srv\\repo",
                "access": "READ_ONLY",
            },
        ]
        with patch(
            "hdg.model_core.os.path.normcase",
            side_effect=lambda value: value,
        ):
            normalized = validate_hierarchy_definition(hierarchy)

        self.assertEqual(
            [scope["id"] for scope in normalized["delivery"]["projectScopes"]],
            ["lower", "upper"],
        )


class McpSurfaceTests(unittest.TestCase):
    def test_progress_tool_result_defaults_to_a_chinese_table(self) -> None:
        result = _tool_result(
            {
                "ok": True,
                "result": {
                    "progressMonitor": {
                        "alerts": [
                            {
                                "code": "SUSPECT_NOT_STARTED",
                                "messageZh": "领取后仍没有首次独立心跳。",
                            }
                        ],
                        "markdownTable": (
                            "| 节点 | 当前阶段 |\n"
                            "|---|---|\n"
                            "| provider review | 运行测试 |"
                        ),
                    }
                },
            },
            is_error=False,
            modern=True,
        )

        rendered = result["content"][0]["text"]
        self.assertIn("## 后台执行进度", rendered)
        self.assertIn("领取后仍没有首次独立心跳", rendered)
        self.assertIn("| provider review | 运行测试 |", rendered)
        self.assertNotIn("SUSPECT_NOT_STARTED", rendered)
        self.assertIn("progressMonitor", result["structuredContent"]["result"])

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
                "refreeze_task_requirement",
                "unfreeze_task_requirement",
            },
        )
        by_name = {tool["name"]: tool for tool in tools}
        progress_tool = by_name["report_loop_progress"]
        self.assertFalse(progress_tool["annotations"]["readOnlyHint"])
        self.assertEqual(
            progress_tool["inputSchema"]["properties"]["phase"]["enum"],
            [
                "STARTING",
                "INSPECTING",
                "TESTING",
                "INVESTIGATING",
                "FIXING",
                "REVIEWING",
                "VERIFYING",
                "WAITING",
            ],
        )
        self.assertIn(
            "user's current language",
            progress_tool["inputSchema"]["properties"]["summary_zh"][
                "description"
            ],
        )
        self.assertNotIn(
            "Simplified Chinese",
            progress_tool["inputSchema"]["properties"]["summary_zh"][
                "description"
            ],
        )
        self.assertEqual(
            set(
                by_name["workspace_status"]["inputSchema"][
                    "properties"
                ]
            ),
            {
                "root_id",
                "base_ref",
                "confirmed_dirty_state_fingerprint",
            },
        )
        self.assertEqual(
            by_name["workspace_status"]["inputSchema"]["required"],
            [],
        )
        self.assertNotIn("available_agents", by_name)
        self.assertEqual(
            by_name["preview_hierarchy"]["inputSchema"]["required"],
            ["hierarchy"],
        )
        self.assertEqual(
            by_name["select_execution_mode"]["inputSchema"]["required"],
            [
                "root_id",
                "selection",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        )
        selection_properties = by_name["select_execution_mode"][
            "inputSchema"
        ]["properties"]
        self.assertEqual(
            selection_properties["selection"]["enum"],
            ["AUTOMATIC", "MANUAL"],
        )
        self.assertNotIn("confirmed", selection_properties)
        self.assertIn(
            "without another confirmation",
            by_name["select_execution_mode"]["description"],
        )
        self.assertEqual(
            by_name["resume_execution_mode"]["inputSchema"]["required"],
            [
                "root_id",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
            ],
        )
        self.assertNotIn(
            "confirmed_by",
            by_name["resume_execution_mode"]["inputSchema"]["properties"],
        )
        self.assertEqual(
            by_name["create_manual_handoff"]["inputSchema"]["required"],
            [
                "hierarchy",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        )
        self.assertEqual(
            by_name["start_manual_handoff"]["inputSchema"]["required"],
            [
                "root_id",
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "started_by",
            ],
        )
        self.assertIn(
            "never weakens or skips STANDARD Review nodes",
            by_name["start_manual_handoff"]["description"],
        )
        self.assertNotIn(
            "confirmed",
            by_name["create_manual_handoff"]["inputSchema"]["properties"],
        )
        manual_handoff_properties = by_name["create_manual_handoff"][
            "inputSchema"
        ]["properties"]
        self.assertIn("expected_current_revision", manual_handoff_properties)
        self.assertIn("continuity_basis", manual_handoff_properties)
        self.assertIn("revision_reason", manual_handoff_properties)
        self.assertEqual(
            manual_handoff_properties["continuity_basis"]["enum"],
            ["USER_EXPLICIT_SAME_DELIVERY"],
        )
        self.assertIn(
            ".layered-delivery/<delivery-id>/handoff-<fingerprint>.md",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertIn(
            "same overview, baseline, progress, acceptance, revisions, "
            "and work-items projections",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertIn(
            "shared scheduler.db",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertIn(
            "root overview.md",
            by_name["create_manual_handoff"]["description"],
        )
        self.assertNotIn("_meta", by_name["create_manual_handoff"])
        self.assertNotIn("recommend_executors", by_name)
        dispatch_plan_schema = by_name[
            "plan_dispatch_batch"
        ]["inputSchema"]
        self.assertEqual(
            dispatch_plan_schema["required"],
            [
                "root_id",
                "expected_graph_fingerprint",
            ],
        )
        self.assertEqual(
            set(dispatch_plan_schema["properties"]),
            {
                "root_id",
                "expected_graph_fingerprint",
            },
        )
        self.assertNotIn("_meta", by_name["plan_dispatch_batch"])
        dispatch_schema = by_name["dispatch_loop"]["inputSchema"]
        self.assertEqual(
            dispatch_schema["required"],
            [
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "dispatch_mode",
                "operation_id",
            ],
        )
        self.assertIn(
            "must omit",
            dispatch_schema["properties"]["receiver_context_id"][
                "description"
            ],
        )
        self.assertIn(
            "never invent",
            dispatch_schema["properties"]["receiver_attestation_id"][
                "description"
            ],
        )
        self.assertNotIn(
            "actual_model_id",
            dispatch_schema["required"],
        )
        self.assertIn(
            "never routes",
            dispatch_schema["properties"]["actual_model_id"][
                "description"
            ],
        )
        self.assertEqual(
            set(dispatch_schema["properties"]),
            {
                "root_id",
                "node_id",
                "owner",
                "agent_id",
                "actual_model_id",
                "dispatch_mode",
                "dispatch_transport",
                "dispatch_reservation_id",
                "dispatch_decision_fingerprint",
                "receiver_context_id",
                "receiver_attestation_id",
                "operation_id",
            },
        )
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
        self.assertNotIn("execution_mode", freeze_schema["properties"])
        self.assertNotIn(
            "execution_mode",
            inspect.signature(freeze_hierarchy).parameters,
        )
        self.assertNotIn(
            "execution_mode",
            inspect.signature(SchedulerRepository.freeze).parameters,
        )
        self.assertIn(
            "expected_delivery_revision",
            freeze_schema["required"],
        )
        self.assertIn(
            "authorized_project_ids",
            freeze_schema["required"],
        )
        self.assertTrue(
            freeze_schema["properties"]["authorized_project_ids"][
                "uniqueItems"
            ]
        )
        revision_prepare = by_name["prepare_delivery_revision"]
        self.assertNotIn("_meta", revision_prepare)
        self.assertIn(
            "hierarchy",
            revision_prepare["inputSchema"]["required"],
        )
        self.assertEqual(
            by_name["delivery_revision_history"]["inputSchema"][
                "required"
            ],
            ["root_id"],
        )
        final_confirmation = by_name["record_user_confirmation"][
            "inputSchema"
        ]["properties"]["confirmed"]
        self.assertNotIn(
            "_meta",
            by_name["record_user_confirmation"],
        )
        self.assertEqual(final_confirmation["type"], "boolean")
        self.assertIs(final_confirmation["const"], True)

        unfreeze = by_name["unfreeze_task_requirement"]["inputSchema"]
        self.assertEqual(
            unfreeze["required"],
            [
                "root_id",
                "task_id",
                "expected_revision",
                "authorized_by",
                "reason",
            ],
        )
        refreeze = by_name["refreeze_task_requirement"]["inputSchema"]
        self.assertEqual(
            refreeze["required"],
            [
                "root_id",
                "task_id",
                "expected_revision",
                "requirement",
                "confirmed_by",
            ],
        )
        self.assertEqual(
            set(refreeze["properties"]["requirement"]["properties"]),
            {"title", "summary", "payload"},
        )

    def test_prepare_hierarchy_exposes_the_complete_v3_input_schema(
        self,
    ) -> None:
        by_name = {
            tool["name"]: tool
            for tool in tool_definitions()
        }
        prepare_schema = by_name["prepare_hierarchy"]["inputSchema"]
        hierarchy_schema = prepare_schema["properties"]["hierarchy"]

        self.assertIs(hierarchy_schema["additionalProperties"], False)
        self.assertEqual(
            set(hierarchy_schema["properties"]),
            {"delivery", "root"},
        )
        self.assertEqual(
            hierarchy_schema["properties"]["root"],
            {
                "oneOf": [
                    {"$ref": "#/$defs/groupRootNode"},
                    {"$ref": "#/$defs/taskRootNode"},
                ]
            },
        )
        self.assertEqual(
            prepare_schema["$defs"],
            hierarchy_contract(root_kind="TASK")["inputSchema"]["$defs"],
        )
        self.assertTrue(
            prepare_schema["$defs"]["loop"]["properties"]["payload"][
                "additionalProperties"
            ]
        )
        project_scopes = hierarchy_schema["properties"]["delivery"][
            "properties"
        ]["projectScopes"]
        self.assertEqual(project_scopes["minItems"], 1)
        self.assertEqual(
            project_scopes["items"]["properties"]["access"]["enum"],
            ["READ_ONLY", "READ_WRITE"],
        )

    def test_prepare_hierarchy_rejects_invalid_schema_before_controller(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["schemaVersion"] = 3
        controller = Mock(spec=LayeredDeliveryController)

        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root="unused",
                controller=controller,
            )

        self.assertEqual(
            caught.exception.code,
            "MCP_TOOL_ARGUMENT_INVALID",
        )
        self.assertEqual(
            caught.exception.details["unknownFields"],
            ["schemaVersion"],
        )
        controller.execute.assert_not_called()

    def test_prepare_hierarchy_preflights_semantic_contract_before_controller(
        self,
    ) -> None:
        hierarchy = group_hierarchy()
        hierarchy["root"]["children"][0]["definition"]["parentId"] = (
            "g-other"
        )
        controller = Mock(spec=LayeredDeliveryController)

        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root="unused",
                controller=controller,
            )

        self.assertEqual(
            caught.exception.code,
            "MCP_TOOL_ARGUMENT_INVALID",
        )
        self.assertEqual(
            caught.exception.details["schemaError"]["code"],
            "WORK_ITEM_PARENT_INVALID",
        )
        controller.execute.assert_not_called()

    def test_prepare_hierarchy_preflight_accepts_both_root_kinds(
        self,
    ) -> None:
        task = task_hierarchy()
        task["root"]["definition"]["execution"]["loop"]["payload"][
            "custom"
        ] = {"nested": ["opaque", 1, True]}

        self.assertEqual(
            validate_tool_arguments(
                "prepare_hierarchy",
                {"hierarchy": task},
            ),
            {"hierarchy": task},
        )
        self.assertEqual(
            validate_tool_arguments(
                "prepare_hierarchy",
                {"hierarchy": group_hierarchy()},
            ),
            {"hierarchy": group_hierarchy()},
        )

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

    def test_dispatch_requires_bounded_actual_executor_metadata(self) -> None:
        dispatch_schema = {
            tool["name"]: tool
            for tool in tool_definitions()
        }["dispatch_loop"]["inputSchema"]
        self.assertEqual(
            dispatch_schema["properties"]["dispatch_mode"]["enum"],
            ["AUTO", "MANUAL"],
        )
        base = {
            "root_id": "d-service",
            "node_id": "loop:t-service",
            "owner": "agent-1",
            "agent_id": "codex",
            "actual_model_id": "host-observed-model",
            "dispatch_mode": "AUTO",
            "receiver_context_id": "context-1",
            "receiver_attestation_id": "attestation-1",
            "operation_id": "op-1",
        }
        self.assertEqual(
            validate_tool_arguments("dispatch_loop", base),
            base,
        )
        host_injected = {
            key: value
            for key, value in base.items()
            if key not in {
                "receiver_context_id",
                "receiver_attestation_id",
            }
        }
        self.assertEqual(
            validate_tool_arguments("dispatch_loop", host_injected),
            host_injected,
        )
        without_model = {
            key: value
            for key, value in base.items()
            if key != "actual_model_id"
        }
        self.assertEqual(
            validate_tool_arguments("dispatch_loop", without_model),
            without_model,
        )
        for invalid in (
            {key: value for key, value in base.items() if key != "agent_id"},
            {**base, "agent_id": "x" * 257},
            {**base, "actual_model_id": "x" * 257},
            {**base, "model_id": "gpt-5.6-sol"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(GatedLoopError) as caught:
                    validate_tool_arguments("dispatch_loop", invalid)
                self.assertEqual(
                    caught.exception.code,
                    "MCP_TOOL_ARGUMENT_INVALID",
                )

    def test_dispatch_owner_exposes_portable_identity_contract(self) -> None:
        dispatch = {
            tool["name"]: tool["inputSchema"]
            for tool in tool_definitions()
        }["dispatch_loop"]
        owner = dispatch["properties"]["owner"]
        self.assertEqual(owner["maxLength"], 192)
        self.assertIn("native agent_id", owner["description"])
        self.assertIsNotNone(re.fullmatch(owner["pattern"], "claude-code"))
        self.assertIsNone(
            re.fullmatch(owner["pattern"], "claude-code#loop:task")
        )

    def test_freeze_adapter_injects_strict_boolean_confirmation(
        self,
    ) -> None:
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
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": (
                        prepared["hierarchyFingerprint"]
                    ),
                    "authorized_project_ids": [],
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
        self.assertEqual(frozen["executionMode"], "active")
        self.assertEqual(frozen["confirmedBy"], "human")
        self.assertEqual(status["status"], "ACTIVE")
        self.assertEqual(
            frozen["executionMode"],
            status["executionMode"],
        )

    def test_execution_choice_adapter_applies_both_modes_without_reconfirm(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        for selection, expected_status in (
            ("AUTOMATIC", "ACTIVE"),
            ("MANUAL", "HANDOFF_READY"),
        ):
            with (
                self.subTest(selection=selection),
                TemporaryDirectory() as root,
            ):
                preview = call_tool(
                    "preview_hierarchy",
                    {"hierarchy": hierarchy},
                    root=root,
                )
                selected = call_tool(
                    "select_execution_mode",
                    {
                        "root_id": preview["rootId"],
                        "selection": selection,
                        "expected_hierarchy_fingerprint": (
                            preview["hierarchyFingerprint"]
                        ),
                        "expected_graph_fingerprint": (
                            preview["graphFingerprint"]
                        ),
                        "authorized_project_ids": [],
                        "confirmed_by": "human",
                    },
                    root=root,
                )

                self.assertEqual(selected["status"], expected_status)
                self.assertEqual(selected["selection"], selection)
                if selection == "AUTOMATIC":
                    self.assertTrue(selected["automaticDispatchRequested"])
                else:
                    self.assertIn(
                        selected["manualHandoff"]["receiverPrompt"],
                        Path(
                            root,
                            selected["manualHandoff"]["path"],
                        ).read_text(encoding="utf-8"),
                    )

    def test_repeated_automatic_choice_keeps_workspace_isolation(self) -> None:
        hierarchy = task_hierarchy()
        with TemporaryDirectory() as root:
            workspace_a = Path(root, "workspace-a")
            workspace_b = Path(root, "workspace-b")
            workspace_a.mkdir()
            workspace_b.mkdir()
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=root,
            )
            arguments = {
                "root_id": preview["rootId"],
                "selection": "AUTOMATIC",
                "expected_hierarchy_fingerprint": (
                    preview["hierarchyFingerprint"]
                ),
                "expected_graph_fingerprint": preview["graphFingerprint"],
                "authorized_project_ids": [],
                "confirmed_by": "human",
            }
            call_tool(
                "select_execution_mode",
                arguments,
                root=root,
                workspace_root=str(workspace_a),
            )

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "select_execution_mode",
                    arguments,
                    root=root,
                    workspace_root=str(workspace_b),
                )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
        )

    def test_handoff_adapter_injects_strict_boolean_confirmation(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        with TemporaryDirectory() as root:
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=root,
            )
            handoff = call_tool(
                "create_manual_handoff",
                {
                    "hierarchy": hierarchy,
                    "expected_hierarchy_fingerprint": (
                        preview["hierarchyFingerprint"]
                    ),
                    "expected_graph_fingerprint": (
                        preview["graphFingerprint"]
                    ),
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=root,
            )

            control_root = Path(root, ".layered-delivery")
            self.assertTrue(Path(control_root, "scheduler.db").is_file())
            self.assertTrue(Path(control_root, "overview.md").is_file())
            generated_files = {
                path.relative_to(control_root).as_posix()
                for path in control_root.rglob("*")
                if path.is_file() and path.name != ".scheduler.lock"
            }
            expected_files = {
                "scheduler.db",
                "overview.md",
                Path(handoff["manualHandoff"]["path"])
                .relative_to(".layered-delivery")
                .as_posix(),
                "d-service/overview.md",
                "d-service/baseline.md",
                "d-service/progress.md",
                "d-service/acceptance.md",
                "d-service/revisions.md",
                "d-service/work-items/t-service/baseline.md",
                "d-service/work-items/t-service/progress.md",
                "d-service/work-items/t-service/acceptance.md",
            }
            self.assertEqual(generated_files, expected_files)
            self.assertTrue(handoff["controlStateCreated"])
            self.assertEqual(
                handoff["humanArtifacts"]["workspaceOverview"],
                ".layered-delivery/overview.md",
            )
            self.assertEqual(
                handoff["nextAction"],
                "OPEN_FROZEN_BUNDLE_AND_START_MANUAL_HANDOFF_IN_RECEIVING_CLI",
            )
            status = call_tool(
                "workspace_status",
                {"root_id": handoff["rootId"]},
                root=root,
            )
            self.assertEqual(status["status"], "HANDOFF_READY")
            self.assertEqual(status["rootId"], handoff["rootId"])
            self.assertEqual(
                status["workspaceIsolation"]["mode"],
                "UNBOUND_MANUAL_HANDOFF",
            )
            started = call_tool(
                "start_manual_handoff",
                {
                    "root_id": handoff["rootId"],
                    "expected_hierarchy_fingerprint": handoff[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": handoff[
                        "graphFingerprint"
                    ],
                    "started_by": "manual-orchestrator",
                },
                root=root,
                workspace_root=root,
            )
            frontier = call_tool(
                "graph_frontier",
                {"root_id": handoff["rootId"]},
                root=root,
                workspace_root=root,
            )
            self.assertEqual(started["executionMode"], "manual")
            self.assertTrue(started["graphRunCreated"])
            self.assertEqual(
                [action["action"] for action in frontier["actions"]],
                ["CLAIM_MANUAL_TASK"],
            )

        self.assertEqual(handoff["status"], "HANDOFF_READY")
        self.assertEqual(handoff["requirementSnapshotStatus"], "FROZEN")
        self.assertEqual(handoff["confirmedBy"], "human")

    def test_distinct_conversation_workspaces_run_distinct_deliveries(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            first_workspace = Path(root, "worktree-first")
            second_workspace = Path(root, "worktree-second")
            first_workspace.mkdir()
            second_workspace.mkdir()
            first = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-first",
                        "t-first",
                    )
                },
                root=root,
                workspace_root=str(first_workspace),
            )
            second = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-second",
                        "t-second",
                    )
                },
                root=root,
                workspace_root=str(second_workspace),
            )
            for prepared, workspace in (
                (first, first_workspace),
                (second, second_workspace),
            ):
                frozen = call_tool(
                    "freeze_hierarchy",
                    {
                        "root_id": prepared["rootId"],
                        "expected_delivery_revision": 1,
                        "expected_hierarchy_fingerprint": (
                            prepared["hierarchyFingerprint"]
                        ),
                        "authorized_project_ids": [],
                        "confirmed_by": "human",
                    },
                    root=root,
                    workspace_root=str(workspace),
                )
                self.assertEqual(frozen["status"], "ACTIVE")
                status = call_tool(
                    "workspace_status",
                    {},
                    root=root,
                    workspace_root=str(workspace),
                )
                self.assertEqual(status["rootId"], prepared["rootId"])
                self.assertEqual(status["status"], "ACTIVE")
                selected = call_tool(
                    "workspace_status",
                    {"root_id": prepared["rootId"]},
                    root=root,
                    workspace_root=str(workspace),
                )
                self.assertEqual(selected["rootId"], prepared["rootId"])

            self.assertNotEqual(
                first["workspaceIsolation"]["workspaceKey"],
                second["workspaceIsolation"]["workspaceKey"],
            )
            self.assertEqual(
                SchedulerRepository(root).run("d-first")["status"],
                "ACTIVE",
            )
            self.assertEqual(
                SchedulerRepository(root).run("d-second")["status"],
                "ACTIVE",
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "graph_status",
                    {"root_id": "d-first"},
                    root=root,
                    workspace_root=str(second_workspace),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_DELIVERY_WORKSPACE_MISMATCH",
            )

    def test_active_workspace_rejects_preparing_another_delivery(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            workspace = Path(root, "one-worktree")
            workspace.mkdir()
            first = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-first",
                        "t-first",
                    )
                },
                root=root,
                workspace_root=str(workspace),
            )
            call_tool(
                "freeze_hierarchy",
                {
                    "root_id": first["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": (
                        first["hierarchyFingerprint"]
                    ),
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=root,
                workspace_root=str(workspace),
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {
                        "hierarchy": isolated_task_hierarchy(
                            "d-second",
                            "t-second",
                        )
                    },
                    root=root,
                    workspace_root=str(workspace),
                )
            active_status = call_tool(
                "workspace_status",
                {},
                root=root,
                workspace_root=str(workspace),
            )
            self.assertEqual(active_status["rootId"], "d-first")
            self.assertEqual(active_status["status"], "ACTIVE")
            absent_status = SchedulerRepository(root).workspace_status(
                root_id="d-second",
                workspace_root=str(workspace),
            )
            self.assertEqual(absent_status["status"], "ABSENT")
            self.assertFalse(
                (
                    Path(root)
                    / ".layered-delivery"
                    / "d-second"
                ).exists()
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_DELIVERY_WORKSPACE_OCCUPIED",
        )
        self.assertEqual(
            caught.exception.details["occupiedRootId"],
            "d-first",
        )
        self.assertEqual(
            caught.exception.details["nextAction"],
            "CREATE_INDEPENDENT_WORKTREE_TASK",
        )
        self.assertEqual(
            caught.exception.details["worktreeSetup"],
            {
                "owner": "HOST",
                "strategy": "HOST_NATIVE_LINKED_WORKTREE",
                "resumeAction": "CALL_WORKSPACE_STATUS_IN_NEW_WORKTREE",
                "controllerCreatesWorktree": False,
            },
        )

    def test_linked_git_worktrees_share_control_root_but_keep_identity(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository_root = Path(root, "repository")
            linked_root = Path(root, "linked")
            common_git = repository_root / ".git"
            worktree_git = common_git / "worktrees" / "linked"
            worktree_git.mkdir(parents=True)
            linked_root.mkdir()
            (linked_root / ".git").write_text(
                f"gitdir: {worktree_git}\n",
                encoding="utf-8",
            )
            (worktree_git / "commondir").write_text(
                "../..\n",
                encoding="utf-8",
            )
            binding = ProjectRootBinding.from_startup(
                None,
                from_sandbox_meta=True,
            )
            resolved = binding.resolve_request(
                {
                    "codex/sandbox-state-meta": {
                        "sandboxCwd": linked_root.as_uri(),
                    }
                },
                stateless=True,
            )
        self.assertEqual(
            resolved.project_root,
            str(repository_root.resolve()),
        )
        self.assertEqual(
            resolved.workspace_root,
            str(linked_root.resolve()),
        )

    def test_detached_worktree_requests_host_feature_branch_setup(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, _worktree, base_commit, _branch_ref = (
                git_delivery_checkout(root)
            )
            detached = Path(root, "worktrees", "codex-detached")
            git_command(
                repository,
                "worktree",
                "add",
                "--detach",
                str(detached),
                "main",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(detached),
                trusted_host_adapter="codex",
            )

            self.assertEqual(discovered["status"], "ABSENT")
            self.assertEqual(
                discovered["gitWorkspace"],
                {
                    "role": "DETACHED_WORKTREE",
                    "headCommit": base_commit,
                },
            )
            self.assertEqual(
                discovered["worktreeSetup"],
                {
                    "state": "FEATURE_BRANCH_REQUIRED",
                    "owner": "HOST",
                    "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["worktreeProvenance"],
                {
                    "strategy": "HOST_NATIVE_LINKED_WORKTREE",
                    "hostAdapterId": "codex",
                    "workspaceRoot": str(detached.resolve()),
                    "topology": "LINKED_WORKTREE",
                    "selectionSource": "LOCAL_MAIN_FALLBACK",
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "baseHeadCommit": base_commit,
                    "integrationTarget": "main",
                },
            )

            branch_ref = "feature/d-codex-worktree"
            git_command(detached, "switch", "-c", branch_ref)
            ready = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(detached),
                trusted_host_adapter="codex",
            )
            self.assertEqual(
                ready["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )

    def test_detached_primary_checkout_still_requires_linked_worktree(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, _worktree, base_commit, _branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(repository, "checkout", "--detach", base_commit)

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            self.assertEqual(discovered["status"], "ABSENT")
            self.assertEqual(
                discovered["worktreeSetup"]["state"],
                "DEDICATED_WORKTREE_REQUIRED",
            )
            self.assertEqual(
                discovered["worktreeSetup"]["nextAction"],
                "CREATE_INDEPENDENT_WORKTREE_TASK",
            )
            self.assertEqual(
                discovered["worktreeProvenance"]["topology"],
                "PRIMARY_WORKTREE",
            )
            self.assertNotIn("suggestedGitBinding", discovered)

    def test_automatic_choice_moves_to_linked_worktree_without_reconfirm(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-auto-transition",
                )
            )
            secondary_container = Path(root, "secondary")
            secondary_container.mkdir()
            (
                secondary_repository,
                secondary_worktree,
                secondary_base_commit,
                secondary_branch_ref,
            ) = git_delivery_checkout(
                str(secondary_container),
                delivery_id="d-auto-transition",
            )
            self.assertEqual(secondary_branch_ref, branch_ref)
            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            hierarchy["delivery"]["id"] = "d-auto-transition"
            hierarchy["delivery"]["projectScopes"] = [
                {
                    "id": "erp-protein",
                    "workspaceRoot": str(repository.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": deepcopy(
                        hierarchy["delivery"]["gitBinding"]
                    ),
                },
                {
                    "id": "erp-pm",
                    "workspaceRoot": str(secondary_repository.resolve()),
                    "access": "READ_WRITE",
                    "gitBinding": {
                        "branchRef": secondary_branch_ref,
                        "baseRef": "main",
                        "baseCommit": secondary_base_commit,
                        "integrationTarget": "main",
                    },
                },
            ]
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )

            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-auto-transition",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": ["erp-pm", "erp-protein"],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertEqual(selected["selection"], "AUTOMATIC")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["nextAction"],
                "CREATE_INDEPENDENT_WORKTREE_TASK",
            )
            self.assertEqual(
                selected["selectionContinuation"],
                {
                    "tool": "resume_execution_mode",
                    "confirmationRequired": False,
                    "selectionPreserved": True,
                },
            )
            self.assertEqual(
                selected["worktreeSetup"]["state"],
                "DEDICATED_WORKTREE_REQUIRED",
            )
            self.assertEqual(
                selected["worktreeSetup"]["resumeAction"],
                "CALL_WORKSPACE_STATUS_THEN_RESUME_EXECUTION_MODE",
            )

            status = call_tool(
                "workspace_status",
                {"root_id": "d-auto-transition"},
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertEqual(
                status["executionSelection"],
                {
                    "selection": "AUTOMATIC",
                    "state": "RECORDED_PENDING_WORKTREE",
                    "confirmationRequired": False,
                    "confirmedBy": "human",
                    "authorizedProjectIds": ["erp-pm", "erp-protein"],
                },
            )
            verified_status = {
                item["id"]: item
                for item in status["verifiedProjectScopes"]
            }
            self.assertEqual(
                verified_status["erp-protein"]["workspaceRoot"],
                str(worktree.resolve()),
            )
            self.assertEqual(
                verified_status["erp-protein"]["declaredWorkspaceRoot"],
                str(repository.resolve()),
            )
            self.assertEqual(
                verified_status["erp-pm"]["workspaceRoot"],
                str(secondary_worktree.resolve()),
            )
            self.assertEqual(
                verified_status["erp-pm"]["declaredWorkspaceRoot"],
                str(secondary_repository.resolve()),
            )

            resumed = call_tool(
                "resume_execution_mode",
                {
                    "root_id": "d-auto-transition",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertEqual(resumed["selection"], "AUTOMATIC")
            self.assertTrue(resumed["automaticDispatchRequested"])
            self.assertFalse(resumed["confirmationRequired"])
            self.assertEqual(
                resumed["nextAction"],
                "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
            )
            resumed_projects = {
                item["id"]: item
                for item in resumed["verifiedProjectScopes"]
            }
            self.assertEqual(
                resumed_projects["erp-protein"]["workspaceRoot"],
                str(worktree.resolve()),
            )
            self.assertEqual(
                resumed_projects["erp-pm"]["workspaceRoot"],
                str(secondary_worktree.resolve()),
            )

    def test_clean_host_native_worktree_is_ready_for_branch_adoption(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )

            self.assertEqual(
                discovered["worktreeProvenance"],
                {
                    "strategy": "HOST_NATIVE_LINKED_WORKTREE",
                    "hostAdapterId": "codex",
                    "workspaceRoot": str(worktree.resolve()),
                    "topology": "LINKED_WORKTREE",
                    "selectionSource": "LOCAL_MAIN_FALLBACK",
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "baseHeadCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["workingTree"],
                {
                    "clean": True,
                    "changeCount": 0,
                    "stateFingerprint": discovered["workingTree"][
                        "stateFingerprint"
                    ],
                },
            )
            self.assertEqual(
                len(discovered["workingTree"]["stateFingerprint"]),
                64,
            )
            self.assertEqual(
                discovered["branchAdoption"],
                {
                    "state": "READY",
                    "nextAction": "USE_SUGGESTED_GIT_BINDING",
                    "workingTreeClean": True,
                },
            )
            self.assertEqual(
                discovered["suggestedGitBinding"]["branchRef"],
                branch_ref,
            )

    def test_dirty_host_native_worktree_requires_exact_confirmation(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            Path(worktree, "dirty.txt").write_text(
                "belongs to the proposed delivery\n",
                encoding="utf-8",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )

            dirty_fingerprint = discovered["workingTree"][
                "stateFingerprint"
            ]
            self.assertFalse(discovered["workingTree"]["clean"])
            self.assertEqual(discovered["workingTree"]["changeCount"], 1)
            self.assertNotIn("suggestedGitBinding", discovered)
            self.assertEqual(
                discovered["candidateGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["branchAdoption"],
                {
                    "state": "DIRTY_CONFIRMATION_REQUIRED",
                    "nextAction": (
                        "CONFIRM_CURRENT_DIFF_BELONGS_TO_DELIVERY"
                    ),
                    "workingTreeClean": False,
                    "dirtyStateFingerprint": dirty_fingerprint,
                },
            )

            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "workspace_status",
                    {"confirmed_dirty_state_fingerprint": "a" * 64},
                    root=str(repository),
                    workspace_root=str(worktree),
                    trusted_host_adapter="codex",
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_DIRTY_STATE_CHANGED",
            )

            confirmed = call_tool(
                "workspace_status",
                {
                    "confirmed_dirty_state_fingerprint": (
                        dirty_fingerprint
                    )
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )

            self.assertEqual(
                confirmed["branchAdoption"]["state"],
                "READY_WITH_CONFIRMED_CHANGES",
            )
            self.assertEqual(
                confirmed["suggestedGitBinding"],
                discovered["candidateGitBinding"],
            )

    def test_branch_checked_out_in_another_worktree_is_not_adopted(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, _first, _base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            second = Path(root, "worktrees", "forced-duplicate")
            git_command(
                repository,
                "worktree",
                "add",
                "--detach",
                str(second),
                "main",
            )
            git_command(
                second,
                "switch",
                "--ignore-other-worktrees",
                branch_ref,
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(second),
                trusted_host_adapter="codex",
            )

            self.assertNotIn("suggestedGitBinding", discovered)
            self.assertEqual(
                discovered["branchAdoption"],
                {
                    "state": "BRANCH_IN_USE_BY_OTHER_WORKTREE",
                    "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                    "workingTreeClean": True,
                    "conflictingWorktreeCount": 2,
                },
            )

    def test_branch_bound_to_another_delivery_is_not_adopted(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, first, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-existing", "t-existing"),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(first),
            )
            call_tool(
                "freeze_hierarchy",
                {
                    "root_id": prepared["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": prepared[
                        "hierarchyFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(first),
            )
            existing = call_tool(
                "workspace_status",
                {"root_id": "d-existing"},
                root=str(repository),
                workspace_root=str(first),
                trusted_host_adapter="codex",
            )
            self.assertEqual(
                existing["worktreeProvenance"],
                {
                    "strategy": "HOST_NATIVE_LINKED_WORKTREE",
                    "hostAdapterId": "codex",
                    "workspaceRoot": str(first.resolve()),
                    "topology": "LINKED_WORKTREE",
                    "selectionSource": "FROZEN_GIT_BINDING",
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "baseHeadCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            git_command(repository, "worktree", "remove", str(first))
            replacement = Path(root, "worktrees", "replacement")
            git_command(
                repository,
                "worktree",
                "add",
                str(replacement),
                branch_ref,
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(replacement),
                trusted_host_adapter="codex",
            )

            self.assertNotIn("suggestedGitBinding", discovered)
            self.assertEqual(
                discovered["branchAdoption"],
                {
                    "state": "BRANCH_BOUND_TO_OTHER_DELIVERY",
                    "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                    "workingTreeClean": True,
                    "conflictingDeliveries": [
                        {"rootId": "d-existing", "status": "ACTIVE"}
                    ],
                },
            )

    def test_branch_used_by_historical_delivery_is_not_adopted(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, first, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-historical", "t-historical"),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(first),
            )
            call_tool(
                "freeze_hierarchy",
                {
                    "root_id": prepared["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": prepared[
                        "hierarchyFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(first),
            )
            call_tool(
                "cancel_graph_run",
                {
                    "root_id": "d-historical",
                    "cancelled_by": "human",
                    "reason": "This Delivery is no longer active.",
                },
                root=str(repository),
                workspace_root=str(first),
            )
            git_command(repository, "worktree", "remove", str(first))
            replacement = Path(root, "worktrees", "historical-replacement")
            git_command(
                repository,
                "worktree",
                "add",
                str(replacement),
                branch_ref,
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(replacement),
                trusted_host_adapter="codex",
            )

            self.assertNotIn("suggestedGitBinding", discovered)
            self.assertEqual(
                discovered["branchAdoption"],
                {
                    "state": "BRANCH_USED_BY_HISTORICAL_DELIVERY",
                    "nextAction": "CREATE_DELIVERY_FEATURE_BRANCH",
                    "workingTreeClean": True,
                    "conflictingDeliveries": [
                        {"rootId": "d-historical", "status": "CANCELLED"}
                    ],
                },
            )

    def test_git_delivery_binding_is_frozen_and_checked_at_runtime(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            Path(repository, "MAINLINE.md").write_text(
                "Mainline advanced after the feature branch was created.\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "MAINLINE.md")
            git_command(
                repository,
                "commit",
                "-m",
                "Advance main after feature fork",
            )
            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(discovered["status"], "ABSENT")
            self.assertEqual(
                discovered["gitWorkspace"]["branchRef"],
                branch_ref,
            )
            self.assertEqual(
                discovered["suggestedGitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                prepared["gitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            self.assertEqual(
                prepared["gitWorkspace"]["branchRef"],
                branch_ref,
            )
            self.assertEqual(
                prepared["gitWorkspace"]["headCommit"],
                base_commit,
            )
            frozen = call_tool(
                "freeze_hierarchy",
                {
                    "root_id": prepared["rootId"],
                    "expected_delivery_revision": 1,
                    "expected_hierarchy_fingerprint": (
                        prepared["hierarchyFingerprint"]
                    ),
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                frozen["gitBinding"]["branchRef"],
                branch_ref,
            )
            authoritative_run = SchedulerRepository(
                str(repository)
            ).run("d-git")
            self.assertEqual(
                authoritative_run["gitBinding"],
                hierarchy["delivery"]["gitBinding"],
            )
            baseline = Path(
                repository,
                ".layered-delivery",
                "d-git",
                "baseline.md",
            ).read_text(encoding="utf-8")
            self.assertIn("## Git 分支绑定", baseline)
            self.assertIn(
                f"Delivery feature 分支：{branch_ref}",
                baseline,
            )
            self.assertIn(
                f"创建基线提交：{base_commit}",
                baseline,
            )
            self.assertIn("最终集成目标：main", baseline)
            self.assertIn(
                f"main@{base_commit} → {branch_ref} → main",
                baseline,
            )

            git_command(
                worktree,
                "switch",
                "-c",
                "feature/wrong-delivery",
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "graph_status",
                    {"root_id": "d-git"},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )
            git_command(worktree, "switch", branch_ref)
            resumed = call_tool(
                "graph_status",
                {"root_id": "d-git"},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(resumed["status"], "ACTIVE")
            self.assertEqual(
                resumed["gitWorkspace"]["branchRef"],
                branch_ref,
            )

    def test_git_delivery_requires_binding_and_valid_common_base(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {
                        "hierarchy": isolated_task_hierarchy(
                            "d-git",
                            "t-git",
                        )
                    },
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BINDING_REQUIRED",
            )

            wrong_branch = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref="feature/another-delivery",
                base_commit=base_commit,
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": wrong_branch},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BRANCH_MISMATCH",
            )

            missing_base = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit="f" * 40,
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": missing_base},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BASE_INVALID",
            )

            Path(repository, "MAINLINE.md").write_text(
                "This commit is not part of the feature branch.\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "MAINLINE.md")
            git_command(repository, "commit", "-m", "Advance mainline")
            non_ancestor = git_command(
                repository,
                "rev-parse",
                "HEAD",
            )
            wrong_base = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit=non_ancestor,
            )
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": wrong_base},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
            self.assertEqual(
                caught.exception.code,
                "SCHEDULER_GIT_BASE_INVALID",
            )

    def test_two_git_deliveries_run_in_separate_feature_worktrees(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, first_worktree, base_commit, first_branch = (
                git_delivery_checkout(root, delivery_id="d-first")
            )
            second_worktree = Path(root, "worktrees", "d-second")
            second_branch = "feature/d-second"
            git_command(
                repository,
                "worktree",
                "add",
                "-b",
                second_branch,
                str(second_worktree),
                "main",
            )
            deliveries = (
                (
                    "d-first",
                    "t-first",
                    first_branch,
                    first_worktree,
                ),
                (
                    "d-second",
                    "t-second",
                    second_branch,
                    second_worktree,
                ),
            )
            for delivery_id, task_id, branch_ref, worktree in deliveries:
                hierarchy = bind_delivery_to_git(
                    isolated_task_hierarchy(
                        delivery_id,
                        task_id,
                        claims=[f"project:test/module:{delivery_id}"],
                    ),
                    branch_ref=branch_ref,
                    base_commit=base_commit,
                )
                prepared = call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": hierarchy},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
                freeze_hierarchy(
                    root=str(repository),
                    root_id=delivery_id,
                    expected_delivery_revision=1,
                    expected_hierarchy_fingerprint=(
                        prepared["hierarchyFingerprint"]
                    ),
                    authorized_project_ids=[],
                    confirmed=True,
                    confirmed_by="human",
                )
                Path(worktree, f"{delivery_id}.txt").write_text(
                    f"{delivery_id} implementation\n",
                    encoding="utf-8",
                )
                git_command(worktree, "add", f"{delivery_id}.txt")
                git_command(
                    worktree,
                    "commit",
                    "-m",
                    f"Implement {delivery_id}",
                )

            for delivery_id, task_id, branch_ref, worktree in deliveries:
                reservation = reserve_loop(
                    root=str(repository),
                    root_id=delivery_id,
                    node_id=f"loop:{task_id}",
                )
                attestation = attest_loop_receiver(
                    root=str(repository),
                    root_id=delivery_id,
                    node_id=f"loop:{task_id}",
                    receiver_context_id=f"context-{delivery_id}",
                    parent_context_id="codex-parent",
                    host_adapter_id="codex",
                    dispatch_reservation_id=reservation[
                        "dispatchReservationId"
                    ],
                )
                dispatched = call_tool(
                    "dispatch_loop",
                    {
                        "root_id": delivery_id,
                        "node_id": f"loop:{task_id}",
                        "owner": f"agent-{delivery_id}",
                        "agent_id": reservation["agentId"],
                        "dispatch_mode": reservation["dispatchMode"],
                        "dispatch_transport": reservation[
                            "dispatchTransport"
                        ],
                        "dispatch_reservation_id": reservation[
                            "dispatchReservationId"
                        ],
                        "dispatch_decision_fingerprint": reservation[
                            "dispatchDecisionFingerprint"
                        ],
                        "receiver_context_id": (
                            f"context-{delivery_id}"
                        ),
                        "receiver_attestation_id": attestation[
                            "receiverAttestationId"
                        ],
                        "operation_id": f"op-{delivery_id}",
                    },
                    root=str(repository),
                    workspace_root=str(worktree),
                    trusted_host_adapter="codex",
                )
                self.assertEqual(dispatched["status"], "CLAIMED")
                self.assertEqual(
                    dispatched["gitWorkspace"]["branchRef"],
                    branch_ref,
                )
                self.assertEqual(
                    dispatched["gitWorkspace"]["headCommit"],
                    git_command(worktree, "rev-parse", "HEAD"),
                )

            self.assertEqual(
                SchedulerRepository(str(repository)).run("d-first")[
                    "status"
                ],
                "ACTIVE",
            )
            self.assertEqual(
                SchedulerRepository(str(repository)).run("d-second")[
                    "status"
                ],
                "ACTIVE",
            )

    def test_git_binding_discovery_falls_back_to_master(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root, mainline="master")
            )
            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "master",
                    "baseCommit": base_commit,
                    "integrationTarget": "master",
                },
            )
            self.assertEqual(
                discovered["worktreeProvenance"]["selectionSource"],
                "LOCAL_MASTER_FALLBACK",
            )
            hierarchy = bind_delivery_to_git(
                isolated_task_hierarchy("d-git", "t-git"),
                branch_ref=branch_ref,
                base_commit=base_commit,
                base_ref="master",
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
            )
            self.assertEqual(
                prepared["gitBinding"]["baseRef"],
                "master",
            )

    def test_git_binding_discovery_prefers_valid_origin_head(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(repository, "branch", "release", base_commit)
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
                workspace_root=str(worktree),
            )

            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "release",
                    "baseCommit": base_commit,
                    "integrationTarget": "release",
                },
            )
            self.assertEqual(
                discovered["worktreeProvenance"]["selectionSource"],
                "ORIGIN_HEAD",
            )

    def test_git_binding_discovery_ignores_dangling_origin_head(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(
                repository,
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/missing",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
            )

            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["worktreeProvenance"]["selectionSource"],
                "LOCAL_MAIN_FALLBACK",
            )

    def test_git_binding_discovery_prefers_host_selected_base(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            git_command(repository, "branch", "release", base_commit)
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
                {"base_ref": "main"},
                root=str(repository),
                workspace_root=str(worktree),
            )

            self.assertEqual(
                discovered["suggestedGitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                discovered["worktreeProvenance"]["selectionSource"],
                "HOST_SELECTED",
            )

    def test_host_selected_base_prefers_origin_tracking_ref(self) -> None:
        with TemporaryDirectory() as root:
            repository, _, base_commit, _ = git_delivery_checkout(root)
            git_command(repository, "branch", "release", base_commit)
            tree = git_command(repository, "rev-parse", "HEAD^{tree}")
            remote_commit = git_command(
                repository,
                "commit-tree",
                tree,
                "-p",
                base_commit,
                "-m",
                "Remote release head",
            )
            git_command(
                repository,
                "update-ref",
                "refs/remotes/origin/release",
                remote_commit,
            )
            git_command(repository, "switch", "release")

            discovered = call_tool(
                "workspace_status",
                {"base_ref": "release"},
                root=str(repository),
                workspace_root=str(repository),
            )

            self.assertEqual(
                discovered["worktreeSetup"]["baseCommit"],
                remote_commit,
            )

    def test_remote_default_does_not_treat_main_as_feature_branch(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, _, base_commit, _ = git_delivery_checkout(root)
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
                discovered["worktreeSetup"]["baseRef"],
                "release",
            )

    def test_git_file_without_worktree_metadata_keeps_its_own_root(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            workspace = Path(root, "submodule")
            git_dir = Path(root, "parent", ".git", "modules", "submodule")
            workspace.mkdir()
            git_dir.mkdir(parents=True)
            (workspace / ".git").write_text(
                f"gitdir: {git_dir}\n",
                encoding="utf-8",
            )
            binding = ProjectRootBinding.from_startup(
                str(workspace),
            )
            resolved = binding.resolve_request(
                None,
                stateless=True,
            )
        self.assertEqual(
            resolved.project_root,
            str(workspace.resolve()),
        )
        self.assertEqual(
            resolved.workspace_root,
            str(workspace.resolve()),
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
        self.assertEqual(
            caught.exception.details["rootId"],
            prepared["rootId"],
        )

    def test_state_contract_mismatch_is_rejected_before_state_access(
        self,
    ) -> None:
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
                    "UPDATE scheduler_metadata SET value = ? "
                    "WHERE key = 'state_contract'",
                    ("schema-v3-incompatible-generator",),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(GatedLoopError) as caught:
                SchedulerRepository(root).hierarchy(prepared["rootId"])

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_STATE_CONTRACT_MISMATCH",
        )
        self.assertEqual(
            caught.exception.details["actualStateContract"],
            "schema-v3-incompatible-generator",
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
                        "receiver_attestation_id": "attestation-integrity",
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
                "Layered Delivery never recommends or selects a "
                "development model",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "plan_dispatch_batch atomically reserves each selected Loop",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "A new user requirement defaults to a new Delivery",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "Codex project task with environment=worktree",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "Claude must start a new worktree session",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "must not merely EnterWorktree in the old Claude session",
                initialized["result"]["instructions"],
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
            self.assertIn(
                "HOST_NATIVE_LINKED_WORKTREE",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "worktreeProvenance",
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
                "Each TASK may stage and commit only its own changes on that "
                "Delivery branch",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "must never use the 90-second first-heartbeat warning as a "
                "sleep or polling interval",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "A native child completion notification interrupts any wait "
                "and triggers graph_frontier immediately",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "code inspection, root-cause confirmation, edit completion, "
                "test start and completion, rework, Review, and final "
                "verification",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "LIGHT Loops report findings and final verification",
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
            self.assertEqual(len(listed["result"]["tools"]), 28)
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

    def test_worker_telemetry_is_display_only_phase_reporting(self) -> None:
        tools = {tool["name"]: tool for tool in tool_definitions()}
        telemetry = tools["record_loop_result"]["inputSchema"][
            "properties"
        ]["outcome"]["properties"]["result"]["properties"][
            "workerTelemetry"
        ]["items"]
        self.assertEqual(
            telemetry["required"],
            ["phase", "agent", "model", "reasoningEffort"],
        )
        self.assertEqual(
            validate_tool_arguments(
                "record_loop_result",
                {
                    "root_id": "d-service",
                    "node_id": "loop:t-service",
                    "outcome": {
                        "status": "SUCCEEDED",
                        "summary": "Loop completed.",
                        "result": {
                            "workerTelemetry": [
                                {
                                    "phase": "review",
                                    "agent": "unreported",
                                    "model": "unreported",
                                    "reasoningEffort": "unreported",
                                    "displayOnly": True,
                                    "nonAuthoritative": True,
                                }
                            ]
                        },
                    },
                },
            )["outcome"]["result"]["workerTelemetry"][0]["model"],
            "unreported",
        )


if __name__ == "__main__":
    unittest.main()
