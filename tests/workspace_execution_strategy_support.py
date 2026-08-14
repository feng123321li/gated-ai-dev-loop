from __future__ import annotations

from copy import deepcopy

from pathlib import Path

import subprocess

from tempfile import TemporaryDirectory

import unittest

from hdg.errors import GatedLoopError

from hdg.graph_model import (
    loop_node_id,
    review_node_id,
    task_review_node_id,
)

from hdg.mcp_tools import call_tool

from hdg.planning import freeze_hierarchy, prepare_delivery_revision

from hdg.repository import SchedulerRepository

from .automatic_dispatch import reserve_loop

from .test_scheduler_contracts import git_command, isolated_task_hierarchy

from .test_scheduler_runtime import success_for_node

def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "repository"
    repository.mkdir()
    git_command(repository, "init", "--initial-branch=main")
    git_command(repository, "config", "user.name", "Scheduler Tests")
    git_command(
        repository,
        "config",
        "user.email",
        "scheduler-tests@example.invalid",
    )
    (repository / "README.md").write_text(
        "# workspace strategy fixture\n",
        encoding="utf-8",
    )
    git_command(repository, "add", "README.md")
    git_command(repository, "commit", "-m", "Initial main baseline")
    return repository, git_command(repository, "rev-parse", "HEAD")

def _preview(
    repository: Path,
    delivery_id: str,
    task_id: str,
) -> dict:
    return call_tool(
        "preview_hierarchy",
        {
            "hierarchy": isolated_task_hierarchy(
                delivery_id,
                task_id,
            )
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )

def _confirm_existing_branch(
    repository: Path,
    delivery_id: str,
    task_id: str,
    branch_ref: str,
) -> dict:
    preview = _preview(repository, delivery_id, task_id)
    return call_tool(
        "confirm_development_baseline",
        {
            "root_id": delivery_id,
            "selection": branch_ref,
            "expected_hierarchy_fingerprint": preview[
                "hierarchyFingerprint"
            ],
            "confirmed_by": "human",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )

def _confirm_new_branch(
    repository: Path,
    delivery_id: str,
    task_id: str,
    branch_ref: str,
) -> dict:
    preview = _preview(repository, delivery_id, task_id)
    return call_tool(
        "confirm_development_baseline",
        {
            "root_id": delivery_id,
            "selection": "NEW_FROM_MAINLINE",
            "branch_name": branch_ref,
            "expected_hierarchy_fingerprint": preview[
                "hierarchyFingerprint"
            ],
            "confirmed_by": "human",
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )

def _select(
    repository: Path,
    confirmed: dict,
) -> dict:
    arguments = {
        "root_id": confirmed["rootId"],
        "selection": "AUTOMATIC",
        "expected_hierarchy_fingerprint": confirmed[
            "hierarchyFingerprint"
        ],
        "expected_graph_fingerprint": confirmed["graphFingerprint"],
        "authorized_project_ids": [],
        "confirmed_by": "human",
    }
    return call_tool(
        "select_execution_mode",
        arguments,
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )

def _resume(
    repository: Path,
    confirmed: dict,
) -> dict:
    return call_tool(
        "resume_execution_mode",
        {
            "root_id": confirmed["rootId"],
            "expected_hierarchy_fingerprint": confirmed[
                "hierarchyFingerprint"
            ],
            "expected_graph_fingerprint": confirmed[
                "graphFingerprint"
            ],
        },
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )

def _complete_to_user_confirmation(
    repository: Path,
    *,
    delivery_id: str,
    task_id: str,
    manual_task: bool = False,
) -> dict:
    for index, node_id in enumerate(
        (
            loop_node_id(task_id),
            task_review_node_id(task_id),
            review_node_id(delivery_id),
        ),
        start=1,
    ):
        operation_id = f"op-{delivery_id}-{index}"
        arguments = {
            "root_id": delivery_id,
            "node_id": node_id,
            "owner": f"receiver-{delivery_id}-{index}",
            "agent_id": "codex",
            "receiver_context_id": f"context-{delivery_id}-{index}",
            "operation_id": operation_id,
        }
        if manual_task and node_id == loop_node_id(task_id):
            arguments["dispatch_mode"] = "MANUAL"
        else:
            reservation = reserve_loop(
                root=str(repository),
                root_id=delivery_id,
                node_id=node_id,
            )
            arguments.update(
                {
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
                }
            )
        call_tool(
            "dispatch_loop",
            arguments,
            root=str(repository),
            workspace_root=str(repository),
            trusted_host_adapter="codex",
        )
        call_tool(
            "record_loop_result",
            {
                "root_id": delivery_id,
                "node_id": node_id,
                "operation_id": operation_id,
                "outcome": success_for_node(
                    node_id,
                    f"{node_id} completed.",
                ),
            },
            root=str(repository),
            workspace_root=str(repository),
            trusted_host_adapter="codex",
        )
    return call_tool(
        "graph_frontier",
        {"root_id": delivery_id},
        root=str(repository),
        workspace_root=str(repository),
        trusted_host_adapter="codex",
    )

def _is_waiting_for_workspace_turn(result: dict) -> bool:
    preparation = result.get("workspacePreparation")
    turn = result.get("workspaceTurn")
    values = {
        result.get("status"),
        result.get("nextAction"),
        preparation.get("state") if isinstance(preparation, dict) else None,
        preparation.get("nextAction")
        if isinstance(preparation, dict)
        else None,
        turn.get("state") if isinstance(turn, dict) else None,
        turn.get("nextAction") if isinstance(turn, dict) else None,
    }
    return bool(
        values
        & {
            "WAITING_FOR_WORKSPACE_TURN",
            "WAIT_FOR_WORKSPACE_TURN",
            "WAIT_FOR_CURRENT_WORKSPACE_TURN",
        }
    )

def _is_waiting_for_workspace_commit(result: dict) -> bool:
    preparation = result.get("workspacePreparation")
    turn = result.get("workspaceTurn")
    commit_gate = result.get("workspaceCommitGate")
    values = {
        result.get("status"),
        result.get("nextAction"),
        preparation.get("state") if isinstance(preparation, dict) else None,
        preparation.get("nextAction")
        if isinstance(preparation, dict)
        else None,
        turn.get("state") if isinstance(turn, dict) else None,
        turn.get("nextAction") if isinstance(turn, dict) else None,
        (
            commit_gate.get("state")
            if isinstance(commit_gate, dict)
            else None
        ),
        (
            commit_gate.get("nextAction")
            if isinstance(commit_gate, dict)
            else None
        ),
    }
    return bool(
        values
        & {
            "WAITING_FOR_WORKSPACE_COMMIT",
            "WAIT_FOR_WORKSPACE_COMMIT",
        }
    )
