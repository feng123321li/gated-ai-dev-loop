from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable, TextIO

from .errors import GatedLoopError
from .host_runtime import is_agent_runtime
from .jsonio import (
    json_structure_within_limits,
    rendered_json,
    strict_json_loads,
)
from .operations import OperationContext, execute_operation
from .timing import timed_stage, timing_session


COMMANDS = (
    "workspace-status",
    "prepare-hierarchy",
    "freeze-hierarchy",
    "ready-tasks",
    "graph-status",
    "graph-frontier",
    "graph-events",
    "graph-replay",
    "rebuild-graph-run",
    "advance-graph",
    "cancel-graph-run",
    "task-context",
    "evidence-contract",
    "record-skill-activation",
    "record-skill-conformance",
    "dispatch-task",
    "heartbeat-task",
    "pause-task",
    "resume-task",
    "claim-task",
    "task-result",
    "remediate-task",
    "retry-item",
    "gate-item",
    "accept-item",
    "acceptance-item",
    "refresh-projections",
    "record-interaction",
    "interaction-log",
)
VALUE_OPTIONS = {
    "--definition", "--host-runtime", "--item", "--owner", "--operation",
    "--status", "--evidence", "--expected-baseline",
    "--action", "--development-mode", "--expected-hierarchy", "--interaction", "--kind",
    "--stage", "--skill", "--activation", "--receipt", "--conformance",
}
FLAG_OPTIONS = {"--json", "--help", "--confirmed", "--dogfood", "--timing"}
COMMAND_OPTIONS = {
    "workspace-status": {"--json", "--help"},
    "prepare-hierarchy": {"--json", "--help", "--definition", "--host-runtime", "--dogfood"},
    "freeze-hierarchy": {
        "--json", "--help", "--item", "--expected-hierarchy", "--development-mode", "--confirmed", "--dogfood",
    },
    "ready-tasks": {"--json", "--help", "--item"},
    "graph-status": {"--json", "--help", "--item"},
    "graph-frontier": {"--json", "--help", "--item"},
    "graph-events": {"--json", "--help", "--item"},
    "graph-replay": {"--json", "--help", "--item"},
    "rebuild-graph-run": {"--json", "--help", "--item", "--confirmed", "--dogfood"},
    "advance-graph": {"--json", "--help", "--item", "--dogfood"},
    "cancel-graph-run": {"--json", "--help", "--item", "--confirmed", "--dogfood"},
    "task-context": {"--json", "--help", "--item", "--dogfood"},
    "evidence-contract": {"--json", "--help", "--item", "--kind"},
    "record-skill-activation": {
        "--json", "--help", "--item", "--stage", "--skill", "--activation", "--dogfood",
    },
    "record-skill-conformance": {
        "--json", "--help", "--item", "--receipt", "--conformance", "--dogfood",
    },
    "claim-task": {"--json", "--help", "--item", "--owner", "--operation", "--dogfood"},
    "dispatch-task": {"--json", "--help", "--item", "--owner", "--operation", "--dogfood"},
    "heartbeat-task": {"--json", "--help", "--item", "--operation", "--dogfood"},
    "pause-task": {"--json", "--help", "--item", "--operation", "--dogfood"},
    "resume-task": {"--json", "--help", "--item", "--dogfood"},
    "task-result": {"--json", "--help", "--item", "--operation", "--status", "--evidence", "--dogfood"},
    "remediate-task": {"--json", "--help", "--item", "--expected-baseline", "--evidence", "--dogfood"},
    "retry-item": {"--json", "--help", "--item", "--expected-baseline", "--dogfood"},
    "gate-item": {"--json", "--help", "--item", "--status", "--evidence", "--dogfood"},
    "accept-item": {"--json", "--help", "--item", "--evidence", "--dogfood"},
    "acceptance-item": {"--json", "--help", "--item", "--action", "--evidence", "--dogfood"},
    "refresh-projections": {"--json", "--help", "--dogfood"},
    "record-interaction": {"--json", "--help", "--item", "--interaction", "--dogfood"},
    "interaction-log": {"--json", "--help", "--item"},
}

