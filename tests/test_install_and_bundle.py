from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from scripts.build_skill import SOURCE_PACKAGE, TARGET_ENTRY, TARGET_PACKAGE, build_skill
from scripts.install_skill import Options, install_skill, parse_args


def file_map(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


class InstallAndBundleTests(unittest.TestCase):
    def test_runtime_control_directory_is_git_ignored(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        gitignore = (repository_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".layered-delivery/", gitignore.splitlines())

    def test_canonical_skill_name_is_layered_delivery(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        self.assertEqual(skill_root.name, "layered-delivery")
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        agent_metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("name: layered-delivery", skill)
        self.assertIn("$layered-delivery", agent_metadata)

    def test_manual_skill_contract_requires_a_copyable_session_command(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        claude_automation = (skill_root / "references" / "claude-automation.md").read_text(encoding="utf-8")
        self.assertIn("必须直接展示返回的 `handoffCommand`", skill)
        self.assertIn("不得只给出 `requirement-handoff.md` 链接", skill)
        self.assertIn(
            "面向人的状态报告必须把 SQLite 和控制器 JSON 中的 UTC 时间转换为当前运行环境的本机时区",
            skill,
        )
        self.assertIn("Claude Code 的权限模式不能由聊天提示切换", skill)
        self.assertIn("`acceptEdits` 不是无人值守模式", skill)
        self.assertIn("claudeCodeAutoHandoff", skill)
        self.assertIn("claude-automation.md", skill)
        self.assertIn("claude -p --permission-mode auto", claude_automation)
        self.assertIn("项目级 `.claude/settings.json`", claude_automation)
        self.assertIn("不默认使用 `bypassPermissions`", claude_automation)

    def test_skill_contract_keeps_controller_invocation_host_portable(self) -> None:
        skill_root = TARGET_PACKAGE.parent.parent
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        transport = (skill_root / "references" / "stdin-transport.md").read_text(encoding="utf-8")
        self.assertIn("从当前 Skill 元数据解析 `<skill-root>`", skill)
        self.assertIn("不得固化用户目录、Skill 安装位置或操作系统路径", skill)
        self.assertIn("直接消费 stdout", skill)
        self.assertIn("保留 stderr", skill)
        self.assertIn("宿主无关调用契约", transport)
        self.assertIn("不得使用临时 JSON 中转只读查询结果", transport)
        self.assertIn("恢复入口是 `graph-frontier`，不是 `task-context`", transport)

    def test_build_is_reproducible_and_bundle_matches_source(self) -> None:
        build_skill()
        self.assertEqual(file_map(SOURCE_PACKAGE), file_map(TARGET_PACKAGE))
        self.assertTrue(TARGET_ENTRY.is_file())
        self.assertIn("from hdg.cli import main", TARGET_ENTRY.read_text(encoding="utf-8"))

    def test_installer_argument_contract(self) -> None:
        options = parse_args(["--target", "codex", "--scope", "project", "--project-root", "C:/work", "--dry-run"])
        self.assertEqual(options.target, "codex")
        self.assertTrue(options.dry_run)
        with self.assertRaises(ValueError):
            parse_args(["--target", "unknown"])

    def test_installer_copies_a_self_contained_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            home = temporary_path / "home"
            source = temporary_path / "source" / "layered-delivery"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("---\nname: layered-delivery\ndescription: test\n---\n", encoding="utf-8")
            (source / "scripts").mkdir()
            (source / "scripts" / "hdg.py").write_text("print('ok')\n", encoding="utf-8")
            result = install_skill(
                Options(target="both", scope="user"),
                source=source,
                home=home,
                cwd=temporary_path,
                environ={},
            )
            self.assertEqual(result["skill"], "layered-delivery")
            self.assertEqual([item["action"] for item in result["results"]], ["created", "created"])
            self.assertTrue((home / ".codex" / "skills" / "layered-delivery" / "scripts" / "hdg.py").is_file())
            self.assertTrue((home / ".claude" / "skills" / "layered-delivery" / "scripts" / "hdg.py").is_file())

    def test_runtime_imports_only_standard_library_or_local_modules(self) -> None:
        allowed_roots = {
            "hdg", "__future__", "abc", "argparse", "ast", "base64", "collections", "contextlib",
            "copy", "dataclasses", "datetime", "enum", "functools", "hashlib", "io", "json",
            "os", "pathlib", "posixpath", "re", "shutil", "sqlite3", "stat", "sys", "tempfile", "time",
            "typing", "unittest", "uuid",
        }
        repository_root = Path(__file__).resolve().parents[1]
        runtime_paths = [
            *SOURCE_PACKAGE.glob("*.py"),
            repository_root / "bin" / "hdg.py",
            repository_root / "scripts" / "build_skill.py",
            repository_root / "scripts" / "install_skill.py",
        ]
        for path in runtime_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]
                else:
                    continue
                self.assertTrue(set(roots) <= allowed_roots, f"non-stdlib import in {path}: {roots}")


if __name__ == "__main__":
    unittest.main()
