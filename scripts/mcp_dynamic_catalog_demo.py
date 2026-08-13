#!/usr/bin/env python3
"""Reference a session-external MCP registry with per-turn tool snapshots."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
sys.path.insert(0, str(SOURCE))

from hdg.mcp_tools import tool_definitions  # noqa: E402


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class DynamicCatalogRegistry:
    """Small reference model for host-owned atomic MCP catalog publication."""

    def __init__(self, expected_tools: Sequence[Mapping[str, object]]) -> None:
        self._expected = {
            str(tool["name"]): deepcopy(dict(tool)) for tool in expected_tools
        }
        self._expected_digest = _digest(
            [self._expected[name] for name in sorted(self._expected)]
        )
        self._servers: dict[str, dict[str, object]] = {}

    def fail_attempt(
        self,
        *,
        server: str,
        attempt: int,
        reason: str,
    ) -> dict[str, object]:
        state = self._servers.setdefault(
            server,
            {"generation": 0, "events": []},
        )
        state.update(
            {
                "available": False,
                "attempt": attempt,
                "failureReason": reason,
                "publishedTools": (),
                "catalogDigest": None,
            }
        )
        events = state["events"]
        if isinstance(events, list):
            events.append(
                {
                    "stage": "FAILED",
                    "attempt": attempt,
                    "reason": reason,
                }
            )
        return {
            "published": False,
            "status": "PLUGIN_MCP_UNAVAILABLE",
            "attempt": attempt,
        }

    def publish_catalog(
        self,
        *,
        server: str,
        attempt: int,
        tools: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        state = self._servers.setdefault(
            server,
            {"generation": 0, "events": []},
        )
        received = {
            str(tool.get("name")): deepcopy(dict(tool))
            for tool in tools
            if isinstance(tool.get("name"), str)
        }
        expected_names = set(self._expected)
        received_names = set(received)
        missing = sorted(expected_names - received_names)
        unexpected = sorted(received_names - expected_names)
        schema_mismatches = sorted(
            name
            for name in expected_names & received_names
            if _canonical(received[name]) != _canonical(self._expected[name])
        )
        duplicate_names = len(received) != len(tools)
        if missing or unexpected or schema_mismatches or duplicate_names:
            state.update(
                {
                    "available": False,
                    "attempt": attempt,
                    "failureReason": "CATALOG_VALIDATION_FAILED",
                    "publishedTools": (),
                    "catalogDigest": None,
                }
            )
            events = state["events"]
            if isinstance(events, list):
                events.append(
                    {
                        "stage": "CATALOG_REJECTED",
                        "attempt": attempt,
                        "missingTools": missing,
                        "unexpectedTools": unexpected,
                        "schemaMismatches": schema_mismatches,
                        "duplicateNames": duplicate_names,
                    }
                )
            status = (
                "PARTIAL_REGISTRATION"
                if missing and not unexpected and not schema_mismatches
                else "CATALOG_INVALID"
            )
            return {
                "published": False,
                "status": status,
                "attempt": attempt,
                "missingTools": missing,
                "unexpectedTools": unexpected,
                "schemaMismatches": schema_mismatches,
                "duplicateNames": duplicate_names,
            }

        generation = int(state.get("generation", 0)) + 1
        published = tuple(
            deepcopy(self._expected[name]) for name in sorted(self._expected)
        )
        state.update(
            {
                "available": True,
                "attempt": attempt,
                "failureReason": None,
                "generation": generation,
                "publishedTools": published,
                "catalogDigest": self._expected_digest,
            }
        )
        events = state["events"]
        if isinstance(events, list):
            events.append(
                {
                    "stage": "CATALOG_PUBLISHED",
                    "attempt": attempt,
                    "generation": generation,
                    "toolCount": len(published),
                    "catalogDigest": self._expected_digest,
                }
            )
        return {
            "published": True,
            "status": "REGISTERED",
            "attempt": attempt,
            "catalogGeneration": generation,
            "toolCount": len(published),
            "catalogDigest": self._expected_digest,
        }

    def snapshot_for_turn(
        self,
        *,
        server: str,
        workspace: str,
        session_id: str,
        turn_id: str,
        agent_role: str,
    ) -> dict[str, object]:
        state = self._servers.get(server, {})
        available = state.get("available") is True
        tools = state.get("publishedTools") if available else ()
        published = tuple(tools) if isinstance(tools, tuple) else ()
        return {
            "server": server,
            "workspace": workspace,
            "sessionId": session_id,
            "turnId": turn_id,
            "agentRole": agent_role,
            "status": "REGISTERED" if available else "PLUGIN_MCP_UNAVAILABLE",
            "catalogGeneration": int(state.get("generation", 0)),
            "catalogDigest": state.get("catalogDigest") if available else None,
            "toolCount": len(published),
            "toolNames": [str(tool["name"]) for tool in published],
            "schemaAssembly": "PER_TURN_TYPED_CATALOG",
        }

    def events(self, server: str) -> list[dict[str, object]]:
        state = self._servers.get(server, {})
        events = state.get("events")
        return deepcopy(events) if isinstance(events, list) else []


def run_reference_demo() -> dict[str, object]:
    """Simulate recovery in the same sessions across two workspaces/roles."""

    server = "delivery-graph"
    registry = DynamicCatalogRegistry(tool_definitions())
    registry.fail_attempt(
        server=server,
        attempt=1,
        reason="SPAWN_FAILED",
    )
    cases = (
        ("G:/workspace/alpha", "session-alpha", "primary"),
        ("G:/workspace/alpha", "session-alpha", "child"),
        ("G:/workspace/beta", "session-beta", "primary"),
        ("G:/workspace/beta", "session-beta", "child"),
    )
    first_turns = [
        registry.snapshot_for_turn(
            server=server,
            workspace=workspace,
            session_id=session_id,
            turn_id=f"{agent_role}-turn-1",
            agent_role=agent_role,
        )
        for workspace, session_id, agent_role in cases
    ]
    registry.publish_catalog(
        server=server,
        attempt=2,
        tools=tool_definitions(),
    )
    second_turns = [
        registry.snapshot_for_turn(
            server=server,
            workspace=workspace,
            session_id=session_id,
            turn_id=f"{agent_role}-turn-2",
            agent_role=agent_role,
        )
        for workspace, session_id, agent_role in cases
    ]
    same_session_recovered = all(
        before["sessionId"] == after["sessionId"]
        and before["workspace"] == after["workspace"]
        and before["agentRole"] == after["agentRole"]
        for before, after in zip(first_turns, second_turns)
    )
    return {
        "schemaVersion": 1,
        "architecture": "EXTERNAL_SUPERVISOR_PER_TURN",
        "description": (
            "Reference simulation only; the host must implement this registry "
            "to refresh real Agent tool schemas."
        ),
        "events": registry.events(server),
        "turnMatrix": [*first_turns, *second_turns],
        "assertions": {
            "sameSessionRecovered": same_session_recovered,
            "activeTurnSnapshotImmutable": all(
                item["toolCount"] == 0
                and item["status"] == "PLUGIN_MCP_UNAVAILABLE"
                for item in first_turns
            ),
            "allNextTurnsRegistered": all(
                item["toolCount"] == len(tool_definitions())
                and item["status"] == "REGISTERED"
                for item in second_turns
            ),
            "genericMcpCallProxyUsed": False,
        },
        "safety": {
            "modelInvocationStarted": False,
            "mcpServerSpawned": False,
            "mcpToolCallAttempted": False,
            "governanceWriteAttempted": False,
            "schedulerDatabaseAccessed": False,
        },
    }


def main() -> int:
    print(
        json.dumps(
            run_reference_demo(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
