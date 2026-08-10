from __future__ import annotations

from contextlib import redirect_stderr
from copy import deepcopy
import io
import inspect
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hdg import mcp_server
from hdg.controller import (
    ControllerContext,
    LayeredDeliveryController,
)
from hdg.errors import GatedLoopError
from hdg.graph_model import loop_node_id
from hdg.git_binding import inspect_frozen_git_workspace_provenance
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
from hdg.planning import (
    _assert_automatic_git_branch_available,
    freeze_hierarchy,
    prepare_hierarchy,
)
from hdg.repository import (
    SCHEDULER_STATE_CONTRACT,
    SchedulerRepository,
)

from .test_loop_architecture import (
    group_hierarchy,
    loop_descriptor,
    task_hierarchy,
)
from .test_scheduler_runtime import at, database_hierarchy
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


class DevelopmentBaselineTests(unittest.TestCase):
    def test_clean_primary_feature_recommends_stacked_child_branch(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            repository.mkdir()
            git_command(repository, "init", "--initial-branch=main")
            git_command(repository, "config", "user.name", "Scheduler Tests")
            git_command(
                repository,
                "config",
                "user.email",
                "scheduler-tests@example.invalid",
            )
            Path(repository, "README.md").write_text(
                "# stacked delivery fixture\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Initial main baseline")
            parent_branch = "feature/m_lf_protein"
            child_branch = "feature/m_lf_mprotein_409"
            git_command(repository, "switch", "-c", parent_branch)
            Path(repository, "parent.txt").write_text(
                "parent feature content\n",
                encoding="utf-8",
            )
            git_command(repository, "add", "parent.txt")
            git_command(repository, "commit", "-m", "Parent feature baseline")
            parent_head = git_command(repository, "rev-parse", "HEAD")

            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-stacked-child",
                        "t-stacked-child",
                    )
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            interaction = preview["pendingInteraction"]
            stacked = next(
                option
                for option in interaction["options"]
                if option["id"] == "NEW_FROM_CURRENT_BRANCH"
            )
            self.assertEqual(
                interaction["defaultOptionId"],
                "NEW_FROM_CURRENT_BRANCH",
            )
            self.assertEqual(
                interaction["recommendedOptionId"],
                "NEW_FROM_CURRENT_BRANCH",
            )
            self.assertTrue(stacked["stackedDelivery"])
            self.assertEqual(stacked["baseRef"], parent_branch)
            self.assertEqual(stacked["baseCommit"], parent_head)
            self.assertEqual(stacked["integrationTarget"], parent_branch)
            self.assertIn(
                "创建子分支（默认、推荐）",
                interaction["markdown"],
            )

            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-stacked-child",
                    "selection": "NEW_FROM_CURRENT_BRANCH",
                    "branch_name": child_branch,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "expected_delivery_revision": 1,
                    "baseline_context_fingerprint": interaction[
                        "baselineContextFingerprint"
                    ],
                    "confirmed_by": "李锋",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            preference = confirmed["developmentBaselineConfirmed"]
            self.assertEqual(preference["branchRef"], child_branch)
            self.assertEqual(preference["baseRef"], parent_branch)
            self.assertEqual(preference["baseCommit"], parent_head)
            self.assertEqual(preference["integrationTarget"], parent_branch)
            self.assertEqual(preference["source"], "NEW_FROM_CURRENT_BRANCH")
            self.assertEqual(
                confirmed["executionChoice"]["baseRef"],
                parent_branch,
            )
            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-stacked-child",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": confirmed[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": confirmed[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "李锋",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]
            self.assertEqual(dispatch["branchRef"], child_branch)
            self.assertEqual(dispatch["baseRef"], parent_branch)
            self.assertEqual(dispatch["baseCommit"], parent_head)
            self.assertEqual(dispatch["integrationTarget"], parent_branch)
            self.assertEqual(
                git_command(repository, "branch", "--list", child_branch),
                "",
            )

    def test_dirty_primary_feature_does_not_offer_stacked_child(self) -> None:
        with TemporaryDirectory() as root:
            repository = Path(root, "repository")
            repository.mkdir()
            git_command(repository, "init", "--initial-branch=main")
            git_command(repository, "config", "user.name", "Scheduler Tests")
            git_command(
                repository,
                "config",
                "user.email",
                "scheduler-tests@example.invalid",
            )
            Path(repository, "README.md").write_text("base\n", encoding="utf-8")
            git_command(repository, "add", "README.md")
            git_command(repository, "commit", "-m", "Initial baseline")
            git_command(repository, "switch", "-c", "feature/parent")
            Path(repository, "dirty.txt").write_text("dirty\n", encoding="utf-8")

            preview = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-dirty-stacked",
                        "t-dirty-stacked",
                    )
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )
            self.assertNotIn(
                "NEW_FROM_CURRENT_BRANCH",
                {
                    option["id"]
                    for option in preview["pendingInteraction"]["options"]
                },
            )

    def test_clean_worktree_without_binding_prompts_then_confirms_and_remembers(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, branch_ref = (
                git_delivery_checkout(root, delivery_id="d-baseline")
            )
            hierarchy = isolated_task_hierarchy("d-baseline", "t-baseline")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertEqual(
                preview["nextAction"],
                "PRESENT_HOST_NATIVE_BASELINE_CHOICE",
            )
            self.assertIn("developmentBaseline", preview)
            self.assertNotIn("executionChoice", preview)
            option_ids = [
                option["id"]
                for option in preview["developmentBaseline"]["options"]
            ]
            self.assertIn(branch_ref, option_ids)
            self.assertIn("NEW_FROM_MAINLINE", option_ids)
            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-baseline",
                    "selection": branch_ref,
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_by": "李锋",
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertIn("executionChoice", confirmed)
            self.assertEqual(
                confirmed["developmentBaselineConfirmed"]["branchRef"],
                branch_ref,
            )
            # Regression for the master-default bug: executionChoice now carries
            # the confirmed mainline base instead of None.
            self.assertEqual(
                confirmed["executionChoice"]["baseRef"], "main"
            )
            self.assertNotEqual(
                confirmed["hierarchyFingerprint"],
                preview["hierarchyFingerprint"],
            )
            remembered = call_tool(
                "preview_hierarchy",
                {
                    "hierarchy": isolated_task_hierarchy(
                        "d-baseline", "t-baseline"
                    )
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertIn("executionChoice", remembered)
            self.assertNotIn("developmentBaseline", remembered)

    def test_dirty_worktree_requires_attributed_baseline(self) -> None:
        with TemporaryDirectory() as root:
            _repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root, delivery_id="d-dirty")
            )
            Path(worktree, "uncommitted.txt").write_text(
                "pending change\n", encoding="utf-8"
            )
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": isolated_task_hierarchy("d-dirty", "t-dirty")},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertEqual(
                preview["pendingInteraction"]["kind"],
                "DEVELOPMENT_BASELINE",
            )
            self.assertTrue(
                preview["developmentBaseline"][
                    "dirtyStateConfirmationRequired"
                ]
            )
            self.assertNotIn("executionChoice", preview)

    def test_new_from_mainline_pins_mainline_head(self) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root, delivery_id="d-new")
            )
            mainline_head = git_command(repository, "rev-parse", "main")
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": isolated_task_hierarchy("d-new", "t-new")},
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            self.assertIn("developmentBaseline", preview)
            confirmed = call_tool(
                "confirm_development_baseline",
                {
                    "root_id": "d-new",
                    "selection": "NEW_FROM_MAINLINE",
                    "branch_name": "feature/new-baseline",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "confirmed_by": "李锋",
                },
                root=str(worktree),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            preference = confirmed["developmentBaselineConfirmed"]
            self.assertEqual(preference["branchRef"], "feature/new-baseline")
            self.assertEqual(preference["baseRef"], "main")
            self.assertEqual(preference["baseCommit"], mainline_head)
            self.assertEqual(preference["source"], "NEW_FROM_MAINLINE")


class HierarchyContractTests(unittest.TestCase):
    def test_database_changes_are_frozen_before_execution(self) -> None:
        hierarchy = database_hierarchy()
        normalized = validate_hierarchy_definition(hierarchy)
        contract = hierarchy_contract(root_kind="TASK")
        guidance = contract["projectionGuidance"]["databaseChanges"]

        self.assertEqual(
            guidance["requiredBeforePreviewWhen"],
            "FEATURE_ADDS_MODIFIES_OR_DELETES_TABLE_SCHEMA",
        )
        self.assertEqual(
            guidance["executionRole"],
            "APPLY_FROZEN_DATABASE_CONTRACT_ONLY",
        )
        self.assertEqual(guidance["assuranceProfile"], "STANDARD")
        self.assertEqual(
            guidance["fieldProjection"]["documents"],
            {
                "index": "database-changes.md",
                "detailsDirectory": "database-changes/",
                "oneDocumentPerTable": True,
            },
        )
        self.assertEqual(
            normalized["root"]["definition"]["execution"]["loop"][
                "payload"
            ]["databaseChanges"][0]["table"],
            "orders",
        )

    def test_database_change_rejects_incomplete_or_unlocked_design(self) -> None:
        missing_before = database_hierarchy()
        missing_before["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["databaseChanges"][0]["before"] = None
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(missing_before)
        self.assertEqual(
            caught.exception.code,
            "DATABASE_CHANGE_CONTRACT_INVALID",
        )

        unlocked = database_hierarchy()
        unlocked["root"]["definition"]["execution"]["loop"][
            "resourceClaims"
        ] = []
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(unlocked)
        self.assertEqual(
            caught.exception.code,
            "DATABASE_CHANGE_CONTRACT_INVALID",
        )

    def test_database_change_rejects_light_assurance(self) -> None:
        hierarchy = database_hierarchy()
        hierarchy["delivery"]["assuranceProfile"] = "LIGHT"
        hierarchy["delivery"]["assuranceRationale"] = "局部字段变更。"
        hierarchy["delivery"]["reviewLoop"] = None
        hierarchy["root"]["reviewLoop"] = None
        with self.assertRaises(GatedLoopError) as caught:
            validate_hierarchy_definition(hierarchy)
        self.assertEqual(caught.exception.code, "DELIVERY_ASSURANCE_INVALID")
        self.assertIn("STANDARD", caught.exception.message)

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
                "archive_delivery",
                "cancel_graph_run",
                "refreeze_task_requirement",
                "unfreeze_task_requirement",
                "handoff_ready_automatic_task",
            },
        )
        by_name = {tool["name"]: tool for tool in tools}
        archive_tool = by_name["archive_delivery"]
        self.assertEqual(
            archive_tool["inputSchema"]["required"],
            ["root_id"],
        )
        self.assertTrue(archive_tool["annotations"]["destructiveHint"])
        self.assertTrue(archive_tool["annotations"]["idempotentHint"])
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
                "_host_workspace_attestation",
                "_host_receiver_operation_attestation",
                "_host_session_attestation",
                "_host_session_context_id",
            },
        )
        self.assertIn(
            "Host-injected",
            by_name["workspace_status"]["inputSchema"]["properties"][
                "_host_workspace_attestation"
            ]["description"],
        )
        self.assertEqual(
            by_name["workspace_status"]["inputSchema"]["required"],
            [],
        )
        self.assertNotIn("available_agents", by_name)
        preview_schema = by_name["preview_hierarchy"]["inputSchema"]
        self.assertEqual(preview_schema["required"], [])
        self.assertIn("hierarchy", preview_schema["properties"])
        self.assertIn("hierarchy_file", preview_schema["properties"])
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
        manual_handoff_schema = by_name["create_manual_handoff"][
            "inputSchema"
        ]
        self.assertEqual(
            manual_handoff_schema["required"],
            [
                "expected_hierarchy_fingerprint",
                "expected_graph_fingerprint",
                "authorized_project_ids",
                "confirmed_by",
            ],
        )
        self.assertIn("hierarchy", manual_handoff_schema["properties"])
        self.assertIn(
            "hierarchy_file", manual_handoff_schema["properties"]
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
                "_host_workspace_attestation",
                "_host_receiver_operation_attestation",
                "_host_session_attestation",
                "_host_session_context_id",
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
                "_host_workspace_attestation",
                "_host_receiver_operation_attestation",
                "_host_session_attestation",
                "_host_session_context_id",
            },
        )
        recovery = by_name["handoff_ready_automatic_task"]
        self.assertEqual(
            recovery["inputSchema"]["required"],
            [
                "root_id",
                "node_id",
                "expected_graph_fingerprint",
                "handoff_request_id",
                "confirmed_no_code_changes",
                "confirmed_by",
                "reason",
            ],
        )
        self.assertTrue(
            recovery["inputSchema"]["properties"][
                "confirmed_no_code_changes"
            ]["const"]
        )
        self.assertTrue(recovery["annotations"]["idempotentHint"])
        self.assertIn("Review", recovery["description"])
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
        self.assertNotIn(
            "hierarchy",
            revision_prepare["inputSchema"]["required"],
        )
        self.assertIn(
            "hierarchy_file",
            revision_prepare["inputSchema"]["properties"],
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

    def test_claude_cli_primary_dispatches_background_delivery_agent(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-claude-cli",
                )
            )
            mainline = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(mainline["status"], "ABSENT")
            self.assertEqual(
                mainline["worktreeSetup"],
                {
                    "state": "DEDICATED_WORKTREE_REQUIRED",
                    "owner": "HOST",
                    "nextAction": "CREATE_INDEPENDENT_WORKTREE_TASK",
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                mainline["worktreeProvenance"]["strategy"],
                "HOST_NATIVE_LINKED_WORKTREE",
            )

            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            hierarchy["delivery"]["id"] = "d-claude-cli"
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
                    "root_id": "d-claude-cli",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["nextAction"],
                "CREATE_INDEPENDENT_WORKTREE_TASK",
            )
            self.assertEqual(
                selected["worktreeSetup"]["strategy"],
                "HOST_NATIVE_LINKED_WORKTREE",
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]
            self.assertEqual(
                dispatch["hostOperation"],
                "CREATE_CLAUDE_BACKGROUND_DELIVERY_AGENT",
            )
            self.assertEqual(
                dispatch["existingWorktreeRoot"],
                str(worktree.resolve()),
            )
            self.assertFalse(dispatch["requiresNewTopLevelSession"])
            self.assertEqual(dispatch["mainConversationRole"], "MONITOR_ONLY")
            self.assertEqual(
                dispatch["agentDispatch"]["agentType"],
                "delivery-graph:delivery-coordinator",
            )
            self.assertTrue(dispatch["agentDispatch"]["runInBackground"])

            attestation = SchedulerRepository(
                str(repository)
            ).issue_host_workspace_attestation(
                host_adapter_id="claude-code",
                context_id="background-coordinator",
                tool_name="workspace_status",
                tool_use_id="tool-use-workspace-status",
                workspace_root=str(worktree),
            )
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(repository),
                trusted_host_adapter="claude-code",
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "claude-code",
                            "version": "test",
                        },
                    },
                },
                connection=connection,
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                connection=connection,
            )
            attested_response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "workspace-status",
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {
                            "root_id": "d-claude-cli",
                            "_host_workspace_attestation": attestation,
                        },
                    },
                },
                connection=connection,
            )
            self.assertTrue(
                attested_response["result"]["structuredContent"]["ok"]
            )
            ready = attested_response["result"]["structuredContent"][
                "result"
            ]

            self.assertEqual(
                ready["gitBinding"],
                {
                    "branchRef": branch_ref,
                    "baseRef": "main",
                    "baseCommit": base_commit,
                    "integrationTarget": "main",
                },
            )
            self.assertEqual(
                ready["worktreeProvenance"]["strategy"],
                "HOST_NATIVE_LINKED_WORKTREE",
            )
            self.assertEqual(
                ready["worktreeProvenance"]["topology"],
                "LINKED_WORKTREE",
            )
            self.assertTrue(ready["workingTree"]["clean"])
            self.assertEqual(
                ready["executionSelection"]["state"],
                "RECORDED_PENDING_WORKTREE",
            )

            resumed = call_tool(
                "resume_execution_mode",
                {
                    "root_id": "d-claude-cli",
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
            self.assertTrue(resumed["automaticDispatchRequested"])
            self.assertEqual(
                resumed["nextAction"],
                "READ_FRONTIER_AND_AUTOMATICALLY_DISPATCH",
            )
            self.assertNotIn("worktreeSetup", resumed)

            monitored = call_tool(
                "graph_frontier",
                {"root_id": "d-claude-cli"},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="claude-code",
            )
            self.assertEqual(monitored["coordinationRole"], "MONITOR_ONLY")
            self.assertFalse(
                monitored["executionWorkspaceMutationAllowed"]
            )

    def test_host_workspace_attestation_is_tool_bound_and_one_time(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, _base_commit, _branch_ref = (
                git_delivery_checkout(root)
            )
            scheduler = SchedulerRepository(str(repository))
            attestation = scheduler.issue_host_workspace_attestation(
                host_adapter_id="claude-code",
                context_id="agent-1",
                tool_name="workspace_status",
                tool_use_id="tool-use-1",
                workspace_root=str(worktree),
            )

            resolved = scheduler.consume_host_workspace_attestation(
                attestation,
                host_adapter_id="claude-code",
                tool_name="workspace_status",
            )
            self.assertEqual(resolved, str(worktree.resolve()))
            with self.assertRaises(GatedLoopError) as replayed:
                scheduler.consume_host_workspace_attestation(
                    attestation,
                    host_adapter_id="claude-code",
                    tool_name="workspace_status",
                )
            self.assertEqual(
                replayed.exception.code,
                "SCHEDULER_HOST_WORKSPACE_ATTESTATION_CONSUMED",
            )

    def test_codex_plan_consumes_hook_preflight_attestation(self) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            scheduler = SchedulerRepository(root)
            attestation = scheduler.issue_host_workspace_attestation(
                host_adapter_id="codex",
                context_id="codex-coordinator",
                tool_name="plan_dispatch_batch",
                tool_use_id="codex-plan-tool",
                workspace_root=root,
            )
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                trusted_host_adapter="codex",
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "codex",
                            "version": "test",
                        },
                    },
                },
                connection=connection,
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
                    "id": "codex-plan",
                    "method": "tools/call",
                    "params": {
                        "name": "plan_dispatch_batch",
                        "arguments": {
                            "root_id": prepared["rootId"],
                            "expected_graph_fingerprint": prepared[
                                "graphFingerprint"
                            ],
                            "_host_workspace_attestation": attestation,
                        },
                    },
                },
                connection=connection,
            )

        structured = response["result"]["structuredContent"]
        self.assertTrue(structured["ok"])
        self.assertEqual(structured["result"]["assignments"], [])
        self.assertEqual(
            structured["result"]["currentSessionTaskNodeIds"],
            ["loop:t-service"],
        )
        self.assertEqual(
            structured["result"]["nextAction"],
            "CLAIM_CURRENT_TASK",
        )

    def test_codex_session_capability_claims_and_heartbeats_current_task(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=task_hierarchy(),
            )
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
            )
            scheduler = SchedulerRepository(root)
            capability = scheduler.issue_host_workspace_attestation(
                host_adapter_id="codex",
                context_id="codex-current-session",
                tool_name="delivery_session",
                tool_use_id="session:codex-current-session",
                workspace_root=root,
                lifetime_seconds=43_200,
            )
            receiver_capability = (
                scheduler.issue_host_workspace_attestation(
                    host_adapter_id="codex",
                    context_id="codex-review-child",
                    tool_name="receiver_session",
                    tool_use_id="receiver:codex-review-child",
                    workspace_root=root,
                    lifetime_seconds=43_200,
                )
            )
            connection = McpConnection(
                project_root=ProjectRootBinding.from_startup(root),
                trusted_host_adapter="codex",
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": LEGACY_PREFERRED_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {
                            "name": "codex",
                            "version": "test",
                        },
                    },
                },
                connection=connection,
            )
            handle_message(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                connection=connection,
            )
            private_session = {
                "_host_session_attestation": capability,
                "_host_session_context_id": "codex-current-session",
            }
            rejected_receiver_claim = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "codex-receiver-inline-claim",
                    "method": "tools/call",
                    "params": {
                        "name": "claim_current_task",
                        "arguments": {
                            "root_id": prepared["rootId"],
                            "node_id": "loop:t-service",
                            "expected_graph_fingerprint": prepared[
                                "graphFingerprint"
                            ],
                            "_host_session_attestation": (
                                receiver_capability
                            ),
                            "_host_session_context_id": (
                                "codex-review-child"
                            ),
                        },
                    },
                },
                connection=connection,
            )
            plan_response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "codex-inline-plan",
                    "method": "tools/call",
                    "params": {
                        "name": "plan_dispatch_batch",
                        "arguments": {
                            "root_id": prepared["rootId"],
                            "expected_graph_fingerprint": prepared[
                                "graphFingerprint"
                            ],
                            **private_session,
                        },
                    },
                },
                connection=connection,
            )
            claim_response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "codex-inline-claim",
                    "method": "tools/call",
                    "params": {
                        "name": "claim_current_task",
                        "arguments": {
                            "root_id": prepared["rootId"],
                            "node_id": "loop:t-service",
                            "expected_graph_fingerprint": prepared[
                                "graphFingerprint"
                            ],
                            **private_session,
                        },
                    },
                },
                connection=connection,
            )
            heartbeat_response = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": "codex-inline-heartbeat",
                    "method": "tools/call",
                    "params": {
                        "name": "heartbeat_loop",
                        "arguments": {
                            "root_id": prepared["rootId"],
                            "node_id": "loop:t-service",
                            **private_session,
                        },
                    },
                },
                connection=connection,
            )

        rejected = rejected_receiver_claim["result"]["structuredContent"]
        plan = plan_response["result"]["structuredContent"]
        claim = claim_response["result"]["structuredContent"]
        heartbeat = heartbeat_response["result"]["structuredContent"]
        self.assertFalse(rejected["ok"])
        self.assertEqual(
            rejected["error"]["code"],
            "SCHEDULER_HOST_HOOK_NOT_READY",
        )
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["result"]["assignments"], [])
        self.assertEqual(
            plan["result"]["currentSessionTaskNodeIds"],
            ["loop:t-service"],
        )
        self.assertEqual(plan["result"]["nextAction"], "CLAIM_CURRENT_TASK")
        self.assertTrue(claim["ok"])
        self.assertEqual(claim["result"]["dispatchMode"], "INLINE_AUTO")
        self.assertTrue(heartbeat["ok"])
        self.assertEqual(heartbeat["result"]["status"], "CLAIMED")

    def test_missing_receiver_operation_never_becomes_internal_error(
        self,
    ) -> None:
        cases = {
            "heartbeat_loop": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
            },
            "report_loop_progress": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
                "phase": "STARTING",
                "summary_zh": "开始处理",
            },
            "pause_loop": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
            },
            "record_loop_result": {
                "root_id": "d-service",
                "node_id": "loop:t-service",
                "outcome": {
                    "status": "SUCCEEDED",
                    "summary": "done",
                    "result": {},
                },
            },
        }
        with TemporaryDirectory() as root:
            for name, arguments in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(GatedLoopError) as caught:
                        call_tool(
                            name,
                            arguments,
                            root=root,
                            trusted_host_adapter="codex",
                        )
                    self.assertEqual(
                        caught.exception.code,
                        "SCHEDULER_RECEIVER_OPERATION_NOT_ATTESTED",
                    )
                with self.subTest(name=name, forged_operation=True):
                    with self.assertRaises(GatedLoopError) as forged:
                        call_tool(
                            name,
                            {
                                **arguments,
                                "operation_id": "known-but-unattested-op",
                            },
                            root=root,
                            trusted_host_adapter="codex",
                        )
                    self.assertEqual(
                        forged.exception.code,
                        "SCHEDULER_RECEIVER_OPERATION_NOT_ATTESTED",
                    )

    def test_claude_cli_linked_worktree_requires_exact_dirty_confirmation(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, _base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-claude-dirty",
                )
            )
            Path(worktree, "change.txt").write_text(
                "belongs to this Delivery\n",
                encoding="utf-8",
            )

            discovered = call_tool(
                "workspace_status",
                {},
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )
            dirty_fingerprint = discovered["branchAdoption"][
                "dirtyStateFingerprint"
            ]

            self.assertEqual(
                discovered["branchAdoption"]["state"],
                "DIRTY_CONFIRMATION_REQUIRED",
            )
            self.assertEqual(discovered["workingTree"]["changeCount"], 1)
            self.assertNotIn("suggestedGitBinding", discovered)

            confirmed = call_tool(
                "workspace_status",
                {
                    "confirmed_dirty_state_fingerprint": dirty_fingerprint,
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(
                confirmed["branchAdoption"]["state"],
                "READY_WITH_CONFIRMED_CHANGES",
            )
            self.assertEqual(
                confirmed["suggestedGitBinding"]["branchRef"],
                branch_ref,
            )

    def test_codex_automatic_choice_moves_to_worktree_without_reconfirm(
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
                trusted_host_adapter="codex",
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
                trusted_host_adapter="codex",
            )

            self.assertEqual(selected["status"], "CHOICE_READY")
            self.assertEqual(selected["selection"], "AUTOMATIC")
            self.assertTrue(selected["selectionRecorded"])
            self.assertFalse(selected["automaticDispatchRequested"])
            self.assertEqual(
                selected["nextAction"],
                "CREATE_REQUIRED_PROJECT_WORKTREES",
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
                "PROJECT_WORKTREES_REQUIRED",
            )
            self.assertEqual(
                selected["worktreeSetup"]["pendingProjectIds"],
                ["erp-protein"],
            )
            self.assertEqual(
                selected["worktreeSetup"]["readyProjectIds"],
                ["erp-pm"],
            )
            self.assertEqual(
                selected["worktreeSetup"]["resumeAction"],
                "CALL_WORKSPACE_STATUS_THEN_RESUME_EXECUTION_MODE",
            )
            dispatch = selected["worktreeSetup"]["hostDispatch"]
            self.assertEqual(dispatch["hostAdapterId"], "codex")
            self.assertEqual(
                dispatch["hostOperation"],
                "CREATE_CODEX_PROJECT_TASK",
            )
            self.assertEqual(dispatch["environment"], "worktree")
            self.assertTrue(dispatch["stableDeliveryWorkspace"])
            self.assertFalse(dispatch["requiresNewTopLevelSession"])
            self.assertEqual(dispatch["mainConversationRole"], "MONITOR_ONLY")
            self.assertEqual(
                dispatch["agentDispatch"],
                {
                    "taskEnvironment": "worktree",
                    "runInBackground": True,
                    "reusePolicy": "RESUME_PROJECT_TASK",
                },
            )
            self.assertEqual(dispatch["baseCommit"], base_commit)
            self.assertEqual(
                dispatch["continuation"]["expectedHierarchyFingerprint"],
                preview["hierarchyFingerprint"],
            )
            self.assertIn("Never start another top-level CLI session", dispatch["prompt"])

            status = call_tool(
                "workspace_status",
                {"root_id": "d-auto-transition"},
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
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
                trusted_host_adapter="codex",
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
            context = call_tool(
                "loop_context",
                {
                    "root_id": "d-auto-transition",
                    "node_id": "loop:t-service",
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )
            context_projects = {
                item["id"]: item
                for item in context["projectScopes"]
            }
            self.assertEqual(
                context_projects["erp-protein"]["workspaceRoot"],
                str(worktree.resolve()),
            )
            self.assertEqual(
                context_projects["erp-pm"]["workspaceRoot"],
                str(secondary_worktree.resolve()),
            )
            self.assertEqual(
                {
                    item["id"]: item["workspaceRoot"]
                    for item in context["projectScopeAnchors"]
                },
                {
                    "erp-protein": str(repository.resolve()),
                    "erp-pm": str(secondary_repository.resolve()),
                },
            )

    def test_codex_host_dispatch_preserves_master_checkout(self) -> None:
        with TemporaryDirectory() as root:
            repository, _worktree, base_commit, branch_ref = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-codex-master",
                    mainline="master",
                )
            )
            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
                base_ref="master",
            )
            hierarchy["delivery"]["id"] = "d-codex-master"
            preview = call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            selected = call_tool(
                "select_execution_mode",
                {
                    "root_id": "d-codex-master",
                    "selection": "AUTOMATIC",
                    "expected_hierarchy_fingerprint": preview[
                        "hierarchyFingerprint"
                    ],
                    "expected_graph_fingerprint": preview[
                        "graphFingerprint"
                    ],
                    "authorized_project_ids": [],
                    "confirmed_by": "human",
                },
                root=str(repository),
                workspace_root=str(repository),
                trusted_host_adapter="codex",
            )

            dispatch = selected["worktreeSetup"]["hostDispatch"]
            self.assertEqual(
                dispatch["hostOperation"],
                "CREATE_CODEX_PROJECT_TASK",
            )
            self.assertEqual(dispatch["environment"], "worktree")
            self.assertEqual(dispatch["baseRef"], "master")
            self.assertEqual(dispatch["integrationTarget"], "master")
            self.assertFalse(dispatch["manualDirectoryChangeRequired"])
            self.assertEqual(
                dispatch["coordinatorCheckoutPolicy"],
                "PRESERVE_CURRENT_CHECKOUT",
            )
            self.assertEqual(
                git_command(repository, "branch", "--show-current"),
                "master",
            )

    def test_loop_context_keeps_parallel_deliveries_in_their_own_worktrees(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, first, base_commit, first_branch = (
                git_delivery_checkout(
                    root,
                    delivery_id="d-first",
                )
            )
            second = Path(root, "worktrees", "d-second")
            second_branch = "feature/d-second"
            git_command(
                repository,
                "worktree",
                "add",
                "-b",
                second_branch,
                str(second),
                "main",
            )

            deliveries = (
                ("d-first", "t-first", first_branch, first),
                ("d-second", "t-second", second_branch, second),
            )
            for delivery_id, task_id, branch_ref, worktree in deliveries:
                hierarchy = bind_delivery_to_git(
                    isolated_task_hierarchy(delivery_id, task_id),
                    branch_ref=branch_ref,
                    base_commit=base_commit,
                )
                hierarchy["delivery"]["projectScopes"] = [
                    {
                        "id": "erp-protein",
                        "workspaceRoot": str(repository.resolve()),
                        "access": "READ_WRITE",
                        "gitBinding": deepcopy(
                            hierarchy["delivery"]["gitBinding"]
                        ),
                    }
                ]
                prepared = call_tool(
                    "prepare_hierarchy",
                    {"hierarchy": hierarchy},
                    root=str(repository),
                    workspace_root=str(worktree),
                )
                call_tool(
                    "freeze_hierarchy",
                    {
                        "root_id": delivery_id,
                        "expected_delivery_revision": 1,
                        "expected_hierarchy_fingerprint": prepared[
                            "hierarchyFingerprint"
                        ],
                        "authorized_project_ids": ["erp-protein"],
                        "confirmed_by": "human",
                    },
                    root=str(repository),
                    workspace_root=str(worktree),
                )

            context = call_tool(
                "loop_context",
                {
                    "root_id": "d-second",
                    "node_id": "loop:t-second",
                },
                root=str(repository),
                workspace_root=str(second),
                trusted_host_adapter="claude-code",
            )

            self.assertEqual(
                context["workspaceIsolation"]["workspaceRoot"],
                str(second.resolve()),
            )
            self.assertEqual(
                context["projectScopes"][0]["workspaceRoot"],
                str(second.resolve()),
            )
            self.assertEqual(
                context["projectScopes"][0]["declaredWorkspaceRoot"],
                str(repository.resolve()),
            )
            self.assertEqual(
                context["projectScopeAnchors"][0]["workspaceRoot"],
                str(repository.resolve()),
            )
            self.assertTrue(
                context["rules"][
                    "projectScopeWorkspaceRootsAreRuntimeVerified"
                ]
            )
            self.assertTrue(
                context["rules"][
                    "loopsMustNotCreateSwitchOrCheckoutGitBranches"
                ]
            )
            self.assertEqual(
                git_command(first, "branch", "--show-current"),
                first_branch,
            )
            self.assertEqual(
                git_command(second, "branch", "--show-current"),
                second_branch,
            )

    def test_single_project_loop_context_synthesizes_verified_scope(
        self,
    ) -> None:
        with TemporaryDirectory() as root:
            repository, worktree, base_commit, branch_ref = (
                git_delivery_checkout(root)
            )
            hierarchy = bind_delivery_to_git(
                task_hierarchy(),
                branch_ref=branch_ref,
                base_commit=base_commit,
            )
            prepared = call_tool(
                "prepare_hierarchy",
                {"hierarchy": hierarchy},
                root=str(repository),
                workspace_root=str(worktree),
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
                workspace_root=str(worktree),
            )

            context = call_tool(
                "loop_context",
                {
                    "root_id": prepared["rootId"],
                    "node_id": loop_node_id("t-service"),
                },
                root=str(repository),
                workspace_root=str(worktree),
                trusted_host_adapter="codex",
            )

        self.assertEqual(context["projectScopeAnchors"], [])
        self.assertEqual(len(context["projectScopes"]), 1)
        scope = context["projectScopes"][0]
        self.assertEqual(scope["id"], "primary")
        self.assertEqual(scope["access"], "READ_WRITE")
        self.assertEqual(scope["scopeSource"], "DELIVERY_GIT_BINDING")
        self.assertEqual(scope["workspaceRoot"], str(worktree.resolve()))
        self.assertEqual(
            scope["gitBinding"],
            hierarchy["delivery"]["gitBinding"],
        )
        self.assertIn("gitWorkspace", scope)
        self.assertTrue(
            context["rules"][
                "projectScopeWorkspaceRootsAreRuntimeVerified"
            ]
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
                "Delivery Graph never recommends or selects a "
                "development model",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "a Hook-attested Codex Delivery session uses "
                "claim_current_task for READY TASK Loops",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "plan_dispatch_batch atomically reserves every Review Loop",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "A new user requirement defaults to a new Delivery",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "Codex uses an environment=worktree project task",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "Claude Code and Codex automatic Git Deliveries always use "
                "HOST_NATIVE_LINKED_WORKTREE",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "hostDispatch",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "It never asks the user to start another Claude session",
                initialized["result"]["instructions"],
            )
            self.assertIn(
                "preserving the primary checkout",
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
            self.assertNotIn(
                "EXCLUSIVE_PRIMARY_CHECKOUT",
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
            self.assertEqual(len(listed["result"]["tools"]), 34)
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


class HierarchyFileTests(unittest.TestCase):
    """hierarchy_file loads a large hierarchy from a workspace file."""

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _valid_hierarchy(self) -> dict:
        with (self._repo_root() / "examples" / "team-loops" / "light-change.json").open(
            encoding="utf-8"
        ) as handle:
            return json.load(handle)

    @staticmethod
    def _write(workspace: Path, name: str, payload: object) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (workspace / name).write_text(text, encoding="utf-8")

    def test_preview_loads_hierarchy_from_file(self) -> None:
        hierarchy = self._valid_hierarchy()
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write(workspace, "h.json", hierarchy)
            call_tool(
                "preview_hierarchy",
                {"hierarchy_file": "h.json"},
                root=str(workspace),
                workspace_root=str(workspace),
            )
            # hierarchy_file was consumed and substituted; artifacts written
            self.assertTrue(
                (workspace / ".layered-delivery" / "scheduler.db").is_file()
            )
            repository = SchedulerRepository(str(workspace))
            stored = repository.hierarchy(hierarchy["delivery"]["id"])
            self.assertEqual(
                stored["hierarchy"]["delivery"]["id"],
                hierarchy["delivery"]["id"],
            )

    def test_inline_hierarchy_still_works(self) -> None:
        hierarchy = self._valid_hierarchy()
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=str(workspace),
                workspace_root=str(workspace),
            )
            self.assertTrue(
                (workspace / ".layered-delivery" / "scheduler.db").is_file()
            )

    def test_inline_and_file_are_mutually_exclusive(self) -> None:
        hierarchy = self._valid_hierarchy()
        with TemporaryDirectory() as temporary:
            self._write(Path(temporary), "h.json", hierarchy)
            with self.assertRaises(GatedLoopError) as caught:
                validate_tool_arguments(
                    "preview_hierarchy",
                    {"hierarchy": hierarchy, "hierarchy_file": "h.json"},
                )
            self.assertEqual(
                caught.exception.code, "SCHEDULER_HIERARCHY_INPUT_CONFLICT"
            )

    def test_neither_inline_nor_file_is_rejected(self) -> None:
        with self.assertRaises(GatedLoopError) as caught:
            validate_tool_arguments("preview_hierarchy", {})
        self.assertEqual(
            caught.exception.code, "SCHEDULER_HIERARCHY_INPUT_REQUIRED"
        )

    def test_file_outside_workspace_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy_file": "../outside.json"},
                    root=str(workspace),
                    workspace_root=str(workspace),
                )
            self.assertEqual(caught.exception.code, "PATH_OUTSIDE_ROOT")

    def test_invalid_json_file_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write(workspace, "bad.json", "{not json")
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy_file": "bad.json"},
                    root=str(workspace),
                    workspace_root=str(workspace),
                )
            self.assertEqual(
                caught.exception.code, "SCHEDULER_HIERARCHY_FILE_INVALID"
            )

    def test_non_object_json_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            self._write(workspace, "arr.json", "[1, 2, 3]")
            with self.assertRaises(GatedLoopError) as caught:
                call_tool(
                    "preview_hierarchy",
                    {"hierarchy_file": "arr.json"},
                    root=str(workspace),
                    workspace_root=str(workspace),
                )
            self.assertEqual(
                caught.exception.code, "SCHEDULER_HIERARCHY_FILE_INVALID"
            )


class AutomaticBranchOccupancyTests(unittest.TestCase):
    """AUTOMATIC refuses a frozen branchRef already held by another worktree."""

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (args, result.stderr)
        return result

    def _primary_repo(self) -> tuple[Path, str]:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        self._git(repo, "-c", "init.defaultBranch=main", "init")
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "t")
        (repo / "f.txt").write_text("x", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "m")
        main_sha = self._git(repo, "rev-parse", "main").stdout.strip()
        self._git(repo, "switch", "-c", "feature/m_lf_protein")
        (repo / "g.txt").write_text("y", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "p")
        return repo, main_sha

    @staticmethod
    def _binding(branch: str, main_sha: str) -> dict:
        return {
            "branchRef": branch,
            "baseRef": "main",
            "baseCommit": main_sha,
            "integrationTarget": "main",
        }

    def test_rejects_branchref_held_by_primary(self) -> None:
        repo, main_sha = self._primary_repo()
        with self.assertRaises(GatedLoopError) as caught:
            _assert_automatic_git_branch_available(
                {
                    "delivery": {
                        "gitBinding": self._binding(
                            "feature/m_lf_protein", main_sha
                        )
                    }
                },
                str(repo),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_GIT_BRANCH_IN_USE_BY_OTHER_WORKTREE",
        )

    def test_allows_free_branch(self) -> None:
        repo, main_sha = self._primary_repo()
        _assert_automatic_git_branch_available(
            {
                "delivery": {
                    "gitBinding": self._binding(
                        "feature/free-branch", main_sha
                    )
                }
            },
            str(repo),
        )

    def test_allows_missing_gitbinding(self) -> None:
        repo, _ = self._primary_repo()
        _assert_automatic_git_branch_available({"delivery": {}}, str(repo))


