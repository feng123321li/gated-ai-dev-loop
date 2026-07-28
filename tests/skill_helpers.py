from __future__ import annotations

from typing import Any

from hdg.repository import GovernanceRepository
from hdg.skill_execution import (
    record_skill_activation,
    record_skill_conformance,
)


def activate_required_skills(
    root: str,
    item_id: str,
    stage: str,
    *,
    execution_id: str,
    executor_id: str,
    execution_host_runtime: str | None = None,
    blocked: bool = False,
    now: object = None,
) -> list[dict[str, Any]]:
    repository = GovernanceRepository(root, now=now)
    registry = repository.read_operational_registry()
    entry = repository.item_by_id(registry, item_id)
    current = entry
    while current["parentId"] is not None:
        current = repository.item_by_id(registry, current["parentId"])
    planning_host_runtime = repository.read_package(
        registry,
        current,
    )[1]["hostRuntime"]
    host_runtime = execution_host_runtime or planning_host_runtime
    mechanism = "HOST_NATIVE_SKILL"
    requirements = repository.effective_required_skills(
        registry,
        entry,
        stage=stage,
    )
    return [
        record_skill_activation(
            root=root,
            item_id=item_id,
            stage=stage,
            skill_name=requirement["name"],
            activation={
                "sessionId": f"session-{execution_id}",
                "executorId": executor_id,
                "executionId": execution_id,
                "nativeInvocationId": (
                    f"native-{stage.lower()}-{index}-{execution_id}"
                ),
                "mechanism": mechanism,
                "status": "BLOCKED" if blocked else "INVOKED",
                "summary": (
                    "The host-native required Skill invocation was blocked "
                    "by a concrete catalog availability failure."
                    if blocked
                    else (
                        "The execution adapter automatically invoked the "
                        "exact frozen Skill through the host-native mechanism."
                    )
                ),
            },
            execution_host_runtime=host_runtime,
            now=now,
        )
        for index, requirement in enumerate(requirements, start=1)
    ]


def conform_required_skills(
    root: str,
    item_id: str,
    receipts: list[dict[str, Any]],
    *,
    blocked: bool = False,
    now: object = None,
) -> None:
    for receipt in receipts:
        record_skill_conformance(
            root=root,
            item_id=item_id,
            activation_receipt_id=receipt["activationReceiptId"],
            conformance={
                "status": "BLOCKED" if blocked else "PASS",
                "summary": (
                    "The required Skill could not complete because its "
                    "host-native invocation was blocked."
                    if blocked
                    else (
                        "The full required Skill workflow was checked "
                        "against the actual stage output."
                    )
                ),
                "checks": [{
                    "name": "complete-workflow",
                    "status": "BLOCKED" if blocked else "PASS",
                    "evidence": (
                        "The host catalog did not provide the exact frozen "
                        "Skill, so its workflow could not execute."
                        if blocked
                        else (
                            "Every required workflow step was applied and "
                            "verified against concrete artifacts."
                        )
                    ),
                }],
            },
            execution_host_runtime=receipt["hostRuntime"],
            now=now,
        )