USAGE = f"""Usage: python -X utf8 <skill-root>/scripts/hdg.py <command> [options]

Commands:
{chr(10).join(f'  {command}' for command in COMMANDS)}

  workspace-status
  prepare-hierarchy --definition - --host-runtime <agent>  # reads one complete requirement tree from stdin
  freeze-hierarchy --item <root-id> --expected-hierarchy <sha256> --development-mode active|manual --confirmed
  ready-tasks --item <root-or-subtree-id>
  graph-status --item <root-or-subtree-id>
  graph-frontier --item <root-or-subtree-id>
  graph-events --item <root-or-subtree-id>
  graph-replay --item <root-or-subtree-id>
  rebuild-graph-run --item <root-or-subtree-id> --confirmed
  advance-graph --item <root-or-subtree-id>
  cancel-graph-run --item <root-or-subtree-id> --confirmed
  task-context --item <task-id>
  evidence-contract --item <id> --kind result|gate|remediation|review|confirmation
  record-skill-activation --item <id> --stage DEVELOPMENT|GATE|FINAL_REVIEW --skill <name> --activation -
  record-skill-conformance --item <id> --receipt <activation-sha256> --conformance -
  dispatch-task --item <task-id> --owner <owner> --operation <id>
  heartbeat-task --item <task-id> --operation <id>
  pause-task --item <task-id> --operation <id>
  resume-task --item <task-id>
  claim-task --item <task-id> --owner <owner> --operation <id>
  task-result --item <task-id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence -
  remediate-task --item <task-id> --expected-baseline <sha256> --evidence -
  retry-item --item <id> --expected-baseline <sha256>
  gate-item --item <id> --status PASS|FAIL --evidence -
  accept-item --item <id> --evidence -
  acceptance-item --item <root-id> --action INDEPENDENT_REVIEW_PASS|REVIEW_BLOCKED|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence -
  record-interaction --item <id> --interaction -
  interaction-log --item <id>
  refresh-projections

Common options:
  --json  Render the command result or error as structured JSON.
  --timing  Write structured phase timings to stderr without changing stdout.

In the layered-delivery implementation repository, every command that writes control state also requires --dogfood.
"""


def _parse(argv: list[str]) -> dict[str, Any]:
    seen: set[str] = set()
    values: dict[str, str] = {}
    positionals = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if not item.startswith("--"):
            positionals.append(item)
            index += 1
            continue
        if item not in VALUE_OPTIONS and item not in FLAG_OPTIONS:
            raise GatedLoopError("UNKNOWN_OPTION", f"Unknown option: {item}")
        if item in seen:
            raise GatedLoopError("DUPLICATE_OPTION", f"Duplicate option: {item}")
        seen.add(item)
        if item in VALUE_OPTIONS:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise GatedLoopError("OPTION_VALUE_REQUIRED", f"Missing value for option: {item}")
            values[item] = argv[index + 1]
            index += 2
        else:
            index += 1
    command = positionals[0] if positionals else None
    if len(positionals) > 1:
        raise GatedLoopError("UNKNOWN_OPTION", f"Unexpected positional argument: {positionals[-1]}")
    if command in COMMAND_OPTIONS:
        for option in seen:
            if option != "--timing" and option not in COMMAND_OPTIONS[command]:
                raise GatedLoopError("UNKNOWN_OPTION", f"Option is not valid for {command}: {option}")
    if "--host-runtime" in values and not is_agent_runtime(values["--host-runtime"]):
        raise GatedLoopError("OPTION_VALUE_INVALID", "--host-runtime must be a safe lowercase Agent identifier")
    if "--development-mode" in values and values["--development-mode"] not in {"active", "manual"}:
        raise GatedLoopError("OPTION_VALUE_INVALID", "--development-mode must be active or manual")
    return {
        "command": command,
        "json": "--json" in seen,
        "confirmed": "--confirmed" in seen,
        "dogfood": "--dogfood" in seen,
        "timing": "--timing" in seen,
        "values": values,
    }


def _required(parsed: dict[str, Any], option: str) -> str:
    if option not in parsed["values"]:
        raise GatedLoopError("OPTION_REQUIRED", f"{parsed['command']} requires {option}")
    return parsed["values"][option]


def _read_structured(
    source: str,
    kind: str,
    *,
    stdin: TextIO,
) -> dict[str, Any]:
    if source != "-":
        raise GatedLoopError(
            f"{kind}_STDIN_REQUIRED",
            f"{kind.lower()} JSON must be provided directly through stdin with '-'",
        )
    try:
        with timed_stage("input.read"):
            text = stdin.read()
    except Exception:
        raise GatedLoopError(f"{kind}_READ", f"Unable to read {kind.lower()} JSON")
    if not json_structure_within_limits(text):
        raise GatedLoopError(
            f"{kind}_PARSE",
            f"{kind.lower()} JSON exceeds the structure limit",
        )
    try:
        value = strict_json_loads(text)
    except (
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
    ):
        raise GatedLoopError(f"{kind}_PARSE", f"{kind.lower()} JSON must be a mapping")
    if not isinstance(value, dict):
        raise GatedLoopError(f"{kind}_PARSE", f"{kind.lower()} JSON must be a mapping")
    return value


