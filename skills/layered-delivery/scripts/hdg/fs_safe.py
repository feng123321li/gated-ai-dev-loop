from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Callable, TypeVar

from .errors import GatedLoopError


T = TypeVar("T")


def _contained(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def safe_path(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> Path:
    """Resolve a path inside root without crossing a volume or following symlinks."""

    root_path = Path(root).absolute()
    candidate_path = Path(candidate)
    target = candidate_path.absolute() if candidate_path.is_absolute() else (root_path / candidate_path).absolute()
    root_drive = os.path.splitdrive(str(root_path))[0].casefold()
    target_drive = os.path.splitdrive(str(target))[0].casefold()
    if root_drive != target_drive:
        raise GatedLoopError("PATH_CROSS_VOLUME", f"Path is on another volume: {candidate}")
    if not _contained(root_path, target):
        raise GatedLoopError("PATH_OUTSIDE_ROOT", f"Path escapes root: {candidate}")

    current = root_path
    if current.exists() and current.is_symlink():
        raise GatedLoopError("PATH_SYMLINK", f"Symbolic link is not allowed: {current}")
    relative = target.relative_to(root_path)
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise GatedLoopError("PATH_SYMLINK", f"Symbolic link is not allowed: {current}")
        if not _contained(root_path.resolve(), current.resolve()):
            raise GatedLoopError("PATH_OUTSIDE_ROOT", f"Real path escapes root: {current}")
    return target


def read_regular_file(root: str | os.PathLike[str], candidate: str | os.PathLike[str]) -> bytes:
    target = safe_path(root, candidate)
    try:
        before = target.stat()
    except FileNotFoundError:
        raise
    if not stat.S_ISREG(before.st_mode) or target.is_symlink():
        raise GatedLoopError("PATH_NOT_FILE", f"Path is not a regular file: {candidate}")
    data = target.read_bytes()
    after = target.stat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    current = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity != current:
        raise GatedLoopError("PATH_FILE_CHANGED", f"File changed while being read: {candidate}")
    return data


def atomic_write(path: str | os.PathLike[str], content: str | bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    data = content.encode("utf-8") if isinstance(content, str) else content
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_create_directory(target: str | os.PathLike[str], populate: Callable[[Path], None]) -> None:
    destination = Path(target)
    if destination.exists():
        raise GatedLoopError("PATH_EXISTS", f"Directory already exists: {destination}")
    staging = destination.with_name(f"{destination.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    try:
        populate(staging)
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def atomic_replace_directory(target: str | os.PathLike[str], populate: Callable[[Path], None]) -> None:
    destination = Path(target)
    staging = destination.with_name(f"{destination.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    backup = destination.with_name(f"{destination.name}.backup.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    staging.mkdir(parents=True)
    moved_old = False
    try:
        populate(staging)
        if destination.exists():
            os.replace(destination, backup)
            moved_old = True
        os.replace(staging, destination)
        if moved_old:
            shutil.rmtree(backup)
    except Exception:
        if moved_old and not destination.exists() and backup.exists():
            os.replace(backup, destination)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists() and destination.exists():
            shutil.rmtree(backup)
