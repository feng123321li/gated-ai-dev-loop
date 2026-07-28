#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = REPOSITORY_ROOT / "src" / "hdg"
CANONICAL_SKILL = REPOSITORY_ROOT / "skills" / "layered-delivery"
SKILL_SCRIPTS = CANONICAL_SKILL / "scripts"
TARGET_PACKAGE = SKILL_SCRIPTS / "hdg"
TARGET_ENTRY = SKILL_SCRIPTS / "hdg.py"
TARGET_MCP_ENTRY = SKILL_SCRIPTS / "hdg_mcp.py"
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "layered-delivery"
PLUGIN_SKILL = PLUGIN_ROOT / "skills" / "layered-delivery"

MCP_ENTRY = '''#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from hdg.mcp_server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _copy_source(
    source: Path,
    destination: Path,
    *,
    excluded_names: frozenset[str] = frozenset(),
) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=lambda _directory, names: [
            name
            for name in names
            if name in excluded_names
            or name == "__pycache__"
            or name.endswith((".pyc", ".pyo"))
        ],
    )


def _replace_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    backup = destination.parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
    moved = False
    try:
        _copy_source(source, staging)
        if destination.exists():
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
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup)


def build_plugin_payload() -> Path:
    if not CANONICAL_SKILL.is_dir() or CANONICAL_SKILL.is_symlink():
        raise RuntimeError(f"Canonical Skill source is invalid: {CANONICAL_SKILL}")
    _replace_tree(CANONICAL_SKILL, PLUGIN_SKILL)
    return PLUGIN_SKILL


def build_skill() -> tuple[Path, Path]:
    if not SOURCE_PACKAGE.is_dir() or SOURCE_PACKAGE.is_symlink():
        raise RuntimeError(f"Python controller source is invalid: {SOURCE_PACKAGE}")
    SKILL_SCRIPTS.mkdir(parents=True, exist_ok=True)
    staging = SKILL_SCRIPTS / f".hdg.tmp-{uuid.uuid4().hex}"
    backup = SKILL_SCRIPTS / f".hdg.backup-{uuid.uuid4().hex}"
    moved = False
    try:
        _copy_source(
            SOURCE_PACKAGE,
            staging,
            excluded_names=frozenset({"cli.py", "__main__.py"}),
        )
        if TARGET_PACKAGE.exists():
            os.replace(TARGET_PACKAGE, backup)
            moved = True
        try:
            os.replace(staging, TARGET_PACKAGE)
        except Exception:
            if moved:
                os.replace(backup, TARGET_PACKAGE)
            raise
        if moved:
            shutil.rmtree(backup)
        if TARGET_ENTRY.exists():
            TARGET_ENTRY.unlink()
        temporary_mcp_entry = TARGET_MCP_ENTRY.with_name(
            f"{TARGET_MCP_ENTRY.name}.tmp-{uuid.uuid4().hex}"
        )
        temporary_mcp_entry.write_text(MCP_ENTRY, encoding="utf-8", newline="\n")
        os.replace(temporary_mcp_entry, TARGET_MCP_ENTRY)
        build_plugin_payload()
        return TARGET_MCP_ENTRY, TARGET_PACKAGE
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and TARGET_PACKAGE.exists():
            shutil.rmtree(backup)


def main() -> int:
    try:
        entry, package = build_skill()
        print(f"Built Plugin MCP controller: {entry}")
        print(f"Bundled Plugin runtime package: {package}")
        print(f"Built dual-host Plugin payload: {PLUGIN_SKILL}")
        return 0
    except Exception as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