def _run(parsed: dict[str, Any], *, cwd: str, stdin: TextIO) -> Any:
    context = OperationContext(
        root=cwd,
        explicit_dogfood=parsed["dogfood"],
    )
    command = parsed["command"]
    if command == "workspace-status":
        return execute_operation(
            "workspace_status",
            {},
            context=context,
        )
    if command == "prepare-hierarchy":
        definition = _read_structured(
            _required(parsed, "--definition"),
            "HIERARCHY_DEFINITION",
            stdin=stdin,
        )
        return execute_operation(
            "prepare_hierarchy",
            {
                "hierarchy": definition,
                "host_runtime": _required(parsed, "--host-runtime"),
            },
            context=context,
        )
    if command == "freeze-hierarchy":
        return execute_operation(
            "freeze_hierarchy",
            {
                "item_id": _required(parsed, "--item"),
                "expected_hierarchy_fingerprint": _required(
                    parsed, "--expected-hierarchy"
                ),
                "development_mode": _required(parsed, "--development-mode"),
                "confirmed": parsed["confirmed"],
            },
            context=context,
        )
    if command == "ready-tasks":
        return execute_operation(
            "ready_tasks",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "graph-status":
        return execute_operation(
            "graph_status",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "graph-frontier":
        return execute_operation(
            "graph_frontier",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "graph-events":
        return execute_operation(
            "graph_events",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "graph-replay":
        return execute_operation(
            "graph_replay",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "rebuild-graph-run":
        return execute_operation(
            "rebuild_graph_run",
            {
                "item_id": _required(parsed, "--item"),
                "confirmed": parsed["confirmed"],
            },
            context=context,
        )
    if command == "advance-graph":
        return execute_operation(
            "advance_graph",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "cancel-graph-run":
        return execute_operation(
            "cancel_graph_run",
            {
                "item_id": _required(parsed, "--item"),
                "confirmed": parsed["confirmed"],
            },
            context=context,
        )
    if command == "task-context":
        return execute_operation(
            "task_context",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "evidence-contract":
        return execute_operation(
            "evidence_contract",
            {
                "item_id": _required(parsed, "--item"),
                "contract_kind": _required(parsed, "--kind"),
            },
            context=context,
        )
    if command == "record-skill-activation":
        activation = _read_structured(
            _required(parsed, "--activation"),
            "SKILL_ACTIVATION",
            stdin=stdin,
        )
        return execute_operation(
            "record_skill_activation",
            {
                "item_id": _required(parsed, "--item"),
                "stage": _required(parsed, "--stage"),
                "skill_name": _required(parsed, "--skill"),
                "activation": activation,
            },
            context=context,
        )
    if command == "record-skill-conformance":
        conformance = _read_structured(
            _required(parsed, "--conformance"),
            "SKILL_CONFORMANCE",
            stdin=stdin,
        )
        return execute_operation(
            "record_skill_conformance",
            {
                "item_id": _required(parsed, "--item"),
                "activation_receipt_id": _required(
                    parsed,
                    "--receipt",
                ),
                "conformance": conformance,
            },
            context=context,
        )
    if command == "claim-task":
        return execute_operation(
            "claim_task",
            {
                "item_id": _required(parsed, "--item"),
                "owner": _required(parsed, "--owner"),
                "operation_id": _required(parsed, "--operation"),
            },
            context=context,
        )
    if command == "dispatch-task":
        return execute_operation(
            "dispatch_task",
            {
                "item_id": _required(parsed, "--item"),
                "owner": _required(parsed, "--owner"),
                "operation_id": _required(parsed, "--operation"),
            },
            context=context,
        )
    if command == "heartbeat-task":
        return execute_operation(
            "heartbeat_task",
            {
                "item_id": _required(parsed, "--item"),
                "operation_id": _required(parsed, "--operation"),
            },
            context=context,
        )
    if command == "pause-task":
        return execute_operation(
            "pause_task",
            {
                "item_id": _required(parsed, "--item"),
                "operation_id": _required(parsed, "--operation"),
            },
            context=context,
        )
    if command == "resume-task":
        return execute_operation(
            "resume_task",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    if command == "retry-item":
        return execute_operation(
            "retry_item",
            {
                "item_id": _required(parsed, "--item"),
                "expected_baseline_fingerprint": _required(
                    parsed, "--expected-baseline"
                ),
            },
            context=context,
        )
    if command == "refresh-projections":
        return execute_operation(
            "refresh_projections",
            {},
            context=context,
        )
    if command == "record-interaction":
        interaction = _read_structured(
            _required(parsed, "--interaction"), "WORK_ITEM_INTERACTION", stdin=stdin
        )
        return execute_operation(
            "record_interaction",
            {
                "item_id": _required(parsed, "--item"),
                "interaction": interaction,
            },
            context=context,
        )
    if command == "interaction-log":
        return execute_operation(
            "interaction_log",
            {"item_id": _required(parsed, "--item")},
            context=context,
        )
    evidence_source = _required(parsed, "--evidence")
    if evidence_source != "-":
        raise GatedLoopError(
            "WORK_ITEM_EVIDENCE_STDIN_REQUIRED",
            "Evidence artifact must be provided directly through stdin with --evidence -",
        )
    evidence = _read_structured(evidence_source, "WORK_ITEM_EVIDENCE", stdin=stdin)
    if command == "remediate-task":
        return execute_operation(
            "remediate_task",
            {
                "item_id": _required(parsed, "--item"),
                "expected_baseline_fingerprint": _required(
                    parsed, "--expected-baseline"
                ),
                "evidence": evidence,
            },
            context=context,
        )
    if command == "task-result":
        return execute_operation(
            "task_result",
            {
                "item_id": _required(parsed, "--item"),
                "operation_id": _required(parsed, "--operation"),
                "status": _required(parsed, "--status"),
                "evidence": evidence,
            },
            context=context,
        )
    if command == "acceptance-item":
        return execute_operation(
            "record_acceptance",
            {
                "item_id": _required(parsed, "--item"),
                "action": _required(parsed, "--action"),
                "evidence": evidence,
            },
            context=context,
        )
    if command == "accept-item":
        return execute_operation(
            "accept_item",
            {
                "item_id": _required(parsed, "--item"),
                "evidence": evidence,
            },
            context=context,
        )
    if command == "gate-item":
        return execute_operation(
            "gate_item",
            {
                "item_id": _required(parsed, "--item"),
                "status": _required(parsed, "--status"),
                "evidence": evidence,
            },
            context=context,
        )
    raise GatedLoopError(
        "UNKNOWN_COMMAND",
        f"Unknown hdg command: {command}",
    )


def run_cli(
    argv: list[str],
    *,
    cwd: str | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    cwd = cwd or os.getcwd()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    command = next((value for value in argv if not value.startswith("--")), None)
    json_output = "--json" in argv
    with timing_session(
        command=command or "help",
        enabled="--timing" in argv,
    ) as timing:
        ok = False
        try:
            if command is None or "--help" in argv:
                with timed_stage("output.render"):
                    stdout.write(USAGE)
                ok = True
                return 0
            if command not in COMMANDS:
                error = GatedLoopError("UNKNOWN_COMMAND", f"Unknown hdg command: {command}")
                with timed_stage("output.render"):
                    if json_output:
                        stderr.write(rendered_json({"ok": False, "error": {"code": error.code, "message": error.message, "details": error.details}}))
                    else:
                        stderr.write(f"ERROR {error.code}: {error.message}\n")
                return error.exit_code
            with timed_stage("cli.parse"):
                parsed = _parse(argv)
            with timed_stage("command.execute"):
                result = _run(parsed, cwd=cwd, stdin=stdin)
            with timed_stage("output.render"):
                stdout.write(rendered_json({"ok": True, "result": result}) if parsed["json"] else rendered_json(result))
            ok = True
            return 0
        except GatedLoopError as error:
            with timed_stage("output.render"):
                if json_output:
                    stderr.write(rendered_json({"ok": False, "error": {"code": error.code, "message": error.message, "details": error.details}}))
                else:
                    stderr.write(f"ERROR {error.code}: {error.message}\n")
            return error.exit_code
        except Exception:
            error = GatedLoopError("INTERNAL_ERROR", "Unexpected error")
            with timed_stage("output.render"):
                if json_output:
                    stderr.write(rendered_json({"ok": False, "error": {"code": error.code, "message": error.message, "details": {}}}))
                else:
                    stderr.write(f"ERROR {error.code}: {error.message}\n")
            return 1
        finally:
            if timing is not None:
                stderr.write(
                    "HDG_TIMING "
                    + json.dumps(
                        timing.result(ok=ok),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )


def main(argv: list[str] | None = None) -> int:
    return run_cli(list(sys.argv[1:] if argv is None else argv))
