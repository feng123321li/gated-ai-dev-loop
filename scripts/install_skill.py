#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


SKILL_NAME = "layered-delivery"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPOSITORY_ROOT / "skills" / SKILL_NAME


@dataclass(frozen=True)
class Options:
    target: str = "both"
    scope: str = "user"
    project_root: str | None = None
    dry_run: bool = False
    force: bool = False
    help: bool = False


def parse_args(argv: Sequence[str]) -> Options:
    values = {
        "target": "both",
        "scope": "user",
        "project_root": None,
        "dry_run": False,
        "force": False,
        "help": False,
    }
    seen: set[str] = set()
    index = 0
    while index < len(argv):
        option = argv[index]
        if option in seen:
            raise ValueError(f"参数重复: {option}")
        seen.add(option)
        if option in {"--dry-run", "--force", "--help"}:
            values[option[2:].replace("-", "_")] = True
            index += 1
            continue
        if option not in {"--target", "--scope", "--project-root"}:
            raise ValueError(f"未知参数: {option}")
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            raise ValueError(f"{option} 缺少参数值")
        values[option[2:].replace("-", "_")] = argv[index + 1]
        index += 2
    if values["target"] not in {"codex", "claude", "both"}:
        raise ValueError("--target 必须是 codex、claude 或 both")
    if values["scope"] not in {"user", "project"}:
        raise ValueError("--scope 必须是 user 或 project")
    if values["scope"] == "user" and values["project_root"]:
        raise ValueError("--project-root 只能与 --scope project 一起使用")
    return Options(**values)


def resolve_targets(
    options: Options,
    *,
    home: Path | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    home = (home or Path.home()).absolute()
    cwd = (cwd or Path.cwd()).absolute()
    environ = environ or os.environ
    project_root = Path(options.project_root).absolute() if options.project_root else cwd
    selected = ("codex", "claude") if options.target == "both" else (options.target,)
    targets = []
    for host in selected:
        if options.scope == "project":
            root = project_root / (".agents/skills" if host == "codex" else ".claude/skills")
        elif host == "codex":
            root = Path(environ.get("CODEX_HOME", str(home / ".codex"))).absolute() / "skills"
        else:
            root = home / ".claude" / "skills"
        targets.append({"host": host, "root": str(root), "destination": str(root / SKILL_NAME)})
    return targets


def _assert_no_symlink_components(candidate: Path) -> None:
    absolute = Path(os.path.abspath(candidate))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"拒绝符号链接路径: {current}")


def _assert_plain_source(directory: Path) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Skill 源目录无效: {directory}")
    for child in directory.rglob("*"):
        if child.is_symlink():
            raise ValueError(f"Skill 源目录包含符号链接: {child}")
        if not child.is_dir() and not child.is_file():
            raise ValueError(f"Skill 源目录包含非常规文件: {child}")


def _install_one(source: Path, target: dict[str, str], options: Options) -> dict[str, object]:
    root = Path(target["root"])
    destination = Path(target["destination"])
    if destination.parent.absolute() != root.absolute() or destination.name != SKILL_NAME:
        raise ValueError(f"安装目标越界: {destination}")
    _assert_no_symlink_components(root)
    _assert_no_symlink_components(destination)
    exists = destination.exists()
    if exists and (not destination.is_dir() or destination.is_symlink()):
        raise ValueError(f"安装目标不是普通目录: {destination}")
    if options.dry_run:
        return {**target, "action": "replace" if exists else "create", "dryRun": True}
    if exists and not options.force:
        raise ValueError(f"安装目标已存在，请使用 --force: {destination}")
    root.mkdir(parents=True, exist_ok=True)
    _assert_no_symlink_components(root)
    staging = root / f".{SKILL_NAME}.tmp-{uuid.uuid4().hex}"
    backup = root / f".{SKILL_NAME}.backup-{uuid.uuid4().hex}"
    moved = False
    try:
        shutil.copytree(source, staging)
        if exists:
            os.replace(destination, backup)
            moved = True
        try:
            os.replace(staging, destination)
        except Exception:
            if moved:
                os.replace(backup, destination)
            raise
        if moved:
            shutil.rmtree(backup)
        return {**target, "action": "replaced" if exists else "created", "dryRun": False}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup)


def install_skill(
    options: Options,
    *,
    source: Path = DEFAULT_SOURCE,
    home: Path | None = None,
    cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    source = source.absolute()
    _assert_plain_source(source)
    results = [
        _install_one(source, target, options)
        for target in resolve_targets(options, home=home, cwd=cwd, environ=environ)
    ]
    return {"skill": SKILL_NAME, "scope": options.scope, "results": results}


HELP = """安装分层交付治理 Skill

用法:
  python scripts/install_skill.py [选项]

选项:
  --target codex|claude|both   安装目标，默认 both
  --scope user|project         安装范围，默认 user
  --project-root <path>        项目级安装根目录
  --dry-run                    只显示计划，不写入
  --force                      安全替换已有安装
  --help                       显示帮助
"""


def main(argv: Sequence[str] | None = None) -> int:
    try:
        options = parse_args(list(sys.argv[1:] if argv is None else argv))
        if options.help:
            print(HELP, end="")
            return 0
        print(json.dumps(install_skill(options), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(f"安装失败: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
