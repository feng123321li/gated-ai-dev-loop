from __future__ import annotations

from contextlib import contextmanager

from copy import deepcopy

from datetime import datetime, timedelta, timezone

import json

from pathlib import Path

import shutil

import sqlite3

import subprocess

from tempfile import TemporaryDirectory

from threading import Event, Lock, Thread, current_thread

import unittest

from unittest.mock import patch

import hdg.graph_runtime as graph_runtime

from hdg.dispatch_planning import plan_dispatch_batch

from hdg.errors import GatedLoopError

from hdg.graph_frontier import get_graph_frontier

from hdg.graph_model import (
    group_review_node_id,
    join_node_id,
    loop_node_id,
    review_node_id,
    task_review_node_id,
)

from hdg.graph_runtime import (
    archive_delivery,
    cancel_graph_run,
    graph_events,
    graph_status,
    heartbeat_loop,
    loop_context,
    pause_loop,
    report_loop_progress,
    rebuild_graph_run,
    record_loop_result,
    record_user_confirmation,
    resume_loop,
    dispatch_loop as runtime_dispatch_loop,
)

from hdg.jsonio import fingerprint

from hdg.loop_contracts import (
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
    select_execution_mode,
    start_manual_handoff,
    workspace_status,
)

from hdg.repository import SchedulerRepository

from .test_loop_architecture import (
    group_hierarchy,
    loop_descriptor,
    node,
    recursive_hierarchy,
    skill_hint,
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

def review_success(
    loop_kind: str,
    summary: str = "Review completed.",
    *,
    findings: list[dict] | None = None,
) -> dict:
    result = {
        "validationDecision": {
            "decision": "TARGETED_RERUN",
            "reusedEvidenceRefs": [],
            "executedEvidenceRefs": ["review-boundary-check"],
            "riskTriggers": ["Independent review boundary."],
            "rationale": "The layer-owned acceptance boundary was checked.",
        },
        "reviewFindings": findings or [],
    }
    if loop_kind == "TASK_REVIEW_LOOP":
        result["taskAcceptance"] = {
            "acceptanceChecks": [
                {
                    "acceptancePoint": "The frozen TASK contract is met.",
                    "status": "SATISFIED",
                    "evidenceRefs": ["review-boundary-check"],
                }
            ],
            "localBehavior": "VERIFIED",
            "publicContract": "NOT_APPLICABLE",
            "targetedRegression": "VERIFIED",
            "decision": "ACCEPTED",
            "rationale": "The TASK-owned behavior is accepted.",
        }
    elif loop_kind == "GROUP_REVIEW_LOOP":
        result["groupIntegration"] = {
            "seams": [
                {
                    "seam": "Direct-child integration boundary",
                    "participants": ["child-a", "child-b"],
                    "status": "VERIFIED",
                    "evidenceRefs": ["review-boundary-check"],
                }
            ],
            "decision": "INTEGRATED",
            "rationale": "Only direct-child composition was reviewed.",
        }
    elif loop_kind == "DELIVERY_REVIEW_LOOP":
        result["deliveryReadiness"] = {
            "requirementCoverage": [
                {
                    "acceptancePoint": "The Delivery acceptance is complete.",
                    "ownerRefs": ["root-work-item"],
                    "status": "COVERED",
                    "evidenceRefs": ["review-boundary-check"],
                }
            ],
            "integrationEvidence": "SUFFICIENT",
            "operationalReadiness": "NOT_APPLICABLE",
            "openBlockingRisks": [],
            "acceptedRisks": [],
            "decision": "READY_FOR_USER_CONFIRMATION",
            "rationale": "The Delivery is ready for final user confirmation.",
        }
    else:
        raise ValueError(f"Unsupported Review Loop kind: {loop_kind}")
    return {"status": "SUCCEEDED", "summary": summary, "result": result}

def success_for_node(node_id: str, summary: str) -> dict:
    if node_id.startswith("review:task:"):
        return review_success("TASK_REVIEW_LOOP", summary)
    if node_id.startswith("review:group:"):
        return review_success("GROUP_REVIEW_LOOP", summary)
    if node_id.startswith("review:delivery:"):
        return review_success("DELIVERY_REVIEW_LOOP", summary)
    return success(summary)

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

def database_hierarchy() -> dict:
    source = task_hierarchy()
    loop = source["root"]["definition"]["execution"]["loop"]
    loop["resourceClaims"] = ["db-schema-orders"]
    loop["payload"]["databaseChanges"] = [
        {
            "projectId": "erp-service",
            "database": "erp",
            "schema": "public",
            "table": "orders",
            "summary": "增加订单取消原因并建立状态查询索引。",
            "changeType": "MODIFY",
            "before": {
                "comment": "订单主表",
                "columns": [
                    {
                        "name": "id",
                        "type": "bigint",
                        "nullable": False,
                        "default": None,
                        "comment": "订单标识",
                        "autoIncrement": True,
                    },
                    {
                        "name": "status",
                        "type": "varchar(32)",
                        "nullable": False,
                        "default": "CREATED",
                        "comment": "订单状态",
                    },
                ],
                "primaryKey": {"name": "pk_orders", "columns": ["id"]},
                "uniqueConstraints": [],
                "indexes": [],
                "foreignKeys": [],
            },
            "after": {
                "comment": "订单主表",
                "columns": [
                    {
                        "name": "id",
                        "type": "bigint",
                        "nullable": False,
                        "default": None,
                        "comment": "订单标识",
                        "autoIncrement": True,
                    },
                    {
                        "name": "status",
                        "type": "varchar(32)",
                        "nullable": False,
                        "default": "CREATED",
                        "comment": "订单状态",
                    },
                    {
                        "name": "cancel_reason",
                        "type": "varchar(500)",
                        "nullable": True,
                        "default": None,
                        "comment": "订单取消原因",
                    },
                ],
                "primaryKey": {"name": "pk_orders", "columns": ["id"]},
                "uniqueConstraints": [],
                "indexes": [
                    {
                        "name": "idx_orders_status",
                        "columns": ["status"],
                        "unique": False,
                    }
                ],
                "foreignKeys": [],
            },
            "migration": {
                "forward": "新增 cancel_reason 并创建 idx_orders_status。",
                "rollback": "删除索引后删除 cancel_reason。",
                "backfill": "历史订单无需回填，保持 NULL。",
                "compatibility": "先执行向后兼容迁移，再发布应用。",
                "verification": [
                    "核对字段类型、可空性与注释",
                    "验证索引存在且迁移可回滚",
                ],
            },
            "resourceClaim": "db-schema-orders",
        }
    ]
    return source
