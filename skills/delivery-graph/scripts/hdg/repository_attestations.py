from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import secrets
from typing import Any, Callable

from .errors import fail


HOST_WORKSPACE_ATTESTATION_SECONDS = 60


class HostWorkspaceAttestationStore:
    """Persistence boundary for host lifecycle/workspace attestations."""

    def __init__(
        self,
        repository: Any,
        *,
        timestamp_fn: Callable[[object], str],
    ) -> None:
        self.repository = repository
        self.timestamp_fn = timestamp_fn

    def issue(
        self,
        *,
        host_adapter_id: str,
        context_id: str,
        tool_name: str,
        tool_use_id: str,
        workspace_root: str | os.PathLike[str],
        lifetime_seconds: int = HOST_WORKSPACE_ATTESTATION_SECONDS,
    ) -> str:
        if (
            not isinstance(lifetime_seconds, int)
            or isinstance(lifetime_seconds, bool)
            or lifetime_seconds < 1
            or lifetime_seconds > 86_400
        ):
            fail(
                "SCHEDULER_HOST_WORKSPACE_ATTESTATION_INVALID",
                "Host workspace evidence lifetime must be 1..86400 seconds",
            )

        workspace = str(
            Path(workspace_root).absolute().resolve(strict=True)
        )
        attestation = secrets.token_hex(32)
        attestation_digest = hashlib.sha256(
            attestation.encode("utf-8")
        ).hexdigest()
        context_digest = hashlib.sha256(
            context_id.encode("utf-8")
        ).hexdigest()
        tool_use_digest = hashlib.sha256(
            tool_use_id.encode("utf-8")
        ).hexdigest()
        at = self.timestamp_fn(self.repository.now)
        expires_at = (
            datetime.fromisoformat(at.replace("Z", "+00:00"))
            + timedelta(seconds=lifetime_seconds)
        ).isoformat().replace("+00:00", "Z")
        with self.repository.transaction() as connection:
            connection.execute(
                "UPDATE host_workspace_attestations "
                "SET status = 'SUPERSEDED' "
                "WHERE host_adapter_id = ? AND context_digest = ? "
                "AND tool_use_digest = ? AND status = 'ISSUED'",
                (
                    host_adapter_id,
                    context_digest,
                    tool_use_digest,
                ),
            )
            connection.execute(
                "INSERT INTO host_workspace_attestations("
                "attestation_digest, host_adapter_id, context_digest, "
                "tool_name, tool_use_digest, workspace_root, "
                "workspace_key, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'ISSUED', ?, ?)",
                (
                    attestation_digest,
                    host_adapter_id,
                    context_digest,
                    tool_name,
                    tool_use_digest,
                    workspace,
                    self.repository.workspace_key(workspace),
                    at,
                    expires_at,
                ),
            )
        return attestation

    def validate_session(
        self,
        attestation: str,
        *,
        host_adapter_id: str,
        context_id: str,
        tool_name: str,
    ) -> str:
        digest = hashlib.sha256(attestation.encode("utf-8")).hexdigest()
        context_digest = hashlib.sha256(
            context_id.encode("utf-8")
        ).hexdigest()
        at = self.timestamp_fn(self.repository.now)
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT * FROM host_workspace_attestations "
                "WHERE attestation_digest = ?",
                (digest,),
            ).fetchone()
        if row is None:
            fail(
                "SCHEDULER_HOST_SESSION_ATTESTATION_MISSING",
                "Host session evidence does not exist",
            )
        if row["status"] != "ISSUED":
            fail(
                "SCHEDULER_HOST_SESSION_ATTESTATION_INACTIVE",
                "Host session evidence is no longer active",
            )
        if row["expires_at"] < at:
            fail(
                "SCHEDULER_HOST_SESSION_ATTESTATION_EXPIRED",
                "Host session evidence expired",
            )
        if (
            row["host_adapter_id"] != host_adapter_id
            or row["context_digest"] != context_digest
            or row["tool_name"] != tool_name
        ):
            fail(
                "SCHEDULER_HOST_SESSION_ATTESTATION_MISMATCH",
                "Host session evidence targets another receiver",
            )
        workspace = str(
            Path(row["workspace_root"]).absolute().resolve(strict=True)
        )
        if self.repository.workspace_key(workspace) != row["workspace_key"]:
            fail(
                "SCHEDULER_HOST_SESSION_ATTESTATION_MISMATCH",
                "Host session evidence no longer matches its workspace",
            )
        return workspace

    def consume(
        self,
        attestation: str,
        *,
        host_adapter_id: str,
        tool_name: str,
    ) -> str:
        digest = hashlib.sha256(attestation.encode("utf-8")).hexdigest()
        at = self.timestamp_fn(self.repository.now)
        with self.repository.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM host_workspace_attestations "
                "WHERE attestation_digest = ?",
                (digest,),
            ).fetchone()
            if row is None:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_MISSING",
                    "Host workspace evidence does not exist",
                )
            if row["status"] != "ISSUED":
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_CONSUMED",
                    "Host workspace evidence is no longer active",
                )
            if row["expires_at"] < at:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_EXPIRED",
                    "Host workspace evidence expired",
                )
            if (
                row["host_adapter_id"] != host_adapter_id
                or row["tool_name"] != tool_name
            ):
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_MISMATCH",
                    "Host workspace evidence targets another call",
                )
            workspace = str(
                Path(row["workspace_root"]).absolute().resolve(strict=True)
            )
            if self.repository.workspace_key(workspace) != row["workspace_key"]:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_MISMATCH",
                    "Host workspace evidence no longer matches its workspace",
                )
            updated = connection.execute(
                "UPDATE host_workspace_attestations "
                "SET status = 'CONSUMED', consumed_at = ? "
                "WHERE attestation_digest = ? AND status = 'ISSUED'",
                (at, digest),
            )
            if updated.rowcount != 1:
                fail(
                    "SCHEDULER_HOST_WORKSPACE_ATTESTATION_CONSUMED",
                    "Host workspace evidence was consumed concurrently",
                )
        return workspace


__all__ = (
    "HOST_WORKSPACE_ATTESTATION_SECONDS",
    "HostWorkspaceAttestationStore",
)
