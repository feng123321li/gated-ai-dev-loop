from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread, current_thread
import unittest
from unittest.mock import patch

from hdg.errors import GatedLoopError
from hdg.graph_frontier import get_graph_frontier
from hdg.graph_model import (
    group_review_node_id,
    loop_node_id,
    review_node_id,
)
from hdg.graph_runtime import (
    attest_loop_receiver,
    cancel_graph_run,
    graph_events,
    graph_status,
    heartbeat_loop,
    loop_context,
    pause_loop,
    report_host_capacity_exhausted,
    report_loop_progress,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
    resume_loop,
)
from hdg.loop_contracts import (
    loop_completion_policy,
    loop_execution_policy,
)
from hdg.mcp_tools import tool_definitions
from hdg.mcp_tools import call_tool
from hdg.model_core import validate_hierarchy_definition
from hdg.model_rendering import (
    PROJECTION_TEMPLATES,
    PROJECTION_TEMPLATE_VERSION,
    STATUS_TEXT,
    WORK_ITEM_DIRECTORY,
)
from hdg.planning import (
    create_manual_handoff,
    freeze_hierarchy,
    prepare_hierarchy,
    preview_hierarchy,
    workspace_status,
)
from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    group_hierarchy,
    loop_descriptor,
    node,
    recursive_hierarchy,
    skill_hint,
    task_definition,
    task_hierarchy,
)
from .automatic_dispatch import dispatch_loop, reserve_loop


