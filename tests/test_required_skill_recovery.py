from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hdg.acceptance import accept_work_item, record_acceptance
from hdg.errors import GatedLoopError
from hdg.evidence import evidence_record
from hdg.execution import dispatch_task, record_task_result
from hdg.jsonio import canonical_json
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import capability_hierarchy, task_hierarchy
from .test_required_skills import (
    REQUIRED_SKILLS,
    _gate,
    _skill_usage,
    _task_result,
)


def _complete_through(
    root: str,
    stage: str,
) -> tuple[str, dict]:
    prepared = prepare_hierarchy(
        root=root,
        hierarchy=task_hierarchy(requiredSkills=REQUIRED_SKILLS),
        host_runtime="codex",
    )
    item_id = prepared["rootId"]
    freeze_hierarchy(
        root=root,
        root_id=item_id,
        expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
        development_mode="active",
        confirmed=True,
    )
    dispatch_task(
        root=root,
        item_id=item_id,
        owner="developer",
        operation_id="op-required-skill-recovery",
    )
    result = _task_result(item_id, "op-required-skill-recovery")
    result["skillUsage"] = [_skill_usage(
        "tdd-workflow",
        "DEVELOPMENT",
        "Applied the complete red-green-refactor workflow to the frozen regression command.",
    )]
    record_task_result(
        root=root,
        item_id=item_id,
        operation_id="op-required-skill-recovery",
        status="IMPLEMENTED",
        evidence=result,
    )
    if stage == "result":
        return item_id, prepared

    gate = _gate(item_id, prepared["baselineFingerprints"][item_id])
    gate["skillUsage"] = [_skill_usage(
        "tdd-workflow",
        "GATE",
        "Applied the complete gate workflow to scope, tests, acceptance trace, and findings.",
    )]
    accept_work_item(
        root=root,
        item_id=item_id,
        evidence=gate,
    )
    if stage == "gate":
        return item_id, prepared

    review = {
        "schemaVersion": 3,
        "kind": "INDEPENDENT_REVIEW",
        "reviewer": "fresh-reviewer",
        "isolation": "FRESH_READ_ONLY",
        "verdict": "PASS",
        "findings": {"p0": 0, "p1": 0},
        "skillUsage": [_skill_usage(
            "source-command-python-review",
            "FINAL_REVIEW",
            "Applied the complete fresh read-only Python review and found no P0 or P1 issues.",
        )],
    }
    record_acceptance(
        root=root,
        item_id=item_id,
        action="INDEPENDENT_REVIEW_PASS",
        evidence=review,
    )
    return item_id, prepared


def _mutate_entry(
    root: str,
    item_id: str,
    mutate: object,
) -> None:
    database = Path(root, ".layered-delivery", "governance.sqlite3")
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT entry_json FROM work_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        entry = json.loads(row[0])
        mutate(entry)
        connection.execute(
            "UPDATE work_items SET entry_json = ? WHERE id = ?",
            (canonical_json(entry), item_id),
        )
        connection.commit()
    finally:
        connection.close()


