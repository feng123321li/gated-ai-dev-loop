#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = REPOSITORY_ROOT / "src" / "hdg"
SKILL_SCRIPTS = REPOSITORY_ROOT / "skills" / "layered-delivery" / "scripts"
TARGET_PACKAGE = SKILL_SCRIPTS / "hdg"
TARGET_ENTRY = SKILL_SCRIPTS / "hdg.py"

ENTRY = '''#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from hdg.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _copy_source(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def build_skill() -> tuple[Path, Path]:
    if not SOURCE_PACKAGE.is_dir() or SOURCE_PACKAGE.is_symlink():
        raise RuntimeError(f"Python controller source is invalid: {SOURCE_PACKAGE}")
    SKILL_SCRIPTS.mkdir(parents=True, exist_ok=True)
    staging = SKILL_SCRIPTS / f".hdg.tmp-{uuid.uuid4().hex}"
    backup = SKILL_SCRIPTS / f".hdg.backup-{uuid.uuid4().hex}"
    moved = False
    try:
        _copy_source(SOURCE_PACKAGE, staging)
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
        temporary_entry = TARGET_ENTRY.with_name(f"{TARGET_ENTRY.name}.tmp-{uuid.uuid4().hex}")
        temporary_entry.write_text(ENTRY, encoding="utf-8", newline="\n")
        os.replace(temporary_entry, TARGET_ENTRY)
        return TARGET_ENTRY, TARGET_PACKAGE
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and TARGET_PACKAGE.exists():
            shutil.rmtree(backup)


def main() -> int:
    try:
        entry, package = build_skill()
        print(f"Built Python Skill controller: {entry}")
        print(f"Bundled Python package: {package}")
        return 0
    except Exception as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