def delivery_task_hierarchy(
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


def at(minutes: int) -> datetime:
    return datetime(
        2026,
        7,
        29,
        8,
        0,
        tzinfo=timezone.utc,
    ) + timedelta(minutes=minutes)


def success(summary: str = "Loop completed.") -> dict:
    return {
        "status": "SUCCEEDED",
        "summary": summary,
        "result": {"evidence": "opaque-to-scheduler"},
    }


def parallel_hierarchy() -> dict:
    source = group_hierarchy()
    source["root"]["children"][1]["definition"]["execution"][
        "dependsOn"
    ] = []
    shared = ["project:erp/module:shared"]
    for child in source["root"]["children"]:
        child["definition"]["execution"]["loop"][
            "resourceClaims"
        ] = shared
    return source


def disjoint_parallel_hierarchy() -> dict:
    source = group_hierarchy()
    source["root"]["children"][1]["definition"]["execution"][
        "dependsOn"
    ] = []
    return source


def hierarchy_nodes(hierarchy: dict) -> list[dict]:
    pending = [hierarchy["root"]]
    result = []
    while pending:
        current = pending.pop()
        result.append(current)
        pending.extend(reversed(current["children"]))
    return result


def hierarchical_work_item_paths(hierarchy: dict) -> dict[str, str]:
    paths: dict[str, str] = {}

    def visit(current: dict, parent_path: str | None) -> None:
        item_id = current["definition"]["id"]
        item_path = (
            f"{WORK_ITEM_DIRECTORY}/{item_id}"
            if parent_path is None
            else f"{parent_path}/children/{item_id}"
        )
        paths[item_id] = item_path
        for child in current["children"]:
            visit(child, item_path)

    visit(hierarchy["root"], None)
    return paths


def auditable_recursive_hierarchy() -> dict:
    source = recursive_hierarchy()
    for current in hierarchy_nodes(source):
        definition = current["definition"]
        item_id = definition["id"]
        definition["summary"] = f"Audit summary for {item_id}."
        if definition["kind"] == "TASK":
            loop = definition["execution"]["loop"]
            loop["ref"] = f"audit/task/{item_id}@1"
            loop["resourceClaims"] = [
                f"project:audit/task:{item_id}"
            ]
            loop["payload"] = {
                "rawAuditMarker": f"raw-task-payload::{item_id}",
                "acceptance": [
                    f"{item_id} 的验收结果可独立核对",
                    "所有自动化检查通过",
                ],
                "businessRules": [
                    "保持依赖关系与资源锁一致",
                ],
                "nested": {
                    "workItemId": item_id,
                    "enabled": True,
                },
                "notes": "首行\n# 不能改变模板 | `原样文本`",
            }
        else:
            review = current["reviewLoop"]
            review["ref"] = f"audit/group-review/{item_id}@1"
            review["resourceClaims"] = [
                f"project:audit/group:{item_id}"
            ]
            review["payload"] = {
                "rawAuditMarker": f"raw-group-review::{item_id}",
                "reviewFocus": ["核对全部直接子级结果"],
                "nested": {"workItemId": item_id},
            }
    delivery = source["delivery"]
    delivery["summary"] = "Audit summary for the complete Delivery."
    delivery_review = delivery["reviewLoop"]
    delivery_review["ref"] = "audit/delivery-review/d-recursive@1"
    delivery_review["resourceClaims"] = [
        "project:audit/delivery:d-recursive"
    ]
    delivery_review["payload"] = {
        "rawAuditMarker": "raw-delivery-review::d-recursive",
        "reviewFocus": ["核对完整交付结果"],
        "nested": {"deliveryId": "d-recursive"},
    }
    return source


def interface_hierarchy() -> dict:
    source = task_hierarchy()
    source["root"]["definition"]["execution"]["loop"]["payload"].update(
        {
            "interfaces": [
                {
                    "protocol": "HTTP",
                    "name": "创建订单",
                    "summary": "调整创建订单接口及其字段。",
                    "changeType": "MODIFY",
                    "before": {
                        "method": "POST",
                        "path": "/api/v1/orders",
                        "request": [
                            {
                                "name": "legacyCustomerNo",
                                "type": "string",
                                "required": True,
                                "description": "原客户编号",
                            },
                            {
                                "name": "quantity",
                                "type": "integer",
                                "required": False,
                                "description": "商品数量",
                            },
                            {
                                "name": "channel",
                                "type": "string",
                                "required": False,
                                "description": "下单渠道",
                            }
                        ],
                        "response": [
                            {
                                "name": "orderNo",
                                "type": "string",
                                "description": "原订单编号",
                            }
                        ],
                    },
                    "after": {
                        "method": "POST",
                        "path": "/api/orders",
                        "request": [
                            {
                                "name": "customerId",
                                "type": "string",
                                "required": True,
                                "description": "客户标识",
                            },
                            {
                                "name": "quantity",
                                "type": "integer",
                                "required": True,
                                "description": "必须大于零的商品数量",
                            },
                            {
                                "name": "channel",
                                "type": "string",
                                "required": False,
                                "description": "下单渠道",
                            }
                        ],
                        "response": [
                            {
                                "name": "orderId",
                                "type": "string",
                                "description": "订单标识",
                            }
                        ],
                    },
                },
                {
                    "protocol": "DUBBO",
                    "name": "创建订单服务",
                    "summary": "供内部服务创建订单。",
                    "changeType": "CREATE",
                    "before": None,
                    "after": {
                        "service": "com.example.order.OrderService",
                        "method": "createOrder",
                        "request": {
                            "type": "CreateOrderRequest",
                            "fields": ["customerId"],
                        },
                        "response": {
                            "type": "CreateOrderResponse",
                            "fields": ["orderId"],
                        },
                    },
                },
                {
                    "protocol": "GRPC",
                    "name": "旧版订单查询服务",
                    "summary": "删除不再使用的旧版 gRPC 接口。",
                    "changeType": "DELETE",
                    "before": {
                        "identifier": (
                            "order.v1.LegacyOrderService/GetOrder"
                        ),
                        "request": [
                            {
                                "name": "id",
                                "type": "string",
                                "required": True,
                                "description": "订单标识",
                            }
                        ],
                        "response": {
                            "type": "LegacyOrderResponse",
                        },
                    },
                    "after": None,
                },
            ]
        }
    )
    return source


class SchedulerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = self.temporary.name

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare_and_freeze(self, hierarchy: dict) -> dict:
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        return prepared

    def test_manual_handoff_materializes_development_bundle_without_starting(
        self,
    ) -> None:
        first = prepare_hierarchy(
            root=self.root,
            hierarchy=delivery_task_hierarchy("d-first", "t-first"),
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=first["rootId"],
            expected_hierarchy_fingerprint=(
                first["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        hierarchy = delivery_task_hierarchy("d-second", "t-second")
        hierarchy["root"]["definition"]["execution"]["loop"][
            "payload"
        ]["goal"] = "实现第二个独立需求并完成验证。"

        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(3),
        )

        self.assertEqual(preview["status"], "PREVIEW")
        self.assertEqual(
            preview["nextAction"],
            "SELECT_AUTOMATIC_EXECUTION_OR_MANUAL_HANDOFF",
        )
        self.assertEqual(handoff["status"], "HANDOFF_READY")
        self.assertEqual(
            handoff["requirementSnapshotStatus"],
            "FROZEN",
        )
        self.assertEqual(
            handoff["nextAction"],
            "OPEN_FROZEN_BUNDLE_IN_ANY_CLI",
        )
        self.assertFalse(handoff["controlStateCreated"])
        self.assertFalse(handoff["graphRunCreated"])
        self.assertFalse(handoff["workspaceCreated"])
        self.assertEqual(
            set(handoff["manualHandoff"]),
            {"path", "format", "selfContained"},
        )
        self.assertEqual(handoff["manualHandoff"]["format"], "MARKDOWN")
        self.assertTrue(handoff["manualHandoff"]["selfContained"])

        handoff_root = Path(
            self.root,
            ".layered-delivery",
            "d-second",
        )
        files = list(handoff_root.glob("handoff-*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(
            {path.name for path in handoff_root.iterdir()},
            {
                "acceptance.md",
                "baseline.md",
                files[0].name,
                "overview.md",
                "progress.md",
                "revisions.md",
                "work-items",
            },
        )
        self.assertEqual(
            files[0].relative_to(Path(self.root)).as_posix(),
            handoff["manualHandoff"]["path"],
        )
        self.assertEqual(
            set(handoff["humanArtifacts"]),
            {
                "overview",
                "baseline",
                "progress",
                "acceptance",
                "revisions",
                "taskBaselines",
                "workItems",
            },
        )
        self.assertNotIn(
            "workspaceOverview",
            handoff["humanArtifacts"],
        )
        for artifact_name in (
            "overview",
            "baseline",
            "progress",
            "acceptance",
            "revisions",
        ):
            with self.subTest(artifact=artifact_name):
                self.assertTrue(
                    Path(
                        self.root,
                        handoff["humanArtifacts"][artifact_name],
                    ).is_file()
                )
        task_artifacts = handoff["humanArtifacts"]["workItems"][
            "t-second"
        ]
        self.assertEqual(task_artifacts["kind"], "TASK")
        for artifact_name in ("baseline", "progress", "acceptance"):
            with self.subTest(task_artifact=artifact_name):
                self.assertTrue(
                    Path(
                        self.root,
                        task_artifacts[artifact_name],
                    ).is_file()
                )
        self.assertFalse(
            Path(self.root, ".layered-delivery", "handoffs").exists()
        )
        content = files[0].read_text(encoding="utf-8")
        for expected in (
            "# 开发内容交接",
            "d-second",
            "t-second",
            "实现第二个独立需求并完成验证。",
            preview["hierarchyFingerprint"],
            preview["graphFingerprint"],
            "交接前不指定",
            "开始实际开发时再创建",
            "需求内容快照已冻结",
            "切换到任意 CLI",
            "直接按冻结内容开发",
            '"id": "d-second"',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, content)
        for forbidden in (
            "目标开发 Agent",
            "Codex",
            "Claude Code",
            "glm-5.2",
            "gpt-5.6",
            "prepare_hierarchy",
            "freeze_hierarchy",
            "graph_frontier",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, content)

        for projection_name in (
            "overview.md",
            "baseline.md",
            "progress.md",
            "acceptance.md",
        ):
            with self.subTest(projection=projection_name):
                projection = Path(
                    handoff_root,
                    projection_name,
                ).read_text(encoding="utf-8")
                self.assertIn(
                    "需求已冻结（手动开发，调度未启动）",
                    projection,
                )
        revisions = Path(
            handoff_root,
            "revisions.md",
        ).read_text(encoding="utf-8")
        self.assertIn("HANDOFF\\_READY", revisions)
        self.assertIn("已冻结，未创建 Graph Run", revisions)

        active = workspace_status(root=self.root)
        self.assertEqual(active["rootId"], "d-first")
        self.assertEqual(active["status"], "ACTIVE")
        with self.assertRaises(GatedLoopError) as caught:
            SchedulerRepository(self.root).hierarchy("d-second")
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_HIERARCHY_MISSING",
        )
        self.assertFalse(Path(self.root, "worktrees").exists())

    def test_manual_handoff_shares_directory_with_later_projections(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy("d-shared", "t-shared")
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        handoff_path = Path(
            self.root,
            handoff["manualHandoff"]["path"],
        )
        manual_overview = handoff_path.with_name("overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "需求已冻结（手动开发，调度未启动）",
            manual_overview,
        )

        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )

        self.assertEqual(prepared["rootId"], "d-shared")
        self.assertTrue(handoff_path.is_file())
        delivery_root = handoff_path.parent
        prepared_overview = Path(
            delivery_root,
            "overview.md",
        ).read_text(encoding="utf-8")
        self.assertIn("待冻结", prepared_overview)
        self.assertNotIn(
            "需求已冻结（手动开发，调度未启动）",
            prepared_overview,
        )
        for projection in (
            "overview.md",
            "baseline.md",
            "progress.md",
            "acceptance.md",
            "revisions.md",
            "work-items",
        ):
            with self.subTest(projection=projection):
                self.assertTrue(Path(delivery_root, projection).exists())
        self.assertFalse(
            Path(self.root, ".layered-delivery", "handoffs").exists()
        )

    def test_manual_and_automatic_delivery_trees_share_structure(
        self,
    ) -> None:
        hierarchy = interface_hierarchy()
        root_id = hierarchy["delivery"]["id"]
        with TemporaryDirectory() as automatic_root:
            prepare_hierarchy(
                root=automatic_root,
                hierarchy=hierarchy,
                now=at(0),
            )
            automatic_delivery = Path(
                automatic_root,
                ".layered-delivery",
                root_id,
            )
            automatic_files = {
                path.relative_to(automatic_delivery).as_posix()
                for path in automatic_delivery.rglob("*")
                if path.is_file()
            }

        with TemporaryDirectory() as manual_root:
            preview = preview_hierarchy(
                root=manual_root,
                hierarchy=hierarchy,
                now=at(0),
            )
            create_manual_handoff(
                root=manual_root,
                hierarchy=hierarchy,
                expected_hierarchy_fingerprint=(
                    preview["hierarchyFingerprint"]
                ),
                expected_graph_fingerprint=preview[
                    "graphFingerprint"
                ],
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )
            manual_delivery = Path(
                manual_root,
                ".layered-delivery",
                root_id,
            )
            manual_files = {
                path.relative_to(manual_delivery).as_posix()
                for path in manual_delivery.rglob("*")
                if path.is_file()
                and not path.name.startswith("handoff-")
            }
            handoff_files = list(
                manual_delivery.glob("handoff-*.md")
            )

        self.assertEqual(manual_files, automatic_files)
        self.assertEqual(len(handoff_files), 1)
        self.assertTrue(
            any(path.startswith("work-items/") for path in manual_files)
        )
        self.assertTrue(
            any("/interfaces/" in path for path in manual_files)
        )

    def test_manual_handoff_preserves_matching_graph_projections(
        self,
    ) -> None:
        hierarchy = delivery_task_hierarchy(
            "d-existing",
            "t-existing",
        )
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        preview = preview_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(2),
        )

        handoff = create_manual_handoff(
            root=self.root,
            hierarchy=hierarchy,
            expected_hierarchy_fingerprint=(
                preview["hierarchyFingerprint"]
            ),
            expected_graph_fingerprint=preview["graphFingerprint"],
            authorized_project_ids=[],
            confirmed=True,
            confirmed_by="human",
            now=at(3),
        )

        delivery_root = Path(
            self.root,
            ".layered-delivery",
            "d-existing",
        )
        overview = Path(delivery_root, "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("运行中", overview)
        self.assertNotIn(
            "需求已冻结（手动开发，调度未启动）",
            overview,
        )
        self.assertTrue(
            Path(self.root, handoff["manualHandoff"]["path"]).is_file()
        )
        self.assertEqual(
            workspace_status(root=self.root)["status"],
            "ACTIVE",
        )

    def test_task_and_review_are_uniform_loops_until_confirmation(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [loop_node_id("t-service")],
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="agent-1",
            operation_id="op-task-1",
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            operation_id="op-task-1",
            outcome=success("Task Loop completed."),
            now=at(4),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            ["review:task:t-service"],
        )
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id="review:task:t-service",
            owner="task-reviewer-1",
            operation_id="op-task-review-1",
            now=at(6),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id="review:task:t-service",
            operation_id="op-task-review-1",
            outcome=success("Task review completed."),
            now=at(7),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(8),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [review_node_id(root_id)],
        )
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
            owner="reviewer-1",
            operation_id="op-review-1",
            now=at(9),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
            operation_id="op-review-1",
            outcome=success("Independent review completed."),
            now=at(10),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(11),
        )
        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "RECORD_USER_CONFIRMATION",
                    "nodeId": f"confirm:{root_id}",
                }
            ],
        )
        completed = record_user_confirmation(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            confirmed_by="human",
            summary="Accepted.",
            now=at(12),
        )
        self.assertEqual(completed["status"], "COMPLETED")
        terminal_before = graph_status(
            root=self.root,
            root_id=root_id,
        )
        terminal_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        terminal_after = graph_status(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(terminal_frontier["status"], "COMPLETED")
        self.assertEqual(terminal_frontier["actions"], [])
        self.assertEqual(
            terminal_after["updatedAt"],
            terminal_before["updatedAt"],
        )
        self.assertEqual(
            terminal_after["completedAt"],
            terminal_before["completedAt"],
        )
        completed_overview = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            (
                "| 任务 | t-service | Run t-service | 已成功 | "
                "Task review completed. | "
                "[查看](work-items/t-service/acceptance.md) |"
            ),
            completed_overview,
        )
        self.assertNotIn("agent-1", completed_overview)
        self.assertIn("| 已成功 | reviewer-1 | 1 |", completed_overview)
        self.assertIn("opaque-to-scheduler", completed_overview)
        self.assertNotIn("SUCCEEDED", completed_overview)
        task_acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "t-service"
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        self.assertIn("| 已成功 | agent-1 | 1 |", task_acceptance)
        self.assertIn(
            "| 已成功 | task-reviewer-1 | 1 |",
            task_acceptance,
        )
        workspace_overview = (
            Path(self.root)
            / ".layered-delivery"
            / "overview.md"
        ).read_text(encoding="utf-8")
        self.assertIn("| 已完成 |", workspace_overview)
        self.assertNotIn("TASK 进度", workspace_overview)
        self.assertNotIn("GROUP 数量", workspace_overview)
        self.assertNotIn("COMPLETED", workspace_overview)

        event_types = [
            item["eventType"]
            for item in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
        ]
        self.assertIn("LOOP_SUCCEEDED", event_types)
        self.assertIn("USER_CONFIRMED", event_types)
        self.assertNotIn("TASK_IMPLEMENTED", event_types)
        self.assertNotIn("GATE_FAILED", event_types)

    def test_root_task_review_runs_before_delivery_review(self) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["reviewLoop"] = loop_descriptor(
            "task/independent-review-loop@1"
        )
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        task_id = loop_node_id("t-service")
        task_review_id = "review:task:t-service"

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_id,
            owner="task-agent",
            operation_id="op-task-with-review",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_id,
            operation_id="op-task-with-review",
            outcome=success("Task implementation completed."),
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [task_review_id],
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
        )
        self.assertEqual(context["kind"], "TASK_REVIEW_LOOP")
        self.assertEqual(context["workItemId"], "t-service")
        self.assertEqual(context["humanArtifacts"]["workItem"]["kind"], "TASK")
        self.assertEqual(
            context["loop"]["ref"],
            "task/independent-review-loop@1",
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
            owner="task-reviewer",
            operation_id="op-task-review",
            now=at(5),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=task_review_id,
            operation_id="op-task-review",
            outcome=success("Task review completed."),
            now=at(6),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(7),
        )
        self.assertEqual(
            [item["nodeId"] for item in frontier["readyLoops"]],
            [review_node_id(root_id)],
        )
        projection_root = Path(self.root, ".layered-delivery", root_id)
        task_root = projection_root / WORK_ITEM_DIRECTORY / "t-service"
        baseline = (task_root / "baseline.md").read_text(encoding="utf-8")
        progress = (task_root / "progress.md").read_text(encoding="utf-8")
        acceptance = (task_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("task/independent-review-loop@1", baseline)
        self.assertIn("TASK Review", progress)
        self.assertIn("Task review completed.", acceptance)

    def test_group_without_review_is_rejected_before_prepare(self) -> None:
        hierarchy = group_hierarchy()
        hierarchy["root"]["reviewLoop"] = None
        with self.assertRaises(GatedLoopError) as caught:
            prepare_hierarchy(
                root=self.root,
                hierarchy=hierarchy,
                now=at(0),
            )
        self.assertEqual(
            caught.exception.code,
            "WORK_ITEM_GROUP_REVIEW_REQUIRED",
        )

    def test_task_and_review_select_shared_skill_hints_at_runtime(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint(
                "springboot-tdd",
                "Prefer TDD when the active Loop is a Spring task.",
            )
        ]
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]

        for node_id in (
            loop_node_id("t-service"),
            review_node_id(root_id),
        ):
            with self.subTest(node_id=node_id):
                context = loop_context(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                )
                self.assertEqual(
                    context["skillHints"],
                    hierarchy["root"]["skillHints"],
                )
                self.assertTrue(
                    context["rules"]["skillHintsAreAdvisory"]
                )
                self.assertTrue(
                    context["rules"]["selectSkillsAtRuntime"]
                )
                self.assertTrue(
                    context["rules"][
                        "prioritizeApplicableSkillHints"
                    ]
                )

    def test_recursive_review_context_contains_all_upstream_loop_results(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(group_hierarchy())
        root_id = prepared["rootId"]
        for minute, item_id in ((2, "t-api"), (6, "t-core")):
            node_id = loop_node_id(item_id)
            operation_id = f"op-{item_id}"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="agent",
                receiver_context_id=f"context-{item_id}",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success(f"{item_id} completed."),
                now=at(minute + 1),
            )
            review_id = f"review:task:{item_id}"
            review_operation = f"op-review-{item_id}"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=review_id,
                owner="task-reviewer",
                receiver_context_id=f"context-review-{item_id}",
                operation_id=review_operation,
                now=at(minute + 2),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=review_id,
                operation_id=review_operation,
                outcome=success(f"{item_id} review completed."),
                now=at(minute + 3),
            )

        group_review_id = group_review_node_id("g-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
            owner="group-reviewer",
            receiver_context_id="context-review-g-service",
            operation_id="op-group-review",
            now=at(10),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=group_review_id,
            operation_id="op-group-review",
            outcome=success("g-service review completed."),
            now=at(11),
        )

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=review_node_id(root_id),
        )

        self.assertEqual(
            [
                item["nodeId"]
                for item in context["upstreamLoopResults"]
            ],
            [
                loop_node_id("t-api"),
                loop_node_id("t-core"),
                group_review_id,
                "review:task:t-api",
                "review:task:t-core",
            ],
        )
        self.assertEqual(
            [
                item["outcome"]["summary"]
                for item in context["upstreamLoopResults"]
            ],
            [
                "t-api completed.",
                "t-core completed.",
                "g-service review completed.",
                "t-api review completed.",
                "t-core review completed.",
            ],
        )

    def test_reviews_progress_recursively_from_groups_to_delivery(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(recursive_hierarchy())
        root_id = prepared["rootId"]
        minute = 2

        def complete(node_id: str) -> None:
            nonlocal minute
            operation_id = f"op-{node_id.replace(':', '-')}"
            frontier = get_graph_frontier(
                root=self.root,
                root_id=root_id,
                now=at(minute),
            )
            self.assertIn(
                node_id,
                [item["nodeId"] for item in frontier["readyLoops"]],
            )
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="recursive-agent",
                receiver_context_id=f"context-{node_id}",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success(f"{node_id} completed."),
                now=at(minute + 1),
            )
            minute += 2

        ordered_loops = [
            loop_node_id("t-bootstrap"),
            "review:task:t-bootstrap",
            loop_node_id("t-model"),
            "review:task:t-model",
            loop_node_id("t-repository"),
            "review:task:t-repository",
            group_review_node_id("g-domain"),
            loop_node_id("t-api"),
            "review:task:t-api",
            group_review_node_id("g-backend"),
            loop_node_id("t-e2e"),
            "review:task:t-e2e",
            group_review_node_id("g-quality"),
            loop_node_id("t-docs"),
            "review:task:t-docs",
            group_review_node_id("g-root"),
        ]
        for node_id in ordered_loops:
            complete(node_id)

        delivery_review_id = review_node_id(root_id)
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=delivery_review_id,
        )
        self.assertEqual(
            {
                item["nodeId"]
                for item in context["upstreamLoopResults"]
            },
            set(ordered_loops),
        )
        complete(delivery_review_id)
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(minute),
        )
        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "RECORD_USER_CONFIRMATION",
                    "nodeId": f"confirm:{root_id}",
                }
            ],
        )

    def test_expired_worker_cannot_pause_or_submit_a_result(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent",
            operation_id="op-expired",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError):
            pause_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expired",
                now=at(40),
            )
        with self.assertRaises(GatedLoopError):
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-expired",
                outcome=success("Too late."),
                now=at(40),
            )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(40),
        )
        self.assertEqual(
            frontier["readyLoops"][0]["attempt"],
            2,
        )

    def test_loop_context_handoff_separates_expired_lease_recovery(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        policy = loop_execution_policy()
        self.assertEqual(
            policy,
            {
                "assuranceProfile": "STANDARD",
                "reviewTopology": "TASK_GROUP_AND_DELIVERY_REVIEWS",
                "contextIsolation": "REQUIRED",
                "dispatch": {
                    "preferredExecutor": "HOST_NATIVE_AGENT",
                    "noAgentCapacityBeforeClaim": (
                        "MANUAL_HANDOFF_WITHOUT_CLAIM"
                    ),
                },
                "claimedLoopHandoff": {
                    "trigger": "CONTEXT_OR_HOOK_PRESSURE",
                    "requiresLiveLease": True,
                    "action": "PAUSE_AND_HANDOFF",
                    "loopOutcome": "NONE",
                },
                "progressReporting": {
                    "tool": "report_loop_progress",
                    "language": "USER_PREFERRED",
                    "heartbeatRenewsLease": True,
                    "progressRenewsLease": False,
                    "reportAt": [
                        "LOOP_START",
                        "CODE_INSPECTION_COMPLETE",
                        "TEST_RUN",
                        "ISSUE_FOUND",
                        "FIX_APPLIED",
                        "REREVIEW",
                        "FINAL_VERIFICATION",
                    ],
                    "rawLogsAllowed": False,
                    "hiddenReasoningAllowed": False,
                },
                "providerRateLimit": {
                    "softStopTrigger": (
                        "KNOWN_REMAINING_CAPACITY_AT_OR_BELOW_5_PERCENT"
                    ),
                    "requiresLiveLease": True,
                    "requiresKnownResetAt": True,
                    "withResetAt": "PAUSE_UNTIL_RESET",
                    "executorScopeBeforeReset": (
                        "WAIT_FOR_EXECUTOR_NATIVE_WAKE"
                    ),
                    "hostScopeBeforeReset": "WAIT_FOR_HOST_NATIVE_WAKE",
                    "nativeWake": {
                        "claudeCode": "SESSION_ONE_SHOT_CRON",
                        "codexDesktop": "THREAD_SCHEDULED_TASK",
                    },
                    "atReset": (
                        "AGENT_RELOADS_FRONTIER_AND_REDISPATCHES"
                    ),
                    "sameAttempt": True,
                    "loopOutcome": "NONE",
                    "hard429": {
                        "action": "TRIP_HOST_CAPACITY_BREAKER",
                        "hostCallback": "MODEL_EXTERNAL_HOST_ADAPTER",
                        "cancelRecurringMonitors": True,
                        "scheduleWake": (
                            "HOST_NATIVE_ONE_SHOT_AT_RESET"
                        ),
                    },
                },
                "expiredLeaseRecovery": {
                    "action": "ADVANCE_GRAPH",
                    "pauseAllowed": False,
                    "reuseOperationId": False,
                },
                "receivingContext": {
                    "reuseFrozenGraph": True,
                    "reloadViaMcp": True,
                },
            },
        )
        self.assertNotIn("capacityPressure", repr(policy))

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        dispatch_action = next(
            action
            for action in frontier["actions"]
            if action["action"] == "DISPATCH_LOOP"
        )
        self.assertEqual(
            dispatch_action["executionPolicy"],
            policy,
        )

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        self.assertEqual(context["executionPolicy"], policy)
        self.assertEqual(
            context["completionPolicy"],
            loop_completion_policy(),
        )
        self.assertEqual(
            context["completionPolicy"]["actionableFinding"],
            "RESOLVE_AND_REEVALUATE_IN_CURRENT_LOOP",
        )
        self.assertEqual(
            context["completionPolicy"]["payloadRole"],
            "GOALS_CONSTRAINTS_AND_KNOWN_ACCEPTANCE_INPUT",
        )
        self.assertEqual(
            context["completionPolicy"]["reviewCycle"],
            "FIND_RESOLVE_VERIFY_AND_REREVIEW_UNTIL_TERMINAL",
        )
        self.assertEqual(
            context["completionPolicy"]["reviewFindings"],
            {
                "resultField": "reviewFindings",
                "severities": ["P0", "P1", "P2"],
                "p0p1": "RESOLVE_AND_REREVIEW_BEFORE_SUCCEEDED",
                "p2": "ALWAYS_LIST_IN_ACCEPTANCE_REPORT",
            },
        )
        self.assertEqual(
            context["humanArtifacts"],
            {
                "taskBaseline": (
                    f".layered-delivery/{root_id}/"
                    f"{WORK_ITEM_DIRECTORY}/t-service/baseline.md"
                ),
                "workItem": {
                    "kind": "TASK",
                    "baseline": (
                        f".layered-delivery/{root_id}/"
                        f"{WORK_ITEM_DIRECTORY}/t-service/baseline.md"
                    ),
                    "progress": (
                        f".layered-delivery/{root_id}/"
                        f"{WORK_ITEM_DIRECTORY}/t-service/progress.md"
                    ),
                    "acceptance": (
                        f".layered-delivery/{root_id}/"
                        f"{WORK_ITEM_DIRECTORY}/t-service/acceptance.md"
                    ),
                },
            },
        )
        self.assertEqual(
            context["rules"],
            {
                "payloadIsOpaqueToScheduler": True,
                "internalGateAndSkillPolicyOwnedByLoop": True,
                "implementationPlanMayAdaptWithinLoop": True,
                "actionableFindingsStayInsideLoop": True,
                "skillHintsAreAdvisory": True,
                "selectSkillsAtRuntime": True,
                "prioritizeApplicableSkillHints": True,
                "returnOnlyStandardLoopOutcome": True,
                "coordinatorMustNotExecuteLoopInline": True,
                "accessOnlyAuthorizedProjectScopes": True,
            },
        )

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-original",
            operation_id="op-original",
            now=at(3),
        )
        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-original",
            now=at(4),
        )
        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(paused["executionPolicy"], policy)
        self.assertEqual(
            paused["handoff"]["resumeSequence"],
            [
                "graph_frontier",
                "resume_loop",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ],
        )
        self.assertTrue(paused["handoff"]["reuseFrozenGraph"])
        self.assertFalse(paused["handoff"]["reprepare"])
        self.assertFalse(paused["handoff"]["refreeze"])

        paused_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(
            [item["nodeId"] for item in paused_frontier["pausedLoops"]],
            [node_id],
        )
        self.assertIn(
            {
                "action": "RESUME_LOOP_IN_INDEPENDENT_CONTEXT",
                "nodeId": node_id,
                "executionPolicy": policy,
            },
            paused_frontier["actions"],
        )

        resumed = resume_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            now=at(6),
        )
        self.assertEqual(resumed["status"], "READY")
        self.assertEqual(resumed["executionPolicy"], policy)
        self.assertIn("REDISPATCH", resumed["nextAction"])
        ready_frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(7),
        )
        self.assertEqual(ready_frontier["pausedLoops"], [])
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready_frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )

    def test_light_assurance_keeps_safety_but_reduces_process_reporting(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "The actual change is confined to one internal helper "
                    "with targeted tests and no boundary impact."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        dispatch_action = next(
            action
            for action in frontier["actions"]
            if action["action"] == "DISPATCH_LOOP"
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        execution_policy = context["executionPolicy"]
        completion_policy = context["completionPolicy"]

        self.assertEqual(execution_policy["assuranceProfile"], "LIGHT")
        self.assertEqual(
            execution_policy["reviewTopology"],
            "NO_INDEPENDENT_REVIEW_LOOPS",
        )
        self.assertEqual(
            execution_policy["progressReporting"]["reportAt"],
            ["ISSUE_FOUND", "FINAL_VERIFICATION"],
        )
        self.assertTrue(
            execution_policy["progressReporting"][
                "shortLoopMayReportOnlyFinal"
            ]
        )
        self.assertEqual(
            execution_policy["contextIsolation"],
            "REQUIRED",
        )
        self.assertEqual(
            dispatch_action["executionPolicy"],
            execution_policy,
        )
        self.assertEqual(
            completion_policy["verificationScope"],
            "TARGETED_FOR_DECLARED_CHANGE",
        )
        self.assertEqual(
            completion_policy["reviewCycle"],
            "FOCUSED_REVIEW_RESOLVE_VERIFY_AND_REREVIEW_IF_NEEDED",
        )
        self.assertEqual(
            completion_policy["reviewFindings"]["p0p1"],
            "RESOLVE_AND_REREVIEW_BEFORE_SUCCEEDED",
        )

    def test_light_delivery_completes_without_independent_review_loops(
        self,
    ) -> None:
        hierarchy = task_hierarchy()
        hierarchy["delivery"].update(
            {
                "assuranceProfile": "LIGHT",
                "assuranceRationale": (
                    "The actual diff changes one internal helper, keeps all "
                    "interfaces stable, and has a focused passing test."
                ),
                "reviewLoop": None,
            }
        )
        hierarchy["root"]["reviewLoop"] = None
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="light-agent",
            operation_id="op-light",
            now=at(2),
        )
        report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-light",
            phase="VERIFYING",
            summary_zh="Focused verification passed for the local change.",
            tests={"passed": 1, "failed": 0, "skipped": 0, "total": 1},
            now=at(3),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-light",
            outcome=success("Light change and focused verification completed."),
            now=at(4),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(5),
        )
        self.assertEqual(frontier["readyLoops"], [])
        self.assertIn(
            "RECORD_USER_CONFIRMATION",
            [action["action"] for action in frontier["actions"]],
        )
        completed = record_user_confirmation(
            root=self.root,
            root_id=root_id,
            confirmed=True,
            confirmed_by="human",
            summary="Accepted the focused change.",
            now=at(6),
        )
        self.assertEqual(completed["status"], "COMPLETED")
        event_types = [
            event["eventType"]
            for event in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
        ]
        self.assertEqual(event_types.count("LOOP_SUCCEEDED"), 1)
        self.assertNotIn("review:task:t-service", repr(frontier))
        acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        self.assertIn("LIGHT 保障档不创建 Delivery Review Loop", acceptance)

    def test_rate_limited_loop_waits_until_reset_then_redispatches(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(20).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-rate-limited",
            operation_id="op-rate-limited",
            now=at(3),
        )

        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-rate-limited",
            resume_at=reset_at,
            capacity_scope="EXECUTOR",
            now=at(4),
        )

        self.assertEqual(paused["status"], "PAUSED")
        self.assertEqual(paused["resumeAt"], reset_at)
        self.assertEqual(
            paused["nextAction"],
            "WAIT_FOR_EXECUTOR_CAPACITY",
        )
        self.assertEqual(
            paused["handoff"]["resumeSequence"],
            [
                "workspace_status",
                "graph_frontier",
                "loop_context",
                "dispatch_loop",
            ],
        )
        progress = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "t-service"
            / "progress.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "等待至 2026-07-29 16:20:00 由 Agent 恢复派遣",
            progress,
        )
        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        rebuilt_node = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(rebuilt_node["status"], "PAUSED")
        self.assertEqual(rebuilt_node["resumeAt"], reset_at)
        self.assertIsNone(rebuilt_node["leaseExpiresAt"])
        self.assertIsNone(rebuilt_node["finishedAt"])

        waiting = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(10),
        )
        self.assertEqual(waiting["nextWakeAt"], reset_at)
        self.assertEqual(
            waiting["pausedLoops"],
            [
                {
                    "nodeId": node_id,
                    "kind": "TASK_LOOP",
                    "workItemId": "t-service",
                    "attempt": 1,
                    "previousOwner": "agent-rate-limited",
                    "previousOperationId": "op-rate-limited",
                    "resumeAt": reset_at,
                    "capacityScope": "EXECUTOR",
                }
            ],
        )
        self.assertEqual(
            waiting["actions"],
            [
                {
                    "action": "WAIT_FOR_EXECUTOR_CAPACITY",
                    "nodeId": node_id,
                    "resumeAt": reset_at,
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )

        ready = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        self.assertIsNone(ready["nextWakeAt"])
        self.assertEqual(ready["pausedLoops"], [])
        self.assertEqual(ready["readyLoops"][0]["attempt"], 1)
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        auto_resumed = [
            event
            for event in graph_events(
                root=self.root,
                root_id=root_id,
            )["events"]
            if event["eventType"] == "NODE_AUTO_RESUMED"
        ]
        self.assertEqual(len(auto_resumed), 1)
        self.assertEqual(
            auto_resumed[0]["payload"],
            {"resumeAt": reset_at},
        )

    def test_rate_limited_loop_can_resume_early_with_alternate_agent(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(20).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-rate-limited",
            operation_id="op-rate-limited-alternate",
            now=at(3),
        )
        pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-rate-limited-alternate",
            resume_at=reset_at,
            capacity_scope="EXECUTOR",
            now=at(4),
        )

        resumed = resume_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            now=at(6),
        )
        self.assertEqual(resumed["status"], "READY")
        alternate = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-independent-alternate",
            operation_id="op-independent-alternate",
            now=at(7),
        )
        self.assertEqual(alternate["owner"], "agent-independent-alternate")
        state = graph_status(root=self.root, root_id=root_id)
        current = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(current["attempt"], 1)
        self.assertIsNone(current["resumeAt"])

    def test_rate_limit_pause_requires_a_future_reset_time(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-rate-limited",
            operation_id="op-invalid-reset",
            now=at(3),
        )

        for invalid in (
            "not-a-timestamp",
            at(4).isoformat().replace("+00:00", "Z"),
        ):
            with self.subTest(resume_at=invalid):
                with self.assertRaises(GatedLoopError) as caught:
                    pause_loop(
                        root=self.root,
                        root_id=root_id,
                        node_id=node_id,
                        operation_id="op-invalid-reset",
                        resume_at=invalid,
                        capacity_scope="EXECUTOR",
                        now=at(4),
                    )
                self.assertEqual(
                    caught.exception.code,
                    "SCHEDULER_RESUME_TIME_INVALID",
                )

        state = graph_status(root=self.root, root_id=root_id)
        current = next(
            item
            for item in state["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(current["status"], "CLAIMED")

    def test_host_rate_limit_waits_for_host_native_wake(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(20).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="host-native-agent",
            operation_id="op-host-rate-limit",
            now=at(3),
        )

        paused = pause_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-host-rate-limit",
            resume_at=reset_at,
            capacity_scope="HOST",
            now=at(4),
        )
        self.assertEqual(paused["nextAction"], "WAIT_FOR_HOST_CAPACITY")
        waiting = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(10),
        )
        self.assertEqual(
            waiting["actions"],
            [
                {
                    "action": "WAIT_FOR_HOST_CAPACITY",
                    "nodeId": node_id,
                    "resumeAt": reset_at,
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )
        self.assertEqual(
            waiting["pausedLoops"][0]["capacityScope"],
            "HOST",
        )
        ready = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )

    def test_hard_429_trips_host_breaker_after_worker_stops(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        reset_at = at(40).isoformat().replace("+00:00", "Z")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            model_id="claude-opus",
            operation_id="op-hard-429",
            now=at(3),
        )

        tripped = report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=reset_at,
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-hard-429",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )
        waiting = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(35),
        )

        self.assertEqual(tripped["status"], "OPEN")
        self.assertTrue(tripped["cancelRecurringMonitors"])
        self.assertEqual(tripped["wakeMode"], "HOST_NATIVE_ONE_SHOT")
        self.assertEqual(waiting["nextWakeAt"], reset_at)
        self.assertEqual(
            waiting["actions"],
            [
                {
                    "action": "WAIT_FOR_HOST_CAPACITY",
                    "resetAt": reset_at,
                    "capacityKey": "claude-code:default",
                    "affectedNodeIds": [node_id],
                    "cancelRecurringMonitors": True,
                    "wakeMode": "HOST_NATIVE_ONE_SHOT",
                }
            ],
        )
        current = graph_status(root=self.root, root_id=root_id)
        current_node = next(
            item for item in current["nodes"] if item["nodeId"] == node_id
        )
        self.assertEqual(current_node["status"], "PAUSED")
        replayed = report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=reset_at,
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-hard-429",
            reason="HTTP 429 quota exhausted",
            now=at(31),
        )
        self.assertTrue(replayed["idempotentReplay"])
        repository = SchedulerRepository(self.root)
        with repository.transaction() as connection:
            connection.execute("DELETE FROM host_capacity_breakers")
        rebuilt_open = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(rebuilt_open["executionMode"], "active")
        self.assertEqual(
            rebuilt_open["hostCapacity"]["capacityKey"],
            "claude-code:default",
        )
        with repository.read() as connection:
            rebuilt_breaker = repository.open_host_capacity_breaker(
                connection,
                agent_id="claude-code",
                at=at(35).isoformat().replace("+00:00", "Z"),
            )
        self.assertIsNotNone(rebuilt_breaker)

        ready = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(40),
        )
        self.assertNotIn("hostCapacity", ready)
        self.assertIn(
            node_id,
            [
                action["nodeId"]
                for action in ready["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        rebuilt_restored = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        self.assertNotIn("hostCapacity", rebuilt_restored)

    def test_hard_quota_report_rejects_unbounded_reset_horizon(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            model_id="claude-opus",
            receiver_context_id="claude-context",
            operation_id="op-hard-quota-horizon",
            now=at(3),
        )
        with self.assertRaises(GatedLoopError) as caught:
            report_host_capacity_exhausted(
                root=self.root,
                root_id=prepared["rootId"],
                node_id=node_id,
                reset_at=at(30 + 25 * 60).isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                host_adapter_id="claude-code",
                receiver_context_id="claude-context",
                report_id="report-too-far",
                reason="HTTP 429 quota exhausted",
                now=at(30),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_HOST_CAPACITY_REPORT_INVALID",
        )

    def test_rebuild_does_not_overwrite_newer_global_capacity_report(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            model_id="claude-opus",
            receiver_context_id="claude-worker",
            operation_id="op-stale-open",
            now=at(3),
        )
        report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=at(40).isoformat().replace("+00:00", "Z"),
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-stale-open",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )
        repository = SchedulerRepository(self.root)
        newer_reset = at(60).isoformat().replace("+00:00", "Z")
        newer_reported = at(50).isoformat().replace("+00:00", "Z")
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE host_capacity_breakers SET reset_at = ?, "
                "report_id = 'report-newer-open', status = 'OPEN', "
                "reported_at = ?, restored_at = NULL, "
                "reason = 'newer host report' "
                "WHERE capacity_key = 'claude-code:default'",
                (newer_reset, newer_reported),
            )

        rebuild_graph_run(root=self.root, root_id=root_id)

        with repository.read() as connection:
            breaker = connection.execute(
                "SELECT * FROM host_capacity_breakers WHERE "
                "capacity_key = 'claude-code:default'"
            ).fetchone()
        self.assertEqual(breaker["report_id"], "report-newer-open")
        self.assertEqual(breaker["reset_at"], newer_reset)
        self.assertEqual(breaker["status"], "OPEN")

    def test_rebuild_old_restore_does_not_clear_newer_global_breaker(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-worker",
            agent_id="claude-code",
            model_id="claude-opus",
            receiver_context_id="claude-worker",
            operation_id="op-stale-restore",
            now=at(3),
        )
        report_host_capacity_exhausted(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            reset_at=at(40).isoformat().replace("+00:00", "Z"),
            host_adapter_id="claude-code",
            receiver_context_id="claude-worker",
            report_id="report-stale-restore",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )
        get_graph_frontier(root=self.root, root_id=root_id, now=at(40))
        repository = SchedulerRepository(self.root)
        newer_reset = at(60).isoformat().replace("+00:00", "Z")
        newer_reported = at(50).isoformat().replace("+00:00", "Z")
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE host_capacity_breakers SET reset_at = ?, "
                "report_id = 'report-newer-after-restore', "
                "status = 'OPEN', reported_at = ?, restored_at = NULL, "
                "reason = 'newer host report' "
                "WHERE capacity_key = 'claude-code:default'",
                (newer_reset, newer_reported),
            )

        rebuild_graph_run(root=self.root, root_id=root_id)

        with repository.read() as connection:
            breaker = connection.execute(
                "SELECT * FROM host_capacity_breakers WHERE "
                "capacity_key = 'claude-code:default'"
            ).fetchone()
        self.assertEqual(
            breaker["report_id"],
            "report-newer-after-restore",
        )
        self.assertEqual(breaker["reset_at"], newer_reset)
        self.assertEqual(breaker["status"], "OPEN")

    def test_hard_quota_breaker_pauses_same_agent_across_deliveries(
        self,
    ) -> None:
        deliveries = []
        for delivery_id, task_id in (
            ("d-first", "t-first"),
            ("d-second", "t-second"),
        ):
            workspace = Path(self.root, delivery_id)
            workspace.mkdir()
            prepared = prepare_hierarchy(
                root=self.root,
                hierarchy=delivery_task_hierarchy(delivery_id, task_id),
                workspace_root=str(workspace),
                now=at(0),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=delivery_id,
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )
            dispatch_loop(
                root=self.root,
                root_id=delivery_id,
                node_id=loop_node_id(task_id),
                owner=f"claude-{task_id}",
                agent_id="claude-code",
                model_id="claude-opus",
                receiver_context_id=f"context-{task_id}",
                operation_id=f"op-{task_id}",
                now=at(3),
            )
            deliveries.append((delivery_id, task_id))

        report_host_capacity_exhausted(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
            reset_at=at(40).isoformat().replace("+00:00", "Z"),
            host_adapter_id="claude-code",
            receiver_context_id="context-t-first",
            report_id="report-cross-delivery",
            reason="HTTP 429 quota exhausted",
            now=at(30),
        )

        for delivery_id, task_id in deliveries:
            node = next(
                item
                for item in graph_status(
                    root=self.root,
                    root_id=delivery_id,
                )["nodes"]
                if item["nodeId"] == loop_node_id(task_id)
            )
            self.assertEqual(node["status"], "PAUSED")

    def test_timed_pause_requires_explicit_capacity_scope(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="host-native-agent",
            operation_id="op-missing-capacity-scope",
            now=at(3),
        )

        with self.assertRaises(GatedLoopError) as caught:
            pause_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-missing-capacity-scope",
                resume_at=at(20).isoformat().replace("+00:00", "Z"),
                now=at(4),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_CAPACITY_SCOPE_INVALID",
        )

    def test_group_review_context_links_group_work_item_projections(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(group_hierarchy())
        root_id = prepared["rootId"]

        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=group_review_node_id("g-service"),
        )

        item_prefix = (
            f".layered-delivery/{root_id}/"
            f"{WORK_ITEM_DIRECTORY}/g-service"
        )
        self.assertEqual(
            context["humanArtifacts"],
            {
                "workItem": {
                    "kind": "GROUP",
                    "baseline": f"{item_prefix}/baseline.md",
                    "progress": f"{item_prefix}/progress.md",
                    "acceptance": f"{item_prefix}/acceptance.md",
                }
            },
        )

    def test_task_work_item_progress_and_acceptance_follow_run_state(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        item_root = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "t-service"
        )
        baseline_before = (item_root / "baseline.md").read_bytes()

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-task",
            agent_id="codex",
            model_id="gpt-5.6-sol",
            operation_id="op-task-projection",
            now=at(2),
        )
        claimed = graph_status(root=self.root, root_id=root_id)
        claimed_node = next(
            item
            for item in claimed["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(claimed_node["agentId"], "codex")
        self.assertEqual(claimed_node["modelId"], "gpt-5.6-sol")

        running_progress = (item_root / "progress.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            (
                "| TASK | 执行中 | codex | gpt-5.6-sol | "
                "agent-task | 1 |"
            ),
            running_progress,
        )

        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-task-projection",
            outcome={
                "status": "SUCCEEDED",
                "summary": "任务实现与验证已完成。",
                "result": {"evidence": "全部自动化检查通过"},
            },
            now=at(3),
        )
        rebuild_graph_run(root=self.root, root_id=root_id)
        rebuilt = graph_status(root=self.root, root_id=root_id)
        rebuilt_node = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(rebuilt_node["agentId"], "codex")
        self.assertEqual(rebuilt_node["modelId"], "gpt-5.6-sol")

        completed_progress = (item_root / "progress.md").read_text(
            encoding="utf-8"
        )
        acceptance = (item_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            (
                "| 阶段 | 当前进度 | 执行代理 | 执行模型 | "
                "认领身份 | 执行轮次 | "
                "最近更新时间（UTC+8） | 结果摘要 |"
            ),
            completed_progress,
        )
        self.assertIn(
            (
                "| TASK | 已成功 | codex | gpt-5.6-sol | "
                "agent-task | 1 |"
            ),
            completed_progress,
        )
        self.assertNotIn("\n- 当前进度：", completed_progress)
        self.assertIn("任务实现与验证已完成。", completed_progress)
        self.assertIn(
            (
                "| 当前进度 | 认领身份 | 执行轮次 | "
                "结束时间（UTC+8） | 结果摘要 |"
            ),
            acceptance,
        )
        self.assertIn("全部自动化检查通过", acceptance)
        self.assertEqual(
            (item_root / "baseline.md").read_bytes(),
            baseline_before,
        )

    def test_review_findings_are_classified_in_acceptance_reports(
        self,
    ) -> None:
        hierarchy = group_hierarchy()
        prepared = self.prepare_and_freeze(hierarchy)
        root_id = prepared["rootId"]
        for minute, item_id in ((2, "t-api"), (6, "t-core")):
            node_id = loop_node_id(item_id)
            operation_id = f"op-{item_id}-severity"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                owner="task-agent",
                receiver_context_id=f"context-{item_id}-severity",
                operation_id=operation_id,
                now=at(minute),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id=operation_id,
                outcome=success(f"{item_id} completed."),
                now=at(minute + 1),
            )
            task_review_id = f"review:task:{item_id}"
            task_review_operation = f"op-{item_id}-task-review-severity"
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=task_review_id,
                owner="task-review-agent",
                receiver_context_id=(
                    f"context-{item_id}-task-review-severity"
                ),
                operation_id=task_review_operation,
                now=at(minute + 2),
            )
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=task_review_id,
                operation_id=task_review_operation,
                outcome=success(f"{item_id} task review completed."),
                now=at(minute + 3),
            )

        review_id = group_review_node_id("g-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=review_id,
            owner="review-agent",
            receiver_context_id="context-group-review-severity",
            operation_id="op-review-severity",
            now=at(10),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=review_id,
            operation_id="op-review-severity",
            outcome={
                "status": "SUCCEEDED",
                "summary": "P0/P1 已修复，P2 已记录。",
                "result": {
                    "reviewFindings": [
                        {
                            "severity": "P0",
                            "summary": "关键数据可能丢失",
                            "status": "RESOLVED",
                            "resolution": "修复字段映射并完成回归。",
                            "evidence": "数据链路测试通过",
                        },
                        {
                            "severity": "P1",
                            "summary": "异常分支缺少覆盖",
                            "status": "RESOLVED",
                            "resolution": "补充异常测试并复审。",
                            "evidence": "新增测试通过",
                        },
                        {
                            "severity": "P2",
                            "summary": "导出任务日志不足",
                            "status": "ACCEPTED",
                            "resolution": "作为非阻断改进项保留。",
                            "evidence": "不影响本次验收",
                        },
                    ],
                    "verification": {"tests": "passed"},
                },
            },
            now=at(11),
        )

        group_acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / WORK_ITEM_DIRECTORY
            / "g-service"
            / "acceptance.md"
        ).read_text(encoding="utf-8")
        delivery_acceptance = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "acceptance.md"
        ).read_text(encoding="utf-8")

        self.assertIn("#### Review 问题分级", group_acceptance)
        self.assertIn("- P0：1 项，未关闭 0 项", group_acceptance)
        self.assertIn("- P1：1 项，未关闭 0 项", group_acceptance)
        self.assertIn(
            "- P2：1 项（必须逐项列示）",
            group_acceptance,
        )
        self.assertIn(
            "| 级别 | 问题 | 状态 | 处置 | 证据 |",
            group_acceptance,
        )
        self.assertIn("关键数据可能丢失", group_acceptance)
        self.assertIn("异常分支缺少覆盖", group_acceptance)
        self.assertIn("导出任务日志不足", group_acceptance)
        self.assertIn("已修复", group_acceptance)
        self.assertIn("已接受", group_acceptance)
        self.assertEqual(
            group_acceptance.count("导出任务日志不足"),
            1,
        )
        self.assertIn(
            "[查看](children/t-api/acceptance.md)",
            group_acceptance,
        )
        self.assertIn(
            "[查看](children/t-core/acceptance.md)",
            group_acceptance,
        )
        self.assertNotIn("opaque-to-scheduler", group_acceptance)

        self.assertIn("## 根工作项验收", delivery_acceptance)
        self.assertIn(
            "P0/P1 已修复，P2 已记录。",
            delivery_acceptance,
        )
        self.assertIn(
            f"[查看]({WORK_ITEM_DIRECTORY}/g-service/acceptance.md)",
            delivery_acceptance,
        )
        self.assertNotIn("关键数据可能丢失", delivery_acceptance)
        self.assertNotIn("异常分支缺少覆盖", delivery_acceptance)
        self.assertNotIn("导出任务日志不足", delivery_acceptance)

    def test_infrastructure_failure_retries_but_loop_block_does_not(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-infra-1",
            now=at(2),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-infra-1",
            outcome={
                "status": "BLOCKED",
                "summary": "Worker transport failed.",
                "result": {},
            },
            failure_class="RETRYABLE_INFRA",
            now=at(3),
        )
        self.assertTrue(result["retried"])
        self.assertEqual(result["nextAttempt"], 2)
        self.assertEqual(result["schedulerStatus"], "READY")

        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-2",
            operation_id="op-domain-2",
            now=at(4),
        )
        result = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-domain-2",
            outcome={
                "status": "BLOCKED",
                "summary": "Loop needs external authority.",
                "result": {"request": "approve vendor contract"},
            },
            failure_class="EXTERNAL_AUTHORITY",
            now=at(5),
        )
        self.assertFalse(result["retried"])
        self.assertEqual(result["schedulerStatus"], "BLOCKED")
        self.assertEqual(
            graph_status(
                root=self.root,
                root_id=root_id,
            )["status"],
            "BLOCKED",
        )

    def test_blocked_outcome_requires_explicit_failure_class(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="review-agent",
            operation_id="op-premature-block",
            now=at(2),
        )

        with self.assertRaises(GatedLoopError) as caught:
            record_loop_result(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-premature-block",
                outcome={
                    "status": "BLOCKED",
                    "summary": "A correctable Review finding remains.",
                    "result": {"finding": "implementation defect"},
                },
                now=at(3),
            )

        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_FAILURE_CLASS_REQUIRED",
        )
        self.assertIn(
            "internal correction and reevaluation",
            caught.exception.message,
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
        )
        self.assertEqual(context["status"], "CLAIMED")

    def test_resource_claims_serialize_independent_loops(self) -> None:
        prepared = self.prepare_and_freeze(parallel_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="agent-api",
            operation_id="op-api",
            now=at(2),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )
        core = next(
            item
            for item in frontier["readyLoops"]
            if item["nodeId"] == loop_node_id("t-core")
        )
        self.assertEqual(
            core["resourceConflicts"],
            [loop_node_id("t-api")],
        )
        self.assertNotIn(
            loop_node_id("t-core"),
            [
                item.get("nodeId")
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_LOOP"
            ],
        )
        with self.assertRaises(GatedLoopError):
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id("t-core"),
                owner="agent-core",
                operation_id="op-core",
                now=at(3),
            )

    def test_resource_claims_serialize_loops_across_deliveries(self) -> None:
        first_workspace = Path(self.root, "worktree-first")
        second_workspace = Path(self.root, "worktree-second")
        first_workspace.mkdir()
        second_workspace.mkdir()
        claim = ["project:erp/environment:shared"]
        prepared = []
        for delivery_id, task_id, workspace in (
            ("d-first", "t-first", first_workspace),
            ("d-second", "t-second", second_workspace),
        ):
            current = call_tool(
                "prepare_hierarchy",
                {
                    "hierarchy": delivery_task_hierarchy(
                        delivery_id,
                        task_id,
                        claims=claim,
                    )
                },
                root=self.root,
                workspace_root=str(workspace),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=current["rootId"],
                expected_delivery_revision=1,
                expected_hierarchy_fingerprint=(
                    current["hierarchyFingerprint"]
                ),
                authorized_project_ids=[],
                confirmed=True,
                confirmed_by="human",
            )
            prepared.append(current)

        first_reservation = reserve_loop(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
        )
        first_attestation = attest_loop_receiver(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
            receiver_context_id="context-first",
            parent_context_id="codex-parent",
            host_adapter_id="codex",
            dispatch_reservation_id=first_reservation[
                "dispatchReservationId"
            ],
        )
        call_tool(
            "dispatch_loop",
            {
                "root_id": "d-first",
                "node_id": loop_node_id("t-first"),
                "owner": "agent-first",
                "agent_id": first_reservation["agentId"],
                "model_id": first_reservation["modelId"],
                "dispatch_mode": first_reservation["dispatchMode"],
                "dispatch_transport": first_reservation[
                    "dispatchTransport"
                ],
                "dispatch_reservation_id": first_reservation[
                    "dispatchReservationId"
                ],
                "dispatch_reasoning_class": first_reservation[
                    "dispatchReasoningClass"
                ],
                "dispatch_decision_fingerprint": first_reservation[
                    "dispatchDecisionFingerprint"
                ],
                "receiver_context_id": "context-first",
                "receiver_attestation_id": first_attestation[
                    "receiverAttestationId"
                ],
                "operation_id": "op-first",
            },
            root=self.root,
            workspace_root=str(first_workspace),
            trusted_host_adapter="codex",
        )
        frontier = call_tool(
            "graph_frontier",
            {"root_id": "d-second"},
            root=self.root,
            workspace_root=str(second_workspace),
        )
        second_ready = next(
            item
            for item in frontier["readyLoops"]
            if item["nodeId"] == loop_node_id("t-second")
        )
        self.assertEqual(
            second_ready["resourceConflicts"],
            [f"d-first/{loop_node_id('t-first')}"],
        )
        self.assertFalse(
            any(
                action["action"] == "DISPATCH_LOOP"
                for action in frontier["actions"]
            )
        )
        with self.assertRaises(GatedLoopError) as caught:
            second_reservation = reserve_loop(
                root=self.root,
                root_id="d-second",
                node_id=loop_node_id("t-second"),
            )
            second_attestation = attest_loop_receiver(
                root=self.root,
                root_id="d-second",
                node_id=loop_node_id("t-second"),
                receiver_context_id="context-second",
                parent_context_id="codex-parent",
                host_adapter_id="codex",
                dispatch_reservation_id=second_reservation[
                    "dispatchReservationId"
                ],
            )
            call_tool(
                "dispatch_loop",
                {
                    "root_id": "d-second",
                    "node_id": loop_node_id("t-second"),
                    "owner": "agent-second",
                    "agent_id": second_reservation["agentId"],
                    "model_id": second_reservation["modelId"],
                    "dispatch_mode": second_reservation[
                        "dispatchMode"
                    ],
                    "dispatch_transport": second_reservation[
                        "dispatchTransport"
                    ],
                    "dispatch_reservation_id": second_reservation[
                        "dispatchReservationId"
                    ],
                    "dispatch_reasoning_class": second_reservation[
                        "dispatchReasoningClass"
                    ],
                    "dispatch_decision_fingerprint": second_reservation[
                        "dispatchDecisionFingerprint"
                    ],
                    "receiver_context_id": "context-second",
                    "receiver_attestation_id": second_attestation[
                        "receiverAttestationId"
                    ],
                    "operation_id": "op-second",
                },
                root=self.root,
                workspace_root=str(second_workspace),
                trusted_host_adapter="codex",
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_RESOURCE_CONFLICT",
        )
        self.assertEqual(
            caught.exception.details["conflictingRootId"],
            "d-first",
        )

    def test_expired_cross_delivery_claim_does_not_block_dispatch(
        self,
    ) -> None:
        first_workspace = Path(self.root, "worktree-first")
        second_workspace = Path(self.root, "worktree-second")
        first_workspace.mkdir()
        second_workspace.mkdir()
        claim = ["project:erp/environment:shared"]
        for delivery_id, task_id, workspace in (
            ("d-first", "t-first", first_workspace),
            ("d-second", "t-second", second_workspace),
        ):
            prepared = prepare_hierarchy(
                root=self.root,
                hierarchy=delivery_task_hierarchy(
                    delivery_id,
                    task_id,
                    claims=claim,
                ),
                workspace_root=str(workspace),
                now=at(0),
            )
            freeze_hierarchy(
                root=self.root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=(
                    prepared["hierarchyFingerprint"]
                ),
                confirmed=True,
                confirmed_by="human",
                now=at(1),
            )

        dispatch_loop(
            root=self.root,
            root_id="d-first",
            node_id=loop_node_id("t-first"),
            owner="agent-first",
            operation_id="op-first-expiring",
            now=at(2),
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id="d-second",
            now=at(33),
        )
        self.assertIn(
            loop_node_id("t-second"),
            [
                action.get("nodeId")
                for action in frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        dispatched = dispatch_loop(
            root=self.root,
            root_id="d-second",
            node_id=loop_node_id("t-second"),
            owner="agent-second",
            operation_id="op-second-after-expiry",
            now=at(33),
        )
        self.assertEqual(dispatched["status"], "CLAIMED")

    def test_unstarted_task_requirement_can_be_unfrozen_and_refrozen(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        task_id = "t-service"
        initial = graph_status(root=self.root, root_id=root_id)
        self.assertEqual(
            initial["taskRequirements"],
            [
                {
                    "taskId": task_id,
                    "revision": 1,
                    "status": "FROZEN",
                    "updatedAt": at(1).isoformat().replace(
                        "+00:00",
                        "Z",
                    ),
                }
            ],
        )

        unfrozen = call_tool(
            "unfreeze_task_requirement",
            {
                "root_id": root_id,
                "task_id": task_id,
                "expected_revision": 1,
                "authorized_by": "human",
                "reason": "Clarify the acceptance boundary.",
            },
            root=self.root,
        )
        self.assertEqual(
            unfrozen["taskRequirement"]["status"],
            "UNFROZEN",
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(2),
        )
        self.assertNotIn(
            loop_node_id(task_id),
            [
                action.get("nodeId")
                for action in frontier["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        self.assertIn(
            {
                "action": "REFREEZE_TASK_REQUIREMENT",
                "nodeId": loop_node_id(task_id),
                "taskId": task_id,
                "revision": 1,
            },
            frontier["actions"],
        )
        with self.assertRaises(GatedLoopError) as caught:
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id(task_id),
                owner="agent",
                operation_id="op-unfrozen",
                now=at(2),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_REQUIREMENT_UNFROZEN",
        )

        requirement = unfrozen["taskRequirement"]["requirement"]
        requirement["title"] = "Run clarified service task"
        requirement["summary"] = "Implement the clarified requirement."
        requirement["payload"] = {
            "goal": "Deliver the revised result.",
            "acceptance": ["The revised acceptance boundary is verified."],
        }
        refrozen = call_tool(
            "refreeze_task_requirement",
            {
                "root_id": root_id,
                "task_id": task_id,
                "expected_revision": 1,
                "requirement": requirement,
                "confirmed_by": "human",
            },
            root=self.root,
        )
        self.assertEqual(
            refrozen["taskRequirement"]["revision"],
            2,
        )
        self.assertEqual(
            refrozen["taskRequirement"]["status"],
            "FROZEN",
        )
        context = loop_context(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id(task_id),
        )
        self.assertEqual(
            context["loop"]["payload"],
            requirement["payload"],
        )
        self.assertEqual(
            context["taskRequirement"]["revision"],
            2,
        )
        resumed = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(3),
        )
        self.assertIn(
            loop_node_id(task_id),
            [
                action.get("nodeId")
                for action in resumed["actions"]
                if action["action"] == "DISPATCH_LOOP"
            ],
        )
        baseline = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "work-items"
            / task_id
            / "baseline.md"
        ).read_text(encoding="utf-8")
        self.assertIn("需求版本：2", baseline)
        self.assertIn("需求状态：已冻结", baseline)
        self.assertIn(requirement["title"], baseline)
        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )
        self.assertEqual(
            rebuilt["taskRequirements"][0]["revision"],
            2,
        )
        self.assertEqual(
            rebuilt["taskRequirements"][0]["status"],
            "FROZEN",
        )

    def test_started_task_requirement_cannot_be_unfrozen(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-service"),
            owner="agent",
            operation_id="op-started-requirement",
            now=at(2),
        )
        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "unfreeze_task_requirement",
                {
                    "root_id": root_id,
                    "task_id": "t-service",
                    "expected_revision": 1,
                    "authorized_by": "human",
                    "reason": "Too late.",
                },
                root=self.root,
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_ALREADY_STARTED",
        )

    def test_retried_task_requirement_cannot_be_unfrozen(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent",
            operation_id="op-started-before-retry",
            now=at(2),
        )
        retried = record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-started-before-retry",
            outcome={
                "status": "BLOCKED",
                "summary": "Worker transport failed.",
                "result": {},
            },
            failure_class="RETRYABLE_INFRA",
            now=at(3),
        )
        self.assertTrue(retried["retried"])
        self.assertEqual(retried["schedulerStatus"], "READY")

        with self.assertRaises(GatedLoopError) as caught:
            call_tool(
                "unfreeze_task_requirement",
                {
                    "root_id": root_id,
                    "task_id": "t-service",
                    "expected_revision": 1,
                    "authorized_by": "human",
                    "reason": "This task already entered development.",
                },
                root=self.root,
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_TASK_ALREADY_STARTED",
        )

    def test_initial_frontier_reserves_shared_resources_deterministically(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(parallel_hierarchy())
        frontier = get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )

        dispatch_actions = [
            item
            for item in frontier["actions"]
            if item["action"] == "DISPATCH_LOOP"
        ]
        self.assertEqual(
            dispatch_actions,
            [
                {
                    "action": "DISPATCH_LOOP",
                    "nodeId": loop_node_id("t-api"),
                    "loopRef": "project/java-service-loop@1",
                    "executionPolicy": loop_execution_policy(),
                }
            ],
        )
        ready = {
            item["nodeId"]: item
            for item in frontier["readyLoops"]
        }
        self.assertEqual(
            ready[loop_node_id("t-api")]["resourceConflicts"],
            [],
        )
        self.assertEqual(
            ready[loop_node_id("t-core")]["resourceConflicts"],
            [loop_node_id("t-api")],
        )

    def test_initial_frontier_dispatches_disjoint_ready_loops(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        frontier = get_graph_frontier(
            root=self.root,
            root_id=prepared["rootId"],
            now=at(2),
        )

        self.assertEqual(
            [
                item["nodeId"]
                for item in frontier["actions"]
                if item["action"] == "DISPATCH_LOOP"
            ],
            [
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            ],
        )
        self.assertTrue(
            all(
                not item["resourceConflicts"]
                for item in frontier["readyLoops"]
            )
        )

    def test_replan_required_suppresses_new_dispatches(self) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            owner="agent-api",
            operation_id="op-api-replan",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=loop_node_id("t-api"),
            operation_id="op-api-replan",
            outcome={
                "status": "REPLAN_REQUIRED",
                "summary": "The frozen topology must change.",
                "result": {"reason": "new dependency"},
            },
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )

        self.assertEqual(
            frontier["actions"],
            [
                {
                    "action": "REPLAN_HIERARCHY",
                    "nodeId": loop_node_id("t-api"),
                }
            ],
        )
        self.assertIn(
            loop_node_id("t-core"),
            [item["nodeId"] for item in frontier["readyLoops"]],
        )
        with self.assertRaises(GatedLoopError) as caught:
            dispatch_loop(
                root=self.root,
                root_id=root_id,
                node_id=loop_node_id("t-core"),
                owner="agent-core",
                operation_id="op-stale-frontier",
                now=at(5),
            )
        self.assertEqual(
            caught.exception.code,
            "SCHEDULER_REPLAN_REQUIRED",
        )

    def test_prepare_projection_is_namespaced_and_auditable(
        self,
    ) -> None:
        hierarchy = auditable_recursive_hierarchy()
        hierarchy["root"]["skillHints"] = [
            skill_hint("springboot-tdd", "Prefer TDD when applicable.")
        ]
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        control = Path(self.root) / ".layered-delivery"
        projections = control / prepared["rootId"]
        artifact_prefix = f".layered-delivery/{prepared['rootId']}"
        nodes = hierarchy_nodes(hierarchy)
        task_nodes = [
            current
            for current in nodes
            if current["definition"]["kind"] == "TASK"
        ]
        item_paths = hierarchical_work_item_paths(hierarchy)
        expected_work_items = {}
        for current in nodes:
            definition = current["definition"]
            item_prefix = f"{artifact_prefix}/{item_paths[definition['id']]}"
            expected_work_items[definition["id"]] = {
                "kind": definition["kind"],
                "baseline": f"{item_prefix}/baseline.md",
                "progress": f"{item_prefix}/progress.md",
                "acceptance": f"{item_prefix}/acceptance.md",
            }
        expected_task_baselines = {
            current["definition"]["id"]: expected_work_items[
                current["definition"]["id"]
            ]["baseline"]
            for current in task_nodes
        }

        self.assertEqual(
            prepared["humanArtifacts"],
            {
                "workspaceOverview": ".layered-delivery/overview.md",
                "overview": f"{artifact_prefix}/overview.md",
                "baseline": f"{artifact_prefix}/baseline.md",
                "progress": f"{artifact_prefix}/progress.md",
                "acceptance": f"{artifact_prefix}/acceptance.md",
                "revisions": f"{artifact_prefix}/revisions.md",
                "taskBaselines": expected_task_baselines,
                "workItems": expected_work_items,
            },
        )
        self.assertTrue((control / "scheduler.db").is_file())
        self.assertTrue((control / "overview.md").is_file())
        for filename in (
            "overview.md",
            "baseline.md",
            "progress.md",
            "acceptance.md",
        ):
            self.assertTrue((projections / filename).is_file())
        self.assertFalse((projections / "interfaces.md").exists())
        for filename in (
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            self.assertFalse((control / filename).exists())
            self.assertFalse((projections / filename).exists())
        overview = (projections / "overview.md").read_text(
            encoding="utf-8"
        )
        delivery_baseline = (projections / "baseline.md").read_text(
            encoding="utf-8"
        )
        acceptance = (projections / "acceptance.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("hierarchyFingerprint", overview)
        self.assertNotIn(prepared["hierarchyFingerprint"], overview)
        self.assertIn(
            prepared["hierarchyFingerprint"],
            delivery_baseline,
        )
        self.assertNotIn("graphFingerprint", overview)
        self.assertNotIn(prepared["graphFingerprint"], overview)
        self.assertIn(prepared["graphFingerprint"], delivery_baseline)
        self.assertIn(
            (
                "| 交付标识 | 标题 | 当前状态 | TASK 进度 | "
                "GROUP 数量 | 最近更新（UTC+8） |"
            ),
            overview,
        )
        self.assertIn(
            (
                f"| {prepared['rootId']} | {hierarchy['delivery']['title']} "
                "| 待冻结 | 已完成 0/6 | 4 |"
            ),
            overview,
        )
        self.assertIn("[需求基线](baseline.md)", overview)
        self.assertIn("[执行进展](progress.md)", overview)
        self.assertIn("[验收记录](acceptance.md)", overview)
        self.assertNotIn("[接口契约](interfaces.md)", overview)
        self.assertNotIn(
            "[查看接口契约](interfaces.md)",
            delivery_baseline,
        )
        self.assertIn("## GROUP/TASK 清单", delivery_baseline)
        self.assertIn(
            "| 层级路径 | 节点类型 | 上级 | 前置依赖 | "
            "标题 | 需求基线 | 执行进展 | 验收记录 | 接口契约 |",
            delivery_baseline,
        )
        self.assertIn("| 分组 |", delivery_baseline)
        self.assertIn("| 任务 |", delivery_baseline)
        self.assertIn("springboot-tdd", delivery_baseline)
        self.assertNotIn(hierarchy["delivery"]["summary"], overview)
        self.assertIn(
            hierarchy["delivery"]["summary"],
            delivery_baseline,
        )
        self.assertNotIn("```json", overview)
        self.assertNotIn("（PREPARED）", overview)

        workspace_overview = (control / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# 全部交付调度与进度总览", workspace_overview)
        self.assertIn("交付数量：1", workspace_overview)
        self.assertIn(
            (
                "| 交付标识 | 需求标题 | 当前状态 | "
                "最近更新（UTC+8） | 交付详情 |"
            ),
            workspace_overview,
        )
        self.assertNotIn("需求摘要", workspace_overview)
        self.assertNotIn("TASK 进度", workspace_overview)
        self.assertNotIn("GROUP 数量", workspace_overview)
        self.assertNotIn(hierarchy["delivery"]["summary"], workspace_overview)
        self.assertIn(prepared["rootId"], workspace_overview)
        self.assertIn(
            f"({prepared['rootId']}/overview.md)",
            workspace_overview,
        )
        self.assertIn("待冻结", workspace_overview)
        self.assertNotIn("PREPARED", workspace_overview)

        work_item_root = projections / WORK_ITEM_DIRECTORY
        self.assertTrue(work_item_root.is_dir())
        self.assertEqual(
            {
                path.name
                for path in work_item_root.iterdir()
                if path.is_dir()
            },
            {hierarchy["root"]["definition"]["id"]},
        )
        root_item = work_item_root / hierarchy["root"]["definition"]["id"]
        self.assertEqual(
            {
                path.name
                for path in (root_item / "children").iterdir()
                if path.is_dir()
            },
            {
                child["definition"]["id"]
                for child in hierarchy["root"]["children"]
            },
        )
        self.assertTrue(
            (root_item / "children" / "g-backend").is_dir()
        )
        self.assertTrue(
            (root_item / "children" / "g-quality").is_dir()
        )
        for current in nodes:
            definition = current["definition"]
            item_id = definition["id"]
            item_root = projections / item_paths[item_id]
            with self.subTest(work_item_id=item_id):
                self.assertIn(item_id, delivery_baseline)
                self.assertEqual(
                    {
                        path.name
                        for path in item_root.iterdir()
                        if path.is_file()
                    },
                    {
                        "baseline.md",
                        "progress.md",
                        "acceptance.md",
                    },
                )
                self.assertIn(
                    f"{item_paths[item_id]}/baseline.md",
                    delivery_baseline,
                )
                item_progress = (item_root / "progress.md").read_text(
                    encoding="utf-8"
                )
                item_acceptance = (
                    item_root / "acceptance.md"
                ).read_text(encoding="utf-8")
                self.assertNotIn("投影模板版本", item_progress)
                self.assertIn("|", item_progress)
                self.assertIn("未启动", item_progress)
                self.assertNotIn("\n- 当前进度：", item_progress)
                self.assertNotIn("投影模板版本", item_acceptance)
                if definition["kind"] == "TASK":
                    loop = definition["execution"]["loop"]
                    baseline = (item_root / "baseline.md").read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn("投影模板版本", baseline)
                    self.assertIn(
                        prepared["hierarchyFingerprint"],
                        baseline,
                    )
                    self.assertIn(
                        prepared["graphFingerprint"],
                        baseline,
                    )
                    self.assertIn(definition["summary"], baseline)
                    for dependency in definition["execution"][
                        "dependsOn"
                    ]:
                        self.assertIn(dependency, baseline)
                    self.assertIn(loop["ref"], baseline)
                    self.assertIn(
                        loop["resourceClaims"][0],
                        baseline,
                    )
                    self.assertIn(
                        loop["payload"]["rawAuditMarker"],
                        baseline,
                    )
                    self.assertIn("### 验收标准", baseline)
                    self.assertIn("### 业务规则", baseline)
                    self.assertIn("：是", baseline)
                    self.assertIn(
                        r"首行<br>\# 不能改变模板 \| \`原样文本\`",
                        baseline,
                    )
                    self.assertNotIn("```json", baseline)
                    self.assertNotIn('"acceptance"', baseline)
                    self.assertNotIn('"rawAuditMarker"', baseline)
                    self.assertIn("springboot-tdd", baseline)
                    self.assertNotIn("## 关联接口契约", baseline)
                    self.assertNotIn(definition["summary"], delivery_baseline)
                    self.assertNotIn(loop["ref"], overview)
                    self.assertNotIn(
                        loop["payload"]["rawAuditMarker"],
                        overview,
                    )
                else:
                    group_baseline = (
                        item_root / "baseline.md"
                    ).read_text(encoding="utf-8")
                    self.assertIn(
                        definition["summary"],
                        group_baseline,
                    )
                    for dependency in definition["decomposition"][
                        "dependsOn"
                    ]:
                        self.assertIn(dependency, group_baseline)
                    review = current["reviewLoop"]
                    self.assertIn(review["ref"], group_baseline)
                    self.assertIn(
                        review["resourceClaims"][0],
                        group_baseline,
                    )
                    self.assertIn(
                        review["payload"]["rawAuditMarker"],
                        group_baseline,
                    )
                    self.assertIn("### 审查重点", group_baseline)
                    for child in current["children"]:
                        child_id = child["definition"]["id"]
                        self.assertIn(
                            f"children/{child_id}/baseline.md",
                            group_baseline,
                        )
                    self.assertNotIn(
                        review["payload"]["rawAuditMarker"],
                        delivery_baseline,
                    )

        delivery_review = hierarchy["delivery"]["reviewLoop"]
        self.assertIn(delivery_review["ref"], delivery_baseline)
        self.assertIn(
            delivery_review["resourceClaims"][0],
            delivery_baseline,
        )
        self.assertIn(
            delivery_review["payload"]["rawAuditMarker"],
            delivery_baseline,
        )
        self.assertIn("##### 原始审计标记", delivery_baseline)
        self.assertIn(
            delivery_review["payload"]["rawAuditMarker"],
            acceptance,
        )
        root_item_id = hierarchy["root"]["definition"]["id"]
        self.assertIn("## 根工作项验收", acceptance)
        self.assertIn(
            (
                f"[查看]({WORK_ITEM_DIRECTORY}/{root_item_id}/"
                "acceptance.md)"
            ),
            acceptance,
        )
        for current in nodes:
            definition = current["definition"]
            if definition["kind"] == "TASK":
                self.assertNotIn(
                    (
                        definition["execution"]["loop"]["payload"][
                            "acceptance"
                        ][0]
                    ),
                    acceptance,
                )
            else:
                self.assertNotIn(
                    current["reviewLoop"]["payload"][
                        "rawAuditMarker"
                    ],
                    acceptance,
                )
        self.assertNotIn('"rawAuditMarker"', delivery_baseline)

        work_item_ids = {
            current["definition"]["id"]
            for current in nodes
        }
        for item_id in work_item_ids:
            self.assertFalse((control / item_id).exists())
            self.assertFalse((projections / item_id).exists())
        self.assertFalse(
            any(
                path.name == "development-plan.md"
                for path in control.rglob("*")
            )
        )

    def test_delivery_human_projections_separate_baseline_progress_and_acceptance(
        self,
    ) -> None:
        hierarchy = interface_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )

        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        baseline = (projection_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        progress = (projection_root / "progress.md").read_text(
            encoding="utf-8"
        )
        acceptance = (projection_root / "acceptance.md").read_text(
            encoding="utf-8"
        )
        task_root = (
            projection_root
            / WORK_ITEM_DIRECTORY
            / "t-service"
        )
        interfaces = (task_root / "interfaces.md").read_text(
            encoding="utf-8"
        )
        interface_directory = task_root / "interfaces"
        interface_documents = sorted(interface_directory.glob("*.md"))
        self.assertEqual(
            [path.name for path in interface_documents],
            [
                "001-http-post-api-orders.md",
                (
                    "002-dubbo-com-example-order-orderservice-"
                    "createorder.md"
                ),
                (
                    "003-grpc-order-v1-legacyorderservice-"
                    "getorder.md"
                ),
            ],
        )
        interface_details = {
            path.name: path.read_text(encoding="utf-8")
            for path in interface_documents
        }
        all_interface_details = "\n".join(interface_details.values())
        create_order_detail = interface_details[
            "001-http-post-api-orders.md"
        ]
        task_baseline = (task_root / "baseline.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("[需求基线](baseline.md)", overview)
        self.assertIn("[执行进展](progress.md)", overview)
        self.assertIn("[验收记录](acceptance.md)", overview)
        self.assertNotIn("[接口契约](interfaces.md)", overview)
        self.assertEqual(
            prepared["humanArtifacts"]["workItems"]["t-service"][
                "interfaces"
            ],
            (
                f".layered-delivery/{prepared['rootId']}/"
                f"{WORK_ITEM_DIRECTORY}/t-service/interfaces.md"
            ),
        )
        self.assertNotIn("interfaces", prepared["humanArtifacts"])
        self.assertNotIn("## TASK 执行进度", overview)
        self.assertNotIn("## GROUP 协调与审查", overview)

        self.assertIn("# 交付需求基线", baseline)
        self.assertIn(hierarchy["delivery"]["summary"], baseline)
        self.assertIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/baseline.md",
            baseline,
        )
        self.assertIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/interfaces.md",
            baseline,
        )

        self.assertIn("# 交付执行进展", progress)
        self.assertIn("t-service", progress)
        self.assertIn(
            (
                "| 层级路径 | 阶段 | 当前进度 | 执行代理 | 执行模型 | "
                "认领身份 | 执行轮次 | "
                "最近更新时间（UTC+8） | 结果摘要 | 节点进展 |"
            ),
            progress,
        )
        self.assertIn("| t-service | TASK | 未启动 |", progress)
        self.assertNotIn("\n- 当前进度：", progress)
        self.assertNotIn("Deliver one observable result.", progress)

        self.assertIn("# 交付验收记录", acceptance)
        self.assertIn(
            (
                "| 当前进度 | 认领身份 | 执行轮次 | "
                "结束时间（UTC+8） | 结果摘要 |"
            ),
            acceptance,
        )
        self.assertIn("The loop returns verified evidence.", acceptance)
        self.assertIn("最终用户确认", acceptance)

        self.assertIn("# TASK 接口契约", interfaces)
        for label in (
            "来源 TASK",
            "协议",
            "接口名称",
            "变更类型",
            "修改前调用标识",
            "修改后调用标识",
            "简介",
        ):
            with self.subTest(chinese_label=label):
                self.assertIn(label, interfaces)
        self.assertNotIn("## 接口详情", interfaces)
        self.assertNotIn("legacyCustomerNo", interfaces)
        self.assertIn("创建订单", interfaces)
        self.assertIn("修改", interfaces)
        self.assertIn("POST /api/v1/orders", interfaces)
        self.assertIn("POST /api/orders", interfaces)
        self.assertIn(
            "[创建订单](interfaces/001-http-post-api-orders.md)",
            interfaces,
        )
        self.assertIn("legacyCustomerNo", all_interface_details)
        self.assertIn("customerId", all_interface_details)
        self.assertIn("必填", create_order_detail)
        self.assertIn("类型", create_order_detail)
        self.assertIn("说明", create_order_detail)
        self.assertIn("字段路径", create_order_detail)
        self.assertIn("[返回接口清单](../interfaces.md)", create_order_detail)
        self.assertIn(
            (
                "| ~~legacyCustomerNo~~ | 删除 | ~~string~~ | "
                "~~是~~ | ~~原客户编号~~ |"
            ),
            create_order_detail,
        )
        self.assertIn(
            (
                "| customerId | 新增 | string | "
                "是 | 客户标识 |"
            ),
            create_order_detail,
        )
        self.assertIn(
            (
                "| quantity | 修改 | integer | 否 → 是 | "
                "商品数量 → 必须大于零的商品数量 |"
            ),
            create_order_detail,
        )
        self.assertIn(
            (
                "| channel | 未变 | string | 否 | 下单渠道 |"
            ),
            create_order_detail,
        )
        response_section = create_order_detail.split("## 出参", 1)[1]
        self.assertNotIn("必填", response_section)
        self.assertIn(
            "| ~~orderNo~~ | 删除 | ~~string~~ | ~~原订单编号~~ |",
            response_section,
        )
        self.assertIn(
            "| orderId | 新增 | string | 订单标识 |",
            response_section,
        )
        self.assertNotIn("— →", all_interface_details)
        self.assertNotIn("→ —", all_interface_details)
        self.assertIn("orderId", all_interface_details)
        self.assertIn("创建订单服务", interfaces)
        self.assertIn("新增", interfaces)
        self.assertIn("不适用 →", all_interface_details)
        self.assertIn(
            "com.example.order.OrderService.createOrder",
            interfaces,
        )
        self.assertIn("CreateOrderRequest", all_interface_details)
        self.assertIn("CreateOrderResponse", all_interface_details)
        self.assertIn("旧版订单查询服务", interfaces)
        self.assertIn("GRPC", interfaces)
        self.assertIn("删除", interfaces)
        self.assertIn(
            (
                "~~[旧版订单查询服务](interfaces/"
                "003-grpc-order-v1-legacyorderservice-getorder.md)~~"
            ),
            interfaces,
        )
        self.assertIn(
            "order.v1.LegacyOrderService/GetOrder",
            interfaces,
        )
        self.assertIn("LegacyOrderResponse", all_interface_details)
        self.assertIn("→ 不适用", all_interface_details)
        self.assertNotIn("#### 修改前", all_interface_details)
        self.assertNotIn("#### 修改后", all_interface_details)
        self.assertIn("## 入参", all_interface_details)
        self.assertIn("## 出参", all_interface_details)
        self.assertNotIn("```json", all_interface_details)
        self.assertNotIn("PREPARED", interfaces)
        self.assertIn(
            "[查看本 TASK 的接口契约](interfaces.md)",
            task_baseline,
        )
        self.assertNotIn("创建订单", task_baseline)
        self.assertNotIn("legacyCustomerNo", task_baseline)

    def test_workspace_status_backfills_projection_files_for_stored_deliveries(
        self,
    ) -> None:
        first = prepare_hierarchy(
            root=self.root,
            hierarchy=interface_hierarchy(),
            now=at(0),
        )
        second_hierarchy = task_hierarchy()
        second_hierarchy["delivery"].update(
            {
                "id": "d-older-projection",
                "title": "另一个历史交付",
                "summary": "验证全部已有 Delivery 都会补建投影。",
            }
        )
        second = prepare_hierarchy(
            root=self.root,
            hierarchy=second_hierarchy,
            now=at(1),
        )
        projection_roots = [
            Path(self.root) / ".layered-delivery" / prepared["rootId"]
            for prepared in (first, second)
        ]
        for projection_root in projection_roots:
            for filename in (
                "baseline.md",
                "progress.md",
                "acceptance.md",
            ):
                path = projection_root / filename
                if path.exists():
                    path.unlink()
            work_items = projection_root / WORK_ITEM_DIRECTORY
            if work_items.exists():
                shutil.rmtree(work_items)
            (projection_root / "overview.md").write_text(
                "# 旧版总览\n",
                encoding="utf-8",
            )

        status = workspace_status(root=self.root)

        self.assertEqual(status["status"], "PREPARED")
        self.assertEqual(status["rootId"], second["rootId"])
        for index, projection_root in enumerate(projection_roots):
            for filename in (
                "overview.md",
                "baseline.md",
                "progress.md",
                "acceptance.md",
            ):
                with self.subTest(
                    root_id=projection_root.name,
                    filename=filename,
                ):
                    content = (projection_root / filename).read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn("投影模板版本", content)
            item_root = (
                projection_root / WORK_ITEM_DIRECTORY / "t-service"
            )
            for filename in (
                "baseline.md",
                "progress.md",
                "acceptance.md",
            ):
                with self.subTest(
                    root_id=projection_root.name,
                    work_item_file=filename,
                ):
                    content = (item_root / filename).read_text(
                        encoding="utf-8"
                    )
                    self.assertNotIn("投影模板版本", content)
            interface_projection = item_root / "interfaces.md"
            if index == 0:
                self.assertNotIn(
                    "投影模板版本",
                    interface_projection.read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    len(list((item_root / "interfaces").glob("*.md"))),
                    3,
                )
            else:
                self.assertFalse(interface_projection.exists())
                self.assertFalse((item_root / "interfaces").exists())

    def test_reprepare_without_interfaces_removes_optional_projection(
        self,
    ) -> None:
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=interface_hierarchy(),
            now=at(0),
        )
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        task_root = (
            projection_root / WORK_ITEM_DIRECTORY / "t-service"
        )
        self.assertTrue((task_root / "interfaces.md").is_file())
        self.assertTrue((task_root / "interfaces").is_dir())

        replacement = prepare_hierarchy(
            root=self.root,
            hierarchy=task_hierarchy(),
            now=at(1),
        )

        self.assertNotIn(
            "interfaces",
            replacement["humanArtifacts"]["workItems"]["t-service"],
        )
        self.assertFalse((task_root / "interfaces.md").exists())
        self.assertFalse((task_root / "interfaces").exists())
        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        baseline = (projection_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        task_baseline = (task_root / "baseline.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("[接口契约](interfaces.md)", overview)
        self.assertNotIn(
            f"{WORK_ITEM_DIRECTORY}/t-service/interfaces.md",
            baseline,
        )
        self.assertNotIn("## 关联接口契约", task_baseline)

    def test_projection_set_is_fixed_and_rebuilt_from_sqlite(
        self,
    ) -> None:
        self.assertEqual(
            set(PROJECTION_TEMPLATES),
            {
                "overview.md",
                "baseline.md",
                "progress.md",
                "acceptance.md",
                "revisions.md",
            },
        )
        self.assertGreaterEqual(PROJECTION_TEMPLATE_VERSION, 7)
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=interface_hierarchy(),
            now=at(0),
        )
        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        workspace_overview_path = (
            Path(self.root) / ".layered-delivery" / "overview.md"
        )
        original_workspace_overview = (
            workspace_overview_path.read_bytes()
        )
        filenames = set(PROJECTION_TEMPLATES)
        original = {
            filename: (projection_root / filename).read_bytes()
            for filename in filenames
        }
        work_item_root = projection_root / WORK_ITEM_DIRECTORY
        original_work_items = {
            path.relative_to(work_item_root).as_posix(): path.read_bytes()
            for path in work_item_root.rglob("*")
            if path.is_file()
        }
        for filename in filenames:
            (projection_root / filename).write_text(
                f"agent-authored replacement: {filename}\n",
                encoding="utf-8",
            )
        for filename in original_work_items:
            (work_item_root / filename).write_text(
                f"agent-authored replacement: {filename}\n",
                encoding="utf-8",
            )
        (work_item_root / "stale-agent-file.md").write_text(
            "not controller data\n",
            encoding="utf-8",
        )
        workspace_overview_path.write_text(
            "agent-authored workspace summary\n",
            encoding="utf-8",
        )
        for filename in (
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            (projection_root / filename).write_text(
                "legacy machine projection\n",
                encoding="utf-8",
            )

        repository = SchedulerRepository(self.root)
        repository.write_projections(prepared["rootId"])

        rebuilt = {
            filename: (projection_root / filename).read_bytes()
            for filename in filenames
        }
        rebuilt_work_items = {
            path.relative_to(work_item_root).as_posix(): path.read_bytes()
            for path in work_item_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(rebuilt, original)
        self.assertEqual(
            workspace_overview_path.read_bytes(),
            original_workspace_overview,
        )
        self.assertEqual(rebuilt_work_items, original_work_items)
        self.assertNotIn(
            "stale-agent-file.md",
            rebuilt_work_items,
        )
        for filename in (
            "hierarchy.json",
            "graph.json",
            "state.json",
        ):
            self.assertFalse((projection_root / filename).exists())
        shutil.rmtree(work_item_root)
        work_item_root.write_text(
            "agent replaced the controller directory\n",
            encoding="utf-8",
        )
        repository.write_projections(prepared["rootId"])
        self.assertTrue(work_item_root.is_dir())
        self.assertEqual(
            {
                path.relative_to(work_item_root).as_posix(): path.read_bytes()
                for path in work_item_root.rglob("*")
                if path.is_file()
            },
            original_work_items,
        )
        self.assertNotIn(
            "投影模板版本",
            rebuilt["overview.md"].decode("utf-8"),
        )

    def test_reprepare_replaces_the_exact_work_item_projection_set(
        self,
    ) -> None:
        original_hierarchy = task_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=original_hierarchy,
            now=at(0),
        )
        work_item_root = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / WORK_ITEM_DIRECTORY
        )
        self.assertTrue(
            (work_item_root / "t-service" / "baseline.md").is_file()
        )

        replacement = task_hierarchy()
        replacement["root"]["definition"]["id"] = "t-replacement"
        replacement["root"]["definition"]["title"] = "Replacement task"
        replacement["root"]["definition"]["summary"] = (
            "Execute the replacement Task Loop."
        )
        updated = prepare_hierarchy(
            root=self.root,
            hierarchy=replacement,
            now=at(1),
        )

        self.assertEqual(updated["rootId"], prepared["rootId"])
        self.assertFalse((work_item_root / "t-service").exists())
        replacement_baseline = (
            work_item_root / "t-replacement" / "baseline.md"
        )
        self.assertTrue(replacement_baseline.is_file())
        self.assertTrue(
            (work_item_root / "t-replacement" / "progress.md").is_file()
        )
        self.assertTrue(
            (
                work_item_root
                / "t-replacement"
                / "acceptance.md"
            ).is_file()
        )
        self.assertIn(
            "Execute the replacement Task Loop.",
            replacement_baseline.read_text(encoding="utf-8"),
        )

    def test_concurrent_disjoint_dispatch_projection_does_not_regress(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        earlier_waiting = Event()
        release_earlier = Event()
        later_finished = Event()
        clock_lock = Lock()
        errors: list[BaseException] = []
        expected_machine_time = (
            at(3).isoformat().replace("+00:00", "Z")
        )
        clock_values = iter(
            [
                at(2).isoformat().replace("+00:00", "Z"),
                expected_machine_time,
            ]
        )
        original_transaction = SchedulerRepository.transaction

        def ordered_timestamp(now: object = None) -> str:
            del now
            with clock_lock:
                return next(clock_values)

        @contextmanager
        def coordinated_transaction(
            repository: SchedulerRepository,
        ):
            if current_thread().name == "earlier-dispatch":
                earlier_waiting.set()
                if not release_earlier.wait(timeout=5):
                    raise AssertionError(
                        "Timed out releasing the earlier dispatch"
                    )
            with original_transaction(repository) as connection:
                yield connection

        def claim(
            *,
            item_id: str,
            operation_id: str,
            finished: Event | None = None,
        ) -> None:
            try:
                dispatch_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=loop_node_id(item_id),
                    owner=current_thread().name,
                    operation_id=operation_id,
                )
            except BaseException as error:
                errors.append(error)
            finally:
                if finished is not None:
                    finished.set()

        with (
            patch(
                "hdg.graph_runtime.timestamp",
                new=ordered_timestamp,
            ),
            patch.object(
                SchedulerRepository,
                "transaction",
                new=coordinated_transaction,
            ),
        ):
            earlier = Thread(
                target=claim,
                kwargs={
                    "item_id": "t-api",
                    "operation_id": "op-concurrent-earlier",
                },
                name="earlier-dispatch",
            )
            later = Thread(
                target=claim,
                kwargs={
                    "item_id": "t-core",
                    "operation_id": "op-concurrent-later",
                    "finished": later_finished,
                },
                name="later-dispatch",
            )
            earlier.start()
            self.assertTrue(earlier_waiting.wait(timeout=5))
            later.start()
            try:
                self.assertTrue(later_finished.wait(timeout=5))
            finally:
                release_earlier.set()
            earlier.join(timeout=5)
            later.join(timeout=5)

        self.assertFalse(earlier.is_alive())
        self.assertFalse(later.is_alive())
        self.assertEqual(errors, [])

        run = SchedulerRepository(self.root).run(root_id)
        claimed_at = {
            node["nodeId"]: node["claimedAt"]
            for node in run["nodes"]
            if node["nodeId"]
            in {
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            }
        }
        self.assertEqual(
            set(claimed_at),
            {
                loop_node_id("t-api"),
                loop_node_id("t-core"),
            },
        )
        self.assertTrue(all(claimed_at.values()))
        self.assertEqual(run["updatedAt"], expected_machine_time)
        run_updated = datetime.fromisoformat(
            run["updatedAt"].replace("Z", "+00:00")
        )
        claimed_times = [
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in claimed_at.values()
        ]
        self.assertGreaterEqual(run_updated, max(claimed_times))

        projection_root = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
        )
        self.assertFalse((projection_root / "state.json").exists())
        human_time = at(3).astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S")
        overview = (projection_root / "overview.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(human_time, overview)
        self.assertNotIn("T08:03:00+08:00", overview)

    def test_delivery_ids_retain_separate_requirement_projections(
        self,
    ) -> None:
        first = task_hierarchy()
        second = deepcopy(first)
        second["delivery"].update(
            {
                "id": "d-secondary",
                "title": "第二个交付需求",
                "summary": "保留独立的需求投影目录。",
            }
        )

        first_prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=first,
            now=at(0),
        )
        second_prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=second,
            now=at(1),
        )

        control = Path(self.root) / ".layered-delivery"
        first_overview = (
            control / first_prepared["rootId"] / "overview.md"
        )
        second_overview = (
            control / second_prepared["rootId"] / "overview.md"
        )
        self.assertTrue(first_overview.is_file())
        self.assertTrue(second_overview.is_file())
        self.assertIn(
            first_prepared["rootId"],
            first_overview.read_text(encoding="utf-8"),
        )
        self.assertIn(
            second_prepared["rootId"],
            second_overview.read_text(encoding="utf-8"),
        )
        workspace_overview = (
            control / "overview.md"
        ).read_text(encoding="utf-8")
        self.assertIn("交付数量：2", workspace_overview)
        self.assertIn(first_prepared["rootId"], workspace_overview)
        self.assertIn(second_prepared["rootId"], workspace_overview)
        self.assertIn(
            f"({first_prepared['rootId']}/overview.md)",
            workspace_overview,
        )
        self.assertIn(
            f"({second_prepared['rootId']}/overview.md)",
            workspace_overview,
        )

    def test_frozen_projection_contains_runtime_progress(self) -> None:
        hierarchy = auditable_recursive_hierarchy()
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=hierarchy,
            now=at(0),
        )
        frozen = freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=at(1),
        )
        projections = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
        )
        overview = (projections / "overview.md").read_text(
            encoding="utf-8"
        )
        progress = (projections / "progress.md").read_text(
            encoding="utf-8"
        )
        acceptance = (projections / "acceptance.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual(frozen["status"], "ACTIVE")
        self.assertFalse((projections / "state.json").exists())
        self.assertIn(
            (
                f"| {prepared['rootId']} | "
                f"{hierarchy['delivery']['title']} | 运行中 |"
            ),
            overview,
        )
        self.assertNotIn("ACTIVE", overview)
        self.assertIn("运行状态：运行中", progress)
        statuses = {
            state["status"]
            for state in frozen["nodes"]
        }
        self.assertIn("READY", statuses)
        self.assertIn("PENDING", statuses)
        item_paths = hierarchical_work_item_paths(hierarchy)
        for state in frozen["nodes"]:
            with self.subTest(node_id=state["nodeId"]):
                node_id = state["nodeId"]
                status = STATUS_TEXT[state["status"]]
                if node_id.startswith("confirm:"):
                    self.assertIn(f"| {status} | 无 | 1 |", acceptance)
                    continue
                if node_id.startswith("loop:"):
                    item_id = node_id.removeprefix("loop:")
                    stage = "TASK"
                    path = item_paths[item_id].removeprefix(
                        f"{WORK_ITEM_DIRECTORY}/"
                    ).replace("/children/", "/")
                elif node_id.startswith("join:"):
                    item_id = node_id.removeprefix("join:")
                    stage = "GROUP 完成点"
                    path = item_paths[item_id].removeprefix(
                        f"{WORK_ITEM_DIRECTORY}/"
                    ).replace("/children/", "/")
                elif node_id.startswith("review:group:"):
                    item_id = node_id.removeprefix("review:group:")
                    stage = "GROUP Review"
                    path = item_paths[item_id].removeprefix(
                        f"{WORK_ITEM_DIRECTORY}/"
                    ).replace("/children/", "/")
                else:
                    path = hierarchy["delivery"]["id"]
                    stage = "Delivery Review"
                self.assertIn(
                    (
                        f"| {path} | {stage} | {status} | "
                        "无 | 无 | 无 | 1 |"
                    ),
                    progress,
                )

    def test_projection_labels_statuses_and_times_are_localized(
        self,
    ) -> None:
        prepared_at = datetime(
            2026,
            1,
            1,
            0,
            0,
            tzinfo=timezone.utc,
        )
        prepared = prepare_hierarchy(
            root=self.root,
            hierarchy=task_hierarchy(),
            now=prepared_at,
        )
        overview_path = (
            Path(self.root)
            / ".layered-delivery"
            / prepared["rootId"]
            / "overview.md"
        )
        baseline_path = overview_path.with_name("baseline.md")
        progress_path = overview_path.with_name("progress.md")
        prepared_overview = overview_path.read_text(encoding="utf-8")
        prepared_baseline = baseline_path.read_text(encoding="utf-8")

        self.assertIn(
            "2026-01-01 08:00:00",
            prepared_overview,
        )
        self.assertNotIn(
            "2026-01-01T08:00:00+08:00",
            prepared_overview,
        )
        self.assertIn(
            "| d-service | Deliver d-service | 待冻结 |",
            prepared_overview,
        )
        self.assertIn("| 任务 |", prepared_baseline)
        self.assertNotIn("PREPARED", prepared_overview)

        freeze_hierarchy(
            root=self.root,
            root_id=prepared["rootId"],
            expected_hierarchy_fingerprint=(
                prepared["hierarchyFingerprint"]
            ),
            confirmed=True,
            confirmed_by="human",
            now=prepared_at + timedelta(minutes=1),
        )
        dispatch_loop(
            root=self.root,
            root_id=prepared["rootId"],
            node_id=loop_node_id("t-service"),
            owner="agent-local-time",
            operation_id="op-local-time",
            now=prepared_at + timedelta(minutes=2),
        )
        active_overview = overview_path.read_text(encoding="utf-8")
        active_progress = progress_path.read_text(encoding="utf-8")

        self.assertIn(
            "| d-service | Deliver d-service | 运行中 |",
            active_overview,
        )
        self.assertIn(
            (
                "| t-service | TASK | 执行中 | codex | gpt-test | "
                "agent-local-time | 1 | "
                "2026-01-01 08:02:00 |"
            ),
            active_progress,
        )
        for machine_status in ("FROZEN", "ACTIVE", "CLAIMED"):
            self.assertNotIn(machine_status, active_overview)
            self.assertNotIn(machine_status, active_progress)
        self.assertNotRegex(
            active_progress,
            r"2026-01-01T\d{2}:\d{2}:\d{2}(?:Z|\+08:00)",
        )

    def test_loop_progress_is_audited_without_renewing_its_lease(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        claimed = dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="claude-reviewer",
            agent_id="claude-code",
            model_id="sonnet",
            actual_model_id="glm-5.2",
            operation_id="op-progress",
            now=at(2),
        )

        reported = report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-progress",
            phase="TESTING",
            summary_zh="正在运行测试，准备检查接口兼容性。",
            completed_zh=["已完成代码检查"],
            next_step_zh="检查接口兼容性",
            progress_percent=70,
            tests={
                "passed": 74,
                "failed": 0,
                "skipped": 0,
                "total": 74,
            },
            now=at(2) + timedelta(seconds=30),
        )

        self.assertEqual(reported["phaseZh"], "运行测试")
        self.assertEqual(reported["leaseExpiresAt"], claimed["leaseExpiresAt"])
        events = graph_events(root=self.root, root_id=root_id)["events"]
        progress_event = next(
            event
            for event in events
            if event["eventType"] == "LOOP_PROGRESS_REPORTED"
        )
        self.assertEqual(progress_event["payload"]["summaryZh"], "正在运行测试，准备检查接口兼容性。")
        self.assertFalse(
            any(event["eventType"] == "LOOP_HEARTBEAT" for event in events)
        )

        status = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(2) + timedelta(seconds=48),
        )
        state = next(
            item for item in status["nodes"] if item["nodeId"] == node_id
        )
        self.assertEqual(state["progress"]["progressPercent"], 70)
        self.assertEqual(state["modelId"], "sonnet")
        self.assertEqual(state["actualModelId"], "glm-5.2")
        self.assertEqual(state["actualModelSource"], "HOST_REPORTED")
        table = status["progressMonitor"]["markdownTable"]
        self.assertIn("| 节点 | 执行器 | 当前阶段 |", table)
        self.assertIn("t-service · 任务执行", table)
        self.assertIn(
            "第 1 轮 · claude-code · 原生 sonnet → 实际 glm-5.2",
            table,
        )
        self.assertIn("运行测试", table)
        self.assertIn("74/74 通过", table)
        self.assertIn("准备检查接口兼容性", table)
        self.assertIn("尚无独立心跳", table)
        self.assertNotIn("LOOP_PROGRESS_REPORTED", table)
        self.assertNotIn("op-progress", table)

        heartbeat_missing = graph_status(
            root=self.root,
            root_id=root_id,
            now=at(2) + timedelta(seconds=91),
        )["progressMonitor"]
        self.assertEqual(
            heartbeat_missing["alerts"][0]["code"],
            "HEARTBEAT_MISSING",
        )
        self.assertIn("已开始但无独立心跳", heartbeat_missing["markdownTable"])

        projection = (
            Path(self.root)
            / ".layered-delivery"
            / root_id
            / "progress.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## 实时进度监控", projection)
        self.assertIn("74/74 通过", projection)
        self.assertNotIn("op-progress", projection)

        rebuilt = rebuild_graph_run(root=self.root, root_id=root_id)
        rebuilt_state = next(
            item for item in rebuilt["nodes"] if item["nodeId"] == node_id
        )
        self.assertEqual(
            rebuilt_state["progress"]["summaryZh"],
            "正在运行测试，准备检查接口兼容性。",
        )

    def test_loop_progress_accepts_user_language_and_requires_live_claim(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-progress",
            operation_id="op-progress-validation",
            now=at(2),
        )

        reported = report_loop_progress(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-progress-validation",
            phase="INSPECTING",
            summary_zh="Inspecting source code.",
            completed_zh=["Loaded the relevant modules."],
            next_step_zh="Run the focused tests.",
            now=at(2) + timedelta(seconds=1),
        )
        self.assertEqual(reported["summaryZh"], "Inspecting source code.")
        self.assertEqual(
            reported["completedZh"],
            ["Loaded the relevant modules."],
        )
        self.assertEqual(reported["nextStepZh"], "Run the focused tests.")

        with self.assertRaises(GatedLoopError) as caught:
            report_loop_progress(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-progress-validation",
                phase="INSPECTING",
                summary_zh="Invalid\x01progress",
                now=at(2) + timedelta(seconds=1),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_PROGRESS_INVALID")

        with self.assertRaises(GatedLoopError) as caught:
            report_loop_progress(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-other",
                phase="INSPECTING",
                summary_zh="正在检查源代码。",
                now=at(2) + timedelta(seconds=2),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_OPERATION_INVALID")

        with self.assertRaises(GatedLoopError) as caught:
            report_loop_progress(
                root=self.root,
                root_id=root_id,
                node_id=node_id,
                operation_id="op-progress-validation",
                phase="VERIFYING",
                summary_zh="正在执行最终验证。",
                now=at(33),
            )
        self.assertEqual(caught.exception.code, "SCHEDULER_OPERATION_INVALID")

    def test_progress_monitor_localizes_silence_and_recovers_expired_lease(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        operation_id = "op-progress-monitor"
        claimed_at = at(2)
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="background-agent",
            agent_id="claude-code",
            model_id="glm-5.2",
            operation_id=operation_id,
            now=claimed_at,
        )

        not_started = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(seconds=91),
        )["progressMonitor"]
        self.assertEqual(not_started["alerts"][0]["code"], "SUSPECT_NOT_STARTED")
        self.assertIn("疑似未启动", not_started["markdownTable"])

        heartbeat_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id=operation_id,
            now=claimed_at + timedelta(minutes=2),
        )
        alive_without_progress = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=5, seconds=1),
        )["progressMonitor"]
        self.assertEqual(
            alive_without_progress["alerts"][0]["code"],
            "ALIVE_WITHOUT_PROGRESS",
        )
        self.assertIn("存活但无可见进展", alive_without_progress["markdownTable"])

        suspect_lost = graph_status(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=10),
        )["progressMonitor"]
        self.assertEqual(suspect_lost["alerts"][0]["code"], "SUSPECT_LOST")
        self.assertIn("疑似失联", suspect_lost["markdownTable"])

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=claimed_at + timedelta(minutes=33),
        )
        self.assertEqual(frontier["progressMonitor"]["recommendedPollSeconds"], 30)
        events = graph_events(root=self.root, root_id=root_id)["events"]
        expired = next(
            event
            for event in events
            if event["eventType"] == "CLAIM_LEASE_EXPIRED"
        )
        self.assertEqual(expired["payload"]["failureClass"], "WORKER_LOST")

    def test_materialized_state_can_be_rebuilt_from_events(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-rebuild",
            now=at(2),
        )
        repository = SchedulerRepository(self.root)
        run_id = repository.run(root_id)["runId"]
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE node_runs SET status = 'BLOCKED' "
                "WHERE run_id = ? AND node_id = ?",
                (run_id, node_id),
            )

        rebuilt = rebuild_graph_run(
            root=self.root,
            root_id=root_id,
        )

        state = next(
            item
            for item in rebuilt["nodes"]
            if item["nodeId"] == node_id
        )
        self.assertEqual(state["status"], "CLAIMED")
        self.assertGreater(rebuilt["rebuiltFromEvents"], 0)

    def test_rebuild_does_not_overwrite_a_concurrent_claim(self) -> None:
        prepared = self.prepare_and_freeze(
            disjoint_parallel_hierarchy()
        )
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-api")
        operation_id = "op-during-rebuild"
        snapshot_captured = Event()
        release_snapshot = Event()
        dispatch_finished = Event()
        errors: list[BaseException] = []
        original_events = SchedulerRepository.events

        def held_event_snapshot(
            repository: SchedulerRepository,
            *args: object,
            **kwargs: object,
        ) -> list[dict]:
            page = original_events(repository, *args, **kwargs)
            if (
                current_thread().name == "rebuild-thread"
                and not snapshot_captured.is_set()
            ):
                snapshot_captured.set()
                if not release_snapshot.wait(timeout=5):
                    raise AssertionError(
                        "Timed out releasing the rebuild event snapshot"
                    )
            return page

        def rebuild() -> None:
            try:
                rebuild_graph_run(root=self.root, root_id=root_id)
            except BaseException as error:
                errors.append(error)

        def claim() -> None:
            try:
                dispatch_loop(
                    root=self.root,
                    root_id=root_id,
                    node_id=node_id,
                    owner="concurrent-agent",
                    operation_id=operation_id,
                    now=at(2),
                )
            except BaseException as error:
                errors.append(error)
            finally:
                dispatch_finished.set()

        with patch.object(
            SchedulerRepository,
            "events",
            new=held_event_snapshot,
        ):
            rebuild_thread = Thread(
                target=rebuild,
                name="rebuild-thread",
            )
            dispatch_thread = Thread(
                target=claim,
                name="dispatch-during-rebuild",
            )
            rebuild_thread.start()
            self.assertTrue(snapshot_captured.wait(timeout=5))
            dispatch_thread.start()
            try:
                dispatch_finished.wait(timeout=1)
            finally:
                release_snapshot.set()
            rebuild_thread.join(timeout=5)
            dispatch_thread.join(timeout=5)

        self.assertFalse(rebuild_thread.is_alive())
        self.assertFalse(dispatch_thread.is_alive())
        self.assertEqual(errors, [])

        events = graph_events(root=self.root, root_id=root_id)["events"]
        claim_event = next(
            event
            for event in events
            if event["eventType"] == "LOOP_CLAIMED"
            and event["operationId"] == operation_id
        )
        run = graph_status(root=self.root, root_id=root_id)
        state = next(
            item
            for item in run["nodes"]
            if item["nodeId"] == node_id
        )

        self.assertEqual(state["status"], "CLAIMED")
        self.assertEqual(state["operationId"], operation_id)
        self.assertEqual(
            state["claimedAt"],
            claim_event["recordedAt"],
        )
        self.assertGreaterEqual(
            datetime.fromisoformat(
                run["updatedAt"].replace("Z", "+00:00")
            ),
            datetime.fromisoformat(
                claim_event["recordedAt"].replace("Z", "+00:00")
            ),
        )

    def test_loop_cancellation_blocks_the_run_with_a_frontier_action(
        self,
    ) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        node_id = loop_node_id("t-service")
        dispatch_loop(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            owner="agent-1",
            operation_id="op-cancelled-loop",
            now=at(2),
        )
        record_loop_result(
            root=self.root,
            root_id=root_id,
            node_id=node_id,
            operation_id="op-cancelled-loop",
            outcome={
                "status": "CANCELLED",
                "summary": "Internal Loop was cancelled.",
                "result": {},
            },
            now=at(3),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(4),
        )
        self.assertEqual(frontier["status"], "BLOCKED")
        self.assertIn(
            {
                "action": "RESOLVE_LOOP_CANCELLATION",
                "nodeId": node_id,
            },
            frontier["actions"],
        )

    def test_cancelled_graph_is_a_stable_terminal_frontier(self) -> None:
        prepared = self.prepare_and_freeze(task_hierarchy())
        root_id = prepared["rootId"]
        cancelled = cancel_graph_run(
            root=self.root,
            root_id=root_id,
            cancelled_by="human",
            reason="Requirement withdrawn.",
            now=at(2),
        )

        frontier = get_graph_frontier(
            root=self.root,
            root_id=root_id,
            now=at(20),
        )
        after = graph_status(root=self.root, root_id=root_id)

        self.assertEqual(frontier["status"], "CANCELLED")
        self.assertEqual(frontier["actions"], [])
        self.assertEqual(frontier["blockedLoops"], [])
        self.assertEqual(after["status"], "CANCELLED")
        self.assertEqual(after["updatedAt"], cancelled["updatedAt"])
        self.assertEqual(after["cancelledAt"], cancelled["cancelledAt"])