class DraftCleanupTests(unittest.TestCase):
    """cancel_graph_run abandons a pre-run draft and releases its requirementKey."""

    def _hierarchy(self) -> dict:
        repo_root = Path(__file__).resolve().parents[1]
        with (repo_root / "examples" / "team-loops" / "light-change.json").open(
            encoding="utf-8"
        ) as handle:
            hierarchy = json.load(handle)
        hierarchy["delivery"]["requirementKey"] = "MPROTEIN-CLEANUP-TEST"
        return hierarchy

    def test_abandon_prerun_draft_releases_requirement_key(self) -> None:
        hierarchy = self._hierarchy()
        root_id = hierarchy["delivery"]["id"]
        with TemporaryDirectory() as temporary:
            root = str(Path(temporary))
            call_tool(
                "preview_hierarchy",
                {"hierarchy": hierarchy},
                root=root,
                workspace_root=root,
            )
            result = call_tool(
                "cancel_graph_run",
                {
                    "root_id": root_id,
                    "cancelled_by": "tester",
                    "reason": "stuck pre-run draft",
                },
                root=root,
                workspace_root=root,
            )
            self.assertEqual(result["deliveryStatus"], "ABANDONED")
            self.assertIsNone(result["runId"])
            # requirementKey released: a new Delivery with the same key previews OK
            retry = json.loads(json.dumps(hierarchy))
            retry["delivery"]["id"] = root_id + "-retry"
            call_tool(
                "preview_hierarchy",
                {"hierarchy": retry},
                root=root,
                workspace_root=root,
            )


