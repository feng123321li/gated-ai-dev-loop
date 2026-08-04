from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Mapping
import uuid

from .errors import fail
from .jsonio import strict_json_loads


ORCHESTRATOR_CONFIG_ENV = "LAYERED_DELIVERY_ORCHESTRATOR_CONFIG"
ORCHESTRATOR_CONFIG_FILE = "orchestrator.json"
ORCHESTRATOR_CONFIG_SCHEMA_VERSION = 2
MAX_ORCHESTRATOR_CONFIG_BYTES = 64 * 1024

QUOTA_EXHAUSTION_POLICIES = frozenset({"PAUSE_AND_RESUME"})


@dataclass(frozen=True)
class OrchestratorConfig:
    """Validated user-level policy shared by every local host Adapter."""

    max_concurrent_executors: int = 4
    quota_exhaustion_policy: str = "PAUSE_AND_RESUME"
    source: str = "BUILT_IN_DEFAULTS"
    config_path: str | None = None

    def policy(self) -> dict[str, Any]:
        return {
            "schemaVersion": ORCHESTRATOR_CONFIG_SCHEMA_VERSION,
            "maxConcurrentExecutors": self.max_concurrent_executors,
            "quotaExhaustionPolicy": self.quota_exhaustion_policy,
        }

    def public_summary(self) -> dict[str, Any]:
        return {
            **self.policy(),
            "source": self.source,
            "configPath": self.config_path,
        }


def built_in_orchestrator_config() -> OrchestratorConfig:
    return OrchestratorConfig()


def orchestrator_config_path(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Resolve one per-user config path shared across local host products."""

    resolved_home = home or Path.home()
    resolved_environ = dict(os.environ if environ is None else environ)
    explicit = resolved_environ.get(ORCHESTRATOR_CONFIG_ENV)
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            fail(
                "ORCHESTRATOR_CONFIG_PATH_INVALID",
                f"{ORCHESTRATOR_CONFIG_ENV} must be an absolute path",
            )
        return candidate

    resolved_platform = platform or sys.platform
    if resolved_platform == "win32":
        base = Path(
            resolved_environ.get(
                "APPDATA",
                str(resolved_home / "AppData" / "Roaming"),
            )
        )
    elif resolved_platform == "darwin":
        base = resolved_home / "Library" / "Application Support"
    else:
        config_home = resolved_environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home else resolved_home / ".config"
    return base / "layered-delivery" / ORCHESTRATOR_CONFIG_FILE


def _validate_config(value: object, *, path: Path) -> OrchestratorConfig:
    if not isinstance(value, dict):
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator configuration must be a JSON object",
            configPath=str(path),
        )
    expected = {
        "schemaVersion",
        "maxConcurrentExecutors",
        "quotaExhaustionPolicy",
    }
    if set(value) != expected:
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator configuration fields are missing or unknown",
            configPath=str(path),
            expectedFields=sorted(expected),
        )
    schema_version = value["schemaVersion"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != ORCHESTRATOR_CONFIG_SCHEMA_VERSION
    ):
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator schemaVersion is invalid",
            configPath=str(path),
            supportedSchemaVersion=ORCHESTRATOR_CONFIG_SCHEMA_VERSION,
        )
    maximum = value["maxConcurrentExecutors"]
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 1 <= maximum <= 64
    ):
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "maxConcurrentExecutors must be an integer from 1 through 64",
            configPath=str(path),
        )
    quota_policy = value["quotaExhaustionPolicy"]
    if (
        not isinstance(quota_policy, str)
        or quota_policy not in QUOTA_EXHAUSTION_POLICIES
    ):
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "quotaExhaustionPolicy is invalid",
            configPath=str(path),
            allowedValues=sorted(QUOTA_EXHAUSTION_POLICIES),
        )
    return OrchestratorConfig(
        max_concurrent_executors=maximum,
        quota_exhaustion_policy=quota_policy,
        source="USER_CONFIG",
        config_path=str(path),
    )


def load_orchestrator_config(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> OrchestratorConfig:
    """Load a strict config or return safe built-in defaults when absent."""

    path = orchestrator_config_path(
        home=home,
        environ=environ,
        platform=platform,
    )
    if path.is_symlink():
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator configuration must not be a symbolic link",
            configPath=str(path),
        )
    if not path.exists():
        return OrchestratorConfig(config_path=str(path))
    try:
        metadata = path.stat()
    except OSError:
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator configuration metadata is not readable",
            configPath=str(path),
        )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > MAX_ORCHESTRATOR_CONFIG_BYTES
    ):
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator configuration must be a bounded regular file",
            configPath=str(path),
            maxBytes=MAX_ORCHESTRATOR_CONFIG_BYTES,
        )
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = strict_json_loads(raw)
    except (OSError, UnicodeError, ValueError, RecursionError, MemoryError):
        fail(
            "ORCHESTRATOR_CONFIG_INVALID",
            "Orchestrator configuration is not valid strict UTF-8 JSON",
            configPath=str(path),
        )
    return _validate_config(parsed, path=path)


def save_orchestrator_config(
    value: object,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> OrchestratorConfig:
    """Validate and atomically replace the shared per-user policy file."""

    path = orchestrator_config_path(
        home=home,
        environ=environ,
        platform=platform,
    )
    config = _validate_config(value, path=path)
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        fail(
            "ORCHESTRATOR_CONFIG_WRITE_FAILED",
            "Orchestrator configuration directory cannot be created",
            configPath=str(path),
        )
    if parent.is_symlink() or not parent.is_dir():
        fail(
            "ORCHESTRATOR_CONFIG_WRITE_FAILED",
            "Orchestrator configuration directory must be a real directory",
            configPath=str(path),
        )
    if path.is_symlink() or (path.exists() and not path.is_file()):
        fail(
            "ORCHESTRATOR_CONFIG_WRITE_FAILED",
            "Orchestrator configuration target must be a regular file",
            configPath=str(path),
        )

    temporary = parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    serialized = json.dumps(
        config.policy(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        fail(
            "ORCHESTRATOR_CONFIG_WRITE_FAILED",
            "Orchestrator configuration could not be saved atomically",
            configPath=str(path),
        )
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return config


__all__ = (
    "MAX_ORCHESTRATOR_CONFIG_BYTES",
    "ORCHESTRATOR_CONFIG_ENV",
    "ORCHESTRATOR_CONFIG_FILE",
    "ORCHESTRATOR_CONFIG_SCHEMA_VERSION",
    "OrchestratorConfig",
    "QUOTA_EXHAUSTION_POLICIES",
    "built_in_orchestrator_config",
    "load_orchestrator_config",
    "orchestrator_config_path",
    "save_orchestrator_config",
)
