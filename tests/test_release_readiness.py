from __future__ import annotations

import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

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
    _codex_plugin_available,
    _find_smoke_artifact,
    _host_command,
    _prepare_workspace,
    _prompt,
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

    def test_host_smoke_probe_is_local_and_supports_both_hosts(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "scripts" / "host_smoke.py"),
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
        self.assertEqual(set(result["hosts"]), {"codex", "claude-code"})
        self.assertFalse(result["modelInvocationStarted"])
        self.assertEqual(result["pluginVersion"], __version__)
        self.assertEqual(result["toolCount"], 31)

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
        with patch("scripts.host_smoke.shutil.which", return_value="codex"):
            with patch(
                "scripts.host_smoke.subprocess.run",
                return_value=completed,
            ):
                self.assertTrue(_codex_plugin_available())

    def test_codex_smoke_disables_competing_version_for_one_invocation(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            with patch(
                "scripts.host_smoke._codex_plugin_state",
                return_value=(
                    True,
                    ["delivery-graph@majorbio-skills"],
                ),
            ):
                with patch(
                    "scripts.host_smoke.shutil.which",
                    return_value="codex",
                ):
                    command = _host_command(
                        "codex",
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
                "scripts.host_smoke.shutil.which",
                return_value="claude",
            ):
                command = _host_command(
                    "claude-code",
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

    def test_claude_smoke_starts_on_main_primary_checkout(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            development = _prepare_workspace(workspace, "claude-code")
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

    def test_codex_smoke_starts_in_a_linked_feature_worktree(self) -> None:
        with TemporaryDirectory() as temporary:
            repository = Path(temporary, "repository")
            development = _prepare_workspace(repository, "codex")
            primary_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repository,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
            feature_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=development,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
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
        self.assertNotEqual(development, repository)
        self.assertEqual(primary_branch, "main")
        self.assertEqual(feature_branch, "feature/m_lf_host_smoke")
        self.assertNotEqual(git_dir, common_dir)

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
            self.assertEqual(_find_smoke_artifact(workspace), artifact)

    def test_host_smoke_accepts_readme_only_after_this_run_changes_it(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            _prepare_workspace(workspace, "claude-code")
            self.assertIsNone(_find_smoke_artifact(workspace))
            readme = workspace / "README.md"
            readme.write_text(
                "# Delivery Graph host smoke\n"
                "- LIGHT real-host smoke: PASS\n",
                encoding="utf-8",
            )
            self.assertEqual(_find_smoke_artifact(workspace), readme)

    def test_host_smoke_prompt_forbids_cross_agent_dispatch(self) -> None:
        prompt = _prompt("light", "claude-code")
        self.assertIn("current-host dispatch only", prompt)
        self.assertIn("never dispatch to another Agent", prompt)
        self.assertIn("Do not read prior Codex/Claude", prompt)
        self.assertIn("delivery-coordinator background agent", prompt)
        self.assertIn("Do not call TaskCreate", prompt)
        self.assertIn("dispatch_loop first, then loop_context", prompt)
        self.assertIn("`delivery-graph smoke\\n`", prompt)

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
        self.assertEqual(len(tool_definitions()), 31)
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
