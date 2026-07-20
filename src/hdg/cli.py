from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from .acceptance import accept_work_item, record_acceptance, record_work_item_gate
from .errors import GatedLoopError
from .execution import (
    build_task_context,
    claim_task,
    dispatch_task,
    list_ready_tasks,
    record_task_result,
)
from .fs_safe import read_regular_file
from .host_runtime import is_agent_runtime
from .interactions import list_interactions, record_interaction
from .jsonio import rendered_json
from .planning import (
    freeze_hierarchy,
    prepare_hierarchy,
    refresh_work_item_projections,
    retry_work_item,
)


COMMANDS = (
    "prepare-hierarchy",
    "freeze-hierarchy",
    "ready-tasks",
    "task-context",
    "dispatch-task",
    "claim-task",
    "task-result",
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
    "--action", "--development-mode", "--expected-hierarchy", "--interaction",
}
FLAG_OPTIONS = {"--json", "--help", "--confirmed", "--dogfood"}
COMMAND_OPTIONS = {
    "prepare-hierarchy": {"--json", "--help", "--definition", "--host-runtime", "--dogfood"},
    "freeze-hierarchy": {
        "--json", "--help", "--item", "--expected-hierarchy", "--development-mode", "--confirmed", "--dogfood",
    },
    "ready-tasks": {"--json", "--help", "--item"},
    "task-context": {"--json", "--help", "--item", "--dogfood"},
    "claim-task": {"--json", "--help", "--item", "--owner", "--operation", "--dogfood"},
    "dispatch-task": {"--json", "--help", "--item", "--owner", "--operation", "--dogfood"},
    "task-result": {"--json", "--help", "--item", "--operation", "--status", "--evidence", "--dogfood"},
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

  prepare-hierarchy --definition <file|-> --host-runtime <agent>  # writes one complete requirement tree
  freeze-hierarchy --item <root-id> --expected-hierarchy <sha256> --development-mode active|manual --confirmed
  ready-tasks --item <root-or-subtree-id>
  task-context --item <task-id>
  dispatch-task --item <task-id> --owner <owner> --operation <id>
  claim-task --item <task-id> --owner <owner> --operation <id>
  task-result --item <task-id> --operation <id> --status IMPLEMENTED|BLOCKED --evidence -
  retry-item --item <id> --expected-baseline <sha256>
  gate-item --item <id> --status PASS|FAIL --evidence -
  accept-item --item <id> --evidence -
  acceptance-item --item <root-id> --action INDEPENDENT_REVIEW_PASS|HUMAN_REVIEW_ACCEPTED|USER_CONFIRMED --evidence -
  record-interaction --item <id> --interaction <file|->
  interaction-log --item <id>
  refresh-projections

Common options:
  --json  Render the command result or error as structured JSON.

In the hierarchical-delivery-governance implementation repository, every command that writes control state also requires --dogfood.
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
            if option not in COMMAND_OPTIONS[command]:
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
    cwd: str,
    stdin: TextIO,
) -> dict[str, Any]:
    try:
        if source == "-":
            text = stdin.read()
        else:
            portable = source.replace("\\", "/").lower()
            basename = portable.split("/")[-1]
            if basename.startswith(".env") or "production" in portable:
                raise GatedLoopError("INPUT_PATH_FORBIDDEN", "Structured input path is forbidden")
            text = read_regular_file(cwd, source).decode("utf-8")
    except GatedLoopError:
        raise
    except Exception:
        raise GatedLoopError(f"{kind}_READ", f"Unable to read {kind.lower()} JSON")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise GatedLoopError(f"{kind}_PARSE", f"{kind.lower()} JSON must be a mapping")
    if not isinstance(value, dict):
        raise GatedLoopError(f"{kind}_PARSE", f"{kind.lower()} JSON must be a mapping")
    return value


