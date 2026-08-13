from __future__ import annotations

from io import BytesIO, StringIO
import json
import tempfile
import unittest

from hdg.mcp_adapter import MODERN_PROTOCOL_VERSION
from hdg.mcp_server import serve


def modern_meta() -> dict[str, object]:
    return {
        "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "diagnostic-test",
            "version": "1.0.0",
        },
    }


def encoded_messages(*messages: dict[str, object]) -> BytesIO:
    value = "".join(json.dumps(message) + "\n" for message in messages)
    return BytesIO(value.encode("utf-8"))


class FailingOutput(StringIO):
    def write(self, value: str) -> int:
        raise BrokenPipeError("host closed stdout")


class McpLifecycleDiagnosticsTests(unittest.TestCase):
    def test_modern_discovery_and_catalog_are_logged_by_stage(self) -> None:
        diagnostics = StringIO()
        with tempfile.TemporaryDirectory() as root:
            serve(
                stdin=encoded_messages(
                    {
                        "jsonrpc": "2.0",
                        "id": "discover",
                        "method": "server/discover",
                        "params": {"_meta": modern_meta()},
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": "list",
                        "method": "tools/list",
                        "params": {"_meta": modern_meta()},
                    },
                ),
                stdout=StringIO(),
                root=root,
                diagnostic_stream=diagnostics,
            )

        events = [json.loads(line) for line in diagnostics.getvalue().splitlines()]
        self.assertEqual(
            [event["stage"] for event in events],
            [
                "SERVER_STARTED",
                "DISCOVERY_RESPONDED",
                "TOOLS_LIST_RESPONDED",
                "TRANSPORT_EOF",
            ],
        )
        catalog = events[2]
        self.assertEqual(catalog["protocolMode"], "STATELESS_2026_07_28")
        self.assertEqual(catalog["toolCount"], 33)
        self.assertTrue(events[-1]["toolCatalogDelivered"])
        self.assertIn("schema", events[-1]["diagnosticHint"].lower())

    def test_eof_before_any_request_explains_host_spawn_boundary(self) -> None:
        diagnostics = StringIO()
        with tempfile.TemporaryDirectory() as root:
            serve(
                stdin=BytesIO(),
                stdout=StringIO(),
                root=root,
                diagnostic_stream=diagnostics,
            )

        events = [json.loads(line) for line in diagnostics.getvalue().splitlines()]
        self.assertEqual(events[-1]["stage"], "TRANSPORT_EOF")
        self.assertEqual(events[-1]["requestCount"], 0)
        self.assertIn("before any mcp request", events[-1]["diagnosticHint"].lower())

    def test_response_delivery_failure_records_last_method_without_payload(self) -> None:
        diagnostics = StringIO()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(BrokenPipeError):
                serve(
                    stdin=encoded_messages(
                        {
                            "jsonrpc": "2.0",
                            "id": "list-private-id",
                            "method": "tools/list",
                            "params": {"_meta": modern_meta()},
                        }
                    ),
                    stdout=FailingOutput(),
                    root=root,
                    diagnostic_stream=diagnostics,
                )

        event = json.loads(diagnostics.getvalue().splitlines()[-1])
        self.assertEqual(event["stage"], "RESPONSE_DELIVERY_FAILED")
        self.assertEqual(event["method"], "tools/list")
        self.assertNotIn("list-private-id", diagnostics.getvalue())
        self.assertIn("host closed", event["diagnosticHint"].lower())


if __name__ == "__main__":
    unittest.main()
