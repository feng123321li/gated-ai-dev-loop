"""ZCode real-host smoke implementation.

ZCode has no headless execution contract, so its smoke is two-phase: the
harness prepares a persistent disposable workspace and writes the prompt
outside it, a real ZCode session completes the middle step up to
RECORD_USER_CONFIRMATION, and the harness then verifies the scheduler.db
evidence chain.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .common import (
    __version__,
    prepare_workspace,
    PRIMARY_CHECKOUT_EXECUTION_REQUIREMENT,
    profile_name,
    render_smoke_prompt,
    validate_smoke,
)


def zcode_prompt(scenario: str) -> str:
    return render_smoke_prompt(
        profile_name(scenario),
        host="zcode",
        workspace_requirement=(
            "This ZCode session owns the CURRENT_WORKSPACE_SERIAL turn in "
            "the current checkout on `main`. Keep all coordination in this "
            "main session and do not open another checkout or run any "
            "`git worktree` command. When the Controller requests "
            "current-branch preparation, use only the exact branch and base "
            "commit in its gitBinding. Answer every controller-owned "
            "pendingInteraction through the host-native AskUserQuestion "
            "selector exactly as presented."
        ),
        execution_requirement=PRIMARY_CHECKOUT_EXECUTION_REQUIREMENT,
    )


def run_zcode_smoke(args: argparse.Namespace) -> int:
    """Two-phase ZCode smoke: prepare, real session, verify."""
    if args.model:
        raise RuntimeError(
            "--model does not apply to the two-phase zcode smoke"
        )
    if not args.execute and not args.verify_only:
        print(
            "real-host smoke is opt-in; inspect the plan, then repeat with "
            "--execute --workspace-dir <empty-dir>",
            file=sys.stderr,
        )
        print(zcode_prompt(args.scenario))
        return 2
    if args.workspace_dir is None:
        raise RuntimeError(
            "the zcode smoke is two-phase and needs a persistent "
            "--workspace-dir: prepare it here, complete the written prompt "
            "in a real ZCode session, then re-run with --verify-only"
        )
    workspace = Path(args.workspace_dir)
    if args.verify_only:
        result = validate_smoke(workspace, args.scenario, "zcode")
        result.update(
            {
                "host": "zcode",
                "scenario": args.scenario,
                "pluginVersion": __version__,
            }
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    workspace = prepare_workspace(workspace, "zcode")
    prompt_path = workspace.with_name(f"{workspace.name}-prompt.md")
    prompt_path.write_text(
        zcode_prompt(args.scenario),
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "host": "zcode",
                "scenario": args.scenario,
                "pluginVersion": __version__,
                "workspace": str(workspace),
                "promptFile": str(prompt_path),
                "nextAction": (
                    "Open a real ZCode session on the workspace directory, "
                    "paste the written prompt, and let it stop at "
                    "RECORD_USER_CONFIRMATION; then re-run this command "
                    "with --verify-only"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