def _run(parsed: dict[str, Any], *, cwd: str, stdin: TextIO) -> Any:
    common = {"root": cwd, "explicit_dogfood": parsed["dogfood"]}
    command = parsed["command"]
    if command == "prepare-hierarchy":
        definition = _read_structured(
            _required(parsed, "--definition"),
            "HIERARCHY_DEFINITION",
            cwd=cwd,
            stdin=stdin,
        )
        return prepare_hierarchy(
            **common,
            hierarchy=definition,
            host_runtime=_required(parsed, "--host-runtime"),
        )
    if command == "freeze-hierarchy":
        return freeze_hierarchy(
            **common,
            root_id=_required(parsed, "--item"),
            expected_hierarchy_fingerprint=_required(parsed, "--expected-hierarchy"),
            development_mode=_required(parsed, "--development-mode"),
            confirmed=parsed["confirmed"],
        )
    if command == "ready-tasks":
        return list_ready_tasks(root=cwd, work_item_id=_required(parsed, "--item"))
    if command == "task-context":
        return build_task_context(**common, item_id=_required(parsed, "--item"))
    if command == "claim-task":
        return claim_task(
            **common,
            item_id=_required(parsed, "--item"),
            owner=_required(parsed, "--owner"),
            operation_id=_required(parsed, "--operation"),
        )
    if command == "dispatch-task":
        return dispatch_task(
            **common,
            item_id=_required(parsed, "--item"),
            owner=_required(parsed, "--owner"),
            operation_id=_required(parsed, "--operation"),
        )
    if command == "retry-item":
        return retry_work_item(
            **common,
            item_id=_required(parsed, "--item"),
            expected_baseline_fingerprint=_required(parsed, "--expected-baseline"),
        )
    if command == "refresh-projections":
        return refresh_work_item_projections(**common)
    if command == "record-interaction":
        interaction = _read_structured(
            _required(parsed, "--interaction"), "WORK_ITEM_INTERACTION", cwd=cwd, stdin=stdin
        )
        return record_interaction(
            **common,
            item_id=_required(parsed, "--item"),
            interaction=interaction,
        )
    if command == "interaction-log":
        return list_interactions(root=cwd, item_id=_required(parsed, "--item"))
    evidence_source = _required(parsed, "--evidence")
    if evidence_source != "-":
        raise GatedLoopError(
            "WORK_ITEM_EVIDENCE_STDIN_REQUIRED",
            "Evidence artifact must be provided directly through stdin with --evidence -",
        )
    evidence = _read_structured(evidence_source, "WORK_ITEM_EVIDENCE", cwd=cwd, stdin=stdin)
    if command == "task-result":
        return record_task_result(
            **common,
            item_id=_required(parsed, "--item"),
            operation_id=_required(parsed, "--operation"),
            status=_required(parsed, "--status"),
            evidence=evidence,
        )
    if command == "acceptance-item":
        return record_acceptance(
            **common,
            item_id=_required(parsed, "--item"),
            action=_required(parsed, "--action"),
            evidence=evidence,
        )
    if command == "accept-item":
        return accept_work_item(**common, item_id=_required(parsed, "--item"), evidence=evidence)
    return record_work_item_gate(
        **common,
        item_id=_required(parsed, "--item"),
        status=_required(parsed, "--status"),
        evidence=evidence,
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
    if command is None or "--help" in argv:
        stdout.write(USAGE)
        return 0
    if command not in COMMANDS:
        error = GatedLoopError("UNKNOWN_COMMAND", f"Unknown hdg command: {command}")
        if json_output:
            stderr.write(rendered_json({"ok": False, "error": {"code": error.code, "message": error.message, "details": error.details}}))
        else:
            stderr.write(f"ERROR {error.code}: {error.message}\n")
        return error.exit_code
    try:
        parsed = _parse(argv)
        result = _run(parsed, cwd=cwd, stdin=stdin)
        stdout.write(rendered_json({"ok": True, "result": result}) if parsed["json"] else rendered_json(result))
        return 0
    except GatedLoopError as error:
        if json_output:
            stderr.write(rendered_json({"ok": False, "error": {"code": error.code, "message": error.message, "details": error.details}}))
        else:
            stderr.write(f"ERROR {error.code}: {error.message}\n")
        return error.exit_code
    except Exception:
        error = GatedLoopError("INTERNAL_ERROR", "Unexpected error")
        if json_output:
            stderr.write(rendered_json({"ok": False, "error": {"code": error.code, "message": error.message, "details": {}}}))
        else:
            stderr.write(f"ERROR {error.code}: {error.message}\n")
        return 1


def main(argv: list[str] | None = None) -> int:
    return run_cli(list(sys.argv[1:] if argv is None else argv))