def _ready_capability_for_isolation_transition(
    root: str,
    transition: str,
) -> tuple[str, str, dict]:
    hierarchy = capability_hierarchy()
    hierarchy["root"]["definition"]["requiredSkills"] = [{
        "name": "tdd-workflow",
        "stages": ["DEVELOPMENT"],
        "purpose": "Apply the complete workflow in the child Task.",
    }]
    prepared = prepare_hierarchy(
        root=root,
        hierarchy=hierarchy,
        host_runtime="codex",
    )
    root_id = prepared["rootId"]
    task_id = "t-python-controller"
    freeze_hierarchy(
        root=root,
        root_id=root_id,
        expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
        development_mode="active",
        confirmed=True,
    )
    dispatch_task(
        root=root,
        item_id=task_id,
        owner="developer",
        operation_id="op-isolated-required-skill",
    )
    result = _task_result(task_id, "op-isolated-required-skill")
    result["skillUsage"] = [_skill_usage(
        "tdd-workflow",
        "DEVELOPMENT",
        "Applied the complete workflow to the frozen controller regression.",
    )]
    record_task_result(
        root=root,
        item_id=task_id,
        operation_id="op-isolated-required-skill",
        status="IMPLEMENTED",
        evidence=result,
    )
    accept_work_item(
        root=root,
        item_id=task_id,
        evidence=_gate(
            task_id,
            prepared["baselineFingerprints"][task_id],
        ),
    )

    root_gate = {
        "schemaVersion": 3,
        "kind": "WORK_ITEM_GATE",
        "workItemId": root_id,
        "baselineFingerprint": prepared["baselineFingerprints"][root_id],
        "verdict": "PASS",
        "summary": "The aggregate gate verified the child contract.",
        "scope": {"changedFiles": [], "outOfScopeFiles": []},
        "acceptance": [{
            "id": "A-001",
            "requirementIds": ["R-001"],
            "status": "PASS",
            "evidence": "The child requirement passed its frozen gate.",
        }],
        "tests": [{
            "argv": ["python", "-m", "unittest", "discover"],
            "exitCode": 0,
            "testsRun": 1,
            "summary": "The aggregate unittest command passed.",
        }],
        "findings": {"p0": [], "p1": [], "p2": []},
    }
    if transition in {"review", "confirmation"}:
        accept_work_item(
            root=root,
            item_id=root_id,
            evidence=root_gate,
        )
    if transition == "confirmation":
        record_acceptance(
            root=root,
            item_id=root_id,
            action="HUMAN_REVIEW_ACCEPTED",
            evidence={
                "schemaVersion": 3,
                "kind": "HUMAN_REVIEW",
                "reviewer": "user",
                "verdict": "ACCEPTED",
            },
        )
    return root_id, task_id, root_gate


