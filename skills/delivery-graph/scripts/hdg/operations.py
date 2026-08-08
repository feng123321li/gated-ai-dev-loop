from __future__ import annotations

from typing import Any

from .controller import (
    CONTROLLER_OPERATIONS,
    ControllerContext,
    DEFAULT_CONTROLLER,
)


# Preserve the documented Python façade while controller.py remains canonical.
OPERATIONS = CONTROLLER_OPERATIONS


def execute_operation(
    name: str,
    *,
    root: str,
    explicit_dogfood: bool = False,
    **arguments: Any,
) -> dict[str, Any]:
    return DEFAULT_CONTROLLER.execute(
        name,
        arguments,
        context=ControllerContext(
            project_root=root,
            explicit_dogfood=explicit_dogfood,
        ),
    )


__all__ = ("execute_operation",)