class RemovedCouplingTests(unittest.TestCase):
    def test_old_scope_gate_skill_and_plan_fields_are_rejected(
        self,
    ) -> None:
        source = task_hierarchy()
        definition = source["root"]["definition"]
        for field, value in (
            ("scope", ["src/**"]),
            ("gateLevel", "FULL"),
            ("requiredSkills", []),
            ("developmentPlan", {}),
        ):
            candidate = deepcopy(source)
            candidate["root"]["definition"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(GatedLoopError):
                    validate_hierarchy_definition(candidate)

    def test_mcp_surface_contains_only_outer_scheduler_tools(
        self,
    ) -> None:
        tools = tool_definitions()
        names = {tool["name"] for tool in tools}
        self.assertIn("dispatch_loop", names)
        self.assertIn("record_loop_result", names)
        self.assertNotIn("dispatch_task", names)
        self.assertNotIn("gate_item", names)
        self.assertNotIn("record_skill_activation", names)
        self.assertNotIn("record_skill_conformance", names)
        self.assertNotIn("remediate_task", names)
        pause_tool = next(
            tool for tool in tools if tool["name"] == "pause_loop"
        )
        self.assertIn("live lease", pause_tool["description"])
        self.assertNotIn(
            "capacity handoff",
            pause_tool["description"],
        )
        context_tool = next(
            tool for tool in tools if tool["name"] == "loop_context"
        )
        self.assertIn(
            "expired-lease recovery",
            context_tool["description"],
        )
        self.assertIn(
            "completion policy",
            context_tool["description"],
        )
        result_tool = next(
            tool for tool in tools if tool["name"] == "record_loop_result"
        )
        self.assertIn(
            "correctable finding",
            result_tool["description"],
        )
        self.assertIn(
            "Required when outcome.status is BLOCKED",
            result_tool["inputSchema"]["properties"]["failure_class"][
                "description"
            ],
        )
        self.assertTrue(
            {
                "execute_sql",
                "query_sqlite",
                "write_projection",
                "refresh_projections",
            }.isdisjoint(names)
        )
        forbidden_arguments = {
            "sql",
            "query",
            "template",
            "filename",
            "content",
            "projection",
        }
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("sql", tool["name"].lower())
                self.assertNotIn(
                    "projection",
                    tool["name"].lower(),
                )
                self.assertTrue(
                    forbidden_arguments.isdisjoint(
                        tool["inputSchema"]["properties"]
                    )
                )


if __name__ == "__main__":
    unittest.main()