class RequiredSkillRecoveryTests(unittest.TestCase):
    def test_isolated_required_skill_child_blocks_ancestor_transitions(
        self,
    ) -> None:
        for transition in ("gate", "review", "confirmation"):
            with (
                self.subTest(transition=transition),
                tempfile.TemporaryDirectory() as root,
            ):
                root_id, task_id, root_gate = (
                    _ready_capability_for_isolation_transition(
                        root,
                        transition,
                    )
                )

                def corrupt(entry: dict) -> None:
                    entry["latestResult"]["artifact"].pop("skillUsage")
                    entry["latestResult"]["evidence"]["path"] = (
                        ".legacy/result.json"
                    )

                _mutate_entry(root, task_id, corrupt)
                repository = GovernanceRepository(root)
                repository.read_operational_registry()
                self.assertTrue(repository.is_item_isolated(task_id))

                with self.assertRaises(GatedLoopError) as raised:
                    if transition == "gate":
                        accept_work_item(
                            root=root,
                            item_id=root_id,
                            evidence=root_gate,
                        )
                    elif transition == "review":
                        record_acceptance(
                            root=root,
                            item_id=root_id,
                            action="HUMAN_REVIEW_ACCEPTED",
                            evidence={
                                "schemaVersion": 3,
                                "kind": "HUMAN_REVIEW",
                                "reviewer": "user",
                                "verdict": "ACCEPTED",
                            },
                        )
                    else:
                        record_acceptance(
                            root=root,
                            item_id=root_id,
                            action="USER_CONFIRMED",
                            evidence={
                                "schemaVersion": 3,
                                "kind": "USER_CONFIRMATION",
                                "confirmedBy": "user",
                                "decision": "CONFIRMED",
                            },
                        )
                self.assertEqual(
                    raised.exception.code,
                    "WORK_ITEM_HIERARCHY_ISOLATED",
                )

    def test_restart_rejects_missing_required_skill_usage_in_current_artifacts(
        self,
    ) -> None:
        for stage, record_path in (
            ("result", ("latestResult",)),
            ("gate", ("gate",)),
            ("review", ("acceptance", "review")),
        ):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as root:
                item_id, _ = _complete_through(root, stage)

                def corrupt(entry: dict) -> None:
                    record = entry
                    for key in record_path:
                        record = record[key]
                    record["artifact"].pop("skillUsage")
                    record["evidence"] = evidence_record(record["artifact"])
                    entry["latestEvidence"] = record["evidence"]

                _mutate_entry(root, item_id, corrupt)

                with self.assertRaises(GatedLoopError) as raised:
                    GovernanceRepository(root).read_operational_registry()
                self.assertEqual(
                    raised.exception.code,
                    "WORK_ITEM_STORED_EVIDENCE_INVALID",
                )

    def test_restart_rejects_artifact_whose_saved_hash_does_not_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _complete_through(root, "result")

            def corrupt(entry: dict) -> None:
                entry["latestResult"]["artifact"]["skillUsage"][0][
                    "evidence"
                ] = (
                    "Changed the stored Skill usage without updating its "
                    "saved evidence hash."
                )

            _mutate_entry(root, item_id, corrupt)

            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(root).read_operational_registry()
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_STORED_EVIDENCE_INVALID",
            )

    def test_restart_revalidates_inherited_required_skills(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            hierarchy = capability_hierarchy()
            hierarchy["root"]["definition"]["requiredSkills"] = [{
                "name": "tdd-workflow",
                "stages": ["DEVELOPMENT"],
                "purpose": (
                    "Apply the complete TDD workflow to every descendant Task."
                ),
            }]
            prepared = prepare_hierarchy(
                root=root,
                hierarchy=hierarchy,
                host_runtime="codex",
            )
            task_id = hierarchy["root"]["children"][0]["definition"]["id"]
            freeze_hierarchy(
                root=root,
                root_id=prepared["rootId"],
                expected_hierarchy_fingerprint=prepared[
                    "hierarchyFingerprint"
                ],
                development_mode="active",
                confirmed=True,
            )
            dispatch_task(
                root=root,
                item_id=task_id,
                owner="developer",
                operation_id="op-inherited-skill-recovery",
            )
            result = _task_result(
                task_id,
                "op-inherited-skill-recovery",
            )
            result["skillUsage"] = [_skill_usage(
                "tdd-workflow",
                "DEVELOPMENT",
                "Applied the inherited complete TDD workflow to the descendant Task.",
            )]
            record_task_result(
                root=root,
                item_id=task_id,
                operation_id="op-inherited-skill-recovery",
                status="IMPLEMENTED",
                evidence=result,
            )

            def corrupt(entry: dict) -> None:
                record = entry["latestResult"]
                record["artifact"].pop("skillUsage")
                record["evidence"] = evidence_record(record["artifact"])
                entry["latestEvidence"] = record["evidence"]

            _mutate_entry(root, task_id, corrupt)

            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(root).read_operational_registry()
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_STORED_EVIDENCE_INVALID",
            )

    def test_restart_rejects_semantically_valid_artifact_not_bound_to_graph(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _complete_through(root, "gate")

            def corrupt(entry: dict) -> None:
                record = entry["gate"]
                record["artifact"]["skillUsage"][0]["evidence"] = (
                    "Applied a different but structurally valid gate Skill "
                    "usage statement after the graph event was recorded."
                )
                record["evidence"] = evidence_record(record["artifact"])
                entry["latestEvidence"] = record["evidence"]

            _mutate_entry(root, item_id, corrupt)

            with self.assertRaises(GatedLoopError) as raised:
                GovernanceRepository(root).read_operational_registry()
            self.assertEqual(
                raised.exception.code,
                "WORK_ITEM_STORED_EVIDENCE_INVALID",
            )

    def test_restart_accepts_untouched_required_skill_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            item_id, _ = _complete_through(root, "review")

            registry = GovernanceRepository(root).read_operational_registry()

            entry = next(
                item for item in registry["workItems"] if item["id"] == item_id
            )
            self.assertEqual(
                entry["acceptance"]["status"],
                "WAITING_FOR_USER_CONFIRMATION",
            )


if __name__ == "__main__":
    unittest.main()
