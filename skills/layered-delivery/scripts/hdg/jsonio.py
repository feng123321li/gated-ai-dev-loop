from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any


_SENSITIVE_KEYS = ("stdout", "stderr", "env", "token", "key", "secret", "password")


def canonical_json(value: Any) -> str:
    """Return the byte-compatible canonical form used for persisted fingerprints."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, separators=(",", ": ")) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def canonical_relative_path(value: str) -> str:
    portable = str(value).replace("\\", "/")
    normalized = str(PurePosixPath(portable))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        from .errors import GatedLoopError

        raise GatedLoopError("PATH_OUTSIDE_ROOT", f"Path escapes root: {value}")
    return normalized


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return lowered in {"stdout", "stderr", "env"} or any(
        token in lowered for token in _SENSITIVE_KEYS[3:]
    )


def redact(value: Any) -> Any:
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _sensitive(str(key)) else redact(child)
            for key, child in value.items()
        }
    return value


def rendered_json(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, separators=(",", ":")) + "\n"
