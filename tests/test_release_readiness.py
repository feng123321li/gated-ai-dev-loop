from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from hdg import __version__
from hdg.graph_model import (
    compile_delivery_graph,
    graph_assurance_profile,
)
from hdg.jsonio import fingerprint
from hdg.mcp_tools import tool_definitions
from hdg.model_core import validate_hierarchy_definition
from hdg.planning import freeze_hierarchy
from scripts.host_smoke import (
    claude_host_command,
    claude_prompt,
    codex_bootstrap_prompt,
    codex_host_command,
    codex_plan_prompt,
    codex_plugin_available,
    codex_resume_command,
    codex_resume_prompt,
    codex_session_id,
    find_smoke_artifact,
    prepare_workspace,
    run_smoke,
    zcode_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "examples" / "team-loops"


class TeamReleaseReadinessTests(unittest.TestCase):
    def test_team_loop_templates_are_valid_and_cover_both_profiles(
        self,
    ) -> None:
        expected = {
            "light-change.json": "LIGHT",
            "single-task-standard.json": "STANDARD",
            "parallel-group-standard.json": "STANDARD",
        }
        self.assertEqual(
            {path.name for path in TEMPLATES.glob("*.json")},
            set(expected),
        )
        for name, expected_profile in expected.items():
            with self.subTest(template=name):
                source = json.loads(
                    (TEMPLATES / name).read_text(encoding="utf-8")
                )
                hierarchy = validate_hierarchy_definition(source)
                graph = compile_delivery_graph(
                    hierarchy,
                    hierarchy_fingerprint=fingerprint(hierarchy),
                )
                self.assertEqual(
                    graph_assurance_profile(graph),
                    expected_profile,
                )
        light = json.loads(
            (TEMPLATES / "light-change.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(light["delivery"]["reviewLoop"])
        self.assertIsNone(light["root"]["reviewLoop"])

    def test_team_runbooks_cover_full_plugin_lifecycle(self) -> None:
        operations = (ROOT / "docs" / "team-operations.md").read_text(
            encoding="utf-8"
        )
        compatibility = (
            ROOT / "docs" / "host-compatibility.md"
        ).read_text(encoding="utf-8")
        templates = (ROOT / "docs" / "team-loop-templates.md").read_text(
            encoding="utf-8"
        )
        for heading in ("安装", "升级", "恢复", "卸载", "回滚"):
            self.assertIn(heading, operations)
        for host in ("Codex", "Claude Code"):
            self.assertIn(host, operations)
            self.assertIn(host, compatibility)
        self.assertIn("核心契约", compatibility)
        self.assertIn("真实宿主", compatibility)
        self.assertIn("完全相同", templates)
        self.assertIn("不表示前缀冲突", templates)
        self.assertIn("LIGHT", templates)
        self.assertIn("STANDARD", templates)

    def test_mcp_registration_and_five_minute_runbooks_are_actionable(self) -> None:
        lifecycle = (
            ROOT / "docs" / "mcp-host-lifecycle-contract.md"
        ).read_text(encoding="utf-8")
        quickstart = (
            ROOT / "docs" / "five-minute-quickstart.md"
        ).read_text(encoding="utf-8")
        for required in (
            "2026-07-28",
            "server/discover",
            "initialize",
            "SPAWN_STARTED",
            "CONNECTED",
            "FAILED",
            "stderr",
            "timeoutMs",
            "热重连",
            "mcp_registration_probe.py",
            "mcp_dynamic_catalog_demo.py",
            "EXTERNAL_SUPERVISOR_PER_TURN",
            "PLUGIN_MCP_UNAVAILABLE",
        ):
            self.assertIn(required, lifecycle)
        for required in (
            "5 分钟",
            "LIGHT",
            "基线",
            "状态指纹",
            "短任务不要求 heartbeat_loop",
            "PLUGIN_MCP_UNAVAILABLE",
        ):
            self.assertIn(required, quickstart)

    def test_gitlab_ci_has_contract_matrix_and_opt_in_host_jobs(self) -> None:
        ci = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        for version in ("3.10", "3.12", "3.14"):
            self.assertIn(version, ci)
        for command in (
            "python -m unittest",
            "python -m compileall",
            "python scripts/build_skill.py",
            "python scripts/validate_release.py",
        ):
            self.assertIn(command, ci)
        self.assertIn("host-smoke:codex", ci)
        self.assertIn("host-smoke:claude", ci)
        self.assertGreaterEqual(ci.count("when: manual"), 2)

    def test_host_smoke_probe_is_local_and_supports_all_hosts(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "scripts.host_smoke",
                "probe",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            set(result["hosts"]), {"codex", "claude-code", "zcode"}
        )
        self.assertFalse(result["modelInvocationStarted"])
        self.assertEqual(result["pluginVersion"], __version__)
        self.assertEqual(result["toolCount"], 32)

    def test_codex_probe_finds_candidate_alongside_installed_old_version(
        self,
    ) -> None:
        installed = {
            "installed": [
                {"name": "delivery-graph", "version": "0.31.0"},
                {"name": "delivery-graph", "version": __version__},
            ]
        }
        completed = subprocess.CompletedProcess(
            ["codex", "plugin", "list", "--json"],
            0,
            stdout=json.dumps(installed),
            stderr="",
        )
        with patch(
            "scripts.host_smoke.codex.shutil.which", return_value="codex"
        ):
            with patch(
                "scripts.host_smoke.codex.subprocess.run",
                return_value=completed,
            ):
                self.assertTrue(codex_plugin_available())

    def test_codex_smoke_disables_competing_version_for_one_invocation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            with patch(
                "scripts.host_smoke.codex.codex_plugin_state",
                return_value=(
                    True,
                    ["delivery-graph@majorbio-skills"],
                ),
            ):
                with patch(
                    "scripts.host_smoke.codex.shutil.which",
                    return_value="codex",
                ):
                    command = codex_host_command(
                        workspace=Path(temporary),
                        scenario="light",
                        model=None,
                    )
        self.assertIn(
            'plugins."delivery-graph@majorbio-skills".enabled=false',
            command,
        )
        self.assertNotIn("--ephemeral", command)

    def test_claude_smoke_hard_denies_final_user_confirmation(self) -> None:
        with TemporaryDirectory() as temporary:
            with patch(
                "scripts.host_smoke.claude.shutil.which",
                return_value="claude",
            ):
                command = claude_host_command(
                    workspace=Path(temporary),
                    scenario="light",
                    model=None,
                )
        disallowed_index = command.index("--disallowedTools")
        self.assertEqual(
            command[disallowed_index + 1],
            "mcp__plugin_delivery-graph_delivery-graph__"
            "record_user_confirmation",
        )
        self.assertIn("NEVER call record_user_confirmation", command[-1])

    def test_claude_smoke_allows_current_checkout_branch_preparation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            with patch(
                "scripts.host_smoke.claude.shutil.which",
                return_value="claude",
            ):
                command = claude_host_command(
                    workspace=Path(temporary),
                    scenario="light",
                    model=None,
                )
        allowed_index = command.index("--allowedTools")
        allowed_tools = command[allowed_index + 1].split(",")
        self.assertIn("Bash(git *)", allowed_tools)

    def test_claude_smoke_starts_on_main_primary_checkout(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            development = prepare_workspace(workspace, "claude-code")
            completed = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            git_dir = subprocess.run(
                ["git", "rev-parse", "--absolute-git-dir"],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
            common_dir = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(development, workspace)
        self.assertEqual(
            completed.stdout.strip(),
            "main",
        )
        self.assertEqual(git_dir, common_dir)

    def _zcode_run_args(
        self,
        *,
        execute: bool = True,
        workspace_dir: Path | None = None,
        verify_only: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            host="zcode",
            scenario="light",
            model=None,
            timeout=60,
            execute=execute,
            workspace_dir=workspace_dir,
            verify_only=verify_only,
        )

    def test_zcode_prompt_uses_primary_checkout_and_native_owner(self) -> None:
        prompt = zcode_prompt("light")
        self.assertIn("This ZCode session owns the", prompt)
        self.assertIn("CURRENT_WORKSPACE_SERIAL", prompt)
        self.assertIn("AskUserQuestion", prompt)
        self.assertIn("owner=zcode", prompt)
        self.assertIn("NEVER call record_user_confirmation", prompt)
        self.assertIn("do not open another checkout", prompt)
        self.assertNotIn("Claude Code session", prompt)
        self.assertNotIn("linked worktree", prompt)

    def test_zcode_prepare_workspace_is_primary_checkout(self) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            development = prepare_workspace(workspace, "zcode")
            completed = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            git_dir = subprocess.run(
                ["git", "rev-parse", "--absolute-git-dir"],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
            common_dir = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(development, workspace)
        self.assertEqual(completed.stdout.strip(), "main")
        self.assertEqual(git_dir, common_dir)

    def test_zcode_two_phase_smoke_requires_persistent_workspace(
        self,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "--workspace-dir"):
            run_smoke(self._zcode_run_args())

    def test_zcode_flags_are_rejected_for_other_hosts(self) -> None:
        args = argparse.Namespace(
            host="codex",
            scenario="light",
            model=None,
            timeout=60,
            execute=False,
            workspace_dir=Path("unused"),
            verify_only=False,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "apply only to the zcode smoke",
        ):
            run_smoke(args)

    def test_zcode_prepare_phase_writes_prompt_outside_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            self.assertEqual(
                run_smoke(
                    self._zcode_run_args(workspace_dir=base / "ws")
                ),
                0,
            )
            workspace = base / "ws"
            prompt_path = base / "ws-prompt.md"
            self.assertTrue(prompt_path.is_file())
            self.assertIn(
                "ZCode",
                prompt_path.read_text(encoding="utf-8"),
            )
            self.assertIsNone(find_smoke_artifact(workspace))

    def test_host_smoke_accepts_content_evidence_not_a_fixed_filename(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            artifact = workspace / "evidence" / "result.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                "Delivery Graph real-host smoke passed.\n",
                encoding="utf-8",
            )
            self.assertEqual(find_smoke_artifact(workspace), artifact)

    def test_host_smoke_accepts_readme_only_after_this_run_changes_it(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            prepare_workspace(workspace, "claude-code")
            self.assertIsNone(find_smoke_artifact(workspace))
            readme = workspace / "README.md"
            readme.write_text(
                "# Delivery Graph host smoke\n"
                "- LIGHT real-host smoke: PASS\n",
                encoding="utf-8",
            )
            self.assertEqual(find_smoke_artifact(workspace), readme)

    def test_claude_host_smoke_uses_current_workspace_serial_dispatch(
        self,
    ) -> None:
        prompt = claude_prompt("light")
        self.assertIn("current-host dispatch only", prompt)
        self.assertIn("never dispatch to another Agent", prompt)
        self.assertIn("Do not read prior Codex/Claude", prompt)
        self.assertIn("Do not call TaskCreate", prompt)
        self.assertIn("CURRENT_WORKSPACE_SERIAL", prompt)
        self.assertIn(
            "PREPARE_CURRENT_WORKSPACE_BRANCH_THEN_RESUME_EXECUTION",
            prompt,
        )
        self.assertIn("gitBinding", prompt)
        self.assertIn("current checkout", prompt)
        self.assertIn("resume_execution_mode", prompt)
        self.assertIn("plan_dispatch_batch", prompt)
        self.assertIn("independent current-host child", prompt)
        self.assertIn("call dispatch_loop first", prompt)
        self.assertIn("operation_id", prompt)
        self.assertIn("`delivery-graph smoke\\n`", prompt)
        self.assertNotIn("hostDispatch", prompt)
        self.assertNotIn("delivery-coordinator", prompt)
        self.assertNotIn("background coordinator", prompt)
        self.assertNotIn("linked worktree", prompt)
        self.assertNotIn("git worktree add", prompt)
        self.assertIn("short LIGHT receiver may finish without", prompt)
        self.assertNotIn("smoke is failed if LOOP_HEARTBEAT is absent", prompt)

    def test_codex_host_smoke_uses_reserved_independent_receivers(self) -> None:
        bootstrap = codex_bootstrap_prompt("light")
        resumed = codex_resume_prompt("light")
        self.assertIn("Do not claim any Loop", bootstrap)
        self.assertIn("second invocation will resume", bootstrap)
        self.assertIn("plan_dispatch_batch", resumed)
        self.assertIn("dispatch_loop", resumed)
        self.assertIn("operation_id", resumed)
        self.assertIn("read loop_context once", resumed)
        self.assertIn("distinct host-native child", codex_resume_prompt("standard"))
        self.assertNotIn("SessionStart", resumed)
        self.assertNotIn("claim_current_task", resumed)
        self.assertIn("short LIGHT receiver may finish without", resumed)
        standard = codex_resume_prompt("standard")
        self.assertIn("heartbeat_loop", standard)

    def test_codex_host_smoke_resumes_exact_bootstrap_thread(self) -> None:
        with TemporaryDirectory() as temporary:
            log_path = Path(temporary, "codex.jsonl")
            log_path.write_text(
                json.dumps(
                    {
                        "type": "thread.started",
                        "thread_id": "codex-smoke-thread",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                codex_session_id(log_path),
                "codex-smoke-thread",
            )
        with (
            patch("scripts.host_smoke.codex.shutil.which", return_value="codex"),
            patch(
                "scripts.host_smoke.codex.codex_plugin_state",
                return_value=(True, []),
            ),
        ):
            command = codex_resume_command(
                session_id="codex-smoke-thread",
                prompt="continue",
                model=None,
            )
        self.assertEqual(command[1:3], ["exec", "resume"])
        self.assertNotIn("--dangerously-bypass-hook-trust", command)
        self.assertEqual(command[-2:], ["codex-smoke-thread", "continue"])

    def test_release_surfaces_and_public_automatic_contract_match(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        version = re.search(
            r'^version = "([^"]+)"$', pyproject, flags=re.MULTILINE
        )
        self.assertIsNotNone(version)
        expected_version = version.group(1)
        for manifest in (
            ROOT
            / "plugins"
            / "delivery-graph"
            / ".codex-plugin"
            / "plugin.json",
            ROOT
            / "plugins"
            / "delivery-graph"
            / ".claude-plugin"
            / "plugin.json",
        ):
            self.assertEqual(
                json.loads(manifest.read_text(encoding="utf-8"))["version"],
                expected_version,
            )
        self.assertIn(
            f"当前版本：**{expected_version}**",
            (ROOT / "README.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(len(tool_definitions()), 32)
        self.assertNotIn("execution_mode", inspect.signature(freeze_hierarchy).parameters)
        tools = {tool["name"]: tool for tool in tool_definitions()}
        self.assertNotIn(
            "execution_mode",
            tools["freeze_hierarchy"]["inputSchema"]["properties"],
        )
        self.assertEqual(
            tools["dispatch_loop"]["inputSchema"]["properties"][
                "dispatch_mode"
            ]["enum"],
            ["AUTO", "MANUAL"],
        )

    def test_dashboard_resource_is_declared_as_wheel_package_data(
        self,
    ) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        package_data = re.search(
            r"\[tool\.setuptools\.package-data\]\s+"
            r"hdg\s*=\s*\[(?P<patterns>[^]]+)\]",
            pyproject,
        )
        self.assertIsNotNone(package_data)
        self.assertIn('"assets/*.html"', package_data.group("patterns"))

        source = ROOT / "src" / "hdg" / "assets" / "delivery-dashboard.html"
        canonical = (
            ROOT
            / "skills"
            / "delivery-graph"
            / "scripts"
            / "hdg"
            / "assets"
            / "delivery-dashboard.html"
        )
        plugin = (
            ROOT
            / "plugins"
            / "delivery-graph"
            / "skills"
            / "delivery-graph"
            / "scripts"
            / "hdg"
            / "assets"
            / "delivery-dashboard.html"
        )
        self.assertEqual(canonical.read_bytes(), source.read_bytes())
        self.assertEqual(plugin.read_bytes(), source.read_bytes())

    def test_offline_built_wheel_contains_dashboard_resource(self) -> None:
        if any(
            importlib.util.find_spec(module) is None
            for module in ("pip", "setuptools", "wheel")
        ):
            self.skipTest(
                "offline wheel build requires installed pip, setuptools, "
                "and wheel"
            )

        with TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            project = temporary_root / "project"
            distribution = temporary_root / "dist"
            project.mkdir()
            shutil.copy2(ROOT / "pyproject.toml", project / "pyproject.toml")
            shutil.copytree(
                ROOT / "src",
                project / "src",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--no-index",
                    "--disable-pip-version-check",
                    "--wheel-dir",
                    str(distribution),
                    str(project),
                ],
                cwd=temporary_root,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            wheels = list(distribution.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                dashboard = archive.read(
                    "hdg/assets/delivery-dashboard.html"
                )

        self.assertEqual(
            dashboard,
            (
                ROOT
                / "src"
                / "hdg"
                / "assets"
                / "delivery-dashboard.html"
            ).read_bytes(),
        )

    def test_release_validator_passes_current_candidate(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "scripts" / "validate_release.py"),
            ],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("release candidate valid", completed.stdout.lower())


if __name__ == "__main__":
    unittest.main()
