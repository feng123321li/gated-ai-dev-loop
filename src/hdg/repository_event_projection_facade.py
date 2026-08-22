from __future__ import annotations

import sqlite3
from typing import Any

from .repository_events import DeliveryEventStore


class RepositoryEventProjectionFacadeMixin:
    """Expose event and projection stores through the public repository."""

    @staticmethod
    def delivery_closure_from_connection(
        connection: sqlite3.Connection,
        root_id: str,
    ) -> dict[str, Any]:
        return DeliveryEventStore.delivery_closure_from_connection(
            connection,
            root_id,
        )

    def delivery_closure(self, root_id: str) -> dict[str, Any]:
        return self._delivery_event_store().delivery_closure(root_id)

    @staticmethod
    def latest_nodes(
        connection: sqlite3.Connection,
        run_id: str,
    ) -> list[dict[str, Any]]:
        return DeliveryEventStore.latest_nodes(connection, run_id)

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        node_id: str | None,
        attempt: int | None,
        event_type: str,
        actor: str,
        operation_id: str | None,
        payload: dict[str, Any],
        at: str,
    ) -> dict[str, Any]:
        return self._delivery_event_store()._append_event(
            connection,
            run_id=run_id,
            node_id=node_id,
            attempt=attempt,
            event_type=event_type,
            actor=actor,
            operation_id=operation_id,
            payload=payload,
            at=at,
        )

    def append_event(
        self,
        connection: sqlite3.Connection,
        **arguments: Any,
    ) -> dict[str, Any]:
        return self._delivery_event_store().append_event(
            connection,
            **arguments,
        )

    def events(
        self,
        root_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._delivery_event_store().events(
            root_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def refresh_ready(
        self,
        connection: sqlite3.Connection,
        graph: dict[str, Any],
        run_id: str,
        *,
        at: str,
        touch_run: bool = True,
    ) -> bool:
        return self._delivery_event_store().refresh_ready(
            connection,
            graph,
            run_id,
            at=at,
            touch_run=touch_run,
        )

    def write_projections(
        self,
        root_id: str,
        *,
        preserve_manual_updates: bool = True,
        refresh_workspace_overview: bool = True,
    ) -> None:
        return self._delivery_projection_store().write_projections(
            root_id,
            preserve_manual_updates=preserve_manual_updates,
            refresh_workspace_overview=refresh_workspace_overview,
        )

    def _write_workspace_overview(self) -> None:
        return self._delivery_projection_store()._write_workspace_overview()

    def write_workspace_overview(self) -> None:
        return self._delivery_projection_store().write_workspace_overview()

    def _workspace_projection_sources(self) -> list[dict[str, Any]]:
        return self._delivery_projection_store()._workspace_projection_sources()


__all__ = ("RepositoryEventProjectionFacadeMixin",)
