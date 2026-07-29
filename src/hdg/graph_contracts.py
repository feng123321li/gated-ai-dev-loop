from __future__ import annotations

from typing import Any

from .errors import fail
from .evidence import (
    gate_evidence_contract,
    task_result_evidence_contract,
    validation_remediation_evidence_contract,
)


def mcp_call(tool: str, **arguments: Any) -> dict[str, Any]:
    """Describe one host MCP invocation without exposing a shell command."""

    return {
        "tool": tool,
        "arguments": arguments,
    }


def evidence_contract_ref(
    work_item_id: str,
    contract_kind: str,
) -> dict[str, Any]:
    artifact_kinds = {
        "result": "TASK_RESULT",
        "gate": "WORK_ITEM_GATE",
        "remediation": "VALIDATION_REMEDIATION",
        "review": "ROOT_REVIEW",
        "confirmation": "USER_CONFIRMATION",
    }
    return {
        "artifactKind": artifact_kinds[contract_kind],
        "mcpCall": mcp_call(
            "evidence_contract",
            item_id=work_item_id,
            contract_kind=contract_kind,
        ),
    }


def _remediation_contract(
    repository: Any,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    definition = repository.assert_current_lineage(registry, entry)[0]
    return validation_remediation_evidence_contract(
        entry,
        definition,
        authorized_file_changes=repository.effective_task_file_changes(
            definition
        ),
    )


def _result_contract(
    repository: Any,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    if entry["kind"] != "TASK":
        fail(
            "WORK_ITEM_RESULT_CONTRACT_TASK_REQUIRED",
            "Task result evidence contracts require a Task",
        )
    if entry["status"] != "CLAIMED" or not entry.get("claim"):
        fail(
            "WORK_ITEM_RESULT_CONTRACT_NOT_READY",
            "Task result evidence contracts require an active claim",
            mcpCall=mcp_call(
                "dispatch_task",
                item_id=entry["id"],
                owner="<owner>",
                operation_id="<operation-id>",
            ),
        )
    definition = repository.assert_current_lineage(registry, entry)[0]
    return task_result_evidence_contract(
        entry,
        definition,
        authorized_file_changes=repository.effective_task_file_changes(
            definition
        ),
        required_skills=repository.effective_required_skills(
            registry,
            entry,
            stage="DEVELOPMENT",
        ),
    )


def _gate_contract(
    repository: Any,
    registry: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    definition = repository.assert_current_lineage(registry, entry)[0]
    additional_planned_files: set[str] = set()
    if entry["kind"] == "TASK":
        frozen_files = {
            item["path"]
            for item in definition["developmentPlan"].get("fileChanges", [])
        }
        effective_files = {
            item["path"]
            for item in repository.effective_task_file_changes(definition)
        }
        additional_planned_files = effective_files - frozen_files
    return gate_evidence_contract(
        entry,
        definition,
        additional_planned_files=additional_planned_files,
        required_skills=repository.effective_required_skills(
            registry,
            entry,
            stage="GATE",
        ),
    )
