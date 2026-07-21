from __future__ import annotations

from typing import Any


class GatedLoopError(Exception):
    """Stable, user-facing controller error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}


def fail(code: str, message: str, **details: Any) -> None:
    raise GatedLoopError(code, message, details=details)
