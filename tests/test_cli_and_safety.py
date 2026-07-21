from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hdg.cli import run_cli
from hdg.errors import GatedLoopError
from hdg.execution import dispatch_task, record_task_result
from hdg.fs_safe import safe_path
from hdg.jsonio import fingerprint
from hdg.planning import freeze_hierarchy, prepare_hierarchy
from hdg.repository import GovernanceRepository

from .fixtures import task_hierarchy


class CliAndSafetyTests(unittest.TestCase):
    def test_help_is_python_only_and_has_no_legacy_commands(self) -> None:
        output = io.StringIO()
        self.assertEqual(run_cli(["--help"], stdout=output), 0)
        help_text = output.getvalue()
        self.assertIn("python -X utf8 <skill-root>/scripts/hdg.py", help_text)
        self.assertIn("--json", help_text)
        self.assertIn("prepare-hierarchy", help_text)
        self.assertIn("prepare-hierarchy --definition - --host-runtime <agent>", help_text)
        self.assertIn("record-interaction --item <id> --interaction -", help_text)
        self.assertNotIn("<file|->", help_text)
        self.assertIn("freeze-hierarchy", help_text)
        self.assertIn("record-interaction", help_text)
        self.assertIn("interaction-log", help_text)
        self.assertIn("--development-mode active|manual", help_text)
        self.assertNotIn("select-development-mode", help_text)
        self.assertIn("retry-item --item <id> --expected-baseline <sha256>", help_text)
        self.assertIn("remediate-task --item <task-id> --expected-baseline <sha256> --evidence -", help_text)
        self.assertNotIn("retry-item --item <id> --expected-baseline <sha256> --confirmed", help_text)
        self.assertNotIn("prepare-item", help_text)
        self.assertNotIn("freeze-item", help_text)
        self.assertNotIn("promote-item", help_text)
        self.assertNotIn("revise-item", help_text)
        self.assertNotIn("upgrade-registry", help_text)
        self.assertNotIn("delivery-item", help_text)
        self.assertNotIn("hdg.mjs", help_text)

    def test_definition_can_be_read_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stdout = io.StringIO()
            stderr = io.StringIO()
            code = run_cli(
                ["prepare-hierarchy", "--definition", "-", "--host-runtime", "codex", "--json"],
                cwd=temporary,
                stdin=io.StringIO(json.dumps(task_hierarchy())),
                stdout=stdout,
                stderr=stderr,
            )
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertTrue(json.loads(stdout.getvalue())["ok"])

    def test_definition_file_paths_are_rejected_without_creating_control_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            definition_path = Path(temporary, "_hdg_definition.json")
            definition_path.write_text(json.dumps(task_hierarchy()), encoding="utf-8")
            stderr = io.StringIO()
            code = run_cli(
                [
                    "prepare-hierarchy",
                    "--definition",
                    definition_path.name,
                    "--host-runtime",
                    "claude-code",
                    "--json",
                ],
                cwd=temporary,
                stdout=io.StringIO(),
                stderr=stderr,
            )
            self.assertEqual(code, 1)
            self.assertEqual(
                json.loads(stderr.getvalue())["error"]["code"],
                "HIERARCHY_DEFINITION_STDIN_REQUIRED",
            )
            self.assertFalse(Path(temporary, ".layered-delivery").exists())

    def test_interaction_file_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            interaction_path = Path(temporary, "_hdg_interaction.json")
            interaction_path.write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            code = run_cli(
                [
                    "record-interaction",
                    "--item",
                    "t-example",
                    "--interaction",
                    interaction_path.name,
                    "--json",
                ],
                cwd=temporary,
                stdout=io.StringIO(),
                stderr=stderr,
            )
            self.assertEqual(code, 1)
            self.assertEqual(
                json.loads(stderr.getvalue())["error"]["code"],
                "WORK_ITEM_INTERACTION_STDIN_REQUIRED",
            )

    def test_execution_artifacts_stream_from_stdin_and_persist_only_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_stdout = io.StringIO()
            self.assertEqual(
                run_cli(
                    ["prepare-hierarchy", "--definition", "-", "--host-runtime", "codex", "--json"],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(task_hierarchy())),
                    stdout=prepare_stdout,
                ),
                0,
            )
            prepared = json.loads(prepare_stdout.getvalue())["result"]
            item_id = prepared["rootId"]
            baseline = prepared["baselineFingerprints"][item_id]
            self.assertEqual(
                run_cli(
                    [
                        "freeze-hierarchy", "--item", item_id,
                        "--expected-hierarchy", prepared["hierarchyFingerprint"],
                        "--development-mode", "active", "--confirmed", "--json",
                    ],
                    cwd=temporary,
                    stdout=io.StringIO(),
                ),
                0,
            )
            self.assertEqual(
                run_cli(
                    [
                        "dispatch-task", "--item", item_id,
                        "--owner", "developer", "--operation", "op-inline", "--json",
                    ],
                    cwd=temporary,
                    stdout=io.StringIO(),
                ),
                0,
            )
            result_artifact = {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": item_id,
                "operationId": "op-inline",
                "status": "IMPLEMENTED",
                "summary": "Implemented without a temporary evidence file.",
                "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                }],
                "blockers": [],
            }
            self.assertEqual(
                run_cli(
                    [
                        "task-result", "--item", item_id, "--operation", "op-inline",
                        "--status", "IMPLEMENTED", "--evidence", "-", "--json",
                    ],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(result_artifact)),
                    stdout=io.StringIO(),
                ),
                0,
            )
            gate_artifact = {
                "schemaVersion": 3,
                "kind": "WORK_ITEM_GATE",
                "workItemId": item_id,
                "baselineFingerprint": baseline,
                "verdict": "PASS",
                "summary": "All frozen checks passed without a temporary evidence file.",
                "scope": {
                    "changedFiles": ["src/controller.py", "tests/test_controller.py"],
                    "outOfScopeFiles": [],
                },
                "acceptance": [{"id": "A-001", "status": "PASS", "evidence": "Verified."}],
                "tests": [{
                    "argv": ["python", "-m", "unittest", "tests.test_controller"],
                    "exitCode": 0,
                    "testsRun": 1,
                    "summary": "Passed.",
                }],
                "findings": {"p0": [], "p1": [], "p2": []},
            }
            self.assertEqual(
                run_cli(
                    ["accept-item", "--item", item_id, "--evidence", "-", "--json"],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(gate_artifact)),
                    stdout=io.StringIO(),
                ),
                0,
            )
            review_artifact = {
                "schemaVersion": 3,
                "kind": "INDEPENDENT_REVIEW",
                "reviewer": "fresh-reviewer",
                "isolation": "FRESH_READ_ONLY",
                "verdict": "PASS",
                "findings": {"p0": 0, "p1": 0},
            }
            self.assertEqual(
                run_cli(
                    [
                        "acceptance-item", "--item", item_id,
                        "--action", "INDEPENDENT_REVIEW_PASS", "--evidence", "-", "--json",
                    ],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(review_artifact)),
                    stdout=io.StringIO(),
                ),
                0,
            )
            confirmation_artifact = {
                "schemaVersion": 3,
                "kind": "USER_CONFIRMATION",
                "confirmedBy": "user",
                "decision": "CONFIRMED",
            }
            self.assertEqual(
                run_cli(
                    [
                        "acceptance-item", "--item", item_id,
                        "--action", "USER_CONFIRMED", "--evidence", "-", "--json",
                    ],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(confirmation_artifact)),
                    stdout=io.StringIO(),
                ),
                0,
            )

            self.assertEqual(list(Path(temporary).rglob("*.json")), [])
            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                entry = json.loads(
                    connection.execute(
                        "SELECT entry_json FROM work_items WHERE id = ?", (item_id,)
                    ).fetchone()[0]
                )
                report = json.loads(
                    connection.execute(
                        "SELECT report_json FROM reports WHERE work_item_id = ? AND report_kind = 'ACCEPTANCE'",
                        (item_id,),
                    ).fetchone()[0]
                )
            self.assertEqual(entry["latestResult"]["artifact"], result_artifact)
            self.assertEqual(entry["latestResult"]["evidence"], {"sha256": fingerprint(result_artifact)})
            self.assertEqual(entry["gate"]["artifact"], gate_artifact)
            self.assertEqual(entry["gate"]["evidence"], {"sha256": fingerprint(gate_artifact)})
            self.assertEqual(entry["acceptance"]["review"]["artifact"], review_artifact)
            self.assertEqual(
                entry["acceptance"]["review"]["evidence"],
                {"sha256": fingerprint(review_artifact)},
            )
            self.assertEqual(
                entry["acceptance"]["userConfirmation"]["artifact"],
                confirmation_artifact,
            )
            self.assertEqual(
                entry["acceptance"]["userConfirmation"]["evidence"],
                {"sha256": fingerprint(confirmation_artifact)},
            )
            self.assertEqual(report["gate"]["artifact"], gate_artifact)

    def test_invalid_stdin_artifact_rolls_back_without_releasing_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_stdout = io.StringIO()
            run_cli(
                ["prepare-hierarchy", "--definition", "-", "--host-runtime", "codex", "--json"],
                cwd=temporary,
                stdin=io.StringIO(json.dumps(task_hierarchy())),
                stdout=prepare_stdout,
            )
            prepared = json.loads(prepare_stdout.getvalue())["result"]
            item_id = prepared["rootId"]
            run_cli(
                [
                    "freeze-hierarchy", "--item", item_id,
                    "--expected-hierarchy", prepared["hierarchyFingerprint"],
                    "--development-mode", "active", "--confirmed", "--json",
                ],
                cwd=temporary,
                stdout=io.StringIO(),
            )
            run_cli(
                [
                    "dispatch-task", "--item", item_id,
                    "--owner", "developer", "--operation", "op-current", "--json",
                ],
                cwd=temporary,
                stdout=io.StringIO(),
            )
            invalid_artifact = {
                "schemaVersion": 3,
                "kind": "TASK_RESULT",
                "taskId": item_id,
                "operationId": "op-stale",
                "status": "IMPLEMENTED",
                "summary": "Stale operation result.",
                "changedFiles": [],
                "tests": [],
                "blockers": [],
            }
            stderr = io.StringIO()
            self.assertEqual(
                run_cli(
                    [
                        "task-result", "--item", item_id, "--operation", "op-current",
                        "--status", "IMPLEMENTED", "--evidence", "-", "--json",
                    ],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(invalid_artifact)),
                    stdout=io.StringIO(),
                    stderr=stderr,
                ),
                1,
            )
            self.assertEqual(
                json.loads(stderr.getvalue())["error"]["code"],
                "WORK_ITEM_RESULT_EVIDENCE_INVALID",
            )
            database = Path(temporary, ".layered-delivery", "governance.sqlite3")
            with closing(sqlite3.connect(database)) as connection:
                entry = json.loads(
                    connection.execute(
                        "SELECT entry_json FROM work_items WHERE id = ?", (item_id,)
                    ).fetchone()[0]
                )
            self.assertEqual(entry["status"], "CLAIMED")
            self.assertEqual(entry["claim"]["operationId"], "op-current")

    def test_execution_evidence_file_paths_are_rejected(self) -> None:
        stderr = io.StringIO()
        self.assertEqual(
            run_cli(
                [
                    "accept-item", "--item", "t-example",
                    "--evidence", ".hdg-tmp/t-example-gate.json", "--json",
                ],
                cwd=tempfile.gettempdir(),
                stdout=io.StringIO(),
                stderr=stderr,
            ),
            1,
        )
        self.assertEqual(
            json.loads(stderr.getvalue())["error"]["code"],
            "WORK_ITEM_EVIDENCE_STDIN_REQUIRED",
        )

    def test_validation_remediation_streams_from_stdin_without_a_new_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_hierarchy(
                root=temporary,
                hierarchy=task_hierarchy(),
                host_runtime="codex",
            )
            task_id = prepared["rootId"]
            baseline = prepared["baselineFingerprints"][task_id]
            freeze_hierarchy(
                root=temporary,
                root_id=task_id,
                expected_hierarchy_fingerprint=prepared["hierarchyFingerprint"],
                development_mode="active",
                confirmed=True,
            )
            dispatch_task(root=temporary, item_id=task_id, owner="developer", operation_id="op-cli")
            record_task_result(
                root=temporary,
                item_id=task_id,
                operation_id="op-cli",
                status="IMPLEMENTED",
                evidence={
                    "schemaVersion": 3,
                    "kind": "TASK_RESULT",
                    "taskId": task_id,
                    "operationId": "op-cli",
                    "status": "IMPLEMENTED",
                    "summary": "Implemented before validation remediation.",
                    "changedFiles": ["src/controller.py"],
                    "tests": [{"argv": ["python", "-m", "unittest"], "exitCode": 0, "testsRun": 1}],
                    "blockers": [],
                },
            )
            artifact = {
                "schemaVersion": 3,
                "kind": "VALIDATION_REMEDIATION",
                "taskId": task_id,
                "baselineFingerprint": baseline,
                "source": "REGRESSION",
                "summary": "Correct a file omission found by validation.",
                "acceptanceIds": ["A-001"],
                "fileChanges": [{
                    "path": "src/controller_docs.py",
                    "action": "MODIFY",
                    "purpose": "Align documentation with the frozen behavior.",
                }],
                "assertions": {
                    "goalUnchanged": True,
                    "requirementsUnchanged": True,
                    "acceptanceUnchanged": True,
                    "interfacesUnchanged": True,
                    "dataContractUnchanged": True,
                    "testCommandsUnchanged": True,
                    "topologyUnchanged": True,
                    "externalAuthorityUnchanged": True,
                },
            }
            stdout = io.StringIO()
            stderr = io.StringIO()
            self.assertEqual(
                run_cli(
                    [
                        "remediate-task", "--item", task_id,
                        "--expected-baseline", baseline,
                        "--evidence", "-", "--json",
                    ],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(artifact)),
                    stdout=stdout,
                    stderr=stderr,
                ),
                0,
                stderr.getvalue(),
            )
            result = json.loads(stdout.getvalue())["result"]
            self.assertEqual(result["id"], task_id)
            self.assertEqual(result["status"], "FROZEN")
            self.assertEqual(len(GovernanceRepository(temporary).read_registry()["workItems"]), 1)
            self.assertEqual(list(Path(temporary).rglob("*.json")), [])

    def test_freeze_requires_mode_in_the_same_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            code = run_cli(
                [
                    "freeze-hierarchy",
                    "--item", "t-example",
                    "--expected-hierarchy", "0" * 64,
                    "--confirmed",
                    "--json",
                ],
                cwd=temporary,
                stderr=stderr,
            )
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "OPTION_REQUIRED")

    def test_interaction_can_be_recorded_and_listed_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            prepare_stdout = io.StringIO()
            run_cli(
                ["prepare-hierarchy", "--definition", "-", "--host-runtime", "codex", "--json"],
                cwd=temporary,
                stdin=io.StringIO(json.dumps(task_hierarchy())),
                stdout=prepare_stdout,
            )
            item_id = json.loads(prepare_stdout.getvalue())["result"]["rootId"]
            interaction = {
                "schemaVersion": 3,
                "sessionId": "cli-session",
                "actor": "AGENT",
                "eventType": "AGENT_UPDATE",
                "summary": "已完成 SQLite 状态检查。",
                "operationId": None,
                "hostRuntime": "codex",
            }
            stdout = io.StringIO()
            self.assertEqual(
                run_cli(
                    ["record-interaction", "--item", item_id, "--interaction", "-", "--json"],
                    cwd=temporary,
                    stdin=io.StringIO(json.dumps(interaction)),
                    stdout=stdout,
                ),
                0,
            )
            self.assertEqual(json.loads(stdout.getvalue())["result"]["eventType"], "AGENT_UPDATE")
            stdout = io.StringIO()
            self.assertEqual(
                run_cli(["interaction-log", "--item", item_id, "--json"], cwd=temporary, stdout=stdout),
                0,
            )
            self.assertEqual(json.loads(stdout.getvalue())["result"][-1]["summary"], "已完成 SQLite 状态检查。")

    def test_removed_mode_selection_command_is_rejected(self) -> None:
        stderr = io.StringIO()
        code = run_cli(["select-development-mode", "--json"], stderr=stderr)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "UNKNOWN_COMMAND")

    @unittest.skipUnless(os.name == "nt", "Windows drive semantics")
    def test_cross_volume_input_is_rejected(self) -> None:
        with self.assertRaises(GatedLoopError) as raised:
            safe_path("G:\\workspace", "C:\\Temp\\definition.json")
        self.assertEqual(raised.exception.code, "PATH_CROSS_VOLUME")


if __name__ == "__main__":
    unittest.main()
