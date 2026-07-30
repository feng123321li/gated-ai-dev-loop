from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote, urlsplit

from .errors import GatedLoopError


CODEX_SANDBOX_META_KEY = "codex/sandbox-state-meta"
MINIMUM_CLAUDE_CODE_USER_INTERACTION_VERSION = (2, 1, 199)


def _resolve_project_root(
    root: str | os.PathLike[str] | None,
) -> str:
    configured = root
    if configured is None:
        configured = os.environ.get("HDG_PROJECT_ROOT") or os.getcwd()
    candidate = Path(configured).expanduser()
    if candidate.is_symlink():
        raise GatedLoopError(
            "PROJECT_ROOT_INVALID",
            "MCP project root must not be a symbolic link",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise GatedLoopError(
            "PROJECT_ROOT_INVALID",
            "MCP project root must be an existing directory",
        )
    if not resolved.is_dir():
        raise GatedLoopError(
            "PROJECT_ROOT_INVALID",
            "MCP project root must be an existing directory",
        )
    return str(resolved)


def _local_path_from_file_uri(uri: str) -> str:
    if not uri.lower().startswith("file://"):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must be a local file URI",
        )
    parsed = urlsplit(uri)
    if (
        parsed.scheme.lower() != "file"
        or parsed.query
        or parsed.fragment
        or parsed.netloc.lower() not in {"", "localhost"}
    ):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must be a local file URI",
        )
    decoded = unquote(parsed.path)
    if not decoded or "\x00" in decoded:
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must contain a valid local path",
        )
    if os.name == "nt":
        if re.match(r"^/[A-Za-z]:/", decoded):
            decoded = decoded[1:]
        decoded = decoded.replace("/", "\\")
    elif not decoded.startswith("/"):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "sandboxCwd must contain an absolute local path",
        )
    return decoded


def _project_root_from_sandbox_meta(
    meta: object,
) -> str | None:
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "MCP request _meta must be a JSON object",
        )
    if CODEX_SANDBOX_META_KEY not in meta:
        return None
    sandbox_state = meta[CODEX_SANDBOX_META_KEY]
    if not isinstance(sandbox_state, dict):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "Codex sandbox metadata must be a JSON object",
        )
    sandbox_cwd = sandbox_state.get("sandboxCwd")
    if not isinstance(sandbox_cwd, str):
        raise GatedLoopError(
            "SANDBOX_METADATA_INVALID",
            "Codex sandbox metadata must include sandboxCwd",
        )
    return _local_path_from_file_uri(sandbox_cwd)


@dataclass
class ProjectRootBinding:
    """Resolve the project root without leaking it into the controller API."""

    _configured_root: str | None
    from_sandbox_meta: bool = False
    _legacy_bound_root: str | None = None

    @classmethod
    def from_startup(
        cls,
        root: str | os.PathLike[str] | None,
        *,
        from_sandbox_meta: bool = False,
    ) -> ProjectRootBinding:
        configured_environment_root = os.environ.get("HDG_PROJECT_ROOT")
        if from_sandbox_meta:
            if root is not None or configured_environment_root:
                raise GatedLoopError(
                    "PROJECT_ROOT_CONFIGURATION_CONFLICT",
                    "Sandbox metadata root binding cannot be combined with "
                    "--project-root or HDG_PROJECT_ROOT",
                )
            return cls(None, from_sandbox_meta=True)
        return cls(_resolve_project_root(root))

    @property
    def bound_root(self) -> str | None:
        return self._configured_root or self._legacy_bound_root

    def resolve(
        self,
        meta: object,
        *,
        stateless: bool,
    ) -> str:
        metadata_root = _project_root_from_sandbox_meta(meta)
        if metadata_root is None:
            if self.from_sandbox_meta:
                raise GatedLoopError(
                    "PROJECT_ROOT_UNAVAILABLE",
                    "Codex sandbox metadata is required on every MCP request",
                )
            if self._configured_root is None:
                raise GatedLoopError(
                    "PROJECT_ROOT_UNAVAILABLE",
                    "MCP project root is not configured",
                )
            return self._configured_root

        resolved_metadata_root = _resolve_project_root(metadata_root)
        if self._configured_root is not None:
            if os.path.normcase(self._configured_root) != os.path.normcase(
                resolved_metadata_root
            ):
                raise GatedLoopError(
                    "PROJECT_ROOT_MISMATCH",
                    "MCP request targets a different configured project root",
                )
            return self._configured_root

        if stateless:
            return resolved_metadata_root
        if self._legacy_bound_root is None:
            self._legacy_bound_root = resolved_metadata_root
            return resolved_metadata_root
        if os.path.normcase(self._legacy_bound_root) != os.path.normcase(
            resolved_metadata_root
        ):
            raise GatedLoopError(
                "PROJECT_ROOT_MISMATCH",
                "Legacy MCP session is already bound to another project root",
            )
        return self._legacy_bound_root


def _version_triplet(
    value: str | None,
) -> tuple[int, int, int] | None:
    if value is None:
        return None
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+.]|$)", value.strip())
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


@dataclass(frozen=True)
class HostCompatibilityPolicy:
    """Keep host-specific safety compatibility outside protocol and domain code."""

    def ensure_user_interaction_tool_supported(
        self,
        *,
        requires_user_interaction: bool,
        client_info: Mapping[str, object] | None,
    ) -> None:
        if not requires_user_interaction or client_info is None:
            return
        name_value = client_info.get("name")
        version_value = client_info.get("version")
        name = name_value if isinstance(name_value, str) else ""
        version = version_value if isinstance(version_value, str) else None
        if "claude" not in name.casefold() or "code" not in name.casefold():
            return
        parsed = _version_triplet(version)
        if (
            parsed is not None
            and parsed >= MINIMUM_CLAUDE_CODE_USER_INTERACTION_VERSION
        ):
            return
        raise GatedLoopError(
            "MCP_CLIENT_UPGRADE_REQUIRED",
            (
                "Claude Code 2.1.199 or later is required for tools "
                "that must always reach a human approval prompt"
            ),
            details={
                "minimumVersion": "2.1.199",
                "clientName": name_value,
                "clientVersion": version_value,
            },
        )


DEFAULT_HOST_POLICY = HostCompatibilityPolicy()


__all__ = (
    "CODEX_SANDBOX_META_KEY",
    "DEFAULT_HOST_POLICY",
    "HostCompatibilityPolicy",
    "ProjectRootBinding",
)
