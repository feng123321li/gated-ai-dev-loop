from __future__ import annotations

import errno
import os
import shutil
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, TypeVar

from .errors import GatedLoopError
from .timing import timed_stage, timing_increment


T = TypeVar("T")
_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCK_STATE = threading.local()


def _lock_key(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def _process_lock(path: Path) -> threading.RLock:
    key = _lock_key(path)
    with _LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


def _try_lock_file(stream: BinaryIO) -> bool:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(
            stream.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError:
        return False
    return True


def _unlock_file(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    path: str | os.PathLike[str],
    *,
    timeout_seconds: float = 30.0,
) -> Iterator[None]:
    """Serialize one filesystem operation across threads and processes."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    process_lock = _process_lock(target)
    remaining = max(0.0, deadline - time.monotonic())
    if not process_lock.acquire(timeout=remaining):
        raise GatedLoopError(
            "FILESYSTEM_LOCK_TIMEOUT",
            f"Timed out waiting for filesystem lock: {target}",
        )
    try:
        key = _lock_key(target)
        depths = getattr(_THREAD_LOCK_STATE, "depths", None)
        if depths is None:
            depths = {}
            _THREAD_LOCK_STATE.depths = depths
        if depths.get(key, 0) > 0:
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        with target.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            while not _try_lock_file(stream):
                if time.monotonic() >= deadline:
                    raise GatedLoopError(
                        "FILESYSTEM_LOCK_TIMEOUT",
                        f"Timed out waiting for filesystem lock: {target}",
                    )
                time.sleep(0.05)
            try:
                depths[key] = 1
                yield
            finally:
                try:
                    _unlock_file(stream)
                finally:
                    depths.pop(key, None)
    finally:
        process_lock.release()


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


def _same_regular_file(target: Path, data: bytes) -> bool:
    try:
        before = target.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(before.st_mode):
        return False
    try:
        existing = target.read_bytes()
        after = target.lstat()
    except FileNotFoundError:
        return False
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    current = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    return identity == current and existing == data


def atomic_write(
    path: str | os.PathLike[str],
    content: str | bytes,
    *,
    durable: bool = True,
) -> bool:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    with timed_stage("filesystem.compare"):
        unchanged = _same_regular_file(target, data)
    if unchanged:
        timing_increment("filesSkipped")
        return False
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            if durable:
                stream.flush()
                with timed_stage("filesystem.fsync"):
                    os.fsync(stream.fileno())
        with timed_stage("filesystem.replace"):
            os.replace(temporary, target)
        timing_increment("filesWritten")
        if durable:
            timing_increment("fileFsyncs")
        return True
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


def _remove_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        shutil.rmtree(path)
    else:
        path.unlink()


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
            _remove_path(backup)
    except Exception:
        if moved_old and not destination.exists() and backup.exists():
            os.replace(backup, destination)
        raise
    finally:
        if staging.exists():
            _remove_path(staging)
        if backup.exists() and destination.exists():
            _remove_path(backup)