class StaleBaseRebaseAdvisoryTests(unittest.TestCase):
    """workspace_status surfaces a worktreeRebase advisory when the frozen base falls behind main."""

    @staticmethod
    def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, (args, result.stderr)
        return result

    def _repo(self) -> tuple[Path, str]:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name)
        self._git(repo, "-c", "init.defaultBranch=main", "init")
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "t")
        (repo / "a").write_text("a", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "c0")
        c0 = self._git(repo, "rev-parse", "main").stdout.strip()
        self._git(repo, "switch", "-c", "feature/x")
        (repo / "b").write_text("b", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "c1")
        return repo, c0

    @staticmethod
    def _binding(c0: str) -> dict:
        return {
            "branchRef": "feature/x",
            "baseRef": "main",
            "baseCommit": c0,
            "integrationTarget": "main",
        }

    def test_no_advisory_when_main_unchanged(self) -> None:
        repo, c0 = self._repo()
        result = inspect_frozen_git_workspace_provenance(
            str(repo), self._binding(c0)
        )
        self.assertNotIn("worktreeRebase", result)

    def test_advisory_when_main_advanced(self) -> None:
        repo, c0 = self._repo()
        self._git(repo, "switch", "main")
        (repo / "c").write_text("c", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-m", "c2")
        c2 = self._git(repo, "rev-parse", "main").stdout.strip()
        self._git(repo, "switch", "feature/x")
        result = inspect_frozen_git_workspace_provenance(
            str(repo), self._binding(c0)
        )
        advisory = result["worktreeRebase"]
        self.assertTrue(advisory["required"])
        self.assertEqual(advisory["frozenBaseCommit"], c0)
        self.assertEqual(advisory["currentBaseCommit"], c2)
        self.assertEqual(advisory["integrationTarget"], "main")


if __name__ == "__main__":
    unittest.main()
