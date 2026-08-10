from __future__ import annotations

import json
from pathlib import Path
import unittest

from hdg.mcp_tools import tool_definitions


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "delivery-graph"
CODEX_MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
CODEX_HOOKS = PLUGIN / "hooks" / "hooks.json"
REPO_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


class AgentPluginProtocolTests(unittest.TestCase):
    def assert_plugin_relative_path(
        self,
        value: object,
        *,
        field: str,
    ) -> Path:
        self.assertIsInstance(value, str, field)
        assert isinstance(value, str)
        self.assertTrue(value.startswith("./"), field)
        self.assertNotIn("\\", value, field)
        candidate = (PLUGIN / value).resolve()
        try:
            candidate.relative_to(PLUGIN.resolve())
        except ValueError as error:
            self.fail(f"{field} escapes the plugin root: {error}")
        self.assertTrue(candidate.exists(), f"{field}: {candidate}")
        return candidate

    def test_all_tools_publish_current_structured_metadata(self) -> None:
        tools = tool_definitions()
        self.assertEqual(len(tools), 33)

        problems: list[str] = []
        for tool in tools:
            name = tool.get("name", "<unnamed>")
            title = tool.get("title")
            if not isinstance(title, str) or not title.strip():
                problems.append(f"{name}: non-empty title")

            output_schema = tool.get("outputSchema")
            if (
                not isinstance(output_schema, dict)
                or output_schema.get("type") != "object"
            ):
                problems.append(f"{name}: root object outputSchema")

            annotations = tool.get("annotations")
            for hint in (
                "readOnlyHint",
                "destructiveHint",
                "openWorldHint",
            ):
                if (
                    not isinstance(annotations, dict)
                    or not isinstance(annotations.get(hint), bool)
                ):
                    problems.append(f"{name}: boolean annotations.{hint}")

        self.assertFalse(problems, "\n" + "\n".join(problems))

    def test_codex_manifest_is_well_formed_and_paths_stay_in_plugin(
        self,
    ) -> None:
        manifest = _read_json(CODEX_MANIFEST)
        self.assertEqual(manifest.get("name"), "delivery-graph")
        self.assertEqual(manifest.get("name"), PLUGIN.name)
        self.assertRegex(
            str(manifest.get("name", "")),
            r"^[a-z0-9][a-z0-9-]*$",
        )
        self.assertRegex(
            str(manifest.get("version", "")),
            r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$",
        )
        self.assertIsInstance(manifest.get("description"), str)
        self.assertTrue(str(manifest["description"]).strip())

        plugin_manifest_files = [
            path.relative_to(PLUGIN / ".codex-plugin").as_posix()
            for path in (PLUGIN / ".codex-plugin").rglob("*")
            if path.is_file()
        ]
        self.assertEqual(plugin_manifest_files, ["plugin.json"])

        skills = manifest.get("skills")
        skill_paths = skills if isinstance(skills, list) else [skills]
        self.assertTrue(skill_paths)
        for index, path in enumerate(skill_paths):
            self.assert_plugin_relative_path(
                path,
                field=f"skills[{index}]",
            )

        mcp_servers = manifest.get("mcpServers")
        if isinstance(mcp_servers, str):
            mcp_path = self.assert_plugin_relative_path(
                mcp_servers,
                field="mcpServers",
            )
            mcp_config = _read_json(mcp_path)
            configured = mcp_config.get("mcp_servers", mcp_config)
            self.assertIsInstance(configured, dict)
            self.assertTrue(configured)
        else:
            # Inline server maps remain a legal compatibility shape.
            self.assertIsInstance(mcp_servers, dict)
            self.assertTrue(mcp_servers)
            for server_name, server in mcp_servers.items():
                with self.subTest(server=server_name):
                    self.assertIsInstance(server, dict)
                    self.assertIsInstance(server.get("command"), str)
                    self.assertTrue(server["command"].strip())
                    self.assertIsInstance(server.get("args", []), list)
                    self.assertTrue(
                        all(
                            isinstance(argument, str)
                            for argument in server.get("args", [])
                        )
                    )

        hooks = manifest.get("hooks")
        if hooks is None:
            self.assertTrue(CODEX_HOOKS.is_file())
        elif isinstance(hooks, str):
            self.assert_plugin_relative_path(hooks, field="hooks")
        elif isinstance(hooks, list):
            self.assertTrue(hooks)
            for index, path in enumerate(hooks):
                if isinstance(path, str):
                    self.assert_plugin_relative_path(
                        path,
                        field=f"hooks[{index}]",
                    )
                else:
                    self.assertIsInstance(path, dict)
        else:
            self.assertIsInstance(hooks, dict)

        apps = manifest.get("apps")
        if apps is not None:
            self.assert_plugin_relative_path(apps, field="apps")

        interface = manifest.get("interface")
        self.assertIsInstance(interface, dict)
        for field in (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
        ):
            self.assertIsInstance(interface.get(field), str, field)
            self.assertTrue(interface[field].strip(), field)
        capabilities = interface.get("capabilities")
        self.assertIsInstance(capabilities, list)
        self.assertTrue(capabilities)
        self.assertTrue(
            all(
                isinstance(capability, str) and capability.strip()
                for capability in capabilities
            )
        )
        for field in ("composerIcon", "logo"):
            if field in interface:
                self.assert_plugin_relative_path(
                    interface[field],
                    field=f"interface.{field}",
                )
        screenshots = interface.get("screenshots", [])
        self.assertIsInstance(screenshots, list)
        for index, path in enumerate(screenshots):
            self.assert_plugin_relative_path(
                path,
                field=f"interface.screenshots[{index}]",
            )

    def test_codex_and_claude_hooks_are_separated(self) -> None:
        codex_hooks = _read_json(CODEX_HOOKS)
        codex_events = codex_hooks.get("hooks")
        self.assertIsInstance(codex_events, dict)
        self.assertNotIn("StopFailure", codex_events)

        claude_candidates: list[Path] = []
        for path in sorted((PLUGIN / "hooks").glob("*.json")):
            if path.resolve() == CODEX_HOOKS.resolve():
                continue
            candidate = _read_json(path).get("hooks")
            if isinstance(candidate, dict) and "StopFailure" in candidate:
                claude_candidates.append(path)
        self.assertTrue(
            claude_candidates,
            "a separate Claude hooks JSON file must retain StopFailure",
        )

    def test_codex_hook_commands_use_plugin_root(self) -> None:
        hooks = _read_json(CODEX_HOOKS)["hooks"]
        self.assertIsInstance(hooks, dict)
        commands: list[str] = []
        for groups in hooks.values():
            self.assertIsInstance(groups, list)
            for group in groups:
                self.assertIsInstance(group, dict)
                handlers = group.get("hooks")
                self.assertIsInstance(handlers, list)
                for handler in handlers:
                    self.assertIsInstance(handler, dict)
                    if handler.get("type") != "command":
                        continue
                    command = handler.get("command")
                    self.assertIsInstance(command, str)
                    commands.append(command)
                    self.assertIn("${PLUGIN_ROOT}", command)
        self.assertTrue(commands)

    def test_repo_marketplace_has_install_policy_and_category(self) -> None:
        self.assertTrue(
            REPO_MARKETPLACE.is_file(),
            f"repository marketplace is missing: {REPO_MARKETPLACE}",
        )
        marketplace = _read_json(REPO_MARKETPLACE)
        self.assertEqual(
            marketplace.get("name"),
            "delivery-graph-development",
        )
        interface = marketplace.get("interface")
        self.assertIsInstance(interface, dict)
        self.assertEqual(
            interface.get("displayName"),
            "分层交付 Graph 控制面",
        )

        plugins = marketplace.get("plugins")
        self.assertIsInstance(plugins, list)
        entries = [
            item
            for item in plugins
            if isinstance(item, dict)
            and item.get("name") == "delivery-graph"
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        policy = entry.get("policy")
        self.assertIsInstance(policy, dict)
        for field in ("installation", "authentication"):
            self.assertIsInstance(policy.get(field), str)
            self.assertTrue(policy[field].strip())
        self.assertIsInstance(entry.get("category"), str)
        self.assertTrue(entry["category"].strip())

        source = entry.get("source")
        self.assertIsInstance(source, dict)
        self.assertIsInstance(source.get("source"), str)
        if source.get("source") == "local":
            source_path = source.get("path")
            self.assertEqual(source_path, "./plugins/delivery-graph")
            assert isinstance(source_path, str)
            self.assertNotIn("\\", source_path)
            resolved = (ROOT / source_path).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError as error:
                self.fail(f"marketplace source escapes the repo: {error}")
            self.assertEqual(resolved, PLUGIN.resolve())


if __name__ == "__main__":
    unittest.main()
